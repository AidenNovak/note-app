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
    avatar_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    notes: Mapped[List["Note"]] = relationship(back_populates="owner", cascade="all, delete-orphan")
    folders: Mapped[List["Folder"]] = relationship(back_populates="owner", cascade="all, delete-orphan")


class Folder(Base):
    __tablename__ = "folders"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    parent_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("folders.id"), nullable=True)
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
    folder_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("folders.id"), nullable=True)
    title_source: Mapped[MetadataSource] = mapped_column(Enum(MetadataSource), default=MetadataSource.SYSTEM)
    tag_source: Mapped[MetadataSource] = mapped_column(Enum(MetadataSource), default=MetadataSource.NONE)
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
