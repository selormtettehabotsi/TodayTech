"""Telegram alerting module for critical events and system notifications."""

from __future__ import annotations

from typing import Any

import httpx
import structlog

log = structlog.get_logger()


async def send_telegram_alert(message: str, settings: Any = None) -> bool:
    """Send an immediate alert message directly to the configured Telegram user."""
    if settings is None:
        try:
            from todaytech.core.config import get_settings

            settings = get_settings()
        except Exception as exc:
            log.warning("alerts.settings_failed", error=str(exc))
            return False

    token = getattr(settings, "telegram_bot_token", None)
    user_id = getattr(settings, "telegram_user_id", None)

    if not token or not user_id or str(token).startswith("your"):
        log.warning("alerts.telegram_not_configured")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": user_id,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code != 200:
                # Fallback to plain text if markdown formatting encounters issues
                payload.pop("parse_mode", None)
                retry_resp = await client.post(url, json=payload)
                return retry_resp.status_code == 200
            return True
    except Exception as exc:
        log.warning("alerts.telegram_send_failed", error=str(exc))
        return False
