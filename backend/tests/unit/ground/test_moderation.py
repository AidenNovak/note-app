"""Tests for Ground UGC moderation (App Store Guideline 1.2 compliance)."""
from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import GroundPost, PostReport, User, UserBlock


pytestmark = pytest.mark.asyncio


async def _make_post(db: AsyncSession, user: User, title: str = "Hello world") -> GroundPost:
    post = GroundPost(
        id=str(uuid.uuid4()),
        user_id=user.id,
        post_type="mind_graph",
        ref_id=user.id,
        title=title,
        preview="preview text",
    )
    db.add(post)
    await db.commit()
    await db.refresh(post)
    return post


class TestReportPost:
    async def test_report_creates_record(
        self, client: AsyncClient, db: AsyncSession, test_user: User, second_user: User, second_auth_headers: dict
    ):
        post = await _make_post(db, test_user)
        resp = await client.post(
            f"/api/v1/ground/posts/{post.id}/report",
            json={"reason": "spam", "details": "obvious spam"},
            headers=second_auth_headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"post_id": post.id, "reported": True}

        result = await db.execute(select(PostReport).where(PostReport.post_id == post.id))
        report = result.scalar_one()
        assert report.reason == "spam"
        assert report.reporter_id == second_user.id

    async def test_cannot_report_own_post(
        self, client: AsyncClient, db: AsyncSession, test_user: User, auth_headers: dict
    ):
        post = await _make_post(db, test_user)
        resp = await client.post(
            f"/api/v1/ground/posts/{post.id}/report",
            json={"reason": "spam"},
            headers=auth_headers,
        )
        assert resp.status_code == 400

    async def test_duplicate_report_is_noop(
        self, client: AsyncClient, db: AsyncSession, test_user: User, second_auth_headers: dict
    ):
        post = await _make_post(db, test_user)
        for _ in range(2):
            r = await client.post(
                f"/api/v1/ground/posts/{post.id}/report",
                json={"reason": "harassment"},
                headers=second_auth_headers,
            )
            assert r.status_code == 200
        # Only one report row should exist despite double submission (unique constraint).
        from sqlalchemy import func
        count_res = await db.execute(
            select(func.count()).select_from(PostReport).where(PostReport.post_id == post.id)
        )
        assert count_res.scalar_one() == 1

    async def test_invalid_reason_rejected(
        self, client: AsyncClient, db: AsyncSession, test_user: User, second_auth_headers: dict
    ):
        post = await _make_post(db, test_user)
        resp = await client.post(
            f"/api/v1/ground/posts/{post.id}/report",
            json={"reason": "not-a-real-reason"},
            headers=second_auth_headers,
        )
        assert resp.status_code == 422


