#!/usr/bin/env node
/**
 * Atélier Insight Generator
 * 
 * 使用 Vercel AI SDK 的轻量级 Insight 生成器
 * 支持三种模式：quick / standard / deep
 * 
 * Usage: node atelier-insight.mjs <workspace_path> [--mode=quick|standard|deep]
 */

import fs from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"
import { generateText, streamText } from "ai"

const __filename = fileURLToPath(import.meta.url)
const __dirname = path.dirname(__filename)

// Parse arguments
const workspaceArg = process.argv[2]
const modeArg = process.argv.find(arg => arg.startsWith("--mode="))?.split("=")[1] || "standard"

if (!workspaceArg) {
  console.error("Usage: node atelier-insight.mjs <workspace_path> [--mode=quick|standard|deep]")
  process.exit(1)
}

const workspacePath = path.resolve(workspaceArg)
const contextPath = path.join(workspacePath, "context.json")

// Load environment
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
  provider: process.env.AI_SDK_PROVIDER || "openai",
  model: process.env.AI_SDK_MODEL || "gpt-4o-mini",  // 默认用轻量级模型
  apiKey: process.env.AI_SDK_API_KEY || process.env.OPENAI_API_KEY || process.env.OPENROUTER_API_KEY,
  baseUrl: process.env.AI_SDK_BASE_URL || process.env.OPENROUTER_BASE_URL,
  mode: modeArg, // quick | standard | deep
  streaming: process.env.AI_SDK_STREAMING !== "false",
}

// Mode configurations
const MODE_CONFIG = {
  quick: {
    maxTokens: 1500,
    temperature: 0.6,
    minLength: 200,
    maxLength: 400,
    description: "快速回顾 - 像朋友随口分享一个观察",
  },
  standard: {
    maxTokens: 2500,
    temperature: 0.7,
    minLength: 600,
    maxLength: 1000,
    description: "标准洞察 - 平衡深度和易读性",
  },
  deep: {
    maxTokens: 4000,
    temperature: 0.7,
    minLength: 1500,
    maxLength: 2000,
    description: "深度分析 - 周期性全面回顾",
  },
}

// Progress emitter
function emitProgress(payload) {
  process.stdout.write(
    `PROGRESS: ${JSON.stringify({ timestamp: new Date().toISOString(), ...payload })}\n`
  )
}

// JSON utilities
function extractJson(text) {
  // Try to extract JSON from markdown code blocks
  const codeBlockMatch = text.match(/```(?:json)?\s*\n?([\s\S]*?)```/)
  if (codeBlockMatch) {
    return codeBlockMatch[1].trim()
  }
  
  // Try to find JSON object - match outermost braces
  let depth = 0
  let start = -1
  for (let i = 0; i < text.length; i++) {
    if (text[i] === '{') {
      if (depth === 0) start = i
      depth++
    } else if (text[i] === '}') {
      depth--
      if (depth === 0 && start !== -1) {
        return text.slice(start, i + 1)
      }
    }
  }
  
  return text.trim()
}

