#!/usr/bin/env node
/**
 * AI SDK Insight Agent
 * 
 * 使用 Vercel AI SDK 替代 Claude Agent SDK 的 insight 生成器
 * 支持多 provider：OpenAI、Anthropic、OpenRouter
 * 
 * Usage: node ai_sdk_insight_agent.mjs <workspace_path>
 */

import fs from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"

// AI SDK imports
import { generateText, streamText } from "ai"

// Provider imports (根据配置动态加载)
const PROVIDER_IMPORTS = {
  openai: () => import("@ai-sdk/openai"),
  anthropic: () => import("@ai-sdk/anthropic"),
  google: () => import("@ai-sdk/google"),
}

const __filename = fileURLToPath(import.meta.url)
const __dirname = path.dirname(__filename)

// Parse arguments
const workspaceArg = process.argv[2]
if (!workspaceArg) {
  console.error("Usage: node ai_sdk_insight_agent.mjs <workspace_path>")
  process.exit(1)
}

const workspacePath = path.resolve(workspaceArg)
const contextPath = path.join(workspacePath, "context.json")
const taskConfigPath = path.join(workspacePath, "task_config.json")

// Load environment variables from .env file
function loadEnv() {
  const envPath = path.join(__dirname, "..", "backend", ".env")
  if (fs.existsSync(envPath)) {
    const content = fs.readFileSync(envPath, "utf8")
    for (const line of content.split("\n")) {
      const [key, ...valueParts] = line.split("=")
      if (key && valueParts.length > 0) {
        const value = valueParts.join("=").trim()
        if (value && !process.env[key]) {
          process.env[key] = value.replace(/^["']|["']$/g, "")
        }
      }
    }
  }
}
loadEnv()

// Configuration
const config = {
  provider: process.env.AI_SDK_PROVIDER || "openai", // openai | anthropic | openrouter
  model: process.env.AI_SDK_MODEL || "gpt-4o",
  apiKey: process.env.AI_SDK_API_KEY || process.env.OPENAI_API_KEY || process.env.OPENROUTER_API_KEY,
  baseUrl: process.env.AI_SDK_BASE_URL || process.env.OPENROUTER_BASE_URL,
  maxTokens: parseInt(process.env.AI_SDK_MAX_TOKENS || "8000", 10),
  temperature: parseFloat(process.env.AI_SDK_TEMPERATURE || "0.7"),
  streaming: process.env.AI_SDK_STREAMING !== "false",
}

// Progress emitter
function emitProgress(payload) {
  process.stdout.write(
    `PROGRESS: ${JSON.stringify({ timestamp: new Date().toISOString(), ...payload })}\n`
  )
}

// JSON utilities
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

  const candidates = [
    rawStr,
    stripJson(rawStr),
    extractJsonCandidate(rawStr),
  ]

  const codeBlockMatch = rawStr.match(/```(?:json)?\s*\n?([\s\S]*?)```/)
  if (codeBlockMatch) {
    candidates.push(codeBlockMatch[1].trim())
  }

  for (const candidate of candidates) {
    if (!candidate) continue
    try {
      return JSON.parse(candidate)
    } catch {
      try {
        const cleaned = candidate
          .replace(/,\s*([}\]])/g, "$1")
          .replace(/[\x00-\x08\x0b\x0c\x0e-\x1f]/g, "")
        return JSON.parse(cleaned)
      } catch {
        // next candidate
      }
    }
  }

  throw new Error(`${label} was not valid JSON (length=${rawStr.length}, starts=${rawStr.substring(0, 80)})`)
}

// Load context
if (!fs.existsSync(contextPath)) {
  console.error(`Missing context file at ${contextPath}`)
  process.exit(1)
}

const context = JSON.parse(fs.readFileSync(contextPath, "utf8"))

// Read notes
function readNote(pathRef) {
  return fs.readFileSync(path.join(workspacePath, pathRef), "utf8").slice(0, 4000)
}

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
    { title: note.title, tags: note.tags, updated_at: note.updated_at, path: note.path },
  ])
)

