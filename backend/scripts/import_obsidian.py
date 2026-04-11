#!/usr/bin/env python3
"""Import markdown notes from a local Obsidian vault into note-app.

Usage:
    python backend/scripts/import_obsidian.py [--dry-run] [--base-url URL] [--vault-path PATH]
"""
from __future__ import annotations

import argparse
import re
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

DEFAULT_BASE = "http://localhost:8000"
DEFAULT_VAULT = "/Users/lijixiang/Documents/code-projects/Obsidian Vault"
DEFAULT_SKIP = {".obsidian", "_duplicates", ".trae", "png", "podnote", ".trash"}

EMAIL = "demo@atelier.dev"
USERNAME = "demo"
PASSWORD = "Demo1234!"

IMAGE_RE = re.compile(r"!\[\[([^\]]+\.(?:png|jpg|jpeg|gif|webp|svg))\]\]", re.IGNORECASE)


@dataclass
class VaultNote:
    file_path: Path
    relative_path: str
    title: str
    folder_parts: tuple[str, ...]
    tags: list[str]
    content: str
    size: int = 0


@dataclass
class ImportStats:
    scanned: int = 0
    skipped_empty: int = 0
    created: int = 0
    skipped_exists: int = 0
    failed: int = 0
    folders_created: int = 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def auth(client: httpx.Client) -> str:
    r = client.post("/api/v1/auth/register", json={
        "email": EMAIL, "username": USERNAME, "password": PASSWORD,
    })
    if r.status_code == 201:
        print(f"  Registered user {EMAIL}")
    elif r.status_code == 400:
        print(f"  User exists, logging in")
    else:
        r.raise_for_status()

    r = client.post("/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD})
    r.raise_for_status()
    token = r.json()["access_token"]
    print(f"  Token: {token[:20]}...")
    return token


def transform_content(raw: str) -> str:
    """Convert Obsidian image embeds to HTML comments."""
    return IMAGE_RE.sub(r"<!-- obsidian-image: \1 -->", raw)


def scan_vault(vault_path: Path, skip_dirs: set[str]) -> list[VaultNote]:
    """Walk the vault and build a list of VaultNote objects."""
    notes: list[VaultNote] = []
    for md in sorted(vault_path.rglob("*.md")):
        # Skip files inside excluded directories
        if any(part in skip_dirs for part in md.relative_to(vault_path).parts):
            continue
        rel = md.relative_to(vault_path)
        size = md.stat().st_size
        if size == 0:
            continue
        content = md.read_text(encoding="utf-8", errors="replace")
        folder_parts = rel.parent.parts if rel.parent != Path(".") else ()
        title = md.stem[:255]
        tags = [p[:64] for p in folder_parts] if folder_parts else []
        notes.append(VaultNote(
            file_path=md,
            relative_path=str(rel),
            title=title,
            folder_parts=folder_parts,
            tags=tags,
            content=content,
            size=size,
        ))
    return notes


def create_folder_hierarchy(
    client: httpx.Client, headers: dict, folder_paths: set[tuple[str, ...]], stats: ImportStats,
) -> dict[tuple[str, ...], str]:
    """Create folders top-down, return mapping of folder_path_tuple -> folder_id."""
    folder_map: dict[tuple[str, ...], str] = {}
    # Collect all prefixes that need to exist
    all_paths: set[tuple[str, ...]] = set()
    for fp in folder_paths:
        for i in range(1, len(fp) + 1):
            all_paths.add(fp[:i])
    # Sort by depth so parents are created first
    for fp in sorted(all_paths, key=len):
        if fp in folder_map:
            continue
        name = fp[-1][:128]
        parent_id = folder_map.get(fp[:-1]) if len(fp) > 1 else None
        payload: dict = {"name": name}
        if parent_id:
            payload["parent_id"] = parent_id
        r = client.post("/api/v1/folders", json=payload, headers=headers)
        if r.status_code == 201:
            folder_map[fp] = r.json()["id"]
            stats.folders_created += 1
            print(f"  📁 Created folder: {'/'.join(fp)}")
        elif r.status_code == 400:
            # Folder may already exist — try to find it
            found = find_folder_id(client, headers, name, parent_id)
            if found:
                folder_map[fp] = found
                print(f"  📁 Folder exists: {'/'.join(fp)}")
            else:
                print(f"  ⚠️  Failed to create/find folder: {'/'.join(fp)} ({r.text[:100]})")
        else:
            print(f"  ⚠️  Folder creation failed ({r.status_code}): {'/'.join(fp)}")
    return folder_map


def find_folder_id(client: httpx.Client, headers: dict, name: str, parent_id: str | None) -> str | None:
    """Search existing folders for a match by name and parent."""
    r = client.get("/api/v1/folders", headers=headers)
    if r.status_code != 200:
        return None
    folders = r.json()
    return _search_folder_tree(folders, name, parent_id)


def _search_folder_tree(folders: list[dict], name: str, parent_id: str | None) -> str | None:
    for f in folders:
        if f["name"] == name and f.get("parent_id") == parent_id:
            return f["id"]
        children = f.get("children", [])
        if children:
            result = _search_folder_tree(children, name, parent_id)
            if result:
                return result
    return None


def import_notes(
    client: httpx.Client, headers: dict,
    notes: list[VaultNote], folder_map: dict[tuple[str, ...], str],
    stats: ImportStats,
) -> None:
    total = len(notes)
    for i, note in enumerate(notes, 1):
        folder_id = folder_map.get(note.folder_parts) if note.folder_parts else None
        if note.size > 500_000:
            print(f"  ⚠️  Large file ({note.size // 1024}KB): {note.relative_path}")

        # Check if note already exists (simple title search)
        params: dict = {"keyword": note.title, "page_size": "5"}
        if folder_id:
            params["folder_id"] = folder_id
        r = client.get("/api/v1/notes", params=params, headers=headers)
        if r.status_code == 200:
            items = r.json().get("items", [])
            if any(item["title"] == note.title for item in items):
                stats.skipped_exists += 1
                print(f"  [{i:3d}/{total}] Exists: {note.title[:50]}")
                continue

        content = transform_content(note.content)
        payload = {
            "title": note.title,
            "markdown_content": content,
            "tags": note.tags,
        }
        if folder_id:
            payload["folder_id"] = folder_id

        r = client.post("/api/v1/notes", json=payload, headers=headers)
        if r.status_code == 201:
            stats.created += 1
            print(f"  [{i:3d}/{total}] Created: {note.title[:50]}")
        else:
            stats.failed += 1
            print(f"  [{i:3d}/{total}] FAILED ({r.status_code}): {note.title[:50]} — {r.text[:80]}")
        time.sleep(0.05)


def verify(client: httpx.Client, headers: dict) -> None:
    r = client.get("/api/v1/notes?page_size=1", headers=headers)
    if r.status_code == 200:
        total = r.json().get("total", 0)
        print(f"  Total notes in DB: {total}")
    r = client.get("/api/v1/folders", headers=headers)
    if r.status_code == 200:
        folders = r.json()
        print(f"  Top-level folders: {len(folders)}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Import Obsidian vault into note-app")
    parser.add_argument("--base-url", default=DEFAULT_BASE)
    parser.add_argument("--vault-path", default=DEFAULT_VAULT)
    parser.add_argument("--dry-run", action="store_true", help="Scan only, no API calls")
    parser.add_argument("--skip-folders", default="", help="Extra folders to skip (comma-separated)")
    args = parser.parse_args()

    vault = Path(args.vault_path)
    if not vault.is_dir():
        print(f"Vault not found: {vault}")
        return

    skip_dirs = DEFAULT_SKIP.copy()
    if args.skip_folders:
        skip_dirs.update(f.strip() for f in args.skip_folders.split(",") if f.strip())

    print("═══ Obsidian Vault Import ═══\n")

    print("1. Scanning vault")
    notes = scan_vault(vault, skip_dirs)
    stats = ImportStats(scanned=len(notes))
    folder_paths = {n.folder_parts for n in notes if n.folder_parts}
    print(f"  Found {len(notes)} notes across {len(folder_paths)} folders")

    if args.dry_run:
        print("\n── Dry Run Summary ──")
        for fp in sorted(folder_paths):
            count = sum(1 for n in notes if n.folder_parts == fp)
            print(f"  📁 {'/'.join(fp)} ({count} notes)")
        root_count = sum(1 for n in notes if not n.folder_parts)
        if root_count:
            print(f"  📄 Root level ({root_count} notes)")
        large = [n for n in notes if n.size > 500_000]
        if large:
            print(f"\n  Large files (>500KB):")
            for n in large:
                print(f"    {n.relative_path} ({n.size // 1024}KB)")
        print(f"\n  Total: {len(notes)} notes to import")
        return

    client = httpx.Client(base_url=args.base_url, timeout=120)

    print("\n2. Auth")
    token = auth(client)
    headers = {"Authorization": f"Bearer {token}"}

    print("\n3. Creating folders")
    folder_map = create_folder_hierarchy(client, headers, folder_paths, stats)

    print(f"\n4. Importing {len(notes)} notes")
    import_notes(client, headers, notes, folder_map, stats)

    print("\n5. Verification")
    verify(client, headers)

    print(f"\n── Summary ──")
    print(f"  Scanned:  {stats.scanned}")
    print(f"  Created:  {stats.created}")
    print(f"  Skipped:  {stats.skipped_exists}")
    print(f"  Failed:   {stats.failed}")
    print(f"  Folders:  {stats.folders_created}")
    print("\n═══ Done ═══")


if __name__ == "__main__":
    main()
