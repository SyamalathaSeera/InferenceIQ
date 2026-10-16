from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver


@asynccontextmanager
async def open_checkpointer(url: str) -> AsyncIterator[BaseCheckpointSaver]:
    """Open a durable local or production LangGraph checkpointer."""

    if url.startswith(("postgres://", "postgresql://", "postgresql+asyncpg://")):
        postgres_url = url.replace("postgresql+asyncpg://", "postgresql://", 1)
        async with AsyncPostgresSaver.from_conn_string(postgres_url) as saver:
            await saver.setup()
            yield saver
        return

    async with AsyncSqliteSaver.from_conn_string(url) as saver:
        await saver.setup()
        yield saver
