import os
from pathlib import Path

import pytest
from dotenv import dotenv_values
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
test_url = os.environ.get("TEST_DATABASE_URL") or dotenv_values(ROOT / ".env").get(
    "TEST_DATABASE_URL"
)
if not test_url or make_url(test_url).database != "newai_test":
    raise RuntimeError("Tests require an explicit TEST_DATABASE_URL with database name newai_test.")
os.environ["DATABASE_URL"] = test_url
os.environ["UPLOAD_DIR"] = str(ROOT / "test" / "results" / "uploads")
# 自动测试不得读取本地密钥后发起真实 LLM 请求。
os.environ["LLM_CALLS_ENABLED"] = "false"
os.environ["ANALYSIS_PASSWORD_REQUIRED"] = "false"
# 既有模拟模型不用于费用测试；限额专项用例会设置自己的小额上限。
os.environ["LLM_HOURLY_TOKEN_LIMIT"] = "1000000000"


@pytest.fixture(scope="session")
def migrate_test_database():
    from alembic import command
    from alembic.config import Config

    command.upgrade(Config(str(ROOT / "backend" / "alembic.ini")), "head")


@pytest.fixture
def client(migrate_test_database):
    from app.main import app
    from fastapi.testclient import TestClient

    with TestClient(app) as value:
        yield value


@pytest.fixture
def project(client):
    response = client.post(
        "/api/projects", json={"name": "Integration project(test)", "description": "test"}
    )
    assert response.status_code == 201
    return response.json()["id"]
