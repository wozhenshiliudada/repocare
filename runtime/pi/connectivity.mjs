// One explicitly approved Pi response. No project, tools, history or retry.
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createAgentSession, ModelRuntime, SessionManager, SettingsManager,
  createExtensionRuntime, VERSION} from '@earendil-works/pi-coding-agent';
import {streamSimple} from '@earendil-works/pi-ai/api/openai-completions';
import {preparePayload} from './provider-payload.mjs';
import {effectiveModelReasoning,providerModel,reasoningStreamOptions} from './provider-model.mjs';
import {effectiveModelLimits} from './model-limits.mjs';
import {inputUpperEstimate} from './compaction-settings.mjs';
const {AuthStorage} = await import(new URL('./core/auth-storage.js',
  import.meta.resolve('@earendil-works/pi-coding-agent')).href);

export async function checkConnectivity(request, options={}) {
  if (VERSION !== '1.0.4' || request.protocol_version !== 1 || request.request_budget !== 1
      || !/^[a-f0-9]{64}$/.test(request.preview_revision)) throw new Error('Invalid explicit connectivity request');
  let key, endpoint, modelId;
  if (options.mockUrl) {
    const url = new URL(options.mockUrl);
    if (url.protocol !== 'http:' || url.hostname !== '127.0.0.1' || url.username || url.password
        || url.search || url.hash) throw new Error('Expected explicit synthetic loopback endpoint');
    key='synthetic-not-a-real-key'; endpoint=options.mockUrl; modelId='mock-maintenance';
  } else {
    key=process.env.PI_MAINTENANCE_API_KEY;
    endpoint=process.env.PI_MAINTENANCE_BASE_URL;
    modelId=process.env.PI_MAINTENANCE_MODEL;
    if (!key || !endpoint || !modelId) throw new Error('Explicit provider configuration required');
    const url=new URL(endpoint);
    if (url.protocol !== 'https:' || url.username || url.password || url.search || url.hash)
      throw new Error('Expected explicit HTTPS endpoint');
  }
  const limits=effectiveModelLimits({modelId,baseUrl:endpoint,mockUrl:options.mockUrl,legacyContextWindowTokens:32768,
    legacyMaxOutputTokens:64});
  const probeOutputTokens=Math.min(64,limits.maxOutputTokens);
  const reasoning=effectiveModelReasoning();
  const scratch=await fs.mkdtemp(path.join(os.tmpdir(),'repocare-connectivity-'));
  let session, timer, requests=0, completed=0, timedOut=false;
  try {
    const runtime=await ModelRuntime.create({credentials:AuthStorage.inMemory(),modelsPath:null,
      modelsStorePath:path.join(scratch,'catalog.json'),allowModelNetwork:false,refreshOnCreate:false});
    const provider='pi-connectivity';
    runtime.registerProvider(provider,{name:'Explicit connectivity',api:'openai-completions',baseUrl:endpoint,
      models:[providerModel({id:modelId,name:modelId,reasoning:false,input:['text'],contextWindow:limits.contextWindowTokens,maxTokens:limits.maxOutputTokens,
        cost:{input:0,output:0,cacheRead:0,cacheWrite:0},
        compat:{supportsStore:false,supportsDeveloperRole:false,maxTokensField:'max_tokens'}},reasoning)],
      streamSimple:(model,context,opts)=>{
        if(limits.enforceContextBudget&&inputUpperEstimate(context)+probeOutputTokens>=limits.budgetContextWindowTokens)
          throw new Error('Connectivity context capacity reached');
        if (requests !== 0) throw new Error('Connectivity allows exactly one adapter invocation');
        requests++;
        const stream=streamSimple(model,context,{...opts,...reasoningStreamOptions(reasoning),maxRetries:0,maxTokens:probeOutputTokens,timeoutMs:60000,
          temperature:0,onPayload:payload=>preparePayload(payload,endpoint,reasoning)});
        stream.result().then(message=>{
          if (message.stopReason==='stop' && message.content.some(c=>c.type==='text' && c.text.trim())) completed++;
        },()=>{});
        return stream;
      }});
    await runtime.setRuntimeApiKey(provider,key);
    const resourceLoader={getExtensions:()=>({extensions:[],errors:[],runtime:createExtensionRuntime()}),
      getSkills:()=>({skills:[],diagnostics:[]}),getPrompts:()=>({prompts:[],diagnostics:[]}),
      getThemes:()=>({themes:[],diagnostics:[]}),getAgentsFiles:()=>({agentsFiles:[]}),
      getSystemPrompt:()=> 'This is one connectivity check. Reply with REPOCARE_CONNECTIVITY_OK. No tools or project work.',
      getSystemPromptSource:()=>undefined,getAppendSystemPrompt:()=>[],getAppendSystemPromptSources:()=>[],
      extendResources:()=>{},reload:async()=>{}};
    ({session}=await createAgentSession({cwd:scratch,agentDir:path.join(scratch,'agent'),modelRuntime:runtime,
      model:runtime.getModel(provider,modelId),thinkingLevel:reasoning.thinkingLevel,tools:[],customTools:[],resourceLoader,
      sessionManager:SessionManager.inMemory(scratch),
      settingsManager:SettingsManager.inMemory({compaction:{enabled:false},retry:{enabled:false},cacheWarming:'off'})}));
    if (session.getActiveToolNames().length !== 0) throw new Error('Unexpected connectivity tools');
    timer=setTimeout(()=>{timedOut=true;session.agent.abort();},options.timeoutMs??60000);
    await session.prompt('Reply once to confirm this configured model can respond.');
    if (timedOut || requests!==1 || completed!==1 || session.messages.some(m=>m.role==='assistant'
        && m.stopReason!=='stop')) throw new Error('Connectivity did not finish one text response');
    return {protocol_version:1,status:'completed',evidence:{schema_version:1,purpose:'model_connectivity',
      status:'completed',pi_version:VERSION,requests:1,requests_started:1,consumption:'reported',
      count_scope:'adapter_stream_invocations',active_tools:[],tool_calls:0,response_received:true,
      preview_revision:request.preview_revision,transport_retries:0,repair_verified:false,
      model_cost_verified:false,transport:options.mockUrl?'loopback-mock':'live'}};
  } finally {
    clearTimeout(timer);session?.dispose();await fs.rm(scratch,{recursive:true,force:true});
  }
}

if (process.argv[1]===fileURLToPath(import.meta.url)) {
  try {
    let raw='';for await (const chunk of process.stdin) {raw+=chunk;if(raw.length>4096)throw new Error('Input too large');}
    const args=process.argv.slice(2),index=args.indexOf('--mock-url');
    const result=await checkConnectivity(JSON.parse(raw),{mockUrl:index>=0?args[index+1]:undefined});
    process.stdout.write(JSON.stringify(result));
  } catch {
    // Never emit raw model text, credentials, URLs from errors, or a guessed count.
    process.stdout.write(JSON.stringify({protocol_version:1,status:'failed'}));process.exitCode=1;
  }
}
