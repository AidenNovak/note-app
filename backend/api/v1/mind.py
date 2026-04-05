from __future__ import annotations

import hashlib
import math
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Note, NoteTag, User
from app.schemas import (
    GraphEdgeOut,
    GraphNodeOut,
    GraphResponse,
    MindNodeNoteOut,
    MindNodeNotesResponse,
    SynthesisUpdateOut,
)
from app.auth.utils import get_current_user

router = APIRouter(prefix="/mind", tags=["mind"])

_APP_ROOT = Path(__file__).resolve().parents[2]
_GRAPH_HTML = _APP_ROOT / "app" / "mind" / "graph.html"
_JOURNEY_HTML = _APP_ROOT / "app" / "mind" / "journey.html"
_CLUSTER_COLORS = [
    "#7A9573",
    "#9AA97C",
    "#8BA7A0",
    "#C1B58A",
]


def _stable_ratio(*parts: object) -> float:
    raw = "::".join(str(part) for part in parts).encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    return int(digest[:12], 16) / float(0xFFFFFFFFFFFF)


def _resolve_layout_seed(user_id: str, tags: list[str]) -> int:
    digest = hashlib.sha256(f"{user_id}|{'|'.join(tags)}".encode("utf-8")).hexdigest()
    return int(digest[:12], 16)


def _build_cluster_assignments(
    top_tags: list[tuple[str, int]],
    tag_strength_map: dict[str, dict[str, float]],
    layout_seed: int,
) -> tuple[list[str], dict[str, str], dict[str, str]]:
    core_tags = [tag for tag, _ in top_tags[: min(4, len(top_tags))]]
    if not core_tags:
        return [], {}, {}

    color_by_cluster = {
        cluster: _CLUSTER_COLORS[index % len(_CLUSTER_COLORS)]
        for index, cluster in enumerate(core_tags)
    }

    cluster_by_tag: dict[str, str] = {}
    for tag, _count in top_tags:
        if tag in color_by_cluster:
            cluster_by_tag[tag] = tag
            continue

        weights = tag_strength_map.get(tag, {})
        ranked_cores = sorted(
            ((core, weights.get(core, 0.0)) for core in core_tags),
            key=lambda item: (-item[1], item[0]),
        )
        if ranked_cores and ranked_cores[0][1] > 0:
            cluster_by_tag[tag] = ranked_cores[0][0]
            continue

        fallback_index = int(_stable_ratio(layout_seed, tag) * len(core_tags)) % len(core_tags)
        cluster_by_tag[tag] = core_tags[fallback_index]

    return core_tags, cluster_by_tag, color_by_cluster


