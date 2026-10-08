"""Rehearse restart and full restoration exclusively in isolated test containers."""

import argparse
import hashlib
import io
import json
import subprocess
import time
import zipfile
from pathlib import Path

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]
SOURCE = "newai-step6-test"
DESTINATION = "newai-step6-restore"


def compose(project, config, *args, data=None):
    command = [
        "docker",
        "compose",
        "--env-file",
        str(config),
        "-f",
        "compose.yaml",
        "-f",
        "test/config/compose.deployment.yaml",
        "-p",
        project,
        *args,
    ]
    result = subprocess.run(command, cwd=ROOT, input=data, capture_output=True)
    if result.returncode:
        from uuid import uuid4

        error_dir = ROOT / "test/results/deployment-errors"
        error_dir.mkdir(parents=True, exist_ok=True)
        (error_dir / f"recovery-error-{uuid4()}.log").write_bytes(result.stderr)
        raise RuntimeError(f"Isolated {project} operation failed; command output omitted.")
    return result.stdout


def guard(project, config):
    settings = dotenv_values(config)
    if settings.get("POSTGRES_DB") != "newai_test":
        raise RuntimeError("Refuse to modify a non-test database.")
    identifier = compose(project, config, "ps", "-q", "db").decode().strip()
    actual = json.loads(subprocess.check_output(["docker", "inspect", identifier]))[0]
    if actual["Config"]["Labels"]["com.docker.compose.project"] != project:
        raise RuntimeError("Container project identity does not match.")
    return settings


def wait_http(port):
    with httpx.Client(timeout=10, trust_env=False) as client:
        for _ in range(60):
            try:
                if client.get(f"http://127.0.0.1:{port}/api/health").status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(1)
    raise RuntimeError("Isolated HTTP health deadline reached.")


