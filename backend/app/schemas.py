from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


# ── Auth ──────────────────────────────────────────────

class RegisterRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    username: str = Field(min_length=3, max_length=64)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str | None = None
    token_type: str = "bearer"
    expires_in: int = 3600


class UserOut(BaseModel):
    id: str
    username: str
    email: str
    avatar_url: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Files ──────────────────────────────────────────────

class FileOut(BaseModel):
    id: str
    filename: str
    mime_type: str
    size: int
    category: str
    url: str
    note_id: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Notes ──────────────────────────────────────────────

class NoteCreate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    markdown_content: str | None = None
    folder_id: str | None = None
    tags: list[str] | None = None


class NoteUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    folder_id: str | None = None
    tags: list[str] | None = None
    markdown_content: str | None = None
    version_summary: str | None = Field(default=None, max_length=255)


class AttachmentOut(BaseModel):
    id: str
    type: str
    url: str
    filename: str
    mime_type: str
    size: int
    category: str


class NoteOut(BaseModel):
    id: str
    title: str
    title_source: str
    status: str
    folder_id: str | None
    tags: list[str]
    tag_source: str
    source_type: str | None = None
    attachment_count: int = 0
    content_preview: str = ""
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class NoteDetail(NoteOut):
    markdown_content: str | None
    attachments: list[AttachmentOut]
    source_type: str | None
    source_file_id: str | None
    current_version: int


class NoteListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[NoteOut]


class FileReferenceNoteOut(BaseModel):
    id: str
    title: str
    updated_at: datetime


class FileDetail(FileOut):
    references: list[FileReferenceNoteOut] = Field(default_factory=list)


class FileListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[FileOut]


class FileReferenceListResponse(BaseModel):
    file_id: str
    references: list[FileReferenceNoteOut]


# ── Tasks ──────────────────────────────────────────────

class TaskOut(BaseModel):
    id: str
    note_id: str
    type: str
    status: str
    progress: float
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class TaskDetail(TaskOut):
    error: str | None
    input_file_id: str | None
    completed_at: datetime | None


class TaskListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[TaskOut]


# ── Folders ──────────────────────────────────────────────

class FolderCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=128)
    parent_id: str | None = None


class FolderUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str | None = Field(default=None, min_length=1, max_length=128)
    parent_id: str | None = None


class FolderOut(BaseModel):
    id: str
    name: str
    parent_id: str | None
    created_at: datetime
    updated_at: datetime
    children: list["FolderOut"] = Field(default_factory=list)

    model_config = {"from_attributes": True}


# ── Tags ──────────────────────────────────────────────

class TagsAdd(BaseModel):
    tags: list[str] = Field(min_length=1)


class TagOut(BaseModel):
    tag: str


# ── Search ──────────────────────────────────────────────

class SearchResultItem(BaseModel):
    id: str
    type: str
    title: str
    highlight: str
    created_at: datetime


class SearchResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[SearchResultItem]


class SuggestResponse(BaseModel):
    suggestions: list[str]


# ── Versions ──────────────────────────────────────────────

class VersionOut(BaseModel):
    version: int
    version_origin: str
    derived_from_version: int | None
    title: str
    title_source: str
    tags: list[str]
    tag_source: str
    summary: str
    created_at: datetime

    model_config = {"from_attributes": True}


class VersionListResponse(BaseModel):
    note_id: str
    versions: list[VersionOut]


class VersionDetail(BaseModel):
    id: str
    version: int
    version_origin: str
    derived_from_version: int | None
    title: str
    title_source: str
    tags: list[str]
    tag_source: str
    markdown_content: str | None
    summary: str
    created_at: datetime

    model_config = {"from_attributes": True}


class AIVersionCreateRequest(BaseModel):
    instructions: str | None = Field(default=None, max_length=2000)
    source_version: int | None = Field(default=None, ge=1)


# ── Mind Graph ──────────────────────────────────────────

class GraphNodeOut(BaseModel):
    id: str
    label: str
    note_count: int
    size: float = 1
    color: str = "#999999"
    x: float = 0
    y: float = 0
    z: float = 0
    rank: int = 0
    degree: float = 0
    cluster: str | None = None
    is_core: bool = False


class GraphEdgeOut(BaseModel):
    source: str
    target: str
    strength: float = 1
    relation: str = "co_occurrence"
    co_occurrence_count: int = 0
    content_similarity: float = 0
    shared_note_count: int = 0


class GraphResponse(BaseModel):
    nodes: list[GraphNodeOut]
    edges: list[GraphEdgeOut]
    core_mind_note_count: int = 0
    layout_seed: int = 0
    focus_node_id: str | None = None


