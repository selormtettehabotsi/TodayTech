"""APScheduler jobs — daily digest and deadline reminders.

The daily digest runs at the user's configured time and timezone.
Deadline reminders fire at 7, 3, and 1 day(s) before each deadline.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from datetime import time as dt_time
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import structlog
from sqlalchemy.ext.asyncio import AsyncSession
from telegram.ext import Application

from todaytech.core.config import Settings
from todaytech.core.models import UserProfile

log = structlog.get_logger()


async def reverify_opportunity(
    opp: Any,
    today: date,
    client: httpx.AsyncClient | None = None,
) -> tuple[bool, str]:
    """Re-verify a single opportunity against its official page.

    Returns (downgraded: bool, status_message: str).
    """
    if not opp.source_url:
        opp.date_status = "unknown"
        opp.conflict = True
        opp.date_evidence = f"Re-verification on {today}: Missing source_url."
        opp.verified_on = today
        opp.deadline = None
        opp.days_remaining = None
        return True, "downgraded_no_source_url"

    should_close_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=10, follow_redirects=True)
        should_close_client = True

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        resp = await client.get(opp.source_url, headers=headers)
        if resp.status_code in (404, 410):
            opp.date_status = "unknown"
            opp.conflict = True
            opp.date_evidence = (
                f"Re-verification on {today}: Official page returned HTTP {resp.status_code}."
            )
            opp.verified_on = today
            opp.deadline = None
            opp.days_remaining = None
            return True, f"downgraded_http_{resp.status_code}"

        if resp.status_code >= 400:
            return False, f"skipped_http_{resp.status_code}"

        text = resp.text.lower()
        # Check closed keywords
        closed_signals = [
            "applications are now closed",
            "applications closed",
            "submissions are now closed",
            "submission has closed",
            "registration is closed",
            "this program has concluded",
            "applications have ended",
        ]
        if any(sig in text for sig in closed_signals):
            opp.date_status = "unknown"
            opp.conflict = True
            opp.date_evidence = (
                f"Re-verification on {today}: Official page stated applications closed."
            )
            opp.verified_on = today
            opp.deadline = None
            opp.days_remaining = None
            return True, "downgraded_closed"

        # If it was confirmed with a deadline, verify deadline is still present on page
        if opp.date_status == "confirmed" and opp.deadline:
            deadline_str = opp.deadline.strftime("%Y-%m-%d")
            month_name = opp.deadline.strftime("%B").lower()
            day_str = str(opp.deadline.day)

            date_found = deadline_str in text or (
                month_name in text and (day_str in text or f"{day_str}," in text)
            )

            # Check if page prominently features a past closed cycle instead
            past_closed_indicators = [
                "applications for 2024",
                "applications for 2025",
                "2024 cohort has ended",
                "2025 cohort has ended",
            ]
            has_past_closed = any(p in text for p in past_closed_indicators)

            if not date_found or has_past_closed:
                opp.date_status = "estimated"
                opp.conflict = True
                opp.date_evidence = f"Re-verification on {today}: Official page no longer confirms deadline {opp.deadline}."
                opp.verified_on = today
                opp.deadline = None
                opp.days_remaining = None
                return True, "downgraded_deadline_missing"
            else:
                opp.conflict = False
                opp.verified_on = today
                opp.days_remaining = (opp.deadline - today).days
                return False, "confirmed_valid"

        opp.verified_on = today
        return False, "unchanged"

    except Exception as exc:
        log.warning("reverify.item_failed", title=opp.title, error=str(exc))
        return False, f"error_{exc}"
    finally:
        if should_close_client:
            await client.aclose()


async def reverify_opportunities(
    session: AsyncSession,
    today: date,
    force_all: bool = False,
) -> dict[str, int]:
    """Re-verify items closing within 14 days daily, and all other items weekly."""
    from sqlalchemy import and_, or_, select

    from todaytech.core.database import OpportunityRow

    if force_all:
        stmt = select(OpportunityRow).where(OpportunityRow.is_scam.is_(False))
    else:
        cutoff_7d = today - timedelta(days=7)
        stmt = (
            select(OpportunityRow)
            .where(OpportunityRow.is_scam.is_(False))
            .where(
                or_(
                    and_(
                        OpportunityRow.date_status == "confirmed",
                        OpportunityRow.deadline.is_not(None),
                        OpportunityRow.deadline <= today + timedelta(days=14),
                        or_(
                            OpportunityRow.verified_on.is_(None), OpportunityRow.verified_on < today
                        ),
                    ),
                    or_(
                        OpportunityRow.verified_on.is_(None),
                        OpportunityRow.verified_on <= cutoff_7d,
                    ),
                )
            )
        )

    result = await session.execute(stmt)
    opps = list(result.scalars().all())

    stats = {"checked": len(opps), "downgraded": 0, "confirmed": 0}
    async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
        for opp in opps:
            downgraded, _ = await reverify_opportunity(opp, today, client)
            if downgraded:
                stats["downgraded"] += 1
            elif opp.date_status == "confirmed":
                stats["confirmed"] += 1

    await session.commit()
    log.info("reverification.completed", **stats)
    return stats


def setup_scheduler(
    app: Application,
    settings: Settings,
    profile: UserProfile,
) -> None:
    """Register scheduled jobs on the bot's job queue."""
    job_queue = app.job_queue
    if job_queue is None:
        log.warning("scheduler.no_job_queue")
        return

    # Parse digest time
    try:
        hour, minute = map(int, profile.digest_time.split(":"))
        tz = ZoneInfo(profile.timezone)
    except Exception as exc:
        log.warning("scheduler.invalid_time", error=str(exc))
        hour, minute = 8, 0
        tz = ZoneInfo("UTC")

    # Daily digest
    job_queue.run_daily(
        _daily_digest_job,
        time=dt_time(hour=hour, minute=minute, tzinfo=tz),
        data={"settings": settings},
        name="daily_digest",
    )
    log.info("scheduler.daily_digest_set", time=f"{hour:02d}:{minute:02d}", timezone=str(tz))

    # Deadline reminder check every 6 hours
    job_queue.run_repeating(
        _deadline_reminder_job,
        interval=timedelta(hours=6),
        first=timedelta(minutes=5),
        data={"settings": settings},
        name="deadline_reminders",
    )
    log.info("scheduler.deadline_reminders_set")

    # Re-verification job every 24 hours (closing within 14d daily, others weekly)
    job_queue.run_repeating(
        _reverification_job,
        interval=timedelta(hours=24),
        first=timedelta(minutes=10),
        data={"settings": settings},
        name="opportunity_reverification",
    )
    log.info("scheduler.reverification_set")

    # IP Watcher job: checks public IP and alerts owner on change
    job_queue.run_repeating(
        _ip_watcher_job,
        interval=timedelta(minutes=30),
        first=timedelta(seconds=30),
        data={"settings": settings},
        name="ip_watcher",
    )
    log.info("scheduler.ip_watcher_set")


