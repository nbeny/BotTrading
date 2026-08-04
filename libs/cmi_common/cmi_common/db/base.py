"""SQLAlchemy declarative base."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, MetaData, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Explicit naming convention keeps Alembic autogenerate stable across runs.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class TimestampMixin:
    #: `timestamptz` en base, comme toutes les colonnes temporelles du schema.
    #: Sans le type explicite, SQLAlchemy rend le parametre sans fuseau et toute
    #: lecture filtrant sur un datetime aware leve asyncpg.DataError -- le meme
    #: defaut qui a rendu l'axe positioning muet, ici pour Token, Decision et
    #: Trade d'un seul coup.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
