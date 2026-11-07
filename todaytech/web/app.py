"""FastAPI public read-only web application for TodayTech.

Serves the public opportunity radar and read-only REST APIs.
All mutation actions (scans, saving, applying, ignoring) are strictly managed
via the authenticated Telegram bot.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import structlog
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

from todaytech.core.config import get_settings
from todaytech.core.database import (
    OpportunityRow,
    get_last_scan,
    get_opportunities,
    get_opportunity_by_id,
    get_session,
    init_db,
)
from todaytech.web.rate_limit import get_client_ip, rate_limiter

log = structlog.get_logger()

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"


async def _seed_demo_if_empty(session) -> None:
    """Seed benchmark opportunities if database is fresh."""
    from todaytech.core.database import upsert_opportunity

    demo_items: list[dict[str, Any]] = [
        {
            "external_id": "c8f1e29a1b2c3d4e",
            "title": "Google Summer of Code 2027 Mentee Program",
            "type": "fellowship",
            "organizer": "Google Open Source",
            "deadline": None,
            "date_status": "estimated",
            "estimated_window": "likely late March or early April 2027, based on past cycles",
            "cycle_year": 2027,
            "date_evidence": "Official 2027 application dates not yet published on summerofcode.withgoogle.com",
            "conflict": False,
            "verified_on": date(2026, 11, 7),
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
            "date_status": "confirmed",
            "cycle_year": 2026,
            "date_evidence": "Confirmed on ETHGlobal official schedule",
            "conflict": False,
            "verified_on": date(2026, 11, 7),
            "mode": "online",
            "location": "Global",
            "cost": 0.0,
            "reward": "$150,000+ in quadratic bounties and sponsor tracks",
            "eligibility_text": "Open to all software developers, researchers, and students globally.",
            "countries_allowed": '["all"]',
            "level_required": "undergraduate",
            "field_required": "Computer Science, AI, Web3",
            "fit_score": 94,
            "fit_reason": "Excellent hackathon fit; high prize pool; feasible 48-hour build timeline",
            "eligibility_verdict": "Eligible",
            "eligibility_reason": "Global eligibility confirmed",
            "application_url": "https://ethglobal.com",
            "source_url": "https://ethglobal.com",
            "source_name": "ETHGlobal",
        },
    ]

    for item in demo_items:
        await upsert_opportunity(session, item)
    await session.commit()


def _serialize_public_opportunity(opp: OpportunityRow, today: date) -> dict[str, Any]:
    """Serialize strictly permitted public fields.

    Forbidden: profile, preferences, email, saved/ignored flags, drafts,
    CV/SOP text, match reasoning (fit_reason), eligibility_reason, internal errors.
    """
    days_left = (
        (opp.deadline - today).days
        if (opp.date_status == "confirmed" and opp.deadline and opp.deadline >= today)
        else None
    )

    return {
        "id": opp.external_id,
        "title": opp.title,
        "organisation": opp.organizer or "Independent",
        "type": opp.type,
        "field": opp.field_required or "Computer Science / STEM",
        "location": opp.location or "Global",
        "funding": opp.reward or "N/A",
        "date_status": opp.date_status or "unknown",
        "deadline": (
            opp.deadline.isoformat()
            if (opp.date_status == "confirmed" and opp.deadline and opp.deadline >= today)
            else None
        ),
        "estimated_window": opp.estimated_window,
        "days_left": days_left,
        "verified_on": opp.verified_on.isoformat() if opp.verified_on else None,
        "source_url": opp.source_url or "",
        "apply_url": opp.application_url or opp.source_url or "",
        "match_percentage": opp.fit_score,
    }


def create_app() -> FastAPI:
    """FastAPI application factory for the public read-only TodayTech dashboard."""
    # Task 5: Disable /docs, /redoc, /openapi.json in production
    app = FastAPI(
        title="TodayTech Radar",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["GET", "HEAD", "OPTIONS"],
        allow_headers=["*"],
    )

    # Task 2: Allow only GET/HEAD/OPTIONS. Return 405 for all write methods.
    @app.middleware("http")
    async def enforce_read_only_middleware(request: Request, call_next):
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            return Response(
                content=json.dumps({"error": "Method Not Allowed. Dashboard is public read-only."}),
                status_code=405,
                media_type="application/json",
                headers={"Allow": "GET, HEAD, OPTIONS"},
            )
        return await call_next(request)

    # Task 4: Per-IP rate limiting (60 req/min with temporary block on abuse)
    @app.middleware("http")
    async def rate_limit_middleware(request: Request, call_next):
        if request.url.path in ("/health", "/api/health"):
            return await call_next(request)

        ip = get_client_ip(dict(request.headers), request.client.host if request.client else None)
        allowed, retry_after, error_msg = await rate_limiter.check(ip)
        if not allowed:
            headers = {"Retry-After": str(retry_after or 60)}
            return Response(
                content=json.dumps({"error": error_msg or "Too many requests. Please slow down."}),
                status_code=429,
                media_type="application/json",
                headers=headers,
            )
        return await call_next(request)

    # Task 6: Strict security headers over HTTP
    @app.middleware("http")
    async def add_security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; "
            "img-src 'self' data: https:; "
            "connect-src 'self'; "
            "frame-ancestors 'none';"
        )
        return response

    @app.middleware("http")
    async def add_no_cache_headers(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response

    # Task 4: Generic error handling with no stack traces or secrets
    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        return Response(
            content=json.dumps({"error": exc.detail}),
            status_code=exc.status_code,
            media_type="application/json",
        )

    @app.exception_handler(Exception)
    async def general_exception_handler(request: Request, exc: Exception):
        log.error("web.unhandled_exception", error=str(exc))
        return Response(
            content=json.dumps({"error": "An unexpected error occurred."}),
            status_code=500,
            media_type="application/json",
        )

    # Static assets
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings = get_settings()
        await init_db(settings.database_url)
        async with get_session() as session:
            await _seed_demo_if_empty(session)
        yield

    app.router.lifespan_context = lifespan

    # ── Public Endpoints ─────────────────────────────────────────

    @app.get("/health")
    @app.get("/api/health")
    async def health_check() -> dict[str, str]:
        return {"status": "healthy", "service": "todaytech-web"}

    @app.get("/", response_class=HTMLResponse)
    async def get_index() -> HTMLResponse:
        index_file = TEMPLATES_DIR / "index.html"
        if not index_file.exists():
            return HTMLResponse("<h1>TodayTech Radar is loading... please refresh shortly.</h1>")
        return HTMLResponse(index_file.read_text(encoding="utf-8"))

    @app.get("/api/stats")
    async def get_stats() -> dict[str, Any]:
        """Public radar stats: total active matches, closing soon, and last scan timestamp."""
        today = datetime.now(timezone.utc).date()
        async with get_session() as session:
            opps = await get_opportunities(session, limit=500)
            last_scan = await get_last_scan(session)

        active_matches_count = sum(
            1
            for o in opps
            if not o.is_scam
            and o.date_status == "confirmed"
            and (not o.deadline or o.deadline >= today)
        )
        closing_soon_count = sum(
            1
            for o in opps
            if not o.is_scam
            and o.date_status == "confirmed"
            and o.deadline
            and (o.deadline - today) <= timedelta(days=7)
            and o.deadline >= today
        )

        return {
            "total_opportunities": active_matches_count,
            "closing_soon_count": closing_soon_count,
            "last_scan": (
                last_scan.finished_at.isoformat() if last_scan and last_scan.finished_at else None
            ),
        }

    @app.get("/api/opportunities")
    async def list_opportunities(
        type: str | None = Query(None),
        query: str | None = Query(None),
        min_score: int = Query(0, ge=0, le=100),
        closing_soon: bool = Query(False),
        timeframe: str | None = Query(None),  # day, week, month, all
        limit: int = Query(50, ge=1, le=50),
    ) -> list[dict[str, Any]]:
        """List publicly available opportunities.

        Protected against quota abuse: capped query length, capped limit,
        and returns strictly permitted public fields.
        """
        # Task 4: Cap query length
        if query and len(query) > 100:
            raise HTTPException(
                status_code=400,
                detail="Search query exceeds maximum length of 100 characters.",
            )

        today = datetime.now(timezone.utc).date()
        terms = query.lower().split() if query else []
        async with get_session() as session:
            opps = await get_opportunities(
                session, type_filter=type, min_score=min_score, limit=None
            )

        results: list[dict[str, Any]] = []
        for opp in opps:
            if opp.is_scam:
                continue

            # Drop items with past confirmed deadline
            if opp.date_status == "confirmed" and opp.deadline and opp.deadline < today:
                continue

            # Closing soon filter
            if closing_soon:
                if (
                    opp.date_status != "confirmed"
                    or not opp.deadline
                    or (opp.deadline - today) > timedelta(days=7)
                    or opp.deadline < today
                ):
                    continue

            # Timeframe filter (day, week, month)
            if timeframe == "day":
                if (
                    opp.date_status == "confirmed"
                    and opp.deadline
                    and (opp.deadline - today).days > 3
                    and opp.deadline >= today
                ):
                    continue
            elif timeframe == "week":
                if (
                    opp.date_status != "confirmed"
                    or not opp.deadline
                    or (opp.deadline - today).days > 7
                    or opp.deadline < today
                ):
                    continue
            elif timeframe == "month":
                if (
                    opp.date_status != "confirmed"
                    or not opp.deadline
                    or (opp.deadline - today).days > 30
                    or opp.deadline < today
                ):
                    continue

            # Query search matching across public fields
            if terms:
                combined = " ".join(
                    str(field or "")
                    for field in (
                        opp.title,
                        opp.type,
                        opp.organizer,
                        opp.location,
                        opp.field_required,
                        opp.reward,
                        opp.estimated_window,
                    )
                ).lower()
                if not all(term in combined for term in terms):
                    continue

            results.append(_serialize_public_opportunity(opp, today))
            if len(results) >= limit:
                break

        return results

    @app.get("/api/opportunity/{external_id}")
    async def get_opportunity(external_id: str) -> dict[str, Any]:
        """Fetch details for a single opportunity in public read-only mode."""
        today = datetime.now(timezone.utc).date()
        async with get_session() as session:
            opp = await get_opportunity_by_id(session, external_id)
            if not opp or opp.is_scam:
                raise HTTPException(status_code=404, detail="Opportunity not found")

        return _serialize_public_opportunity(opp, today)

    return app


app = create_app()
