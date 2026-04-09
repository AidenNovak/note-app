"""End-to-end test suite for Atelier API — tests full user flows."""
import json
import os
import time
import uuid

import pytest
import requests

BASE = os.getenv("ATELIER_TEST_API_BASE", "http://localhost:8003")
HEADERS = {"Content-Type": "application/json"}
auth_headers = {}
pytestmark = pytest.mark.integration


def _register_user():
    suffix = uuid.uuid4().hex[:12]
    r = requests.post(
        f"{BASE}/auth/register",
        headers=HEADERS,
        json={
            "username": f"e2e_user_{suffix}",
            "email": f"e2e_{suffix}@test.com",
            "password": "test123456",
        },
    )
    assert r.status_code == 201, f"Register failed: {r.text}"
    data = r.json()
    assert "id" in data
    assert data["username"].startswith("e2e_user")
    return data["email"]


def _login_user(email):
    r = requests.post(
        f"{BASE}/auth/login",
        headers=HEADERS,
        json={"email": email, "password": "test123456"},
    )
    assert r.status_code == 200, f"Login failed: {r.text}"
    data = r.json()
    assert "access_token" in data
    auth_headers["Authorization"] = f"Bearer {data['access_token']}"
    return auth_headers


def _create_notes():
    notes = [
        ("Shopping List", "Buy milk, eggs, bread", "shopping,life"),
        ("Project Ideas", "Build a knowledge graph app with Flutter", "project,tech,flutter"),
        ("Meeting Notes", "Discussed Q2 roadmap and milestones", "meeting,project"),
        ("Reading List", "Finish 'Thinking Fast and Slow' this month", "reading,life"),
        ("Code Snippet", "async def hello(): print('world')", "code,tech"),
    ]
    note_ids = []
    for title, content, tags in notes:
        r = requests.post(
            f"{BASE}/notes",
            headers=auth_headers,
            data={"title": title, "content": content, "tags": tags},
        )
        assert r.status_code == 201, f"Create note failed: {r.text}"
        data = r.json()
        note_ids.append(data["id"])
    return note_ids


@pytest.fixture(scope="session", autouse=True)
def live_api():
    try:
        response = requests.get(f"{BASE}/health", timeout=2)
        if response.status_code != 200:
            pytest.skip(f"Live API health endpoint is unavailable at {BASE}/health")
        payload = response.json()
        if payload.get("status") != "ok":
            pytest.skip("Live API health check did not return an ok status")
    except requests.RequestException as exc:
        pytest.skip(f"Live API is not running at {BASE}: {exc}")


@pytest.fixture(scope="session")
def email(live_api):
    return _register_user()


@pytest.fixture(scope="session", autouse=True)
def authenticated_session(live_api, email):
    return _login_user(email)


@pytest.fixture(scope="session")
def note_ids(authenticated_session):
    return _create_notes()


def test_1_health():
    """Backend is alive."""
    r = requests.get(f"{BASE}/")
    assert r.status_code == 200
    data = r.json()
    assert data["name"] == "atelier API"
    print("  ✅ Health check passed")


