// Explicit wire compatibility for immutable profiles. No model-name heuristics.
export const REASONING_FIELDS=['PI_MAINTENANCE_REASONING_PROTOCOL','PI_MAINTENANCE_REASONING_EFFORT','PI_MAINTENANCE_REASONING_LEVELS'];
export const REASONING_EFFORTS={none:['off'],openai:['off','minimal','low','medium','high','xhigh','max'],deepseek:['off','low','high','max']};
export function validatedReasoningLevels(levels,protocol){
 if(!Array.isArray(levels)||levels.length<1||levels.length>7)throw new Error('Invalid reasoning levels');
 const seen=new Set();
 for(const row of levels){
  if(!row||typeof row!=='object'||Object.keys(row).sort().join()!=='api_value,level'
   ||!REASONING_EFFORTS.openai.includes(row.level)||seen.has(row.level))throw new Error('Invalid reasoning level');
  seen.add(row.level);const value=row.api_value;
  if(value!==null&&(typeof value!=='string'||!value.length||value.length>64||/[\s\x00-\x1f\x7f]/u.test(value)))
   throw new Error('Invalid reasoning API value');
  if(protocol==='none'&&(row.level!=='off'||value!==null)
   ||protocol==='deepseek'&&row.level==='off'&&value!==null
   ||protocol==='openai'&&row.level==='off'&&value!==null&&value!=='none')throw new Error('Invalid off mapping');
 }
 return levels;
}
export function effectiveModelReasoning(env=process.env){
 const present=REASONING_FIELDS.filter(field=>Object.hasOwn(env,field));
 if(!present.length)return {explicit:false,protocol:null,effort:null,thinkingLevel:'off'};
 const protocol=env[REASONING_FIELDS[0]],effort=env[REASONING_FIELDS[1]];
 if(!Object.hasOwn(env,REASONING_FIELDS[0])||!Object.hasOwn(env,REASONING_FIELDS[1])||!Object.hasOwn(REASONING_EFFORTS,protocol))
  throw new Error('Unsupported or incomplete model reasoning settings');
 let levels=null;
 if(Object.hasOwn(env,REASONING_FIELDS[2])){
  try{levels=validatedReasoningLevels(JSON.parse(env[REASONING_FIELDS[2]]),protocol);}
  catch{throw new Error('Invalid reasoning mappings');}
 }
 const allowed=levels?.map(row=>row.level)??REASONING_EFFORTS[protocol];
 if(!allowed.includes(effort))throw new Error('Selected reasoning level is not configured');
 const apiValue=levels?levels.find(row=>row.level===effort).api_value:effort==='off'?(protocol==='openai'?'none':null):effort;
 return {explicit:true,protocol,effort,levels,apiValue,thinkingLevel:effort};
}
export function providerModel(model,reasoning){
 if(!reasoning.explicit)return model;
 if(reasoning.protocol==='none')return {...model,reasoning:false,compat:{...model.compat,supportsReasoningEffort:false}};
 // SDK null means "unsupported", while our API null means "omit the field".
 // Keep supported canonical levels for Pi; the final payload applies the exact mapping.
 const supported=reasoning.levels?.map(row=>row.level)??REASONING_EFFORTS[reasoning.protocol];
 const thinkingLevelMap=Object.fromEntries(REASONING_EFFORTS.openai.map(level=>
  [level,supported.includes(level)?(level==='off'?'none':level):null]));
 return {...model,reasoning:true,thinkingLevelMap,compat:{...model.compat,
  thinkingFormat:reasoning.protocol,supportsReasoningEffort:true,requiresThinkingAsText:false,
  requiresReasoningContentOnAssistantMessages:reasoning.protocol==='deepseek'}};
}
export function reasoningStreamOptions(reasoning){
 if(!reasoning.explicit)return {};
 return {reasoning:reasoning.effort==='off'?undefined:reasoning.effort};
}
