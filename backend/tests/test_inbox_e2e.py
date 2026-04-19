"""End-to-end test for Inbox features: multi-level folders + image/audio/video uploads + note attachments.

Run: ATELIER_TEST_API_BASE=http://localhost:8000 python tests/test_inbox_e2e.py
"""
from __future__ import annotations

import io
import os
import struct
import sys
import uuid
import zlib

import requests

BASE = os.getenv("ATELIER_TEST_API_BASE", "http://localhost:8000/api/v1")
JSON = {"Content-Type": "application/json"}

passed = 0
failed = 0
errors: list[tuple[str, str]] = []


def step(name: str):
    def deco(fn):
        def wrap(*a, **kw):
            global passed, failed
            try:
                out = fn(*a, **kw)
                passed += 1
                print(f"  ✅ {name}")
                return out
            except AssertionError as e:
                failed += 1
                errors.append((name, str(e)))
                print(f"  ❌ {name}: {e}")
                raise
            except Exception as e:
                failed += 1
                errors.append((name, f"{type(e).__name__}: {e}"))
                print(f"  ❌ {name}: {type(e).__name__}: {e}")
                raise
        return wrap
    return deco


# ---------- helpers: synthetic media ----------

def make_png(w: int = 4, h: int = 4) -> bytes:
    """Tiny valid PNG (solid color) without external libs."""
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)  # 8-bit RGB
    raw = b"".join(b"\x00" + b"\xff\xa0\x40" * w for _ in range(h))  # filter byte + RGB pixels
    idat = zlib.compress(raw, 9)
    return sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


def make_wav(seconds: float = 0.05) -> bytes:
    """Tiny silent WAV (PCM mono 8kHz)."""
    sample_rate = 8000
    n = int(sample_rate * seconds)
    data = b"\x80" * n
    fmt = struct.pack("<4sIHHIIHH", b"fmt ", 16, 1, 1, sample_rate, sample_rate, 1, 8)
    data_chunk = struct.pack("<4sI", b"data", len(data)) + data
    riff_size = 4 + len(fmt) + len(data_chunk)
    return struct.pack("<4sI4s", b"RIFF", riff_size, b"WAVE") + fmt + data_chunk


def make_mp4_stub() -> bytes:
    """Minimal MP4 ftyp box — enough for mime detection / register."""
    ftyp = b"\x00\x00\x00\x20ftypisom\x00\x00\x02\x00isomiso2avc1mp41"
    mdat = b"\x00\x00\x00\x08mdat"
    return ftyp + mdat


# ---------- auth ----------

def register_and_login() -> dict:
    suffix = uuid.uuid4().hex[:10]
    email = f"inbox_e2e_{suffix}@test.com"
    r = requests.post(
        f"{BASE}/auth/register",
        headers=JSON,
        json={"username": f"inbox_{suffix}", "email": email, "password": "test123456"},
        timeout=10,
    )
    assert r.status_code == 201, f"register failed {r.status_code} {r.text}"
    r = requests.post(
        f"{BASE}/auth/login",
        headers=JSON,
        json={"email": email, "password": "test123456"},
        timeout=10,
    )
    assert r.status_code == 200, f"login failed {r.status_code} {r.text}"
    token = r.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


# ---------- folder steps ----------

@step("Create root folder")
def create_root(auth):
    r = requests.post(f"{BASE}/folders", headers={**auth, **JSON}, json={"name": "Inbox-E2E Root"}, timeout=10)
    assert r.status_code == 201, r.text
    return r.json()["id"]


@step("Create child folder under root")
def create_child(auth, parent_id):
    r = requests.post(f"{BASE}/folders", headers={**auth, **JSON},
                      json={"name": "Child", "parent_id": parent_id}, timeout=10)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["parent_id"] == parent_id, "parent_id mismatch"
    return body["id"]


@step("Create grandchild folder (3 levels deep)")
def create_grandchild(auth, child_id):
    r = requests.post(f"{BASE}/folders", headers={**auth, **JSON},
                      json={"name": "Grandchild", "parent_id": child_id}, timeout=10)
    assert r.status_code == 201, r.text
    return r.json()["id"]


@step("Reject sub-folder under non-existent parent")
def create_under_bogus(auth):
    bogus = "local-99999999"
    r = requests.post(f"{BASE}/folders", headers={**auth, **JSON},
                      json={"name": "Orphan", "parent_id": bogus}, timeout=10)
    assert r.status_code == 404, f"expected 404 for bogus parent, got {r.status_code}: {r.text}"


