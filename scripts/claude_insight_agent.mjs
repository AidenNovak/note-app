import fs from "node:fs"
import os from "node:os"
import path from "node:path"
import { pathToFileURL } from "node:url"

const workspaceArg = process.argv[2]
if (!workspaceArg) {
  console.error("Workspace path is required")
  process.exit(1)
}

const workspacePath = path.resolve(workspaceArg)
const projectRoot = process.cwd()
const contextPath = path.join(workspacePath, "context.json")
const taskConfigPath = path.join(workspacePath, "task_config.json")

const homeDir = os.homedir()
const settingsPath = path.join(homeDir, ".claude", "settings.json")
if (fs.existsSync(settingsPath)) {
  try {
    const settings = JSON.parse(fs.readFileSync(settingsPath, "utf8"))
    if (settings.env) {
      for (const [key, value] of Object.entries(settings.env)) {
        if (!process.env[key]) {
          process.env[key] = value
        }
      }
    }
  } catch (error) {
    console.warn("Failed to load local Claude settings:", error.message)
  }
}

let taskConfig = { type: "insight", workflow_version: "cloud-sdk-v1", stages: [] }
if (fs.existsSync(taskConfigPath)) {
  try {
    taskConfig = JSON.parse(fs.readFileSync(taskConfigPath, "utf8"))
  } catch (error) {
    console.warn("Failed to load task config:", error.message)
  }
}

if (!fs.existsSync(contextPath)) {
  console.error(`Missing context file at ${contextPath}`)
  process.exit(1)
}

const sdkRoot = process.env.CLAUDE_AGENT_SDK_ROOT || path.join(os.homedir(), "agent-sdk")
const sdkPath = path.join(
  sdkRoot,
  "node_modules",
  "@anthropic-ai",
  "claude-agent-sdk",
  "sdk.mjs",
)

if (!fs.existsSync(sdkPath)) {
  console.error(`Claude Agent SDK not found at ${sdkPath}`)
  process.exit(1)
}

const { query } = await import(pathToFileURL(sdkPath).href)

function emitProgress(payload) {
  process.stdout.write(
    `PROGRESS: ${JSON.stringify({ timestamp: new Date().toISOString(), ...payload })}\n`,
  )
}

function stripJson(raw) {
  const trimmed = String(raw || "").trim()
  if (!trimmed.startsWith("```")) {
    return trimmed
  }
  return trimmed.replace(/^```(?:json)?\s*/, "").replace(/\s*```$/, "").trim()
}

function extractJsonCandidate(raw) {
  const stripped = stripJson(raw)
  const objectStart = stripped.indexOf("{")
  const objectEnd = stripped.lastIndexOf("}")
  if (objectStart !== -1 && objectEnd !== -1 && objectEnd > objectStart) {
    return stripped.slice(objectStart, objectEnd + 1)
  }
  const arrayStart = stripped.indexOf("[")
  const arrayEnd = stripped.lastIndexOf("]")
  if (arrayStart !== -1 && arrayEnd !== -1 && arrayEnd > arrayStart) {
    return stripped.slice(arrayStart, arrayEnd + 1)
  }
  return stripped
}

function parseJsonOrDie(raw, label) {
  const rawStr = String(raw || "").trim()
  if (!rawStr) {
    throw new Error(`${label} returned empty output`)
  }

  // Collect all text chunks if the result contains multiple code blocks or mixed content
  const candidates = [
    rawStr,
    stripJson(rawStr),
    extractJsonCandidate(rawStr),
  ]

  // Also try extracting JSON from within markdown code blocks more aggressively
  const codeBlockMatch = rawStr.match(/```(?:json)?\s*\n?([\s\S]*?)```/)
  if (codeBlockMatch) {
    candidates.push(codeBlockMatch[1].trim())
  }

  for (const candidate of candidates) {
    if (!candidate) {
      continue
    }
    try {
      return JSON.parse(candidate)
    } catch {
      // Try cleaning common issues
      try {
        const cleaned = candidate
          .replace(/,\s*([}\]])/g, "$1")           // trailing commas
          .replace(/[\x00-\x08\x0b\x0c\x0e-\x1f]/g, "") // control chars (keep \n \r \t)
        return JSON.parse(cleaned)
      } catch {
        // next candidate
      }
    }
  }

  throw new Error(`${label} was not valid JSON (length=${rawStr.length}, starts=${rawStr.substring(0, 80)})`)
}

