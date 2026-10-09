from __future__ import annotations

import json
import sys
from pathlib import Path
from uuid import UUID

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

SERVICE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SERVICE_ROOT.parents[1]
sys.path.insert(0, str(SERVICE_ROOT))
TRACE_ROOT = REPO_ROOT / "packages" / "production-trace"
if str(TRACE_ROOT) not in sys.path:
    sys.path.append(str(TRACE_ROOT))

try:
    from video_postproduction.db import Base
except ImportError:
    Base = None

try:
    from video_postproduction.main import app
except ImportError:
    app = None


@pytest.fixture
def session() -> Session:
    if Base is None:
        yield None  # type: ignore[misc]
        return
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    with factory() as database_session:
        yield database_session


@pytest_asyncio.fixture
async def client():
    if app is None:
        yield None
        return
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as test_client:
        yield test_client


def valid_initial_edl(project_id: UUID) -> dict[str, object]:
    example_path = (
        REPO_ROOT
        / "packages"
        / "video-postproduction-edl"
        / "examples"
        / "initial-edl.json"
    )
    document = json.loads(example_path.read_text(encoding="utf-8"))
    document["project_id"] = str(project_id)
    return document
