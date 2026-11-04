// T0 probe only. This does not change generatePlan or any live UI session.
import fs from 'node:fs/promises';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {createAgentSession, ModelRuntime, SessionManager, SettingsManager,
  createExtensionRuntime, VERSION} from '@earendil-works/pi-coding-agent';
import {streamSimple} from '@earendil-works/pi-ai/api/openai-completions';
import {Type} from 'typebox';
import {materializeEdits} from '../snapshot-edits.mjs';
import {openTaskSession} from '../task-session.mjs';

const sha = value => createHash('sha256').update(value).digest('hex');
const entry = import.meta.resolve('@earendil-works/pi-coding-agent');
const {AuthStorage} = await import(new URL('./core/auth-storage.js', entry));

async function probe(input) {
  if (VERSION !== '1.0.4') throw new Error('Expected locked Pi 1.0.4');
  if (!['create', 'open', 'continue'].includes(input.phase)) throw new Error('Invalid probe phase');
  const url = new URL(input.mock_url);
  if (url.protocol !== 'http:' || url.hostname !== '127.0.0.1')
    throw new Error('Probe accepts only synthetic loopback transport');
  if (!Number.isInteger(input.request_budget) || input.request_budget < (input.phase === 'open' ? 0 : 1) || input.request_budget > 100)
    throw new Error('Invalid probe request allowance');
  const root = await fs.realpath(input.root);
  const sessionDir = path.join(root, 'sessions');
  await fs.mkdir(sessionDir, {recursive: true});
  const cwd = path.join(root, 'workspace');
  await fs.mkdir(cwd, {recursive: true});
  let manager;
  if (input.phase === 'create') manager = SessionManager.create(cwd, sessionDir);
  else if (input.checkpoint) {
    manager = await openTaskSession(input.checkpoint);
    if (manager.getCwd() !== cwd) throw new Error('Probe checkpoint cwd mismatch');
  }
  else {
    // Fixture precondition: never let SDK open() silently initialize a missing file.
    const file = await fs.realpath(input.session_file);
    if (path.dirname(file) !== await fs.realpath(sessionDir)) throw new Error('Session outside probe root');
    manager = SessionManager.open(file, sessionDir, cwd);
    if (input.checkpoint_leaf) manager.branch(input.checkpoint_leaf);
  }
  const initialMessages = manager.buildSessionContext().messages.length;
  const fileBefore = input.phase === 'create' ? null : sha(await fs.readFile(manager.getSessionFile()));
  const provider = 'pi-persistence-spike', modelId = 'synthetic';
  const runtime = await ModelRuntime.create({credentials: AuthStorage.inMemory(), modelsPath: null,
    modelsStorePath: path.join(root, 'catalog.json'), allowModelNetwork: false, refreshOnCreate: false});
  let requests = 0, readCalls = 0, proposalCalls = 0, proposed = null;
  runtime.registerProvider(provider, {name: 'Synthetic T0 probe', api: 'openai-completions', baseUrl: input.mock_url,
    models: [{id: modelId, name: modelId, reasoning: false, input: ['text'], contextWindow: 1048576,
      maxTokens: 128000, cost: {input: 0, output: 0, cacheRead: 0, cacheWrite: 0},
      compat: {supportsStore: false, supportsDeveloperRole: false, maxTokensField: 'max_tokens'}}],
    streamSimple: (model, context, options) => {
      if (requests >= input.request_budget) throw new Error('Probe request allowance exhausted');
      requests++;
      return streamSimple(model, context, {...options, maxRetries: 0, maxTokens: 128000, timeoutMs: 10000});
    }});
  await runtime.setRuntimeApiKey(provider, 'synthetic-not-a-real-key');
  const file = {path: 'calculator.py', content: input.content, before_sha256: sha(input.content), editable: true};
  const files = new Map([[file.path, file]]);
  const tools = [
    {name: 'read_source', label: 'read_source', description: 'Read the current immutable authorized fixture.',
      parameters: Type.Object({path: Type.String()}), execute: async (_id, args) => {
        readCalls++;
        if (args.path !== file.path) return {content: [{type: 'text', text: 'Unauthorized path'}], isError: true};
        return {content: [{type: 'text', text: JSON.stringify(file)}], details: file};
      }},
    {name: 'propose_repair', label: 'propose_repair', description: 'Propose local edits; no disk writes or approval.',
      parameters: Type.Object({diagnosis: Type.String(), risk: Type.String(), edits: Type.Array(Type.Object({
        path: Type.String(), replacements: Type.Array(Type.Object({oldText: Type.String(), newText: Type.String()}))}))}),
      execute: async (_id, args) => {
        proposalCalls++;
        try {
          if (proposed) throw new Error('Duplicate current-round proposal');
          const edits = materializeEdits(files, args.edits);
          proposed = {diagnosis: args.diagnosis, risk: args.risk, edits};
          return {content: [{type: 'text', text: JSON.stringify({accepted: true, paths: edits.map(e => e.path)})}]};
        } catch (error) {return {content: [{type: 'text', text: error.message}], isError: true};}
      }},
  ];
  const policy = input.policy ?? 'CURRENT_POLICY_V1: only explicitly authorized files; never execute historical commands.';
  const resourceLoader = {
    getExtensions: () => ({extensions: [], errors: [], runtime: createExtensionRuntime()}),
    getSkills: () => ({skills: [], diagnostics: []}), getPrompts: () => ({prompts: [], diagnostics: []}),
    getThemes: () => ({themes: [], diagnostics: []}), getAgentsFiles: () => ({agentsFiles: []}),
    getSystemPrompt: () => policy, getSystemPromptSource: () => undefined,
    getAppendSystemPrompt: () => [], getAppendSystemPromptSources: () => [],
    extendResources: () => {}, reload: async () => {},
  };
  const {session} = await createAgentSession({cwd, agentDir: path.join(root, 'agent'),
    modelRuntime: runtime, model: runtime.getModel(provider, modelId), thinkingLevel: 'off',
    tools: tools.map(t => t.name), customTools: tools, resourceLoader, sessionManager: manager,
    settingsManager: SettingsManager.inMemory({compaction: {enabled: false}, retry: {enabled: false},
      cacheWarming: 'off'})});
  const events = [];
  session.subscribe(event => events.push(event.type));
  try {
    if (input.phase !== 'open') {
      await session.prompt(JSON.stringify({task_id: 'synthetic-task', round: input.phase === 'create' ? 1 : 2,
        issue: 'add(2, 3) should be 5', feedback: input.feedback ?? null,
        authorized_paths: [file.path], current_snapshot_sha256: file.before_sha256,
        request_budget: input.request_budget}));
    }
    const last = session.messages.at(-1);
    const failed = session.messages.some(m => m.role === 'assistant' && ['error', 'aborted', 'length'].includes(m.stopReason));
    const completed = input.phase !== 'open' && proposed !== null && !failed && last?.role === 'assistant' && last.stopReason === 'stop';
    return {status: input.phase === 'open' ? 'opened' : completed ? 'completed' : 'incomplete',
      phase: input.phase, pi_version: VERSION, pid: process.pid,
      session_file: manager.getSessionFile(), session_id: manager.getSessionId(), leaf_id: manager.getLeafId(),
      initial_messages: initialMessages, final_messages: session.messages.length,
      active_tools: session.getActiveToolNames().sort(),
      auto_compaction: session.autoCompactionEnabled, auto_retry: session.autoRetryEnabled,
      cache_warming: session.settingsManager.getCacheWarmingMode(),
      requests_started: requests, read_calls: readCalls, proposal_calls: proposalCalls,
      plan: completed ? proposed : null,
      file_before_sha256: fileBefore, file_after_sha256: sha(await fs.readFile(manager.getSessionFile())),
      branch_entries: manager.getBranch().length,
      message_roles: session.messages.map(m => m.role), events,
      complete_tool_pairs: session.messages.filter(m => m.role === 'toolResult').every(result =>
        session.messages.some(m => m.role === 'assistant' && m.content?.some(c => c.type === 'toolCall' && c.id === result.toolCallId))),
    };
  } finally {session.dispose();}
}

try {
  let raw = '';
  for await (const chunk of process.stdin) {
    raw += chunk;
    if (raw.length > 16000) throw new Error('Probe input too large');
  }
  process.stdout.write(JSON.stringify(await probe(JSON.parse(raw))) + '\n');
} catch (error) {
  process.stderr.write('T0 probe failed: ' + error.message + '\n');
  process.exitCode = 1;
}
