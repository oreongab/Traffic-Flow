"""
PostgreSQL Connection — TraffixFlow (SQLAlchemy + Neon)
"""

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker, scoped_session
from config import Config

_engine = None
_session_factory = None


def _active_metadata_tables(base) -> list:
    return [
        table
        for table in base.metadata.sorted_tables
        if not bool(table.info.get("optional_runtime_table"))
    ]


def get_engine():
    """Get or create the SQLAlchemy engine."""
    global _engine
    if _engine is None:
        uri = Config.DATABASE_URI
        is_sqlite = uri.startswith("sqlite")
        kwargs = {}
        if not is_sqlite:
            kwargs.update(pool_size=5, max_overflow=10, pool_pre_ping=True)
        _engine = create_engine(uri, **kwargs)
    return _engine


def get_session():
    """Get a thread-safe scoped session."""
    global _session_factory
    if _session_factory is None:
        engine = get_engine()
        factory = sessionmaker(bind=engine)
        _session_factory = scoped_session(factory)
    return _session_factory()


def init_db():
    """Create all tables."""
    from database.models import Base
    engine = get_engine()
    Base.metadata.create_all(engine, tables=_active_metadata_tables(Base))
    _ensure_runtime_columns(engine)
    print("PostgreSQL initialized - all tables ready")


def _ensure_runtime_columns(engine) -> None:
    """Apply additive fixes for legacy schemas that predate normalized runtime columns."""
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    if "cameras" not in table_names:
        return

    column_names = {column["name"] for column in inspector.get_columns("cameras")}
    statements: list[str] = []
    if "road_id" not in column_names:
        statements.append("ALTER TABLE cameras ADD COLUMN road_id VARCHAR(100)")
        statements.append("UPDATE cameras SET road_id = road WHERE road_id IS NULL AND COALESCE(road, '') <> ''")
    if "junction_id" not in column_names:
        statements.append("ALTER TABLE cameras ADD COLUMN junction_id VARCHAR(255)")
        statements.append("UPDATE cameras SET junction_id = junction WHERE junction_id IS NULL AND COALESCE(junction, '') <> ''")

    if not str(engine.url).startswith("sqlite"):
        varchar_targets = [
            ("junctions", "junction_id", 255),
            ("junctions", "sumo_tls_id", 255),
            ("cameras", "junction_id", 255),
            ("cameras", "sumo_tls_id", 255),
            ("approaches", "junction_id", 255),
            ("signal_controllers", "junction_id", 255),
            ("signal_states", "junction_id", 255),
            ("ai_decisions", "junction_id", 255),
        ]

        for table_name, column_name, target_length in varchar_targets:
            if table_name not in table_names:
                continue
            try:
                columns = {column["name"]: column for column in inspector.get_columns(table_name)}
            except Exception:
                continue
            column = columns.get(column_name)
            if column is None:
                continue
            current_length = getattr(column.get("type"), "length", None)
            if current_length is not None and current_length >= target_length:
                continue
            statements.append(
                f"ALTER TABLE {table_name} ALTER COLUMN {column_name} TYPE VARCHAR({target_length})"
            )

    if not statements:
        return

    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))


def close_db():
    """Dispose engine and clear session."""
    global _engine, _session_factory
    if _session_factory:
        _session_factory.remove()
        _session_factory = None
    if _engine:
        _engine.dispose()
        _engine = None
