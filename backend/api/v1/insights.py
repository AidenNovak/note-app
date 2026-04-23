from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.utils import get_current_user
from app.database import async_session, get_db
from app.intelligence.insights.share_cards import render_share_card_png
from app.models import InsightGeneration, TaskStatus, User
from app.schemas import InsightDetailOut, InsightGenerationOut, InsightOut, StatusResponse
from app.intelligence.insights.service import (
    _check_db_events_available,
    build_terminal_event,
    build_report_detail,
    broadcast_log,
    clear_generation_buffers,
    create_generation,
    get_latest_generation,
    get_report,
    list_reports,
    serialize_generation,
    serialize_report,
    subscribe_to_generation,
    unsubscribe_from_generation,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/insights", tags=["insights"])
STREAM_STATUS_POLL_INTERVAL_SECONDS = 2.0


@router.get("/generations/{generation_id}/stream")
async def stream_generation_logs(
    generation_id: str,
    last_sequence: int = Query(0, ge=0, description="Resume streaming from this sequence number"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Stream real-time logs of a specific insight generation.

    Supports resumable streaming via ``last_sequence`` — if the client
    disconnects and reconnects, pass the last received sequence to resume
    without missing events.
    """
    # Verify generation belongs to user
    result = await db.execute(
        select(InsightGeneration).where(
            InsightGeneration.id == generation_id,
            InsightGeneration.user_id == current_user.id,
        )
    )
    generation = result.scalar_one_or_none()
    if generation is None:
        raise HTTPException(status_code=404, detail={"error": {"code": "GENERATION_NOT_FOUND", "message": "Generation not found"}})

    async def event_generator():
        terminal_event = build_terminal_event(generation)
        if terminal_event is not None:
            yield f"data: {json.dumps(terminal_event)}\n\n"
            return

        queue = await subscribe_to_generation(generation_id, db=db, after_sequence=last_sequence)
        db_sequence = last_sequence  # tracks last delivered sequence from insight_events table
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=STREAM_STATUS_POLL_INTERVAL_SECONDS)
                    yield f"data: {json.dumps(event)}\n\n"
                    if event.get("type") in ("completed", "error"):
                        break
                except asyncio.TimeoutError:
                    # ── Poll insight_events for Worker-written lifecycle events ──
                    if await _check_db_events_available():
                        try:
                            from app.intelligence.insights.event_store import get_events
                            async with async_session() as inner_db:
                                new_events = await get_events(inner_db, generation_id, after_sequence=db_sequence)
                            found_terminal = False
                            for ev in new_events:
                                try:
                                    payload = json.loads(ev.payload_json)
                                except json.JSONDecodeError:
                                    payload = {"type": ev.event_type}
                                db_sequence = ev.sequence
                                yield f"data: {json.dumps(payload)}\n\n"
                                if payload.get("type") in ("completed", "error"):
                                    found_terminal = True
                                    break
                            if new_events:
                                if found_terminal:
                                    return
                                continue  # got events, loop without generation status check
                        except Exception as exc:
                            logger.debug("DB event poll failed: %s", exc)

                    # ── Fallback: check generation status ──
                    # Use a fresh DB session because the injected session may be closed
                    # while the streaming response is active.
                    async with async_session() as inner_db:
                        result = await inner_db.execute(
                            select(InsightGeneration).where(
                                InsightGeneration.id == generation_id,
                                InsightGeneration.user_id == current_user.id,
                            )
                        )
                        refreshed = result.scalar_one_or_none()
                        if refreshed is None:
                            continue
                        term = build_terminal_event(refreshed)
                        if term is None:
                            continue
                    yield f"data: {json.dumps(term)}\n\n"
                    break
        finally:
            unsubscribe_from_generation(generation_id, queue)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.get("", response_model=list[InsightOut])
async def get_insights(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    reports = await list_reports(db, current_user.id)
    return [serialize_report(report) for report in reports]


@router.get("/generations/latest", response_model=Optional[InsightGenerationOut])
async def get_latest_insight_generation(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    generation = await get_latest_generation(db, current_user.id)
    if generation is None:
        return None
    return serialize_generation(generation)


@router.get("/{insight_id}/share-card.png")
async def download_insight_share_card(
    insight_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user),
):
    """Render share card as PNG.

    Accepts two auth modes:
    - Normal: JWT bearer token (iOS client)
    - Internal: X-Worker-Api-Key header (Cloudflare Worker callback)
    """
    from app.models import InsightReport
    from sqlalchemy.orm import selectinload

    worker_api_key = os.environ.get("WORKER_API_KEY", "")
    incoming_key = request.headers.get("X-Worker-Api-Key", "")
    is_worker_request = bool(worker_api_key and incoming_key == worker_api_key)

    if is_worker_request:
        # Worker has no JWT — fetch by report ID only (no user scope)
        result = await db.execute(
            select(InsightReport)
            .options(
                selectinload(InsightReport.evidence_items),
                selectinload(InsightReport.action_items),
                selectinload(InsightReport.generation),
            )
            .where(InsightReport.id == insight_id)
        )
        report = result.scalar_one_or_none()
    else:
        if current_user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"error": {"code": "UNAUTHORIZED", "message": "Authentication required"}},
            )
        report = await get_report(db, current_user.id, insight_id)

    if report is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "INSIGHT_NOT_FOUND", "message": "Insight report not found"}},
        )

    user_id_for_detail = report.user_id if is_worker_request else current_user.id
    detail = await build_report_detail(db, user_id_for_detail, report)
    image_bytes = await run_in_threadpool(render_share_card_png, detail.share_card)
    safe_name = "".join(char if char.isascii() and char.isalnum() else "_" for char in detail.title).strip("_") or "insight"
    return Response(
        content=image_bytes,
        media_type="image/png",
        headers={
            "Content-Disposition": f'attachment; filename="{safe_name}.png"',
            "Cache-Control": "no-store",
        },
    )


@router.get("/{insight_id}", response_model=InsightDetailOut)
async def get_insight_detail(
    insight_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    report = await get_report(db, current_user.id, insight_id)
    if report is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "INSIGHT_NOT_FOUND", "message": "Insight report not found"}},
        )

    return await build_report_detail(db, current_user.id, report)


# ── Generation ──────────────────────


async def _background_generate(generation_id: str) -> None:
    """Run the insight pipeline.

    Mode A (local): runs pipeline in-process (legacy).
    Mode B (worker): delegates to Cloudflare Worker via HTTP.

    Toggle via WORKER_INSIGHTS_URL env var. If set, FastAPI acts as a
    proxy — it creates the DB record, calls the Worker, and the Worker
    writes events back to Supabase. FastAPI's SSE endpoint reads from
    Supabase as usual, so iOS requires zero changes.
    """
    import os
    worker_url = os.environ.get("WORKER_INSIGHTS_URL", "").rstrip("/")

    if worker_url:
        await _background_generate_via_worker(generation_id, worker_url)
    else:
        await _background_generate_local(generation_id)


async def _background_generate_via_worker(generation_id: str, worker_url: str) -> None:
    """Delegate generation to Cloudflare Worker.

    Passes the FastAPI-created generation_id so both systems operate on the
    same DB record. The Worker marks it COMPLETED when the pipeline finishes,
    which the FastAPI SSE endpoint detects via status polling.
    """
    worker_api_key = os.environ.get("WORKER_API_KEY", "")
    async with async_session() as db:
        generation = await db.get(InsightGeneration, generation_id)
        if generation is None:
            return

        user_id = generation.user_id

        try:
            import httpx
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"{worker_url}/api/v1/insights/generate",
                    json={"user_id": str(user_id), "generation_id": generation_id},
                    headers={
                        "Content-Type": "application/json",
                        "X-Worker-Api-Key": worker_api_key,
                    },
                )
                resp.raise_for_status()
                logger.info(
                    "Worker generation started for generation %s",
                    generation_id,
                )

            # Mark local generation as processing
            generation.status = TaskStatus.PROCESSING
            generation.workflow_version = "parallel-v1"
            await db.commit()

        except Exception as exc:
            logger.exception("Worker delegation failed for %s", generation_id)
            generation.status = TaskStatus.FAILED
            generation.error = f"Worker error: {str(exc)[:400]}"
            generation.is_active = False
            generation.completed_at = datetime.now(timezone.utc)
            await db.commit()
            await broadcast_log(generation_id, {"type": "error", "message": str(exc)[:300]})
            clear_generation_buffers(generation_id)


async def _background_generate_local(generation_id: str) -> None:
    """Local pipeline — archived. Set WORKER_INSIGHTS_URL to use the Cloudflare Worker."""
    async with async_session() as db:
        generation = await db.get(InsightGeneration, generation_id)
        if generation is None:
            return
        msg = (
            "Local FastAPI pipeline has been archived. "
            "Set the WORKER_INSIGHTS_URL environment variable to delegate to the Cloudflare Worker."
        )
        logger.error("_background_generate_local called but pipeline is archived: %s", generation_id)
        generation.status = TaskStatus.FAILED
        generation.error = msg
        generation.is_active = False
        generation.completed_at = datetime.now(timezone.utc)
        await db.commit()
        await broadcast_log(generation_id, {"type": "error", "message": msg})


@router.post("/generate", response_model=InsightGenerationOut, status_code=status.HTTP_202_ACCEPTED)
async def generate_insights(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Generate insights via parallel multi-theme LLM calls."""
    generation, created = await create_generation(db, current_user.id)
    if created:
        asyncio.create_task(_background_generate(generation.id))
    return serialize_generation(generation)


@router.post("/generate/clustered", response_model=InsightGenerationOut, status_code=status.HTTP_202_ACCEPTED)
async def generate_insights_clustered(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Alias for /generate — kept for backward compatibility."""
    return await generate_insights(db=db, current_user=current_user)


# ── Chat & Regenerate endpoints ──────────────────────


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000, description="用户消息")


class RegenerateRequest(BaseModel):
    angle_index: int = Field(ge=0, description="要重新生成的角度索引（0-based）")
    instruction: str | None = Field(default=None, max_length=1000, description="额外的生成指导")


@router.post("/generations/{generation_id}/chat", response_model=StatusResponse)
async def chat_with_insight_agent(
    generation_id: str,
    body: ChatRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Send a follow-up message to the insight agent.

    Chat is now handled by the Cloudflare Worker via WebSocket.
    Connect to wss://<worker-url>/agents/InsightAgent/<generation_id>
    """
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail={
            "error": {
                "code": "MIGRATED_TO_WORKER",
                "message": "Chat is now handled by the Cloudflare Worker. Connect via WebSocket.",
            }
        },
    )


@router.post("/generations/{generation_id}/regenerate", response_model=StatusResponse)
async def regenerate_insight_angle(
    generation_id: str,
    body: RegenerateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Regenerate a specific angle report.

    Regeneration is now handled by the Cloudflare Worker.
    Connect via WebSocket to wss://<worker-url>/agents/InsightAgent/<generation_id>
    """
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail={
            "error": {
                "code": "MIGRATED_TO_WORKER",
                "message": "Regeneration is now handled by the Cloudflare Worker. Connect via WebSocket.",
            }
        },
    )


# ── Resume endpoint (deprecated — pipeline no longer pauses) ──


class ResumeRequest(BaseModel):
    action: str = Field(default="approve", pattern="^(approve|retry|skip)$")


@router.post("/generations/{generation_id}/resume", response_model=StatusResponse)
async def resume_insight_generation(
    generation_id: str,
    body: ResumeRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Resume a paused insight generation (deprecated).

    The new pipeline no longer supports human-in-the-loop pauses.
    This endpoint returns a no-op for backward compatibility.
    """
    result = await db.execute(
        select(InsightGeneration).where(
            InsightGeneration.id == generation_id,
            InsightGeneration.user_id == current_user.id,
        )
    )
    generation = result.scalar_one_or_none()
    if generation is None:
        raise HTTPException(status_code=404, detail={"error": {"code": "GENERATION_NOT_FOUND", "message": "Generation not found"}})

    return {"status": "ok", "message": "Resume is not needed in the current pipeline."}


# ── Export endpoints ──────────────────────


@router.get("/{insight_id}/export")
async def export_insight(
    insight_id: str,
    fmt: str = Query("md", alias="format"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Export insight report as markdown, HTML, or plain text."""
    report = await get_report(db, current_user.id, insight_id)
    if report is None:
        raise HTTPException(status_code=404, detail={"error": {"code": "INSIGHT_NOT_FOUND", "message": "Insight not found"}})

    detail = await build_report_detail(db, current_user.id, report)
    title = detail.title
    markdown = detail.report_markdown or ""
    safe_name = "".join(c if c.isascii() and c.isalnum() else "_" for c in title).strip("_") or "insight"

    if fmt == "md":
        content = f"# {title}\n\n{markdown}"
        return Response(
            content=content.encode("utf-8"),
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{safe_name}.md"'},
        )

    if fmt == "html":
        html = f"""<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8"><title>{title}</title>
<style>body{{font-family:system-ui,sans-serif;max-width:800px;margin:40px auto;padding:0 20px;line-height:1.7;color:#1a1a1a}}
h1{{font-size:28px}}h2{{font-size:22px;margin-top:32px}}blockquote{{border-left:3px solid #3c6531;padding-left:16px;color:#555;margin:16px 0}}</style>
</head><body><h1>{title}</h1>"""
        # Simple markdown → HTML conversion
        for line in markdown.split("\n"):
            stripped = line.strip()
            if stripped.startswith("## "):
                html += f"<h2>{stripped[3:]}</h2>"
            elif stripped.startswith("> "):
                html += f"<blockquote>{stripped[2:]}</blockquote>"
            elif stripped.startswith("- "):
                html += f"<li>{stripped[2:]}</li>"
            elif stripped:
                html += f"<p>{stripped}</p>"
        html += "</body></html>"
        return Response(
            content=html.encode("utf-8"),
            media_type="text/html; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{safe_name}.html"'},
        )

    # Default: plain text
    return Response(
        content=f"{title}\n{'=' * len(title)}\n\n{markdown}".encode("utf-8"),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}.txt"'},
    )


# ── Share card HTML (info-card-designer style) ──────────────────────


class ShareCardEditRequest(BaseModel):
    headline: str | None = Field(default=None, max_length=120)
    summary: str | None = Field(default=None, max_length=600)


@router.get("/{insight_id}/share-card.html")
async def get_share_card_html(
    insight_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return an info-card-designer style HTML share card for the insight."""
    report = await get_report(db, current_user.id, insight_id)
    if report is None:
        raise HTTPException(status_code=404, detail={"error": {"code": "INSIGHT_NOT_FOUND", "message": "Insight not found"}})

    detail = await build_report_detail(db, current_user.id, report)
    card = detail.share_card

    # Theme colors
    theme_colors = {
        "trend": "#1A4A3A", "connection": "#2C3E8C",
        "gap": "#8B6914", "opportunity": "#5B2D8E", "report": "#2C2C2C",
    }
    accent = theme_colors.get(card.theme, "#2C2C2C")

    evidence_html = ""
    if card.evidence_quote:
        source = f'<div class="source">— {card.evidence_source}</div>' if card.evidence_source else ""
        evidence_html = f"""<div class="bg-block">
<div class="label">证据</div>
<div class="quote">"{card.evidence_quote}"</div>
{source}</div>"""

    action_html = ""
    if card.action_title:
        detail_text = f'<div class="action-detail">{card.action_detail}</div>' if card.action_detail else ""
        action_html = f"""<div class="action-block">
<div class="label" style="color:#fff">下一步</div>
<div class="action-title">{card.action_title}</div>
{detail_text}</div>"""

    metrics_html = ""
    if card.metrics:
        items = "".join(f'<div class="metric"><span class="metric-value">{m.value}</span><span class="metric-label">{m.label}</span></div>' for m in card.metrics)
        metrics_html = f'<div class="metrics">{items}</div>'

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=600">
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{margin:0;background:#f5f3ed}}
.card{{width:600px;background:#f5f3ed;padding:38px;display:flex;flex-direction:column;gap:24px;position:relative;overflow:hidden}}
.card::before{{content:'';position:absolute;inset:0;background-image:url("data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='noise'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23noise)' opacity='0.04'/%3E%3C/svg%3E");pointer-events:none;z-index:1}}
.card>*{{position:relative;z-index:2}}
.accent-bar{{width:80px;height:6px;background:{accent}}}
.eyebrow{{font-size:16px;letter-spacing:2px;color:{accent};text-transform:uppercase;font-weight:600}}
.headline{{font-size:56px;line-height:1.1;color:#0a0a0a;font-family:serif}}
.summary{{font-size:20px;line-height:1.6;color:#555}}
.bg-block{{background:rgba(0,0,0,0.03);border-left:5px solid {accent};padding:20px 24px}}
.bg-block .label{{font-size:14px;letter-spacing:1.5px;color:{accent};text-transform:uppercase;font-weight:600;margin-bottom:8px}}
.bg-block .quote{{font-size:22px;line-height:1.5;color:#0a0a0a;font-style:italic}}
.bg-block .source{{font-size:15px;color:#777;margin-top:8px}}
.action-block{{background:{accent};border-radius:16px;padding:24px;color:#fff}}
.action-block .action-title{{font-size:28px;line-height:1.3;margin-top:8px}}
.action-block .action-detail{{font-size:15px;color:#e8e4dd;margin-top:8px;line-height:1.5}}
.metrics{{display:flex;gap:24px;padding:16px 0;border-top:1px solid #d5d2cb}}
.metric{{display:flex;flex-direction:column;gap:4px}}
.metric-value{{font-size:28px;font-weight:700;color:#0a0a0a}}
.metric-label{{font-size:14px;color:#777}}
.footer{{font-size:15px;color:#777;text-align:right;border-top:1px solid #d5d2cb;padding-top:16px}}
</style></head><body>
<div class="card">
<div class="accent-bar"></div>
<div class="eyebrow">{card.eyebrow}</div>
<div class="headline">{card.headline}</div>
<div class="summary">{card.summary}</div>
{evidence_html}
{action_html}
{metrics_html}
<div class="footer">{card.footer}</div>
</div></body></html>"""

    return HTMLResponse(html)


@router.post("/{insight_id}/share-card/edit", response_model=StatusResponse)
async def update_share_card_content(
    insight_id: str,
    body: ShareCardEditRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update the share card headline/summary for a report (user editing)."""
    report = await get_report(db, current_user.id, insight_id)
    if report is None:
        raise HTTPException(status_code=404, detail={"error": {"code": "INSIGHT_NOT_FOUND", "message": "Insight not found"}})

    # Update report_json with new share_card fields
    try:
        report_data = json.loads(report.report_json) if report.report_json else {}
    except json.JSONDecodeError:
        report_data = {}

    share_card = report_data.get("share_card", {})
    if body.headline is not None:
        share_card["headline"] = body.headline
    if body.summary is not None:
        share_card["summary"] = body.summary
    report_data["share_card"] = share_card

    report.report_json = json.dumps(report_data, ensure_ascii=False)
    await db.commit()

    return {"status": "ok"}
