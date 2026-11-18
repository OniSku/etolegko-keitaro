import asyncio

from alembic import context
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import Settings
from app.models import Base

settings = Settings()


async def migrate() -> None:
    engine = create_async_engine(settings.database_url.get_secret_value())
    async with engine.connect() as connection:

        def run(sync_connection: Connection) -> None:
            context.configure(
                connection=sync_connection, target_metadata=Base.metadata, compare_type=True
            )
            with context.begin_transaction():
                context.run_migrations()

        await connection.run_sync(run)
    await engine.dispose()


asyncio.run(migrate())
