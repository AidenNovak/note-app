from __future__ import annotations

import json
import math
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import delete, select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session, get_db
from app.models import MindConnection, Note, NoteTag, NoteSimilarity, User
from app.schemas import (
    GraphEdgeOut,
    GraphNodeOut,
    GraphResponse,
    MindNodeNoteOut,
    MindNodeNotesResponse,
    StatusResponse,
    SynthesisUpdateOut,
)
from app.auth.utils import get_current_user

router = APIRouter(prefix="/mind", tags=["mind"])

_APP_ROOT = Path(__file__).resolve().parents[2]
_GRAPH_HTML = _APP_ROOT / "app" / "mind" / "graph.html"
_JOURNEY_HTML = _APP_ROOT / "app" / "mind" / "journey.html"
_CLUSTER_COLORS = [
    "#6366F1",  # indigo
    "#10B981",  # emerald
    "#F59E0B",  # amber
    "#EC4899",  # pink
    "#8B5CF6",  # violet
    "#06B6D4",  # cyan
    "#EF4444",  # red
    "#84CC16",  # lime
]

# Graph tuning constants
_EDGE_MIN_STRENGTH = 2.0       # drop edges weaker than this
_EDGE_MAX_COUNT = 800           # keep at most this many edges (strongest first)
_CORE_TAG_THRESHOLD = 5         # need ≥5 tags to be "core"
_LABEL_SHOW_TOP_N = 60          # only send labels for top N nodes by degree


@router.get("/graph/web", response_class=HTMLResponse)
async def graph_web():
    """Serve the interactive D3.js knowledge graph page."""
    return HTMLResponse(_GRAPH_HTML.read_text())


@router.get("/journey/web", response_class=HTMLResponse)
async def journey_web():
    """Serve the journey map page."""
    return HTMLResponse(_JOURNEY_HTML.read_text())