@step("List folders returns nested tree")
def list_tree(auth, root_id, child_id, grandchild_id):
    r = requests.get(f"{BASE}/folders", headers=auth, timeout=10)
    assert r.status_code == 200
    tree = r.json()
    root = next((n for n in tree if n["id"] == root_id), None)
    assert root, "root not in tree"
    assert root["children"], "root has no children"
    child = next((n for n in root["children"] if n["id"] == child_id), None)
    assert child, "child not under root"
    grand = next((n for n in child["children"] if n["id"] == grandchild_id), None)
    assert grand, "grandchild not under child"


@step("Reject self-parent (cycle)")
def reject_self_parent(auth, folder_id):
    r = requests.put(f"{BASE}/folders/{folder_id}", headers={**auth, **JSON},
                     json={"parent_id": folder_id}, timeout=10)
    assert r.status_code == 400, f"expected 400, got {r.status_code}: {r.text}"


@step("Reject deletion of non-empty folder")
def reject_delete_nonempty(auth, root_id):
    r = requests.delete(f"{BASE}/folders/{root_id}", headers=auth, timeout=10)
    assert r.status_code == 400, f"expected 400, got {r.status_code}: {r.text}"


@step("Rename folder via PUT")
def rename_folder(auth, folder_id):
    r = requests.put(f"{BASE}/folders/{folder_id}", headers={**auth, **JSON},
                     json={"name": "Grandchild ✏️"}, timeout=10)
    assert r.status_code == 200
    assert "Grandchild" in r.json()["name"]


# ---------- file register steps ----------

@step("Register image file metadata")
def register_image(auth, note_id):
    body = {
        "key": f"attachments/{uuid.uuid4().hex}/test.png",
        "filename": "test.png",
        "content_type": "image/png",
        "size": len(make_png()),
        "note_id": note_id,
    }
    r = requests.post(f"{BASE}/files/register", headers={**auth, **JSON}, json=body, timeout=10)
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["category"] == "image", out
    return out["id"]


@step("Register audio file metadata (wav)")
def register_audio(auth, note_id):
    body = {
        "key": f"attachments/{uuid.uuid4().hex}/clip.wav",
        "filename": "clip.wav",
        "content_type": "audio/wav",
        "size": len(make_wav()),
        "note_id": note_id,
    }
    r = requests.post(f"{BASE}/files/register", headers={**auth, **JSON}, json=body, timeout=10)
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["category"] == "audio", out
    return out["id"]


@step("Register video file metadata (mp4)")
def register_video(auth, note_id):
    body = {
        "key": f"attachments/{uuid.uuid4().hex}/clip.mp4",
        "filename": "clip.mp4",
        "content_type": "video/mp4",
        "size": len(make_mp4_stub()),
        "note_id": note_id,
    }
    r = requests.post(f"{BASE}/files/register", headers={**auth, **JSON}, json=body, timeout=10)
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["category"] == "video", out
    return out["id"]


@step("Reject path-traversal storage key")
def reject_traversal(auth):
    body = {
        "key": "../etc/passwd",
        "filename": "x", "content_type": "image/png", "size": 1,
    }
    r = requests.post(f"{BASE}/files/register", headers={**auth, **JSON}, json=body, timeout=10)
    assert r.status_code == 400, f"expected 400, got {r.status_code}: {r.text}"


@step("List files filter category=image")
def filter_images(auth, image_id):
    r = requests.get(f"{BASE}/files?category=image", headers=auth, timeout=10)
    assert r.status_code == 200, r.text
    ids = [f["id"] for f in r.json()["items"]]
    assert image_id in ids, f"image {image_id} not in {ids}"


@step("List files filter category=audio")
def filter_audio(auth, audio_id):
    r = requests.get(f"{BASE}/files?category=audio", headers=auth, timeout=10)
    assert r.status_code == 200
    ids = [f["id"] for f in r.json()["items"]]
    assert audio_id in ids, f"audio {audio_id} not in {ids}"


@step("List files filter category=video")
def filter_video(auth, video_id):
    r = requests.get(f"{BASE}/files?category=video", headers=auth, timeout=10)
    assert r.status_code == 200
    ids = [f["id"] for f in r.json()["items"]]
    assert video_id in ids, f"video {video_id} not in {ids}"


