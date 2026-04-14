"""
Entitlement resolution — determines a user's current tier from their billing records.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BillingSubscription, BillingPurchase
from app.payments.catalog import (
    ACTIVE_SUBSCRIPTION_STATUSES,
    MEMBERSHIP_TIER_RANK,
    MembershipTier,
    EntitlementSource,
    find_price_by_id,
    resolve_membership_tier,
)


@dataclass
class CurrentEntitlement:
    tier: MembershipTier
    source: EntitlementSource
    plan_id: str | None
    price_id: str | None


@dataclass
class BillingStatus:
    user_id: str
    billing_provider: str | None
    can_manage_billing: bool
    current_entitlement: CurrentEntitlement
    has_active_subscription: bool
    active_plan_id: str | None
    active_price_id: str | None


def _resolve_entitlement(
    subscriptions: list,
    purchases: list,
) -> CurrentEntitlement:
    """Pick the highest-tier active entitlement."""
    best: CurrentEntitlement | None = None

    for sub in subscriptions:
        if sub.status not in ACTIVE_SUBSCRIPTION_STATUSES:
            continue
        price = find_price_by_id(sub.price_id)
        if not price:
            continue
        tier = resolve_membership_tier(price.price_type, price.interval)
        candidate = CurrentEntitlement(tier=tier, source="subscription", plan_id=sub.plan_id, price_id=sub.price_id)
        if best is None or MEMBERSHIP_TIER_RANK[candidate.tier] > MEMBERSHIP_TIER_RANK[best.tier]:
            best = candidate

    for purchase in purchases:
        if purchase.status != "succeeded":
            continue
        price = find_price_by_id(purchase.price_id)
        if not price or price.price_type != "lifetime":
            continue
        candidate = CurrentEntitlement(tier="lifetime", source="lifetime", plan_id=purchase.plan_id, price_id=purchase.price_id)
        if best is None or MEMBERSHIP_TIER_RANK[candidate.tier] > MEMBERSHIP_TIER_RANK[best.tier]:
            best = candidate

    return best or CurrentEntitlement(tier="free", source="none", plan_id=None, price_id=None)


async def get_billing_status(db: AsyncSession, user_id: str) -> BillingStatus:
    """Compute full billing status for a user."""
    sub_result = await db.execute(
        select(BillingSubscription)
        .where(BillingSubscription.user_id == user_id)
        .order_by(BillingSubscription.updated_at.desc())
    )
    subscriptions = list(sub_result.scalars().all())

    purchase_result = await db.execute(
        select(BillingPurchase)
        .where(and_(BillingPurchase.user_id == user_id, BillingPurchase.status == "succeeded"))
        .order_by(BillingPurchase.updated_at.desc())
    )
    purchases = list(purchase_result.scalars().all())

    entitlement = _resolve_entitlement(subscriptions, purchases)
    has_active = any(s.status in ACTIVE_SUBSCRIPTION_STATUSES for s in subscriptions)

    billing_provider: str | None = None
    if entitlement.source == "subscription":
        for s in subscriptions:
            if s.plan_id == entitlement.plan_id and s.price_id == entitlement.price_id:
                billing_provider = s.provider
                break
    elif entitlement.source == "lifetime":
        for p in purchases:
            if p.plan_id == entitlement.plan_id and p.price_id == entitlement.price_id:
                billing_provider = p.provider
                break

    return BillingStatus(
        user_id=user_id,
        billing_provider=billing_provider,
        can_manage_billing=(entitlement.source == "subscription" and billing_provider == "stripe"),
        current_entitlement=entitlement,
        has_active_subscription=has_active,
        active_plan_id=entitlement.plan_id,
        active_price_id=entitlement.price_id,
    )