function toNumber(value) {
  const numeric = Number(value)
  return Number.isFinite(numeric) ? numeric : null
}

function toInt(value) {
  const numeric = toNumber(value)
  return numeric === null ? null : Math.max(0, Math.round(numeric))
}

function toFloat(value) {
  const numeric = toNumber(value)
  return numeric === null ? null : Math.max(0, numeric)
}

function extractUsageMetrics(resultMessage) {
  const directInput =
    toInt(resultMessage.input_tokens) ??
    toInt(resultMessage.inputTokens) ??
    toInt(resultMessage.usage?.input_tokens) ??
    toInt(resultMessage.usage?.inputTokens)
  const directOutput =
    toInt(resultMessage.output_tokens) ??
    toInt(resultMessage.outputTokens) ??
    toInt(resultMessage.usage?.output_tokens) ??
    toInt(resultMessage.usage?.outputTokens)

  const modelUsage = resultMessage.modelUsage
  if (!modelUsage || typeof modelUsage !== "object" || Array.isArray(modelUsage)) {
    return {
      modelName: String(resultMessage.model || resultMessage.model_name || "").trim() || null,
      inputTokens: directInput,
      outputTokens: directOutput,
    }
  }

  const [firstEntry] = Object.entries(modelUsage)
  let inputTokens = directInput ?? 0
  let outputTokens = directOutput ?? 0

  for (const usage of Object.values(modelUsage)) {
    if (!usage || typeof usage !== "object" || Array.isArray(usage)) {
      continue
    }
    inputTokens += toInt(usage.input_tokens) ?? toInt(usage.inputTokens) ?? toInt(usage.input) ?? 0
    outputTokens +=
      toInt(usage.output_tokens) ?? toInt(usage.outputTokens) ?? toInt(usage.output) ?? 0
  }

  return {
    modelName: String(firstEntry?.[0] || resultMessage.model || resultMessage.model_name || "").trim() || null,
    inputTokens,
    outputTokens,
  }
}

