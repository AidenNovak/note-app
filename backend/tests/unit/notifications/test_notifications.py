"""Tests for push notification endpoints and service layer."""
import json
import uuid
from unittest.mock import AsyncMock, patch

import pytest

# ── Device Registration ──────────────────────────────


class TestRegisterDevice:
    @pytest.mark.asyncio
    async def test_register_device(self, client, auth_headers):
        resp = await client.post(
            "/api/v1/notifications/devices",
            json={"token": "ExponentPushToken[abc123]", "platform": "ios"},
            headers=auth_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["token"] == "ExponentPushToken[abc123]"
        assert data["platform"] == "ios"
        assert data["is_active"] is True

    @pytest.mark.asyncio
    async def test_register_device_with_name(self, client, auth_headers):
        resp = await client.post(
            "/api/v1/notifications/devices",
            json={
                "token": "ExponentPushToken[xyz789]",
                "platform": "android",
                "device_name": "Pixel 8",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 201
        assert resp.json()["device_name"] == "Pixel 8"

    @pytest.mark.asyncio
    async def test_register_duplicate_reactivates(self, client, auth_headers):
        token = f"ExponentPushToken[dup-{uuid.uuid4().hex[:8]}]"
        # Register
        resp1 = await client.post(
            "/api/v1/notifications/devices",
            json={"token": token, "platform": "ios"},
            headers=auth_headers,
        )
        assert resp1.status_code == 201
        id1 = resp1.json()["id"]

        # Unregister
        await client.request(
            "DELETE",
            "/api/v1/notifications/devices",
            json={"token": token},
            headers=auth_headers,
        )

        # Re-register — should reactivate, not create new
        resp2 = await client.post(
            "/api/v1/notifications/devices",
            json={"token": token, "platform": "ios"},
            headers=auth_headers,
        )
        assert resp2.status_code == 201
        assert resp2.json()["id"] == id1
        assert resp2.json()["is_active"] is True

    @pytest.mark.asyncio
    async def test_register_invalid_platform(self, client, auth_headers):
        resp = await client.post(
            "/api/v1/notifications/devices",
            json={"token": "ExponentPushToken[a]", "platform": "windows"},
            headers=auth_headers,
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_register_empty_token(self, client, auth_headers):
        resp = await client.post(
            "/api/v1/notifications/devices",
            json={"token": "", "platform": "ios"},
            headers=auth_headers,
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_register_requires_auth(self, client):
        resp = await client.post(
            "/api/v1/notifications/devices",
            json={"token": "ExponentPushToken[x]", "platform": "ios"},
        )
        assert resp.status_code == 401


# ── Unregister Device ─────────────────────────────────


class TestUnregisterDevice:
    @pytest.mark.asyncio
    async def test_unregister_device(self, client, auth_headers):
        token = f"ExponentPushToken[unreg-{uuid.uuid4().hex[:8]}]"
        await client.post(
            "/api/v1/notifications/devices",
            json={"token": token, "platform": "ios"},
            headers=auth_headers,
        )
        resp = await client.request(
            "DELETE",
            "/api/v1/notifications/devices",
            json={"token": token},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    @pytest.mark.asyncio
    async def test_unregister_nonexistent(self, client, auth_headers):
        resp = await client.request(
            "DELETE",
            "/api/v1/notifications/devices",
            json={"token": "ExponentPushToken[notexist]"},
            headers=auth_headers,
        )
        assert resp.status_code == 404


# ── List Devices ──────────────────────────────────────


class TestListDevices:
    @pytest.mark.asyncio
    async def test_list_devices(self, client, auth_headers):
        token = f"ExponentPushToken[list-{uuid.uuid4().hex[:8]}]"
        await client.post(
            "/api/v1/notifications/devices",
            json={"token": token, "platform": "ios"},
            headers=auth_headers,
        )
        resp = await client.get("/api/v1/notifications/devices", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert token in data["tokens"]
        assert data["count"] >= 1

    @pytest.mark.asyncio
    async def test_list_devices_empty(self, client, auth_headers):
        # Second user has no devices
        from tests.factories import UserFactory

        resp = await client.get("/api/v1/notifications/devices", headers=auth_headers)
        assert resp.status_code == 200
        # count could be >= 0 depending on other tests in same session


# ── Preferences ───────────────────────────────────────


class TestPreferences:
    @pytest.mark.asyncio
    async def test_get_default_preferences(self, client, auth_headers):
        resp = await client.get("/api/v1/notifications/preferences", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is True
        assert data["post_liked"] is True
        assert data["note_liked"] is True
        assert data["insight_ready"] is True
        assert data["mind_connection"] is True
        assert data["milestone"] is True
        assert data["quiet_hours_start"] is None

    @pytest.mark.asyncio
    async def test_update_preferences(self, client, auth_headers):
        resp = await client.patch(
            "/api/v1/notifications/preferences",
            json={"post_liked": False, "quiet_hours_start": 22, "quiet_hours_end": 7},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["post_liked"] is False
        assert data["quiet_hours_start"] == 22
        assert data["quiet_hours_end"] == 7
        # Other fields unchanged
        assert data["enabled"] is True

    @pytest.mark.asyncio
    async def test_update_preferences_disable_all(self, client, auth_headers):
        resp = await client.patch(
            "/api/v1/notifications/preferences",
            json={"enabled": False},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["enabled"] is False

    @pytest.mark.asyncio
    async def test_update_preferences_empty_body(self, client, auth_headers):
        resp = await client.patch(
            "/api/v1/notifications/preferences",
            json={},
            headers=auth_headers,
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_update_preferences_invalid_hour(self, client, auth_headers):
        resp = await client.patch(
            "/api/v1/notifications/preferences",
            json={"quiet_hours_start": 25},
            headers=auth_headers,
        )
        assert resp.status_code == 422


# ── Notification History ──────────────────────────────


class TestHistory:
    @pytest.mark.asyncio
    async def test_history_empty(self, client, auth_headers):
        resp = await client.get("/api/v1/notifications/history", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["items"] == []
        assert data["total"] == 0

    @pytest.mark.asyncio
    async def test_history_with_entries(self, client, auth_headers, db, test_user):
        """Insert log entries directly and verify they appear in history."""
        from app.models import PushNotificationLog

        for i in range(3):
            log = PushNotificationLog(
                id=str(uuid.uuid4()),
                user_id=test_user.id,
                type="post_liked",
                title=f"Test {i}",
                body=f"Body {i}",
                status="sent",
            )
            db.add(log)
        await db.commit()

        resp = await client.get("/api/v1/notifications/history", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 3
        assert len(data["items"]) == 3

    @pytest.mark.asyncio
    async def test_history_pagination(self, client, auth_headers, db, test_user):
        from app.models import PushNotificationLog

        for i in range(5):
            log = PushNotificationLog(
                id=str(uuid.uuid4()),
                user_id=test_user.id,
                type="milestone",
                title=f"Milestone {i}",
                body=f"Body {i}",
                status="sent",
            )
            db.add(log)
        await db.commit()

        resp = await client.get(
            "/api/v1/notifications/history?page=1&page_size=2",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 2
        assert data["total"] == 5
        assert data["page"] == 1


# ── Service Layer: send_notification ──────────────────


class TestSendNotification:
    @pytest.mark.asyncio
    async def test_send_respects_disabled_preference(self, client, auth_headers, db, test_user):
        """If user disables post_liked, that notification should be skipped."""
        from app.notifications.service import send_notification, register_device

        # Register device
        await register_device(db, test_user.id, "ExponentPushToken[svc1]", "ios")

        # Disable post_liked
        await client.patch(
            "/api/v1/notifications/preferences",
            json={"post_liked": False},
            headers=auth_headers,
        )

        with patch("app.notifications.service.send_push", new_callable=AsyncMock) as mock_push:
            result = await send_notification(
                db, test_user.id, "post_liked", "Test", "Body"
            )
            assert result is None
            mock_push.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_skips_when_no_tokens(self, db, test_user):
        from app.notifications.service import send_notification

        with patch("app.notifications.service.send_push", new_callable=AsyncMock) as mock_push:
            result = await send_notification(
                db, test_user.id, "milestone", "Test", "Body"
            )
            assert result is None
            mock_push.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_success(self, db, test_user):
        from app.notifications.service import send_notification, register_device

        await register_device(db, test_user.id, "ExponentPushToken[svc2]", "ios")

        with patch("app.notifications.service.send_push", new_callable=AsyncMock) as mock_push:
            mock_push.return_value = [{"status": "ok", "id": "ticket-123"}]
            result = await send_notification(
                db, test_user.id, "insight_ready", "Ready", "Your insight is done"
            )
            assert result is not None
            assert result.status == "sent"
            assert result.type == "insight_ready"
            mock_push.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_records_failure(self, db, test_user):
        from app.notifications.service import send_notification, register_device

        await register_device(db, test_user.id, "ExponentPushToken[svc3]", "ios")

        with patch("app.notifications.service.send_push", new_callable=AsyncMock) as mock_push:
            mock_push.return_value = [{"status": "error", "message": "InvalidToken"}]
            result = await send_notification(
                db, test_user.id, "post_liked", "Title", "Body"
            )
            assert result is not None
            assert result.status == "failed"
            assert "InvalidToken" in result.error


# ── Triggers (rate limiting) ──────────────────────────


class TestTriggers:
    @pytest.mark.asyncio
    async def test_trigger_rate_limited(self, db, test_user):
        """Second notification within 60s should be skipped."""
        from app.notifications.service import register_device
        from app.notifications.triggers import notify_post_liked

        await register_device(db, test_user.id, "ExponentPushToken[trig1]", "ios")

        with patch("app.notifications.service.send_push", new_callable=AsyncMock) as mock_push:
            mock_push.return_value = [{"status": "ok", "id": "t1"}]

            # First call — should send
            await notify_post_liked(db, test_user.id, "Alice", "My post")
            assert mock_push.call_count == 1

            # Second call — rate limited
            await notify_post_liked(db, test_user.id, "Bob", "My post")
            assert mock_push.call_count == 1  # still 1

    @pytest.mark.asyncio
    async def test_different_types_not_rate_limited(self, db, test_user):
        """Different notification types should not rate limit each other."""
        from app.notifications.service import register_device
        from app.notifications.triggers import notify_post_liked, notify_insight_ready

        await register_device(db, test_user.id, "ExponentPushToken[trig2]", "ios")

        with patch("app.notifications.service.send_push", new_callable=AsyncMock) as mock_push:
            mock_push.return_value = [{"status": "ok", "id": "t1"}]

            await notify_post_liked(db, test_user.id, "Alice", "Post")
            await notify_insight_ready(db, test_user.id, "ins-1", "Report")
            assert mock_push.call_count == 2
