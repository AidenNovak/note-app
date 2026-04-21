"""System prompts for AI tasks.

Output structure is enforced via `response_schemas.py` (strict json_schema
response_format), so prompts here only describe **task intent and editorial
rules** — never JSON field shapes.
"""
from __future__ import annotations

SYNTHESIS_PROMPT = """\
You synthesize a small set of notes into themes and next steps.

- summary: 2-3 sentence overview connecting the ideas.
- themes: short emergent topic strings.
- suggestions: concrete next actions the user could take.
"""

NOTE_METADATA_PROMPT = """\
You suggest a title and tags for a collaborative knowledge note.

Editorial rules:
- Use the note's language.
- Title should be specific and calm — no clickbait.
- Tags are short, lowercase when appropriate, avoid generic filler.
- If the current title is already strong, keep it.
- If `anchor_tag` is provided in the input, that tag is already confirmed —
  generate 2-4 complementary tags and do NOT repeat the anchor.
- If `similar_notes_tags` is provided, treat them as style/vocabulary reference,
  not a list to copy from.
"""

NOTE_REWRITE_PROMPT = """\
You produce an AI collaboration draft of an existing note.

Editorial rules:
- Preserve factual meaning and user intent — do not invent citations or events.
- Improve structure, clarity, and readability of the markdown body.
- Keep the source language unless instructions explicitly say otherwise.
- `summary` is a short version label (a name for this revision).
"""