async function runAgent(stageMeta, prompt) {
  const startedAt = new Date().toISOString()
  emitProgress({ type: "agent_start", agent: stageMeta.agent, stage: stageMeta.key })

  let toolUseCount = 0
  let textChunkCount = 0
  let toolResultCount = 0
  const messages = []
  const textChunks = []

  for await (const message of query({
    prompt,
    options: {
      cwd: projectRoot,
      additionalDirectories: [workspacePath],
      settingSources: ["project", "user"],
      agent: stageMeta.agent,
      allowedTools: Array.isArray(stageMeta.allowed_tools) ? stageMeta.allowed_tools : [],
      permissionMode: "dontAsk",
      maxTurns: Number.isFinite(Number(stageMeta.max_turns))
        ? Number(stageMeta.max_turns)
        : Number.parseInt(process.env.INSIGHT_AGENT_MAX_TURNS || "30", 10),
      effort: stageMeta.effort || "low",
      thinkingConfig: { type: "disabled" },
    },
  })) {
    if (message.type === "assistant" && message.message?.content) {
      for (const part of message.message.content) {
        if (part.type === "thinking") {
          emitProgress({ type: "thinking", agent: stageMeta.agent, content: part.thinking })
        } else if (part.type === "tool_use") {
          toolUseCount += 1
          emitProgress({
            type: "tool_use",
            stage: stageMeta.key,
            agent: stageMeta.agent,
            tool: part.name,
            input: part.input,
            tool_use_count: toolUseCount,
          })
        } else if (part.type === "text") {
          textChunkCount += 1
          textChunks.push(part.text)
          emitProgress({
            type: "text",
            stage: stageMeta.key,
            agent: stageMeta.agent,
            content: part.text,
            text_chunk_count: textChunkCount,
          })
        }
      }
    } else if (message.type === "user" && message.tool_use_result) {
      toolResultCount += 1
      emitProgress({
        type: "tool_result",
        stage: stageMeta.key,
        agent: stageMeta.agent,
        tool_result_count: toolResultCount,
      })
    }
    messages.push(message)
  }

  const completedAt = new Date().toISOString()
  const resultMessage = messages.find((message) => message.type === "result")
  if (!resultMessage || resultMessage.subtype !== "success") {
    throw new Error(`${stageMeta.agent} did not return a successful result`)
  }

  const usageMetrics = extractUsageMetrics(resultMessage)
  // Use resultMessage.result first; fall back to concatenated text chunks
  const rawResult = stripJson(resultMessage.result) || textChunks.join("")
  return {
    startedAt,
    completedAt,
    sessionId: String(resultMessage.session_id || resultMessage.sessionId || "").trim() || null,
    modelName: usageMetrics.modelName,
    durationMs: toInt(resultMessage.duration_ms ?? resultMessage.durationMs),
    apiDurationMs: toInt(resultMessage.duration_api_ms ?? resultMessage.durationApiMs),
    totalCostUsd: toFloat(resultMessage.total_cost_usd ?? resultMessage.totalCostUsd),
    inputTokens: usageMetrics.inputTokens,
    outputTokens: usageMetrics.outputTokens,
    raw: rawResult,
  }
}

async function runStage(stageMeta, prompt) {
  emitProgress({
    type: "stage_start",
    stage: stageMeta.key,
    agent: stageMeta.agent,
  })

  let heartbeatTimer = null
  heartbeatTimer = setInterval(() => {
    emitProgress({
      type: "heartbeat",
      stage: stageMeta.key,
      agent: stageMeta.agent,
    })
  }, 2500)

  try {
    const outcome = await runAgent(stageMeta, prompt)
    emitProgress({
      type: "stage_end",
      stage: stageMeta.key,
      agent: stageMeta.agent,
      status: "completed",
      duration_ms: outcome.durationMs,
      api_duration_ms: outcome.apiDurationMs,
      input_tokens: outcome.inputTokens,
      output_tokens: outcome.outputTokens,
      total_cost_usd: outcome.totalCostUsd,
    })
    return outcome
  } catch (error) {
    emitProgress({
      type: "stage_end",
      stage: stageMeta.key,
      agent: stageMeta.agent,
      status: "failed",
      error: String(error?.message || error),
    })
    throw error
  } finally {
    if (heartbeatTimer) {
      clearInterval(heartbeatTimer)
    }
  }
}

function pushRun(agentRuns, stageMeta, outcome, parsed) {
  agentRuns.push({
    agent_name: stageMeta.agent,
    stage: stageMeta.key,
    status: "completed",
    session_id: outcome.sessionId,
    model_name: outcome.modelName,
    duration_ms: outcome.durationMs,
    api_duration_ms: outcome.apiDurationMs,
    total_cost_usd: outcome.totalCostUsd,
    input_tokens: outcome.inputTokens,
    output_tokens: outcome.outputTokens,
    summary: String(parsed.summary || "").trim() || null,
    output: parsed,
    started_at: outcome.startedAt,
    completed_at: outcome.completedAt,
  })
}

function readNote(pathRef) {
  return fs.readFileSync(path.join(workspacePath, pathRef), "utf8").slice(0, 900)
}