def snapshot(port, project_id):
    base = f"/api/projects/{project_id}"
    with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=30, trust_env=False) as client:

        def get(path):
            response = client.get(path)
            response.raise_for_status()
            return response.json()

        sources = get(base + "/sources")
        indexes = get(base + "/indexes")
        hashes = {}
        mappings = {}
        for source in sources:
            download = client.get(base + f"/sources/{source['id']}/download")
            download.raise_for_status()
            hashes[source["id"]] = hashlib.sha256(download.content).hexdigest()
            if source["filename"] == "orders.csv":
                mappings[source["id"]] = get(base + f"/sources/{source['id']}/mapping")
                response = client.post(
                    base + f"/sources/{source['id']}/metrics",
                    json={
                        "metric": "net_sales",
                        "start": "2026-09-01",
                        "end": "2026-09-30",
                        "compare_previous": True,
                        "group": "region",
                    },
                )
                response.raise_for_status()
                metric = response.json()
        response = client.post(
            base + "/search",
            json={
                "query": "新版本闪退和退款",
                "top_k": 5,
                "min_similarity": 0.35,
            },
        )
        response.raise_for_status()
        retrieval = response.json()
        assert retrieval["results"]
        citations = [get(base + "/citations/" + item["chunk_id"]) for item in retrieval["results"]]
        return {
            "project": get(base),
            "sources": sources,
            "indexes": indexes,
            "file_hashes": hashes,
            "mappings": mappings,
            "metric": metric,
            "retrieval": retrieval,
            "citations": citations,
            "history": get(base + "/agent/history"),
        }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-env", type=Path, required=True)
    parser.add_argument("--destination-env", type=Path, required=True)
    parser.add_argument("--smoke", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    for path in (args.source_env, args.destination_env, args.smoke, output):
        if not path.resolve().is_relative_to((ROOT / "test/results").resolve()):
            raise RuntimeError("All inputs and output must stay under test/results.")
    output.mkdir(parents=True, exist_ok=False)
    src = guard(SOURCE, args.source_env)
    dst = dotenv_values(args.destination_env)
    assert src["TEST_WEB_PORT"] == "8081" and dst["TEST_WEB_PORT"] == "8082"
    assert src["POSTGRES_DB"] == dst["POSTGRES_DB"] == "newai_test"
    project_id = json.loads(args.smoke.read_text(encoding="utf-8"))["project_id"]
    # Create a dispatch that lost its final usage, without making any model request.
    seed = """
import json
from uuid import UUID, uuid4
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models.agent import AgentRun
from app.models import workspace,call_protection
from app.services.llm_budget import reserve_tokens
from sqlalchemy.engine import make_url
assert make_url(get_settings().database_url.get_secret_value()).database == 'newai_test'
with SessionLocal() as db:
    run=AgentRun(project_id=UUID(PROJECT), request_id=uuid4(), question='重启恢复验收(test)',
                 provider='custom', model='test-only', status='running', snapshot={'sources':[]})
    db.add(run); db.commit(); identifier=run.id
reservation=reserve_tokens(identifier,12345)
print(json.dumps({'run_id':str(identifier),'reservation_id':str(reservation)}))
""".replace("PROJECT", repr(project_id))
    marker = json.loads(
        compose(SOURCE, args.source_env, "exec", "-T", "backend", "python", "-c", seed)
    )
    compose(SOURCE, args.source_env, "restart", "backend")
    wait_http(8081)
    verify = """
import json
from uuid import UUID
from app.core.database import SessionLocal
from app.models.agent import AgentRun
from app.models.call_protection import TokenHour,TokenReservation
with SessionLocal() as db:
    run=db.get(AgentRun,UUID(RUN)); r=db.get(TokenReservation,UUID(RESERVATION))
    h=db.get(TokenHour,r.hour_start)
    assert run.status=='interrupted' and r.state=='settled' and not r.usage_known
    assert r.charged_tokens==12345 and h.used_tokens>=12345 and h.reserved_tokens==0
    print(json.dumps({'run_status':run.status,'used':h.used_tokens,'reserved':h.reserved_tokens,
        'unknown':h.unknown_requests,'charged':r.charged_tokens,'hour':h.hour_start.isoformat()}))
""".replace("RESERVATION", repr(marker["reservation_id"])).replace("RUN", repr(marker["run_id"]))
    recovered = json.loads(
        compose(SOURCE, args.source_env, "exec", "-T", "backend", "python", "-c", verify)
    )
    compose(SOURCE, args.source_env, "restart", "backend")
    wait_http(8081)
    assert recovered == json.loads(
        compose(SOURCE, args.source_env, "exec", "-T", "backend", "python", "-c", verify)
    )
    original = snapshot(8081, project_id)
    compose(SOURCE, args.source_env, "stop", "backend", "frontend")
    dump = compose(
        SOURCE,
        args.source_env,
        "exec",
        "-T",
        "db",
        "pg_dump",
        "-U",
        src["POSTGRES_USER"],
        "-d",
        "newai_test",
        "--format=custom",
        "--no-owner",
    )
    (output / "database.dump").write_bytes(dump)
    archive_code = """import io,sys,zipfile
from pathlib import Path
buffer=io.BytesIO()
with zipfile.ZipFile(buffer,'w',zipfile.ZIP_DEFLATED) as archive:
    for path in sorted(Path('/app/runtime/uploads').rglob('*')):
        if path.is_file():
            assert not path.is_symlink()
            archive.write(path,path.relative_to('/app/runtime/uploads'))
sys.stdout.buffer.write(buffer.getvalue())
"""
    archive = compose(
        SOURCE,
        args.source_env,
        "run",
        "--rm",
        "-T",
        "--no-deps",
        "backend",
        "python",
        "-c",
        archive_code,
    )
    (output / "uploads.zip").write_bytes(archive)
    with zipfile.ZipFile(io.BytesIO(archive)) as files:
        assert len(files.namelist()) == len(original["sources"])
        assert all(
            not Path(name).is_absolute() and ".." not in Path(name).parts
            for name in files.namelist()
        )
    compose(DESTINATION, args.destination_env, "up", "-d", "--wait", "db")
    guard(DESTINATION, args.destination_env)
    # The target must be an empty isolated database; never clean or overwrite a populated database.
    tables = compose(
        DESTINATION,
        args.destination_env,
        "exec",
        "-T",
        "db",
        "psql",
        "-U",
        dst["POSTGRES_USER"],
        "-d",
        "newai_test",
        "-Atc",
        "select count(*) from pg_tables where schemaname='public'",
    )
    assert tables.strip() == b"0", "Destination database is not empty; restoration refused."
    compose(
        DESTINATION,
        args.destination_env,
        "exec",
        "-T",
        "db",
        "pg_restore",
        "-U",
        dst["POSTGRES_USER"],
        "-d",
        "newai_test",
        "--no-owner",
        "--exit-on-error",
        data=dump,
    )
    extract = """import io,sys,zipfile
from pathlib import Path
with zipfile.ZipFile(io.BytesIO(sys.stdin.buffer.read())) as archive:
    for name in archive.namelist():
        assert not Path(name).is_absolute() and '..' not in Path(name).parts
    archive.extractall('/app/runtime/uploads')
"""
    compose(
        DESTINATION,
        args.destination_env,
        "run",
        "--rm",
        "-T",
        "--no-deps",
        "backend",
        "python",
        "-c",
        extract,
        data=archive,
    )
    compose(DESTINATION, args.destination_env, "up", "-d", "--wait")
    wait_http(8082)
    restored = snapshot(8082, project_id)
    assert original == restored, "Restored API results differ from the original snapshot."
    assert recovered == json.loads(
        compose(DESTINATION, args.destination_env, "exec", "-T", "backend", "python", "-c", verify)
    )
    report = {
        "passed": True,
        "llm_requests": 0,
        "project_id": project_id,
        "restart_recovery": recovered,
        "restored_source_count": len(restored["sources"]),
        "restored_index_count": len(restored["indexes"]),
        "checks": [
            "interrupted-run",
            "conservative-token-recovery",
            "restart-idempotence",
            "database-restore",
            "upload-hashes",
            "mappings-metrics",
            "vector-search-citations",
            "history-budget",
        ],
    }
    (output / "verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
