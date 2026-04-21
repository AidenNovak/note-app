"""
Test data factories for Truth Truth backend.

Usage:
    user_data = UserFactory.build()
    note_data = NoteFactory.build(user_id="some-id")
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone


class UserFactory:
    """Generate user creation data or User model instances."""

    _counter = 0

    @classmethod
    def build(cls, **overrides) -> dict:
        cls._counter += 1
        defaults = {
            "username": f"user{cls._counter}",
            "email": f"user{cls._counter}@example.com",
            "password": "StrongPass123!",
        }
        defaults.update(overrides)
        return defaults

    @classmethod
    def register_payload(cls, **overrides) -> dict:
        """Payload suitable for POST /api/v1/auth/register."""
        data = cls.build(**overrides)
        return {
            "username": data["username"],
            "email": data["email"],
            "password": data["password"],
        }


class NoteFactory:
    """Generate note creation data."""

    _counter = 0

    @classmethod
    def build(cls, **overrides) -> dict:
        cls._counter += 1
        defaults = {
            "title": f"Test Note {cls._counter}",
            "markdown_content": f"# Note {cls._counter}\n\nThis is test content.",
        }
        defaults.update(overrides)
        return defaults

    @classmethod
    def create_payload(cls, **overrides) -> dict:
        """Payload suitable for POST /api/v1/notes."""
        return cls.build(**overrides)


class FolderFactory:
    """Generate folder creation data."""

    _counter = 0

    @classmethod
    def build(cls, **overrides) -> dict:
        cls._counter += 1
        defaults = {
            "name": f"Test Folder {cls._counter}",
        }
        defaults.update(overrides)
        return defaults