function cleanJson(text) {
  // Remove markdown formatting
  let cleaned = text
    .replace(/^```json\s*/i, '')
    .replace(/^```\s*/i, '')
    .replace(/\s*```$/i, '')
    .trim()
  
  // Remove trailing commas before } or ]
  cleaned = cleaned.replace(/,\s*([}\]])/g, '$1')
  
  // Remove control characters except newlines and tabs
  cleaned = cleaned.replace(/[\x00-\x08\x0b\x0c\x0e-\x1f]/g, '')
  
  // Try to fix common issues with quotes in content
  // If there's unescaped quote inside a string value, try to handle it
  return cleaned
}

function parseJsonOrDie(raw, label) {
  const rawStr = String(raw || "").trim()
  if (!rawStr) {
    throw new Error(`${label} returned empty output`)
  }

  const candidates = [
    rawStr,
    extractJson(rawStr),
    cleanJson(rawStr),
    cleanJson(extractJson(rawStr)),
  ]

  for (const candidate of candidates) {
    if (!candidate) continue
    try {
      return JSON.parse(candidate)
    } catch (err) {
      // Try more aggressive cleaning
      try {
        const aggressive = candidate
          .replace(/\n/g, '\\n')
          .replace(/\r/g, '\\r')
          .replace(/\t/g, '\\t')
        return JSON.parse(aggressive)
      } catch {
        // next candidate
      }
    }
  }

  // If all fails, log the raw output for debugging and throw
  console.error(`Failed to parse JSON from ${label}. Raw output (first 500 chars):`)
  console.error(rawStr.substring(0, 500))
  throw new Error(`${label} was not valid JSON (length=${rawStr.length})`)
}

// Load context
if (!fs.existsSync(contextPath)) {
  console.error(`Missing context file at ${contextPath}`)
  process.exit(1)
}

const context = JSON.parse(fs.readFileSync(contextPath, "utf8"))

function readNote(pathRef) {
  try {
    return fs.readFileSync(path.join(workspacePath, pathRef), "utf8").slice(0, 5000)
  } catch {
    return ""
  }
}

const notes = (context.notes || []).map((note) => ({
  id: note.id,
  title: note.title,
  tags: note.tags || [],
  updated_at: note.updated_at,
  content: readNote(note.path),
}))

const noteIds = new Set(notes.map((note) => note.id))

// Get model provider
async function getModel() {
  const provider = config.provider.toLowerCase()
  
  if (provider === "openrouter") {
    const { createOpenAI } = await import("@ai-sdk/openai")
    const openai = createOpenAI({
      apiKey: config.apiKey,
      baseURL: config.baseUrl || "https://openrouter.ai/api/v1",
      headers: {
        "HTTP-Referer": "https://atelier.app",
        "X-Title": "Atelier Insight",
        "X-User-ID": "atelier-user",
      },
    })
    // OpenRouter uses OpenAI-compatible format but model names are like "anthropic/claude-3.5-haiku"
    return openai(config.model)
  }
  
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

// ==================== 提示词系统 ====================

const SYSTEM_PROMPT = `你是一位温暖、细腻的朋友，正在帮朋友翻阅 TA 的笔记。

你的任务不是做冷冰冰的数据分析，而是像一个懂 TA 的朋友，帮 TA 发现：
- 最近 TA 在想什么？
- 有什么反复出现的念头或模式？
- 有什么 TA 可能忽略了的信号？

风格要求：
- 像朋友聊天一样自然、温暖
- 不用专业术语，不说教
- 敢于指出矛盾，但语气温柔
- 让 TA 读完有「被看见了」的感觉

所有输出必须是中文。`

function buildQuickPrompt(notes) {
  const notesText = notes.map(n => {
    const preview = n.content.slice(0, 300).replace(/\n/g, " ")
    return `【${n.title}】${n.tags.length ? `(${n.tags.join(", ")})` : ""}\n${preview}${n.content.length > 300 ? "..." : ""}`
  }).join("\n\n")

  return `快速浏览了朋友的 ${notes.length} 条笔记，想随口分享一个观察：

${notesText}

---

请用 JSON 格式返回：
{
  "title": "一个简短有力的标题，点出核心发现（15字以内）",
  "summary": "一句话总结这个发现",
  "content": "像朋友聊天一样的正文，200-400字，分享一个观察，可以说「我注意到...」「你有没有发现...」",
  "type": "pattern | connection | gap | trend",
  "confidence": 0.7,
  "evidence": [
    {"note_id": "xxx", "quote": "原文引用", "insight": "这点说明了什么"}
  ],
  "suggestion": "一个具体的、可执行的小建议"
}`
}

function buildStandardPrompt(notes) {
  const notesText = notes.map(n => {
    const preview = n.content.slice(0, 800).replace(/\n/g, " ")
    return `【${n.title}】标签：${n.tags.join(", ") || "无"}\n更新时间：${n.updated_at}\n\n${preview}${n.content.length > 800 ? "..." : ""}`
  }).join("\n\n---\n\n")

  return `帮朋友整理笔记，想深入聊聊 TA 最近的思考：

共有 ${notes.length} 条笔记：

${notesText}

---

请像写一封简短的信一样，用 JSON 格式返回：
{
  "title": "点出核心发现的标题（20字以内）",
  "summary": "3-5句话的摘要，让没看笔记的人也能理解",
  "content": "正文内容，600-1000字，结构如下：\n\n【为什么重要】\n- 用具体场景开头，让读者产生共鸣\n- 分析这个发现意味着什么\n- 指出可能的代价或机会\n\n【证据】\n- 引用2-3条笔记原文，每段后面跟上你的解读\n- 说明这些笔记之间的联系\n\n【下一步】\n- 给1-2个具体、可执行的建议\n- 语气像朋友给建议",
  "type": "pattern | connection | gap | trend",
  "confidence": 0.8,
  "importance_score": 0.75,
  "evidence": [
    {"note_id": "xxx", "quote": "原文引用", "rationale": "这段说明了什么，为什么重要"}
  ],
  "action_items": [
    {"title": "行动标题", "detail": "具体做什么", "priority": "medium"}
  ]
}`
}

function buildDeepPrompt(notes) {
  const notesText = notes.map(n => {
    return `【${n.title}】标签：${n.tags.join(", ") || "无"}\n更新时间：${n.updated_at}\n\n${n.content}`
  }).join("\n\n=== 笔记分隔 ===\n\n")

  return `你正在帮一位朋友做周期性的深度回顾。

这位朋友记录了 ${notes.length} 条笔记：

${notesText}

---

请写一篇深度分析，像写给朋友的私人信件。用 JSON 格式返回：
{
  "title": "简短有力的标题，点出变化/矛盾/机会",
  "summary": "3-5句独立可读的摘要",
  "content": "完整的深度报告，1500-2000字，结构如下：\n\n# {标题}\n\n## 为什么重要\n（3-4段，800-1200字）\n- 用具体场景开头，让读者产生共鸣\n- 展开分析：这个模式/联系/矛盾是什么，如何影响决策和行为\n- 把不同笔记编织成连贯的叙事\n- 点出深层含义：揭示了什么价值观、恐惧或渴望\n- 如果不正视，可能会付出什么代价\n\n## 证据\n（逐条展开，每条100-200字分析）\n- 每条证据以原文引用开头\n- 紧跟深入分析：表面在说什么，深层在说什么\n- 至少引用3篇不同笔记\n\n## 建议的下一步\n（2-3个具体建议，每个100-150字）\n- 每个建议要有：做什么、为什么、怎么开始\n- 语气像了解你的朋友在说话\n- 建议之间有层次：从易到难",
  "type": "pattern | connection | gap | trend",
  "confidence": 0.85,
  "importance_score": 0.8,
  "novelty_score": 0.75,
  "evidence": [
    {"note_id": "xxx", "quote": "完整原文引用", "rationale": "详细分析为什么这段话支撑结论"}
  ],
  "action_items": [
    {"title": "行动标题", "detail": "具体做什么、为什么、怎么开始", "priority": "high|medium|low"}
  ]
}`
}

function buildPrompt() {
  const mode = config.mode
  if (mode === "quick") return buildQuickPrompt(notes)
  if (mode === "deep") return buildDeepPrompt(notes)
  return buildStandardPrompt(notes)
}

// ==================== 主流程 ====================

async function generateInsight() {
  const modeConfig = MODE_CONFIG[config.mode]
  emitProgress({ type: "agent_start", agent: "atelier-insight", stage: config.mode })
  emitProgress({ type: "stage_start", stage: config.mode, agent: "atelier-insight" })
  emitProgress({ type: "progress", message: `${modeConfig.description}...` })

  const startedAt = new Date().toISOString()
  
  try {
    const model = await getModel()
    const userPrompt = buildPrompt()
    
    let rawResult = ""
    let usage = { promptTokens: 0, completionTokens: 0 }

    if (config.streaming) {
      emitProgress({ type: "progress", message: "正在生成..." })
      
      const { textStream, usage: usagePromise } = streamText({
        model,
        system: SYSTEM_PROMPT,
        prompt: userPrompt,
        maxTokens: modeConfig.maxTokens,
        temperature: modeConfig.temperature,
      })

      let chunkCount = 0
      for await (const textPart of textStream) {
        rawResult += textPart
        chunkCount++
        if (chunkCount % 10 === 0) {
          emitProgress({
            type: "text",
            stage: config.mode,
            content: `...`,
            progress: rawResult.length,
          })
        }
      }

      const finalUsage = await usagePromise
      usage = {
        promptTokens: finalUsage?.promptTokens || 0,
        completionTokens: finalUsage?.completionTokens || 0,
      }
    } else {
      emitProgress({ type: "progress", message: "生成中..." })
      
      const result = await generateText({
        model,
        system: SYSTEM_PROMPT,
        prompt: userPrompt,
        maxTokens: modeConfig.maxTokens,
        temperature: modeConfig.temperature,
      })

      rawResult = result.text
      usage = {
        promptTokens: result.usage?.promptTokens || 0,
        completionTokens: result.usage?.completionTokens || 0,
      }
    }

    const completedAt = new Date().toISOString()
    
    // Parse result
    const parsed = parseJsonOrDie(rawResult, "Insight output")
    
    // Validate length
    const contentLength = parsed.content?.length || 0
    if (contentLength < modeConfig.minLength) {
      console.warn(`Warning: Generated content (${contentLength} chars) is shorter than expected (${modeConfig.minLength})`)
    }

    emitProgress({
      type: "stage_end",
      stage: config.mode,
      agent: "atelier-insight",
      status: "completed",
      input_tokens: usage.promptTokens,
      output_tokens: usage.completionTokens,
    })

    return {
      startedAt,
      completedAt,
      modelName: config.model,
      mode: config.mode,
      inputTokens: usage.promptTokens,
      outputTokens: usage.completionTokens,
      bundle: parsed,
    }
  } catch (error) {
    emitProgress({
      type: "stage_end",
      stage: config.mode,
      agent: "atelier-insight",
      status: "failed",
      error: String(error?.message || error),
    })
    throw error
  }
}

function normalizeReport(raw, mode) {
  const modeConfig = MODE_CONFIG[mode]
  
  // Build report_markdown from content
  const content = raw.content || raw.summary || ""
  let reportMarkdown = content
  
  // If content doesn't have markdown headers, add them
  if (!content.startsWith("# ") && content.length > 500) {
    reportMarkdown = `# ${raw.title}\n\n${content}`
  }

  return {
    type: String(raw.type || "report").slice(0, 32),
    status: "published",
    title: String(raw.title || "Insight").slice(0, 255),
    description: String(raw.summary || raw.description || "").trim(),
    confidence: Number.isFinite(Number(raw.confidence)) ? Number(raw.confidence) : 0.75,
    importance_score: Number.isFinite(Number(raw.importance_score)) ? Number(raw.importance_score) : 0.7,
    novelty_score: Number.isFinite(Number(raw.novelty_score)) ? Number(raw.novelty_score) : 0.6,
    report_markdown: reportMarkdown,
    source_note_ids: (raw.evidence || raw.evidence_items || [])
      .map(e => e.note_id)
      .filter(id => noteIds.has(id)),
    evidence_items: (raw.evidence || raw.evidence_items || [])
      .map(item => ({
        note_id: item.note_id,
        quote: String(item.quote || item.insight || "").trim(),
        rationale: String(item.rationale || item.insight || "").trim(),
      }))
      .filter(item => noteIds.has(item.note_id) && item.quote),
    action_items: (raw.action_items || [])
      .map(item => ({
        title: String(item.title || raw.suggestion || "").slice(0, 255),
        detail: String(item.detail || item.title || raw.suggestion || "").trim(),
        priority: String(item.priority || "medium").toLowerCase(),
      }))
      .filter(item => item.title && item.detail),
    share_card: raw.share_card || {
      theme: raw.type || "report",
      headline: raw.title,
      summary: raw.summary,
    },
  }
}

