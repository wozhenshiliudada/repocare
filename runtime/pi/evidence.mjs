// Project adapter protocol, not an upstream Pi event format. Never include model text.
import {MAX_USAGE_RECORDS} from './runtime-limits.mjs';
export const EVIDENCE_PREFIX = 'PI_EVIDENCE_V1 ';
const stops = new Set(['stop','length','toolUse','error','aborted']);
const tools = new Set(['read_source','propose_repair']);
export const investigationTools = new Set(['read_source','propose_repair',
  'search_approved_sources','read_approved_source']);
export class PiPlanningFailure extends Error {
  constructor(evidence) {
    super('Pi planning failed; inspect safe runtime evidence');
    this.name = 'PiPlanningFailure';
    this.evidence = evidence;
  }
}
export function safeEvent(event, allowedTools=tools) {
  if (['agent_start','agent_end'].includes(event.type)) return {type:event.type};
  if (['tool_execution_start','tool_execution_end','maintenance_tool'].includes(event.type)) {
    const name = event.toolName ?? event.name;
    if (!allowedTools.has(name)) return null;
    return {type:event.type,tool:name,...(typeof event.isError==='boolean'?{is_error:event.isError}:{}),
      ...(Number.isInteger(event.call)?{call:event.call}:{})};
  }
  if (event.type==='message_end' && event.message?.role==='assistant')
    return {type:'assistant_end',stop_reason:stops.has(event.message.stopReason)?event.message.stopReason:'unknown'};
  return null;
}
export function safeUsage(messages) {
  return messages.filter(m=>m.role==='assistant').slice(0,MAX_USAGE_RECORDS).map(m=>{
    const result={};
    for(const field of ['input','output','cacheRead','cacheWrite','totalTokens'])
      if(Number.isFinite(m.usage?.[field])&&m.usage[field]>=0)result[field]=m.usage[field];
    return result;
  });
}
export function unavailableEvidence(category='runtime_error') {
  return {schema_version:1,status:'failed',failure_category:category,consumption:'unknown',requests:null,
    requests_started:null,count_scope:'adapter_stream_invocations',events:[],usage:[],events_complete:false,
    model_cost_verified:false};
}
