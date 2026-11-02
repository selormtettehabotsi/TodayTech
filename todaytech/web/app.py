"""FastAPI web dashboard application for TodayTech.

Provides REST APIs and serves the editorial web interface.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import structlog
import yaml
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import select

from todaytech.core.config import get_settings, load_profile
from todaytech.core.database import (
    ApplicationRow,
    OpportunityRow,
    UserStateRow,
    get_last_scan,
    get_opportunities,
    get_opportunity_by_id,
    get_session,
    init_db,
    set_user_state,
    upsert_opportunity,
)
from todaytech.core.pipeline import run_scan

log = structlog.get_logger()

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"


class StateUpdateRequest(BaseModel):
    status: str
    notes: str = ""


class ProfileUpdateRequest(BaseModel):
    name: str | None = None
    country: str | None = None
    level: str | None = None
    university: str | None = None
    field_of_study: str | None = None
    skills: list[str] | None = None
    interests: list[str] | None = None
    opportunity_types: list[str] | None = None


async def _seed_demo_if_empty(session) -> None:
    """Seed initial opportunities if the database is fresh so the UI looks stunning immediately."""
    # Always ensure baseline benchmark opportunities are present and updated with real GMT deadlines
    demo_items: list[dict[str, Any]] = [
        {
            "external_id": "c8f1e29a1b2c3d4e",
            "title": "Google Summer of Code 2027 Mentee Program",
            "type": "fellowship",
            "organizer": "Google Open Source",
            "deadline": date(2027, 4, 8),
            "mode": "online",
            "location": "Global / Remote",
            "cost": 0.0,
            "reward": "$3,000 - $6,000 stipend + 1-on-1 industry mentorship",
            "eligibility_text": "Open to post-secondary students and beginners to open source worldwide aged 18+.",
            "countries_allowed": '["all"]',
            "level_required": "undergraduate",
            "field_required": "Computer Science / STEM",
            "fit_score": 96,
            "fit_reason": "Direct match for Python skills and open source background; high stipend",
            "eligibility_verdict": "Eligible",
            "eligibility_reason": "Meets 18+ student requirement; remote participation fully supported",
            "application_url": "https://summerofcode.withgoogle.com",
            "source_url": "https://summerofcode.withgoogle.com",
            "source_name": "Google Open Source",
        },
        {
            "external_id": "7a49d10e9f8e7d6c",
            "title": "ETHGlobal AI & Web3 Autonomous Hackathon",
            "type": "hackathon",
            "organizer": "ETHGlobal",
            "deadline": date(2026, 11, 29),
            "mode": "online",
            "location": "Online / Virtual",
            "cost": 0.0,
            "reward": "$50,000 in bounties, grants, and pool prizes",
            "eligibility_text": "Open to developers, designers, and students worldwide. Teams of 1-5 allowed.",
            "countries_allowed": '["all"]',
            "level_required": "all",
            "field_required": "Software Engineering",
            "fit_score": 91,
            "fit_reason": "Excellent hackathon fit; high prize pool; feasible 48-hour build timeline",
            "eligibility_verdict": "Eligible",
            "eligibility_reason": "Global online participation with no prerequisite credentials",
            "application_url": "https://ethglobal.com",
            "source_url": "https://ethglobal.com",
            "source_name": "Devpost / ETHGlobal",
        },
        {
            "external_id": "3f8a02bd5e6f7a8b",
            "title": "Mozilla Technology Fund: Open Source AI Grant",
            "type": "grant",
            "organizer": "Mozilla Foundation",
            "deadline": date(2026, 12, 18),
            "mode": "online",
            "location": "Worldwide",
            "cost": 0.0,
            "reward": "$25,000 - $50,000 non-dilutive grant funding",
            "eligibility_text": "Open-source AI tools, transparent models, student researchers, and public good builders.",
            "countries_allowed": '["all"]',
            "level_required": "undergraduate",
            "field_required": "Artificial Intelligence",
            "fit_score": 86,
            "fit_reason": "High grant value aligned with open-source ethical AI interests",
            "eligibility_verdict": "Eligible",
            "eligibility_reason": "Worldwide applicants eligible with zero application fee",
            "application_url": "https://foundation.mozilla.org/awards/technology-fund/",
            "source_url": "https://foundation.mozilla.org",
            "source_name": "Mozilla Grants",
        },
        {
            "external_id": "119cb4524a3b2c1d",
            "title": "Mastercard Foundation Scholars Program",
            "type": "scholarship",
            "organizer": "Mastercard Foundation",
            "deadline": date(2027, 1, 31),
            "mode": "in-person",
            "location": "Partner Universities (Africa, UK, US, Canada)",
            "cost": 0.0,
            "reward": "100% full tuition, living stipend, travel, laptop, and leadership coaching",
            "eligibility_text": "African youth with academic talent, leadership potential, facing financial barriers.",
            "countries_allowed": '["Ghana", "Nigeria", "Kenya", "South Africa", "Uganda", "Rwanda"]',
            "level_required": "undergraduate",
            "field_required": "Any",
            "fit_score": 89,
            "fit_reason": "Fully funded comprehensive package; high benefit for early career development",
            "eligibility_verdict": "Eligible",
            "eligibility_reason": "Targeted at youth in your region with strong leadership drive",
            "application_url": "https://mastercardfdn.org/all/scholars/",
            "source_url": "https://mastercardfdn.org",
            "source_name": "Mastercard Foundation",
        },
        {
            "external_id": "55d31ca98b7c6d5e",
            "title": "Outreachy Remote Software Internship",
            "type": "internship",
            "organizer": "Outreachy / Software Freedom Conservancy",
            "deadline": date(2027, 2, 6),
            "mode": "online",
            "location": "Fully Remote",
            "cost": 0.0,
            "reward": "$7,000 USD stipend + dedicated 3-month engineering mentor",
            "eligibility_text": "Open to underrepresented groups in tech globally. Must be able to commit 30 hrs/week.",
            "countries_allowed": '["all"]',
            "level_required": "undergraduate",
            "field_required": "Software Development",
            "fit_score": 88,
            "fit_reason": "Paid remote internship with experienced open-source mentor",
            "eligibility_verdict": "Eligible",
            "eligibility_reason": "Remote eligible, students and self-taught developers encouraged",
            "application_url": "https://www.outreachy.org",
            "source_url": "https://www.outreachy.org",
            "source_name": "Outreachy",
        },
        {
            "external_id": "d41c7b2e9a0f5c63",
            "title": "GitHub Campus Experts Student Ambassador Program",
            "type": "ambassador",
            "organizer": "GitHub Education",
            "deadline": date(2027, 2, 28),
            "mode": "hybrid",
            "location": "Your campus / Global",
            "cost": 0.0,
            "reward": "Community leadership training, event support, and access to the GitHub Education network",
            "eligibility_text": "Students aged 18+ enrolled at a higher-education institution, with a verified GitHub Student Developer Pack.",
            "countries_allowed": '["all"]',
            "level_required": "undergraduate",
            "field_required": "Computer Science / STEM",
            "fit_score": 85,
            "fit_reason": "Campus tech leadership role that builds community and public speaking experience",
            "eligibility_verdict": "Likely",
            "eligibility_reason": "Open to university students worldwide; requires an active campus community plan",
            "application_url": "https://education.github.com/experts",
            "source_url": "https://education.github.com/experts",
            "source_name": "GitHub Education",
        },
    ]

    for item in demo_items:
        await upsert_opportunity(session, item)
    await session.commit()


def create_app() -> FastAPI:
    """Create and configure the FastAPI web dashboard application."""
    app = FastAPI(
        title="TodayTech Radar",
        description="Personal Opportunity Radar Web Dashboard",
        version="0.1.0",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Static assets
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        import os

        settings = get_settings()
        await init_db(settings.database_url)
        async with get_session() as session:
            await _seed_demo_if_empty(session)

        # Dual mode: launch Telegram bot daemon if credentials are present
        bot_app = None
        run_bot = os.environ.get("ENABLE_BOT_IN_WEB", "0") == "1"
        if (
            run_bot
            and settings.telegram_bot_token
            and settings.telegram_user_id
            and not str(settings.telegram_bot_token).startswith("your")
        ):
            try:
                from telegram.ext import Application, CommandHandler, MessageHandler, filters

                from todaytech.bot.app import cmd_setup, cmd_start, handle_message
                from todaytech.scheduler.jobs import setup_scheduler

                bot_app = Application.builder().token(settings.telegram_bot_token).build()
                bot_app.bot_data["settings"] = settings
                from todaytech.bot.app import (
                    cmd_apply,
                    cmd_deadlines,
                    cmd_details,
                    cmd_ignore,
                    cmd_save,
                    cmd_saved,
                    cmd_update,
                )

                bot_app.add_handler(CommandHandler(["start", "help"], cmd_start))
                bot_app.add_handler(CommandHandler("setup", cmd_setup))
                bot_app.add_handler(CommandHandler(["update", "scan", "digest"], cmd_update))
                bot_app.add_handler(CommandHandler(["saved", "pipeline"], cmd_saved))
                bot_app.add_handler(CommandHandler(["deadlines", "upcoming"], cmd_deadlines))
                bot_app.add_handler(CommandHandler("apply", cmd_apply))
                bot_app.add_handler(CommandHandler("details", cmd_details))
                bot_app.add_handler(CommandHandler("save", cmd_save))
                bot_app.add_handler(CommandHandler("ignore", cmd_ignore))
                bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

                profile = load_profile(settings.profile_path)
                if profile:
                    setup_scheduler(bot_app, settings, profile)

                await bot_app.initialize()
                await bot_app.start()
                if bot_app.updater is not None:
                    await bot_app.updater.start_polling(drop_pending_updates=True)
                log.info("web.telegram_bot_active")
            except Exception as exc:
                log.warning("web.telegram_bot_start_failed", error=str(exc))

        yield

        if bot_app:
            try:
                if bot_app.updater is not None:
                    await bot_app.updater.stop()
                await bot_app.stop()
                await bot_app.shutdown()
            except Exception:
                pass

    app.router.lifespan_context = lifespan

    @app.get("/", response_class=HTMLResponse)
    async def get_index() -> HTMLResponse:
        index_file = TEMPLATES_DIR / "index.html"
        if not index_file.exists():
            return HTMLResponse("<h1>TodayTech is loading... please check back shortly.</h1>")
        return HTMLResponse(index_file.read_text(encoding="utf-8"))

    @app.get("/api/stats")
    async def get_stats() -> dict[str, Any]:
        today = datetime.now(timezone.utc).date()
        async with get_session() as session:
            opps = await get_opportunities(session, limit=200)
            saved = await session.execute(
                select(UserStateRow).where(UserStateRow.status == "saved")
            )
            saved_count = len(saved.scalars().all())

            drafting = await session.execute(
                select(UserStateRow).where(UserStateRow.status.in_(["drafting", "in_progress"]))
            )
            drafting_count = len(drafting.scalars().all())

            last_scan = await get_last_scan(session)

        closing_soon_count = sum(
            1
            for o in opps
            if o.deadline and (o.deadline - today) <= timedelta(days=7) and o.deadline >= today
        )

        return {
            "total_opportunities": len(opps),
            "closing_soon_count": closing_soon_count,
            "saved_count": saved_count,
            "drafting_count": drafting_count,
            "last_scan": last_scan.finished_at.isoformat()
            if last_scan and last_scan.finished_at
            else None,
        }

    @app.get("/api/opportunities")
    async def list_opportunities(
        type: str | None = Query(None),
        status: str | None = Query("radar"),  # radar, saved, in_progress, applied, all
        query: str | None = Query(None),
        min_score: int = Query(0),
        closing_soon: bool = Query(False),
        timeframe: str | None = Query(None),  # day, week, month, all
    ) -> list[dict[str, Any]]:
        today = datetime.now(timezone.utc).date()
        # Every term must appear somewhere in the searchable fields ("google code" matches
        # "Google Summer of Code"). Filters run over all rows; the result cap applies after.
        terms = query.lower().split() if query else []
        async with get_session() as session:
            opps = await get_opportunities(
                session, type_filter=type, min_score=min_score, limit=None
            )

            user_states_stmt = select(UserStateRow)
            res = await session.execute(user_states_stmt)
            user_states = {row.opportunity_id: row.status for row in res.scalars().all()}

        results = []
        for opp in opps:
            if opp.is_scam:
                continue

            opp_status = user_states.get(opp.external_id, "radar")

            # Status filter
            if (
                status == "saved"
                and opp_status != "saved"
                or status in ("in_progress", "drafting")
                and opp_status not in ("drafting", "in_progress")
                or status == "applied"
                and opp_status != "applied"
                or status == "pipeline"
                and opp_status not in ("saved", "in_progress", "drafting", "applied")
                or status == "radar"
                and opp_status == "ignored"
            ):
                continue

            # Closing soon filter
            if closing_soon:
                if (
                    not opp.deadline
                    or (opp.deadline - today) > timedelta(days=7)
                    or opp.deadline < today
                ):
                    continue

            # Timeframe filter (day, week, month)
            if timeframe == "day":
                # Closing in next 3 days or top match
                if opp.deadline and (opp.deadline - today).days > 3 and opp.deadline >= today:
                    continue
            elif timeframe == "week":
                # Closing in next 7 days
                if not opp.deadline or (opp.deadline - today).days > 7 or opp.deadline < today:
                    continue
            elif timeframe == "month":
                # Closing in next 30 days
                if not opp.deadline or (opp.deadline - today).days > 30 or opp.deadline < today:
                    continue

            # Query search
            if terms:
                combined = " ".join(
                    field or ""
                    for field in (
                        opp.title,
                        opp.type,
                        opp.organizer,
                        opp.location,
                        opp.mode,
                        opp.field_required,
                        opp.reward,
                        opp.eligibility_text,
                        opp.source_name,
                        opp.fit_reason,
                    )
                ).lower()
                if not all(term in combined for term in terms):
                    continue

            days_left = (opp.deadline - today).days if opp.deadline else None

            results.append(
                {
                    "id": opp.external_id,
                    "title": opp.title,
                    "type": opp.type,
                    "organizer": opp.organizer or "Independent",
                    "deadline": opp.deadline.isoformat() if opp.deadline else None,
                    "days_left": days_left,
                    "mode": opp.mode or "Online",
                    "location": opp.location or "Global",
                    "cost": opp.cost,
                    "reward": opp.reward,
                    "eligibility_verdict": opp.eligibility_verdict,
                    "eligibility_reason": opp.eligibility_reason,
                    "fit_score": opp.fit_score,
                    "fit_reason": opp.fit_reason,
                    "application_url": opp.application_url or opp.source_url,
                    "source_name": opp.source_name,
                    "status": opp_status,
                }
            )
            if len(results) >= 100:
                break

        return results

    @app.get("/api/opportunity/{external_id}")
    async def get_opportunity(external_id: str) -> dict[str, Any]:
        async with get_session() as session:
            opp = await get_opportunity_by_id(session, external_id)
            if not opp:
                raise HTTPException(status_code=404, detail="Opportunity not found")

            user_state_res = await session.execute(
                select(UserStateRow)
                .where(UserStateRow.opportunity_id == opp.external_id)
                .order_by(UserStateRow.id.desc())
            )
            user_state = user_state_res.scalars().first()

            app_res = await session.execute(
                select(ApplicationRow)
                .where(ApplicationRow.opportunity_id == opp.external_id)
                .order_by(ApplicationRow.id.desc())
            )
            app_row = app_res.scalars().first()

        today = datetime.now(timezone.utc).date()
        days_left = (opp.deadline - today).days if opp.deadline else None

        return {
            "id": opp.external_id,
            "title": opp.title,
            "type": opp.type,
            "organizer": opp.organizer,
            "deadline": opp.deadline.isoformat() if opp.deadline else None,
            "days_left": days_left,
            "mode": opp.mode,
            "location": opp.location,
            "cost": opp.cost,
            "reward": opp.reward,
            "eligibility_text": opp.eligibility_text,
            "eligibility_verdict": opp.eligibility_verdict,
            "eligibility_reason": opp.eligibility_reason,
            "fit_score": opp.fit_score,
            "fit_reason": opp.fit_reason,
            "application_url": opp.application_url or opp.source_url,
            "status": user_state.status if user_state else "radar",
            "notes": user_state.notes if user_state else "",
            "has_drafts": app_row is not None,
            "drafts_dir": app_row.drafts_dir if app_row else "",
        }

    @app.post("/api/opportunity/{external_id}/state")
    async def update_opportunity_state(
        external_id: str, body: StateUpdateRequest
    ) -> dict[str, Any]:
        async with get_session() as session:
            opp = await get_opportunity_by_id(session, external_id)
            if not opp:
                raise HTTPException(status_code=404, detail="Opportunity not found")
            row = await set_user_state(session, external_id, body.status, notes=body.notes)
            await session.commit()
            return {"status": row.status, "opportunity_id": external_id}

    @app.post("/api/opportunity/{external_id}/apply")
    async def trigger_apply_assistant(external_id: str) -> dict[str, Any]:
        """Run the application assistant to generate tailored application drafts."""
        from todaytech.apply.assistant import run_application_assistant

        settings = get_settings()

        async with get_session() as session:
            opp = await get_opportunity_by_id(session, external_id)
            if not opp:
                raise HTTPException(status_code=404, detail="Opportunity not found")
            await set_user_state(session, external_id, "in_progress")
            await session.commit()

        # Run assistant
        result_text = await run_application_assistant(external_id, settings)

        return {
            "success": True,
            "opportunity_id": external_id,
            "summary": result_text,
        }

    @app.get("/api/opportunity/{external_id}/drafts")
    async def get_opportunity_drafts(external_id: str) -> dict[str, Any]:
        """List generated drafts and draft contents for an opportunity."""
        base_dir = Path("data/drafts")
        drafts_dir: Path | None = None
        if base_dir.exists():
            cand = base_dir / external_id[:16]
            if cand.exists() and cand.is_dir():
                drafts_dir = cand
            else:
                prefix = external_id[:8]
                for p in base_dir.iterdir():
                    if p.is_dir() and p.name.startswith(prefix):
                        drafts_dir = p
                        break

        if not drafts_dir or not drafts_dir.exists() or not drafts_dir.is_dir():
            return {"has_drafts": False, "files": [], "content": ""}

        files = []
        combined_text = []

        for p in sorted(drafts_dir.iterdir()):
            if p.is_file():
                ext = p.suffix.lower().lstrip(".")
                files.append(
                    {
                        "name": p.name,
                        "size_bytes": p.stat().st_size,
                        "extension": ext,
                        "download_url": f"/drafts/{external_id[:16]}/{p.name}",
                    }
                )
                if ext == "md":
                    try:
                        text = p.read_text(encoding="utf-8")
                        combined_text.append(text)
                    except Exception:
                        pass

        return {
            "has_drafts": len(files) > 0,
            "drafts_dir": str(drafts_dir),
            "files": files,
            "content": "\n\n---\n\n".join(combined_text),
        }

    @app.get("/api/opportunity/{external_id}/download/{filename}")
    @app.get("/drafts/{external_id}/{filename}")
    async def download_draft_file(external_id: str, filename: str) -> FileResponse:
        """Download a generated draft file (.docx, .md, etc.)."""
        clean_name = Path(filename).name
        base_dir = Path("data/drafts")
        drafts_dir: Path | None = None

        if base_dir.exists():
            cand = base_dir / external_id[:16]
            if cand.exists() and cand.is_dir():
                drafts_dir = cand
            else:
                prefix = external_id[:8]
                for p in base_dir.iterdir():
                    if p.is_dir() and p.name.startswith(prefix):
                        drafts_dir = p
                        break

        if not drafts_dir:
            raise HTTPException(status_code=404, detail="Draft directory not found")

        file_path = drafts_dir / clean_name
        if not file_path.exists() or not file_path.is_file():
            raise HTTPException(status_code=404, detail=f"Draft file '{clean_name}' not found")

        media_type = "application/octet-stream"
        if clean_name.endswith(".docx"):
            media_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        elif clean_name.endswith(".md") or clean_name.endswith(".txt"):
            media_type = "text/markdown; charset=utf-8"

        return FileResponse(
            path=file_path,
            filename=clean_name,
            media_type=media_type,
            headers={"Content-Disposition": f'attachment; filename="{clean_name}"'},
        )

    async def _safe_scan() -> None:
        try:
            await run_scan(force=True)
        except Exception as exc:
            log.warning("web.scan_failed", error=str(exc))

    @app.post("/api/scan")
    async def trigger_scan(background_tasks: BackgroundTasks) -> dict[str, str]:
        background_tasks.add_task(_safe_scan)
        return {"message": "Scan started in background"}

    @app.get("/api/digest")
    async def get_daily_digest(timeframe: str = Query("day")) -> dict[str, Any]:
        """Generate and return today's editorial digest bulletin with closing soon & hot picks."""
        from todaytech.core.digest import format_digest

        today = datetime.now(timezone.utc).date()
        async with get_session() as session:
            opportunities = await get_opportunities(session, limit=100)

        # Active unscammed opportunities
        valid_opps = [o for o in opportunities if not o.is_scam]

        # Calculate time window based on timeframe query
        max_days = 3 if timeframe == "day" else (7 if timeframe == "week" else 30)

        closing_soon = [
            o for o in valid_opps if o.deadline and 0 <= (o.deadline - today).days <= max_days
        ]
        closing_soon.sort(key=lambda o: o.deadline or date.max)

        # Top hot picks (ranked by fit score)
        hot_picks = sorted(valid_opps, key=lambda o: o.fit_score, reverse=True)[:10]

        digest_markdown = format_digest(valid_opps[:20])

        def _format_item(o: OpportunityRow) -> dict[str, Any]:
            return {
                "id": o.external_id,
                "title": o.title,
                "type": o.type,
                "organizer": o.organizer or "Independent",
                "deadline": o.deadline.isoformat() if o.deadline else None,
                "days_left": (o.deadline - today).days if o.deadline else None,
                "fit_score": o.fit_score,
                "fit_reason": o.fit_reason or "Strong profile match",
                "reward": o.reward or "Recognition & Experience",
                "application_url": o.application_url or o.source_url,
                "location": o.location or "Global",
                "mode": o.mode or "Online",
            }

        return {
            "date": today.strftime("%B %d, %Y"),
            "timeframe": timeframe,
            "closing_soon": [_format_item(o) for o in closing_soon[:6]],
            "hot_picks": [_format_item(o) for o in hot_picks],
            "digest_markdown": digest_markdown,
        }

    @app.post("/api/digest/send-telegram")
    async def send_digest_telegram() -> dict[str, Any]:
        """Send the formatted daily digest to Telegram on demand."""
        import httpx

        from todaytech.core.digest import format_digest

        settings = get_settings()
        if not settings.telegram_bot_token or not settings.telegram_user_id:
            raise HTTPException(
                status_code=400,
                detail="Telegram bot token or user ID not configured in .env",
            )

        async with get_session() as session:
            opportunities = await get_opportunities(session, limit=50)

        if not opportunities:
            return {"success": False, "message": "No opportunities in database to send."}

        digest_text = format_digest(opportunities)
        url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                url,
                json={
                    "chat_id": settings.telegram_user_id,
                    "text": digest_text,
                    "parse_mode": "Markdown",
                    "disable_web_page_preview": True,
                },
            )
            if resp.status_code == 200:
                return {"success": True, "message": "Digest sent to Telegram!"}
            raise HTTPException(
                status_code=502,
                detail=f"Telegram API error ({resp.status_code}): {resp.text}",
            )

    @app.get("/api/profile")
    async def get_user_profile() -> dict[str, Any]:
        settings = get_settings()
        profile = load_profile(settings.profile_path)
        if profile is None:
            # Fall back to example profile
            profile = load_profile(Path("profile.example.yaml"))

        if profile is None:
            return {
                "name": "Builder",
                "country": "Ghana",
                "level": "undergraduate",
                "year": 3,
                "university": "University",
                "field_of_study": "Computer Science",
                "skills": ["Python", "JavaScript", "AI"],
                "interests": ["Open Source", "Hackathons"],
                "opportunity_types": [
                    "hackathon",
                    "fellowship",
                    "scholarship",
                    "grant",
                    "internship",
                    "ambassador",
                ],
                "max_application_fee": 0.0,
            }

        return {
            "name": profile.name,
            "country": profile.country,
            "level": profile.level,
            "year": profile.year,
            "university": profile.university,
            "field_of_study": profile.field_of_study,
            "skills": profile.skills,
            "interests": profile.interests,
            "opportunity_types": profile.opportunity_types,
            "max_application_fee": profile.max_application_fee,
        }

    @app.post("/api/profile")
    async def save_user_profile(body: dict[str, Any]) -> dict[str, Any]:
        settings = get_settings()
        profile_file = settings.profile_path or Path("profile.yaml")

        existing_data: dict[str, Any] = {}
        if profile_file.exists():
            with open(profile_file, encoding="utf-8") as f:
                existing_data = yaml.safe_load(f) or {}
        elif Path("profile.example.yaml").exists():
            with open("profile.example.yaml", encoding="utf-8") as f:
                existing_data = yaml.safe_load(f) or {}

        existing_data.update(body)

        with open(profile_file, "w", encoding="utf-8") as f:
            yaml.safe_dump(existing_data, f, sort_keys=False)

        return {"success": True, "message": "Profile updated successfully"}

    return app


app = create_app()
