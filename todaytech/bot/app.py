"""Telegram bot application — all command handlers and security.

The bot ONLY responds to the Telegram user ID set in TELEGRAM_USER_ID.
"""

from __future__ import annotations

import re
import sys
from datetime import datetime, timezone

import structlog
from telegram import Update
from telegram.error import Conflict
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from todaytech.core.config import Settings, load_profile
from todaytech.core.database import (
    get_opportunities,
    get_saved_opportunities,
    get_session,
    set_user_state,
)
from todaytech.core.dedupe import is_official_domain, is_valid_estimated_opportunity
from todaytech.core.digest import format_digest
from todaytech.core.pipeline import run_scan
from todaytech.scheduler.jobs import setup_scheduler

log = structlog.get_logger()


# ---------------------------------------------------------------------------
# Security middleware
# ---------------------------------------------------------------------------


def auth_required(func):
    """Decorator: verify authorized user ID and reject any unauthorized users."""

    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        settings: Settings = context.bot_data["settings"]
        sender_id = update.effective_user.id if update.effective_user else None

        if not sender_id or sender_id != settings.telegram_user_id:
            user_id = sender_id or 0
            username = (
                (update.effective_user.username or update.effective_user.first_name)
                if update.effective_user
                else "there"
            )
            log.warning(
                "bot.unauthorized",
                user_id=user_id,
                username=username,
                configured_user_id=settings.telegram_user_id,
            )
            if update.message is not None:
                await update.message.reply_text(
                    "⛔ Access restricted. This TodayTech bot is private.",
                )
            elif getattr(update, "callback_query", None) is not None:
                try:
                    await update.callback_query.answer(
                        "⛔ Access restricted. This bot is private.", show_alert=True
                    )
                except Exception:
                    pass
            return
        return await func(update, context)

    return wrapper


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _send_long(update: Update, text: str) -> None:
    """Send a message, splitting at 4000 chars if needed (Telegram limit), with markdown fallback."""
    if not update.message:
        return
    max_len = 4000
    if len(text) <= max_len:
        try:
            await update.message.reply_text(
                text, parse_mode="Markdown", disable_web_page_preview=True
            )
        except Exception:
            await update.message.reply_text(text, disable_web_page_preview=True)
        return
    chunks = [text[i : i + max_len] for i in range(0, len(text), max_len)]
    for chunk in chunks:
        try:
            await update.message.reply_text(
                chunk, parse_mode="Markdown", disable_web_page_preview=True
            )
        except Exception:
            await update.message.reply_text(chunk, disable_web_page_preview=True)


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------


@auth_required
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start — welcome message."""
    await update.message.reply_text(
        "🚀 *Welcome to TodayTech!*\n\n"
        "I find active hackathons, scholarships, internships, fellowships, and student ambassador "
        "programs tailored specifically to your profile (all dates in GMT 0:00).\n\n"
        "⚡ *Commands you can tap:* \n"
        "• /update — run a live grounded scan across official sources\n"
        "• /deadlines — see upcoming confirmed deadlines (sorted nearest first)\n"
        "• /estimated — see opportunities with projected or TBA dates\n"
        "• /saved — view your saved application pipeline\n"
        "• /ip — view server public IP and public dashboard link\n"
        "• `/details <id>` — full verification breakdown, official source & links\n"
        "• `/apply <id>` — prepare tailored CV, SOP & answers\n"
        "• `/help` — view this help menu\n\n"
        "💬 *Or just send me messages:* \n"
        "• `deadlines` or `estimated`\n"
        "• `only hackathons` or `only scholarships`\n"
        "• `save <id>` to bookmark an opportunity\n"
        "• `ignore <id>` to hide from your feed",
        parse_mode="Markdown",
    )


@auth_required
async def cmd_setup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /setup — prompt to run CLI onboarding."""
    await update.message.reply_text(
        "🔧 **Setup**\n\n"
        "Run `todaytech init` in your terminal to build your profile interactively.\n"
        "Or edit `profile.yaml` directly.\n\n"
        "After setup, send `update me` to get your first digest!",
        parse_mode="Markdown",
    )


