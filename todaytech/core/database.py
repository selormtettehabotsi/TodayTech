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
    or_,
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
    dedupe_key: Mapped[str] = mapped_column(String(256), index=True, default="")

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
    if "dedupe_key" not in columns:
        alter_statements.append(
            "ALTER TABLE opportunities ADD COLUMN dedupe_key VARCHAR(256) DEFAULT ''"
        )

    for stmt in alter_statements:
        try:
            connection.execute(text(stmt))
        except Exception as exc:
            log.warning("database.migration_column_error", stmt=stmt, error=str(exc))

    # Task 3: Remove existing roundup and listicle entries from the database
    from todaytech.core.dedupe import (
        compute_dedupe_key,
        opportunity_rank,
    )
    from todaytech.core.roundup_filter import is_roundup_or_directory

    try:
        res_all = connection.execute(
            text(
                "SELECT id, title, organizer, source_url, application_url, external_id FROM opportunities"
            )
        )
        roundup_ids = []
        for row in res_all.fetchall():
            is_roundup, _ = is_roundup_or_directory(row[1], row[2], row[3], row[4])
            if is_roundup:
                roundup_ids.append(row[0])

        if roundup_ids:
            log.info("database.migration_purging_roundups", count=len(roundup_ids))
            for i in range(0, len(roundup_ids), 200):
                chunk = roundup_ids[i : i + 200]
                placeholders = ",".join(str(cid) for cid in chunk)
                connection.execute(text(f"DELETE FROM opportunities WHERE id IN ({placeholders})"))
    except Exception as exc:
        log.warning("database.migration_roundup_purge_error", error=str(exc))

    # Task 4: Populate dedupe_key on existing rows
    try:
        connection.execute(text("DROP INDEX IF EXISTS uq_opportunities_dedupe_key"))
        res_need_key = connection.execute(
            text(
                "SELECT id, title, organizer, cycle_year, source_url, application_url FROM opportunities WHERE dedupe_key IS NULL OR dedupe_key = ''"
            )
        )
        for row in res_need_key.fetchall():
            dkey = compute_dedupe_key(row[1], row[2], row[3], row[4], row[5])
            connection.execute(
                text("UPDATE opportunities SET dedupe_key = :dk WHERE id = :id"),
                {"dk": dkey, "id": row[0]},
            )
    except Exception as exc:
        log.warning("database.migration_dedupe_key_error", error=str(exc))

    # Task 4: Dedupe existing rows against each other before applying unique index
    try:
        # Group by dedupe_key
        res_groups = connection.execute(
            text(
                "SELECT dedupe_key, COUNT(*) as cnt FROM opportunities WHERE dedupe_key != '' GROUP BY dedupe_key HAVING cnt > 1"
            )
        )
        dup_keys = [r[0] for r in res_groups.fetchall()]
        for dk in dup_keys:
            rows_res = connection.execute(
                text(
                    "SELECT id, external_id, date_status, source_url, verified_on, fit_score, organizer, title, reward, location, application_url, estimated_window FROM opportunities WHERE dedupe_key = :dk"
                ),
                {"dk": dk},
            )
            items = rows_res.fetchall()
            if len(items) <= 1:
                continue

            # Pick winner by opportunity_rank
            class _MockOpp:
                def __init__(self, r):
                    self.id = r[0]
                    self.external_id = r[1]
                    self.date_status = r[2]
                    self.source_url = r[3]
                    self.verified_on = r[4]
                    self.fit_score = r[5]
                    self.organizer = r[6]
                    self.title = r[7]
                    self.reward = r[8]
                    self.location = r[9]
                    self.application_url = r[10]
                    self.estimated_window = r[11]

            sorted_items = sorted(
                items, key=lambda it: opportunity_rank(_MockOpp(it)), reverse=True
            )
            winner = sorted_items[0]
            losers = sorted_items[1:]

            # Merge loser details into winner if winner lacks them
            update_fields = {}
            if not winner[8]:  # reward
                for loser in losers:
                    if loser[8]:
                        update_fields["reward"] = loser[8]
                        break
            if not winner[9]:  # location
                for loser in losers:
                    if loser[9]:
                        update_fields["location"] = loser[9]
                        break
            if not winner[10]:  # application_url
                for loser in losers:
                    if loser[10]:
                        update_fields["application_url"] = loser[10]
                        break

            if update_fields:
                set_clauses = ", ".join(f"{k} = :{k}" for k in update_fields)
                update_fields["wid"] = winner[0]
                connection.execute(
                    text(f"UPDATE opportunities SET {set_clauses} WHERE id = :wid"),
                    update_fields,
                )

            # Re-link any user_state from loser external_ids to winner external_id
            for loser in losers:
                connection.execute(
                    text("UPDATE user_state SET opportunity_id = :wid WHERE opportunity_id = :lid"),
                    {"wid": winner[1], "lid": loser[1]},
                )

            # Delete loser rows
            loser_ids = ",".join(str(loser[0]) for loser in losers)
            connection.execute(text(f"DELETE FROM opportunities WHERE id IN ({loser_ids})"))
    except Exception as exc:
        log.warning("database.migration_dedupe_merge_error", error=str(exc))

    # Task 4: Unique index on dedupe_key column
    try:
        connection.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_opportunities_dedupe_key ON opportunities(dedupe_key)"
            )
        )
    except Exception as exc:
        log.warning("database.migration_unique_index_error", error=str(exc))

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


