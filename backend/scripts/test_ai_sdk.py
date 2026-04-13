"""Quick smoke test for the AI SDK migration."""
import asyncio
import sys
import os
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.intelligence.insights.llm import generate_groups, generate_report


async def test_groups():
    print("=== Testing generate_groups ===")
    system = (
        "You are a knowledge workspace orchestrator. Divide notes into exactly 2 thematic groups.\n\n"
        "## Output Format\n\n"
        "Return ONLY a JSON array of group objects:\n"
        '[\n  {\n    "angle": "A compelling analysis angle",\n'
        '    "note_ids": ["id1", "id2"],\n'
        '    "theme": "Short theme label"\n  }\n]'
    )
    user = "Notes: n1 (cooking tips), n2 (travel Japan), n3 (Python async), n4 (hiking trails). Create exactly 2 groups."
    result = await generate_groups(system=system, user_prompt=user)
    print(f"OK: {len(result.groups)} groups")
    for g in result.groups:
        print(f"  - {g.theme}: {g.angle} (notes: {g.note_ids})")
    return True


async def test_report():
    print("\n=== Testing generate_report ===")
    system = (
        "You are an expert knowledge analyst. Output STRICT JSON with this schema:\n"
        '{\n  "title": "Report title",\n  "description": "2-3 sentence summary",\n'
        '  "type": "report",\n  "report_markdown": "Short markdown report",\n'
        '  "confidence": 0.8,\n  "importance_score": 0.7,\n  "novelty_score": 0.6,\n'
        '  "evidence_items": [{"note_id": "n1", "quote": "...", "rationale": "..."}],\n'
        '  "action_items": [{"title": "...", "detail": "...", "priority": "medium"}],\n'
        '  "share_card": null\n}'
    )
    user = (
        "Analyze these 2 notes:\n"
        "### Cooking Tips (ID: n1)\nUse high heat for stir fry.\n\n"
        "### Travel Japan (ID: n2)\nTokyo street food is amazing.\n\n"
        "Generate a short insight report about food culture."
    )
    result = await generate_report(system=system, user_prompt=user)
    print(f"OK: {result.title}")
    print(f"  type={result.type}, confidence={result.confidence}")
    print(f"  evidence_items={len(result.evidence_items)}, action_items={len(result.action_items)}")
    print(f"  report_markdown[:100]={result.report_markdown[:100]}...")
    return True


async def main():
    ok = True
    try:
        ok = ok and await test_groups()
    except Exception as e:
        print(f"FAIL generate_groups: {e}")
        traceback.print_exc()
        ok = False

    try:
        ok = ok and await test_report()
    except Exception as e:
        print(f"FAIL generate_report: {e}")
        traceback.print_exc()
        ok = False

    print(f"\n{'ALL PASSED' if ok else 'SOME FAILED'}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    asyncio.run(main())
