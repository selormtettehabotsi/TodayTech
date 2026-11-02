"""Digest formatter — produces the user-facing digest text.

Grouped by: Closing Soon / New Today / Worth Planning For.
Each item: title, type, organizer, deadline, fit score, eligibility, link.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from todaytech.core.database import OpportunityRow


def format_digest(
    opportunities: list[OpportunityRow],
    *,
    unhealthy_sources: list[str] | None = None,
    scan_errors: list[str] | None = None,
) -> str:
    """Format a list of opportunities into a scannable digest message."""
    if not opportunities:
        return "📭 No new opportunities found in this scan.\n\nTry broadening your profile or adding more sources."

    today = datetime.now(timezone.utc).date()
    closing_soon: list[OpportunityRow] = []
    new_today: list[OpportunityRow] = []
    worth_planning: list[OpportunityRow] = []

    for opp in opportunities:
        if opp.eligibility_verdict == "Not eligible":
            continue
        if opp.is_scam:
            continue

        if opp.deadline and (opp.deadline - today) <= timedelta(days=7):
            closing_soon.append(opp)
        elif opp.first_seen and (today - opp.first_seen.date()) <= timedelta(days=1):
            new_today.append(opp)
        else:
            worth_planning.append(opp)

    lines: list[str] = []
    lines.append("🎯 **TodayTech Daily Digest**")
    lines.append(f"📅 {today.strftime('%B %d, %Y')}\n")

    if closing_soon:
        lines.append("⏰ **CLOSING SOON**")
        for opp in sorted(closing_soon, key=lambda o: o.deadline or today):
            lines.append(_format_item(opp))
        lines.append("")

    if new_today:
        lines.append("🆕 **NEW TODAY**")
        for opp in sorted(new_today, key=lambda o: -o.fit_score):
            lines.append(_format_item(opp))
        lines.append("")

    if worth_planning:
        lines.append("📋 **WORTH PLANNING FOR**")
        for opp in sorted(worth_planning, key=lambda o: -o.fit_score)[:15]:
            lines.append(_format_item(opp))
        lines.append("")

    # Footer
    total = len(closing_soon) + len(new_today) + len(worth_planning)
    lines.append(f"📊 {total} opportunities shown | Use `details <id>` for more info")

    if unhealthy_sources:
        lines.append(f"⚠️ Unhealthy sources: {', '.join(unhealthy_sources)}")

    return "\n".join(lines)


def _format_item(opp: OpportunityRow) -> str:
    """Format a single digest item."""
    parts: list[str] = []

    # Scam warning
    if opp.is_scam:
        parts.append("🚩")

    # Title
    parts.append(f"  **{opp.title}**")

    # Type and organizer
    type_emoji = _type_emoji(opp.type)
    org = f" by {opp.organizer}" if opp.organizer else ""
    parts.append(f"  {type_emoji} {opp.type.capitalize()}{org}")

    # Deadline
    if opp.deadline:
        today_val = datetime.now(timezone.utc).date()
        days_left = (opp.deadline - today_val).days
        if days_left < 0:
            deadline_str = f"❌ Closed on {opp.deadline}"
        elif days_left == 0:
            deadline_str = f"🔴 Closes Today (Deadline: {opp.deadline} GMT 0:00)"
        elif days_left <= 3:
            deadline_str = f"🔴 {opp.deadline} (GMT 0:00 • {days_left}d left)"
        elif days_left <= 7:
            deadline_str = f"🟡 {opp.deadline} (GMT 0:00 • {days_left}d left)"
        else:
            deadline_str = f"🟢 {opp.deadline} (GMT 0:00 • {days_left}d left)"
        parts.append(f"  📅 {deadline_str}")
    else:
        parts.append("  📅 🗓️ Rolling / Open Enrollment")

    # Score and eligibility
    score_bar = _score_bar(opp.fit_score)
    parts.append(f"  {score_bar} Score: {opp.fit_score}/100 — {opp.fit_reason}")

    # Eligibility
    elig_emoji = _elig_emoji(opp.eligibility_verdict)
    parts.append(f"  {elig_emoji} {opp.eligibility_verdict}")

    # Link and ID
    link = opp.application_url or opp.source_url
    if link:
        parts.append(f"  🔗 {link}")
    parts.append(f"  🆔 `{opp.external_id[:8]}`")

    return "\n".join(parts) + "\n"


def _type_emoji(type_str: str) -> str:
    return {
        "hackathon": "💻",
        "scholarship": "🎓",
        "internship": "💼",
        "fellowship": "🏛️",
        "grant": "💰",
        "program": "📚",
        "competition": "🏆",
        "ambassador": "📣",
    }.get(type_str, "📌")


def _elig_emoji(verdict: str) -> str:
    return {
        "Eligible": "✅",
        "Likely": "🟢",
        "Check": "🟡",
        "Not eligible": "🔴",
    }.get(verdict, "❓")


def _score_bar(score: int) -> str:
    filled = score // 10
    empty = 10 - filled
    return "█" * filled + "░" * empty
