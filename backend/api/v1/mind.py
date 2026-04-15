from __future__ import annotations

import json
import math
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session, get_db
from app.models import MindConnection, Note, NoteTag, NoteSimilarity, User
from app.schemas import (
    GraphEdgeOut,
    GraphNodeOut,
    GraphResponse,
    MindClusterSummaryOut,
    MindNodeNoteOut,
    MindNodeNotesResponse,
    MindNodeWorkspaceNoteOut,
    MindNodeWorkspaceOut,
    MindRelatedNoteOut,
    MindSpotlightNoteOut,
    MindWorkspaceOut,
    MindWorkspaceOverviewOut,
    MindWorkspacePromptOut,
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


def _content_preview(content: str | None, max_len: int = 180) -> str:
    text = (content or "").replace("\n", " ").strip()
    if len(text) <= max_len:
        return text
    return text[:max_len].rstrip() + "…"


async def _load_mind_inputs(
    db: AsyncSession,
    user_id: str,
) -> tuple[list[Note], dict[str, list[str]], dict[tuple[str, str], float]]:
    result = await db.execute(
        select(Note).where(Note.user_id == user_id).order_by(Note.updated_at.desc())
    )
    notes = result.scalars().all()
    note_ids = [note.id for note in notes]

    note_tags: dict[str, list[str]] = {}
    if note_ids:
        tag_result = await db.execute(
            select(NoteTag.note_id, NoteTag.tag).where(NoteTag.note_id.in_(note_ids))
        )
        for note_id_value, tag in tag_result.all():
            note_tags.setdefault(note_id_value, []).append(tag)
        for tags in note_tags.values():
            tags.sort()

    sim_map: dict[tuple[str, str], float] = {}
    if note_ids:
        sim_result = await db.execute(
            select(NoteSimilarity).where(NoteSimilarity.note_id.in_(note_ids))
        )
        for similarity in sim_result.scalars().all():
            pair = (
                min(similarity.note_id, similarity.similar_note_id),
                max(similarity.note_id, similarity.similar_note_id),
            )
            sim_map[pair] = max(sim_map.get(pair, 0.0), similarity.similarity_score)

    return notes, note_tags, sim_map


def _build_graph_snapshot(
    notes: list[Note],
    note_tags: dict[str, list[str]],
    sim_map: dict[tuple[str, str], float],
) -> dict[str, object]:
    if not notes:
        return {
            "notes": [],
            "note_map": {},
            "note_tags": {},
            "note_clusters": {},
            "cluster_color_map": {},
            "cluster_members": {},
            "nodes": [],
            "node_map": {},
            "edges": [],
            "edge_index": {},
            "focus_node_id": None,
        }

    note_ids = [note.id for note in notes]

    tag_freq: dict[str, int] = {}
    for tags in note_tags.values():
        for tag in tags:
            tag_freq[tag] = tag_freq.get(tag, 0) + 1

    top_tags_sorted = sorted(tag_freq.items(), key=lambda item: (-item[1], item[0]))
    cluster_tags = [tag for tag, _ in top_tags_sorted[:len(_CLUSTER_COLORS)]]
    cluster_color_map = {
        tag: _CLUSTER_COLORS[index % len(_CLUSTER_COLORS)]
        for index, tag in enumerate(cluster_tags)
    }

    def _note_cluster(note_id: str) -> str | None:
        tags = note_tags.get(note_id, [])
        for cluster_tag in cluster_tags:
            if cluster_tag in tags:
                return cluster_tag
        return cluster_tags[0] if cluster_tags and tags else None

    note_clusters = {note_id: _note_cluster(note_id) for note_id in note_ids}

    tag_to_notes: dict[str, list[str]] = {}
    for note_id in note_ids:
        for tag in note_tags.get(note_id, []):
            tag_to_notes.setdefault(tag, []).append(note_id)

    pair_shared: dict[tuple[str, str], int] = {}
    for related_note_ids in tag_to_notes.values():
        for index in range(len(related_note_ids)):
            for neighbor_index in range(index + 1, len(related_note_ids)):
                pair = (
                    min(related_note_ids[index], related_note_ids[neighbor_index]),
                    max(related_note_ids[index], related_note_ids[neighbor_index]),
                )
                pair_shared[pair] = pair_shared.get(pair, 0) + 1

    for pair, similarity_score in sim_map.items():
        if pair not in pair_shared and similarity_score * 5 >= _EDGE_MIN_STRENGTH:
            pair_shared[pair] = 0

    edge_records: list[dict[str, object]] = []
    for pair, shared_count in pair_shared.items():
        similarity_score = sim_map.get(pair, 0.0)
        strength = shared_count * 2 + similarity_score * 5
        if strength < _EDGE_MIN_STRENGTH:
            continue
        relation = "hybrid"
        if shared_count > 0 and similarity_score <= 0:
            relation = "co_occurrence"
        elif shared_count <= 0 and similarity_score > 0:
            relation = "semantic_similarity"
        edge_records.append(
            {
                "source": pair[0],
                "target": pair[1],
                "strength": round(strength, 2),
                "relation": relation,
                "co_occurrence_count": shared_count,
                "content_similarity": round(similarity_score, 3),
                "shared_note_count": shared_count,
            }
        )

    edge_records.sort(key=lambda edge: -float(edge["strength"]))
    edge_records = edge_records[:_EDGE_MAX_COUNT]

    edge_strength: dict[str, float] = {note_id: 0.0 for note_id in note_ids}
    edge_index: dict[str, list[dict[str, object]]] = {note_id: [] for note_id in note_ids}
    for edge in edge_records:
        source = str(edge["source"])
        target = str(edge["target"])
        strength = float(edge["strength"])
        edge_strength[source] += strength
        edge_strength[target] += strength
        edge_index[source].append(edge)
        edge_index[target].append(edge)

    note_map = {note.id: note for note in notes}
    cluster_members: dict[str, list[str]] = {}
    for note_id, cluster in note_clusters.items():
        if cluster is not None:
            cluster_members.setdefault(cluster, []).append(note_id)

    total_notes = len(notes)
    sphere_radius = 180.0 + total_notes * 6.0
    golden_angle = math.pi * (3.0 - math.sqrt(5.0))
    positions: dict[str, list[float]] = {}
    for index, note_id in enumerate(note_ids):
        theta = math.acos(1.0 - 2.0 * (index + 0.5) / total_notes)
        phi = golden_angle * index
        positions[note_id] = [
            sphere_radius * math.sin(theta) * math.cos(phi),
            sphere_radius * math.cos(theta),
            sphere_radius * math.sin(theta) * math.sin(phi),
        ]

    force_index: dict[str, list[tuple[str, float]]] = {note_id: [] for note_id in note_ids}
    for edge in edge_records:
        source = str(edge["source"])
        target = str(edge["target"])
        strength = float(edge["strength"])
        force_index[source].append((target, strength))
        force_index[target].append((source, strength))

    for _iteration in range(15):
        displacements: dict[str, list[float]] = {note_id: [0.0, 0.0, 0.0] for note_id in note_ids}
        for note_id in note_ids:
            px, py, pz = positions[note_id]
            for neighbor_id, strength in force_index[note_id]:
                nx, ny, nz = positions[neighbor_id]
                dx, dy, dz = nx - px, ny - py, nz - pz
                distance = math.sqrt(dx * dx + dy * dy + dz * dz) or 1.0
                force = min(strength * 0.3, distance * 0.1)
                displacements[note_id][0] += dx / distance * force
                displacements[note_id][1] += dy / distance * force
                displacements[note_id][2] += dz / distance * force

        for note_id in note_ids:
            positions[note_id][0] += displacements[note_id][0]
            positions[note_id][1] += displacements[note_id][1]
            positions[note_id][2] += displacements[note_id][2]
            x, y, z = positions[note_id]
            magnitude = math.sqrt(x * x + y * y + z * z) or 1.0
            positions[note_id] = [
                x / magnitude * sphere_radius,
                y / magnitude * sphere_radius,
                z / magnitude * sphere_radius,
            ]

    max_tag_count = max((len(note_tags.get(note_id, [])) for note_id in note_ids), default=1) or 1
    nodes: list[GraphNodeOut] = []
    node_map: dict[str, GraphNodeOut] = {}
    for index, note in enumerate(notes):
        note_id = note.id
        tag_count = len(note_tags.get(note_id, []))
        is_core = tag_count >= _CORE_TAG_THRESHOLD
        size = round(0.8 + (tag_count / max_tag_count) * 3.2, 2)
        if is_core:
            size = round(size + 1.0, 2)
        cluster = note_clusters.get(note_id)
        color = cluster_color_map.get(cluster or "", "#9AA097")
        x, y, z = positions.get(note_id, [0.0, 0.0, 0.0])
        node = GraphNodeOut(
            id=note_id,
            label=(note.title or "Untitled")[:20],
            note_count=tag_count,
            size=size,
            color=color,
            x=round(x, 2),
            y=round(y, 2),
            z=round(z, 2),
            rank=index + 1,
            degree=round(edge_strength.get(note_id, 0.0), 2),
            cluster=cluster,
            is_core=is_core,
        )
        nodes.append(node)
        node_map[note_id] = node

    edges = [
        GraphEdgeOut(
            source=str(edge["source"]),
            target=str(edge["target"]),
            strength=float(edge["strength"]),
            relation=str(edge["relation"]),
            co_occurrence_count=int(edge["co_occurrence_count"]),
            content_similarity=float(edge["content_similarity"]),
            shared_note_count=int(edge["shared_note_count"]),
        )
        for edge in edge_records
    ]

    focus_node_id = max(nodes, key=lambda node: (node.degree, node.note_count)).id if nodes else None
    return {
        "notes": notes,
        "note_map": note_map,
        "note_tags": note_tags,
        "note_clusters": note_clusters,
        "cluster_color_map": cluster_color_map,
        "cluster_members": cluster_members,
        "nodes": nodes,
        "node_map": node_map,
        "edges": edges,
        "edge_index": edge_index,
        "focus_node_id": focus_node_id,
    }


def _build_spotlight_note(
    note: Note,
    tags: list[str],
    cluster: str | None,
    degree: float,
    connection_count: int,
) -> MindSpotlightNoteOut:
    return MindSpotlightNoteOut(
        id=note.id,
        title=note.title,
        snippet=_content_preview(note.markdown_content, max_len=140),
        tags=tags,
        cluster=cluster,
        degree=round(degree, 2),
        connection_count=connection_count,
        created_at=note.created_at,
        updated_at=note.updated_at,
    )


def _build_workspace_prompts(
    *,
    densest_cluster: str | None,
    densest_cluster_note_count: int,
    bridge_note: MindSpotlightNoteOut | None,
    orphan_note_count: int,
) -> list[MindWorkspacePromptOut]:
    prompts: list[MindWorkspacePromptOut] = []
    if densest_cluster:
        prompts.append(
            MindWorkspacePromptOut(
                id="densest-cluster",
                title=f"Consolidate {densest_cluster}",
                description=f"{densest_cluster_note_count} notes are already orbiting this theme — it is ready for a synthesis pass.",
                target_cluster=densest_cluster,
            )
        )
    if bridge_note:
        prompts.append(
            MindWorkspacePromptOut(
                id="bridge-note",
                title=f"Review bridge note: {bridge_note.title}",
                description="This note is connecting multiple themes. It is a strong candidate for a summary, tag cleanup, or an Insight trigger.",
                target_node_id=bridge_note.id,
                target_cluster=bridge_note.cluster,
            )
        )
    if orphan_note_count > 0:
        prompts.append(
            MindWorkspacePromptOut(
                id="orphan-notes",
                title="Untangle isolated notes",
                description=f"{orphan_note_count} notes are still sitting alone. Add tags or connect them to an existing theme to grow your map.",
            )
        )
    return prompts

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
    notes, note_tags, sim_map = await _load_mind_inputs(db, current_user.id)
    if not notes:
        return GraphResponse(nodes=[], edges=[], core_mind_note_count=0, layout_seed=0, focus_node_id=None)
    snapshot = _build_graph_snapshot(notes, note_tags, sim_map)
    return GraphResponse(
        nodes=snapshot["nodes"],
        edges=snapshot["edges"],
        core_mind_note_count=len(notes),
        layout_seed=0,
        focus_node_id=snapshot["focus_node_id"],
    )


@router.get("/workspace", response_model=MindWorkspaceOut)
async def get_workspace(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    notes, note_tags, sim_map = await _load_mind_inputs(db, current_user.id)
    snapshot = _build_graph_snapshot(notes, note_tags, sim_map)

    node_map: dict[str, GraphNodeOut] = snapshot["node_map"]
    edge_index: dict[str, list[dict[str, object]]] = snapshot["edge_index"]
    note_map: dict[str, Note] = snapshot["note_map"]
    note_clusters: dict[str, str | None] = snapshot["note_clusters"]
    cluster_members: dict[str, list[str]] = snapshot["cluster_members"]
    cluster_color_map: dict[str, str] = snapshot["cluster_color_map"]

    if not notes:
        return MindWorkspaceOut(
            overview=MindWorkspaceOverviewOut(
                total_notes=0,
                cluster_count=0,
                connected_note_count=0,
                orphan_note_count=0,
                bridge_note_count=0,
            ),
            clusters=[],
            bridge_notes=[],
            orphan_notes=[],
            prompts=[],
        )

    clusters: list[MindClusterSummaryOut] = []
    densest_cluster: str | None = None
    densest_cluster_note_count = 0
    for cluster, member_ids in sorted(
        cluster_members.items(),
        key=lambda item: (-len(item[1]), item[0]),
    ):
        member_nodes = [node_map[note_id] for note_id in member_ids if note_id in node_map]
        member_notes = [note_map[note_id] for note_id in member_ids if note_id in note_map]
        member_notes.sort(key=lambda note: note.updated_at, reverse=True)
        spotlight_node = max(member_nodes, key=lambda node: (node.degree, node.note_count), default=None)
        average_degree = (
            round(sum(node.degree for node in member_nodes) / len(member_nodes), 2)
            if member_nodes
            else 0.0
        )
        clusters.append(
            MindClusterSummaryOut(
                id=cluster,
                label=cluster,
                color=cluster_color_map.get(cluster, "#9AA097"),
                note_count=len(member_ids),
                core_note_count=sum(1 for node in member_nodes if node.is_core),
                average_degree=average_degree,
                recent_titles=[note.title for note in member_notes[:3]],
                spotlight_note_id=spotlight_node.id if spotlight_node else None,
                spotlight_title=spotlight_node.label if spotlight_node else None,
            )
        )
        if len(member_ids) > densest_cluster_note_count:
            densest_cluster = cluster
            densest_cluster_note_count = len(member_ids)

    bridge_candidates: list[tuple[MindSpotlightNoteOut, int]] = []
    orphan_notes: list[MindSpotlightNoteOut] = []
    for note in notes:
        related_edges = edge_index.get(note.id, [])
        node = node_map.get(note.id)
        note_cluster = note_clusters.get(note.id)
        neighbor_clusters = {
            note_clusters.get(
                str(edge["target"]) if str(edge["source"]) == note.id else str(edge["source"])
            )
            for edge in related_edges
        }
        neighbor_clusters.discard(None)
        if note_cluster in neighbor_clusters:
            neighbor_clusters.discard(note_cluster)

        spotlight = _build_spotlight_note(
            note,
            note_tags.get(note.id, []),
            note_cluster,
            node.degree if node else 0.0,
            len(related_edges),
        )
        if not related_edges:
            orphan_notes.append(spotlight)
        if len(neighbor_clusters) >= 1:
            bridge_candidates.append((spotlight, len(neighbor_clusters)))

    bridge_candidates.sort(
        key=lambda item: (-item[1], -item[0].degree, item[0].title.lower()),
    )
    bridge_notes = [item[0] for item in bridge_candidates[:3]]
    orphan_notes.sort(key=lambda note: note.updated_at, reverse=True)
    orphan_notes = orphan_notes[:3]

    prompts = _build_workspace_prompts(
        densest_cluster=densest_cluster,
        densest_cluster_note_count=densest_cluster_note_count,
        bridge_note=bridge_notes[0] if bridge_notes else None,
        orphan_note_count=len([note for note in notes if not edge_index.get(note.id)]),
    )

    connected_note_count = sum(1 for note in notes if edge_index.get(note.id))
    return MindWorkspaceOut(
        overview=MindWorkspaceOverviewOut(
            total_notes=len(notes),
            cluster_count=len(clusters),
            connected_note_count=connected_note_count,
            orphan_note_count=len([note for note in notes if not edge_index.get(note.id)]),
            bridge_note_count=len(bridge_candidates),
        ),
        clusters=clusters,
        bridge_notes=bridge_notes,
        orphan_notes=orphan_notes,
        prompts=prompts,
    )


@router.get("/nodes/{node_id}/workspace", response_model=MindNodeWorkspaceOut)
async def get_node_workspace(
    node_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    notes, note_tags, sim_map = await _load_mind_inputs(db, current_user.id)
    snapshot = _build_graph_snapshot(notes, note_tags, sim_map)

    note_map: dict[str, Note] = snapshot["note_map"]
    node_map: dict[str, GraphNodeOut] = snapshot["node_map"]
    edge_index: dict[str, list[dict[str, object]]] = snapshot["edge_index"]
    note_clusters: dict[str, str | None] = snapshot["note_clusters"]
    cluster_members: dict[str, list[str]] = snapshot["cluster_members"]
    cluster_color_map: dict[str, str] = snapshot["cluster_color_map"]

    note = note_map.get(node_id)
    node = node_map.get(node_id)
    if not note or not node:
        raise HTTPException(status_code=404, detail={"error": {"code": "NODE_NOT_FOUND", "message": "Node not found"}})

    related_notes: list[MindRelatedNoteOut] = []
    bridge_clusters = set()
    for edge in sorted(edge_index.get(node_id, []), key=lambda item: -float(item["strength"]))[:6]:
        other_id = str(edge["target"]) if str(edge["source"]) == node_id else str(edge["source"])
        other_note = note_map.get(other_id)
        other_node = node_map.get(other_id)
        if not other_note or not other_node:
            continue

        other_cluster = note_clusters.get(other_id)
        selected_cluster = note_clusters.get(node_id)
        if other_cluster and other_cluster != selected_cluster:
            bridge_clusters.add(other_cluster)

        shared_tags = sorted(set(note_tags.get(node_id, [])) & set(note_tags.get(other_id, [])))
        related_notes.append(
            MindRelatedNoteOut(
                id=other_note.id,
                title=other_note.title,
                snippet=_content_preview(other_note.markdown_content, max_len=170),
                tags=note_tags.get(other_id, []),
                cluster=other_cluster,
                relation=str(edge["relation"]),
                strength=float(edge["strength"]),
                shared_tags=shared_tags,
                content_similarity=float(edge["content_similarity"]),
                updated_at=other_note.updated_at,
            )
        )

    cluster_note_ids = [
        member_id
        for member_id in cluster_members.get(note_clusters.get(node_id) or "", [])
        if member_id != node_id
    ]
    cluster_note_ids.sort(
        key=lambda member_id: (
            node_map.get(member_id).degree if node_map.get(member_id) else 0.0,
            note_map.get(member_id).updated_at if note_map.get(member_id) else datetime.min.replace(tzinfo=timezone.utc),
        ),
        reverse=True,
    )
    cluster_notes = [
        _build_spotlight_note(
            note_map[member_id],
            note_tags.get(member_id, []),
            note_clusters.get(member_id),
            node_map.get(member_id).degree if node_map.get(member_id) else 0.0,
            len(edge_index.get(member_id, [])),
        )
        for member_id in cluster_note_ids[:5]
        if member_id in note_map
    ]

    if related_notes:
        if bridge_clusters:
            focus_summary = (
                f"This note anchors {note_clusters.get(node_id) or 'an emerging theme'} while also bridging "
                f"{', '.join(sorted(bridge_clusters))}. It is a strong node for synthesis and cross-linking."
            )
        else:
            focus_summary = (
                f"This note sits inside {note_clusters.get(node_id) or 'a developing theme'} with "
                f"{len(related_notes)} strong nearby notes. It is ready to be consolidated into a clearer thread."
            )
    else:
        focus_summary = (
            "This note is still isolated in your map. Add stronger tags or connect it to a neighboring idea to help it find a theme."
        )

    draft_heading = note_clusters.get(node_id) or note.title
    draft_note_title = f"Theme Map — {draft_heading}"
    related_markdown = "\n".join(
        f"- **{related.title}** — {related.relation.replace('_', ' ')} · strength {related.strength:.1f}"
        for related in related_notes[:4]
    ) or "- No strong neighboring notes yet."
    cluster_markdown = "\n".join(
        f"- {cluster_note.title}"
        for cluster_note in cluster_notes[:4]
    ) or "- This theme only contains the focus note for now."
    draft_markdown = (
        f"# {draft_note_title}\n\n"
        f"## Focus note\n"
        f"- **{note.title}**\n"
        f"- Theme: {note_clusters.get(node_id) or 'Unclustered'}\n"
        f"- Connections: {len(edge_index.get(node_id, []))}\n\n"
        f"## Why this node matters\n"
        f"{focus_summary}\n\n"
        f"## Nearby notes\n"
        f"{related_markdown}\n\n"
        f"## Cluster threads\n"
        f"{cluster_markdown}\n\n"
        f"## Next moves\n"
        f"- Clarify the central question behind this theme.\n"
        f"- Merge overlapping notes into one stronger synthesis.\n"
        f"- Decide whether this thread should become an Insight or a Ground post.\n"
    )

    return MindNodeWorkspaceOut(
        node=MindNodeWorkspaceNoteOut(
            id=note.id,
            title=note.title,
            snippet=_content_preview(note.markdown_content, max_len=280),
            tags=note_tags.get(node_id, []),
            cluster=note_clusters.get(node_id),
            color=cluster_color_map.get(note_clusters.get(node_id) or "", "#9AA097"),
            is_core=node.is_core,
            degree=node.degree,
            connection_count=len(edge_index.get(node_id, [])),
            bridge_clusters=sorted(bridge_clusters),
            created_at=note.created_at,
            updated_at=note.updated_at,
        ),
        related_notes=related_notes,
        cluster_notes=cluster_notes,
        focus_summary=focus_summary,
        draft_note_title=draft_note_title,
        draft_markdown=draft_markdown,
    )


@router.get("/nodes/{node_id}/notes", response_model=MindNodeNotesResponse)
async def get_node_notes(
    node_id: str,
    page: int = 1,
    page_size: int = 20,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return peer notes from the same dominant cluster as the selected graph node."""
    notes, note_tags, sim_map = await _load_mind_inputs(db, current_user.id)
    snapshot = _build_graph_snapshot(notes, note_tags, sim_map)
    note_map: dict[str, Note] = snapshot["note_map"]
    note_clusters: dict[str, str | None] = snapshot["note_clusters"]
    node_map: dict[str, GraphNodeOut] = snapshot["node_map"]
    cluster_members: dict[str, list[str]] = snapshot["cluster_members"]

    if node_id not in note_map:
        raise HTTPException(status_code=404, detail={"error": {"code": "NODE_NOT_FOUND", "message": "Node not found"}})

    cluster = note_clusters.get(node_id)
    member_ids = [member_id for member_id in cluster_members.get(cluster or "", []) if member_id != node_id]
    member_ids.sort(
        key=lambda member_id: (
            node_map.get(member_id).degree if node_map.get(member_id) else 0.0,
            note_map.get(member_id).updated_at if note_map.get(member_id) else datetime.min.replace(tzinfo=timezone.utc),
        ),
        reverse=True,
    )

    total = len(member_ids)
    start = (page - 1) * page_size
    selected_ids = member_ids[start : start + page_size]
    return MindNodeNotesResponse(
        node_id=node_id,
        tag=cluster or "",
        total=total,
        page=page,
        page_size=page_size,
        items=[
            MindNodeNoteOut(
                id=note_map[selected_id].id,
                title=note_map[selected_id].title,
                status=note_map[selected_id].status.value,
                tags=note_tags.get(selected_id, []),
                created_at=note_map[selected_id].created_at,
                updated_at=note_map[selected_id].updated_at,
                snippet=_content_preview(note_map[selected_id].markdown_content, max_len=180),
            )
            for selected_id in selected_ids
            if selected_id in note_map
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

            # Notify user about new connections (pick the top one)
            if connections:
                from app.notifications.triggers import notify_mind_connection
                top = connections[0]
                note_a = await db.get(Note, top["note_a_id"])
                note_b = await db.get(Note, top["note_b_id"])
                title_a = (note_a.title if note_a else "笔记")[:15]
                title_b = (note_b.title if note_b else "笔记")[:15]
                await notify_mind_connection(db, user_id, title_a, title_b)
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
