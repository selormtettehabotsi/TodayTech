"""Test Task 4: Re-verification job."""

from datetime import date

import httpx
import pytest

from todaytech.core.database import OpportunityRow
from todaytech.scheduler.jobs import reverify_opportunity


@pytest.mark.asyncio
async def test_reverify_downgrades_on_closed_page():
    today = date(2026, 11, 7)
    opp = OpportunityRow(
        title="Sample Opportunity",
        type="hackathon",
        source_url="https://example.com/hackathon",
        date_status="confirmed",
        deadline=date(2026, 11, 15),
        verified_on=date(2026, 11, 1),
    )

    class MockTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            return httpx.Response(
                200,
                text="<html><body>Applications are now closed for this cycle. Thank you for participating.</body></html>",
            )

    client = httpx.AsyncClient(transport=MockTransport())
    downgraded, status = await reverify_opportunity(opp, today, client)
    await client.aclose()

    assert downgraded is True
    assert opp.date_status == "unknown"
    assert opp.conflict is True
    assert opp.deadline is None
    assert opp.verified_on == today


@pytest.mark.asyncio
async def test_reverify_downgrades_on_http_404():
    today = date(2026, 11, 7)
    opp = OpportunityRow(
        title="Dead Link Opportunity",
        type="fellowship",
        source_url="https://example.com/dead-link",
        date_status="confirmed",
        deadline=date(2026, 11, 20),
        verified_on=date(2026, 11, 1),
    )

    class MockTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            return httpx.Response(404, text="Not Found")

    client = httpx.AsyncClient(transport=MockTransport())
    downgraded, status = await reverify_opportunity(opp, today, client)
    await client.aclose()

    assert downgraded is True
    assert opp.date_status == "unknown"
    assert opp.conflict is True
    assert opp.verified_on == today


@pytest.mark.asyncio
async def test_reverify_confirms_valid_deadline():
    today = date(2026, 11, 7)
    opp = OpportunityRow(
        title="Active Hackathon",
        type="hackathon",
        source_url="https://example.com/active-hack",
        date_status="confirmed",
        deadline=date(2026, 11, 15),
        verified_on=date(2026, 11, 1),
    )

    class MockTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            return httpx.Response(
                200,
                text="<html><body>Round 1 submissions close on November 15, 2026. Submit your project on time.</body></html>",
            )

    client = httpx.AsyncClient(transport=MockTransport())
    downgraded, status = await reverify_opportunity(opp, today, client)
    await client.aclose()

    assert downgraded is False
    assert opp.date_status == "confirmed"
    assert opp.conflict is False
    assert opp.deadline == date(2026, 11, 15)
    assert opp.days_remaining == 8
    assert opp.verified_on == today
