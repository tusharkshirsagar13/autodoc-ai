"""Database engine creation and schema management.

Schema management strategy (academic prototype): tables are created with
`create_all`, which is idempotent and safe on repeated application start-up.
A `schema_info` row records the schema version; if a future version changes
the schema incompatibly, `SCHEMA_VERSION` is bumped and `init_database`
raises a clear message instead of silently corrupting data.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from config.settings import Settings, get_settings
from src.database.models import Base
from src.utils.errors import AutoDocError

SCHEMA_VERSION = 1

_lock = threading.Lock()
_engines: dict[str, Engine] = {}
_session_factories: dict[str, sessionmaker] = {}


def _configure_sqlite(dbapi_connection, _record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=10000")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()


def get_engine(settings: Settings | None = None) -> Engine:
    settings = settings or get_settings()
    url = settings.database_url
    with _lock:
        engine = _engines.get(url)
        if engine is None:
            settings.data_dir.mkdir(parents=True, exist_ok=True)
            engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30}, future=True)
            event.listen(engine, "connect", _configure_sqlite)
            _engines[url] = engine
            init_database(engine)
            _session_factories[url] = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        return engine


def init_database(engine: Engine) -> None:
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE IF NOT EXISTS schema_info (id INTEGER PRIMARY KEY CHECK (id = 1), version INTEGER NOT NULL)"))
        row = conn.execute(text("SELECT version FROM schema_info WHERE id = 1")).fetchone()
        if row is None:
            conn.execute(text("INSERT INTO schema_info (id, version) VALUES (1, :v)"), {"v": SCHEMA_VERSION})
        elif row[0] > SCHEMA_VERSION:
            raise AutoDocError(
                f"Database schema version {row[0]} is newer than this application supports ({SCHEMA_VERSION}).",
                "Upgrade AutoDoc AI or point AUTODOC_DATA_DIR at a different folder.",
            )


@contextmanager
def session_scope(settings: Settings | None = None) -> Iterator[Session]:
    settings = settings or get_settings()
    get_engine(settings)
    factory = _session_factories[settings.database_url]
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def database_status(settings: Settings | None = None) -> dict:
    settings = settings or get_settings()
    try:
        engine = get_engine(settings)
        with engine.connect() as conn:
            version = conn.execute(text("SELECT version FROM schema_info WHERE id = 1")).scalar()
        size = settings.database_path.stat().st_size if settings.database_path.exists() else 0
        return {"ok": True, "path": str(settings.database_path), "schema_version": version, "size_bytes": size}
    except Exception as exc:  # pragma: no cover - surfaced in UI
        return {"ok": False, "path": str(settings.database_path), "error": str(exc)}


def dispose_engines() -> None:
    with _lock:
        for engine in _engines.values():
            engine.dispose()
        _engines.clear()
        _session_factories.clear()