class TestBlockUser:
    async def test_block_then_unblock(
        self, client: AsyncClient, db: AsyncSession, test_user: User, second_user: User, auth_headers: dict
    ):
        r = await client.post(f"/api/v1/ground/users/{second_user.id}/block", headers=auth_headers)
        assert r.status_code == 200
        assert r.json() == {"user_id": second_user.id, "blocked": True}

        listing = await client.get("/api/v1/ground/blocks", headers=auth_headers)
        assert listing.status_code == 200
        assert any(u["id"] == second_user.id for u in listing.json())

        r = await client.delete(f"/api/v1/ground/users/{second_user.id}/block", headers=auth_headers)
        assert r.status_code == 200
        assert r.json() == {"user_id": second_user.id, "blocked": False}

        count = await db.execute(
            select(UserBlock).where(
                UserBlock.blocker_id == test_user.id,
                UserBlock.blocked_id == second_user.id,
            )
        )
        assert count.scalar_one_or_none() is None

    async def test_cannot_block_self(self, client: AsyncClient, test_user: User, auth_headers: dict):
        r = await client.post(f"/api/v1/ground/users/{test_user.id}/block", headers=auth_headers)
        assert r.status_code == 400

    async def test_blocked_user_excluded_from_feed(
        self,
        client: AsyncClient,
        db: AsyncSession,
        test_user: User,
        second_user: User,
        auth_headers: dict,
    ):
        await _make_post(db, second_user, title="from second_user")
        resp = await client.get("/api/v1/ground/posts?sort=recent", headers=auth_headers)
        assert resp.status_code == 200
        assert any(p["title"] == "from second_user" for p in resp.json())

        await client.post(f"/api/v1/ground/users/{second_user.id}/block", headers=auth_headers)
        resp = await client.get("/api/v1/ground/posts?sort=recent", headers=auth_headers)
        assert resp.status_code == 200
        assert not any(p["title"] == "from second_user" for p in resp.json())

    async def test_block_is_symmetric_in_feed(
        self,
        client: AsyncClient,
        db: AsyncSession,
        test_user: User,
        second_user: User,
        auth_headers: dict,
        second_auth_headers: dict,
    ):
        await _make_post(db, test_user, title="from test_user")
        await client.post(f"/api/v1/ground/users/{second_user.id}/block", headers=auth_headers)
        resp = await client.get("/api/v1/ground/posts?sort=recent", headers=second_auth_headers)
        assert resp.status_code == 200
        assert not any(p["title"] == "from test_user" for p in resp.json())


class TestHidePost:
    async def test_hide_excludes_from_my_feed_only(
        self,
        client: AsyncClient,
        db: AsyncSession,
        test_user: User,
        second_user: User,
        auth_headers: dict,
        second_auth_headers: dict,
    ):
        post = await _make_post(db, second_user, title="hide me")
        r = await client.post(f"/api/v1/ground/posts/{post.id}/hide", headers=auth_headers)
        assert r.status_code == 200

        mine = await client.get("/api/v1/ground/posts?sort=recent", headers=auth_headers)
        assert not any(p["id"] == post.id for p in mine.json())

        theirs = await client.get("/api/v1/ground/posts?sort=recent", headers=second_auth_headers)
        assert any(p["id"] == post.id for p in theirs.json())


class TestAdminTakedown:
    async def test_hidden_post_invisible_to_other_users(
        self,
        client: AsyncClient,
        db: AsyncSession,
        test_user: User,
        second_user: User,
        auth_headers: dict,
        second_auth_headers: dict,
    ):
        post = await _make_post(db, test_user, title="will be hidden")
        post.is_hidden = True
        post.hidden_reason = "violates policy"
        await db.commit()

        lst = await client.get("/api/v1/ground/posts?sort=recent", headers=second_auth_headers)
        assert not any(p["id"] == post.id for p in lst.json())
        det = await client.get(f"/api/v1/ground/posts/{post.id}", headers=second_auth_headers)
        assert det.status_code == 404

        owner_det = await client.get(f"/api/v1/ground/posts/{post.id}", headers=auth_headers)
        assert owner_det.status_code == 200


class TestKeywordBanlist:
    async def test_share_rejected_for_banned_keyword(
        self,
        client: AsyncClient,
        test_user: User,
        auth_headers: dict,
    ):
        r = await client.post(
            "/api/v1/ground/posts",
            json={
                "post_type": "mind_graph",
                "ref_id": test_user.id,
                "title": "child porn collection",
                "preview": "illegal content",
            },
            headers=auth_headers,
        )
        assert r.status_code == 400
        assert r.json()["detail"]["error"]["code"] == "CONTENT_POLICY_VIOLATION"

    async def test_clean_content_passes(
        self,
        client: AsyncClient,
        test_user: User,
        auth_headers: dict,
    ):
        r = await client.post(
            "/api/v1/ground/posts",
            json={
                "post_type": "mind_graph",
                "ref_id": test_user.id,
                "title": "A lovely day",
                "preview": "thoughts about the weather",
            },
            headers=auth_headers,
        )
        assert r.status_code == 200, r.text
