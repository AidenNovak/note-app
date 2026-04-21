"""
Plan & price catalog — the single source of truth for all billing plans.
Mirrors the config from easystarter/packages/app-config/src/app-config.ts
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

PriceType = Literal["subscription", "lifetime"]
BillingInterval = Literal["month", "year"]
MembershipTier = Literal["free", "monthly", "yearly", "lifetime"]
EntitlementSource = Literal["none", "subscription", "lifetime"]
Provider = Literal["stripe", "revenuecat"]

MEMBERSHIP_TIER_RANK: dict[MembershipTier, int] = {
    "free": 0,
    "monthly": 1,
    "yearly": 2,
    "lifetime": 3,
}

ACTIVE_SUBSCRIPTION_STATUSES = {"active", "trialing"}


@dataclass(frozen=True)
class Price:
    id: str
    plan_id: str
    provider: Provider
    provider_price_id: str
    currency: str
    amount_cents: int
    price_type: PriceType
    interval: BillingInterval | None = None
    trial_days: int | None = None
    status: str = "active"


@dataclass(frozen=True)
class Plan:
    id: str
    status: str = "active"
    prices: tuple[Price, ...] = ()


# ── Stripe (Web) Plans ────────────────────────────────────

PLANS: list[Plan] = [
    Plan(id="free"),
    Plan(
        id="pro",
        prices=(
            Price(
                id="monthly",
                plan_id="pro",
                provider="stripe",
                provider_price_id="price_1SwIdZ4uQgMehpGvGlktz1NL",
                currency="usd",
                amount_cents=1000,
                price_type="subscription",
                interval="month",
                trial_days=7,
            ),
            Price(
                id="yearly",
                plan_id="pro",
                provider="stripe",
                provider_price_id="price_1SwIg44uQgMehpGvlW6FVytH",
                currency="usd",
                amount_cents=10000,
                price_type="subscription",
                interval="year",
                trial_days=7,
            ),
        ),
    ),
    Plan(
        id="lifetime",
        prices=(
            Price(
                id="lifetime",
                plan_id="lifetime",
                provider="stripe",
                provider_price_id="price_1SwIgs4uQgMehpGvFYBteVsk",
                currency="usd",
                amount_cents=200000,
                price_type="lifetime",
            ),
        ),
    ),
]

# ── Native (RevenueCat) Plans ─────────────────────────────

NATIVE_IOS_PLANS: list[Plan] = [
    Plan(
        id="pro",
        prices=(
            Price(id="monthly", plan_id="pro", provider="revenuecat",
                  provider_price_id="atelier_pro_monthly",
                  currency="usd", amount_cents=999, price_type="subscription", interval="month"),
            Price(id="yearly", plan_id="pro", provider="revenuecat",
                  provider_price_id="atelier_pro_yearly",
                  currency="usd", amount_cents=9999, price_type="subscription", interval="year"),
        ),
    ),
    Plan(
        id="lifetime",
        prices=(
            Price(id="lifetime", plan_id="lifetime", provider="revenuecat",
                  provider_price_id="atelier_lifetime",
                  currency="usd", amount_cents=29900, price_type="lifetime"),
        ),
    ),
]

NATIVE_ANDROID_PLANS: list[Plan] = [
    Plan(
        id="pro",
        prices=(
            Price(id="monthly", plan_id="pro", provider="revenuecat",
                  provider_price_id="atelier_pro_monthly",
                  currency="usd", amount_cents=999, price_type="subscription", interval="month"),
            Price(id="yearly", plan_id="pro", provider="revenuecat",
                  provider_price_id="atelier_pro_yearly",
                  currency="usd", amount_cents=9999, price_type="subscription", interval="year"),
        ),
    ),
    Plan(
        id="lifetime",
        prices=(
            Price(id="lifetime", plan_id="lifetime", provider="revenuecat",
                  provider_price_id="atelier_lifetime",
                  currency="usd", amount_cents=29900, price_type="lifetime"),
        ),
    ),
]

# ── Index helpers ──────────────────────────────────────────

_ALL_PLANS = PLANS + NATIVE_IOS_PLANS + NATIVE_ANDROID_PLANS
_PRICE_INDEX: dict[str, Price] = {}
_PROVIDER_PRICE_INDEX: dict[tuple[str, str], Price] = {}
_PLAN_INDEX: dict[str, Plan] = {}

for _plan in _ALL_PLANS:
    _PLAN_INDEX[_plan.id] = _plan
    for _price in _plan.prices:
        _PRICE_INDEX[_price.id] = _price
        _PROVIDER_PRICE_INDEX[(_price.provider, _price.provider_price_id)] = _price


def find_plan_by_id(plan_id: str) -> Plan | None:
    return _PLAN_INDEX.get(plan_id)


def find_price_by_id(price_id: str) -> Price | None:
    return _PRICE_INDEX.get(price_id)


def find_price_by_provider_price_id(provider: str, provider_price_id: str) -> Price | None:
    return _PROVIDER_PRICE_INDEX.get((provider, provider_price_id))


def list_active_plans() -> list[dict]:
    """Return plans with active prices for client presentation."""
    result = []
    for plan in PLANS:
        if plan.status != "active":
            continue
        prices = [p for p in plan.prices if p.status == "active"]
        result.append({
            "id": plan.id,
            "prices": [
                {
                    "id": p.id,
                    "provider": p.provider,
                    "currency": p.currency,
                    "amount_cents": p.amount_cents,
                    "price_type": p.price_type,
                    "interval": p.interval,
                    "trial_days": p.trial_days,
                }
                for p in prices
            ],
        })
    return result


def resolve_membership_tier(price_type: PriceType, interval: BillingInterval | None) -> MembershipTier:
    if price_type == "lifetime":
        return "lifetime"
    if interval == "year":
        return "yearly"
    if interval == "month":
        return "monthly"
    return "free"