async def _reverification_job(context: Any) -> None:
    """Job: periodic opportunity re-verification."""
    try:
        from todaytech.core.database import get_session

        today = datetime.now(timezone.utc).date()
        async with get_session() as session:
            await reverify_opportunities(session, today)
    except Exception as exc:
        log.error("scheduler.reverification_failed", error=str(exc))


async def _daily_digest_job(context: Any) -> None:
    """Job: run scan and send daily digest."""
    settings: Settings = context.job.data["settings"]

    try:
        from todaytech.core.database import get_opportunities, get_session
        from todaytech.core.digest import format_digest
        from todaytech.core.pipeline import run_scan

        result = await run_scan()

        async with get_session() as session:
            opportunities = await get_opportunities(session, limit=50)

        digest = format_digest(
            opportunities,
            unhealthy_sources=result.get("unhealthy_sources", []),
        )

        # Send to user (with plain-text fallback on markdown error)
        try:
            await context.bot.send_message(
                chat_id=settings.telegram_user_id,
                text=digest,
                parse_mode="Markdown",
                disable_web_page_preview=True,
            )
        except Exception:
            await context.bot.send_message(
                chat_id=settings.telegram_user_id,
                text=digest,
                disable_web_page_preview=True,
            )
        log.info("scheduler.digest_sent")

    except Exception as exc:
        log.error("scheduler.digest_failed", error=str(exc))