const context = JSON.parse(fs.readFileSync(contextPath, "utf8"))
const notes = (context.notes || []).map((note) => ({
  id: note.id,
  title: note.title,
  tags: note.tags,
  updated_at: note.updated_at,
  path: note.path,
  content: readNote(note.path),
}))
const noteIds = new Set(notes.map((note) => note.id))
const noteIndex = Object.fromEntries(
  notes.map((note) => [
    note.id,
    {
      title: note.title,
      tags: note.tags,
      updated_at: note.updated_at,
      path: note.path,
    },
  ]),
)
const baseContext = JSON.stringify(
  {
    generation_id: context.generation_id,
    workspace_path: workspacePath,
    note_count: notes.length,
    notes,
  },
  null,
  2,
)

function buildRetrievalPrompt(stageMeta) {
  return [
    "You are preparing evidence for an Insight generation workflow.",
    'Use "Grep" to search note snapshots and "Read" to quote exact material before making claims.',
    stageMeta.skill_hint || "",
    "Your goal is to identify the strongest note clusters, tensions, and blind spots before report synthesis.",
    "IMPORTANT: All output text (summary, why_relevant, rationale, blind_spots) MUST be written in Chinese (中文).",
    "Return only valid JSON with this shape:",
    "{",
    '  "summary": "short summary",',
    '  "prioritized_notes": [',
    "    {",
    '      "note_id": "note-id",',
    '      "title": "note title",',
    '      "why_relevant": "why it matters",',
    '      "evidence_snippets": [',
    '        {"quote": "exact quote or excerpt", "rationale": "why this snippet matters"}',
    "      ]",
    "    }",
    "  ],",
    '  "blind_spots": ["missing context"]',
    "}",
    "",
    baseContext,
  ]
    .filter(Boolean)
    .join("\n")
}

function buildEditorPrompt(stageMeta, retrieval) {
  const editorInput = JSON.stringify(
    {
      retrieval,
      notes: notes.map((note) => ({
        id: note.id,
        title: note.title,
        tags: note.tags,
        updated_at: note.updated_at,
        content: note.content,
      })),
      note_index: noteIndex,
    },
    null,
    2,
  )

  return [
    "You are combining candidate findings into a final Insight report set.",
    stageMeta.skill_hint || "",
    "Use the retrieval summary to synthesize trends, connections, gaps, and concrete opportunities directly in one pass.",
    "IMPORTANT: All output text (summary, title, description, report_markdown, action items, evidence rationale) MUST be written in Chinese (中文).",
    "Return only valid JSON with this shape:",
    "{",
    '  "summary": "one sentence generation summary",',
    '  "reports": [',
    "    {",
    '      "type": "trend | connection | gap | opportunity",',
    '      "status": "draft | reviewed | published",',
    '      "title": "short card title",',
    '      "description": "1-2 sentence card summary",',
    '      "confidence": 0.0,',
    '      "importance_score": 0.0,',
    '      "novelty_score": 0.0,',
    '      "report_markdown": "# 标题\\n\\n## 为什么重要\\n...\\n\\n## 证据\\n...\\n\\n## 建议的下一步\\n...",',
    '      "source_note_ids": ["note-id"],',
    '      "evidence_items": [',
    '        {"note_id": "note-id", "quote": "evidence quote", "rationale": "why it supports the claim"}',
    "      ],",
    '      "action_items": [',
    '        {"title": "action title", "detail": "what to do next", "priority": "low | medium | high"}',
    "      ]",
    "    }",
    "  ]",
    "}",
    "Requirements:",
    "- Keep 3 to 5 final reports.",
    "- Remove duplicates and merge overlapping candidates.",
    '- Each report_markdown must include sections named "为什么重要", "证据", and "建议的下一步".',
    "",
    editorInput,
  ]
    .filter(Boolean)
    .join("\n")
}

