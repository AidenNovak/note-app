"""Tests for payment endpoints — plans, billing status, checkout, portal, upgrade."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    BillingCustomer,
    BillingSubscription,
    BillingPurchase,
)


pytestmark = pytest.mark.asyncio


# ── GET /plans ───────────────────────────────────────────

class TestGetPlans:
    """GET /api/v1/payments/plans — public, no auth needed."""

    async def test_list_plans(self, client: AsyncClient):
        resp = await client.get("/api/v1/payments/plans")
        assert resp.status_code == 200
        plans = resp.json()["plans"]
        plan_ids = [p["id"] for p in plans]
        assert "free" in plan_ids
        assert "pro" in plan_ids
        assert "lifetime" in plan_ids

    async def test_pro_plan_has_prices(self, client: AsyncClient):
        resp = await client.get("/api/v1/payments/plans")
        pro = next(p for p in resp.json()["plans"] if p["id"] == "pro")
        assert len(pro["prices"]) == 2
        intervals = {p["interval"] for p in pro["prices"]}
        assert intervals == {"month", "year"}

    async def test_lifetime_plan_no_interval(self, client: AsyncClient):
        resp = await client.get("/api/v1/payments/plans")
        lifetime = next(p for p in resp.json()["plans"] if p["id"] == "lifetime")
        assert len(lifetime["prices"]) == 1
        assert lifetime["prices"][0]["price_type"] == "lifetime"
        assert lifetime["prices"][0]["interval"] is None


# ── GET /status ──────────────────────────────────────────

class TestBillingStatus:
    """GET /api/v1/payments/status"""

    async def test_free_user(self, client: AsyncClient, test_user, auth_headers):
        resp = await client.get("/api/v1/payments/status", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["currentEntitlement"]["tier"] == "free"
        assert data["hasActiveSubscription"] is False
        assert data["billingProvider"] is None

    async def test_monthly_subscriber(self, client: AsyncClient, test_user, auth_headers, db):
        sub = BillingSubscription(
            id=str(uuid.uuid4()),
            user_id=test_user.id,
            provider="stripe",
            provider_subscription_id="sub_test_123",
            provider_customer_id="cus_test_123",
            plan_id="pro",
            price_id="monthly",
            status="active",
            current_period_end=datetime.now(timezone.utc) + timedelta(days=30),
        )
        db.add(sub)
        await db.commit()

        resp = await client.get("/api/v1/payments/status", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["currentEntitlement"]["tier"] == "monthly"
        assert data["hasActiveSubscription"] is True
        assert data["billingProvider"] == "stripe"
        assert data["canManageBilling"] is True

    async def test_yearly_subscriber(self, client: AsyncClient, test_user, auth_headers, db):
        sub = BillingSubscription(
            id=str(uuid.uuid4()),
            user_id=test_user.id,
            provider="stripe",
            provider_subscription_id="sub_yearly_123",
            provider_customer_id="cus_yearly_123",
            plan_id="pro",
            price_id="yearly",
            status="active",
            current_period_end=datetime.now(timezone.utc) + timedelta(days=365),
        )
        db.add(sub)
        await db.commit()

        resp = await client.get("/api/v1/payments/status", headers=auth_headers)
        data = resp.json()
        assert data["currentEntitlement"]["tier"] == "yearly"

    async def test_lifetime_purchase(self, client: AsyncClient, test_user, auth_headers, db):
        purchase = BillingPurchase(
            id=str(uuid.uuid4()),
            user_id=test_user.id,
            provider="stripe",
            provider_payment_intent_id="pi_lifetime_123",
            plan_id="lifetime",
            price_id="lifetime",
            status="succeeded",
        )
        db.add(purchase)
        await db.commit()

        resp = await client.get("/api/v1/payments/status", headers=auth_headers)
        data = resp.json()
        assert data["currentEntitlement"]["tier"] == "lifetime"
        assert data["currentEntitlement"]["source"] == "lifetime"

    async def test_lifetime_beats_subscription(self, client: AsyncClient, test_user, auth_headers, db):
        """Lifetime should outrank an active subscription."""
        sub = BillingSubscription(
            id=str(uuid.uuid4()),
            user_id=test_user.id,
            provider="stripe",
            provider_subscription_id="sub_rank_test",
            provider_customer_id="cus_rank_test",
            plan_id="pro",
            price_id="monthly",
            status="active",
            current_period_end=datetime.now(timezone.utc) + timedelta(days=30),
        )
        purchase = BillingPurchase(
            id=str(uuid.uuid4()),
            user_id=test_user.id,
            provider="stripe",
            provider_payment_intent_id="pi_rank_test",
            plan_id="lifetime",
            price_id="lifetime",
            status="succeeded",
        )
        db.add_all([sub, purchase])
        await db.commit()

        resp = await client.get("/api/v1/payments/status", headers=auth_headers)
        assert resp.json()["currentEntitlement"]["tier"] == "lifetime"

    async def test_cancelled_subscription(self, client: AsyncClient, test_user, auth_headers, db):
        sub = BillingSubscription(
            id=str(uuid.uuid4()),
            user_id=test_user.id,
            provider="stripe",
            provider_subscription_id="sub_cancelled",
            provider_customer_id="cus_cancelled",
            plan_id="pro",
            price_id="monthly",
            status="canceled",
            current_period_end=datetime.now(timezone.utc),
        )
        db.add(sub)
        await db.commit()

        resp = await client.get("/api/v1/payments/status", headers=auth_headers)
        data = resp.json()
        assert data["currentEntitlement"]["tier"] == "free"
        assert data["hasActiveSubscription"] is False

    async def test_trialing_counts_as_active(self, client: AsyncClient, test_user, auth_headers, db):
        sub = BillingSubscription(
            id=str(uuid.uuid4()),
            user_id=test_user.id,
            provider="stripe",
            provider_subscription_id="sub_trial_123",
            provider_customer_id="cus_trial_123",
            plan_id="pro",
            price_id="monthly",
            status="trialing",
            current_period_end=datetime.now(timezone.utc) + timedelta(days=7),
        )
        db.add(sub)
        await db.commit()

        resp = await client.get("/api/v1/payments/status", headers=auth_headers)
        data = resp.json()
        assert data["currentEntitlement"]["tier"] == "monthly"
        assert data["hasActiveSubscription"] is True

    async def test_unauthenticated(self, client: AsyncClient):
        resp = await client.get("/api/v1/payments/status")
        assert resp.status_code in (401, 403)


# ── POST /checkout ───────────────────────────────────────

class TestCheckout:
    """POST /api/v1/payments/checkout"""

    async def test_checkout_not_configured(self, client: AsyncClient, auth_headers, mocker):
        mocker.patch("api.v1.payments.settings").STRIPE_SECRET_KEY = ""
        resp = await client.post("/api/v1/payments/checkout", headers=auth_headers, json={
            "plan_id": "pro",
            "price_id": "monthly",
            "success_url": "https://app.jilly.app/success",
            "cancel_url": "https://app.jilly.app/cancel",
        })
        assert resp.status_code == 501

    async def test_checkout_success(self, client: AsyncClient, test_user, auth_headers, db, mocker):
        mocker.patch("api.v1.payments.settings").STRIPE_SECRET_KEY = "sk_test_123"
        mocker.patch(
            "app.payments.service._stripe_request",
            new_callable=AsyncMock,
            return_value={
                "id": "cs_test_session",
                "url": "https://checkout.stripe.com/pay/cs_test_session",
                "expires_at": 1700000000,
            },
        )

        resp = await client.post("/api/v1/payments/checkout", headers=auth_headers, json={
            "plan_id": "pro",
            "price_id": "monthly",
            "success_url": "https://app.jilly.app/success",
            "cancel_url": "https://app.jilly.app/cancel",
        })
        assert resp.status_code == 200
        assert "url" in resp.json()

    async def test_checkout_invalid_plan(self, client: AsyncClient, auth_headers, mocker):
        mocker.patch("api.v1.payments.settings").STRIPE_SECRET_KEY = "sk_test_123"

        resp = await client.post("/api/v1/payments/checkout", headers=auth_headers, json={
            "plan_id": "nonexistent",
            "price_id": "fake",
            "success_url": "https://app.jilly.app/success",
            "cancel_url": "https://app.jilly.app/cancel",
        })
        assert resp.status_code == 400

    async def test_checkout_unauthenticated(self, client: AsyncClient):
        resp = await client.post("/api/v1/payments/checkout", json={
            "plan_id": "pro",
            "price_id": "monthly",
            "success_url": "https://app.jilly.app/success",
            "cancel_url": "https://app.jilly.app/cancel",
        })
        assert resp.status_code in (401, 403)

    async def test_checkout_trial_abuse_prevention(self, client: AsyncClient, test_user, auth_headers, db, mocker):
        """User who had a prior subscription should not get a trial."""
        old_sub = BillingSubscription(
            id=str(uuid.uuid4()),
            user_id=test_user.id,
            provider="stripe",
            provider_subscription_id="sub_old",
            provider_customer_id="cus_old",
            plan_id="pro",
            price_id="monthly",
            status="canceled",
            current_period_end=datetime.now(timezone.utc) - timedelta(days=30),
        )
        db.add(old_sub)
        await db.commit()

        mock_stripe = mocker.patch(
            "app.payments.service._stripe_request",
            new_callable=AsyncMock,
            return_value={
                "id": "cs_no_trial",
                "url": "https://checkout.stripe.com/pay/cs_no_trial",
                "expires_at": 1700000000,
            },
        )
        mocker.patch("api.v1.payments.settings").STRIPE_SECRET_KEY = "sk_test_123"

        resp = await client.post("/api/v1/payments/checkout", headers=auth_headers, json={
            "plan_id": "pro",
            "price_id": "monthly",
            "success_url": "https://app.jilly.app/success",
            "cancel_url": "https://app.jilly.app/cancel",
        })
        assert resp.status_code == 200
        # Verify trial_period_days was NOT sent
        call_data = mock_stripe.call_args[1].get("data") or mock_stripe.call_args[0][2] if mock_stripe.call_args else {}
        if isinstance(call_data, dict):
            assert "subscription_data[trial_period_days]" not in call_data


# ── POST /portal ─────────────────────────────────────────

class TestPortal:
    """POST /api/v1/payments/portal"""

    async def test_portal_not_configured(self, client: AsyncClient, auth_headers, mocker):
        mocker.patch("api.v1.payments.settings").STRIPE_SECRET_KEY = ""
        resp = await client.post("/api/v1/payments/portal", headers=auth_headers, json={
            "return_url": "https://app.jilly.app/settings",
        })
        assert resp.status_code == 501

    async def test_portal_no_customer(self, client: AsyncClient, test_user, auth_headers, mocker):
        mocker.patch("api.v1.payments.settings").STRIPE_SECRET_KEY = "sk_test_123"
        resp = await client.post("/api/v1/payments/portal", headers=auth_headers, json={
            "return_url": "https://app.jilly.app/settings",
        })
        assert resp.status_code == 400

    async def test_portal_success(self, client: AsyncClient, test_user, auth_headers, db, mocker):
        customer = BillingCustomer(
            id=str(uuid.uuid4()),
            user_id=test_user.id,
            provider="stripe",
            provider_customer_id="cus_test_portal",
        )
        db.add(customer)
        await db.commit()

        mocker.patch("api.v1.payments.settings").STRIPE_SECRET_KEY = "sk_test_123"
        mocker.patch(
            "app.payments.service._stripe_request",
            new_callable=AsyncMock,
            return_value={"url": "https://billing.stripe.com/session/portal_123"},
        )

        resp = await client.post("/api/v1/payments/portal", headers=auth_headers, json={
            "return_url": "https://app.jilly.app/settings",
        })
        assert resp.status_code == 200
        assert "url" in resp.json()


# ── POST /upgrade ────────────────────────────────────────

class TestUpgrade:
    """POST /api/v1/payments/upgrade"""

    async def test_upgrade_not_configured(self, client: AsyncClient, auth_headers, mocker):
        mocker.patch("api.v1.payments.settings").STRIPE_SECRET_KEY = ""
        resp = await client.post("/api/v1/payments/upgrade", headers=auth_headers, json={
            "plan_id": "pro",
            "price_id": "yearly",
        })
        assert resp.status_code == 501

    async def test_upgrade_no_subscription(self, client: AsyncClient, test_user, auth_headers, mocker):
        mocker.patch("api.v1.payments.settings").STRIPE_SECRET_KEY = "sk_test_123"
        resp = await client.post("/api/v1/payments/upgrade", headers=auth_headers, json={
            "plan_id": "pro",
            "price_id": "yearly",
        })
        assert resp.status_code == 400

    async def test_upgrade_success(self, client: AsyncClient, test_user, auth_headers, db, mocker):
        sub = BillingSubscription(
            id=str(uuid.uuid4()),
            user_id=test_user.id,
            provider="stripe",
            provider_subscription_id="sub_upgrade_test",
            provider_customer_id="cus_upgrade_test",
            plan_id="pro",
            price_id="monthly",
            status="active",
            current_period_end=datetime.now(timezone.utc) + timedelta(days=30),
        )
        db.add(sub)
        await db.commit()

        mocker.patch("api.v1.payments.settings").STRIPE_SECRET_KEY = "sk_test_123"
        mock_stripe = mocker.patch(
            "app.payments.service._stripe_request",
            new_callable=AsyncMock,
        )
        # GET subscription → returns item ID
        mock_stripe.side_effect = [
            {"items": {"data": [{"id": "si_item_123"}]}},
            {"id": "sub_upgrade_test", "status": "active"},
        ]

        resp = await client.post("/api/v1/payments/upgrade", headers=auth_headers, json={
            "plan_id": "pro",
            "price_id": "yearly",
        })
        assert resp.status_code == 200


# ── Webhook endpoints ────────────────────────────────────

class TestStripeWebhook:
    """POST /api/webhooks/stripe"""

    async def test_webhook_not_configured(self, client: AsyncClient, mocker):
        mocker.patch("api.v1.payments.settings").STRIPE_WEBHOOK_SECRET = ""
        resp = await client.post("/api/webhooks/stripe", content=b"{}")
        assert resp.status_code == 501

    async def test_webhook_missing_signature(self, client: AsyncClient, mocker):
        mocker.patch("api.v1.payments.settings").STRIPE_WEBHOOK_SECRET = "whsec_test"
        resp = await client.post("/api/webhooks/stripe", content=b"{}")
        assert resp.status_code == 400

    async def test_webhook_invalid_signature(self, client: AsyncClient, mocker):
        mocker.patch("api.v1.payments.settings").STRIPE_WEBHOOK_SECRET = "whsec_test"
        mocker.patch(
            "api.v1.payments.handle_stripe_webhook",
            new_callable=AsyncMock,
            side_effect=ValueError("Invalid Stripe signature"),
        )
        resp = await client.post(
            "/api/webhooks/stripe",
            content=b'{"type":"test"}',
            headers={"stripe-signature": "t=123,v1=bad_sig"},
        )
        assert resp.status_code == 400


class TestRevenueCatWebhook:
    """POST /api/webhooks/revenuecat"""

    async def test_webhook_success(self, client: AsyncClient, mocker):
        mocker.patch(
            "api.v1.payments.handle_revenuecat_webhook",
            new_callable=AsyncMock,
            return_value={"status": "ok"},
        )
        resp = await client.post(
            "/api/webhooks/revenuecat",
            content=b'{"event":{"type":"INITIAL_PURCHASE"}}',
            headers={"authorization": "Bearer rc_test_key"},
        )
        assert resp.status_code == 200

    async def test_webhook_invalid_auth(self, client: AsyncClient, mocker):
        mocker.patch(
            "api.v1.payments.handle_revenuecat_webhook",
            new_callable=AsyncMock,
            side_effect=ValueError("Invalid authorization"),
        )
        resp = await client.post(
            "/api/webhooks/revenuecat",
            content=b'{"event":{}}',
        )
        assert resp.status_code == 400
