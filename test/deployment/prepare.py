"""Generate an ignored configuration for an isolated Compose test project."""

import argparse
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))


def main():
    from app.core.access import password_hash
    from dotenv import set_key

    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--web-port", type=int, default=8081)
    parser.add_argument("--db-port", type=int, default=55632)
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to((ROOT / "test/results").resolve()) or output.exists():
        raise RuntimeError("Use a new configuration file under test/results.")
    output.parent.mkdir(parents=True, exist_ok=True)
    password = secrets.token_hex(24)
    source = (ROOT / ".env.example").read_text(encoding="utf-8")
    source = source.replace("YOUR_DATABASE_PASSWORD_HERE", password).replace(
        "POSTGRES_DB=newai\n", "POSTGRES_DB=newai_test\n"
    )
    source = source.replace("/newai\n", "/newai_test\n")
    output.write_text(source, encoding="utf-8")
    for key, value in {
        "POSTGRES_PORT": str(args.db_port),
        "DATABASE_URL": f"postgresql+psycopg://newai:{password}@127.0.0.1:{args.db_port}/newai_test",
        "TEST_DATABASE_URL": f"postgresql+psycopg://newai:{password}@127.0.0.1:{args.db_port}/newai_test",
        "TEST_POSTGRES_PORT": str(args.db_port),
        "TEST_WEB_PORT": str(args.web_port),
        "NEWAI_ENV_FILE_HOST": output.relative_to(ROOT).as_posix(),
        "ANALYSIS_PASSWORD_HASH": password_hash("test-only-call-password"),
        "LLM_CALLS_ENABLED": "false",
    }.items():
        set_key(output, key, value, encoding="utf-8")
    print("Isolated test configuration created; credentials not displayed.")


if __name__ == "__main__":
    main()