async def _deadline_reminder_job(context: Any) -> None:
    """Job: send deadline reminders at 7, 3, and 1 day(s) before."""
    settings: Settings = context.job.data["settings"]

    try:
        from sqlalchemy import select

        from todaytech.core.database import OpportunityRow, get_session

        today = date.today()
        reminder_days = [7, 3, 1]

        async with get_session() as session:
            # Get saved opportunities with upcoming deadlines
            stmt = (
                select(OpportunityRow)
                .where(OpportunityRow.deadline >= today)
                .where(OpportunityRow.deadline <= today + timedelta(days=8))
                .order_by(OpportunityRow.deadline.asc())
            )
            result = await session.execute(stmt)
            opps = list(result.scalars().all())

        reminders: list[str] = []
        for opp in opps:
            if opp.deadline:
                days_left = (opp.deadline - today).days
                if days_left in reminder_days:
                    emoji = "🔴" if days_left <= 1 else "🟡" if days_left <= 3 else "🟢"
                    reminders.append(
                        f"{emoji} **{opp.title}** — {days_left} day(s) left! "
                        f"(Deadline: {opp.deadline})\n"
                        f"   🆔 `{opp.external_id[:8]}`"
                    )

        if reminders:
            text = "⏰ **Deadline Reminders**\n\n" + "\n\n".join(reminders)
            try:
                await context.bot.send_message(
                    chat_id=settings.telegram_user_id,
                    text=text,
                    parse_mode="Markdown",
                    disable_web_page_preview=True,
                )
            except Exception:
                await context.bot.send_message(
                    chat_id=settings.telegram_user_id,
                    text=text,
                    disable_web_page_preview=True,
                )
            log.info("scheduler.reminders_sent", count=len(reminders))

    except Exception as exc:
        log.error("scheduler.reminders_failed", error=str(exc))


_last_known_ip: str | None = None


async def _ip_watcher_job(context: Any) -> None:
    """Job: check server's public IP address and notify owner if it changes."""
    global _last_known_ip
    settings: Settings = context.job.data["settings"]

    try:
        current_ip = None
        for service_url in (
            "https://api.ipify.org",
            "https://icanhazip.com",
            "https://ifconfig.me/ip",
        ):
            try:
                async with httpx.AsyncClient(timeout=5) as client:
                    resp = await client.get(service_url)
                    if resp.status_code == 200 and resp.text.strip():
                        current_ip = resp.text.strip()
                        break
            except Exception:
                continue

        if not current_ip:
            return

        if _last_known_ip is None:
            _last_known_ip = current_ip
            log.info("scheduler.ip_watcher_initialized", ip=current_ip)
            return

        if current_ip != _last_known_ip:
            old_ip = _last_known_ip
            _last_known_ip = current_ip
            alert_text = (
                f"🌐 *TodayTech IP Update Alert*\n\n"
                f"The server's public IP address has changed:\n"
                f"• Previous: `{old_ip}`\n"
                f"• New IP: `{current_ip}`\n\n"
                f"🔗 **New Dashboard URL:** [http://{current_ip}/](http://{current_ip}/)"
            )
            try:
                await context.bot.send_message(
                    chat_id=settings.telegram_user_id,
                    text=alert_text,
                    parse_mode="Markdown",
                    disable_web_page_preview=True,
                )
            except Exception:
                await context.bot.send_message(
                    chat_id=settings.telegram_user_id,
                    text=alert_text,
                    disable_web_page_preview=True,
                )
            log.info("scheduler.ip_changed", old_ip=old_ip, new_ip=current_ip)
    except Exception as exc:
        log.warning("scheduler.ip_watcher_failed", error=str(exc))
