"""
Payment API routes — Stripe checkout/portal/upgrade + webhook endpoints.
"""

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models import User
from app.auth.utils import get_current_user
from app.payments.catalog import list_active_plans
from app.payments.entitlements import get_billing_status
from app.payments.service import (
    create_checkout_session,
    create_portal_session,
    upgrade_subscription,
    handle_stripe_webhook,
    handle_revenuecat_webhook,
)

router = APIRouter(prefix="/payments", tags=["payments"])


@router.get("/plans")
async def get_plans():
    """List all available plans with active prices."""
    return {"plans": list_active_plans()}


@router.get("/status")
async def billing_status(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get the current user's billing status and entitlement."""
    status = await get_billing_status(db, current_user.id)
    return {
        "user_id": status.user_id,
        "billing_provider": status.billing_provider,
        "can_manage_billing": status.can_manage_billing,
        "current_entitlement": {
            "tier": status.current_entitlement.tier,
            "source": status.current_entitlement.source,
        },
        "has_active_subscription": status.has_active_subscription,
        "active_plan_id": status.active_plan_id,
        "active_price_id": status.active_price_id,
    }


@router.post("/checkout")
async def checkout(
    plan_id: str = Body(...),
    price_id: str = Body(...),
    success_url: str = Body(...),
    cancel_url: str = Body(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a Stripe checkout session."""
    if not settings.STRIPE_SECRET_KEY:
        raise HTTPException(status_code=501, detail="Stripe not configured")
    try:
        result = await create_checkout_session(
            db, user_id=current_user.id, user_email=current_user.email,
            plan_id=plan_id, price_id=price_id,
            success_url=success_url, cancel_url=cancel_url,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/portal")
async def portal(
    return_url: str = Body(..., embed=True),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a Stripe billing portal session."""
    if not settings.STRIPE_SECRET_KEY:
        raise HTTPException(status_code=501, detail="Stripe not configured")
    try:
        result = await create_portal_session(db, user_id=current_user.id, return_url=return_url)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/upgrade")
async def upgrade(
    plan_id: str = Body(...),
    price_id: str = Body(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Upgrade an existing subscription."""
    if not settings.STRIPE_SECRET_KEY:
        raise HTTPException(status_code=501, detail="Stripe not configured")
    try:
        result = await upgrade_subscription(
            db, user_id=current_user.id, plan_id=plan_id, price_id=price_id,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── Webhooks (no auth, signature-verified) ────────────────

webhook_router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@webhook_router.post("/stripe")
async def stripe_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """Handle Stripe webhook events with idempotent processing."""
    if not settings.STRIPE_WEBHOOK_SECRET:
        raise HTTPException(status_code=501, detail="Stripe webhooks not configured")

    raw_body = await request.body()
    signature = request.headers.get("stripe-signature", "")
    if not signature:
        raise HTTPException(status_code=400, detail="Missing stripe-signature header")

    try:
        result = await handle_stripe_webhook(db, raw_body=raw_body, signature=signature)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@webhook_router.post("/revenuecat")
async def revenuecat_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """Handle RevenueCat webhook events."""
    raw_body = await request.body()
    authorization = request.headers.get("authorization")

    try:
        result = await handle_revenuecat_webhook(db, raw_body=raw_body, authorization=authorization)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
