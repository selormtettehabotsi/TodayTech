"""One-off database migration script to remove roundups and merge duplicate opportunities.

Usage:
    python -m todaytech.scripts.merge_duplicates [--db-path PATH]
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import date
from pathlib import Path

from todaytech.core.config import get_settings
from todaytech.core.dedupe import (
    canonicalize_url,
    compute_dedupe_key,
    is_official_domain,
    opportunity_rank,
)
from todaytech.core.roundup_filter import is_roundup_or_directory


class MockRow:
    """Helper for passing tuple data to opportunity_rank."""

    def __init__(
        self,
        row_id: int,
        ext_id: str,
        title: str,
        org: str,
        date_status: str,
        deadline: str | None,
        source_url: str,
        verified_on: str | None,
        fit_score: int,
    ) -> None:
        self.id = row_id
        self.external_id = ext_id
        self.title = title
        self.organizer = org
        self.date_status = date_status
        self.deadline = deadline
        self.source_url = source_url
        self.verified_on: date | None = None
        if verified_on:
            try:
                self.verified_on = date.fromisoformat(str(verified_on)[:10])
            except Exception:
                self.verified_on = None
        self.fit_score = fit_score or 0


def run_merge_duplicates(db_path: str) -> dict:
    """Execute the roundup purge and duplicate merge against the SQLite database."""
    con = sqlite3.connect(db_path)
    cur = con.cursor()

    report: dict = {
        "db_path": db_path,
        "initial_total": 0,
        "roundups_removed": 0,
        "duplicate_groups_merged": 0,
        "duplicates_removed": 0,
        "final_total": 0,
        "merged_details": [],
    }

    # 1. Check if opportunities table exists
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='opportunities'")
    if not cur.fetchone():
        print(f"No 'opportunities' table found in {db_path}.")
        con.close()
        return report

    # 2. Add any missing columns to schema
    cur.execute("PRAGMA table_info(opportunities)")
    columns = [col[1] for col in cur.fetchall()]
    schema_additions = [
        ("dedupe_key", "VARCHAR(256) DEFAULT ''"),
        ("date_status", "VARCHAR(32) DEFAULT 'unknown'"),
        ("estimated_window", "VARCHAR(256)"),
        ("cycle_year", "INTEGER"),
        ("date_evidence", "TEXT"),
        ("conflict", "BOOLEAN DEFAULT 0"),
        ("verified_on", "DATE"),
        ("fetched_on", "DATE"),
        ("days_remaining", "INTEGER"),
        ("level_required", "VARCHAR(64) DEFAULT ''"),
    ]
    for col_name, col_def in schema_additions:
        if col_name not in columns:
            try:
                cur.execute(f"ALTER TABLE opportunities ADD COLUMN {col_name} {col_def}")
            except Exception:
                pass
    con.commit()

    cur.execute("SELECT COUNT(*) FROM opportunities")
    report["initial_total"] = cur.fetchone()[0]

    # 3. Purge roundup/listicle entries (Task 3) and unanchored partner programmes (Task 5)
    from todaytech.core.partner_programs import validate_partner_program

    cur.execute(
        "SELECT id, title, organizer, source_url, application_url, external_id, level_required FROM opportunities"
    )
    all_rows = cur.fetchall()
    purge_ids = []
    for r in all_rows:
        is_roundup, reason = is_roundup_or_directory(r[1], r[2], r[3], r[4])
        if is_roundup:
            purge_ids.append(r[0])
            continue
        level_req = r[6] if len(r) > 6 and r[6] else ""
        is_partner_valid, reason = validate_partner_program(
            r[1], r[2], r[3], r[4], level_required=level_req
        )
        if not is_partner_valid:
            purge_ids.append(r[0])

    if purge_ids:
        report["roundups_removed"] = len(purge_ids)
        for i in range(0, len(purge_ids), 200):
            chunk = purge_ids[i : i + 200]
            placeholders = ",".join(str(cid) for cid in chunk)
            cur.execute(f"DELETE FROM opportunities WHERE id IN ({placeholders})")
        con.commit()

    # 4. Canonicalize URLs and populate dedupe_key for all rows (Task 4)
    cur.execute("DROP INDEX IF EXISTS uq_opportunities_dedupe_key")
    con.commit()
    cur.execute(
        "SELECT id, title, organizer, cycle_year, source_url, application_url FROM opportunities"
    )
    remaining_rows = cur.fetchall()
    for r in remaining_rows:
        row_id, title, org, cy, src_url, app_url = r
        clean_src = canonicalize_url(src_url)
        clean_app = canonicalize_url(app_url)
        dkey = compute_dedupe_key(title, org, cy, clean_src, clean_app)
        cur.execute(
            "UPDATE opportunities SET dedupe_key = ?, source_url = ?, application_url = ? WHERE id = ?",
            (dkey, clean_src, clean_app, row_id),
        )
    con.commit()

    # 5. Identify and merge duplicates (Task 4)
    cur.execute(
        "SELECT dedupe_key, COUNT(*) as cnt FROM opportunities WHERE dedupe_key != '' GROUP BY dedupe_key HAVING cnt > 1"
    )
    duplicate_keys = [r[0] for r in cur.fetchall()]

    for dk in duplicate_keys:
        cur.execute(
            """SELECT id, external_id, title, organizer, date_status, deadline,
                      source_url, verified_on, fit_score, reward, location,
                      application_url, estimated_window, cycle_year
               FROM opportunities WHERE dedupe_key = ?""",
            (dk,),
        )
        group_rows = cur.fetchall()
        if len(group_rows) <= 1:
            continue

        report["duplicate_groups_merged"] += 1

        # Rank each candidate in the duplicate group
        candidates = []
        for r in group_rows:
            mock = MockRow(
                row_id=r[0],
                ext_id=r[1],
                title=r[2],
                org=r[3],
                date_status=r[4],
                deadline=r[5],
                source_url=r[6],
                verified_on=r[7],
                fit_score=r[8],
            )
            candidates.append((opportunity_rank(mock), r))

        candidates.sort(key=lambda x: x[0], reverse=True)
        winner = candidates[0][1]
        losers = [c[1] for c in candidates[1:]]

        # Merge non-empty fields from losers into winner
        winner_id = winner[0]
        winner_ext_id = winner[1]
        winner_reward = winner[9]
        winner_loc = winner[10]
        winner_app_url = winner[11]
        winner_src_url = winner[6]
        winner_est_window = winner[12]

        updates: dict[str, str] = {}
        if not winner_reward:
            for loser in losers:
                if loser[9]:
                    updates["reward"] = loser[9]
                    break
        if not winner_loc:
            for loser in losers:
                if loser[10]:
                    updates["location"] = loser[10]
                    break
        if not winner_app_url:
            for loser in losers:
                if loser[11]:
                    updates["application_url"] = loser[11]
                    break
        # Check official URL precedence: never let non-official replace official!
        winner_src = updates.get("source_url", winner_src_url)
        winner_org = winner[3]
        winner_app = updates.get("application_url", winner_app_url)

        if not is_official_domain(winner_src, winner_org):
            found_official = False
            for loser in losers:
                if is_official_domain(loser[6], loser[3]):
                    updates["source_url"] = loser[6]
                    winner_src = loser[6]
                    found_official = True
                    break
            if not found_official:
                if is_official_domain(winner_app, winner_org):
                    updates["source_url"] = winner_app
                    winner_src = winner_app
                    found_official = True
                else:
                    for loser in losers:
                        if is_official_domain(loser[11], loser[3]):
                            updates["source_url"] = loser[11]
                            winner_src = loser[11]
                            found_official = True
                            break
            if not found_official:
                # No merged record has an official URL -> drop the item entirely!
                all_ids = [winner_id] + [loser[0] for loser in losers]
                placeholders = ",".join(str(cid) for cid in all_ids)
                cur.execute(f"DELETE FROM opportunities WHERE id IN ({placeholders})")
                report["dropped_no_official_url"] = report.get("dropped_no_official_url", 0) + len(
                    all_ids
                )
                continue

        if not winner_est_window:
            for loser in losers:
                if loser[12]:
                    updates["estimated_window"] = loser[12]
                    break

        if updates:
            set_str = ", ".join(f"{k} = ?" for k in updates.keys())
            vals = list(updates.values()) + [winner_id]
            cur.execute(f"UPDATE opportunities SET {set_str} WHERE id = ?", vals)

        # Relink any user_state from loser external_ids to winner external_id
        for loser in losers:
            loser_ext_id = loser[1]
            cur.execute(
                "UPDATE user_state SET opportunity_id = ? WHERE opportunity_id = ?",
                (winner_ext_id, loser_ext_id),
            )

        # Delete loser opportunities
        loser_ids = [loser[0] for loser in losers]
        report["duplicates_removed"] += len(loser_ids)
        placeholders = ",".join(str(lid) for lid in loser_ids)
        cur.execute(f"DELETE FROM opportunities WHERE id IN ({placeholders})")

        report["merged_details"].append(
            {
                "key": dk,
                "winner_title": winner[2],
                "winner_org": winner[3],
                "winner_status": winner[4],
                "winner_url": updates.get("source_url", winner[6]),
                "losers_removed": [
                    {"id": loser[0], "title": loser[2], "url": loser[6], "status": loser[4]}
                    for loser in losers
                ],
            }
        )

    con.commit()

    # 6. Purge non-official sources where no official source or apply URL exists (Task 1 & Task 3)
    cur.execute("SELECT id, title, organizer, source_url, application_url FROM opportunities")
    non_official_ids = []
    for r in cur.fetchall():
        row_id, title, org, src, app = r
        clean_src = canonicalize_url(src)
        clean_app = canonicalize_url(app)
        if not is_official_domain(clean_src, org):
            if is_official_domain(clean_app, org):
                cur.execute(
                    "UPDATE opportunities SET source_url = ? WHERE id = ?",
                    (clean_app, row_id),
                )
            else:
                non_official_ids.append(row_id)

    if non_official_ids:
        report["non_official_purged"] = len(non_official_ids)
        for i in range(0, len(non_official_ids), 200):
            chunk = non_official_ids[i : i + 200]
            placeholders = ",".join(str(cid) for cid in chunk)
            cur.execute(f"DELETE FROM opportunities WHERE id IN ({placeholders})")
        con.commit()

    # 7. Apply unique index
    cur.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_opportunities_dedupe_key ON opportunities(dedupe_key)"
    )
    con.commit()

    cur.execute("SELECT COUNT(*) FROM opportunities")
    report["final_total"] = cur.fetchone()[0]

    con.close()
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Merge duplicate opportunities and remove roundups in TodayTech SQLite DB."
    )
    parser.add_argument(
        "--db-path",
        type=str,
        default="",
        help="Path to todaytech.db SQLite file (defaults to settings database_url).",
    )
    args = parser.parse_args()

    db_path = args.db_path
    if not db_path:
        settings = get_settings()
        clean = settings.database_url.split(":///")[-1]
        db_path = clean or "data/todaytech.db"

    target = Path(db_path)
    if not target.exists():
        print(f"Error: Database file not found at {target.resolve()}")
        sys.exit(1)

    print(f"Starting deduplication and roundup cleanup on: {target.resolve()}")
    report = run_merge_duplicates(str(target))

    print("\n" + "=" * 60)
    print(" TODAYTECH DEDUPLICATION & ROUNDUP PURGE REPORT")
    print("=" * 60)
    print(f"Database:                  {report['db_path']}")
    print(f"Initial Opportunities:     {report['initial_total']}")
    print(f"Roundups/Listicles Purged: {report['roundups_removed']}")
    print(f"Non-Official URLs Purged:  {report.get('non_official_purged', 0)}")
    print(f"Duplicate Groups Merged:   {report['duplicate_groups_merged']}")
    print(f"Duplicate Rows Removed:    {report['duplicates_removed']}")
    print(f"Dropped (No Official URL): {report.get('dropped_no_official_url', 0)}")
    print(f"Final Opportunities Left:  {report['final_total']}")
    print("=" * 60)

    if report["merged_details"]:
        print("\nMerged Opportunities Sample (up to 10):")
        for i, item in enumerate(report["merged_details"][:10], start=1):
            print(f"\n[{i}] Key: {item['key']}")
            print(
                f"    WINNER: '{item['winner_title']}' ({item['winner_org']}) [{item['winner_status']}]"
            )
            print(f"    URL:    {item['winner_url']}")
            for loser in item["losers_removed"]:
                print(
                    f"    MERGED: [id={loser['id']}] '{loser['title']}' [{loser['status']}] -> {loser['url']}"
                )


if __name__ == "__main__":
    main()
