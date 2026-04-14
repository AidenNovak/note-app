"""Integration tests — multi-step flows crossing module boundaries."""
from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from unittest.mock import AsyncMock

from app.models import BillingCustomer


pytestmark = pytest.mark.asyncio


def _mock_ai(mocker):
    mocker.patch("api.v1.notes._background_embed")
    mocker.patch("api.v1.notes._background_ai_tag")
    mocker.patch(
        "app.note_collaboration._generate_metadata",
        return_value={"title": "AI Title", "tags": ["ai-tag"]},
    )


class TestAuthToNotesFlow:
    """Register → verify → create note → update → delete."""

    async def test_full_flow(self, client: AsyncClient, mocker, db):
        _mock_ai(mocker)
        mock_email = mocker.patch("api.v1.auth.send_email", new_callable=AsyncMock, return_value=True)
        mocker.patch("api.v1.auth.render_verification_email", return_value=("Verify", "<p>Code</p>"))

        username = f"flow_{uuid.uuid4().hex[:8]}"
        email = f"{username}@test.com"

        # 1. Register
        reg = await client.post("/api/v1/auth/register", json={
            "username": username,
            "email": email,
            "password": "FlowTest123!",
        })
        assert reg.status_code == 201

        # 2. Login to get tokens
        login = await client.post("/api/v1/auth/login", json={
            "email": email,
            "password": "FlowTest123!",
        })
        assert login.status_code == 200
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        # 2. Get profile
        me = await client.get("/api/v1/auth/me", headers=headers)
        assert me.status_code == 200
        assert me.json()["email_verified"] is False

        # 3. Create a note
        note = await client.post("/api/v1/notes", json={
            "title": "Integration Note",
            "markdown_content": "# Test\nIntegration content",
            "tags": ["integration", "test"],
        }, headers=headers)
        assert note.status_code == 201
        note_id = note.json()["id"]
        assert note.json()["title"] == "Integration Note"

        # 4. List notes — should have 1
        listing = await client.get("/api/v1/notes", headers=headers)
        assert listing.json()["total"] == 1

        # 5. Update the note
        updated = await client.patch(f"/api/v1/notes/{note_id}", json={
            "title": "Updated Integration Note",
        }, headers=headers)
        assert updated.status_code == 200

        # 6. Get note detail
        detail = await client.get(f"/api/v1/notes/{note_id}", headers=headers)
        assert detail.status_code == 200
        assert detail.json()["title"] == "Updated Integration Note"
        assert "Integration content" in detail.json()["markdown_content"]

        # 7. Delete the note
        deleted = await client.delete(f"/api/v1/notes/{note_id}", headers=headers)
        assert deleted.status_code == 204

        # 8. Confirm gone
        gone = await client.get(f"/api/v1/notes/{note_id}", headers=headers)
        assert gone.status_code == 404


class TestNoteWithFileFlow:
    """Create note → register file → list files → check references."""

    async def test_note_file_flow(self, client: AsyncClient, auth_headers, mocker):
        _mock_ai(mocker)

        # 1. Create a note
        note = await client.post("/api/v1/notes", json={
            "title": "File Test Note",
            "markdown_content": "Has attachment",
        }, headers=auth_headers)
        assert note.status_code == 201
        note_id = note.json()["id"]

        # 2. Register a file attached to the note
        file_reg = await client.post("/api/v1/files/register", json={
            "key": "uploads/integration-test.jpg",
            "filename": "integration-test.jpg",
            "content_type": "image/jpeg",
            "size": 4096,
            "note_id": note_id,
        }, headers=auth_headers)
        assert file_reg.status_code == 201
        file_id = file_reg.json()["id"]

        # 3. List files — should include our file
        files = await client.get("/api/v1/files", headers=auth_headers)
        assert files.json()["total"] >= 1
        file_ids = [f["id"] for f in files.json()["items"]]
        assert file_id in file_ids

        # 4. Check file references
        refs = await client.get(f"/api/v1/files/{file_id}/references", headers=auth_headers)
        assert refs.status_code == 200
        assert len(refs.json()["references"]) == 1
        assert refs.json()["references"][0]["title"] == "File Test Note"

        # 5. Get file meta
        meta = await client.get(f"/api/v1/files/{file_id}/meta", headers=auth_headers)
        assert meta.status_code == 200
        assert meta.json()["category"] == "image"

        # 6. Delete file
        del_resp = await client.delete(f"/api/v1/files/{file_id}", headers=auth_headers)
        assert del_resp.status_code == 204


