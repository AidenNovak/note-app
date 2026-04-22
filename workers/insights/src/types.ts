/**
 * Type definitions aligned with backend Python schemas.
 *
 * These map directly to:
 *   - backend/app/models.py (SQLAlchemy ORM)
 *   - backend/app/schemas.py (Pydantic API models)
 *   - backend/app/intelligence/insights/schemas_ai.py (AI structured output)
 */

// ── TaskStatus ──
export type TaskStatus = "PENDING" | "PROCESSING" | "COMPLETED" | "FAILED";

// ── Note ──
export interface Note {
  id: string;
  title: string;
  markdown_content: string | null;
  tags: string[];
  created_at: string;
  updated_at: string;
  user_id: string;
}

// ── MindConnection ──
export interface MindConnection {
  id: string;
  user_id: string;
  note_a_id: string;
  note_b_id: string;
  shared_tags: string[];
  similarity_score: number;
  connection_type: string;
}

// ── InsightGeneration ──
export interface InsightGeneration {
  id: string;
  user_id: string;
  status: TaskStatus;
  workflow_version: string;
  summary: string | null;
  is_active: boolean;
  total_reports: number;
  error: string | null;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
  workspace_json: string | null;
}

// ── InsightReport ──
export interface InsightReport {
  id: string;
  generation_id: string;
  user_id: string;
  type: string;
  title: string;
  description: string;
  status: string;
  report_version: number;
  confidence: number;
  importance_score: number;
  novelty_score: number;
  card_rank: number;
  report_markdown: string;
  report_json: string;
  source_note_ids: string[];
  created_at: string;
  generated_at: string;
  evidence_items: InsightEvidenceItem[];
  action_items: InsightActionItem[];
}

// ── InsightEvidenceItem ──
export interface InsightEvidenceItem {
  id: string;
  report_id: string;
  note_id: string;
  quote: string;
  rationale: string;
  sort_order: number;
}

// ── InsightActionItem ──
export interface InsightActionItem {
  id: string;
  report_id: string;
  title: string;
  detail: string;
  priority: "high" | "medium" | "low";
  sort_order: number;
}

// ── Agent Config ──

export interface AgentConfig {
  modelTier: "fast" | "capable";
  persona: string;
}

// ── AI Structured Output (aligned with schemas_ai.py) ──

export interface ShareCardMetric {
  label: string;
  value: string;
}

export interface ShareCardOutput {
  theme: string;
  eyebrow: string;
  headline: string;
  summary: string;
  highlight: string;
  evidence_quote: string;
  evidence_source: string;
  action_title: string;
  action_detail: string;
  metrics: ShareCardMetric[];
  footer: string;
}

export interface EvidenceItemOutput {
  note_id: string;
  quote: string;
  rationale: string;
}

export interface ActionItemOutput {
  title: string;
  detail: string;
  priority: "high" | "medium" | "low";
}

export interface InsightReportOutput {
  title: string;
  description: string;
  type: string;
  report_markdown: string;
  thinking_trace: string | null;
  confidence: number;
  importance_score: number;
  novelty_score: number;
  evidence_items: EvidenceItemOutput[];
  action_items: ActionItemOutput[];
  share_card: ShareCardOutput | null;
}

export interface AngleOutput {
  angle_name: string;
  description: string;
  note_ids: string[];
  type_hint: string;
  /** Optional visual theme color assigned by the pipeline */
  _color?: { accent: string; bg: string };
}

export interface AngleListOutput {
  angles: AngleOutput[];
}

// ── Events (aligned with SSE event types) ──

export interface InsightEvent {
  type: string;
  sequence: number;
  [key: string]: unknown;
}

// ── Workspace (aligned with agent.py workspace dict) ──

export interface AgentWorkspace {
  reports?: Array<{ report: InsightReportOutput; note_ids: string[] }>;
  note_map?: Record<string, Note>;
  conversation?: Array<{ role: string; content: string; ts: string }>;
  memory?: string;
}

// ── API Request/Response ──

export interface GenerateRequest {
  user_id: string;
  note_ids?: string[]; // optional: limit to specific notes
}

export interface ChatRequest {
  message: string;
}

export interface RegenerateRequest {
  angle_index: number;
  instruction?: string;
}
