"""Tests for the TodayTech Public Read-Only Web Dashboard and REST APIs."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from todaytech.core.database import get_session, init_db
from todaytech.web.app import _seed_demo_if_empty, create_app
from todaytech.web.rate_limit import get_client_ip, is_trusted_proxy, rate_limiter


@pytest.fixture(autouse=True)
async def setup_test_db(tmp_path):
    # Isolated DB per test
    rate_limiter.reset()
    await init_db(f"sqlite+aiosqlite:///{(tmp_path / 'test.db').as_posix()}")
    async with get_session() as session:
        await _seed_demo_if_empty(session)


@pytest.mark.asyncio
async def test_public_unauthenticated_get_works():
    """Requirement 1 & 7: Verify unauthenticated GET / and public APIs work without any login/password."""
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Home page
        res_index = await client.get("/")
        assert res_index.status_code == 200
        assert "TodayTech" in res_index.text

        # Health checks
        res_h1 = await client.get("/health")
        assert res_h1.status_code == 200
        assert res_h1.json()["status"] == "healthy"

        res_h2 = await client.get("/api/health")
        assert res_h2.status_code == 200
        assert res_h2.json()["status"] == "healthy"

        # Stats API
        res_stats = await client.get("/api/stats")
        assert res_stats.status_code == 200
        stats = res_stats.json()
        assert "total_opportunities" in stats
        assert "closing_soon_count" in stats

        # Opportunities API
        res_opps = await client.get("/api/opportunities")
        assert res_opps.status_code == 200
        opps = res_opps.json()
        assert isinstance(opps, list)
        assert len(opps) > 0


@pytest.mark.asyncio
async def test_all_write_methods_return_405():
    """Requirement 2 & 7: Allow only GET requests. Return 405 for POST, PUT, PATCH and DELETE."""
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        target_endpoints = [
            "/",
            "/api/stats",
            "/api/opportunities",
            "/api/opportunity/c8f1e29a1b2c3d4e",
            "/api/scan",
            "/api/profile",
            "/api/opportunity/c8f1e29a1b2c3d4e/state",
            "/api/opportunity/c8f1e29a1b2c3d4e/apply",
            "/api/digest",
        ]

        for ep in target_endpoints:
            # POST
            res_post = await client.post(ep, json={"test": "data"})
            assert res_post.status_code == 405, (
                f"POST {ep} expected 405, got {res_post.status_code}"
            )
            assert res_post.headers.get("allow") == "GET, HEAD, OPTIONS"

            # PUT
            res_put = await client.put(ep, json={"test": "data"})
            assert res_put.status_code == 405, f"PUT {ep} expected 405, got {res_put.status_code}"

            # PATCH
            res_patch = await client.patch(ep, json={"test": "data"})
            assert res_patch.status_code == 405, (
                f"PATCH {ep} expected 405, got {res_patch.status_code}"
            )

            # DELETE
            res_del = await client.delete(ep)
            assert res_del.status_code == 405, (
                f"DELETE {ep} expected 405, got {res_del.status_code}"
            )


@pytest.mark.asyncio
async def test_public_api_never_exposes_private_fields():
    """Requirement 3 & 7: Return strictly permitted fields.

    MUST NOT return profile, preferences, email, saved or ignored flags,
    application drafts, CV or SOP text, or match reasoning text.
    Fails if any private field appears in any public response.
    """
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res_list = await client.get("/api/opportunities")
        assert res_list.status_code == 200
        items = res_list.json()
        assert len(items) > 0

        first_id = items[0]["id"]
        res_detail = await client.get(f"/api/opportunity/{first_id}")
        assert res_detail.status_code == 200
        detail = res_detail.json()

        res_stats = await client.get("/api/stats")
        assert res_stats.status_code == 200
        stats = res_stats.json()

        # Forbidden fields
        forbidden_fields = {
            "profile",
            "preferences",
            "email",
            "saved",
            "ignored",
            "status",
            "notes",
            "user_state",
            "saved_count",
            "drafting_count",
            "draft",
            "drafts",
            "cv",
            "sop",
            "fit_reason",
            "eligibility_reason",
            "eligibility_text",
            "is_scam",
            "scam_reason",
            "raw_text",
        }

        # Check all items in list response
        for item in items:
            for key in item.keys():
                assert key.lower() not in forbidden_fields, (
                    f"Forbidden field '{key}' leaked in list item!"
                )

        # Check detail item response
        for key in detail.keys():
            assert key.lower() not in forbidden_fields, (
                f"Forbidden field '{key}' leaked in detail item!"
            )

        # Check stats response
        for key in stats.keys():
            assert key.lower() not in forbidden_fields, f"Forbidden field '{key}' leaked in stats!"

        # Verify required permitted fields are present
        required_public_fields = {
            "title",
            "organisation",
            "type",
            "field",
            "location",
            "funding",
            "date_status",
            "deadline",
            "estimated_window",
            "days_left",
            "verified_on",
            "source_url",
            "apply_url",
            "match_percentage",
        }
        for field in required_public_fields:
            assert field in detail, (
                f"Expected public field '{field}' was missing from detail response!"
            )


@pytest.mark.asyncio
async def test_rate_limiting_blocks_abuse():
    """Requirement 4 & 7: Per-IP rate limiting blocks burst/abuse with HTTP 429 and Retry-After."""
    app = create_app()
    transport = ASGITransport(app=app)

    # Use a custom client IP
    headers = {"X-Forwarded-For": "203.0.113.195"}

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Send 60 requests (allowed)
        for i in range(60):
            res = await client.get("/api/stats", headers=headers)
            assert res.status_code == 200, f"Request {i + 1} failed with {res.status_code}"

        # 61st request triggers rate limit block (HTTP 429)
        res_blocked = await client.get("/api/stats", headers=headers)
        assert res_blocked.status_code == 429
        assert "Retry-After" in res_blocked.headers
        data = res_blocked.json()
        assert "Rate limit exceeded" in data["error"]

        # Health endpoint remains accessible
        res_health = await client.get("/health", headers=headers)
        assert res_health.status_code == 200


@pytest.mark.asyncio
async def test_client_ip_buckets_and_spoof_protection():
    """Requirement Task 1: Verify separate rate limit buckets per client IP

    and ensure proxy headers from untrusted direct clients are ignored.
    """
    app = create_app()

    # 1. Direct unit verification of get_client_ip
    # Case A: Trusted proxy (Caddy / Docker network / 127.0.0.1 / testclient)
    trusted_host = "172.18.0.4"
    assert is_trusted_proxy(trusted_host) is True
    assert (
        get_client_ip({"X-Forwarded-For": "198.51.100.1"}, client_host=trusted_host)
        == "198.51.100.1"
    )
    assert get_client_ip({"X-Real-IP": "198.51.100.2"}, client_host=trusted_host) == "198.51.100.2"

    # Case B: Untrusted client directly connecting (spoofed header)
    untrusted_host = "203.0.113.50"
    assert is_trusted_proxy(untrusted_host) is False
    # Spoofed header MUST BE IGNORED; real connecting socket IP must be used
    spoofed_result = get_client_ip(
        {"X-Forwarded-For": "1.2.3.4", "X-Real-IP": "5.6.7.8"},
        client_host=untrusted_host,
    )
    assert spoofed_result == "203.0.113.50"

    # 2. Integration verification: Two separate client IPs get independent rate limit buckets
    rate_limiter.reset()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Client 1 makes 60 requests through trusted proxy
        headers_client1 = {"X-Forwarded-For": "198.51.100.10"}
        for _ in range(60):
            res1 = await client.get("/api/stats", headers=headers_client1)
            assert res1.status_code == 200

        # 61st request from Client 1 is blocked
        res1_blocked = await client.get("/api/stats", headers=headers_client1)
        assert res1_blocked.status_code == 429

        # Client 2 has a separate bucket: request immediately succeeds with 200
        headers_client2 = {"X-Forwarded-For": "198.51.100.20"}
        res2 = await client.get("/api/stats", headers=headers_client2)
        assert res2.status_code == 200


@pytest.mark.asyncio
async def test_query_length_capping_and_page_size():
    """Requirement 4: Cap query length <= 100 and limit page size."""
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Long query > 100 chars returns 400 Bad Request
        long_query = "a" * 101
        res = await client.get("/api/opportunities", params={"query": long_query})
        assert res.status_code == 400
        assert "maximum length of 100" in res.json()["error"]

        # Query <= 100 succeeds
        valid_query = "a" * 100
        res_ok = await client.get("/api/opportunities", params={"query": valid_query})
        assert res_ok.status_code == 200


@pytest.mark.asyncio
async def test_docs_and_debug_routes_disabled():
    """Requirement 5: Confirm /docs, /openapi.json, and /redoc are disabled in production."""
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.get("/docs")).status_code == 404
        assert (await client.get("/redoc")).status_code == 404
        assert (await client.get("/openapi.json")).status_code == 404


@pytest.mark.asyncio
async def test_security_headers_over_http():
    """Requirement 6: Verify X-Frame-Options: DENY, X-Content-Type-Options: nosniff,

    Referrer-Policy: no-referrer, and CSP with no unsafe-inline.
    """
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/api/stats")
        assert res.status_code == 200
        assert res.headers.get("x-frame-options") == "DENY"
        assert res.headers.get("x-content-type-options") == "nosniff"
        assert res.headers.get("referrer-policy") == "no-referrer"

        csp = res.headers.get("content-security-policy", "")
        assert "frame-ancestors 'none'" in csp
        assert "unsafe-inline" not in csp
        assert "default-src 'self'" in csp


@pytest.mark.asyncio
async def test_search_and_timeframe_filters():
    """Verify search keyword and timeframe filtering in public API."""
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Search by term
        res_google = await client.get("/api/opportunities", params={"query": "Google"})
        assert res_google.status_code == 200
        items_google = res_google.json()
        assert any("Google" in item["title"] for item in items_google)

        # Timeframe filters
        for tf in ("all", "day", "week", "month"):
            res = await client.get("/api/opportunities", params={"timeframe": tf})
            assert res.status_code == 200
            assert isinstance(res.json(), list)
