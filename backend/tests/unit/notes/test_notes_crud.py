"""Unit tests for Notes CRUD API (Phase C4)."""
from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Folder, Note, NoteTag, NoteVersion, TaskStatus, AIStatus, MetadataSource


pytestmark = pytest.mark.asyncio


# ── Helpers ───────────────────────────────────────────

async def _create_folder(db: AsyncSession, user_id: str, name: str = "Test Folder") -> Folder:
    folder = Folder(id=str(uuid.uuid4()), name=name, user_id=user_id)
    db.add(folder)
    await db.commit()
    await db.refresh(folder)
    return folder


async def _create_note(
    db: AsyncSession,
    user_id: str,
    *,
    title: str = "Test Note",
    content: str = "Hello world",
    folder_id: str | None = None,
    tags: list[str] | None = None,
) -> Note:
    note_id = str(uuid.uuid4())
    note = Note(
        id=note_id,
        title=title,
        markdown_content=content,
        status=TaskStatus.COMPLETED,
        user_id=user_id,
        folder_id=folder_id,
        current_version=1,
    )
    db.add(note)
    if tags:
        for t in tags:
            db.add(NoteTag(id=str(uuid.uuid4()), note_id=note_id, tag=t))
    db.add(NoteVersion(
        id=str(uuid.uuid4()),
        note_id=note_id,
        version=1,
        summary="Initial",
        title=title,
        markdown_content=content,
    ))
    await db.commit()
    await db.refresh(note)
    return note


# ── Create ────────────────────────────────────────────

