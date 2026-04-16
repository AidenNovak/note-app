"""Graph-based community detection for insight clustering.

Uses networkx Louvain algorithm on the MindConnection graph to discover
thematic note clusters. These clusters feed into the angle-discovery LLM
call that selects 3-5 insight report topics.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

import networkx as nx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import MindConnection, Note

logger = logging.getLogger(__name__)

# Minimum edge weight to include in the graph
_MIN_EDGE_WEIGHT = 1.0
# Max clusters to return (merge smallest if exceeded)
_MAX_CLUSTERS = 8
# Min notes in a cluster to be considered standalone
_MIN_CLUSTER_SIZE = 3


@dataclass
class NoteCluster:
    """A community of related notes discovered via graph clustering."""
    cluster_id: int
    note_ids: list[str]
    shared_tags: list[str] = field(default_factory=list)
    internal_connection_ids: list[str] = field(default_factory=list)
    avg_similarity: float = 0.0


async def fetch_graph_data(
    db: AsyncSession, user_id: str
) -> tuple[list[dict], list[MindConnection], dict[str, list[str]]]:
    """Fetch notes, connections, and tags for graph construction."""
    # Fetch all notes with tags eagerly loaded
    result = await db.execute(
        select(Note)
        .options(selectinload(Note.tags))
        .where(Note.user_id == user_id)
        .order_by(Note.updated_at.desc())
    )
    notes_raw = result.scalars().all()

    note_ids = [n.id for n in notes_raw]
    if not note_ids:
        return [], [], {}

    # Build tag map from eagerly loaded tags
    note_tags: dict[str, list[str]] = {}
    for n in notes_raw:
        note_tags[n.id] = [t.tag for t in n.tags]

    # Fetch all MindConnections
    conn_result = await db.execute(
        select(MindConnection)
        .where(MindConnection.user_id == user_id)
    )
    connections = conn_result.scalars().all()

    # Build note dicts
    notes = []
    for n in notes_raw:
        notes.append({
            "id": n.id,
            "title": n.title or "(untitled)",
            "tags": note_tags.get(n.id, []),
            "content": n.markdown_content or "",
            "word_count": len((n.markdown_content or "").split()),
            "created_at": n.created_at.isoformat() if n.created_at else "",
        })

    return notes, connections, note_tags


def build_graph(connections: list[MindConnection]) -> nx.Graph:
    """Build a weighted undirected graph from MindConnections."""
    G = nx.Graph()

    for conn in connections:
        shared = json.loads(conn.shared_tags) if conn.shared_tags else []
        shared_count = len(shared)
        sim = conn.similarity_score or 0.0
        # Same formula as mind.py edge strength
        weight = shared_count * 2 + sim * 5
        if weight < _MIN_EDGE_WEIGHT:
            continue

        if G.has_edge(conn.note_a_id, conn.note_b_id):
            G[conn.note_a_id][conn.note_b_id]["weight"] = max(
                G[conn.note_a_id][conn.note_b_id]["weight"], weight
            )
        else:
            G.add_edge(
                conn.note_a_id, conn.note_b_id,
                weight=weight,
                connection_id=conn.id,
                shared_tags=shared,
                similarity=sim,
            )

    return G


def detect_communities(
    G: nx.Graph,
    all_note_ids: set[str],
    note_tags: dict[str, list[str]],
    connections: list[MindConnection],
) -> list[NoteCluster]:
    """Run Louvain community detection and return NoteCluster list."""
    if G.number_of_nodes() < 2:
        # Fallback: single cluster with all notes
        if all_note_ids:
            return [NoteCluster(
                cluster_id=0,
                note_ids=list(all_note_ids),
                shared_tags=_top_tags(all_note_ids, note_tags),
            )]
        return []

    # Louvain community detection
    communities = nx.community.louvain_communities(G, weight="weight", resolution=1.0)
    clusters: list[NoteCluster] = []

    # Build connection lookup for internal connections
    conn_lookup: dict[tuple[str, str], str] = {}
    for c in connections:
        pair = (min(c.note_a_id, c.note_b_id), max(c.note_a_id, c.note_b_id))
        conn_lookup[pair] = c.id

    for idx, community_set in enumerate(communities):
        nids = list(community_set)
        # Collect internal connection IDs
        internal_conns = []
        total_sim = 0.0
        sim_count = 0
        for i, a in enumerate(nids):
            for b in nids[i + 1:]:
                pair = (min(a, b), max(a, b))
                cid = conn_lookup.get(pair)
                if cid:
                    internal_conns.append(cid)
                if G.has_edge(a, b):
                    total_sim += G[a][b].get("similarity", 0.0)
                    sim_count += 1

        clusters.append(NoteCluster(
            cluster_id=idx,
            note_ids=nids,
            shared_tags=_top_tags(set(nids), note_tags),
            internal_connection_ids=internal_conns,
            avg_similarity=round(total_sim / sim_count, 3) if sim_count else 0.0,
        ))

    # Handle isolated notes (in all_note_ids but not in graph)
    graph_nodes = set(G.nodes())
    isolated = all_note_ids - graph_nodes
    if isolated:
        clusters.append(NoteCluster(
            cluster_id=len(clusters),
            note_ids=list(isolated),
            shared_tags=_top_tags(isolated, note_tags),
            avg_similarity=0.0,
        ))

    # Sort by size descending
    clusters.sort(key=lambda c: len(c.note_ids), reverse=True)

    # Merge tiny clusters into nearest larger cluster
    if len(clusters) > _MAX_CLUSTERS:
        clusters = _merge_small_clusters(clusters, G)

    return clusters


def _top_tags(
    note_ids: set[str], note_tags: dict[str, list[str]], top_n: int = 5
) -> list[str]:
    """Get the most frequent tags across a set of notes."""
    freq: dict[str, int] = {}
    for nid in note_ids:
        for t in note_tags.get(nid, []):
            freq[t] = freq.get(t, 0) + 1
    return [t for t, _ in sorted(freq.items(), key=lambda x: -x[1])[:top_n]]


def _merge_small_clusters(
    clusters: list[NoteCluster], G: nx.Graph
) -> list[NoteCluster]:
    """Merge small clusters into larger ones until we have <= _MAX_CLUSTERS."""
    while len(clusters) > _MAX_CLUSTERS:
        # Find the smallest cluster
        smallest = min(clusters, key=lambda c: len(c.note_ids))
        clusters.remove(smallest)

        # Find the cluster with most connections to smallest
        best_target = None
        best_score = -1
        for other in clusters:
            score = sum(
                1 for nid in smallest.note_ids
                for oid in other.note_ids
                if G.has_edge(nid, oid)
            )
            if score > best_score:
                best_score = score
                best_target = other

        if best_target:
            best_target.note_ids.extend(smallest.note_ids)
            best_target.internal_connection_ids.extend(smallest.internal_connection_ids)
            # Merge tags
            tag_set = set(best_target.shared_tags + smallest.shared_tags)
            best_target.shared_tags = list(tag_set)[:5]
        else:
            # No connections at all — just append to largest
            clusters[0].note_ids.extend(smallest.note_ids)

    return clusters


async def cluster_notes(
    db: AsyncSession, user_id: str
) -> tuple[list[NoteCluster], list[dict], dict[str, list[str]]]:
    """Main entry: fetch graph data, build graph, detect communities.

    Returns (clusters, all_notes, note_tags).
    """
    import asyncio

    notes, connections, note_tags = await fetch_graph_data(db, user_id)
    if not notes:
        return [], [], {}

    all_note_ids = {n["id"] for n in notes}
    # Run CPU-intensive graph operations in a thread to avoid blocking the event loop
    G = await asyncio.to_thread(build_graph, connections)

    logger.info(
        "Graph built: %d nodes, %d edges from %d connections (%d total notes)",
        G.number_of_nodes(), G.number_of_edges(), len(connections), len(notes),
    )

    clusters = await asyncio.to_thread(
        detect_communities, G, all_note_ids, note_tags, connections
    )

    logger.info(
        "Detected %d communities: %s",
        len(clusters),
        [(c.cluster_id, len(c.note_ids), c.shared_tags[:3]) for c in clusters],
    )

    return clusters, notes, note_tags
