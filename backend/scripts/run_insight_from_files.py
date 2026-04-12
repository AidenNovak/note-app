#!/usr/bin/env python3
"""Run insight generation from arbitrary markdown files (standalone script).

Usage:
    python scripts/run_insight_from_files.py file1.md file2.md ... --output-dir ~/Downloads
"""
from __future__ import annotations

import asyncio
import json
import sys
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Add backend root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from app.intelligence.insights.llm import (
    discover_angles,
    generate_report_for_angle,
)


def read_files_as_notes(file_paths: list[str]) -> list[dict]:
    """Read markdown files and convert to note-like dicts."""
    notes = []
    for fp in file_paths:
        p = Path(fp)
        if not p.exists():
            print(f"⚠️  File not found: {fp}")
            continue
        content = p.read_text(encoding="utf-8").strip()
        note_id = str(uuid.uuid4())[:8]
        notes.append({
            "id": note_id,
            "title": p.stem,
            "content": content,
            "tags": [],
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
    return notes


def build_cluster_summary(notes: list[dict]) -> str:
    """Build cluster summary text for angle discovery."""
    lines = [f"# 知识图谱概览 — {len(notes)} 条笔记, 1 个主题簇\n"]
    lines.append(f"\n## 簇 0: 用户笔记 ({len(notes)} 条笔记)")
    lines.append(f"内部连接数: 0, 平均相似度: 0.0")
    for note in notes:
        preview = note["content"][:120].replace("\n", " ")
        lines.append(f"- [{note['id']}] {note['title']} | | {preview}...")
    return "\n".join(lines)


def build_notes_content(note_ids: list[str], note_map: dict[str, dict]) -> str:
    """Build full note content for report generation."""
    parts = []
    for nid in note_ids:
        note = note_map.get(nid)
        if not note:
            continue
        tags = ", ".join(note["tags"]) if note["tags"] else "无标签"
        parts.append(
            f"### {note['title']} (ID: {nid})\n"
            f"标签: {tags} | 创建于: {note.get('created_at', '未知')}\n\n"
            f"{note['content']}\n"
        )
    return "\n---\n".join(parts)


# Dummy broadcast_log for standalone use (pipeline imports it)
import app.intelligence.insights.service as _svc
_original_broadcast = _svc.broadcast_log
async def _noop_broadcast(generation_id, event):
    msg = event.get("message", "")
    if msg:
        print(f"  📡 {msg}")
_svc.broadcast_log = _noop_broadcast


async def main(file_paths: list[str], output_dir: str):
    notes = read_files_as_notes(file_paths)
    if not notes:
        print("❌ No valid files provided.")
        return

    note_map = {n["id"]: n for n in notes}
    print(f"📄 Loaded {len(notes)} notes: {[n['title'] for n in notes]}")

    # Phase 1: Angle discovery
    print("\n🔍 Phase 1: Discovering analysis angles...")
    cluster_summary = build_cluster_summary(notes)
    num_angles = min(5, max(2, len(notes)))

    angle_result = await discover_angles(
        cluster_summaries=cluster_summary,
        num_angles=num_angles,
    )
    angles = angle_result.angles[:5]
    print(f"  Found {len(angles)} angles:")
    for i, a in enumerate(angles):
        # Validate note_ids
        a.note_ids = [nid for nid in a.note_ids if nid in note_map]
        if not a.note_ids:
            a.note_ids = list(note_map.keys())  # fallback: use all notes
        print(f"    {i+1}. [{a.type_hint}] {a.angle_name}: {a.description} ({len(a.note_ids)} notes)")

    # Phase 2: Generate reports
    print(f"\n📝 Phase 2: Generating {len(angles)} reports...")
    generation_id = str(uuid.uuid4())
    date_str = datetime.now().strftime("%Y-%m-%d")
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    reports = []
    for i, angle in enumerate(angles):
        print(f"\n  --- Report {i+1}/{len(angles)}: {angle.angle_name} ---")
        notes_content = build_notes_content(angle.note_ids, note_map)
        try:
            report = await generate_report_for_angle(
                angle_name=angle.angle_name,
                angle_description=angle.description,
                type_hint=angle.type_hint,
                notes_content=notes_content,
                note_count=len(angle.note_ids),
                date=date_str,
                generation_id=generation_id,
                group_index=i + 1,
            )
            reports.append(report)
            print(f"  ✅ {report.title}")
        except Exception as exc:
            print(f"  ❌ Failed: {exc}")

    # Phase 3: Save reports
    print(f"\n💾 Saving {len(reports)} reports to {out_path}/")
    for i, report in enumerate(reports):
        # Save markdown
        md_file = out_path / f"insight-{i+1}-{report.type}.md"
        md_content = f"# {report.title}\n\n"
        md_content += f"> {report.description}\n\n"
        md_content += f"**类型**: {report.type} | **置信度**: {report.confidence} | **重要性**: {report.importance_score} | **新颖度**: {report.novelty_score}\n\n"
        md_content += "---\n\n"
        md_content += report.report_markdown + "\n\n"

        md_content += "---\n\n## 📌 证据\n\n"
        for ev in report.evidence_items:
            md_content += f"- **[{ev.note_id}]** \"{ev.quote}\"\n  — {ev.rationale}\n\n"

        md_content += "## 🎯 行动建议\n\n"
        for act in report.action_items:
            md_content += f"- **[{act.priority}] {act.title}**: {act.detail}\n\n"

        md_file.write_text(md_content, encoding="utf-8")
        print(f"  📄 {md_file.name}")

    # Save full JSON
    json_file = out_path / "insight-reports-full.json"
    json_data = [r.model_dump() for r in reports]
    json_file.write_text(json.dumps(json_data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  📋 {json_file.name}")

    print(f"\n🎉 Done! {len(reports)} reports saved to {out_path}/")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Generate insight reports from markdown files")
    parser.add_argument("files", nargs="+", help="Markdown file paths")
    parser.add_argument("--output-dir", default=os.path.expanduser("~/Downloads"), help="Output directory")
    args = parser.parse_args()
    asyncio.run(main(args.files, args.output_dir))