class TestCreateNote:
    async def test_create_minimal(self, client: AsyncClient, auth_headers, mocker):
        mocker.patch("api.v1.notes._background_embed")
        mocker.patch("api.v1.notes._background_ai_tag")
        resp = await client.post("/api/v1/notes", json={}, headers=auth_headers)
        assert resp.status_code == 201
        data = resp.json()
        assert data["title"]  # system assigns default title
        assert data["id"]
        assert data["status"] == "completed"

    async def test_create_with_content(self, client: AsyncClient, auth_headers, mocker):
        mocker.patch("api.v1.notes._background_embed")
        mocker.patch("api.v1.notes._background_ai_tag")
        resp = await client.post(
            "/api/v1/notes",
            json={"title": "My Note", "markdown_content": "# Hello\nWorld"},
            headers=auth_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["title"] == "My Note"
        assert data["title_source"] == "human"

    async def test_create_with_tags(self, client: AsyncClient, auth_headers, mocker):
        mocker.patch("api.v1.notes._background_embed")
        mocker.patch("api.v1.notes._background_ai_tag")
        resp = await client.post(
            "/api/v1/notes",
            json={"title": "Tagged", "tags": ["python", "FastAPI"]},
            headers=auth_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert "fastapi" in data["tags"]
        assert "python" in data["tags"]
        assert data["tag_source"] == "human"

    async def test_create_with_folder(self, client: AsyncClient, auth_headers, db, test_user, mocker):
        mocker.patch("api.v1.notes._background_embed")
        mocker.patch("api.v1.notes._background_ai_tag")
        folder = await _create_folder(db, test_user.id)
        resp = await client.post(
            "/api/v1/notes",
            json={"title": "In folder", "folder_id": folder.id},
            headers=auth_headers,
        )
        assert resp.status_code == 201
        assert resp.json()["folder_id"] == folder.id

    async def test_create_invalid_folder(self, client: AsyncClient, auth_headers, mocker):
        mocker.patch("api.v1.notes._background_embed")
        mocker.patch("api.v1.notes._background_ai_tag")
        resp = await client.post(
            "/api/v1/notes",
            json={"title": "Bad folder", "folder_id": "nonexistent"},
            headers=auth_headers,
        )
        assert resp.status_code == 404

    async def test_create_unauthenticated(self, client: AsyncClient):
        resp = await client.post("/api/v1/notes", json={"title": "Nope"})
        assert resp.status_code == 401

    async def test_create_generates_version(self, client: AsyncClient, auth_headers, db, mocker):
        mocker.patch("api.v1.notes._background_embed")
        mocker.patch("api.v1.notes._background_ai_tag")
        resp = await client.post(
            "/api/v1/notes",
            json={"title": "Versioned", "markdown_content": "Content"},
            headers=auth_headers,
        )
        assert resp.status_code == 201
        note_id = resp.json()["id"]
        # Verify version was created
        from sqlalchemy import select
        result = await db.execute(select(NoteVersion).where(NoteVersion.note_id == note_id))
        versions = result.scalars().all()
        assert len(versions) == 1
        assert versions[0].version == 1
        assert versions[0].summary == "Initial capture"


# ── Read ──────────────────────────────────────────────

class TestGetNote:
    async def test_get_note(self, client: AsyncClient, auth_headers, db, test_user):
        note = await _create_note(db, test_user.id, title="Detail Note", content="Detailed content")
        resp = await client.get(f"/api/v1/notes/{note.id}", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "Detail Note"
        assert data["markdown_content"] == "Detailed content"
        assert data["current_version"] == 1
        assert "attachments" in data

    async def test_get_note_not_found(self, client: AsyncClient, auth_headers):
        resp = await client.get("/api/v1/notes/nonexistent", headers=auth_headers)
        assert resp.status_code == 404

    async def test_get_other_users_note(self, client: AsyncClient, auth_headers, db, second_user):
        note = await _create_note(db, second_user.id, title="Secret")
        resp = await client.get(f"/api/v1/notes/{note.id}", headers=auth_headers)
        assert resp.status_code == 404  # should not see other user's note


# ── List ──────────────────────────────────────────────

class TestListNotes:
    async def test_list_empty(self, client: AsyncClient, auth_headers):
        resp = await client.get("/api/v1/notes", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["items"] == []

    async def test_list_with_notes(self, client: AsyncClient, auth_headers, db, test_user):
        await _create_note(db, test_user.id, title="Note A")
        await _create_note(db, test_user.id, title="Note B")
        resp = await client.get("/api/v1/notes", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2

    async def test_list_pagination(self, client: AsyncClient, auth_headers, db, test_user):
        for i in range(5):
            await _create_note(db, test_user.id, title=f"Note {i}")
        resp = await client.get("/api/v1/notes?page=1&page_size=2", headers=auth_headers)
        data = resp.json()
        assert data["total"] == 5
        assert len(data["items"]) == 2
        assert data["page"] == 1
        assert data["page_size"] == 2

    async def test_list_filter_by_folder(self, client: AsyncClient, auth_headers, db, test_user):
        folder = await _create_folder(db, test_user.id)
        await _create_note(db, test_user.id, title="In folder", folder_id=folder.id)
        await _create_note(db, test_user.id, title="No folder")
        resp = await client.get(f"/api/v1/notes?folder_id={folder.id}", headers=auth_headers)
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["title"] == "In folder"

    async def test_list_filter_by_tag(self, client: AsyncClient, auth_headers, db, test_user):
        await _create_note(db, test_user.id, title="Tagged", tags=["python"])
        await _create_note(db, test_user.id, title="Untagged")
        resp = await client.get("/api/v1/notes?tag=python", headers=auth_headers)
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["title"] == "Tagged"

    async def test_list_search_keyword(self, client: AsyncClient, auth_headers, db, test_user):
        await _create_note(db, test_user.id, title="Python Guide", content="Learn python basics")
        await _create_note(db, test_user.id, title="Cooking", content="Make pasta")
        resp = await client.get("/api/v1/notes?keyword=python", headers=auth_headers)
        data = resp.json()
        assert data["total"] == 1

    async def test_list_isolates_users(self, client: AsyncClient, auth_headers, db, test_user, second_user):
        await _create_note(db, test_user.id, title="My note")
        await _create_note(db, second_user.id, title="Their note")
        resp = await client.get("/api/v1/notes", headers=auth_headers)
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["title"] == "My note"


# ── Update ────────────────────────────────────────────

def _mock_ai(mocker):
    """Mock background tasks and AI metadata generation to prevent real API calls."""
    mocker.patch("api.v1.notes._background_embed")
    mocker.patch("api.v1.notes._background_ai_tag")
    mocker.patch(
        "app.note_collaboration._generate_metadata",
        return_value={"title": "AI Title", "tags": ["ai-tag"]},
    )


class TestUpdateNote:
    async def test_update_title(self, client: AsyncClient, auth_headers, db, test_user, mocker):
        _mock_ai(mocker)
        note = await _create_note(db, test_user.id, title="Old Title", tags=["keep"])
        resp = await client.patch(
            f"/api/v1/notes/{note.id}",
            json={"title": "New Title"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["title"] == "New Title"

    async def test_update_content_creates_version(self, client: AsyncClient, auth_headers, db, test_user, mocker):
        _mock_ai(mocker)
        note = await _create_note(db, test_user.id, content="v1", tags=["keep"])
        resp = await client.patch(
            f"/api/v1/notes/{note.id}",
            json={"markdown_content": "v2 content"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        from sqlalchemy import select
        result = await db.execute(
            select(NoteVersion).where(NoteVersion.note_id == note.id).order_by(NoteVersion.version.desc())
        )
        latest = result.scalars().first()
        assert latest.version == 2
        assert latest.markdown_content == "v2 content"

    async def test_update_tags(self, client: AsyncClient, auth_headers, db, test_user, mocker):
        _mock_ai(mocker)
        note = await _create_note(db, test_user.id, tags=["old"])
        resp = await client.patch(
            f"/api/v1/notes/{note.id}",
            json={"tags": ["new", "updated"]},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        # Verify via DB directly (shared session identity map may show stale relationship)
        from sqlalchemy import select
        result = await db.execute(select(NoteTag).where(NoteTag.note_id == note.id))
        db_tags = sorted(t.tag for t in result.scalars().all())
        assert db_tags == ["new", "updated"]

    async def test_update_move_to_folder(self, client: AsyncClient, auth_headers, db, test_user, mocker):
        _mock_ai(mocker)
        note = await _create_note(db, test_user.id, tags=["keep"])
        folder = await _create_folder(db, test_user.id)
        resp = await client.patch(
            f"/api/v1/notes/{note.id}",
            json={"folder_id": folder.id},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["folder_id"] == folder.id

    async def test_update_not_found(self, client: AsyncClient, auth_headers):
        resp = await client.patch(
            "/api/v1/notes/nonexistent",
            json={"title": "Nope"},
            headers=auth_headers,
        )
        assert resp.status_code == 404

    async def test_update_other_users_note(self, client: AsyncClient, auth_headers, db, second_user, mocker):
        _mock_ai(mocker)
        note = await _create_note(db, second_user.id)
        resp = await client.patch(
            f"/api/v1/notes/{note.id}",
            json={"title": "Hacked"},
            headers=auth_headers,
        )
        assert resp.status_code == 404

    async def test_put_also_works(self, client: AsyncClient, auth_headers, db, test_user, mocker):
        _mock_ai(mocker)
        note = await _create_note(db, test_user.id, title="Via PUT", tags=["keep"])
        resp = await client.put(
            f"/api/v1/notes/{note.id}",
            json={"title": "Updated via PUT"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["title"] == "Updated via PUT"


# ── Delete ────────────────────────────────────────────

class TestDeleteNote:
    async def test_delete_success(self, client: AsyncClient, auth_headers, db, test_user):
        note = await _create_note(db, test_user.id)
        resp = await client.delete(f"/api/v1/notes/{note.id}", headers=auth_headers)
        assert resp.status_code == 204

        # Verify gone
        resp2 = await client.get(f"/api/v1/notes/{note.id}", headers=auth_headers)
        assert resp2.status_code == 404

    async def test_delete_not_found(self, client: AsyncClient, auth_headers):
        resp = await client.delete("/api/v1/notes/nonexistent", headers=auth_headers)
        assert resp.status_code == 404

    async def test_delete_other_users_note(self, client: AsyncClient, auth_headers, db, second_user):
        note = await _create_note(db, second_user.id)
        resp = await client.delete(f"/api/v1/notes/{note.id}", headers=auth_headers)
        assert resp.status_code == 404
