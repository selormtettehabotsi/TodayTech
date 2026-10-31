"""Application assistant — helps users prepare materials for an opportunity.

Steps:
1. Re-read the opportunity and list exact requirements.
2. Build a checklist marking what the user has vs needs.
3. Draft tailored materials (motivation letter, bio, project descriptions).
4. For hackathons: idea shortlist + 48-hour build plan.
5. Final review against stated criteria.
6. Export drafts as Markdown and DOCX to data/drafts/.

NEVER submits anything on the user's behalf.
"""

from __future__ import annotations

import json
from pathlib import Path

import structlog
from docx import Document

from todaytech.core.config import Settings, load_profile
from todaytech.core.database import (
    ApplicationRow,
    OpportunityRow,
    get_session,
)
from todaytech.core.llm import create_llm_client_from_settings

log = structlog.get_logger()


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_REQUIREMENTS_PROMPT = """Analyze this opportunity and list all application requirements.

OPPORTUNITY:
- Title: {title}
- Type: {type}
- Organizer: {organizer}
- Eligibility: {eligibility_text}
- Reward: {reward}
- Source URL: {source_url}

Return JSON:
{{
  "requirements": ["list", "of", "required", "items"],
  "documents_needed": ["CV", "motivation letter", "transcript", etc.],
  "has_essay": true/false,
  "essay_prompts": ["list of essay questions if any"],
  "needs_referees": true/false,
  "referee_count": 0,
  "additional_links_needed": ["portfolio", "github", etc.],
  "notes": "any additional notes"
}}
"""

_DRAFT_PROMPT = """Write a {draft_type} for this opportunity application.

OPPORTUNITY:
- Title: {title}
- Type: {type}
- Organizer: {organizer}
- What they're looking for: {eligibility_text}
- Reward: {reward}

USER PROFILE:
- Name: {name}
- Field: {field_of_study}
- Level: {level}, Year {year}
- University: {university}
- Skills: {skills}
- Interests: {interests}
- Causes: {causes}

{voice_instruction}

INSTRUCTIONS:
- Be specific and concrete. Reference the user's actual skills and experiences.
- Match the opportunity's stated criteria.
- No filler, no generic phrases.
- Be direct and genuine.
- Keep it under 500 words unless the opportunity specifies otherwise.
"""

_HACKATHON_PROMPT = """Generate hackathon preparation materials for this event.

HACKATHON:
- Title: {title}
- Theme/Focus: {eligibility_text}
- Prizes: {reward}

USER:
- Skills: {skills}
- Interests: {interests}
- Level: {level}

Return JSON:
{{
  "ideas": [
    {{
      "name": "Project Name",
      "description": "One paragraph",
      "tech_stack": ["list"],
      "matches_theme": true/false,
      "difficulty": "beginner|intermediate|advanced",
      "unique_angle": "what makes this stand out"
    }}
  ],
  "build_plan_48h": {{
    "hour_0_4": "Setup and planning",
    "hour_4_12": "Core features",
    "hour_12_24": "MVP completion",
    "hour_24_36": "Polish and testing",
    "hour_36_44": "Documentation and demo prep",
    "hour_44_48": "Final testing and submission"
  }},
  "tips": ["list of hackathon tips"]
}}
"""

_REVIEW_PROMPT = """Review this draft against the opportunity's requirements.

OPPORTUNITY REQUIREMENTS:
{requirements}

DRAFT:
{draft}

Provide feedback in JSON:
{{
  "score": 0-100,
  "strengths": ["list"],
  "improvements": ["list"],
  "missing_elements": ["list"],
  "revised_draft": "improved version if score < 80, otherwise empty string"
}}
"""


# ---------------------------------------------------------------------------
# Main assistant
# ---------------------------------------------------------------------------


