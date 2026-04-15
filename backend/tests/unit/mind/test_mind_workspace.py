"""Unit tests for the Mind workspace endpoints."""
from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Note, NoteSimilarity, NoteTag, TaskStatus


pytestmark = pytest.mark.asyncio


async def _create_note(
    db: AsyncSession,
    user_id: str,
    *,
    title: str,
    content: str,
    tags: list[str],
) -> Note:
    note = Note(
        id=str(uuid.uuid4()),
        title=title,
        markdown_content=content,
        status=TaskStatus.COMPLETED,
        user_id=user_id,
        current_version=1,
    )
    db.add(note)
    for tag in tags:
        db.add(NoteTag(id=str(uuid.uuid4()), note_id=note.id, tag=tag))
    await db.commit()
    await db.refresh(note)
    return note


async def _create_similarity(db: AsyncSession, note_a_id: str, note_b_id: str, score: float) -> None:
    db.add(
        NoteSimilarity(
            id=str(uuid.uuid4()),
            note_id=note_a_id,
            similar_note_id=note_b_id,
            similarity_score=score,
        )
    )
    await db.commit()


class TestMindWorkspace:
    async def test_get_workspace_summarizes_clusters_and_prompts(
        self,
        client: AsyncClient,
        auth_headers,
        db: AsyncSession,
        test_user,
    ):
        note_a = await _create_note(
            db,
            test_user.id,
            title="Writing Draft",
            content="Outline the next essay draft.",
            tags=["writing", "draft"],
        )
        note_b = await _create_note(
            db,
            test_user.id,
            title="Writing Revision",
            content="Revise the narrative arc and pacing.",
            tags=["writing", "editing"],
        )
        note_c = await _create_note(
            db,
            test_user.id,
            title="Research Thread",
            content="Research references for the essay.",
            tags=["writing", "research"],
        )
        note_d = await _create_note(
            db,
            test_user.id,
            title="Source Notes",
            content="Collect citations and external sources.",
            tags=["research", "sources"],
        )
        await _create_note(
            db,
            test_user.id,
            title="Lonely Fragment",
            content="A note that is still floating alone.",
            tags=["solo"],
        )
        await _create_similarity(db, note_a.id, note_b.id, 0.82)
        await _create_similarity(db, note_c.id, note_d.id, 0.64)

        resp = await client.get("/api/v1/mind/workspace", headers=auth_headers)

        assert resp.status_code == 200
        data = resp.json()
        assert data["overview"]["total_notes"] == 5
        assert data["overview"]["cluster_count"] >= 2
        assert data["overview"]["orphan_note_count"] >= 1
        assert len(data["clusters"]) >= 2
        assert any(cluster["label"] == "writing" for cluster in data["clusters"])
        assert len(data["prompts"]) >= 1

    async def test_get_node_workspace_returns_related_notes_and_draft(
        self,
        client: AsyncClient,
        auth_headers,
        db: AsyncSession,
        test_user,
    ):
        note_a = await _create_note(
            db,
            test_user.id,
            title="Focus Node",
            content="This note should anchor the workspace summary.",
            tags=["writing", "draft"],
        )
        note_b = await _create_note(
            db,
            test_user.id,
            title="Neighbor Note",
            content="A neighboring note connected by theme and similarity.",
            tags=["writing", "editing"],
        )
        note_c = await _create_note(
            db,
            test_user.id,
            title="Research Node",
            content="Related research that should appear as a bridge.",
            tags=["research", "writing"],
        )
        await _create_similarity(db, note_a.id, note_b.id, 0.91)
        await _create_similarity(db, note_a.id, note_c.id, 0.55)

        resp = await client.get(f"/api/v1/mind/nodes/{note_a.id}/workspace", headers=auth_headers)

        assert resp.status_code == 200
        data = resp.json()
        assert data["node"]["id"] == note_a.id
        assert data["node"]["title"] == "Focus Node"
        assert len(data["related_notes"]) >= 2
        assert data["draft_note_title"].startswith("Theme Map")
        assert "Focus Node" in data["draft_markdown"]
        assert data["focus_summary"]

    async def test_get_node_notes_returns_cluster_peers(
        self,
        client: AsyncClient,
        auth_headers,
        db: AsyncSession,
        test_user,
    ):
        note_a = await _create_note(
            db,
            test_user.id,
            title="Theme Anchor",
            content="Anchor note.",
            tags=["writing", "draft"],
        )
        note_b = await _create_note(
            db,
            test_user.id,
            title="Theme Peer",
            content="Peer note.",
            tags=["writing", "editing"],
        )
        await _create_note(
            db,
            test_user.id,
            title="Other Cluster",
            content="Different cluster.",
            tags=["research"],
        )
        await _create_similarity(db, note_a.id, note_b.id, 0.77)

        resp = await client.get(f"/api/v1/mind/nodes/{note_a.id}/notes", headers=auth_headers)

        assert resp.status_code == 200
        data = resp.json()
        assert data["node_id"] == note_a.id
        assert data["tag"] == "writing"
        assert data["total"] >= 1
        assert any(item["id"] == note_b.id for item in data["items"])