class TestBillingFlow:
    """Check free status → view plans → attempt checkout."""

    async def test_billing_flow(self, client: AsyncClient, auth_headers, db, test_user, mocker):
        # 1. Check billing status — should be free tier
        status = await client.get("/api/v1/payments/status", headers=auth_headers)
        assert status.status_code == 200
        data = status.json()
        assert data["current_entitlement"]["tier"] == "free"
        assert data["has_active_subscription"] is False

        # 2. Get available plans
        plans = await client.get("/api/v1/payments/plans")
        assert plans.status_code == 200
        plan_ids = [p["id"] for p in plans.json()["plans"]]
        assert "pro" in plan_ids
        assert "lifetime" in plan_ids

        # 3. Attempt checkout (Stripe not configured → 501)
        checkout = await client.post("/api/v1/payments/checkout", json={
            "plan_id": "pro",
            "price_id": "monthly",
            "success_url": "https://app.test.com/success",
            "cancel_url": "https://app.test.com/cancel",
        }, headers=auth_headers)
        assert checkout.status_code == 501  # Stripe not configured in test

        # 4. Configure Stripe mock and retry
        mocker.patch("api.v1.payments.settings").STRIPE_SECRET_KEY = "sk_test_123"
        mocker.patch("api.v1.payments.settings").STRIPE_PRICE_ID_PRO_MONTHLY = "price_test"
        mocker.patch("api.v1.payments.settings").STRIPE_PRICE_ID_PRO_YEARLY = "price_yearly"
        mocker.patch("api.v1.payments.settings").STRIPE_PRICE_ID_LIFETIME = "price_life"
        mocker.patch("api.v1.payments.settings").EASYSTARTER_WEB_URL = "https://app.test.com"
        mocker.patch(
            "api.v1.payments.create_checkout_session",
            new_callable=AsyncMock,
            return_value={"checkout_url": "https://checkout.stripe.com/test"},
        )

        # Create BillingCustomer for checkout
        db.add(BillingCustomer(
            id=str(uuid.uuid4()),
            user_id=test_user.id,
            provider="stripe",
            provider_customer_id="cus_test_123",
        ))
        await db.commit()

        checkout2 = await client.post("/api/v1/payments/checkout", json={
            "plan_id": "pro",
            "price_id": "monthly",
            "success_url": "https://app.test.com/success",
            "cancel_url": "https://app.test.com/cancel",
        }, headers=auth_headers)
        assert checkout2.status_code == 200
        assert "checkout_url" in checkout2.json()


class TestTokenRefreshFlow:
    """Login → use access token → refresh → use new token."""

    async def test_refresh_flow(self, client: AsyncClient, mocker):
        mocker.patch("api.v1.auth.send_email", new_callable=AsyncMock, return_value=True)
        mocker.patch("api.v1.auth.render_verification_email", return_value=("Verify", "<p>Code</p>"))

        username = f"refresh_{uuid.uuid4().hex[:8]}"
        email = f"{username}@test.com"

        # 1. Register
        reg = await client.post("/api/v1/auth/register", json={
            "username": username,
            "email": email,
            "password": "Refresh123!",
        })
        assert reg.status_code == 201

        # 2. Login
        login = await client.post("/api/v1/auth/login", json={
            "email": email,
            "password": "Refresh123!",
        })
        assert login.status_code == 200
        refresh_token = login.json()["refresh_token"]
        access_token = login.json()["access_token"]

        # 3. Use access token
        me = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {access_token}"})
        assert me.status_code == 200

        # 4. Refresh
        refresh = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
        assert refresh.status_code == 200
        new_access = refresh.json()["access_token"]

        # 5. Use new token
        me2 = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {new_access}"})
        assert me2.status_code == 200
        assert me2.json()["username"] == username