function buildReviewPrompt(stageMeta, editedBundle) {
  return [
    "You are reviewing a candidate Insight bundle before publication.",
    stageMeta.skill_hint || "",
    "Tighten the wording, remove unsupported claims, and keep only source-backed evidence.",
    "IMPORTANT: All output text MUST be written in Chinese (中文). Keep note_id values unchanged.",
    "Return only valid JSON with this shape:",
    "{",
    '  "summary": "review summary",',
    '  "reports": [',
    "    {",
    '      "type": "trend | connection | gap | opportunity",',
    '      "status": "reviewed | published",',
    '      "title": "short card title",',
    '      "description": "1-2 sentence card summary",',
    '      "confidence": 0.0,',
    '      "importance_score": 0.0,',
    '      "novelty_score": 0.0,',
    '      "review_summary": "what changed in review",',
    '      "report_markdown": "# 标题\\n\\n## 为什么重要\\n...\\n\\n## 证据\\n...\\n\\n## 建议的下一步\\n...",',
    '      "source_note_ids": ["note-id"],',
    '      "evidence_items": [',
    '        {"note_id": "note-id", "quote": "evidence quote", "rationale": "why it supports the claim"}',
    "      ],",
    '      "action_items": [',
    '        {"title": "action title", "detail": "what to do next", "priority": "low | medium | high"}',
    "      ]",
    "    }",
    "  ]",
    "}",
    "",
    JSON.stringify(
      {
        note_index: noteIndex,
        candidate_bundle: editedBundle,
      },
      null,
      2,
    ),
  ]
    .filter(Boolean)
    .join("\n")
}

function buildCardPrompt(stageMeta, reviewedBundle) {
  return [
    "You are composing shareable card summaries for Insight reports.",
    stageMeta.skill_hint || "",
    "Each card should stay faithful to the reviewed report and feel ready for external sharing.",
    "IMPORTANT: All output text (headline, summary, highlight, action_title, action_detail, footer, metrics labels) MUST be written in Chinese (中文).",
    "Return only valid JSON with this shape:",
    "{",
    '  "summary": "short card composition summary",',
    '  "cards": [',
    "    {",
    '      "report_title": "exact report title",',
    '      "share_card": {',
    '        "theme": "trend | connection | gap | opportunity | report",',
    '        "eyebrow": "short uppercase label",',
    '        "headline": "shareable headline",',
    '        "summary": "1-2 sentence summary",',
    '        "highlight": "optional short supporting line",',
    '        "evidence_quote": "optional exact quote",',
    '        "evidence_source": "optional note title",',
    '        "action_title": "optional action heading",',
    '        "action_detail": "optional action detail",',
    '        "metrics": [',
    '          {"label": "Confidence", "value": "88%"}',
    "        ],",
    '        "footer": "short footer line"',
    "      }",
    "    }",
    "  ]",
    "}",
    "Requirements:",
    "- Return one card per reviewed report.",
    "- Keep the card faithful to the reviewed report and avoid adding claims that are not already supported.",
    "",
    JSON.stringify(
      {
        note_index: noteIndex,
        reviewed_bundle: reviewedBundle,
      },
      null,
      2,
    ),
  ]
    .filter(Boolean)
    .join("\n")
}

