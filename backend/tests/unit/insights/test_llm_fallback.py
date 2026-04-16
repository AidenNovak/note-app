from __future__ import annotations

from types import SimpleNamespace

from app.intelligence.insights import llm


class _FakeCompletions:
    def create(self, **kwargs):
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="hello from fallback"),
                    finish_reason="stop",
                )
            ],
            usage={"total_tokens": 12},
        )


class _FakeChat:
    def __init__(self):
        self.completions = _FakeCompletions()


class _FakeModel:
    def __init__(self):
        self.chat = _FakeChat()


def test_generate_text_sync_falls_back_to_openai_client(monkeypatch):
    monkeypatch.setattr(llm, "_HAS_AI_SDK", False)

    result = llm._generate_text_sync(
        model=_FakeModel(),
        system="system prompt",
        prompt="user prompt",
        max_tokens=128,
        temperature=0.7,
    )

    assert result.text == "hello from fallback"
    assert result.finish_reason == "stop"
    assert result.usage == {"total_tokens": 12}
