from alembic import context
from app.core.database import Base, engine
from app.models import (
    agent,  # noqa: F401
    analysis,  # noqa: F401
    call_protection,  # noqa: F401
    workspace,  # noqa: F401
)

target_metadata = Base.metadata

if context.is_offline_mode():
    from app.core.config import get_settings

    context.configure(
        url=get_settings().database_url.get_secret_value(),
        target_metadata=target_metadata,
        literal_binds=True,
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