function buildInsightPrompt(stageMeta) {
  const notesPayload = JSON.stringify(
    {
      generation_id: context.generation_id,
      workspace_path: workspacePath,
      note_count: notes.length,
      notes: notes.map((note) => ({
        id: note.id,
        title: note.title,
        tags: note.tags,
        updated_at: note.updated_at,
        content: note.content,
      })),
    },
    null,
    2,
  )

  return [
    "# 角色",
    "",
    "你是一位兼具人文素养和分析能力的个人洞察顾问。",
    "你不是在做数据分析——你是在读一个人的思想日记，试图理解他/她是谁、在意什么、擅长什么、可能忽略了什么。",
    "",
    "# 任务",
    "",
    "仔细阅读下面提供的全部笔记，然后生成一篇深度洞察报告：",
    "",
    "1. **理解这个人**：从笔记中感受作者的思维方式、关注领域、内在矛盾和成长轨迹",
    "2. **发现深层联系**：找到笔记之间不明显但有意义的联系——不是表面的主题分类，而是思维模式、价值取向、反复出现的关切",
    "3. **聚焦一个最有价值的发现**：选择你认为对这个人最重要、最有洞见的一个切入点，写成一篇完整的洞察报告",
    "4. **为报告生成分享卡片**：用于可视化展示",
    "",
    "# 人文要求（非常重要）",
    "",
    "- 找笔记间的深层联系，不是表面主题分类。比如：两篇看似无关的笔记可能反映了同一种焦虑或同一个未被说出的愿望",
    "- 洞察这个人真正擅长什么、在意什么、可能忽略了什么",
    "- 行动建议要像一个了解你的朋友给的建议——具体、温暖、可执行，而不是空洞的「建议多思考」",
    "- 语言要温暖有洞见，不空洞不说教。写出让人读了会说「对，就是这样」的文字",
    "- 如果发现矛盾或张力，不要回避——矛盾往往是最有价值的洞察入口",
    "- 宁可把一个发现写透，也不要蜻蜓点水地罗列多个",
    "",
    "# 输出格式",
    "",
    "返回且仅返回一个合法的 JSON 对象，形状如下（reports 数组只包含 1 个元素）：",
    "",
    "{",
    '  "summary": "一句话概括这次洞察的核心发现",',
    '  "reports": [',
    "    {",
    '      "type": "trend | connection | gap | opportunity",',
    '      "status": "published",',
    '      "title": "简短有力的标题（点出变化/矛盾/机会，避免空泛）",',
    '      "description": "3-5 句独立可读的摘要，让没看过笔记的人也能理解这个发现的价值",',
    '      "report_markdown": "完整的深度报告，格式见下方「报告写作要求」",',
    '      "source_note_ids": ["note-id"],',
    '      "evidence_items": [',
    '        {"note_id": "note-id", "quote": "笔记原文逐字引用（尽量完整，保留上下文）", "rationale": "详细分析为什么这段话支撑该结论，不要只写一句话"}',
    "      ],",
    '      "action_items": [',
    '        {"title": "行动标题", "detail": "具体做什么、为了什么、怎么开始", "priority": "low | medium | high"}',
    "      ],",
    '      "share_card": {',
    '        "theme": "trend | connection | gap | opportunity",',
    '        "eyebrow": "简短分类标签",',
    '        "headline": "适合分享的标题",',
    '        "summary": "3-5 句分享摘要，信息量要足，让人看完就理解核心发现",',
    '        "evidence_quote": "最有力的一段原文引用",',
    '        "evidence_source": "笔记标题或日期",',
    '        "action_title": "最重要的一个行动建议",',
    '        "action_detail": "具体怎么做",',
    '        "footer": "简短底部文字"',
    "      }",
    "    }",
    "  ]",
    "}",
    "",
    "# 报告写作要求（report_markdown）",
    "",
    "报告是用户在 app 里阅读的主体内容，必须有深度、有篇幅、有洞见。不是摘要，是一篇完整的分析文章。",
    "",
    "## 结构",
    "",
    "```markdown",
    "# {标题}",
    "",
    "## 为什么重要",
    "",
    "（3-5 段，800-1200 字）",
    "- 先用一个具体的场景或画面开头，让读者立刻产生共鸣",
    "- 然后展开分析：这个模式/联系/矛盾是什么，它如何影响你的决策和行为",
    "- 把不同笔记之间的联系编织成一个连贯的叙事，不是罗列",
    "- 点出这个发现的深层含义——它揭示了你什么样的价值观、恐惧或渴望",
    "- 如果不正视这个发现，可能会付出什么代价",
    "",
    "## 证据",
    "",
    "（逐条展开，每条 100-200 字分析）",
    "- 每条证据以笔记原文引用开头（用 > 引用块格式）",
    "- 紧跟 2-3 句深入分析：这段话表面在说什么，深层在说什么，它和核心发现的关系是什么",
    "- 如果多条证据之间有递进或对比关系，要明确指出",
    "- 至少引用 3 篇不同笔记的内容",
    "",
    "## 建议的下一步",
    "",
    "（2-3 个具体建议，每个 100-150 字）",
    "- 每个建议要有：做什么、为什么这样做、具体怎么开始",
    "- 语气像一个了解你的朋友在说话，不是教科书",
    "- 建议之间要有层次：从最容易开始的到需要更多勇气的",
    "```",
    "",
    "## 写作风格",
    "",
    "- 像写给一个聪明的朋友看的私人信件，不是学术论文",
    "- 用具体的细节和画面，不用抽象的概括",
    "- 敢于指出矛盾和不舒服的真相，但语气温暖",
    "- 整篇报告读完应该让人有「被看见了」的感觉",
    "- 总字数 1500-2500 字",
    "",
    "# 约束",
    "",
    "- 只输出 1 篇报告，把这一个发现写深写透",
    "- report_markdown 是报告主体，必须 1500-2500 字，按上面的结构和风格要求写",
    "- evidence_items 至少 3 条，来自不同笔记，每条的 quote 要完整（保留上下文），rationale 要详细分析",
    "- action_items 给 2-3 个具体建议，每个都要说清楚做什么、为什么、怎么开始",
    "- share_card 是报告的浓缩版封面，summary 要 3-5 句话，让人看完就理解核心发现",
    "- source_note_ids 和 evidence_items 的 note_id 必须来自下面提供的真实笔记 ID",
    "- report_markdown 必须包含三段：「## 为什么重要」「## 证据」「## 建议的下一步」",
    "- 所有面向用户的文本必须使用中文",
    "- 只返回 JSON，不要包含任何其他文字",
    "",
    "# 笔记数据",
    "",
    notesPayload,
  ].join("\n")
}