class MindNodeNoteOut(BaseModel):
    id: str
    title: str
    status: str
    tags: list[str]
    created_at: datetime
    updated_at: datetime
    snippet: str


class MindNodeNotesResponse(BaseModel):
    node_id: str
    tag: str
    total: int
    page: int
    page_size: int
    items: list[MindNodeNoteOut]


class SynthesisUpdateOut(BaseModel):
    id: str
    title: str
    description: str
    created_at: datetime


# ── Insights ──────────────────────────────────────────

class InsightOut(BaseModel):
    id: str
    generation_id: str
    type: str
    status: str
    title: str
    description: str
    confidence: float = 0.0
    importance_score: float = 0.0
    novelty_score: float = 0.0
    report_version: int = 1
    evidence_count: int = 0
    action_items_count: int = 0
    source_notes_count: int = 0  # Number of unique source notes referenced
    created_at: datetime
    generated_at: datetime


class InsightSourceNoteOut(BaseModel):
    id: str
    title: str
    tags: list[str] = []
    updated_at: datetime


class InsightEvidenceItemOut(BaseModel):
    id: str
    note_id: str
    note_title: str
    quote: str
    rationale: str
    sort_order: int = 0


class InsightActionItemOut(BaseModel):
    id: str
    title: str
    detail: str
    priority: str
    sort_order: int = 0


class InsightShareCardMetricOut(BaseModel):
    label: str
    value: str


class InsightShareCardOut(BaseModel):
    theme: str
    eyebrow: str
    headline: str
    summary: str
    highlight: str | None = None
    evidence_quote: str | None = None
    evidence_source: str | None = None
    action_title: str | None = None
    action_detail: str | None = None
    metrics: list[InsightShareCardMetricOut] = Field(default_factory=list)
    footer: str


class InsightAgentRunOut(BaseModel):
    id: str
    agent_name: str
    stage: str
    status: str
    session_id: str | None = None
    model_name: str | None = None
    duration_ms: int | None = None
    api_duration_ms: int | None = None
    total_cost_usd: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    summary: str | None = None
    error: str | None = None
    started_at: datetime
    completed_at: datetime | None = None


class InsightGenerationOut(BaseModel):
    id: str
    status: str
    workflow_version: str
    summary: str | None = None
    is_active: bool = False
    total_reports: int = 0
    error: str | None = None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
    total_duration_ms: int = 0
    total_api_duration_ms: int = 0
    total_cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    agent_runs: list[InsightAgentRunOut] = Field(default_factory=list)


class InsightDetailOut(InsightOut):
    report_markdown: str
    review_summary: str | None = None
    source_notes: list[InsightSourceNoteOut]
    evidence_items: list[InsightEvidenceItemOut]
    action_items: list[InsightActionItemOut]
    share_card: InsightShareCardOut
    generation: InsightGenerationOut | None = None


# ── Ground (Social) ──────────────────────────────────

class PublicUserOut(BaseModel):
    id: str
    username: str
    avatar_url: str | None = None


class GroundFeedItem(BaseModel):
    id: str
    note_id: str
    author: PublicUserOut
    title: str
    preview: str
    likes: int = 0
    liked_by_me: bool = False
    shared_at: datetime


class GroundPostOut(BaseModel):
    id: str
    post_type: str  # note | mind_graph | insight
    ref_id: str
    author: PublicUserOut
    title: str
    preview: str
    extra_json: str | None = None
    likes: int = 0
    liked_by_me: bool = False
    relevance_score: float | None = None
    created_at: datetime


# ── Profile ──────────────────────────────────────────

class ProfileUpdate(BaseModel):
    username: str | None = None
    email: EmailStr | None = None


class UserProfileUpdate(BaseModel):
    """Schema for PATCH /me — only allow specific fields."""
    model_config = ConfigDict(str_strip_whitespace=True)

    username: str | None = Field(default=None, min_length=2, max_length=64)
    avatar_url: str | None = Field(default=None, max_length=512)


# ── Simple action responses ──────────────────────────

class TagListResponse(BaseModel):
    tags: list[str]


class StatusResponse(BaseModel):
    status: str


class NoteLikeResponse(BaseModel):
    note_id: str
    liked: bool


class NoteShareResponse(BaseModel):
    note_id: str
    shared: bool


class PostLikeResponse(BaseModel):
    post_id: str
    liked: bool


class ExploreResponse(BaseModel):
    trending: list = Field(default_factory=list)
    recommended: list = Field(default_factory=list)
    categories: list = Field(default_factory=list)


class FileRegisterRequest(BaseModel):
    key: str = Field(min_length=1, max_length=1024)
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(min_length=1, max_length=128)
    size: int = Field(ge=0)
    note_id: str | None = None
