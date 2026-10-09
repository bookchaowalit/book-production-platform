import logging.config
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

from video_postproduction.config import get_settings


def test_migrations_resolve_when_started_from_api_working_directory(monkeypatch) -> None:
    config_path = Path(__file__).parents[1] / "alembic.ini"
    expected_migrations = config_path.parent / "migrations"
    monkeypatch.chdir(config_path.parent)

    config = Config(str(config_path))
    scripts = ScriptDirectory.from_config(config)

    assert Path(scripts.dir).resolve() == expected_migrations.resolve()


def test_head_migration_adds_fast_transcription_quality(monkeypatch, tmp_path) -> None:
    config_path = Path(__file__).parents[1] / "alembic.ini"
    database_path = tmp_path / "migration.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    monkeypatch.chdir(config_path.parent)
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setattr(logging.config, "fileConfig", lambda *args, **kwargs: None)
    config = Config(str(config_path))
    get_settings.cache_clear()
    try:
        command.upgrade(config, "0002_render_outputs")
        engine = create_engine(database_url)
        try:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        INSERT INTO projects
                            (id, name, instructions, status, created_at, updated_at)
                        VALUES
                            (:id, :name, NULL, :status, :created_at, :updated_at)
                        """
                    ),
                    {
                        "id": "00000000-0000-0000-0000-000000000001",
                        "name": "Existing project",
                        "status": "ready_for_edit",
                        "created_at": "2026-09-29 00:00:00",
                        "updated_at": "2026-09-29 00:00:00",
                    },
                )
        finally:
            engine.dispose()
        get_settings.cache_clear()
        command.upgrade(config, "head")
    finally:
        get_settings.cache_clear()

    engine = create_engine(database_url)
    try:
        columns = {column["name"]: column for column in inspect(engine).get_columns("projects")}
        with engine.connect() as connection:
            quality = connection.execute(
                text("SELECT transcription_quality FROM projects WHERE name = 'Existing project'")
            ).scalar_one()
    finally:
        engine.dispose()

    assert columns["transcription_quality"]["nullable"] is False
    assert quality == "fast"