function normalizeCardBundle(rawBundle) {
  if (!rawBundle || !Array.isArray(rawBundle.cards)) {
    return []
  }
  return rawBundle.cards
    .map((item, index) => {
      if (!item || typeof item !== "object" || Array.isArray(item)) {
        return null
      }
      const shareCard = item.share_card
      if (!shareCard || typeof shareCard !== "object" || Array.isArray(shareCard)) {
        return null
      }
      return {
        reportTitle: String(item.report_title || "").trim(),
        reportIndex: Number.isFinite(Number(item.report_index)) ? Number(item.report_index) : index,
        shareCard,
      }
    })
    .filter(Boolean)
}

function mergeShareCardsIntoReports(reports, cards) {
  if (!Array.isArray(cards) || cards.length === 0) {
    return reports
  }

  return reports.map((report, index) => {
    const matchByTitle = cards.find((card) => card.reportTitle && card.reportTitle === report.title)
    const matchByIndex = cards.find((card) => card.reportIndex === index)
    const match = matchByTitle || matchByIndex
    if (!match) {
      return report
    }
    return {
      ...report,
      share_card: match.shareCard,
    }
  })
}

const configuredStages = Array.isArray(taskConfig.stages) ? taskConfig.stages : []
if (configuredStages.length === 0) {
  throw new Error("Insight workflow is missing stages")
}

const agentRuns = []
let retrievalBundle = null
let editedBundle = null
let reviewedBundle = null

