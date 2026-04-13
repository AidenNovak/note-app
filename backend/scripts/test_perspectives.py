"""Generate insights from two perspectives (philosopher + scientist) and persist to DB.

Usage:
    cd backend && . .venv/bin/activate && set -a && . .env && set +a
    python scripts/test_perspectives.py
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone

import httpx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

# Bootstrap app
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.config import settings
from app.database import async_session
from app.models import (
    InsightActionItem, InsightEvidenceItem, InsightGeneration,
    InsightReport, Note, TaskStatus,
)
from app.intelligence.insights.share_cards import build_share_card_payload

# ---------------------------------------------------------------------------
# Two perspective prompts — same tool schema, different thinking style
# ---------------------------------------------------------------------------

PHILOSOPHER_PROMPT = """\
你是一位深谙东西方哲学的思想家，擅长从存在主义、道家、斯多葛学派、现象学等视角解读日常生活。
你的任务是阅读用户的全部笔记，然后用哲学的眼光写一份洞察报告。

## 你的哲学方法论
- 追问"为什么"而非"是什么" — 探索笔记背后的存在性困惑
- 寻找二元对立与辩证张力 — 确定性vs不确定性、自由vs命运、个体vs关系
- 用哲学概念照亮日常 — 将看似平凡的笔记提升到哲学高度
- 引用经典哲学家的思想作为对话 — 不是掉书袋，而是真正的思想碰撞
- 关注"未说出的" — 笔记的留白和沉默同样重要

## 写作风格
- 像写给一个聪明的朋友的信，不是学术论文
- 用具体的笔记内容引出抽象的哲学思考
- 每个洞察都要落回到"这对你的生活意味着什么"

## 输出格式
直接输出一个 JSON 对象（不要 markdown code block），包含：
{
  "title": "报告标题（哲学性的、引人深思的）",
  "description": "2-3句概要",
  "type": "report",
  "report_markdown": "完整 markdown 报告，800-1200字，用 ## 分节",
  "confidence": 0.0-1.0,
  "importance_score": 0.0-1.0,
  "novelty_score": 0.0-1.0,
  "evidence_items": [
    {"note_id": "...", "quote": "笔记原文引用", "rationale": "哲学解读"}
  ],
  "action_items": [
    {"title": "哲学实践建议", "detail": "具体步骤", "priority": "high|medium|low"}
  ],
  "share_card": {
    "theme": "report",
    "eyebrow": "哲学洞察",
    "headline": "≤80字的核心发现",
    "summary": "2-3句",
    "highlight": "最令人深思的发现",
    "evidence_quote": "最佳引用",
    "evidence_source": "来源笔记标题",
    "action_title": "首要行动",
    "action_detail": "简要说明",
    "metrics": [{"label": "笔记数", "value": "N"}, {"label": "哲学维度", "value": "N"}],
    "footer": "PLACEHOLDER_DATE"
  }
}

写报告时用中文。
"""

SCIENTIST_PROMPT = """\
你是一位跨学科科学家，擅长用系统思维、信息论、认知科学和复杂性理论分析问题。
你的任务是阅读用户的全部笔记，然后用科学家的思维写一份洞察报告。

## 你的科学方法论
- 观察→假设→验证 — 从笔记中提取可检验的模式
- 量化思维 — 关注频率、关联强度、时间序列变化
- 跨学科类比 — 用物理学、生物学、信息论的概念解释行为模式
- 因果推理 vs 相关性 — 严格区分，诚实标注不确定性
- 系统视角 — 将笔记视为一个复杂系统的采样点

## 写作风格
- 清晰、精确、有结构感
- 用数据和模式说话，但不失人文关怀
- 每个发现都标注置信度和证据强度
- 提出可操作的"实验"建议

## 输出格式
直接输出一个 JSON 对象（不要 markdown code block），包含：
{
  "title": "报告标题（科学性的、发现导向的）",
  "description": "2-3句概要",
  "type": "report",
  "report_markdown": "完整 markdown 报告，800-1200字，用 ## 分节",
  "confidence": 0.0-1.0,
  "importance_score": 0.0-1.0,
  "novelty_score": 0.0-1.0,
  "evidence_items": [
    {"note_id": "...", "quote": "笔记原文引用", "rationale": "科学解读"}
  ],
  "action_items": [
    {"title": "科学实验建议", "detail": "具体步骤", "priority": "high|medium|low"}
  ],
  "share_card": {
    "theme": "report",
    "eyebrow": "科学洞察",
    "headline": "≤80字的核心发现",
    "summary": "2-3句",
    "highlight": "最令人惊讶的发现",
    "evidence_quote": "最佳引用",
    "evidence_source": "来源笔记标题",
    "action_title": "首要实验",
    "action_detail": "简要说明",
    "metrics": [{"label": "笔记数", "value": "N"}, {"label": "模式数", "value": "N"}],
    "footer": "PLACEHOLDER_DATE"
  }
}

