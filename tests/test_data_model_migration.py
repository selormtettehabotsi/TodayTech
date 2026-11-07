"""Test Task 1: Data model and migration."""

from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from todaytech.core.database import get_session, init_db
from todaytech.core.models import DateStatus, Opportunity, OpportunityType


def test_opportunity_pydantic_fields():
    opp = Opportunity(
        title="Test Opportunity",
        type=OpportunityType.SCHOLARSHIP,
        source_url="https://example.com/scholarship",
        date_status=DateStatus.CONFIRMED,
        estimated_window="mid March 2027",
        cycle_year=2027,
        date_evidence="Official 2027 cycle dates read on organizer page",
        conflict=False,
        verified_on=date(2026, 11, 7),
    )
    assert opp.date_status == DateStatus.CONFIRMED
    assert opp.cycle_year == 2027
    assert opp.date_evidence == "Official 2027 cycle dates read on organizer page"
    assert opp.source_url == "https://example.com/scholarship"
    assert opp.verified_on == date(2026, 11, 7)


@pytest.mark.asyncio
async def test_schema_migration_sets_existing_to_unknown(tmp_path):
    db_file = tmp_path / "test_migration.db"
    db_url = f"sqlite+aiosqlite:///{db_file.as_posix()}"

    # 1. Create a legacy table without the new columns
    engine = create_async_engine(db_url)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                CREATE TABLE opportunities (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    external_id VARCHAR(256) UNIQUE,
                    title VARCHAR(512),
                    type VARCHAR(64),
                    organizer VARCHAR(256) DEFAULT '',
                    deadline DATE,
                    start_date DATE,
                    location VARCHAR(256) DEFAULT '',
                    mode VARCHAR(32) DEFAULT '',
                    cost FLOAT DEFAULT 0,
                    eligibility_text TEXT DEFAULT '',
                    countries_allowed TEXT DEFAULT '',
                    level_required VARCHAR(64) DEFAULT '',
                    field_required VARCHAR(128) DEFAULT '',
                    reward TEXT DEFAULT '',
                    application_url VARCHAR(1024) DEFAULT '',
                    source_url VARCHAR(1024) DEFAULT '',
                    confidence FLOAT DEFAULT 0.0,
                    fit_score INTEGER DEFAULT 0,
                    fit_reason TEXT DEFAULT '',
                    eligibility_verdict VARCHAR(32) DEFAULT 'Check',
                    eligibility_reason TEXT DEFAULT '',
                    is_scam BOOLEAN DEFAULT 0,
                    scam_reason TEXT DEFAULT '',
                    source_name VARCHAR(128) DEFAULT '',
                    raw_text TEXT DEFAULT '',
                    first_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
                    last_seen DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
        # Insert a legacy row
        await conn.execute(
            text(
                """
                INSERT INTO opportunities (external_id, title, type, source_url)
                VALUES ('legacy_1', 'Legacy GSoC', 'internship', 'https://example.com')
                """
            )
        )
    await engine.dispose()

    # 2. Run init_db which triggers _migrate_schema
    await init_db(db_url)

    # 3. Verify columns and that existing row has date_status='unknown'
    async with get_session() as session:
        result = await session.execute(
            text("SELECT external_id, date_status, verified_on FROM opportunities")
        )
        row = result.fetchone()
        assert row is not None
        assert row[0] == "legacy_1"
        assert row[1] == "unknown"
        assert row[2] is None
