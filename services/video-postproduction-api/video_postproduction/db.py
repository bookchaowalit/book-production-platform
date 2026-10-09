"""SQLAlchemy engine and session lifecycle."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import Settings, get_settings


class Base(DeclarativeBase):
    pass


def _ensure_sqlite_parent(database_url: str) -> None:
    if not database_url.startswith("sqlite:///") or ":memory:" in database_url:
        return
    database_path = database_url.removeprefix("sqlite:///")
    Path(database_path).expanduser().parent.mkdir(parents=True, exist_ok=True)


def make_engine(settings: Settings | None = None) -> Engine:
    runtime_settings = settings or get_settings()
    _ensure_sqlite_parent(runtime_settings.database_url)
    connect_args = {"check_same_thread": False} if runtime_settings.database_url.startswith("sqlite") else {}
    return create_engine(
        runtime_settings.database_url,
        connect_args=connect_args,
        pool_pre_ping=True,
        future=True,
    )


def make_session_factory(settings: Settings | None = None) -> sessionmaker[Session]:
    return sessionmaker(bind=make_engine(settings), autoflush=False, expire_on_commit=False, class_=Session)


SessionLocal = make_session_factory()


def get_session() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
