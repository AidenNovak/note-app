"""
Payment service — Stripe checkout, portal, upgrade, and webhook handling.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import datetime, timezone

import httpx
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import (
    BillingCustomer,
    BillingSubscription,
    BillingPurchase,
    BillingCheckoutSession,
    BillingEvent,
)
from app.payments.catalog import (
    ACTIVE_SUBSCRIPTION_STATUSES,
    MEMBERSHIP_TIER_RANK,
    find_plan_by_id,
    find_price_by_id,
    find_price_by_provider_price_id,
    resolve_membership_tier,
)
from app.payments.entitlements import get_billing_status


# ── Stripe API helpers ────────────────────────────────────

STRIPE_API = "https://api.stripe.com/v1"


async def _stripe_request(method: str, path: str, data: dict | None = None) -> dict:
    """Low-level Stripe API call using httpx (no SDK dependency)."""
    async with httpx.AsyncClient() as client:
        resp = await client.request(
            method,
            f"{STRIPE_API}{path}",
            data=data,
            auth=(settings.STRIPE_SECRET_KEY, ""),
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()


def verify_stripe_signature(payload: bytes, sig_header: str, secret: str) -> dict:
    """Verify Stripe webhook signature and return parsed event."""
    parts = dict(item.split("=", 1) for item in sig_header.split(",") if "=" in item)
    timestamp = parts.get("t", "")
    v1_sig = parts.get("v1", "")

    signed_payload = f"{timestamp}.{payload.decode()}"
    expected = hmac.new(secret.encode(), signed_payload.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(expected, v1_sig):
        raise ValueError("Invalid Stripe signature")

    return json.loads(payload)


# ── Checkout ──────────────────────────────────────────────

async def create_checkout_session(
    db: AsyncSession,
    *,
    user_id: str,
    user_email: str | None,
    plan_id: str,
    price_id: str,
    success_url: str,
    cancel_url: str,
) -> dict:
    """Create a Stripe checkout session."""
    plan = find_plan_by_id(plan_id)
    price = find_price_by_id(price_id)
    if not plan or not price or plan.status != "active" or price.status != "active":
        raise ValueError("Plan or price not available")

    # Check existing customer
    result = await db.execute(
        select(BillingCustomer).where(
            and_(BillingCustomer.user_id == user_id, BillingCustomer.provider == "stripe")
        )
    )
    existing_customer = result.scalar_one_or_none()

    # Trial abuse prevention
    trial_days = price.trial_days
    if trial_days and price.price_type == "subscription":
        sub_result = await db.execute(
            select(BillingSubscription).where(
                and_(BillingSubscription.user_id == user_id, BillingSubscription.provider == "stripe")
            ).limit(1)
        )
        if sub_result.scalar_one_or_none():
            trial_days = None  # Already had a subscription, no trial

    mode = "subscription" if price.price_type == "subscription" else "payment"
    data: dict = {
        "mode": mode,
        "line_items[0][price]": price.provider_price_id,
        "line_items[0][quantity]": "1",
        "success_url": success_url,
        "cancel_url": cancel_url,
        "metadata[userId]": user_id,
        "metadata[planId]": plan_id,
        "metadata[priceId]": price_id,
    }
    if existing_customer:
        data["customer"] = existing_customer.provider_customer_id
    elif user_email:
        data["customer_email"] = user_email
    if trial_days and mode == "subscription":
        data["subscription_data[trial_period_days]"] = str(trial_days)

    session = await _stripe_request("POST", "/checkout/sessions", data)

    # Record checkout session
    db.add(BillingCheckoutSession(
        id=str(uuid.uuid4()),
        user_id=user_id,
        provider="stripe",
        provider_session_id=session["id"],
        plan_id=plan_id,
        price_id=price_id,
        mode=mode,
        status="created",
        expires_at=datetime.fromtimestamp(session.get("expires_at", 0), tz=timezone.utc) if session.get("expires_at") else None,
    ))
    await db.commit()

    return {"url": session.get("url")}


# ── Portal ────────────────────────────────────────────────

async def create_portal_session(db: AsyncSession, *, user_id: str, return_url: str) -> dict:
    """Create a Stripe billing portal session."""
    result = await db.execute(
        select(BillingCustomer).where(
            and_(BillingCustomer.user_id == user_id, BillingCustomer.provider == "stripe")
        )
    )
    customer = result.scalar_one_or_none()
    if not customer:
        raise ValueError("No Stripe customer found")

    session = await _stripe_request("POST", "/billing_portal/sessions", {
        "customer": customer.provider_customer_id,
        "return_url": return_url,
    })
    return {"url": session.get("url")}


# ── Subscription Upgrade ──────────────────────────────────

async def upgrade_subscription(
    db: AsyncSession,
    *,
    user_id: str,
    plan_id: str,
    price_id: str,
) -> dict:
    """Upgrade an existing Stripe subscription to a higher tier."""
    price = find_price_by_id(price_id)
    if not price or price.price_type != "subscription":
        raise ValueError("Invalid price for upgrade")

    result = await db.execute(
        select(BillingSubscription).where(
            and_(
                BillingSubscription.user_id == user_id,
                BillingSubscription.provider == "stripe",
                BillingSubscription.status.in_(list(ACTIVE_SUBSCRIPTION_STATUSES)),
            )
        ).limit(1)
    )
    sub = result.scalar_one_or_none()
    if not sub:
        raise ValueError("No active subscription found")

    # Retrieve Stripe subscription to get item ID
    stripe_sub = await _stripe_request("GET", f"/subscriptions/{sub.provider_subscription_id}")
    item_id = stripe_sub["items"]["data"][0]["id"]

    await _stripe_request("POST", f"/subscriptions/{sub.provider_subscription_id}", {
        f"items[0][id]": item_id,
        f"items[0][price]": price.provider_price_id,
        "proration_behavior": "always_invoice",
        "payment_behavior": "error_if_incomplete",
        "cancel_at_period_end": "false",
    })

    sub.plan_id = plan_id
    sub.price_id = price_id
    sub.cancel_at_period_end = False
    sub.updated_at = datetime.now(timezone.utc)
    await db.commit()

    return {"status": "upgraded"}


# ── Webhook: Idempotent processing ───────────────────────

async def handle_stripe_webhook(db: AsyncSession, *, raw_body: bytes, signature: str) -> dict:
    """Process a Stripe webhook event with idempotency."""
    event = verify_stripe_signature(raw_body, signature, settings.STRIPE_WEBHOOK_SECRET)
    event_id = event.get("id", "")
    event_type = event.get("type", "")

    # Idempotency check
    existing = await db.execute(
        select(BillingEvent).where(
            and_(BillingEvent.provider == "stripe", BillingEvent.provider_event_id == event_id)
        )
    )
    if existing.scalar_one_or_none():
        return {"received": True, "duplicate": True}

    db.add(BillingEvent(
        id=str(uuid.uuid4()),
        provider="stripe",
        provider_event_id=event_id,
        event_type=event_type,
        processed_at=datetime.now(timezone.utc),
        payload_json=json.dumps(event),
    ))

    obj = event.get("data", {}).get("object", {})
    metadata = obj.get("metadata", {})

    if event_type == "checkout.session.completed":
        await _handle_checkout_completed(db, obj, metadata)
    elif event_type.startswith("customer.subscription."):
        await _handle_subscription_event(db, obj, metadata)
    elif event_type == "payment_intent.succeeded":
        await _handle_payment_intent(db, obj, metadata, status="succeeded")
    elif event_type == "payment_intent.payment_failed":
        await _handle_payment_intent(db, obj, metadata, status="failed")
    elif event_type == "charge.refunded":
        await _handle_refund(db, obj)

    await db.commit()
    return {"received": True, "duplicate": False}


# ── Webhook: RevenueCat ──────────────────────────────────

async def handle_revenuecat_webhook(db: AsyncSession, *, raw_body: bytes, authorization: str | None) -> dict:
    """Process a RevenueCat webhook event."""
    if settings.REVENUECAT_WEBHOOK_AUTHORIZATION:
        if authorization != settings.REVENUECAT_WEBHOOK_AUTHORIZATION:
            raise ValueError("Invalid RevenueCat authorization")

    payload = json.loads(raw_body)
    rc_event = payload.get("event", {})
    if not rc_event:
        return {"received": True}

    event_id = rc_event.get("id", "")
    event_type = rc_event.get("type", "")

    # Idempotency check
    existing = await db.execute(
        select(BillingEvent).where(
            and_(BillingEvent.provider == "revenuecat", BillingEvent.provider_event_id == event_id)
        )
    )
    if existing.scalar_one_or_none():
        return {"received": True, "duplicate": True}

    db.add(BillingEvent(
        id=str(uuid.uuid4()),
        provider="revenuecat",
        provider_event_id=event_id,
        event_type=event_type,
        processed_at=datetime.now(timezone.utc),
        payload_json=json.dumps(payload),
    ))

    # Resolve user ID (non-anonymous)
    app_user_id = rc_event.get("app_user_id", "")
    if app_user_id.startswith("$RCAnonymousID:"):
        await db.commit()
        return {"received": True}

    product_id = rc_event.get("product_id", "")
    mapped_price = find_price_by_provider_price_id("revenuecat", product_id)
    if not mapped_price:
        await db.commit()
        return {"received": True}

    # Upsert customer
    await _upsert_billing_customer(db, user_id=app_user_id, provider="revenuecat",
                                    provider_customer_id=app_user_id)

    if mapped_price.price_type == "lifetime":
        await _upsert_revenuecat_purchase(db, rc_event, app_user_id, mapped_price)
    else:
        await _upsert_revenuecat_subscription(db, rc_event, app_user_id, mapped_price)

    await db.commit()
    return {"received": True, "duplicate": False}


# ── Internal handlers ─────────────────────────────────────

async def _handle_checkout_completed(db: AsyncSession, session: dict, metadata: dict):
    user_id = metadata.get("userId") or await _resolve_user_from_customer(db, session.get("customer"))
    if not user_id:
        return

    plan_id = metadata.get("planId", "")
    price_id = metadata.get("priceId", "")
    customer_id = session.get("customer")
    now = datetime.now(timezone.utc)

    if customer_id:
        await _upsert_billing_customer(
            db, user_id=user_id, provider="stripe",
            provider_customer_id=customer_id,
            email=session.get("customer_details", {}).get("email"),
        )

    # Update checkout session record
    result = await db.execute(
        select(BillingCheckoutSession).where(
            and_(
                BillingCheckoutSession.provider == "stripe",
                BillingCheckoutSession.provider_session_id == session.get("id"),
            )
        )
    )
    checkout = result.scalar_one_or_none()
    if checkout:
        checkout.status = "completed"
        checkout.updated_at = now

    mode = session.get("mode")
    if mode == "subscription":
        sub_id = session.get("subscription")
        if isinstance(sub_id, dict):
            sub_id = sub_id.get("id")
        if sub_id and plan_id and price_id:
            await _upsert_subscription(
                db, user_id=user_id, provider="stripe",
                provider_subscription_id=sub_id,
                provider_customer_id=customer_id or "",
                plan_id=plan_id, price_id=price_id,
                status="incomplete",
            )

    elif mode == "payment":
        pi_id = session.get("payment_intent")
        if isinstance(pi_id, dict):
            pi_id = pi_id.get("id")
        paid = session.get("payment_status") in ("paid", "no_payment_required")
        resolved_pi = pi_id or f"checkout_session:{session.get('id')}"
        if plan_id and price_id:
            await _upsert_purchase(
                db, user_id=user_id, provider="stripe",
                provider_payment_intent_id=resolved_pi,
                plan_id=plan_id, price_id=price_id,
                status="succeeded" if paid else "pending",
                paid=paid,
            )
            if paid:
                await _cancel_lower_tier_subscriptions(db, user_id, price_id)


async def _handle_subscription_event(db: AsyncSession, subscription: dict, metadata: dict):
    customer_id = subscription.get("customer")
    if isinstance(customer_id, dict):
        customer_id = customer_id.get("id")

    user_id = metadata.get("userId") or await _resolve_user_from_customer(db, customer_id)
    if not user_id:
        return

    # Map Stripe price to internal price
    items = subscription.get("items", {}).get("data", [])
    stripe_price_id = items[0]["price"]["id"] if items else None
    mapped = find_price_by_provider_price_id("stripe", stripe_price_id) if stripe_price_id else None

    plan_id = mapped.plan_id if mapped else metadata.get("planId", "")
    price_id = mapped.id if mapped else metadata.get("priceId", "")
    if not plan_id or not price_id:
        return

    if customer_id:
        await _upsert_billing_customer(db, user_id=user_id, provider="stripe",
                                        provider_customer_id=customer_id)

    status = _map_stripe_sub_status(subscription.get("status", "incomplete"))
    period_end = items[0].get("current_period_end") if items else None

    await _upsert_subscription(
        db, user_id=user_id, provider="stripe",
        provider_subscription_id=subscription["id"],
        provider_customer_id=customer_id or "",
        plan_id=plan_id, price_id=price_id,
        status=status,
        current_period_end=datetime.fromtimestamp(period_end, tz=timezone.utc) if period_end else None,
        cancel_at_period_end=subscription.get("cancel_at_period_end", False),
        started_at=datetime.fromtimestamp(subscription["start_date"], tz=timezone.utc) if subscription.get("start_date") else None,
        ended_at=datetime.fromtimestamp(subscription["ended_at"], tz=timezone.utc) if subscription.get("ended_at") else None,
    )


async def _handle_payment_intent(db: AsyncSession, pi: dict, metadata: dict, status: str):
    user_id = metadata.get("userId") or await _resolve_user_from_customer(db, pi.get("customer"))
    plan_id = metadata.get("planId")
    price_id = metadata.get("priceId")
    if not user_id or not plan_id or not price_id:
        return

    await _upsert_purchase(
        db, user_id=user_id, provider="stripe",
        provider_payment_intent_id=pi["id"],
        plan_id=plan_id, price_id=price_id,
        status=status,
        paid=(status == "succeeded"),
    )
    if status == "succeeded":
        await _cancel_lower_tier_subscriptions(db, user_id, price_id)


async def _handle_refund(db: AsyncSession, charge: dict):
    pi_id = charge.get("payment_intent")
    if not pi_id:
        return
    result = await db.execute(
        select(BillingPurchase).where(
            and_(BillingPurchase.provider == "stripe",
                 BillingPurchase.provider_payment_intent_id == pi_id)
        )
    )
    purchase = result.scalar_one_or_none()
    if purchase:
        purchase.status = "refunded"
        purchase.updated_at = datetime.now(timezone.utc)


async def _upsert_revenuecat_subscription(db: AsyncSession, event: dict, user_id: str, price):
    from app.payments.catalog import Price
    now = datetime.now(timezone.utc)
    original_txn = event.get("original_transaction_id") or event.get("transaction_id", "")
    if not original_txn:
        return

    event_ts = event.get("event_timestamp_ms", 0)
    provider_event_at = datetime.fromtimestamp(event_ts / 1000, tz=timezone.utc) if event_ts else now

    # Check ordering
    result = await db.execute(
        select(BillingSubscription).where(
            and_(
                BillingSubscription.provider == "revenuecat",
                BillingSubscription.provider_subscription_id == original_txn,
            )
        )
    )
    existing = result.scalar_one_or_none()
    if existing and existing.provider_event_at and event_ts:
        if event_ts <= int(existing.provider_event_at.timestamp() * 1000):
            return  # Old event

    exp_ms = event.get("expiration_at_ms")
    expiration_at = datetime.fromtimestamp(exp_ms / 1000, tz=timezone.utc) if exp_ms else None
    is_expired = expiration_at and expiration_at <= now

    status = "active"
    if event.get("type") == "EXPIRATION" or is_expired:
        status = "canceled"
    elif event.get("period_type") == "TRIAL":
        status = "trialing"

    purchased_ms = event.get("purchased_at_ms")
    started_at = datetime.fromtimestamp(purchased_ms / 1000, tz=timezone.utc) if purchased_ms else None

    if existing:
        existing.user_id = user_id
        existing.plan_id = price.plan_id
        existing.price_id = price.id
        existing.status = status
        existing.current_period_end = expiration_at
        existing.cancel_at_period_end = (event.get("type") == "CANCELLATION")
        existing.started_at = started_at
        existing.ended_at = expiration_at if (event.get("type") == "EXPIRATION" and expiration_at) else None
        existing.provider_event_at = provider_event_at
        existing.updated_at = now
    else:
        db.add(BillingSubscription(
            id=str(uuid.uuid4()),
            user_id=user_id,
            provider="revenuecat",
            provider_subscription_id=original_txn,
            provider_customer_id=user_id,
            plan_id=price.plan_id,
            price_id=price.id,
            status=status,
            current_period_end=expiration_at,
            cancel_at_period_end=(event.get("type") == "CANCELLATION"),
            started_at=started_at,
            ended_at=expiration_at if (event.get("type") == "EXPIRATION" and expiration_at) else None,
            provider_event_at=provider_event_at,
        ))


async def _upsert_revenuecat_purchase(db: AsyncSession, event: dict, user_id: str, price):
    now = datetime.now(timezone.utc)
    txn_id = event.get("transaction_id") or event.get("original_transaction_id", "")
    if not txn_id:
        return

    result = await db.execute(
        select(BillingPurchase).where(
            and_(
                BillingPurchase.provider == "revenuecat",
                BillingPurchase.provider_payment_intent_id == txn_id,
            )
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        existing.status = "succeeded"
        existing.paid_at = now
        existing.updated_at = now
    else:
        db.add(BillingPurchase(
            id=str(uuid.uuid4()),
            user_id=user_id,
            provider="revenuecat",
            provider_payment_intent_id=txn_id,
            plan_id=price.plan_id,
            price_id=price.id,
            status="succeeded",
            paid_at=now,
        ))


# ── Shared DB helpers ─────────────────────────────────────

async def _upsert_billing_customer(db: AsyncSession, *, user_id: str, provider: str,
                                    provider_customer_id: str, email: str | None = None):
    result = await db.execute(
        select(BillingCustomer).where(
            and_(BillingCustomer.user_id == user_id, BillingCustomer.provider == provider)
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        existing.provider_customer_id = provider_customer_id
        if email:
            existing.email = email
        existing.updated_at = datetime.now(timezone.utc)
    else:
        db.add(BillingCustomer(
            id=str(uuid.uuid4()),
            user_id=user_id,
            provider=provider,
            provider_customer_id=provider_customer_id,
            email=email,
        ))


async def _upsert_subscription(db: AsyncSession, *, user_id: str, provider: str,
                                provider_subscription_id: str, provider_customer_id: str,
                                plan_id: str, price_id: str, status: str, **kwargs):
    now = datetime.now(timezone.utc)
    result = await db.execute(
        select(BillingSubscription).where(
            and_(
                BillingSubscription.provider == provider,
                BillingSubscription.provider_subscription_id == provider_subscription_id,
            )
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        existing.user_id = user_id
        existing.plan_id = plan_id
        existing.price_id = price_id
        existing.status = status
        existing.updated_at = now
        for k, v in kwargs.items():
            if v is not None:
                setattr(existing, k, v)
    else:
        db.add(BillingSubscription(
            id=str(uuid.uuid4()),
            user_id=user_id,
            provider=provider,
            provider_subscription_id=provider_subscription_id,
            provider_customer_id=provider_customer_id,
            plan_id=plan_id,
            price_id=price_id,
            status=status,
            **{k: v for k, v in kwargs.items() if v is not None},
        ))


async def _upsert_purchase(db: AsyncSession, *, user_id: str, provider: str,
                            provider_payment_intent_id: str, plan_id: str,
                            price_id: str, status: str, paid: bool):
    now = datetime.now(timezone.utc)
    result = await db.execute(
        select(BillingPurchase).where(
            and_(
                BillingPurchase.provider == provider,
                BillingPurchase.provider_payment_intent_id == provider_payment_intent_id,
            )
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        existing.status = status
        if paid and not existing.paid_at:
            existing.paid_at = now
        existing.updated_at = now
    else:
        db.add(BillingPurchase(
            id=str(uuid.uuid4()),
            user_id=user_id,
            provider=provider,
            provider_payment_intent_id=provider_payment_intent_id,
            plan_id=plan_id,
            price_id=price_id,
            status=status,
            paid_at=now if paid else None,
        ))


async def _resolve_user_from_customer(db: AsyncSession, customer_id: str | None) -> str | None:
    if not customer_id:
        return None
    result = await db.execute(
        select(BillingCustomer.user_id).where(
            and_(BillingCustomer.provider == "stripe",
                 BillingCustomer.provider_customer_id == customer_id)
        )
    )
    row = result.first()
    return row[0] if row else None


async def _cancel_lower_tier_subscriptions(db: AsyncSession, user_id: str, price_id: str):
    """Auto-cancel lower-tier Stripe subscriptions when a higher tier activates."""
    activated_price = find_price_by_id(price_id)
    if not activated_price:
        return
    activated_tier = resolve_membership_tier(activated_price.price_type, activated_price.interval)
    activated_rank = MEMBERSHIP_TIER_RANK[activated_tier]
    if activated_rank <= MEMBERSHIP_TIER_RANK["monthly"]:
        return

    cancellable = {"active", "trialing", "past_due", "unpaid"}
    result = await db.execute(
        select(BillingSubscription).where(
            and_(
                BillingSubscription.user_id == user_id,
                BillingSubscription.provider == "stripe",
            )
        )
    )
    subs = result.scalars().all()
    for sub in subs:
        if sub.status not in cancellable or sub.cancel_at_period_end:
            continue
        sub_price = find_price_by_id(sub.price_id)
        if not sub_price:
            continue
        sub_tier = resolve_membership_tier(sub_price.price_type, sub_price.interval)
        if MEMBERSHIP_TIER_RANK[sub_tier] < activated_rank:
            try:
                await _stripe_request("POST", f"/subscriptions/{sub.provider_subscription_id}", {
                    "cancel_at_period_end": "true",
                })
                sub.cancel_at_period_end = True
                sub.updated_at = datetime.now(timezone.utc)
            except Exception:
                pass  # Best-effort cancellation


def _map_stripe_sub_status(status: str) -> str:
    if status in ("active", "trialing", "past_due", "canceled", "unpaid", "incomplete"):
        return status
    if status == "incomplete_expired":
        return "canceled"
    return "incomplete"
