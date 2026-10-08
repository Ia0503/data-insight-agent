"""Create a local database/configuration/upload snapshot without deleting anything."""

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def main():
    from app.core.config import get_settings
    from sqlalchemy.engine import make_url

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    output = args.output or ROOT / "runtime/backups" / datetime.now(timezone.utc).strftime(
        "%Y%m%dT%H%M%S%fZ"
    )
    output = (ROOT / output).resolve() if not output.is_absolute() else output.resolve()
    if not output.is_relative_to((ROOT / "runtime/backups").resolve()):
        raise RuntimeError("Backup output must stay under this project's runtime/backups.")
    output.mkdir(parents=True, exist_ok=False)
    settings = get_settings()
    url = make_url(settings.database_url.get_secret_value())
    dump = output / "database.dump"
    with dump.open("wb") as stream:
        completed = subprocess.run(
            [
                "docker",
                "compose",
                "exec",
                "-T",
                "db",
                "pg_dump",
                "-U",
                url.username,
                "-d",
                url.database,
                "--format=custom",
            ],
            cwd=ROOT,
            stdout=stream,
            stderr=subprocess.PIPE,
        )
    if completed.returncode or dump.stat().st_size < 100:
        raise RuntimeError("Database backup failed; partial output kept for diagnosis.")
    uploads = settings.upload_dir.resolve()
    files = {}
    with zipfile.ZipFile(output / "uploads.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for file in sorted(uploads.rglob("*")):
            if not file.is_file():
                continue
            if file.is_symlink() or not file.resolve().is_relative_to(uploads):
                raise RuntimeError("Unexpected upload symlink; backup stopped.")
            relative = file.relative_to(uploads).as_posix()
            files[relative] = hashlib.sha256(file.read_bytes()).hexdigest()
            archive.write(file, relative)
    config = ROOT / ".env"
    if config.is_file():
        shutil.copyfile(config, output / "configuration.env")
    (output / "manifest.json").write_text(
        json.dumps(
            {
                "created_at_utc": datetime.now(timezone.utc).isoformat(),
                "git_commit": subprocess.check_output(
                    ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
                ).strip(),
                "database_dump_sha256": hashlib.sha256(dump.read_bytes()).hexdigest(),
                "uploads": files,
                "configuration_included": config.is_file(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"Backup created: {output}; {len(files)} upload files. Keep this directory private.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(
            f"Backup failed ({type(exc).__name__}); no existing files were removed.",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