for (const stageMeta of configuredStages) {
  if (!stageMeta?.kind || !stageMeta?.agent || !stageMeta?.key) {
    throw new Error("Insight workflow stage is missing key metadata")
  }

  if (stageMeta.kind === "insight") {
    const result = await runStage(stageMeta, buildInsightPrompt(stageMeta))
    const insightBundle = parseJsonOrDie(result.raw, "Insight output")
    pushRun(agentRuns, stageMeta, result, insightBundle)
    // Single-agent mode: this bundle is the final output, assign to reviewedBundle
    reviewedBundle = insightBundle
    continue
  }

  if (stageMeta.kind === "retrieval") {
    const result = await runStage(stageMeta, buildRetrievalPrompt(stageMeta))
    retrievalBundle = parseJsonOrDie(result.raw, "Retrieval output")
    pushRun(agentRuns, stageMeta, result, retrievalBundle)
    continue
  }

  if (stageMeta.kind === "editor") {
    const result = await runStage(stageMeta, buildEditorPrompt(stageMeta, retrievalBundle))
    editedBundle = parseJsonOrDie(result.raw, "Editor output")
    pushRun(agentRuns, stageMeta, result, editedBundle)
    continue
  }

  if (stageMeta.kind === "review") {
    const result = await runStage(stageMeta, buildReviewPrompt(stageMeta, editedBundle))
    reviewedBundle = parseJsonOrDie(result.raw, "Review output")
    pushRun(agentRuns, stageMeta, result, reviewedBundle)
    continue
  }

  if (stageMeta.kind === "card") {
    if (!reviewedBundle) {
      continue
    }
    try {
      const result = await runStage(stageMeta, buildCardPrompt(stageMeta, reviewedBundle))
      const cardBundle = parseJsonOrDie(result.raw, "Card output")
      pushRun(agentRuns, stageMeta, result, cardBundle)
      reviewedBundle = {
        ...reviewedBundle,
        reports: mergeShareCardsIntoReports(
          Array.isArray(reviewedBundle.reports) ? reviewedBundle.reports : [],
          normalizeCardBundle(cardBundle),
        ),
      }
    } catch (error) {
      if (stageMeta.optional) {
        emitProgress({
          type: "stage_skipped",
          stage: stageMeta.key,
          agent: stageMeta.agent,
          reason: String(error?.message || error),
        })
        continue
      }
      throw error
    }
    continue
  }

  throw new Error(`Unsupported Insight stage kind: ${stageMeta.kind}`)
}

if (!reviewedBundle || !Array.isArray(reviewedBundle.reports)) {
  throw new Error("Insight workflow did not produce a reviewed report bundle")
}

const reports = reviewedBundle.reports
  .map((report) => ({
    type: String(report.type || "report").slice(0, 32),
    status: String(report.status || "published").slice(0, 32),
    title: String(report.title || "Insight").slice(0, 255),
    description: String(report.description || "").trim(),
    confidence: Number.isFinite(Number(report.confidence)) ? Number(report.confidence) : 0.5,
    importance_score: Number.isFinite(Number(report.importance_score))
      ? Number(report.importance_score)
      : 0.5,
    novelty_score: Number.isFinite(Number(report.novelty_score)) ? Number(report.novelty_score) : 0.5,
    review_summary: String(report.review_summary || "").trim(),
    report_markdown: String(report.report_markdown || "").trim(),
    source_note_ids: Array.isArray(report.source_note_ids)
      ? report.source_note_ids.filter((noteId) => typeof noteId === "string" && noteIds.has(noteId))
      : [],
    evidence_items: Array.isArray(report.evidence_items)
      ? report.evidence_items
          .map((item) => ({
            note_id: item.note_id,
            quote: String(item.quote || "").trim(),
            rationale: String(item.rationale || "").trim(),
          }))
          .filter((item) => noteIds.has(item.note_id) && item.quote && item.rationale)
      : [],
    action_items: Array.isArray(report.action_items)
      ? report.action_items
          .map((item) => ({
            title: String(item.title || "").trim(),
            detail: String(item.detail || "").trim(),
            priority: String(item.priority || "medium").trim().toLowerCase(),
          }))
          .filter((item) => item.title && item.detail)
      : [],
    share_card:
      report.share_card && typeof report.share_card === "object" && !Array.isArray(report.share_card)
        ? report.share_card
        : undefined,
  }))
  .filter((report) => report.description && report.report_markdown && report.evidence_items.length > 0)

const sessionId =
  agentRuns
    .map((run) => run.session_id)
    .filter((value) => typeof value === "string" && value.trim().length > 0)
    .at(-1) || null

console.log(
  JSON.stringify({
    workflow_version: String(taskConfig.workflow_version || "cloud-sdk-v1"),
    session_id: sessionId,
    summary:
      String(reviewedBundle.summary || "").trim() || `Generated ${reports.length} insight reports.`,
    agent_runs: agentRuns,
    reports,
  }),
)