// Main execution
async function main() {
  if (!config.apiKey) {
    console.error(JSON.stringify({ 
      error: "AI_SDK_API_KEY or provider-specific API key is required",
      hint: "Set AI_SDK_API_KEY or OPENROUTER_API_KEY environment variable" 
    }, null, 2))
    process.exit(1)
  }

  // Validate mode
  if (!MODE_CONFIG[config.mode]) {
    console.error(JSON.stringify({
      error: `Invalid mode: ${config.mode}`,
      valid_modes: Object.keys(MODE_CONFIG)
    }, null, 2))
    process.exit(1)
  }

  emitProgress({ 
    type: "starting", 
    message: `Atélier Insight (${config.mode}) - ${MODE_CONFIG[config.mode].description}`,
    provider: config.provider,
    model: config.model,
  })

  try {
    const result = await generateInsight()
    const normalizedReport = normalizeReport(result.bundle, config.mode)

    // Ensure at least one action item from suggestion
    if (normalizedReport.action_items.length === 0 && result.bundle.suggestion) {
      normalizedReport.action_items.push({
        title: "试试看",
        detail: result.bundle.suggestion,
        priority: "medium",
      })
    }

    const output = {
      workflow_version: `atelier-${config.mode}-v1`,
      session_id: null,
      summary: result.bundle.summary || `Generated ${config.mode} insight`,
      agent_runs: [
        {
          agent_name: "atelier-insight",
          stage: config.mode,
          status: "completed",
          session_id: null,
          model_name: result.modelName,
          duration_ms: null,
          api_duration_ms: null,
          total_cost_usd: null,
          input_tokens: result.inputTokens,
          output_tokens: result.outputTokens,
          summary: result.bundle.summary,
          output: result.bundle,
          started_at: result.startedAt,
          completed_at: result.completedAt,
        },
      ],
      reports: [normalizedReport],
    }

    console.log(JSON.stringify(output, null, 2))
  } catch (error) {
    console.error(JSON.stringify({ error: error.message }, null, 2))
    process.exit(1)
  }
}

main()
