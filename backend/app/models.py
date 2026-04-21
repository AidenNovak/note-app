import enum
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utcnow():
    return datetime.now(timezone.utc)


class TaskType(str, enum.Enum):
    TEXT_TO_MARKDOWN = "text_to_markdown"
    VOICE_TO_TEXT = "voice_to_text"
    VIDEO_TO_FRAMES = "video_to_frames"
    FILE_TO_MARKDOWN = "file_to_markdown"
    IMAGE_TO_MARKDOWN = "image_to_markdown"


class TaskStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class SourceType(str, enum.Enum):
    TEXT = "text"
    VOICE = "voice"
    VIDEO = "video"
    FILE = "file"
    IMAGE = "image"


class MetadataSource(str, enum.Enum):
    HUMAN = "human"
    AI = "ai"
    SYSTEM = "system"
    NONE = "none"


class AIStatus(str, enum.Enum):
    IDLE = "idle"
    PENDING = "pending"
    EMBEDDING = "embedding"
    TAGGING = "tagging"
    DONE = "done"
    FAILED = "failed"


class VersionOrigin(str, enum.Enum):
    HUMAN = "human"
    AI = "ai"
    SYSTEM = "system"


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    avatar_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    notes: Mapped[List["Note"]] = relationship(back_populates="owner", cascade="all, delete-orphan")
    folders: Mapped[List["Folder"]] = relationship(back_populates="owner", cascade="all, delete-orphan")
    oauth_accounts: Mapped[List["OAuthAccount"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    sessions: Mapped[List["UserSession"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class Folder(Base):
    __tablename__ = "folders"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    parent_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("folders.id"), nullable=True, index=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    owner: Mapped["User"] = relationship(back_populates="folders")
    parent: Mapped[Optional["Folder"]] = relationship(remote_side="Folder.id", backref="children")
    notes: Mapped[List["Note"]] = relationship(back_populates="folder")


class Note(Base):
    __tablename__ = "notes"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    title: Mapped[str] = mapped_column(String(255))
    markdown_content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[TaskStatus] = mapped_column(Enum(TaskStatus), default=TaskStatus.PENDING)
    source_type: Mapped[Optional[SourceType]] = mapped_column(Enum(SourceType), nullable=True)
    source_file_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("files.id"), nullable=True)
    folder_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("folders.id"), nullable=True, index=True)
    title_source: Mapped[MetadataSource] = mapped_column(Enum(MetadataSource), default=MetadataSource.SYSTEM)
    tag_source: Mapped[MetadataSource] = mapped_column(Enum(MetadataSource), default=MetadataSource.NONE)
    ai_status: Mapped[AIStatus] = mapped_column(Enum(AIStatus), default=AIStatus.IDLE)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    current_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    owner: Mapped["User"] = relationship(back_populates="notes")
    folder: Mapped[Optional["Folder"]] = relationship(back_populates="notes")
    source_file: Mapped[Optional["File"]] = relationship(foreign_keys=[source_file_id])
    attachments: Mapped[List["File"]] = relationship(back_populates="note", foreign_keys="File.note_id", cascade="all, delete-orphan")
    tags: Mapped[List["NoteTag"]] = relationship(back_populates="note", cascade="all, delete-orphan")
    versions: Mapped[List["NoteVersion"]] = relationship(back_populates="note", cascade="all, delete-orphan")
    tasks: Mapped[List["ProcessingTask"]] = relationship(back_populates="note", cascade="all, delete-orphan")


class NoteTag(Base):
    __tablename__ = "note_tags"
    __table_args__ = (UniqueConstraint("note_id", "tag"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    note_id: Mapped[str] = mapped_column(String(36), ForeignKey("notes.id"), index=True)
    tag: Mapped[str] = mapped_column(String(64), index=True)
    note: Mapped["Note"] = relationship(back_populates="tags")


class File(Base):
    __tablename__ = "files"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    filename: Mapped[str] = mapped_column(String(255))
    mime_type: Mapped[str] = mapped_column(String(128))
    size: Mapped[int] = mapped_column(Integer)
    storage_path: Mapped[str] = mapped_column(String(512))
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    note_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("notes.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    note: Mapped[Optional["Note"]] = relationship(back_populates="attachments", foreign_keys=[note_id])


class ProcessingTask(Base):
    __tablename__ = "processing_tasks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    note_id: Mapped[str] = mapped_column(String(36), ForeignKey("notes.id"), index=True)
    type: Mapped[TaskType] = mapped_column(Enum(TaskType))
    status: Mapped[TaskStatus] = mapped_column(Enum(TaskStatus), default=TaskStatus.PENDING)
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    input_file_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("files.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    note: Mapped["Note"] = relationship(back_populates="tasks")


class NoteVersion(Base):
    __tablename__ = "note_versions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    note_id: Mapped[str] = mapped_column(String(36), ForeignKey("notes.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    version_origin: Mapped[VersionOrigin] = mapped_column(Enum(VersionOrigin), default=VersionOrigin.HUMAN)
    derived_from_version: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    title: Mapped[str] = mapped_column(String(255), default="Untitled Note")
    title_source: Mapped[MetadataSource] = mapped_column(Enum(MetadataSource), default=MetadataSource.SYSTEM)
    tags_json: Mapped[str] = mapped_column(Text, default="[]")
    tag_source: Mapped[MetadataSource] = mapped_column(Enum(MetadataSource), default=MetadataSource.NONE)
    markdown_content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    summary: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    note: Mapped["Note"] = relationship(back_populates="versions")


class InsightGeneration(Base):
    __tablename__ = "insight_generations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    status: Mapped[TaskStatus] = mapped_column(Enum(TaskStatus), default=TaskStatus.PENDING)
    workflow_version: Mapped[str] = mapped_column(String(64), default="cloud-sdk-v1")
    session_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    workspace_path: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    total_reports: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    reports: Mapped[List["InsightReport"]] = relationship(back_populates="generation", cascade="all, delete-orphan")
    agent_runs: Mapped[List["InsightAgentRun"]] = relationship(back_populates="generation", cascade="all, delete-orphan")
    logs: Mapped[List["InsightGenerationLog"]] = relationship(back_populates="generation", cascade="all, delete-orphan")
    events: Mapped[List["InsightEvent"]] = relationship(cascade="all, delete-orphan")
    workspace_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    session_state: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)


class InsightReport(Base):
    __tablename__ = "insight_reports"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    generation_id: Mapped[str] = mapped_column(String(36), ForeignKey("insight_generations.id"), index=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    type: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="published")
    report_version: Mapped[int] = mapped_column(Integer, default=1)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    importance_score: Mapped[float] = mapped_column(Float, default=0.0)
    novelty_score: Mapped[float] = mapped_column(Float, default=0.0)
    review_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    card_rank: Mapped[int] = mapped_column(Integer, default=0)
    report_markdown: Mapped[str] = mapped_column(Text)
    report_json: Mapped[str] = mapped_column(Text)
    source_note_ids: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    generation: Mapped["InsightGeneration"] = relationship(back_populates="reports")
    evidence_items: Mapped[List["InsightEvidenceItem"]] = relationship(back_populates="report", cascade="all, delete-orphan")
    action_items: Mapped[List["InsightActionItem"]] = relationship(back_populates="report", cascade="all, delete-orphan")


class InsightEvidenceItem(Base):
    __tablename__ = "insight_evidence_items"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    report_id: Mapped[str] = mapped_column(String(36), ForeignKey("insight_reports.id"), index=True)
    note_id: Mapped[str] = mapped_column(String(36), ForeignKey("notes.id"), index=True)
    quote: Mapped[str] = mapped_column(Text)
    rationale: Mapped[str] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    report: Mapped["InsightReport"] = relationship(back_populates="evidence_items")


class InsightActionItem(Base):
    __tablename__ = "insight_action_items"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    report_id: Mapped[str] = mapped_column(String(36), ForeignKey("insight_reports.id"), index=True)
    title: Mapped[str] = mapped_column(String(255))
    detail: Mapped[str] = mapped_column(Text)
    priority: Mapped[str] = mapped_column(String(16), default="medium")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    report: Mapped["InsightReport"] = relationship(back_populates="action_items")


class InsightAgentRun(Base):
    __tablename__ = "insight_agent_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    generation_id: Mapped[str] = mapped_column(String(36), ForeignKey("insight_generations.id"), index=True)
    agent_name: Mapped[str] = mapped_column(String(64))
    stage: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="completed")
    session_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    model_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    api_duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    total_cost_usd: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    input_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    output_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    generation: Mapped["InsightGeneration"] = relationship(back_populates="agent_runs")


class InsightGenerationLog(Base):
    __tablename__ = "insight_generation_logs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    generation_id: Mapped[str] = mapped_column(String(36), ForeignKey("insight_generations.id"), index=True)
    event_index: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(64))
    stage: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    group_index: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    payload_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    generation: Mapped["InsightGeneration"] = relationship(back_populates="logs")


class InsightEvent(Base):
    """Persistent event stream for insight generation — replaces process-local buffers."""

    __tablename__ = "insight_events"
    __table_args__ = (
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    generation_id: Mapped[str] = mapped_column(String(36), ForeignKey("insight_generations.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(64))
    sequence: Mapped[int] = mapped_column(Integer, default=0, index=True)
    group_index: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    payload_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SharedNote(Base):
    __tablename__ = "shared_notes"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    note_id: Mapped[str] = mapped_column(String(36), ForeignKey("notes.id"), unique=True, index=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    shared_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    note: Mapped["Note"] = relationship()
    user: Mapped["User"] = relationship()
    likes: Mapped[List["NoteLike"]] = relationship(back_populates="shared_note", cascade="all, delete-orphan")


class NoteLike(Base):
    __tablename__ = "note_likes"
    __table_args__ = (UniqueConstraint("shared_note_id", "user_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    shared_note_id: Mapped[str] = mapped_column(String(36), ForeignKey("shared_notes.id"), index=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    shared_note: Mapped["SharedNote"] = relationship(back_populates="likes")


class GroundPost(Base):
    """A post on the Ground feed — can be a mind graph snapshot or an insight report."""
    __tablename__ = "ground_posts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    post_type: Mapped[str] = mapped_column(String(32))  # note | mind_graph | insight
    ref_id: Mapped[str] = mapped_column(String(36), index=True)  # note_id or insight_id
    title: Mapped[str] = mapped_column(String(255))
    preview: Mapped[str] = mapped_column(Text, default="")
    extra_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # graph snapshot, etc.
    is_hidden: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    hidden_reason: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    hidden_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    user: Mapped["User"] = relationship()
    post_likes: Mapped[List["GroundPostLike"]] = relationship(back_populates="post", cascade="all, delete-orphan")


class GroundPostLike(Base):
    __tablename__ = "ground_post_likes"
    __table_args__ = (UniqueConstraint("post_id", "user_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    post_id: Mapped[str] = mapped_column(String(36), ForeignKey("ground_posts.id"), index=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    post: Mapped["GroundPost"] = relationship(back_populates="post_likes")


class PostReport(Base):
    """A user's report of a ground post for moderation review."""
    __tablename__ = "ground_post_reports"
    __table_args__ = (UniqueConstraint("post_id", "reporter_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    post_id: Mapped[str] = mapped_column(String(36), ForeignKey("ground_posts.id", ondelete="CASCADE"), index=True)
    reporter_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    reason: Mapped[str] = mapped_column(String(32))  # spam | harassment | nsfw | violence | hate | self_harm | illegal | other
    details: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="open", nullable=False, index=True)  # open | actioned | dismissed
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    reviewer_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class UserBlock(Base):
    """A blocker->blocked relationship. The blocker no longer sees the blocked user's posts/actions."""
    __tablename__ = "user_blocks"
    __table_args__ = (UniqueConstraint("blocker_id", "blocked_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    blocker_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    blocked_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PostHide(Base):
    """A user-level soft hide — the user doesn't want to see this post again."""
    __tablename__ = "ground_post_hides"
    __table_args__ = (UniqueConstraint("user_id", "post_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    post_id: Mapped[str] = mapped_column(String(36), ForeignKey("ground_posts.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class NoteEmbedding(Base):
    __tablename__ = "note_embeddings"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    note_id: Mapped[str] = mapped_column(String(36), ForeignKey("notes.id"), unique=True, index=True)
    embedding_json: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(String(128), default="openai/text-embedding-3-small")
    dimension: Mapped[int] = mapped_column(Integer, default=1536)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    note: Mapped["Note"] = relationship()


class NoteSimilarity(Base):
    __tablename__ = "note_similarities"
    __table_args__ = (UniqueConstraint("note_id", "similar_note_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    note_id: Mapped[str] = mapped_column(String(36), ForeignKey("notes.id"), index=True)
    similar_note_id: Mapped[str] = mapped_column(String(36), ForeignKey("notes.id"), index=True)
    similarity_score: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MindConnection(Base):
    """Records a discovered connection between two notes from the mind graph."""
    __tablename__ = "mind_connections"
    __table_args__ = (UniqueConstraint("note_a_id", "note_b_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    note_a_id: Mapped[str] = mapped_column(String(36), ForeignKey("notes.id"), index=True)
    note_b_id: Mapped[str] = mapped_column(String(36), ForeignKey("notes.id"), index=True)
    shared_tags: Mapped[str] = mapped_column(Text, default="[]")  # JSON list of shared tag strings
    similarity_score: Mapped[float] = mapped_column(Float, default=0.0)
    connection_type: Mapped[str] = mapped_column(String(32), default="tag_cooccurrence")  # tag_cooccurrence | semantic | hybrid
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ── Auth & Identity ──────────────────────────────────────

class OAuthAccount(Base):
    """Links a user to an external OAuth provider (Apple, Google, GitHub)."""
    __tablename__ = "oauth_accounts"
    __table_args__ = (
        UniqueConstraint("provider", "provider_account_id"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    provider: Mapped[str] = mapped_column(String(32), index=True)  # apple, google, github
    provider_account_id: Mapped[str] = mapped_column(String(255))  # sub from provider
    access_token: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    refresh_token: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    id_token: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    token_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    scope: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    user: Mapped["User"] = relationship(back_populates="oauth_accounts")


class UserSession(Base):
    """Server-side session for multi-device support and active revocation."""
    __tablename__ = "user_sessions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # opaque token
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    refresh_token_hash: Mapped[str] = mapped_column(String(128), unique=True)
    ip_address: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    user: Mapped["User"] = relationship(back_populates="sessions")


class ApiToken(Base):
    """Personal Access Token (PAT) for CLI / automation usage.

    Tokens are returned to the user in plaintext exactly once at creation time
    (prefix `atl_` + base32 random). Only the sha256 hash is stored.
    """
    __tablename__ = "api_tokens"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    # First 12 chars of the plaintext token (e.g. "atl_ab12cd34") — safe to display in UI.
    token_prefix: Mapped[str] = mapped_column(String(16), index=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    # Space-separated subset of: read, write, admin
    scopes: Mapped[str] = mapped_column(String(128), default="read")
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class EmailVerification(Base):
    """Token for email verification and password reset flows."""
    __tablename__ = "email_verifications"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    purpose: Mapped[str] = mapped_column(String(32))  # verify_email, reset_password
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ── Billing & Payments ───────────────────────────────────

class BillingCustomer(Base):
    """Maps a user to their payment provider customer record."""
    __tablename__ = "billing_customers"
    __table_args__ = (
        UniqueConstraint("user_id", "provider"),
        UniqueConstraint("provider", "provider_customer_id"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    provider: Mapped[str] = mapped_column(String(32))  # stripe, revenuecat
    provider_customer_id: Mapped[str] = mapped_column(String(255))
    email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class BillingSubscription(Base):
    """Tracks active subscriptions from Stripe or RevenueCat."""
    __tablename__ = "billing_subscriptions"
    __table_args__ = (
        UniqueConstraint("provider", "provider_subscription_id"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    provider: Mapped[str] = mapped_column(String(32))
    provider_subscription_id: Mapped[str] = mapped_column(String(255))
    provider_customer_id: Mapped[str] = mapped_column(String(255))
    plan_id: Mapped[str] = mapped_column(String(64))
    price_id: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32))  # active, paused, canceled, past_due, etc.
    current_period_end: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, default=False)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    provider_event_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class BillingPurchase(Base):
    """One-time or lifetime purchases."""
    __tablename__ = "billing_purchases"
    __table_args__ = (
        UniqueConstraint("provider", "provider_payment_intent_id"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    provider: Mapped[str] = mapped_column(String(32))
    provider_payment_intent_id: Mapped[str] = mapped_column(String(255))
    plan_id: Mapped[str] = mapped_column(String(64))
    price_id: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32))  # succeeded, pending, failed, refunded
    paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    provider_event_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class BillingCheckoutSession(Base):
    """Records Stripe checkout session attempts."""
    __tablename__ = "billing_checkout_sessions"
    __table_args__ = (
        UniqueConstraint("provider", "provider_session_id"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    provider: Mapped[str] = mapped_column(String(32))
    provider_session_id: Mapped[str] = mapped_column(String(255))
    plan_id: Mapped[str] = mapped_column(String(64))
    price_id: Mapped[str] = mapped_column(String(128))
    mode: Mapped[str] = mapped_column(String(32))  # subscription, payment
    status: Mapped[str] = mapped_column(String(32), default="created")  # created, completed, expired
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class BillingEvent(Base):
    """Webhook idempotency: stores processed provider events to prevent duplicates."""
    __tablename__ = "billing_events"
    __table_args__ = (
        UniqueConstraint("provider", "provider_event_id"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    provider: Mapped[str] = mapped_column(String(32))
    provider_event_id: Mapped[str] = mapped_column(String(255))
    event_type: Mapped[str] = mapped_column(String(64))
    payload_json: Mapped[str] = mapped_column(Text)
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ── Push Notifications ────────────────────────────────


class NotificationType(str, enum.Enum):
    POST_LIKED = "post_liked"
    NOTE_LIKED = "note_liked"
    INSIGHT_READY = "insight_ready"
    MIND_CONNECTION = "mind_connection"
    MILESTONE = "milestone"
    SYSTEM = "system"


class DeviceToken(Base):
    """Stores Expo push tokens for each user's device."""
    __tablename__ = "device_tokens"
    __table_args__ = (
        UniqueConstraint("user_id", "token"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    token: Mapped[str] = mapped_column(String(512), index=True)
    platform: Mapped[str] = mapped_column(String(16))  # ios, android
    device_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class NotificationPreference(Base):
    """Per-user notification preferences (which types are enabled)."""
    __tablename__ = "notification_preferences"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), unique=True, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)  # global kill switch
    post_liked: Mapped[bool] = mapped_column(Boolean, default=True)
    note_liked: Mapped[bool] = mapped_column(Boolean, default=True)
    insight_ready: Mapped[bool] = mapped_column(Boolean, default=True)
    mind_connection: Mapped[bool] = mapped_column(Boolean, default=True)
    milestone: Mapped[bool] = mapped_column(Boolean, default=True)
    quiet_hours_start: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # 0-23
    quiet_hours_end: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class PushNotificationLog(Base):
    """Audit log for sent push notifications."""
    __tablename__ = "push_notification_logs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    type: Mapped[str] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(String(512))
    data_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="sent")  # sent, delivered, failed
    error: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