def _build_layout_positions(
    top_tags: list[tuple[str, int]],
    edge_records: list[dict[str, object]],
    degrees: dict[str, float],
    cluster_by_tag: dict[str, str],
    core_tags: list[str],
    layout_seed: int,
) -> dict[str, tuple[float, float, float]]:
    if not top_tags:
        return {}

    positions: dict[str, list[float]] = {}
    anchor_positions: dict[str, tuple[float, float]] = {}

    if not core_tags:
        core_tags = [tag for tag, _count in top_tags[:1]]

    core_radius_x = 170.0
    core_radius_y = 128.0
    for index, tag in enumerate(core_tags):
        angle = (-math.pi / 2) + (index * (2 * math.pi / max(len(core_tags), 1)))
        x = math.cos(angle) * core_radius_x
        y = math.sin(angle) * core_radius_y
        positions[tag] = [x, y]
        anchor_positions[tag] = (x, y)

    members_by_cluster: dict[str, list[str]] = {cluster: [] for cluster in core_tags}
    for tag, _count in top_tags:
        cluster = cluster_by_tag.get(tag)
        if cluster is None or tag in core_tags:
            continue
        members_by_cluster.setdefault(cluster, []).append(tag)

    core_index = {tag: index for index, tag in enumerate(core_tags)}
    for cluster, members in members_by_cluster.items():
        base_angle = (-math.pi / 2) + (core_index.get(cluster, 0) * (2 * math.pi / max(len(core_tags), 1)))
        sorted_members = sorted(
            members,
            key=lambda tag: (-degrees.get(tag, 0.0), tag),
        )
        for index, tag in enumerate(sorted_members):
            ring = index // 4
            slot = index % 4
            spread = (slot - 1.5) * 0.44
            orbit = 108 + (ring * 54) + (_stable_ratio(layout_seed, cluster, tag) * 18)
            angle = base_angle + spread
            x = anchor_positions[cluster][0] + (math.cos(angle) * orbit)
            y = anchor_positions[cluster][1] + (math.sin(angle) * orbit)
            positions[tag] = [x, y]
            anchor_positions[tag] = (x, y)

    for tag, _count in top_tags:
        if tag in positions:
            continue
        ratio = _stable_ratio(layout_seed, tag)
        angle = ratio * 2 * math.pi
        orbit = 220 + (_stable_ratio(tag, layout_seed, "orbit") * 36)
        x = math.cos(angle) * orbit
        y = math.sin(angle) * orbit
        positions[tag] = [x, y]
        anchor_positions[tag] = (x, y)

    for _ in range(26):
        forces = {tag: [0.0, 0.0] for tag, _count in top_tags}
        tag_names = [tag for tag, _count in top_tags]

        for index, tag_a in enumerate(tag_names):
            pos_a = positions[tag_a]
            for tag_b in tag_names[index + 1:]:
                pos_b = positions[tag_b]
                dx = pos_b[0] - pos_a[0]
                dy = pos_b[1] - pos_a[1]
                dist_sq = (dx * dx) + (dy * dy) + 1.0
                dist = math.sqrt(dist_sq)
                repulsion = 16500 / dist_sq
                fx = (dx / dist) * repulsion
                fy = (dy / dist) * repulsion
                forces[tag_a][0] -= fx
                forces[tag_a][1] -= fy
                forces[tag_b][0] += fx
                forces[tag_b][1] += fy

        for edge in edge_records:
            source = edge["source_tag"]
            target = edge["target_tag"]
            strength = float(edge["strength"])
            pos_source = positions[source]
            pos_target = positions[target]
            dx = pos_target[0] - pos_source[0]
            dy = pos_target[1] - pos_source[1]
            dist = math.sqrt((dx * dx) + (dy * dy)) or 1.0
            desired = max(88.0, 158.0 - min(strength, 7.0) * 10.0)
            spring = (dist - desired) * (0.022 + (strength * 0.003))
            fx = (dx / dist) * spring
            fy = (dy / dist) * spring
            forces[source][0] += fx
            forces[source][1] += fy
            forces[target][0] -= fx
            forces[target][1] -= fy

        for tag, _count in top_tags:
            anchor_x, anchor_y = anchor_positions[tag]
            pos = positions[tag]
            forces[tag][0] += (anchor_x - pos[0]) * 0.08
            forces[tag][1] += (anchor_y - pos[1]) * 0.08

            if tag in core_tags:
                forces[tag][0] += (0 - pos[0]) * 0.03
                forces[tag][1] += (0 - pos[1]) * 0.03

        for tag, _count in top_tags:
            positions[tag][0] += forces[tag][0] * 0.18
            positions[tag][1] += forces[tag][1] * 0.18

    xs = [coords[0] for coords in positions.values()]
    ys = [coords[1] for coords in positions.values()]
    center_x = (min(xs) + max(xs)) / 2 if xs else 0
    center_y = (min(ys) + max(ys)) / 2 if ys else 0

    # Compute z-axis: core nodes at z≈0, satellites offset by cluster index
    z_values: dict[str, float] = {}
    for tag, _count in top_tags:
        if tag in core_tags:
            z_values[tag] = 0.0
        else:
            cluster = cluster_by_tag.get(tag)
            cluster_idx = core_tags.index(cluster) if cluster and cluster in core_tags else 0
            z_base = (cluster_idx - len(core_tags) / 2) * 60.0
            z_jitter = (_stable_ratio(layout_seed, tag, "z") - 0.5) * 40.0
            z_values[tag] = round(z_base + z_jitter, 2)

    return {
        tag: (round(coords[0] - center_x, 2), round(coords[1] - center_y, 2), z_values.get(tag, 0.0))
        for tag, coords in positions.items()
    }


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
    """Return the knowledge graph: nodes (tags/topics) and edges (co-occurrence + content similarity)."""
    # Get all tags for this user with note counts
    result = await db.execute(
        select(NoteTag.tag, func.count(NoteTag.note_id).label("count"))
        .join(Note)
        .where(Note.user_id == current_user.id)
        .group_by(NoteTag.tag)
        .order_by(func.count(NoteTag.note_id).desc())
    )
    tag_counts = result.all()

    if not tag_counts:
        return GraphResponse(
            nodes=[],
            edges=[],
            core_mind_note_count=0,
            layout_seed=0,
            focus_node_id=None,
        )

    # Count total notes
    note_count = await db.execute(
        select(func.count(Note.id)).where(Note.user_id == current_user.id)
    )
    total_notes = note_count.scalar() or 0

    top_tags = tag_counts[:20]
    layout_seed = _resolve_layout_seed(str(current_user.id), [tag for tag, _count in top_tags])
    top_tag_set = {tag for tag, _count in top_tags}
    tag_ids = {
        tag: str(uuid.uuid5(uuid.NAMESPACE_DNS, tag))
        for tag, _count in top_tags
    }

    # --- Co-occurrence edges (original logic) ---
    tag_co_strength: dict[tuple[str, str], int] = {}
    for tag1, _ in top_tags:
        for tag2, _ in top_tags:
            if tag1 >= tag2:
                continue
            co_result = await db.execute(
                select(func.count())
                .select_from(NoteTag.__table__)
                .where(
                    NoteTag.note_id.in_(
                        select(NoteTag.note_id).where(NoteTag.tag == tag1)
                    ),
                    NoteTag.tag == tag2,
                )
            )
            co_count = co_result.scalar() or 0
            if co_count > 0:
                tag_co_strength[(tag1, tag2)] = co_count

    # --- TF-IDF content similarity bonus ---
    tag_similarity_bonus: dict[tuple[str, str], float] = {}
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.metrics.pairwise import cosine_similarity as cos_sim

        # Fetch notes with their tags
        notes_result = await db.execute(
            select(Note.id, Note.title, Note.markdown_content)
            .where(Note.user_id == current_user.id)
        )
        all_notes = notes_result.all()

        tags_result = await db.execute(
            select(NoteTag.note_id, NoteTag.tag)
            .join(Note)
            .where(Note.user_id == current_user.id)
        )
        note_tag_map: dict[str, list[str]] = {}
        for nid, tag in tags_result.all():
            note_tag_map.setdefault(nid, []).append(tag)

        if len(all_notes) >= 2:
            docs = []
            note_ids_ordered = []
            for nid, title, content in all_notes:
                tags_str = " ".join(note_tag_map.get(nid, []))
                text = f"{title or ''} {(content or '')[:500]} {tags_str}"
                docs.append(text)
                note_ids_ordered.append(nid)

            vectorizer = TfidfVectorizer(max_features=500, stop_words="english")
            tfidf_matrix = vectorizer.fit_transform(docs)
            sim_matrix = cos_sim(tfidf_matrix)

            # For each high-similarity note pair, boost their shared tag-pair edges
            for i in range(len(note_ids_ordered)):
                tags_i = set(note_tag_map.get(note_ids_ordered[i], [])) & top_tag_set
                for j in range(i + 1, len(note_ids_ordered)):
                    sim_score = sim_matrix[i, j]
                    if sim_score < 0.15:
                        continue
                    tags_j = set(note_tag_map.get(note_ids_ordered[j], [])) & top_tag_set
                    # Create edges between all tag pairs of similar notes
                    for t1 in tags_i:
                        for t2 in tags_j:
                            if t1 == t2:
                                continue
                            pair = (min(t1, t2), max(t1, t2))
                            bonus = sim_score * 2.0
                            tag_similarity_bonus[pair] = tag_similarity_bonus.get(pair, 0) + bonus
    except Exception:
        pass  # graceful fallback if sklearn unavailable

    edge_records: list[dict[str, object]] = []
    tag_strength_map: dict[str, dict[str, float]] = {tag: {} for tag, _count in top_tags}
    degrees: dict[str, float] = {tag: 0.0 for tag, _count in top_tags}

    all_pairs = set(tag_co_strength) | set(tag_similarity_bonus)
    for tag1, tag2 in sorted(all_pairs, key=lambda item: (item[0], item[1])):
        co_count = tag_co_strength.get((tag1, tag2), 0)
        semantic_bonus = round(tag_similarity_bonus.get((tag1, tag2), 0.0), 3)
        if co_count <= 0 and semantic_bonus <= 0:
            continue

        strength = round(co_count + semantic_bonus, 2)
        relation = "hybrid"
        if co_count > 0 and semantic_bonus <= 0:
            relation = "co_occurrence"
        elif co_count <= 0 and semantic_bonus > 0:
            relation = "semantic_similarity"

        edge_record = {
            "source_tag": tag1,
            "target_tag": tag2,
            "strength": strength,
            "relation": relation,
            "co_occurrence_count": co_count,
            "content_similarity": semantic_bonus,
            "shared_note_count": co_count,
        }
        edge_records.append(edge_record)

        tag_strength_map.setdefault(tag1, {})[tag2] = strength
        tag_strength_map.setdefault(tag2, {})[tag1] = strength
        degrees[tag1] += strength
        degrees[tag2] += strength

    core_tags, cluster_by_tag, color_by_cluster = _build_cluster_assignments(
        top_tags,
        tag_strength_map,
        layout_seed,
    )
    layout_positions = _build_layout_positions(
        top_tags,
        edge_records,
        degrees,
        cluster_by_tag,
        core_tags,
        layout_seed,
    )

    max_note_count = max(count for _tag, count in top_tags)
    nodes = []
    for index, (tag, count) in enumerate(top_tags):
        cluster = cluster_by_tag.get(tag, tag if index == 0 else None)
        count_ratio = count / max(max_note_count, 1)
        size = round(1.35 + (count_ratio * 1.95), 2)
        if tag in core_tags:
            size = round(size + 0.45, 2)

        x, y, z = layout_positions.get(tag, (0.0, 0.0, 0.0))
        nodes.append(GraphNodeOut(
            id=tag_ids[tag],
            label=tag,
            note_count=count,
            size=size,
            color=color_by_cluster.get(cluster or "", "#9AA097"),
            x=x,
            y=y,
            z=z,
            rank=index + 1,
            degree=round(degrees.get(tag, 0.0), 2),
            cluster=cluster,
            is_core=tag in core_tags,
        ))

    edges = []
    for edge in sorted(edge_records, key=lambda item: (-float(item["strength"]), str(item["source_tag"]), str(item["target_tag"]))):
        edges.append(GraphEdgeOut(
            source=tag_ids[str(edge["source_tag"])],
            target=tag_ids[str(edge["target_tag"])],
            strength=float(edge["strength"]),
            relation=str(edge["relation"]),
            co_occurrence_count=int(edge["co_occurrence_count"]),
            content_similarity=float(edge["content_similarity"]),
            shared_note_count=int(edge["shared_note_count"]),
        ))

    focus_node_id = nodes[0].id if nodes else None
    return GraphResponse(
        nodes=nodes,
        edges=edges,
        core_mind_note_count=total_notes,
        layout_seed=layout_seed,
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
