"""Check container restart persistence without deleting development data."""

import subprocess

from app.core.database import SessionLocal, engine
from app.models.workspace import Project
from sqlalchemy import select

with SessionLocal() as db:
    project = Project(name="持久化验证项目(test)", description="验证 Docker 重启后记录仍存在")
    db.add(project)
    db.commit()
    project_id = project.id
subprocess.run(["docker", "compose", "restart", "db"], check=True)
subprocess.run(["docker", "compose", "up", "-d", "--wait", "db"], check=True)
engine.dispose()
with SessionLocal() as db:
    project = db.scalar(select(Project).where(Project.id == project_id))
    assert project and project.name == "持久化验证项目(test)"
print("Development record survived a database container restart.")
