import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

/**
 * Simple Claude SDK query script for AI provider
 * Usage: node claude_simple_query.mjs <workspace_path>
 */

const workspaceArg = process.argv[2];
if (!workspaceArg) {
  console.error('Workspace path is required');
  process.exit(1);
}

const workspacePath = path.resolve(workspaceArg);
const projectRoot = process.cwd();
const promptPath = path.join(workspacePath, 'prompt.txt');
const taskConfigPath = path.join(workspacePath, 'task_config.json');

if (!fs.existsSync(promptPath)) {
  console.error(`Missing prompt file at ${promptPath}`);
  process.exit(1);
}

// Read the prompt
const prompt = fs.readFileSync(promptPath, 'utf8');
let taskConfig = {};
if (fs.existsSync(taskConfigPath)) {
  try {
    taskConfig = JSON.parse(fs.readFileSync(taskConfigPath, 'utf8'));
  } catch (err) {
    console.warn('Failed to load task config:', err.message);
  }
}

// Inject environment variables from local Claude Code configuration if available
const homeDir = os.homedir();
const settingsPath = path.join(homeDir, '.claude', 'settings.json');
if (fs.existsSync(settingsPath)) {
  try {
    const settings = JSON.parse(fs.readFileSync(settingsPath, 'utf8'));
    if (settings.env) {
      for (const [key, value] of Object.entries(settings.env)) {
        if (!process.env[key]) {
          process.env[key] = value;
        }
      }
    }
  } catch (err) {
    console.warn('Failed to load local Claude settings:', err.message);
  }
}

// Load Claude Agent SDK
const sdkRoot = process.env.CLAUDE_AGENT_SDK_ROOT || path.join(os.homedir(), 'agent-sdk');
const sdkPath = path.join(
  sdkRoot,
  'node_modules',
  '@anthropic-ai',
  'claude-agent-sdk',
  'sdk.mjs',
);

if (!fs.existsSync(sdkPath)) {
  console.error(`Claude Agent SDK not found at ${sdkPath}`);
  process.exit(1);
}

const { query } = await import(pathToFileURL(sdkPath).href);

// Run the query
try {
  let result = '';

  for await (const message of query({
    prompt,
    options: {
      cwd: projectRoot,
      additionalDirectories: [workspacePath],
      settingSources: ['project', 'user'],
      agent: 'assistant',
      allowedTools: Array.isArray(taskConfig.allowedTools) ? taskConfig.allowedTools : [],
      permissionMode: 'dontAsk',
      maxTurns: Number.isFinite(Number(taskConfig.maxTurns)) ? Number(taskConfig.maxTurns) : 1,
      effort: typeof taskConfig.effort === 'string' ? taskConfig.effort : 'low',
      thinkingConfig: { type: 'disabled' },
    },
  })) {
    if (message.type === 'assistant' && message.message?.content) {
      for (const part of message.message.content) {
        if (part.type === 'text') {
          result += part.text;
        }
      }
    } else if (message.type === 'result' && message.subtype === 'success') {
      // Output the final result
      console.log(JSON.stringify({
        result: result || message.result,
        session_id: message.session_id || message.sessionId,
        model: message.model || message.model_name,
      }));
      process.exit(0);
    }
  }

  // If we get here without a result, something went wrong
  console.error('No result received from Claude SDK');
  process.exit(1);

} catch (err) {
  console.error('Claude SDK query failed:', err.message);
  process.exit(1);
}
