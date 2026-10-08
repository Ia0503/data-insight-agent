from app.api.workspace import Db
from app.schemas.workspace import ProjectOutput, SourceOutput
from app.services import examples
from fastapi import APIRouter

router = APIRouter(prefix="/api/examples/business")


@router.get("")
def get_example(db: Db):
    value = examples.status(db)
    value["project"] = ProjectOutput.model_validate(value["project"]) if value["project"] else None
    return value


@router.post("", response_model=ProjectOutput)
def prepare_example(db: Db):
    return examples.prepare(db)


@router.post("/files/{filename}")
def import_example(filename: str, db: Db):
    source, warning = examples.import_asset(db, filename)
    return {"source": SourceOutput.model_validate(source), "warning": warning}
