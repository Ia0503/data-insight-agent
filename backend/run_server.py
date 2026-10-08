"""Container startup: migrate once, then run the existing single-process API."""

import sys
from pathlib import Path

import uvicorn
from alembic import command
from alembic.config import Config

ROOT = Path(__file__).resolve().parents[1]


def main():
    try:
        command.upgrade(Config(str(ROOT / "backend/alembic.ini")), "head")
    except Exception as exc:
        print(
            f"Database migration failed ({type(exc).__name__}); API not started.", file=sys.stderr
        )
        raise SystemExit(1) from None
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        workers=1,
        forwarded_allow_ips="*",
        access_log=False,
    )


if __name__ == "__main__":
    main()
