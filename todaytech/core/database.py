"""SQLAlchemy async database layer with Alembic support."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import structlog
from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    func,
    select,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# Engine / Session factory (populated by init_db)
# ---------------------------------------------------------------------------

_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------


class OpportunityRow(Base):
    __tablename__ = "opportunities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    external_id: Mapped[str] = mapped_column(String(256), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(512))
    type: Mapped[str] = mapped_column(String(64), index=True)
    organizer: Mapped[str] = mapped_column(String(256), default="")
    deadline: Mapped[date | None] = mapped_column(nullable=True)
    start_date: Mapped[date | None] = mapped_column(nullable=True)
    location: Mapped[str] = mapped_column(String(256), default="")
    mode: Mapped[str] = mapped_column(String(32), default="")
    cost: Mapped[float] = mapped_column(Float, default=0)
    eligibility_text: Mapped[str] = mapped_column(Text, default="")
    countries_allowed: Mapped[str] = mapped_column(Text, default="")  # JSON list
    level_required: Mapped[str] = mapped_column(String(64), default="")
    field_required: Mapped[str] = mapped_column(String(128), default="")
    reward: Mapped[str] = mapped_column(Text, default="")
    application_url: Mapped[str] = mapped_column(String(1024), default="")
    source_url: Mapped[str] = mapped_column(String(1024), default="")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)

    # Verification & Date Confidence fields
    date_status: Mapped[str] = mapped_column(String(32), default="unknown")
    estimated_window: Mapped[str | None] = mapped_column(String(256), nullable=True, default=None)
    cycle_year: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    date_evidence: Mapped[str] = mapped_column(Text, default="")
    conflict: Mapped[bool] = mapped_column(Boolean, default=False)
    verified_on: Mapped[date | None] = mapped_column(nullable=True, default=None)
    fetched_on: Mapped[date | None] = mapped_column(nullable=True, default=None)
    days_remaining: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)

    fit_score: Mapped[int] = mapped_column(Integer, default=0)
    fit_reason: Mapped[str] = mapped_column(Text, default="")
    eligibility_verdict: Mapped[str] = mapped_column(String(32), default="Check")
    eligibility_reason: Mapped[str] = mapped_column(Text, default="")
    is_scam: Mapped[bool] = mapped_column(Boolean, default=False)
    scam_reason: Mapped[str] = mapped_column(Text, default="")
    source_name: Mapped[str] = mapped_column(String(128), default="")
    raw_text: Mapped[str] = mapped_column(Text, default="")
    first_seen: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    last_seen: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class ScanRow(Base):
    __tablename__ = "scans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    sources_used: Mapped[str] = mapped_column(Text, default="")  # JSON list
    items_found: Mapped[int] = mapped_column(Integer, default=0)
    items_new: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[str] = mapped_column(Text, default="")  # JSON list


class UserStateRow(Base):
    __tablename__ = "user_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    opportunity_id: Mapped[str] = mapped_column(String(256), index=True)
    status: Mapped[str] = mapped_column(String(32), default="saved")  # saved, ignored, applied
    notes: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class ApplicationRow(Base):
    __tablename__ = "applications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    opportunity_id: Mapped[str] = mapped_column(String(256), index=True)
    status: Mapped[str] = mapped_column(String(32), default="drafting")
    requirements_json: Mapped[str] = mapped_column(Text, default="")
    checklist_json: Mapped[str] = mapped_column(Text, default="")
    drafts_dir: Mapped[str] = mapped_column(String(512), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class ReminderRow(Base):
    __tablename__ = "reminders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    opportunity_id: Mapped[str] = mapped_column(String(256), index=True)
    remind_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    message: Mapped[str] = mapped_column(Text, default="")
    sent: Mapped[bool] = mapped_column(Boolean, default=False)


class SourceRow(Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(256), unique=True)
    type: Mapped[str] = mapped_column(String(64))
    value: Mapped[str] = mapped_column(String(1024))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_checked: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str] = mapped_column(Text, default="")
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)


# ---------------------------------------------------------------------------
# Init
# ---------------------------------------------------------------------------


def _migrate_schema(connection) -> None:
    """Ensure all required columns exist and migrate existing rows."""
    from sqlalchemy import inspect, text

    inspector = inspect(connection)
    existing_tables = inspector.get_table_names()
    if "opportunities" not in existing_tables:
        return

    columns = {col["name"] for col in inspector.get_columns("opportunities")}

    alter_statements = []
    if "date_status" not in columns:
        alter_statements.append(
            "ALTER TABLE opportunities ADD COLUMN date_status VARCHAR(32) DEFAULT 'unknown'"
        )
    if "estimated_window" not in columns:
        alter_statements.append(
            "ALTER TABLE opportunities ADD COLUMN estimated_window VARCHAR(256)"
        )
    if "cycle_year" not in columns:
        alter_statements.append("ALTER TABLE opportunities ADD COLUMN cycle_year INTEGER")
    if "date_evidence" not in columns:
        alter_statements.append(
            "ALTER TABLE opportunities ADD COLUMN date_evidence TEXT DEFAULT ''"
        )
    if "conflict" not in columns:
        alter_statements.append("ALTER TABLE opportunities ADD COLUMN conflict BOOLEAN DEFAULT 0")
    if "source_url" not in columns:
        alter_statements.append(
            "ALTER TABLE opportunities ADD COLUMN source_url VARCHAR(1024) DEFAULT ''"
        )
    if "verified_on" not in columns:
        alter_statements.append("ALTER TABLE opportunities ADD COLUMN verified_on DATE")
    if "fetched_on" not in columns:
        alter_statements.append("ALTER TABLE opportunities ADD COLUMN fetched_on DATE")
    if "days_remaining" not in columns:
        alter_statements.append("ALTER TABLE opportunities ADD COLUMN days_remaining INTEGER")

    for stmt in alter_statements:
        try:
            connection.execute(text(stmt))
        except Exception as exc:
            log.warning("database.migration_column_error", stmt=stmt, error=str(exc))

    # Task 1: Set every existing row to "unknown" until it is re-verified
    try:
        connection.execute(
            text("UPDATE opportunities SET date_status = 'unknown' WHERE verified_on IS NULL")
        )
    except Exception as exc:
        log.warning("database.migration_update_error", error=str(exc))


async def init_db(database_url: str) -> None:
    """Create engine, session factory, and all tables."""
    global _engine, _session_factory

    # Ensure data/ dir exists for SQLite
    if "sqlite" in database_url:
        try:
            clean_path = database_url.split(":///")[-1]
            if clean_path:
                Path(clean_path).parent.mkdir(parents=True, exist_ok=True)
            Path("data").mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

    _engine = create_async_engine(database_url, echo=False)
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)

    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_migrate_schema)

    log.info("database.initialized", url=database_url.split("://")[0] + "://***")


def get_session() -> AsyncSession:
    """Return a new async session."""
    if _session_factory is None:
        raise RuntimeError("Database not initialized — call init_db() first.")
    return _session_factory()


# ---------------------------------------------------------------------------
# CRUD helpers
# ---------------------------------------------------------------------------


async def upsert_opportunity(session: AsyncSession, data: dict) -> tuple[OpportunityRow, bool]:
    """Insert or update an opportunity.  Returns (row, is_new)."""
    import json

    if isinstance(data.get("countries_allowed"), list):
        data = dict(data)
        data["countries_allowed"] = json.dumps(data["countries_allowed"])

    ext_id = data.get("external_id", "")
    stmt = select(OpportunityRow).where(OpportunityRow.external_id == ext_id)
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()

    if row is None:
        row = OpportunityRow(**data)
        session.add(row)
        await session.flush()
        return row, True

    # Update existing
    for key, value in data.items():
        if key != "id" and key != "first_seen":
            setattr(row, key, value)
    await session.flush()
    return row, False


async def get_opportunity_by_id(session: AsyncSession, external_id: str) -> OpportunityRow | None:
    """Fetch a single opportunity by external ID (exact or prefix)."""
    stmt = select(OpportunityRow).where(OpportunityRow.external_id == external_id)
    result = await session.execute(stmt)
    row = result.scalars().first()
    if row is not None:
        return row
    stmt_prefix = select(OpportunityRow).where(OpportunityRow.external_id.startswith(external_id))
    result_prefix = await session.execute(stmt_prefix)
    return result_prefix.scalars().first()


async def get_opportunities(
    session: AsyncSession,
    *,
    type_filter: str | None = None,
    min_score: int = 0,
    limit: int | None = 50,
) -> list[OpportunityRow]:
    """Fetch opportunities, ordered by fit score descending. ``limit=None`` returns all rows."""
    stmt = select(OpportunityRow).order_by(OpportunityRow.fit_score.desc())
    if type_filter:
        stmt = stmt.where(OpportunityRow.type == type_filter)
    if min_score > 0:
        stmt = stmt.where(OpportunityRow.fit_score >= min_score)
    if limit is not None:
        stmt = stmt.limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def set_user_state(
    session: AsyncSession,
    opportunity_id: str,
    status: str,
    notes: str = "",
) -> UserStateRow:
    """Save, ignore, or update status of an opportunity."""
    stmt = select(UserStateRow).where(UserStateRow.opportunity_id == opportunity_id)
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    if row is None:
        row = UserStateRow(opportunity_id=opportunity_id, status=status, notes=notes)
        session.add(row)
    else:
        row.status = status
        if notes:
            row.notes = notes
    await session.flush()
    return row


async def get_saved_opportunities(session: AsyncSession) -> list[UserStateRow]:
    """Get all saved opportunities."""
    stmt = select(UserStateRow).where(UserStateRow.status == "saved")
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_last_scan(session: AsyncSession) -> ScanRow | None:
    """Get the most recent scan."""
    stmt = select(ScanRow).order_by(ScanRow.started_at.desc()).limit(1)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()
