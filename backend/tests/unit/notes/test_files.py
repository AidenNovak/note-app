"""Unit tests for Files API (Phase C4)."""
from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import File, Note, TaskStatus


pytestmark = pytest.mark.asyncio


# ── Helpers ───────────────────────────────────────────

async def _create_note(db: AsyncSession, user_id: str, title: str = "Test Note") -> Note:
    note = Note(
        id=str(uuid.uuid4()),
        title=title,
        markdown_content="content",
        status=TaskStatus.COMPLETED,
        user_id=user_id,
        current_version=1,
    )
    db.add(note)
    await db.commit()
    await db.refresh(note)
    return note


async def _create_file(
    db: AsyncSession,
    user_id: str,
    *,
    filename: str = "test.png",
    mime_type: str = "image/png",
    size: int = 1024,
    storage_path: str = "uploads/test.png",
    note_id: str | None = None,
) -> File:
    f = File(
        id=str(uuid.uuid4()),
        filename=filename,
        mime_type=mime_type,
        size=size,
        storage_path=storage_path,
        user_id=user_id,
        note_id=note_id,
    )
    db.add(f)
    await db.commit()
    await db.refresh(f)
    return f


# ── Register ──────────────────────────────────────────

class TestRegisterFile:
    async def test_register_success(self, client: AsyncClient, auth_headers):
        resp = await client.post(
            "/api/v1/files/register",
            json={"key": "uploads/photo.jpg", "filename": "photo.jpg", "content_type": "image/jpeg", "size": 2048},
            headers=auth_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["filename"] == "photo.jpg"
        assert data["mime_type"] == "image/jpeg"
        assert data["size"] == 2048
        assert data["category"] == "image"
        assert "url" in data

    async def test_register_with_note(self, client: AsyncClient, auth_headers, db, test_user):
        note = await _create_note(db, test_user.id)
        resp = await client.post(
            "/api/v1/files/register",
            json={
                "key": "uploads/doc.pdf",
                "filename": "doc.pdf",
                "content_type": "application/pdf",
                "size": 5000,
                "note_id": note.id,
            },
            headers=auth_headers,
        )
        assert resp.status_code == 201

    async def test_register_invalid_note(self, client: AsyncClient, auth_headers):
        resp = await client.post(
            "/api/v1/files/register",
            json={
                "key": "uploads/x.txt",
                "filename": "x.txt",
                "content_type": "text/plain",
                "size": 10,
                "note_id": "nonexistent",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 404

    async def test_register_path_traversal(self, client: AsyncClient, auth_headers):
        resp = await client.post(
            "/api/v1/files/register",
            json={"key": "../../../etc/passwd", "filename": "hack.txt", "content_type": "text/plain", "size": 100},
            headers=auth_headers,
        )
        assert resp.status_code == 400

    async def test_register_absolute_path(self, client: AsyncClient, auth_headers):
        resp = await client.post(
            "/api/v1/files/register",
            json={"key": "/etc/passwd", "filename": "hack.txt", "content_type": "text/plain", "size": 100},
            headers=auth_headers,
        )
        assert resp.status_code == 400

    async def test_register_unauthenticated(self, client: AsyncClient):
        resp = await client.post(
            "/api/v1/files/register",
            json={"key": "uploads/x.txt", "filename": "x.txt", "content_type": "text/plain", "size": 10},
        )
        assert resp.status_code == 401


# ── List ──────────────────────────────────────────────

class TestListFiles:
    async def test_list_empty(self, client: AsyncClient, auth_headers):
        resp = await client.get("/api/v1/files", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0

    async def test_list_with_files(self, client: AsyncClient, auth_headers, db, test_user):
        await _create_file(db, test_user.id, filename="a.png")
        await _create_file(db, test_user.id, filename="b.pdf", mime_type="application/pdf", storage_path="uploads/b.pdf")
        resp = await client.get("/api/v1/files", headers=auth_headers)
        data = resp.json()
        assert data["total"] == 2

    async def test_list_filter_by_category(self, client: AsyncClient, auth_headers, db, test_user):
        await _create_file(db, test_user.id, filename="pic.png", mime_type="image/png", storage_path="uploads/pic.png")
        await _create_file(db, test_user.id, filename="doc.pdf", mime_type="application/pdf", storage_path="uploads/doc.pdf")
        resp = await client.get("/api/v1/files?category=image", headers=auth_headers)
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["filename"] == "pic.png"

    async def test_list_search(self, client: AsyncClient, auth_headers, db, test_user):
        await _create_file(db, test_user.id, filename="report.pdf", mime_type="application/pdf", storage_path="uploads/report.pdf")
        await _create_file(db, test_user.id, filename="photo.jpg", mime_type="image/jpeg", storage_path="uploads/photo.jpg")
        resp = await client.get("/api/v1/files?q=report", headers=auth_headers)
        data = resp.json()
        assert data["total"] == 1

    async def test_list_isolates_users(self, client: AsyncClient, auth_headers, db, test_user, second_user):
        await _create_file(db, test_user.id, filename="mine.png", storage_path="uploads/mine.png")
        await _create_file(db, second_user.id, filename="theirs.png", storage_path="uploads/theirs.png")
        resp = await client.get("/api/v1/files", headers=auth_headers)
        data = resp.json()
        assert data["total"] == 1


# ── Get Meta ──────────────────────────────────────────

class TestGetFileMeta:
    async def test_get_meta(self, client: AsyncClient, auth_headers, db, test_user):
        f = await _create_file(db, test_user.id)
        resp = await client.get(f"/api/v1/files/{f.id}/meta", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["filename"] == "test.png"
        assert "references" in data

    async def test_get_meta_with_note_reference(self, client: AsyncClient, auth_headers, db, test_user):
        note = await _create_note(db, test_user.id, title="My Note")
        f = await _create_file(db, test_user.id, note_id=note.id, storage_path="uploads/ref.png")
        resp = await client.get(f"/api/v1/files/{f.id}/meta", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["references"]) == 1
        assert data["references"][0]["title"] == "My Note"

    async def test_get_meta_not_found(self, client: AsyncClient, auth_headers):
        resp = await client.get("/api/v1/files/nonexistent/meta", headers=auth_headers)
        assert resp.status_code == 404

    async def test_get_meta_other_user(self, client: AsyncClient, auth_headers, db, second_user):
        f = await _create_file(db, second_user.id, storage_path="uploads/other.png")
        resp = await client.get(f"/api/v1/files/{f.id}/meta", headers=auth_headers)
        assert resp.status_code == 404


# ── Get References ────────────────────────────────────

class TestGetFileReferences:
    async def test_references_with_note(self, client: AsyncClient, auth_headers, db, test_user):
        note = await _create_note(db, test_user.id)
        f = await _create_file(db, test_user.id, note_id=note.id, storage_path="uploads/ref2.png")
        resp = await client.get(f"/api/v1/files/{f.id}/references", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["file_id"] == f.id
        assert len(data["references"]) == 1

    async def test_references_no_note(self, client: AsyncClient, auth_headers, db, test_user):
        f = await _create_file(db, test_user.id, storage_path="uploads/solo.png")
        resp = await client.get(f"/api/v1/files/{f.id}/references", headers=auth_headers)
        assert resp.status_code == 200
        assert len(resp.json()["references"]) == 0


# ── Delete ────────────────────────────────────────────

class TestDeleteFile:
    async def test_delete_success(self, client: AsyncClient, auth_headers, db, test_user):
        f = await _create_file(db, test_user.id, storage_path="uploads/del.png")
        resp = await client.delete(f"/api/v1/files/{f.id}", headers=auth_headers)
        assert resp.status_code == 204

        resp2 = await client.get(f"/api/v1/files/{f.id}/meta", headers=auth_headers)
        assert resp2.status_code == 404

    async def test_delete_not_found(self, client: AsyncClient, auth_headers):
        resp = await client.delete("/api/v1/files/nonexistent", headers=auth_headers)
        assert resp.status_code == 404

    async def test_delete_other_user(self, client: AsyncClient, auth_headers, db, second_user):
        f = await _create_file(db, second_user.id, storage_path="uploads/notmine.png")
        resp = await client.delete(f"/api/v1/files/{f.id}", headers=auth_headers)
        assert resp.status_code == 404