async def run_application_assistant(
    opp_id: str,
    settings: Settings,
) -> str:
    """Run the full application assistant workflow for an opportunity."""
    profile = load_profile(settings.profile_path)
    if profile is None:
        return "❌ Profile not found. Run `todaytech init` first."

    llm = create_llm_client_from_settings(settings)

    # Find the opportunity
    async with get_session() as session:
        from sqlalchemy import select

        stmt = select(OpportunityRow).where(OpportunityRow.external_id.startswith(opp_id))
        result = await session.execute(stmt)
        opp = result.scalar_one_or_none()

    if not opp:
        return f"❌ Opportunity `{opp_id}` not found."

    lines: list[str] = [f"📝 **Application Assistant: {opp.title}**\n"]

    # Step 1: Analyze requirements
    lines.append("**Step 1: Requirements Analysis**\n")
    try:
        reqs = await llm.generate_json(
            _REQUIREMENTS_PROMPT.format(
                title=opp.title,
                type=opp.type,
                organizer=opp.organizer,
                eligibility_text=opp.eligibility_text[:500],
                reward=opp.reward,
                source_url=opp.source_url,
            ),
            system="You are an application requirements analyst.",
        )
        lines.append("📋 **Requirements:**")
        for req in reqs.get("requirements", []):
            lines.append(f"  • {req}")
        lines.append("")
    except Exception as exc:
        reqs = {}
        lines.append(f"  ⚠️ Could not analyze requirements: {exc}\n")

    # Step 2: Checklist
    lines.append("**Step 2: Checklist**\n")
    docs_needed = reqs.get("documents_needed", [])
    user_has = []
    user_needs = []
    for doc in docs_needed:
        doc_lower = doc.lower()
        if "cv" in doc_lower or "resume" in doc_lower:
            if profile.cv_path:
                user_has.append(doc)
            else:
                user_needs.append(doc)
        elif "transcript" in doc_lower:
            user_needs.append(doc)
        else:
            user_needs.append(doc)

    if user_has:
        lines.append("✅ **You have:**")
        for item in user_has:
            lines.append(f"  • {item}")
    if user_needs:
        lines.append("📝 **You need:**")
        for item in user_needs:
            lines.append(f"  • {item}")
    lines.append("")

    # Step 3: Draft materials
    drafts_dir = Path("data/drafts") / opp.external_id[:16]
    drafts_dir.mkdir(parents=True, exist_ok=True)

    # Load writing samples for voice matching
    voice_instruction = ""
    if profile.writing_samples:
        samples = []
        for sample_path in profile.writing_samples[:2]:
            try:
                with open(sample_path, encoding="utf-8") as f:
                    samples.append(f.read()[:500])
            except Exception:
                pass
        if samples:
            voice_instruction = "Match the writer's voice from these samples:\n" + "\n---\n".join(
                samples
            )

    draft_types = []
    if opp.type in ("hackathon", "competition"):
        draft_types = ["hackathon pitch", "team bio"]
    else:
        draft_types = ["motivation letter", "short bio"]

    lines.append("**Step 3: Drafts**\n")
    for draft_type in draft_types:
        try:
            draft = await llm.generate(
                _DRAFT_PROMPT.format(
                    draft_type=draft_type,
                    title=opp.title,
                    type=opp.type,
                    organizer=opp.organizer,
                    eligibility_text=opp.eligibility_text[:300],
                    reward=opp.reward,
                    name=profile.name,
                    field_of_study=profile.field_of_study,
                    level=profile.level,
                    year=profile.year,
                    university=profile.university,
                    skills=", ".join(profile.skills[:10]),
                    interests=", ".join(profile.interests[:5]),
                    causes=", ".join(profile.causes[:3]),
                    voice_instruction=voice_instruction,
                ),
                system="You are a professional application writer.",
            )

            # Save as Markdown
            md_path = drafts_dir / f"{draft_type.replace(' ', '_')}.md"
            md_path.write_text(f"# {draft_type.title()}\n\n{draft}", encoding="utf-8")

            # Save as DOCX
            docx_path = drafts_dir / f"{draft_type.replace(' ', '_')}.docx"
            doc = Document()
            doc.add_heading(draft_type.title(), level=1)
            for para in draft.split("\n\n"):
                doc.add_paragraph(para)
            doc.save(str(docx_path))

            lines.append(f"✅ {draft_type.title()} → `{md_path}` + `.docx`")

        except Exception as exc:
            lines.append(f"⚠️ {draft_type.title()} draft failed: {exc}")

    lines.append("")

    # Step 4: Hackathon-specific
    if opp.type in ("hackathon", "competition"):
        lines.append("**Step 4: Hackathon Prep**\n")
        try:
            hack_data = await llm.generate_json(
                _HACKATHON_PROMPT.format(
                    title=opp.title,
                    eligibility_text=opp.eligibility_text[:300],
                    reward=opp.reward,
                    skills=", ".join(profile.skills[:10]),
                    interests=", ".join(profile.interests[:5]),
                    level=profile.level,
                ),
                system="You are a hackathon strategy advisor.",
            )

            lines.append("💡 **Project Ideas:**")
            for idea in hack_data.get("ideas", [])[:3]:
                lines.append(
                    f"  • **{idea.get('name', 'Idea')}** — {idea.get('description', '')[:100]}"
                )
                lines.append(f"    Stack: {', '.join(idea.get('tech_stack', []))}")

            build_plan = hack_data.get("build_plan_48h", {})
            if build_plan:
                lines.append("\n📅 **48-Hour Build Plan:**")
                for period, task in build_plan.items():
                    lines.append(f"  • {period}: {task}")

            # Save hackathon prep
            hack_path = drafts_dir / "hackathon_prep.md"
            hack_path.write_text(json.dumps(hack_data, indent=2), encoding="utf-8")

        except Exception as exc:
            lines.append(f"⚠️ Hackathon prep failed: {exc}")
        lines.append("")

    # Step 5: Track application
    async with get_session() as session:
        app_row = ApplicationRow(
            opportunity_id=opp.external_id,
            status="drafting",
            requirements_json=json.dumps(reqs),
            drafts_dir=str(drafts_dir),
        )
        session.add(app_row)
        await session.commit()

    # Footer
    lines.append("**Next Steps:**")
    lines.append(f"  📁 Drafts saved to `{drafts_dir}`")
    lines.append(f"  📥 Word Doc: `/drafts/{opp.external_id[:16]}/motivation_letter.docx`")
    lines.append(f"  📥 Short Bio: `/drafts/{opp.external_id[:16]}/short_bio.docx`")
    lines.append("  ✏️ Review and personalize the drafts")
    if opp.application_url:
        lines.append(f"  🔗 Apply here: {opp.application_url}")
    lines.append("  ⚠️ We never submit on your behalf — you're in control.")

    return "\n".join(lines)
