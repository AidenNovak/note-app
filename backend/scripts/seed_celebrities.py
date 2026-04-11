#!/usr/bin/env python3
"""Seed Ground with celebrity users, AI-generated notes, insights, and posts.

Usage:
    python backend/scripts/seed_celebrities.py [--base-url URL] [--skip-insights] [--openrouter-key KEY]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import requests

DEFAULT_BASE = "http://localhost:8000"
PASSWORD = "Celeb1234!"

CELEBRITIES: list[dict] = [
    {"username": "karpathy",   "email": "karpathy@seed-ground.dev",   "tags": ["ai", "deep-learning", "neural-networks", "education"],
     "avatar": "https://unavatar.io/x/karpathy"},
    {"username": "elonmusk",   "email": "elonmusk@seed-ground.dev",   "tags": ["space", "tesla", "ai", "startup"],
     "avatar": "https://unavatar.io/x/elonmusk"},
    {"username": "trump",      "email": "trump@seed-ground.dev",      "tags": ["politics", "economy", "leadership", "media"],
     "avatar": "https://unavatar.io/x/realDonaldTrump"},
    {"username": "naval",      "email": "naval@seed-ground.dev",      "tags": ["startup", "investment", "philosophy"],
     "avatar": "https://unavatar.io/x/naval"},
    {"username": "paulgraham", "email": "paulgraham@seed-ground.dev",  "tags": ["startup", "yc", "essays", "product"],
     "avatar": "https://unavatar.io/x/paulg"},
    {"username": "lexfridman", "email": "lexfridman@seed-ground.dev",  "tags": ["ai", "podcast", "philosophy", "research"],
     "avatar": "https://unavatar.io/x/lexfridman"},
    {"username": "vitalik",    "email": "vitalik@seed-ground.dev",     "tags": ["crypto", "ethereum", "web3"],
     "avatar": "https://unavatar.io/x/VitalikButerin"},
    {"username": "sama",       "email": "sama@seed-ground.dev",        "tags": ["ai", "openai", "startup", "policy"],
     "avatar": "https://unavatar.io/x/sama"},
    {"username": "feynman",    "email": "feynman@seed-ground.dev",     "tags": ["physics", "education", "research", "quantum"],
     "avatar": "https://unavatar.io/wikipedia/Richard_Feynman"},
    {"username": "taleb",      "email": "taleb@seed-ground.dev",       "tags": ["risk", "investment", "philosophy", "statistics"],
     "avatar": "https://unavatar.io/x/nntaleb"},
]

# ── Helpers ──────────────────────────────────────────────────────────────


def auth_user(base: str, session: requests.Session, celeb: dict) -> str:
    """Register (or login) a celebrity user, return bearer token."""
    for attempt in range(3):
        r = session.post(f"{base}/api/v1/auth/register", json={
            "username": celeb["username"],
            "email": celeb["email"],
            "password": PASSWORD,
        })
        if r.status_code in (201, 400):
            break
        if r.status_code == 429:
            print(f"    Rate limited on register, waiting 60s…")
            time.sleep(60)
            continue
        if r.status_code >= 500:
            print(f"    Server error {r.status_code} on register, retrying in 5s…")
            time.sleep(5)
            continue
        r.raise_for_status()

    for attempt in range(3):
        r = session.post(f"{base}/api/v1/auth/login", json={
            "email": celeb["email"],
            "password": PASSWORD,
        })
        if r.status_code == 200:
            return r.json()["access_token"]
        if r.status_code == 429:
            print(f"    Rate limited on login, waiting 60s…")
            time.sleep(60)
            continue
        if r.status_code >= 500:
            print(f"    Server error {r.status_code} on login, retrying in 5s…")
            time.sleep(5)
            continue
        r.raise_for_status()
    raise RuntimeError(f"Failed to authenticate {celeb['username']} after retries")


def generate_notes_via_openrouter(
    celeb: dict, count: int, api_key: str,
) -> list[dict]:
    """Call OpenRouter to generate `count` notes as JSON array."""
    tags_str = ", ".join(celeb["tags"])
    prompt = (
        f"You are {celeb['username']}, a well-known figure in {tags_str}.\n"
        f"Generate exactly {count} short personal notes/thoughts as a JSON array.\n"
        f"Each element: {{\"title\": \"...\", \"content\": \"...\", \"tags\": [...]}}\n"
        f"- title: concise (5-12 words)\n"
        f"- content: 80-250 words of markdown, insightful, in first person\n"
        f"- tags: 2-4 tags from [{tags_str}]\n"
        f"Return ONLY the JSON array, no markdown fences."
    )

    r = requests.post(
        os.environ.get("LLM_BASE_URL", "https://openrouter.ai/api/v1") + "/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "model": os.environ.get("LLM_MODEL", "google/gemini-2.0-flash-001"),
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.8,
            "max_tokens": 8000,
        },
        timeout=120,
    )
    r.raise_for_status()
    text = r.json()["choices"][0]["message"]["content"]
    # Strip markdown fences if present
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
    if text.endswith("```"):
        text = text.rsplit("```", 1)[0]
    return json.loads(text.strip())


def create_notes(
    base: str, session: requests.Session, headers: dict, notes_data: list[dict],
) -> list[str]:
    """Create notes via API, return list of created note IDs."""
    ids: list[str] = []
    for n in notes_data:
        payload = {
            "title": n.get("title", "Untitled"),
            "markdown_content": n.get("content", ""),
            "tags": n.get("tags", []),
        }
        r = session.post(f"{base}/api/v1/notes", json=payload, headers=headers)
        if r.status_code == 201:
            ids.append(r.json()["id"])
        else:
            print(f"    ⚠ Note creation failed ({r.status_code}): {r.text[:80]}")
        time.sleep(0.05)
    return ids


def trigger_insights(base: str, session: requests.Session, headers: dict) -> str | None:
    """Trigger multi-agent insight generation, poll until done. Return generation_id."""
    r = session.post(f"{base}/api/v1/insights/generate/multi-agent", headers=headers)
    if r.status_code not in (200, 201, 202):
        print(f"    ⚠ Insight trigger failed ({r.status_code}): {r.text[:80]}")
        return None
    gen_id = r.json()["id"]
    print(f"    Generation {gen_id[:8]}… started")

    for _ in range(120):  # up to ~10 min
        time.sleep(5)
        r = session.get(f"{base}/api/v1/insights/generations/latest", headers=headers)
        if r.status_code != 200:
            continue
        data = r.json()
        if data and data.get("id") == gen_id:
            status = data.get("status", "")
            if status in ("completed", "failed"):
                print(f"    Generation {status} ({data.get('total_reports', 0)} reports)")
                return gen_id if status == "completed" else None
    print("    ⚠ Insight generation timed out")
    return None


def share_insights_to_ground(
    base: str, session: requests.Session, headers: dict,
) -> int:
    """Fetch all insights and share each to Ground. Return count shared."""
    r = session.get(f"{base}/api/v1/insights", headers=headers)
    if r.status_code != 200:
        return 0
    insights = r.json()
    shared = 0
    for ins in insights:
        body = {
            "post_type": "insight",
            "ref_id": ins["id"],
            "title": ins.get("title", "Insight"),
            "preview": ins.get("description", "")[:500],
        }
        r2 = session.post(f"{base}/api/v1/ground/posts", json=body, headers=headers)
        if r2.status_code in (200, 201):
            shared += 1
        time.sleep(0.05)
    return shared


# ── Main ─────────────────────────────────────────────────────────────────


def process_celebrity(
    base: str,
    session: requests.Session,
    celeb: dict,
    api_key: str,
    skip_insights: bool,
    note_count: int = 15,
    token: str | None = None,
) -> dict:
    """Full pipeline for one celebrity. Returns stats dict."""
    name = celeb["username"]
    stats = {"notes": 0, "insights": 0, "ground_posts": 0}

    print(f"\n── {name} ──")

    # 1. Auth
    if not token:
        token = auth_user(base, session, celeb)
    headers = {"Authorization": f"Bearer {token}"}
    print(f"  ✓ Authenticated")

    # Set avatar
    if celeb.get("avatar"):
        r = session.patch(f"{base}/api/v1/auth/me", json={"avatar_url": celeb["avatar"]}, headers=headers)
        if r.status_code == 200:
            print(f"  ✓ Avatar set")
        else:
            print(f"  ⚠ Avatar failed ({r.status_code})")

    # 2. Generate notes via LLM
    print(f"  Generating {note_count} notes via OpenRouter…")
    try:
        notes_data = generate_notes_via_openrouter(celeb, note_count, api_key)
    except Exception as e:
        print(f"  ✗ OpenRouter failed: {e}")
        return stats

    # 3. Create notes
    note_ids = create_notes(base, session, headers, notes_data)
    stats["notes"] = len(note_ids)
    print(f"  ✓ Created {len(note_ids)} notes")

    if not note_ids:
        return stats

    # 4. Insights
    if not skip_insights:
        print(f"  Triggering insight generation…")
        gen_id = trigger_insights(base, session, headers)
        if gen_id:
            shared = share_insights_to_ground(base, session, headers)
            stats["insights"] = shared
            stats["ground_posts"] += shared
            print(f"  ✓ Shared {shared} insights to Ground")
    else:
        print(f"  ⏭ Skipping insights")

    # 5. Share some notes to Ground too
    for nid in note_ids[:5]:  # share first 5 notes
        r = session.post(f"{base}/api/v1/ground/notes/{nid}/share", headers=headers)
        if r.status_code == 200:
            stats["ground_posts"] += 1
        time.sleep(0.05)
    print(f"  ✓ Shared {min(5, len(note_ids))} notes to Ground")

    return stats


def main():
    parser = argparse.ArgumentParser(description="Seed Ground with celebrity content")
    parser.add_argument("--base-url", default=DEFAULT_BASE)
    parser.add_argument("--skip-insights", action="store_true", help="Skip insight generation (faster)")
    parser.add_argument("--openrouter-key", default=os.environ.get("OPENROUTER_API_KEY", ""))
    parser.add_argument("--note-count", type=int, default=15)
    parser.add_argument("--only", help="Comma-separated usernames to process (default: all)")
    args = parser.parse_args()

    if not args.openrouter_key:
        print("Error: --openrouter-key or OPENROUTER_API_KEY env var required")
        sys.exit(1)

    celebs = CELEBRITIES
    if args.only:
        names = {n.strip() for n in args.only.split(",")}
        celebs = [c for c in CELEBRITIES if c["username"] in names]
        if not celebs:
            print(f"No matching celebrities for: {args.only}")
            sys.exit(1)

    session = requests.Session()
    base = args.base_url

    print("═══ Celebrity Seed Script ═══")
    print(f"  Base URL: {base}")
    print(f"  Celebrities: {len(celebs)}")
    print(f"  Notes per user: {args.note_count}")
    print(f"  Skip insights: {args.skip_insights}")

    # Phase 1: Register all users
    print("\n── Phase 1: Registering users ──")
    tokens: dict[str, str] = {}
    for i, celeb in enumerate(celebs):
        token = auth_user(base, session, celeb)
        tokens[celeb["username"]] = token
        print(f"  ✓ {celeb['username']}")
        time.sleep(0.5)

    # Phase 2: Generate notes + share to Ground
    print("\n── Phase 2: Content generation ──")
    totals = {"notes": 0, "insights": 0, "ground_posts": 0}
    for celeb in celebs:
        stats = process_celebrity(
            base, session, celeb, args.openrouter_key,
            skip_insights=args.skip_insights,
            note_count=args.note_count,
            token=tokens[celeb["username"]],
        )
        for k in totals:
            totals[k] += stats[k]

    print(f"\n═══ Summary ═══")
    print(f"  Notes created:  {totals['notes']}")
    print(f"  Insights shared: {totals['insights']}")
    print(f"  Ground posts:   {totals['ground_posts']}")
    print("═══ Done ═══")


if __name__ == "__main__":
    main()

