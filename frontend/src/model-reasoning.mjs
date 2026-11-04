export const CANONICAL_REASONING_LEVELS=['off','minimal','low','medium','high','xhigh','max'];
export function reasoningChoices(profile){
 if(!profile||!['none','openai','deepseek'].includes(profile.reasoning_protocol))return [];
 if(Array.isArray(profile.reasoning_levels))return profile.reasoning_levels.filter(row=>
  row&&CANONICAL_REASONING_LEVELS.includes(row.level)).map(row=>({...row}));
 const levels=profile.reasoning_protocol==='none'?['off']:profile.reasoning_protocol==='deepseek'?['off','low','high','max']:CANONICAL_REASONING_LEVELS;
 return levels.map(level=>({level,api_value:level==='off'?(profile.reasoning_protocol==='openai'?'none':null):level}));
}
export function selectedReasoning(profile){
 const choices=reasoningChoices(profile);
 return choices.some(row=>row.level===profile?.reasoning_effort)?profile.reasoning_effort:choices[0]?.level||'off';
}
export function reasoningCaption(row,profile,language='zh-CN'){
 if(row.api_value===null&&!(row.level==='off'&&profile?.reasoning_protocol==='deepseek'))
  return row.level+(language==='en'?' → omitted (provider default)':' → 省略（服务商默认）');
 if(row.level==='off'&&profile?.reasoning_protocol==='deepseek')return language==='en'?'off → thinking disabled':'off → 关闭思考';
 return row.level+' → '+row.api_value;
}