def test_2_cors():
    """CORS headers are present."""
    r = requests.options(
        f"{BASE}/auth/register",
        headers={
            "Origin": "http://localhost:8080",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert r.status_code == 200
    assert "access-control-allow-origin" in r.headers
    print("  ✅ CORS headers present")


def test_3_register():
    """Register a new user."""
    registered_email = _register_user()
    assert registered_email.endswith("@test.com")
    print(f"  ✅ Registered user: {registered_email}")


def test_4_login(email):
    """Login and store token."""
    headers = _login_user(email)
    assert "Authorization" in headers
    print(f"  ✅ Login successful, token acquired")


def test_5_create_notes(note_ids):
    """Create multiple notes via form data."""
    assert len(note_ids) == 5
    print(f"  ✅ Created {len(note_ids)} notes")


def test_6_list_notes():
    """List notes with pagination."""
    r = requests.get(f"{BASE}/notes?page=1&page_size=10", headers=auth_headers)
    assert r.status_code == 200
    data = r.json()
    assert data["total"] >= 5
    assert len(data["items"]) >= 5
    print(f"  ✅ Listed {data['total']} notes")


def test_7_get_note_detail(note_ids):
    """Get single note detail."""
    nid = note_ids[0]
    r = requests.get(f"{BASE}/notes/{nid}", headers=auth_headers)
    assert r.status_code == 200
    data = r.json()
    assert data["markdown_content"] is not None
    assert data["current_version"] >= 1
    print(f"  ✅ Note detail: {data['title']} (v{data['current_version']})")


def test_8_update_note(note_ids):
    """Update a note's tags."""
    nid = note_ids[0]
    r = requests.put(
        f"{BASE}/notes/{nid}",
        headers={**auth_headers, "Content-Type": "application/json"},
        json={"tags": ["shopping", "life", "groceries"]},
    )
    assert r.status_code == 200
    data = r.json()
    assert "groceries" in data["tags"]
    print(f"  ✅ Updated note tags: {data['tags']}")


def test_9_search():
    """Full-text search."""
    r = requests.get(f"{BASE}/search?q=project", headers=auth_headers)
    assert r.status_code == 200
    data = r.json()
    assert data["total"] >= 1
    print(f"  ✅ Search 'project' found {data['total']} results")


def test_10_tags():
    """List all tags."""
    r = requests.get(f"{BASE}/tags", headers=auth_headers)
    assert r.status_code == 200
    data = r.json()
    tags = data["tags"]
    assert len(tags) >= 3
    print(f"  ✅ Tags: {tags}")


def test_11_mind_graph():
    """Knowledge graph has nodes and edges."""
    time.sleep(3)  # Wait for background processing
    r = requests.get(f"{BASE}/mind/graph", headers=auth_headers)
    assert r.status_code == 200
    data = r.json()
    assert len(data["nodes"]) >= 3
    assert len(data["edges"]) >= 1
    print(f"  ✅ Mind graph: {len(data['nodes'])} nodes, {len(data['edges'])} edges")


def test_12_insights():
    """AI insights are generated and persisted."""
    r = requests.post(f"{BASE}/insights/generate", headers=auth_headers)
    assert r.status_code == 202

    latest = None
    for _ in range(60):
        latest = requests.get(f"{BASE}/insights/generations/latest", headers=auth_headers)
        assert latest.status_code == 200
        payload = latest.json()
        if payload and payload["status"] in {"completed", "failed"}:
            break
        time.sleep(2)

    assert latest is not None
    payload = latest.json()
    assert payload["status"] == "completed", payload

    r = requests.get(f"{BASE}/insights", headers=auth_headers)
    assert r.status_code == 200
    data = r.json()
    assert len(data) >= 1

    detail = requests.get(f"{BASE}/insights/{data[0]['id']}", headers=auth_headers)
    assert detail.status_code == 200
    assert detail.json()["report_markdown"]
    print(f"  ✅ Insights: {len(data)} generated")


def test_13_ground_empty():
    """Ground feed is empty — no notes shared yet."""
    r = requests.get(f"{BASE}/ground/feed", headers=auth_headers)
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 0
    print(f"  ✅ Ground feed: {len(data)} items (empty before sharing)")


def test_14_share_note(note_ids):
    """Share a note to Ground."""
    nid = note_ids[1]  # "Project Ideas"
    r = requests.post(f"{BASE}/ground/notes/{nid}/share", headers=auth_headers)
    assert r.status_code == 200
    data = r.json()
    assert data["shared"] is True
    print(f"  ✅ Shared note: {nid[:8]}...")


def test_15_ground_after_share():
    """Ground feed now has the shared note."""
    r = requests.get(f"{BASE}/ground/feed", headers=auth_headers)
    assert r.status_code == 200
    data = r.json()
    assert len(data) >= 1
    assert data[0]["title"] == "Project Ideas"
    print(f"  ✅ Ground feed: {len(data)} items after sharing")


def test_16_like_note(note_ids):
    """Like a shared note."""
    nid = note_ids[1]
    r = requests.post(f"{BASE}/ground/notes/{nid}/like", headers=auth_headers)
    assert r.status_code == 200
    data = r.json()
    assert data["liked"] is True
    print(f"  ✅ Liked note: {nid[:8]}...")


def test_17_folders():
    """Folders CRUD."""
    # Create folder
    r = requests.post(
        f"{BASE}/folders",
        headers={**auth_headers, "Content-Type": "application/json"},
        json={"name": "Work"},
    )
    assert r.status_code == 201
    folder = r.json()
    folder_id = folder["id"]
    print(f"  ✅ Created folder: Work")

    # List folders
    r = requests.get(f"{BASE}/folders", headers=auth_headers)
    assert r.status_code == 200
    assert len(r.json()) >= 1
    print(f"  ✅ Listed {len(r.json())} folders")


def test_18_versions(note_ids):
    """Version history."""
    nid = note_ids[0]
    r = requests.get(f"{BASE}/notes/{nid}/versions", headers=auth_headers)
    assert r.status_code == 200
    data = r.json()
    assert len(data) >= 1
    print(f"  ✅ Versions: {len(data)} for note {nid[:8]}...")


def test_19_delete_note(note_ids):
    """Delete a note."""
    nid = note_ids[-1]  # Delete the last one
    r = requests.delete(f"{BASE}/notes/{nid}", headers=auth_headers)
    assert r.status_code == 204
    print(f"  ✅ Deleted note: {nid[:8]}...")


def test_20_auth_me():
    """Get current user info."""
    r = requests.get(f"{BASE}/auth/me", headers=auth_headers)
    assert r.status_code == 200
    data = r.json()
    assert "username" in data
    print(f"  ✅ Current user: {data['username']}")


if __name__ == "__main__":
    print("=" * 60)
    print("  Atelier End-to-End Test Suite")
    print("=" * 60)

    tests = [
        ("1.  Health Check", lambda: test_1_health()),
        ("2.  CORS", lambda: test_2_cors()),
        ("3.  Register", lambda: test_3_register()),
        ("4.  Login", lambda: test_4_login(test_3_register.__email if hasattr(test_3_register, '__email') else None)),
    ]

    passed = 0
    failed = 0
    errors = []

    def run(name, fn):
        global passed, failed
        try:
            fn()
            passed += 1
        except Exception as e:
            failed += 1
            errors.append((name, str(e)))
            print(f"  ❌ {name}: {e}")

    print("\n── Running tests ──\n")

    run("1.  Health Check", test_1_health)
    run("2.  CORS", test_2_cors)

    email = None
    print("\n── Auth Flow ──\n")
    try:
        email = test_3_register()
        run("3.  Register", lambda: None)
    except Exception as e:
        failed += 1
        errors.append(("3. Register", str(e)))
        print(f"  ❌ Register: {e}")

    if email:
        try:
            test_4_login(email)
            run("4.  Login", lambda: None)
        except Exception as e:
            failed += 1
            errors.append(("4. Login", str(e)))
            print(f"  ❌ Login: {e}")

    if auth_headers:
        print("\n── Notes CRUD ──\n")
        note_ids = []
        try:
            note_ids = test_5_create_notes()
            run("5.  Create Notes", lambda: None)
        except Exception as e:
            failed += 1
            errors.append(("5. Create Notes", str(e)))

        run("6.  List Notes", test_6_list_notes)

        if note_ids:
            run("7.  Note Detail", lambda: test_7_get_note_detail(note_ids))
            run("8.  Update Note", lambda: test_8_update_note(note_ids))

        print("\n── Search & Tags ──\n")
        run("9.  Search", test_9_search)
        run("10. Tags", test_10_tags)

        print("\n── Mind Graph & Insights ──\n")
        run("11. Mind Graph", test_11_mind_graph)
        run("12. Insights", test_12_insights)

        print("\n── Ground (Social) ──\n")
        run("13. Ground Empty", test_13_ground_empty)
        if note_ids:
            run("14. Share Note", lambda: test_14_share_note(note_ids))
            run("15. Ground After Share", test_15_ground_after_share)
            run("16. Like Note", lambda: test_16_like_note(note_ids))

        print("\n── Folders & Versions ──\n")
        run("17. Folders", test_17_folders)
        if note_ids:
            run("18. Versions", lambda: test_18_versions(note_ids))
            run("19. Delete Note", lambda: test_19_delete_note(note_ids))

        run("20. Auth Me", test_20_auth_me)

    print("\n" + "=" * 60)
    print(f"  Results: {passed} passed, {failed} failed")
    if errors:
        print("\n  Failed tests:")
        for name, err in errors:
            print(f"    ❌ {name}: {err[:80]}")
    print("=" * 60)
