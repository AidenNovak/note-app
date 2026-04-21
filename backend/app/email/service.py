"""Email sending via Resend API (direct HTTP, no extra SDK dependency)."""
from __future__ import annotations

import httpx

from app.config import settings
from app.logging_config import logger

_RESEND_API = "https://api.resend.com/emails"


class EmailError(Exception):
    """Raised when email sending fails."""


async def send_email(*, to: str, subject: str, html: str) -> bool:
    """Send an email via Resend. Raises EmailError on failure."""
    if not settings.RESEND_API_KEY:
        raise EmailError("RESEND_API_KEY not configured — cannot send email")

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            _RESEND_API,
            headers={
                "Authorization": f"Bearer {settings.RESEND_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "from": f"{settings.EMAIL_FROM_NAME} <{settings.EMAIL_FROM_ADDRESS}>",
                "to": [to],
                "subject": subject,
                "html": html,
            },
        )
        if resp.status_code in (200, 201):
            logger.info("email_sent", to=to, subject=subject)
            return True
        error_body = resp.text[:200]
        logger.error("email_send_failed", status=resp.status_code, body=error_body)
        raise EmailError(f"Resend API returned {resp.status_code}: {error_body}")


def render_verification_email(*, name: str, code: str, locale: str = "en") -> tuple[str, str]:
    """Return (subject, html) for email verification."""
    if locale.startswith("zh"):
        subject = f"验证你的 Truth Truth 账号"
        html = f"""
        <div style="font-family: Inter, sans-serif; max-width: 480px; margin: 0 auto; padding: 40px 20px;">
            <h1 style="color: #2d5a3d; font-size: 24px;">你好, {name} 👋</h1>
            <p>你的验证码是：</p>
            <div style="background: #f5f5f0; border-radius: 8px; padding: 20px; text-align: center; margin: 24px 0;">
                <span style="font-size: 32px; font-weight: bold; letter-spacing: 8px; color: #2d5a3d;">{code}</span>
            </div>
            <p style="color: #666;">验证码 15 分钟内有效。如果不是你本人操作，请忽略此邮件。</p>
            <hr style="border: none; border-top: 1px solid #eee; margin: 32px 0;">
            <p style="color: #999; font-size: 12px;">Truth Truth · Truth, twice.</p>
        </div>"""
    else:
        subject = f"Verify your Truth Truth account"
        html = f"""
        <div style="font-family: Inter, sans-serif; max-width: 480px; margin: 0 auto; padding: 40px 20px;">
            <h1 style="color: #2d5a3d; font-size: 24px;">Hi {name} 👋</h1>
            <p>Your verification code is:</p>
            <div style="background: #f5f5f0; border-radius: 8px; padding: 20px; text-align: center; margin: 24px 0;">
                <span style="font-size: 32px; font-weight: bold; letter-spacing: 8px; color: #2d5a3d;">{code}</span>
            </div>
            <p style="color: #666;">This code expires in 15 minutes. If you didn't request this, ignore this email.</p>
            <hr style="border: none; border-top: 1px solid #eee; margin: 32px 0;">
            <p style="color: #999; font-size: 12px;">Truth Truth · Truth, twice.</p>
        </div>"""
    return subject, html


def render_password_reset_email(*, name: str, code: str, locale: str = "en") -> tuple[str, str]:
    """Return (subject, html) for password reset."""
    if locale.startswith("zh"):
        subject = "重置你的 Truth Truth 密码"
        html = f"""
        <div style="font-family: Inter, sans-serif; max-width: 480px; margin: 0 auto; padding: 40px 20px;">
            <h1 style="color: #2d5a3d; font-size: 24px;">密码重置</h1>
            <p>你好 {name}，你的密码重置验证码是：</p>
            <div style="background: #f5f5f0; border-radius: 8px; padding: 20px; text-align: center; margin: 24px 0;">
                <span style="font-size: 32px; font-weight: bold; letter-spacing: 8px; color: #2d5a3d;">{code}</span>
            </div>
            <p style="color: #666;">验证码 15 分钟内有效。</p>
            <hr style="border: none; border-top: 1px solid #eee; margin: 32px 0;">
            <p style="color: #999; font-size: 12px;">Truth Truth · Truth, twice.</p>
        </div>"""
    else:
        subject = "Reset your Truth Truth password"
        html = f"""
        <div style="font-family: Inter, sans-serif; max-width: 480px; margin: 0 auto; padding: 40px 20px;">
            <h1 style="color: #2d5a3d; font-size: 24px;">Password Reset</h1>
            <p>Hi {name}, your password reset code is:</p>
            <div style="background: #f5f5f0; border-radius: 8px; padding: 20px; text-align: center; margin: 24px 0;">
                <span style="font-size: 32px; font-weight: bold; letter-spacing: 8px; color: #2d5a3d;">{code}</span>
            </div>
            <p style="color: #666;">This code expires in 15 minutes.</p>
            <hr style="border: none; border-top: 1px solid #eee; margin: 32px 0;">
            <p style="color: #999; font-size: 12px;">Truth Truth · Truth, twice.</p>
        </div>"""
    return subject, html