@step("File detail exposes references back to note")
def file_detail_refs(auth, file_id, note_id):
    r = requests.get(f"{BASE}/files/{file_id}/meta", headers=auth, timeout=10)
    assert r.status_code == 200, r.text
    body = r.json()
    refs = body.get("references", [])
    assert any(ref["id"] == note_id for ref in refs), f"note {note_id} not in refs {refs}"


# ---------- /storage/upload (R2-gated) ----------

@step("Upload image via /storage/upload (skipped if R2 unconfigured)")
def upload_image(auth):
    files = {"file": ("test.png", make_png(), "image/png")}
    data = {"purpose": "attachment"}
    r = requests.post(f"{BASE}/storage/upload", headers=auth, files=files, data=data, timeout=20)
    if r.status_code == 501:
        print("     (R2 not configured — upload endpoint returned 501; skipped)")
        return None
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["url"].startswith("http"), body
    return body["key"]


# ---------- note + attachment count ----------

@step("Create note inside grandchild folder")
def create_note_in_folder(auth, folder_id):
    r = requests.post(
        f"{BASE}/notes",
        headers={**auth, **JSON},
        json={"title": "Inbox E2E note", "content": "Smoke test", "tags": ["ideas"],
              "folder_id": folder_id, "skip_ai": True},
        timeout=15,
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


@step("Note attachment_count reflects registered files")
def attachment_count(auth, note_id):
    r = requests.get(f"{BASE}/notes/{note_id}", headers=auth, timeout=10)
    assert r.status_code == 200
    body = r.json()
    assert body.get("attachment_count", 0) >= 3, f"expected >=3 attachments, got {body.get('attachment_count')}"


# ---------- cleanup ----------

@step("Delete note")
def delete_note(auth, note_id):
    r = requests.delete(f"{BASE}/notes/{note_id}", headers=auth, timeout=10)
    assert r.status_code == 204


@step("Delete grandchild folder (now empty)")
def delete_grand(auth, folder_id):
    r = requests.delete(f"{BASE}/folders/{folder_id}", headers=auth, timeout=10)
    assert r.status_code == 204, r.text


@step("Delete child folder (now empty)")
def delete_child(auth, folder_id):
    r = requests.delete(f"{BASE}/folders/{folder_id}", headers=auth, timeout=10)
    assert r.status_code == 204, r.text


@step("Delete root folder (now empty)")
def delete_root(auth, folder_id):
    r = requests.delete(f"{BASE}/folders/{folder_id}", headers=auth, timeout=10)
    assert r.status_code == 204, r.text


# ---------- driver ----------

def main() -> int:
    print("=" * 60)
    print(f"  Inbox E2E Suite — {BASE}")
    print("=" * 60)

    print("\n── Auth ──")
    auth = register_and_login()
    print("  ✅ Registered + logged in")
    global passed; passed += 1

    print("\n── Multi-level folders ──")
    root_id = create_root(auth)
    child_id = create_child(auth, root_id)
    grand_id = create_grandchild(auth, child_id)
    try: create_under_bogus(auth)
    except Exception: pass
    list_tree(auth, root_id, child_id, grand_id)
    try: reject_self_parent(auth, grand_id)
    except Exception: pass
    rename_folder(auth, grand_id)
    try: reject_delete_nonempty(auth, root_id)
    except Exception: pass

    print("\n── Note inside nested folder ──")
    note_id = create_note_in_folder(auth, grand_id)

    print("\n── File register: image / audio / video ──")
    image_id = register_image(auth, note_id)
    audio_id = register_audio(auth, note_id)
    video_id = register_video(auth, note_id)
    try: reject_traversal(auth)
    except Exception: pass

    print("\n── File listing & filters ──")
    filter_images(auth, image_id)
    filter_audio(auth, audio_id)
    filter_video(auth, video_id)
    file_detail_refs(auth, image_id, note_id)

    print("\n── /storage/upload (real upload, R2-gated) ──")
    try: upload_image(auth)
    except Exception: pass

    print("\n── Note attachment_count ──")
    attachment_count(auth, note_id)

    print("\n── Cleanup ──")
    delete_note(auth, note_id)
    delete_grand(auth, grand_id)
    delete_child(auth, child_id)
    delete_root(auth, root_id)

    print("\n" + "=" * 60)
    print(f"  Results: {passed} passed, {failed} failed")
    if errors:
        print("\n  Failed:")
        for n, e in errors:
            print(f"    ❌ {n}: {e[:120]}")
    print("=" * 60)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