// Build prompt
function buildInsightPrompt() {
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
    2
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
    '```markdown',
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
    '```',
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

// Get model provider
async function getModel() {
  const provider = config.provider.toLowerCase()
  
  // OpenRouter uses OpenAI-compatible API
  if (provider === "openrouter") {
    const { createOpenAI } = await import("@ai-sdk/openai")
    const openai = createOpenAI({
      apiKey: config.apiKey,
      baseURL: config.baseUrl || "https://openrouter.ai/api/v1",
    })
    return openai(config.model)
  }
  
  // Native providers
  if (provider === "anthropic") {
    const { anthropic } = await import("@ai-sdk/anthropic")
    return anthropic(config.model)
  }
  
  if (provider === "google") {
    const { google } = await import("@ai-sdk/google")
    return google(config.model)
  }
  
  // Default: OpenAI
  const { openai } = await import("@ai-sdk/openai")
  return openai(config.model)
}

// Run insight generation
async function runInsightGeneration() {
  emitProgress({ type: "agent_start", agent: "insight-analyst", stage: "insight" })
  emitProgress({ type: "stage_start", stage: "insight", agent: "insight-analyst" })

  const startedAt = new Date().toISOString()
  
  try {
    const model = await getModel()
    const prompt = buildInsightPrompt()
    
    let rawResult = ""
    let usage = { promptTokens: 0, completionTokens: 0, totalTokens: 0 }

    if (config.streaming) {
      emitProgress({ type: "progress", message: "Streaming response..." })
      
      const { textStream, usage: usagePromise } = streamText({
        model,
        prompt,
        maxTokens: config.maxTokens,
        temperature: config.temperature,
      })

      // Collect text chunks
      for await (const textPart of textStream) {
        rawResult += textPart
        emitProgress({
          type: "text",
          stage: "insight",
          agent: "insight-analyst",
          content: textPart,
          text_chunk_count: rawResult.length,
        })
      }

      // Get usage stats
      const finalUsage = await usagePromise
      usage = {
        promptTokens: finalUsage?.promptTokens || 0,
        completionTokens: finalUsage?.completionTokens || 0,
        totalTokens: finalUsage?.totalTokens || 0,
      }
    } else {
      emitProgress({ type: "progress", message: "Generating response..." })
      
      const result = await generateText({
        model,
        prompt,
        maxTokens: config.maxTokens,
        temperature: config.temperature,
      })

      rawResult = result.text
      usage = {
        promptTokens: result.usage?.promptTokens || 0,
        completionTokens: result.usage?.completionTokens || 0,
        totalTokens: result.usage?.totalTokens || 0,
      }
    }

    const completedAt = new Date().toISOString()
    
    // Parse result
    const insightBundle = parseJsonOrDie(rawResult, "Insight output")

    emitProgress({
      type: "stage_end",
      stage: "insight",
      agent: "insight-analyst",
      status: "completed",
      input_tokens: usage.promptTokens,
      output_tokens: usage.completionTokens,
    })

    return {
      startedAt,
      completedAt,
      modelName: config.model,
      inputTokens: usage.promptTokens,
      outputTokens: usage.completionTokens,
      bundle: insightBundle,
    }
  } catch (error) {
    emitProgress({
      type: "stage_end",
      stage: "insight",
      agent: "insight-analyst",
      status: "failed",
      error: String(error?.message || error),
    })
    throw error
  }
}

// Main execution
async function main() {
  // Validate config
  if (!config.apiKey) {
    console.error("Error: AI_SDK_API_KEY or provider-specific API key is required")
    process.exit(1)
  }

  emitProgress({ type: "starting", message: `Using provider: ${config.provider}, model: ${config.model}` })

  try {
    const result = await runInsightGeneration()
    const { bundle, ...metadata } = result

    // Normalize reports
    const reports = (bundle.reports || [])
      .map((report) => ({
        type: String(report.type || "report").slice(0, 32),
        status: String(report.status || "published").slice(0, 32),
        title: String(report.title || "Insight").slice(0, 255),
        description: String(report.description || "").trim(),
        confidence: Number.isFinite(Number(report.confidence)) ? Number(report.confidence) : 0.5,
        importance_score: Number.isFinite(Number(report.importance_score)) ? Number(report.importance_score) : 0.5,
        novelty_score: Number.isFinite(Number(report.novelty_score)) ? Number(report.novelty_score) : 0.5,
        report_markdown: String(report.report_markdown || "").trim(),
        source_note_ids: Array.isArray(report.source_note_ids)
          ? report.source_note_ids.filter((id) => typeof id === "string" && noteIds.has(id))
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

    // Build final output
    const output = {
      workflow_version: "ai-sdk-v1",
      session_id: null,
      summary: String(bundle.summary || "").trim() || `Generated ${reports.length} insight reports.`,
      agent_runs: [
        {
          agent_name: "insight-analyst",
          stage: "insight",
          status: "completed",
          session_id: null,
          model_name: metadata.modelName,
          duration_ms: null,
          api_duration_ms: null,
          total_cost_usd: null,
          input_tokens: metadata.inputTokens,
          output_tokens: metadata.outputTokens,
          summary: bundle.summary,
          output: bundle,
          started_at: metadata.startedAt,
          completed_at: metadata.completedAt,
        },
      ],
      reports,
    }

    console.log(JSON.stringify(output))
  } catch (error) {
    console.error("Error:", error.message)
    process.exit(1)
  }
}

main()
