from app.core.config import get_settings
from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    pass


engine = create_engine(
    get_settings().database_url.get_secret_value(),
    pool_pre_ping=True,
    pool_timeout=5,
    connect_args={
        "connect_timeout": 5,
        "options": (
            f"-c statement_timeout={get_settings().database_statement_timeout_ms}"
            " -c lock_timeout=5000"
        ),
    },
    hide_parameters=True,
)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

INSTANCE_LOCK = 903_050_301


def acquire_instance():
    connection = engine.connect()
    try:
        acquired = connection.scalar(
            text("SELECT pg_try_advisory_lock(:key)"), {"key": INSTANCE_LOCK}
        )
        connection.commit()
        if not acquired:
            raise RuntimeError("另一个后端实例正在使用本数据库；请先停止原实例。")
        return connection
    except Exception:
        connection.close()
        raise


def release_instance(connection):
    try:
        connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": INSTANCE_LOCK})
        connection.commit()
    except Exception:
        connection.invalidate()
    finally:
        connection.close()


def get_db():
    with SessionLocal() as session:
        yield session