写报告时用中文。
"""

# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

async def fetch_all_notes(db: AsyncSession) -> list[dict]:
    result = await db.execute(select(Note).order_by(Note.updated_at.desc()))
    notes = result.scalars().all()
    return [
        {
            "id": n.id,
            "title": n.title or "(untitled)",
            "content": n.markdown_content or "",
        }
        for n in notes
    ]


def build_notes_block(notes: list[dict]) -> str:
    lines = [f"# 用户笔记（共 {len(notes)} 条）\n"]
    for i, n in enumerate(notes, 1):
        lines.append(f"## 笔记 {i}: {n['title']} (ID: {n['id']})\n{n['content']}\n")
    return "\n".join(lines)


async def call_openrouter(system: str, user_msg: str) -> dict:
    """Single-pass OpenRouter call, return parsed JSON."""
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": settings.AI_MODEL,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_msg},
                ],
                "max_tokens": 4096,
                "temperature": 0.7,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        text = data["choices"][0]["message"]["content"]

    # Strip markdown code fences if present
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()
    if text.startswith("json"):
        text = text[4:].strip()

    return json.loads(text)


PLACEHOLDER_SUFFIX = " — 对比测试"


async def persist_report(
    db: AsyncSession, user_id: str, payload: dict, notes: list[dict], label: str,
) -> str:
    """Write one report into the DB and return its ID."""
    now = datetime.now(timezone.utc)
    gen_id = str(uuid.uuid4())
    report_id = str(uuid.uuid4())

    # Deactivate old generations for this user
    await db.execute(
        update(InsightGeneration)
        .where(InsightGeneration.user_id == user_id)
        .values(is_active=False)
    )

    db.add(InsightGeneration(
        id=gen_id,
        user_id=user_id,
        status=TaskStatus.COMPLETED,
        workflow_version=f"perspective-test-{label}",
        is_active=True,
        total_reports=1,
        completed_at=now,
        summary=f"[{label}] {payload.get('title', 'Report')}",
    ))

    evidence_items = payload.get("evidence_items", [])
    action_items = payload.get("action_items", [])
    valid_ids = {n["id"] for n in notes}

    share_card_data = build_share_card_payload(
        report_type=payload.get("type", "report"),
        title=payload.get("title", "Insight Report"),
        description=payload.get("description", ""),
        confidence=float(payload.get("confidence", 0.7)),
        importance_score=float(payload.get("importance_score", 0.7)),
        novelty_score=float(payload.get("novelty_score", 0.5)),
        generated_at=now,
        evidence_items=evidence_items,
        action_items=action_items,
        raw_share_card=payload.get("share_card"),
    )

    db.add(InsightReport(
        id=report_id,
        generation_id=gen_id,
        user_id=user_id,
        type=payload.get("type", "report"),
        status="published",
        title=f"[{label}] {payload.get('title', 'Report')}",
        description=payload.get("description", ""),
        report_version=1,
        confidence=float(payload.get("confidence", 0.7)),
        importance_score=float(payload.get("importance_score", 0.7)),
        novelty_score=float(payload.get("novelty_score", 0.5)),
        card_rank=1,
        report_markdown=payload.get("report_markdown", ""),
        report_json=json.dumps(payload, ensure_ascii=False),
        source_note_ids=json.dumps([n["id"] for n in notes]),
        generated_at=now,
    ))

    for idx, ev in enumerate(evidence_items, 1):
        nid = ev.get("note_id", "")
        if nid not in valid_ids and valid_ids:
            nid = next(iter(valid_ids))
        db.add(InsightEvidenceItem(
            id=str(uuid.uuid4()),
            report_id=report_id,
            note_id=nid,
            quote=str(ev.get("quote", ""))[:500],
            rationale=str(ev.get("rationale", ""))[:500],
            sort_order=idx,
        ))

    for idx, act in enumerate(action_items, 1):
        db.add(InsightActionItem(
            id=str(uuid.uuid4()),
            report_id=report_id,
            title=str(act.get("title", ""))[:255],
            detail=str(act.get("detail", ""))[:500],
            priority=str(act.get("priority", "medium"))[:16],
            sort_order=idx,
        ))

    await db.commit()
    return report_id


async def main():
    async with async_session() as db:
        notes = await fetch_all_notes(db)
        if not notes:
            print("No notes found!")
            return

        # Find user_id from first note
        result = await db.execute(select(Note).limit(1))
        user_id = result.scalar_one().user_id
        print(f"User: {user_id}, Notes: {len(notes)}")

        notes_block = build_notes_block(notes)
        today = datetime.now(timezone.utc).strftime("%Y年%m月%d日")

        # Generate both perspectives in parallel
        print("\n🔮 Generating philosopher perspective...")
        phil_prompt = PHILOSOPHER_PROMPT.replace("PLACEHOLDER_DATE", f"生成于 {today}")
        print("🔬 Generating scientist perspective...")
        sci_prompt = SCIENTIST_PROMPT.replace("PLACEHOLDER_DATE", f"生成于 {today}")

        phil_payload, sci_payload = await asyncio.gather(
            call_openrouter(phil_prompt, notes_block),
            call_openrouter(sci_prompt, notes_block),
        )

        print(f"\n✅ Philosopher: {phil_payload.get('title')}")
        print(f"✅ Scientist:   {sci_payload.get('title')}")

        # Persist — scientist first (older), then philosopher (newer, will be active)
        sci_id = await persist_report(db, user_id, sci_payload, notes, "科学家")
        phil_id = await persist_report(db, user_id, phil_payload, notes, "哲学家")

        print(f"\n📝 Scientist report ID:   {sci_id}")
        print(f"📝 Philosopher report ID: {phil_id}")
        print("\n🎉 Done! Check the simulator — pull to refresh the Insights tab.")


if __name__ == "__main__":
    asyncio.run(main())
