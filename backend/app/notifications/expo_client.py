"""Expo push notification client.

Wraps the Expo Push API (https://docs.expo.dev/push-notifications/sending-notifications/)
using plain httpx instead of the SDK to avoid extra deps.
"""
from __future__ import annotations

import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"


async def send_push(
    tokens: list[str],
    title: str,
    body: str,
    data: Optional[dict] = None,
    *,
    badge: Optional[int] = None,
    sound: str = "default",
    channel_id: str = "default",
) -> list[dict]:
    """Send push notification to one or more Expo push tokens.

    Returns list of ticket dicts from Expo API.
    """
    if not tokens:
        return []

    messages = []
    for token in tokens:
        msg = {
            "to": token,
            "title": title,
            "body": body,
            "sound": sound,
            "channelId": channel_id,
        }
        if data:
            msg["data"] = data
        if badge is not None:
            msg["badge"] = badge
        messages.append(msg)

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                EXPO_PUSH_URL,
                json=messages,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
            )
            resp.raise_for_status()
            result = resp.json()
            tickets = result.get("data", [])

            for i, ticket in enumerate(tickets):
                if ticket.get("status") == "error":
                    logger.warning(
                        "push_delivery_error token=%s error=%s",
                        tokens[i] if i < len(tokens) else "?",
                        ticket.get("message"),
                    )

            return tickets
    except Exception:
        logger.exception("expo_push_send_failed")
        return []