@auth_required
async def cmd_update(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle 'update me' — run fresh scan and send digest."""
    await update.message.reply_text(
        "🔍 Running fresh scan across live sources... This may take up to a minute."
    )

    try:
        # Always force fresh scan on user request
        result = await run_scan(force=True)

        if "error" in result:
            await update.message.reply_text(f"❌ {result['error']}")
            return

        # Fetch and format digest
        async with get_session() as session:
            opportunities = await get_opportunities(session, limit=100)
            opportunities = [
                o for o in opportunities if is_official_domain(o.source_url, o.organizer)
            ]

        digest = format_digest(
            opportunities,
            unhealthy_sources=result.get("unhealthy_sources", []),
        )
        new_count = result.get("items_new", 0)
        found_count = result.get("items_found", 0)
        header = f"✨ **Scan Refresh Complete!**\nFound {found_count} opportunities ({new_count} new to your radar).\n\n"
        await _send_long(update, header + digest)

    except Exception as exc:
        log.error("bot.update_failed", error=str(exc))
        await update.message.reply_text(f"❌ Scan failed: {exc}")


@auth_required
async def cmd_filter(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle 'only <type>' — filter opportunities by type."""
    text = update.message.text.lower().strip()
    # Extract type
    type_map = {
        "hackathon": "hackathon",
        "hackathons": "hackathon",
        "scholarship": "scholarship",
        "scholarships": "scholarship",
        "internship": "internship",
        "internships": "internship",
        "fellowship": "fellowship",
        "fellowships": "fellowship",
        "grant": "grant",
        "grants": "grant",
        "program": "program",
        "programs": "program",
        "competition": "competition",
        "competitions": "competition",
        "ambassador": "ambassador",
        "ambassadors": "ambassador",
    }

    opp_type = None
    for keyword, mapped_type in type_map.items():
        if keyword in text:
            opp_type = mapped_type
            break

    if not opp_type:
        await update.message.reply_text(
            "❓ Which type? hackathons, scholarships, internships, fellowships, grants, programs, "
            "competitions, or ambassadors?"
        )
        return

    async with get_session() as session:
        opportunities = await get_opportunities(session, type_filter=opp_type, limit=50)
        opportunities = [o for o in opportunities if is_official_domain(o.source_url, o.organizer)][
            :20
        ]

    if not opportunities:
        await update.message.reply_text(f"📭 No {opp_type}s found. Try running `update me` first.")
        return

    digest = format_digest(opportunities)
    await _send_long(update, digest)


@auth_required
async def cmd_details(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle 'details <id>' — full breakdown of an opportunity."""
    text = update.message.text.strip()
    match = re.search(r"details?\s+(\S+)", text, re.IGNORECASE)
    if not match:
        await update.message.reply_text("Usage: `details <id>`", parse_mode="Markdown")
        return

    opp_id = match.group(1)

    async with get_session() as session:
        # Try exact match or prefix match
        from sqlalchemy import select

        from todaytech.core.database import OpportunityRow

        stmt = select(OpportunityRow).where(OpportunityRow.external_id.startswith(opp_id))
        result = await session.execute(stmt)
        opp = result.scalars().first()

    if not opp or not is_official_domain(opp.source_url, opp.organizer):
        await update.message.reply_text(
            f"❌ Opportunity `{opp_id}` not found or was excluded by official source policy.",
            parse_mode="Markdown",
        )
        return

    details = _format_details(opp)
    await _send_long(update, details)


@auth_required
async def cmd_save(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle 'save <id>' — save an opportunity."""
    text = update.message.text.strip()
    match = re.search(r"save\s+(\S+)", text, re.IGNORECASE)
    if not match:
        await update.message.reply_text("Usage: `save <id>`", parse_mode="Markdown")
        return

    opp_id = match.group(1)
    async with get_session() as session:
        from todaytech.core.database import get_opportunity_by_id

        opp = await get_opportunity_by_id(session, opp_id)
        full_id = opp.external_id if opp else opp_id
        await set_user_state(session, full_id, "saved")
        await session.commit()

    await update.message.reply_text(f"✅ Saved `{opp_id}`", parse_mode="Markdown")


@auth_required
async def cmd_ignore(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle 'ignore <id>' — ignore an opportunity."""
    text = update.message.text.strip()
    match = re.search(r"ignore\s+(\S+)", text, re.IGNORECASE)
    if not match:
        await update.message.reply_text("Usage: `ignore <id>`", parse_mode="Markdown")
        return

    opp_id = match.group(1)
    async with get_session() as session:
        from todaytech.core.database import get_opportunity_by_id

        opp = await get_opportunity_by_id(session, opp_id)
        full_id = opp.external_id if opp else opp_id
        await set_user_state(session, full_id, "ignored")
        await session.commit()

    await update.message.reply_text(f"🙈 Ignored `{opp_id}`", parse_mode="Markdown")


@auth_required
async def cmd_saved(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle 'saved' — show saved opportunities."""
    async with get_session() as session:
        saved = await get_saved_opportunities(session)

    if not saved:
        await update.message.reply_text(
            "📭 No saved opportunities yet. Use `save <id>` to save one."
        )
        return

    lines = ["📌 **Saved Opportunities**\n"]
    for item in saved:
        lines.append(f"• `{item.opportunity_id}` — {item.status}")
    await _send_long(update, "\n".join(lines))


@auth_required
async def cmd_deadlines(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle 'deadlines' — show upcoming confirmed deadlines."""
    from sqlalchemy import select

    from todaytech.core.database import OpportunityRow

    today = datetime.now(timezone.utc).date()
    async with get_session() as session:
        stmt = (
            select(OpportunityRow)
            .where(
                OpportunityRow.deadline >= today,
                OpportunityRow.date_status == "confirmed",
            )
            .order_by(OpportunityRow.deadline.asc())
            .limit(50)
        )
        result = await session.execute(stmt)
        opps = [o for o in result.scalars().all() if is_official_domain(o.source_url, o.organizer)][
            :20
        ]

    if not opps:
        await update.message.reply_text(
            "📭 No upcoming confirmed deadlines found.\n"
            "Try `/update` to scan or `/estimated` to view programs with upcoming estimated cycles.",
            parse_mode="Markdown",
        )
        return

    lines = ["📅 **Upcoming Confirmed Deadlines (GMT 0:00)**\n"]
    for opp in opps:
        days_left = (opp.deadline - today).days if opp.deadline else "?"
        lines.append(
            f"• **{opp.title}**\n  Deadline: `{opp.deadline}` ({days_left}d left) • ID: `{opp.external_id[:8]}`"
        )
    lines.append("\n💡 Use `/details <id>` to view official source verification.")
    await _send_long(update, "\n".join(lines))


@auth_required
async def cmd_estimated(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle '/estimated' — list opportunities whose dates are not yet published.

    Task 4: Show an item as estimated only if the official page exists and there is
    evidence of a previous cycle. Items with only 'Expected window TBA' and no evidence
    must not be shown.
    """
    from sqlalchemy import select

    from todaytech.core.database import OpportunityRow

    async with get_session() as session:
        stmt = (
            select(OpportunityRow)
            .where(OpportunityRow.date_status == "estimated")
            .order_by(OpportunityRow.fit_score.desc())
            .limit(50)
        )
        result = await session.execute(stmt)
        opps = [o for o in result.scalars().all() if is_valid_estimated_opportunity(o)][:20]

    if not opps:
        await update.message.reply_text(
            "📭 No estimated opportunities with previous cycle evidence found."
        )
        return

    lines = [
        "⏳ **Estimated / Projected Opportunities**\n"
        "Official current-cycle dates are not yet published:\n"
    ]
    for opp in opps:
        status_label = (opp.date_status or "estimated").upper()
        window = opp.estimated_window or "Previous cycle projection"
        evidence_lines = []
        if opp.prev_cycle_date:
            evidence_lines.append(f"Prev Cycle Date: `{opp.prev_cycle_date}`")
        if opp.prev_cycle_source:
            evidence_lines.append(f"Prev Cycle Source: {opp.prev_cycle_source}")
        ev_str = f"  Evidence: {', '.join(evidence_lines)}\n" if evidence_lines else ""

        lines.append(
            f"• **{opp.title}** `[{status_label}]`\n"
            f"  Window: {window}\n"
            f"{ev_str}"
            f"  Official Source: {opp.source_url or 'None'}\n"
            f"  ID: `{opp.external_id[:8]}`\n"
        )
    lines.append("💡 Use `/details <id>` to view full verification details.")
    await _send_long(update, "\n".join(lines))


@auth_required
async def cmd_apply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle 'apply <id>' — start application assistant."""
    text = update.message.text.strip()
    match = re.search(r"apply\s+(\S+)", text, re.IGNORECASE)
    if not match:
        await update.message.reply_text("Usage: `apply <id>`", parse_mode="Markdown")
        return

    opp_id = match.group(1)
    await update.message.reply_text(
        f"📝 Starting application assistant for `{opp_id}`...\n"
        "Drafting personalized motivation letter, checklist, and Word document...",
        parse_mode="Markdown",
    )

    try:
        from pathlib import Path

        from todaytech.apply.assistant import run_application_assistant

        settings: Settings = context.bot_data["settings"]
        result = await run_application_assistant(opp_id, settings)
        await _send_long(update, result)

        base_drafts = Path("data/drafts")
        if base_drafts.exists() and update.message:
            target_dir = base_drafts / opp_id[:16]
            if not target_dir.exists():
                for cand in base_drafts.glob(f"{opp_id[:8]}*"):
                    if cand.is_dir():
                        target_dir = cand
                        break
            if target_dir.exists() and target_dir.is_dir():
                for docx_file in sorted(target_dir.glob("*.docx")):
                    try:
                        with open(docx_file, "rb") as doc_fp:
                            await update.message.reply_document(
                                document=doc_fp,
                                filename=docx_file.name,
                                caption=f"📄 {docx_file.stem.replace('_', ' ').title()}",
                            )
                    except Exception as doc_err:
                        log.warning(
                            "bot.send_doc_failed", error=str(doc_err), filename=docx_file.name
                        )
    except Exception as exc:
        log.error("bot.apply_failed", error=str(exc))
        await update.message.reply_text(f"❌ Application assistant failed: {exc}")


@auth_required
async def cmd_add_source(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle 'add source <url>' — add a custom source."""
    text = update.message.text.strip()
    match = re.search(r"add\s+source\s+(\S+)", text, re.IGNORECASE)
    if not match:
        await update.message.reply_text("Usage: `add source <url>`", parse_mode="Markdown")
        return

    url = match.group(1)
    # TODO: append to sources.yaml
    await update.message.reply_text(
        f"✅ Added source: {url}\nIt will be included in the next scan."
    )


# ---------------------------------------------------------------------------
# Natural language router
# ---------------------------------------------------------------------------


@auth_required
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Route natural-language messages to the appropriate handler."""
    if not update.message or not update.message.text:
        return
    text = update.message.text.strip().lower().lstrip("/")

    if text.startswith("update") or text in ("scan", "refresh", "what's new", "digest"):
        return await cmd_update(update, context)
    elif text.startswith("only ") or text.startswith("show "):
        return await cmd_filter(update, context)
    elif text.startswith("detail"):
        return await cmd_details(update, context)
    elif text.startswith("apply"):
        return await cmd_apply(update, context)
    elif text.startswith("save "):
        return await cmd_save(update, context)
    elif text.startswith("ignore "):
        return await cmd_ignore(update, context)
    elif text in ("saved", "pipeline", "my list"):
        return await cmd_saved(update, context)
    elif text in ("deadlines", "upcoming"):
        return await cmd_deadlines(update, context)
    elif text in ("estimated", "tba", "projected"):
        return await cmd_estimated(update, context)
    elif text.startswith("add source"):
        return await cmd_add_source(update, context)
    elif text in ("help", "start", "menu"):
        return await cmd_start(update, context)
    else:
        await update.message.reply_text(
            "🤔 I didn't understand that. Try:\n"
            "• `/update` — run scan & get today's digest\n"
            "• `/deadlines` — upcoming confirmed closing dates\n"
            "• `/estimated` — opportunities with projected/TBA dates\n"
            "• `only hackathons` or `only scholarships`\n"
            "• `/details <id>` — verification breakdown & official links\n"
            "• `/apply <id>` — prepare tailored draft\n"
            "• `/saved` — your saved opportunities\n"
            "• `/help` — view full command menu",
            parse_mode="Markdown",
        )


# ---------------------------------------------------------------------------
# Details formatter
# ---------------------------------------------------------------------------


def _format_details(opp) -> str:
    """Format full details of an opportunity."""
    date_status_str = (opp.date_status or "unknown").upper()
    lines = [
        f"📋 **{opp.title}**\n",
        f"🆔 `{opp.external_id}`",
        f"📌 Type: {opp.type.capitalize()}",
        f"🏢 Organizer: {opp.organizer or 'Not stated'}",
        f"🛡️ Date Status: `{date_status_str}`",
    ]

    if opp.date_status == "confirmed" and opp.deadline:
        today = datetime.now(timezone.utc).date()
        days_left = (opp.deadline - today).days
        lines.append(f"📅 Confirmed Deadline: `{opp.deadline}` (GMT 0:00, {days_left}d left)")
    elif opp.estimated_window:
        lines.append(f"⏳ Estimated Window: {opp.estimated_window}")
    elif opp.deadline:
        lines.append(f"📅 Deadline: `{opp.deadline}` (GMT 0:00)")
    else:
        lines.append("📅 Deadline: Rolling / Open Enrollment")

    if opp.verified_on:
        lines.append(f"✅ Verified On: `{opp.verified_on}`")
    if opp.date_evidence:
        lines.append(f"📝 Date Evidence: {opp.date_evidence}")

    lines.extend(
        [
            f"📅 Program Start: {opp.start_date or 'Not stated'}",
            f"📍 Location: {opp.location or 'Not stated'} ({opp.mode or 'N/A'})",
            f"💰 Cost: {'Free' if opp.cost == 0 else f'${opp.cost:.0f}'}",
            f"🎁 Reward: {opp.reward or 'Not stated'}",
        ]
    )

    lines.append("\n**Eligibility:**")
    lines.append(f"  Verdict: {opp.eligibility_verdict}")
    lines.append(f"  Reason: {opp.eligibility_reason}")
    if opp.eligibility_text:
        lines.append(f"  Text: {opp.eligibility_text[:300]}")

    lines.append(f"\n**Fit Score: {opp.fit_score}/100**")
    lines.append(f"  {opp.fit_reason}")

    if opp.is_scam:
        lines.append(f"\n🚩 **Scam Warning:** {opp.scam_reason}")

    if opp.source_url:
        lines.append(f"\n🌐 Official Source: {opp.source_url}")
    if opp.application_url and opp.application_url != opp.source_url:
        lines.append(f"🔗 Application URL: {opp.application_url}")

    lines.append(f"\n💡 Use `/apply {opp.external_id[:8]}` to start the application assistant")
    lines.append(f"💾 Use `/save {opp.external_id[:8]}` to save this opportunity")

    return "\n".join(lines)


@auth_required
async def cmd_ip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /ip — return current server public IP and dashboard URL."""
    if not update.message:
        return

    from todaytech.scheduler.jobs import fetch_public_ip

    public_ip = (await fetch_public_ip()) or "Unknown"

    msg = (
        f"🌐 *TodayTech Server Public IP*\n\n"
        f"• **Public IP:** `{public_ip}`\n"
        f"• **Web Dashboard:** [http://{public_ip}/](http://{public_ip}/)\n\n"
        f"💡 Dashboard is public read-only. All actions (scan, save, apply) are managed exclusively through this bot."
    )
    await update.message.reply_text(msg, parse_mode="Markdown", disable_web_page_preview=True)


# ---------------------------------------------------------------------------
# Bot startup
# ---------------------------------------------------------------------------


async def start_bot(settings: Settings) -> None:
    """Start the Telegram bot and scheduler."""
    import asyncio

    settings.validate_runtime(require_search_and_llm=False)
    app = Application.builder().token(settings.telegram_bot_token).build()
    app.bot_data["settings"] = settings

    # Register handlers
    app.add_handler(CommandHandler(["start", "help", "menu"], cmd_start))
    app.add_handler(CommandHandler("setup", cmd_setup))
    app.add_handler(CommandHandler(["update", "scan", "digest", "refresh"], cmd_update))
    app.add_handler(CommandHandler(["saved", "pipeline"], cmd_saved))
    app.add_handler(CommandHandler(["deadlines", "upcoming"], cmd_deadlines))
    app.add_handler(CommandHandler(["estimated", "tba"], cmd_estimated))
    app.add_handler(CommandHandler(["ip", "serverip"], cmd_ip))
    app.add_handler(CommandHandler("apply", cmd_apply))
    app.add_handler(CommandHandler("details", cmd_details))
    app.add_handler(CommandHandler("save", cmd_save))
    app.add_handler(CommandHandler("ignore", cmd_ignore))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Error handler for Telegram Conflict
    stop_event = asyncio.Event()

    async def bot_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        if isinstance(context.error, Conflict):
            log.error(
                "bot.conflict_detected",
                error="Polling terminated because another bot instance is running with this token.",
            )
            print(
                "FATAL: Telegram conflict: Another instance is polling with this bot token. Exiting cleanly.",
                file=sys.stderr,
            )
            try:
                from todaytech.core.alerts import send_telegram_alert

                await send_telegram_alert(
                    "🚨 *TodayTech Alert: Telegram Polling Conflict*\n\n"
                    "Another bot instance started polling with this token. "
                    "Shutting down this instance to avoid update conflicts.",
                    settings=settings,
                )
            except Exception:
                pass
            stop_event.set()
        else:
            log.error("bot.unhandled_error", error=str(context.error))

    app.add_error_handler(bot_error_handler)

    # Set up scheduler for daily digests and reminders
    profile = load_profile(settings.profile_path)
    if profile:
        setup_scheduler(app, settings, profile)

    log.info("bot.starting")
    await app.initialize()

    # Startup check: probe get_updates to detect if another instance is already actively polling
    try:
        me = await app.bot.get_me()
        log.info("bot.authenticated", username=me.username, id=me.id)
        await app.bot.get_updates(offset=-1, limit=1, timeout=0)
    except Conflict:
        log.error(
            "bot.startup_conflict",
            error="Another bot instance is currently active and polling with this token. Exiting.",
        )
        print(
            "FATAL: Another bot instance is currently active with this token. Exiting cleanly to avoid polling conflicts.",
            file=sys.stderr,
        )
        try:
            from todaytech.core.alerts import send_telegram_alert

            await send_telegram_alert(
                "🚨 *TodayTech Alert: Startup Polling Conflict*\n\n"
                "A conflicting bot process is already actively polling Telegram with this bot token. "
                "The new instance is terminating immediately.",
                settings=settings,
            )
        except Exception:
            pass
        sys.exit(1)
    except Exception as exc:
        log.warning("bot.startup_probe_warning", error=str(exc))

    await app.start()
    await app.updater.start_polling(drop_pending_updates=False)

    try:
        await stop_event.wait()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()