@router.get("/journey")
async def get_journey(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return a journey graph: notes as nodes connected by time, tags, and co-occurrence."""
    from app.models import NoteTag

    # Get all notes for this user, ordered by time
    result = await db.execute(
        select(Note)
        .where(Note.user_id == current_user.id)
        .order_by(Note.created_at.asc())
    )
    notes = result.scalars().all()

    if not notes:
        return {"nodes": [], "edges": [], "tags": []}

    # Get all tags for these notes
    note_ids = [n.id for n in notes]
    tag_result = await db.execute(
        select(NoteTag.note_id, NoteTag.tag)
        .where(NoteTag.note_id.in_(note_ids))
    )
    note_tags = {}  # note_id -> [tags]
    for nid, tag in tag_result.all():
        note_tags.setdefault(nid, []).append(tag)

    # Collect unique tags
    all_tags = sorted(set(t for tags in note_tags.values() for t in tags))

    # Build note nodes
    nodes = []
    for i, note in enumerate(notes):
        tags = note_tags.get(note.id, [])
        content = (note.markdown_content or "")[:120].replace("\n", " ").strip()
        nodes.append({
            "id": note.id,
            "type": "note",
            "title": note.title,
            "snippet": content,
            "tags": tags,
            "created_at": note.created_at.isoformat() if note.created_at else None,
            "index": i,  # temporal position
        })

    edges = []
    seen = set()

    def _add_edge(src, tgt, etype, strength=1):
        key = (src, tgt, etype)
        if key not in seen and src != tgt:
            seen.add(key)
            edges.append({"source": src, "target": tgt, "type": etype, "strength": strength})

    # 1. Temporal edges: sequential notes
    for i in range(len(notes) - 1):
        _add_edge(notes[i].id, notes[i + 1].id, "temporal", 1)

    # 2. Tag edges: notes sharing tags
    tag_notes = {}  # tag -> [note_ids]
    for nid, tags in note_tags.items():
        for t in tags:
            tag_notes.setdefault(t, []).append(nid)
    for _tag, nids in tag_notes.items():
        for i in range(len(nids)):
            for j in range(i + 1, len(nids)):
                _add_edge(nids[i], nids[j], "shared_tag", 2)

    # 3. Co-occurrence: notes whose tags overlap significantly
    for i in range(len(notes)):
        tags_i = set(note_tags.get(notes[i].id, []))
        for j in range(i + 1, len(notes)):
            tags_j = set(note_tags.get(notes[j].id, []))
            overlap = len(tags_i & tags_j)
            if overlap >= 2:
                _add_edge(notes[i].id, notes[j].id, "co_occurrence", overlap)

    return {"nodes": nodes, "edges": edges, "tags": all_tags}


@router.get("/graph", response_model=GraphResponse)
async def get_graph(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return the knowledge graph: each note is a node, edges from shared tags + similarity."""
    # Fetch all notes
    result = await db.execute(
        select(Note).where(Note.user_id == current_user.id).order_by(Note.created_at.desc())
    )
    notes = result.scalars().all()

    if not notes:
        return GraphResponse(nodes=[], edges=[], core_mind_note_count=0, layout_seed=0, focus_node_id=None)

    note_ids = [n.id for n in notes]
    total_notes = len(notes)

    # Fetch tags per note
    tag_result = await db.execute(
        select(NoteTag.note_id, NoteTag.tag).where(NoteTag.note_id.in_(note_ids))
    )
    note_tags: dict[str, list[str]] = {}
    for nid, tag in tag_result.all():
        note_tags.setdefault(nid, []).append(tag)

    # Fetch similarity scores
    sim_result = await db.execute(
        select(NoteSimilarity).where(NoteSimilarity.note_id.in_(note_ids))
    )
    sim_map: dict[tuple[str, str], float] = {}
    for s in sim_result.scalars().all():
        pair = (min(s.note_id, s.similar_note_id), max(s.note_id, s.similar_note_id))
        sim_map[pair] = max(sim_map.get(pair, 0), s.similarity_score)

    # Count tag frequency across all notes for cluster assignment
    tag_freq: dict[str, int] = {}
    for tags in note_tags.values():
        for t in tags:
            tag_freq[t] = tag_freq.get(t, 0) + 1
    top_tags_sorted = sorted(tag_freq.items(), key=lambda x: -x[1])
    cluster_tags = [t for t, _ in top_tags_sorted[:len(_CLUSTER_COLORS)]]
    cluster_color_map = {
        tag: _CLUSTER_COLORS[i % len(_CLUSTER_COLORS)]
        for i, tag in enumerate(cluster_tags)
    }

    # Assign each note to a cluster (its most frequent tag among cluster_tags)
    def _note_cluster(nid: str) -> str | None:
        tags = note_tags.get(nid, [])
        for ct in cluster_tags:
            if ct in tags:
                return ct
        return cluster_tags[0] if cluster_tags else None

    # Build edges using inverted tag index (avoids O(n²) brute force)
    tag_to_notes: dict[str, list[str]] = {}
    for nid in note_ids:
        for t in note_tags.get(nid, []):
            tag_to_notes.setdefault(t, []).append(nid)

    pair_shared: dict[tuple[str, str], int] = {}
    for _tag, nids in tag_to_notes.items():
        for i in range(len(nids)):
            for j in range(i + 1, len(nids)):
                pair = (min(nids[i], nids[j]), max(nids[i], nids[j]))
                pair_shared[pair] = pair_shared.get(pair, 0) + 1

    # Include high-similarity pairs without shared tags
    for pair, sim_score in sim_map.items():
        if pair not in pair_shared and sim_score * 5 >= _EDGE_MIN_STRENGTH:
            pair_shared[pair] = 0

    edge_list: list[dict] = []
    for pair, shared_count in pair_shared.items():
        sim_score = sim_map.get(pair, 0.0)
        strength = shared_count * 2 + sim_score * 5
        if strength < _EDGE_MIN_STRENGTH:
            continue
        relation = "hybrid"
        if shared_count > 0 and sim_score <= 0:
            relation = "co_occurrence"
        elif shared_count <= 0 and sim_score > 0:
            relation = "semantic_similarity"
        edge_list.append({
            "source": pair[0],
            "target": pair[1],
            "strength": round(strength, 2),
            "relation": relation,
            "co_occurrence_count": shared_count,
            "content_similarity": round(sim_score, 3),
            "shared_note_count": shared_count,
        })

    # Keep only the strongest edges
    edge_list.sort(key=lambda x: -x["strength"])
    edge_list = edge_list[:_EDGE_MAX_COUNT]

    # Recompute degree from surviving edges only
    edge_strength: dict[str, float] = {nid: 0.0 for nid in note_ids}
    for e in edge_list:
        edge_strength[e["source"]] += e["strength"]
        edge_strength[e["target"]] += e["strength"]

    # Layout: golden-angle spiral on sphere + force-directed refinement
    n = len(notes)
    sphere_radius = 180.0 + n * 6.0
    golden_angle = math.pi * (3.0 - math.sqrt(5.0))

    # Initial positions via golden-angle spiral on sphere
    positions: dict[str, list[float]] = {}
    for idx, nid in enumerate(note_ids):
        theta = math.acos(1.0 - 2.0 * (idx + 0.5) / n)
        phi = golden_angle * idx
        x = sphere_radius * math.sin(theta) * math.cos(phi)
        y = sphere_radius * math.cos(theta)
        z = sphere_radius * math.sin(theta) * math.sin(phi)
        positions[nid] = [x, y, z]

    # Force-directed refinement: 15 iterations (reduced for speed)
    # Connected notes attract, then project back to sphere
    edge_index: dict[str, list[tuple[str, float]]] = {nid: [] for nid in note_ids}
    for e in edge_list:
        edge_index[e["source"]].append((e["target"], e["strength"]))
        edge_index[e["target"]].append((e["source"], e["strength"]))

    for _iteration in range(15):
        displacements: dict[str, list[float]] = {nid: [0.0, 0.0, 0.0] for nid in note_ids}
        for nid in note_ids:
            px, py, pz = positions[nid]
            for neighbor, strength in edge_index[nid]:
                nx, ny, nz = positions[neighbor]
                dx, dy, dz = nx - px, ny - py, nz - pz
                dist = math.sqrt(dx * dx + dy * dy + dz * dz) or 1.0
                # Attraction proportional to strength, capped
                force = min(strength * 0.3, dist * 0.1)
                displacements[nid][0] += dx / dist * force
                displacements[nid][1] += dy / dist * force
                displacements[nid][2] += dz / dist * force

        # Apply displacements and project back to sphere
        for nid in note_ids:
            positions[nid][0] += displacements[nid][0]
            positions[nid][1] += displacements[nid][1]
            positions[nid][2] += displacements[nid][2]
            # Project back to sphere surface
            x, y, z = positions[nid]
            mag = math.sqrt(x * x + y * y + z * z) or 1.0
            positions[nid] = [
                x / mag * sphere_radius,
                y / mag * sphere_radius,
                z / mag * sphere_radius,
            ]

    # Build node list — wider size range, stricter core threshold
    max_tag_count = max((len(note_tags.get(nid, [])) for nid in note_ids), default=1) or 1
    nodes = []
    for idx, note in enumerate(notes):
        nid = note.id
        tag_count = len(note_tags.get(nid, []))
        is_core = tag_count >= _CORE_TAG_THRESHOLD
        # Size: 0.8 (leaf) to 5.0 (hub) — much wider range
        size = round(0.8 + (tag_count / max_tag_count) * 3.2, 2)
        if is_core:
            size = round(size + 1.0, 2)
        cluster = _note_cluster(nid)
        color = cluster_color_map.get(cluster or "", "#9AA097")
        px, py, pz = positions.get(nid, [0.0, 0.0, 0.0])
        nodes.append(GraphNodeOut(
            id=nid,
            label=(note.title or "Untitled")[:20],
            note_count=tag_count,
            size=size,
            color=color,
            x=round(px, 2),
            y=round(py, 2),
            z=round(pz, 2),
            rank=idx + 1,
            degree=round(edge_strength.get(nid, 0.0), 2),
            cluster=cluster,
            is_core=is_core,
        ))

    edges = [
        GraphEdgeOut(
            source=e["source"],
            target=e["target"],
            strength=e["strength"],
            relation=e["relation"],
            co_occurrence_count=e["co_occurrence_count"],
            content_similarity=e["content_similarity"],
            shared_note_count=e["shared_note_count"],
        )
        for e in sorted(edge_list, key=lambda x: -x["strength"])
    ]

    focus_node_id = nodes[0].id if nodes else None
    return GraphResponse(
        nodes=nodes,
        edges=edges,
        core_mind_note_count=total_notes,
        layout_seed=0,
        focus_node_id=focus_node_id,
    )


@router.get("/nodes/{node_id}/notes", response_model=MindNodeNotesResponse)
async def get_node_notes(
    node_id: str,
    page: int = 1,
    page_size: int = 20,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get notes for a specific graph node (by tag label derived from node_id)."""
    tag_result = await db.execute(
        select(NoteTag.tag)
        .join(Note)
        .where(Note.user_id == current_user.id)
        .distinct()
    )
    tag = next(
        (
            value
            for (value,) in tag_result.all()
            if str(uuid.uuid5(uuid.NAMESPACE_DNS, value)) == node_id
        ),
        None,
    )

    if not tag:
        raise HTTPException(status_code=404, detail={"error": {"code": "NODE_NOT_FOUND", "message": "Node not found"}})

    # Get notes with this tag
    query = (
        select(Note)
        .join(NoteTag)
        .where(Note.user_id == current_user.id, NoteTag.tag == tag)
        .order_by(Note.created_at.desc())
    )

    count_q = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_q)).scalar() or 0

    query = query.offset((page - 1) * page_size).limit(page_size)
    notes = (await db.execute(query)).scalars().all()
    note_ids = [note.id for note in notes]

    note_tags: dict[str, list[str]] = {}
    if note_ids:
        tags_result = await db.execute(
            select(NoteTag.note_id, NoteTag.tag).where(NoteTag.note_id.in_(note_ids))
        )
        for note_id_value, note_tag in tags_result.all():
            note_tags.setdefault(note_id_value, []).append(note_tag)

    return MindNodeNotesResponse(
        node_id=node_id,
        tag=tag,
        total=total,
        page=page,
        page_size=page_size,
        items=[
            MindNodeNoteOut(
                id=n.id,
                title=n.title,
                status=n.status.value,
                tags=sorted(note_tags.get(n.id, [])),
                created_at=n.created_at,
                updated_at=n.updated_at,
                snippet=((n.markdown_content or "")[:180].replace("\n", " ").strip()),
            )
            for n in notes
        ],
    )


@router.get("/synthesis", response_model=list[SynthesisUpdateOut])
async def get_synthesis(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """AI-generated synthesis updates about new connections between notes."""
    # Collect tags and recent note content for context
    tag_result = await db.execute(
        select(NoteTag.tag)
        .join(Note)
        .where(Note.user_id == current_user.id)
        .distinct()
        .limit(20)
    )
    tags = [r[0] for r in tag_result.all()]

    if not tags:
        return []

    note_result = await db.execute(
        select(Note.title, Note.markdown_content)
        .where(Note.user_id == current_user.id)
        .order_by(Note.created_at.desc())
        .limit(15)
    )
    notes = note_result.all()
    notes_text = ""
    for title, content in notes:
        snippet = (content or "")[:200]
        notes_text += f"- {title}: {snippet}\n"

    if not notes_text.strip():
        return []

    # Call AI
    try:
        import json
        from app.intelligence.ai import get_provider
        from app.intelligence.ai.prompts import SYNTHESIS_PROMPT

        provider = get_provider()
        user_prompt = f"User's tags: {', '.join(tags)}\n\nUser's notes:\n{notes_text}"
        raw = await provider.generate(SYNTHESIS_PROMPT, user_prompt, profile="mind_synthesis")

        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
            if raw.endswith("```"):
                raw = raw[:-3]
            raw = raw.strip()

        data = json.loads(raw)
        updates = []
        summary = data.get("summary", "")
        if summary:
            updates.append(SynthesisUpdateOut(
                id=str(uuid.uuid4()),
                title="Knowledge Summary",
                description=summary,
                created_at=datetime.now(timezone.utc),
            ))
        for theme in data.get("themes", [])[:3]:
            updates.append(SynthesisUpdateOut(
                id=str(uuid.uuid4()),
                title="Emerging Theme",
                description=f"Detected theme: {theme}",
                created_at=datetime.now(timezone.utc),
            ))
        for suggestion in data.get("suggestions", [])[:3]:
            updates.append(SynthesisUpdateOut(
                id=str(uuid.uuid4()),
                title="Suggested Action",
                description=suggestion,
                created_at=datetime.now(timezone.utc),
            ))

        import logging
        logging.getLogger(__name__).info("AI synthesis generated %d updates for user %s", len(updates), current_user.id)
        return updates

    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("AI synthesis failed: %s", e)
        return []


# ── Connection recording ──────────────────────


async def _record_connections_background(user_id: str) -> None:
    """Discover and persist note connections based on shared tags and similarity scores."""
    import logging
    logger = logging.getLogger(__name__)
    try:
        async with async_session() as db:
            result = await db.execute(
                select(Note.id).where(Note.user_id == user_id)
            )
            note_ids = [r[0] for r in result.all()]
            if len(note_ids) < 2:
                return

            tag_result = await db.execute(
                select(NoteTag.note_id, NoteTag.tag)
                .where(NoteTag.note_id.in_(note_ids))
            )
            note_tags: dict[str, list[str]] = {}
            for nid, tag in tag_result.all():
                note_tags.setdefault(nid, []).append(tag)

            sim_result = await db.execute(
                select(NoteSimilarity)
                .where(NoteSimilarity.note_id.in_(note_ids))
            )
            sim_map: dict[tuple[str, str], float] = {}
            for s in sim_result.scalars().all():
                pair = (min(s.note_id, s.similar_note_id), max(s.note_id, s.similar_note_id))
                sim_map[pair] = max(sim_map.get(pair, 0), s.similarity_score)

            connections: list[dict] = []
            ids = list(note_tags.keys())
            for i in range(len(ids)):
                tags_i = set(note_tags.get(ids[i], []))
                for j in range(i + 1, len(ids)):
                    tags_j = set(note_tags.get(ids[j], []))
                    shared = tags_i & tags_j
                    pair = (min(ids[i], ids[j]), max(ids[i], ids[j]))
                    sim = sim_map.get(pair, 0.0)
                    if not shared and sim < 0.3:
                        continue
                    conn_type = "hybrid" if shared and sim > 0.3 else ("tag_cooccurrence" if shared else "semantic")
                    connections.append({
                        "note_a_id": pair[0],
                        "note_b_id": pair[1],
                        "shared_tags": json.dumps(sorted(shared)),
                        "similarity_score": round(sim, 4),
                        "connection_type": conn_type,
                    })

            if not connections:
                return

            await db.execute(delete(MindConnection).where(MindConnection.user_id == user_id))
            for conn in connections[:100]:
                db.add(MindConnection(id=str(uuid.uuid4()), user_id=user_id, **conn))
            await db.commit()
            logger.info("Recorded %d mind connections for user %s", len(connections[:100]), user_id)
    except Exception:
        logger.warning("Failed to record mind connections", exc_info=True)


@router.post("/connections/refresh", response_model=StatusResponse)
async def refresh_connections(
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
):
    """Trigger background refresh of mind connections."""
    background_tasks.add_task(_record_connections_background, current_user.id)
    return {"status": "queued"}
