"""Run a test-only backend; refuse to use the development database."""

import os
from pathlib import Path

import uvicorn
from dotenv import dotenv_values
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
url = os.environ.get("TEST_DATABASE_URL") or dotenv_values(ROOT / ".env").get("TEST_DATABASE_URL")
if not url or make_url(url).database != "newai_test":
    raise RuntimeError("TEST_DATABASE_URL must point to newai_test.")
os.environ["DATABASE_URL"] = url
os.environ["UPLOAD_DIR"] = str(ROOT / "test" / "results" / "uploads")
os.environ["LLM_CALLS_ENABLED"] = "false"
os.environ["ANALYSIS_PASSWORD_REQUIRED"] = "false"
if __name__ == "__main__":
    uvicorn.run("app.main:app", host="127.0.0.1", port=8001)
