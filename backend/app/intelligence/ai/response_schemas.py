from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.intelligence.ai.provider import AIResponseFormat


class StrictSchemaModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NoteMetadataOutput(StrictSchemaModel):
    title: str = Field(max_length=80)
    tags: list[str] = Field(min_length=2, max_length=5)


class NoteRewriteOutput(StrictSchemaModel):
    title: str = Field(max_length=80)
    markdown_content: str
    tags: list[str] = Field(min_length=3, max_length=5)
    summary: str = Field(max_length=80)


class MindSynthesisOutput(StrictSchemaModel):
    summary: str
    themes: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)


def response_format_for(model_class: type[BaseModel], *, name: str, strict: bool = True) -> AIResponseFormat:
    return AIResponseFormat(
        type="json_schema",
        name=name,
        schema=model_class.model_json_schema(),
        strict=strict,
    )


NOTE_METADATA_RESPONSE_FORMAT = response_format_for(NoteMetadataOutput, name="note_metadata")
NOTE_REWRITE_RESPONSE_FORMAT = response_format_for(NoteRewriteOutput, name="note_rewrite")
MIND_SYNTHESIS_RESPONSE_FORMAT = response_format_for(MindSynthesisOutput, name="mind_synthesis")
