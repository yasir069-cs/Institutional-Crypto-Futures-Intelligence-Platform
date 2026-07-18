"""Generic async repository base class.

Implements common CRUD operations so domain repositories stay thin.
Subclasses bind to a specific ORM model and add domain-specific queries.
"""

from __future__ import annotations

from typing import Any, Generic, TypeVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import Base

ModelT = TypeVar("ModelT", bound=Base)


class BaseRepository(Generic[ModelT]):
    """Generic async repository."""

    model: type[ModelT]

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, id_: int) -> ModelT | None:
        result = await self.session.get(self.model, id_)
        return result  # type: ignore[no-any-return]

    async def get_by(self, **filters: Any) -> list[ModelT]:
        """Return all rows matching the given column=value filters."""
        stmt = select(self.model)
        for col, val in filters.items():
            stmt = stmt.where(getattr(self.model, col) == val)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def add(self, instance: ModelT) -> ModelT:
        self.session.add(instance)
        await self.session.flush()
        return instance

    async def add_many(self, instances: list[ModelT]) -> list[ModelT]:
        self.session.add_all(instances)
        await self.session.flush()
        return instances

    async def delete(self, instance: ModelT) -> None:
        await self.session.delete(instance)

    async def count(self, **filters: Any) -> int:
        from sqlalchemy import func

        stmt = select(func.count()).select_from(self.model)
        for col, val in filters.items():
            stmt = stmt.where(getattr(self.model, col) == val)
        result = await self.session.execute(stmt)
        return int(result.scalar() or 0)


__all__ = ["BaseRepository"]