async def upsert_opportunity(
    session: AsyncSession, data: dict
) -> tuple[OpportunityRow | None, bool]:
    """Insert or update an opportunity with cross-database deduplication and roundup rejection.

    Returns (row, is_new).
    """
    import json

    from todaytech.core.dedupe import (
        canonicalize_url,
        compute_dedupe_key,
        merge_two_opportunity_records,
        opportunity_rank,
    )
    from todaytech.core.roundup_filter import is_roundup_or_directory

    # Task 3: Reject roundup/listicle pages
    is_roundup, reason = is_roundup_or_directory(
        data.get("title", ""),
        data.get("organizer", ""),
        data.get("source_url", ""),
        data.get("application_url", ""),
    )
    if is_roundup:
        log.info("database.upsert_dropped_roundup", title=data.get("title"), reason=reason)
        return None, False

    data = dict(data)
    if isinstance(data.get("countries_allowed"), list):
        data["countries_allowed"] = json.dumps(data["countries_allowed"])

    # Normalise URLs
    src_url = canonicalize_url(data.get("source_url", ""))
    app_url = canonicalize_url(data.get("application_url", ""))
    data["source_url"] = src_url
    data["application_url"] = app_url

    # Task 4: Dedupe key
    dedupe_key = data.get("dedupe_key")
    if not dedupe_key:
        dedupe_key = compute_dedupe_key(
            title=data.get("title", ""),
            organizer=data.get("organizer", ""),
            cycle_year=data.get("cycle_year"),
            source_url=src_url,
            application_url=app_url,
        )
    data["dedupe_key"] = dedupe_key

    ext_id = data.get("external_id", "")

    # Query for duplicate matches in database
    conditions = [OpportunityRow.dedupe_key == dedupe_key]
    if ext_id:
        conditions.append(OpportunityRow.external_id == ext_id)
    if src_url:
        conditions.append(OpportunityRow.source_url == src_url)
    if app_url:
        conditions.append(OpportunityRow.application_url == app_url)

    stmt = select(OpportunityRow).where(or_(*conditions))
    result = await session.execute(stmt)
    existing_rows = list(result.scalars().all())

    if not existing_rows:
        row = OpportunityRow(**data)
        session.add(row)
        await session.flush()
        return row, True

    # Existing row found — compare precedence
    row = existing_rows[0]

    incoming_rank = opportunity_rank(data)
    existing_rank = opportunity_rank(row)

    if incoming_rank >= existing_rank:
        # Incoming wins: merge existing fields into incoming
        winner_data = merge_two_opportunity_records(
            data,
            {
                "reward": row.reward,
                "location": row.location,
                "application_url": row.application_url,
                "source_url": row.source_url,
                "estimated_window": row.estimated_window,
            },
        )
        for key, value in winner_data.items():
            if key != "id" and key != "first_seen":
                setattr(row, key, value)
    else:
        # Existing wins: merge incoming non-empty fields into existing
        if not row.reward and data.get("reward"):
            row.reward = data["reward"]
        if not row.location and data.get("location"):
            row.location = data["location"]
        if not row.application_url and data.get("application_url"):
            row.application_url = data["application_url"]

    row.last_seen = func.now()
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
