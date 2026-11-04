const roots=new WeakMap(),fieldIds=new WeakMap(),snapshots=new WeakMap();
const sensitive=/(?:api[\s_-]*key|key|token|secret|password|credential|confirm|approv|authoriz|permission|consent|grant|payment|cost|budget|allow[_-]?auto|autonomous|密钥|令牌|密码|确认|授权|权限|允许|同意|审批|批准|费用|预算|支付|自主)/i;
const permissionCheckbox=/(?:enable|allow|auto|execute|execution|monitor|delete|remove|write|publish|push|merge|destroy|开启|启用|自动|执行|修复|监听|监控|删除|写入|发布|合并|推送|联网)/i;
const id=()=>{const bytes=new Uint8Array(16);globalThis.crypto.getRandomValues(bytes);return [...bytes].map(n=>n.toString(16).padStart(2,'0')).join('');};
const plain=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
function visible(node){
 if(!node?.isConnected||node.closest?.('[hidden],[inert],[aria-hidden="true"]'))return false;
 if(node.getClientRects&&node.getClientRects().length===0)return false;
 const style=node.ownerDocument?.defaultView?.getComputedStyle?.(node);
 return !style||style.display!=='none'&&style.visibility!=='hidden'&&style.visibility!=='collapse';
}
function rootState(root){let state=roots.get(root);if(!state){state={id:id()};roots.set(root,state);}return state;}
/** Explicitly expire a draft when the app changes pages while retaining the same form root. */
export function invalidateForm(root){if(root)roots.set(root,{id:id()});}
function labelText(item){
 if(!item.childNodes)return item.textContent||'';
 const read=node=>['INPUT','TEXTAREA','SELECT','BUTTON'].includes(node.tagName)?'':node.nodeType===3?node.textContent||'':[...(node.childNodes||[])].map(read).join(' ');
 return read(item);
}
function label(node){
 const labels=[...(node.labels||[])].map(labelText).join(' ').trim();
 return String(node.getAttribute?.('aria-label')||labels||node.getAttribute?.('placeholder')||node.name||node.id||'').replace(/\s+/g,' ').trim().slice(0,160);
}
function collect(root){
 if(!visible(root)||typeof root.querySelectorAll!=='function')return null;
 const fields=[],nodes=[],observed=[],form_id=rootState(root).id;let omitted=0;
 for(const node of root.querySelectorAll('input,textarea,select')){
  if(!visible(node)||node.disabled||node.readOnly||node.matches?.(':disabled')||node.closest?.('[data-form-assist="exclude"]'))continue;
  // Only controls whose own handlers have been audited as local drafts may be filled.
  if(!['','true'].includes(node.getAttribute?.('data-form-assist-draft')))continue;
  const tag=node.tagName?.toLowerCase(),inputType=(node.type||'text').toLowerCase(),text=label(node),purpose=[text,...[...(node.labels||[])].map(labelText),node.id,node.name,node.getAttribute?.('placeholder'),node.getAttribute?.('autocomplete')];
  if(!text||sensitive.test(purpose.join(' ')))continue;
  let type;
  if(tag==='textarea')type='text';
  else if(tag==='select'&&!node.multiple)type='select';
  else if(tag==='input'&&['text','url','email','search','tel','time'].includes(inputType))type='text';
  else if(tag==='input'&&['number','checkbox'].includes(inputType))type=inputType;
  else continue;
  if(type==='checkbox'&&permissionCheckbox.test([text,node.id,node.name].join(' ')))continue;
  let fieldId=fieldIds.get(node);if(!fieldId){fieldId=id();fieldIds.set(node,fieldId);}
  const value=type==='checkbox'?Boolean(node.checked):type==='number'?(node.value===''?'':Number(node.value)):String(node.value??'');
  const field={id:fieldId,label:text,type,value};
  observed.push({field,purpose,tag,inputType,draft:node.getAttribute?.('data-form-assist-draft')});
  if(typeof value==='number'&&!Number.isFinite(value)||typeof value==='string'&&value.length>4000){omitted++;continue;}
  if(type==='select'){
   const options=[...node.options].filter(option=>!option.disabled&&!option.parentElement?.disabled);
   field.options=options.map(option=>String(option.value));
   field.option_labels=options.map(option=>String(option.label||option.textContent||option.value));
   if(field.options.length>100||field.options.some(option=>option.length>256)||field.option_labels.some(caption=>caption.length>256)||!field.options.includes(value)){omitted++;continue;}
  }
  if(type==='number'){for(const boundary of ['min','max'])if(node[boundary]!==''&&node[boundary]!=null&&Number.isFinite(Number(node[boundary])))field[boundary]=Number(node[boundary]);}
  if(fields.length>=64||new TextEncoder().encode(JSON.stringify({schema_version:1,form_id,revision:'0'.repeat(64),fields:[...fields,field]})).byteLength>100000){omitted++;continue;}
  fields.push(field);nodes.push(node);
 }
 if(!fields.length)return null;
 const signature=JSON.stringify({schema_version:1,form_id,fields,observed});
 return {form_id,fields,nodes,signature,omitted};
}
export async function captureForm(root){
 const initial=collect(root);if(!initial)return null;
 const hash=await globalThis.crypto.subtle.digest('SHA-256',new TextEncoder().encode(initial.signature));
 const current=collect(root);if(!current||current.signature!==initial.signature||current.nodes.some((node,index)=>node!==initial.nodes[index]))return null;
 const revision=[...new Uint8Array(hash)].map(n=>n.toString(16).padStart(2,'0')).join('');
 const snapshot={schema_version:1,form_id:initial.form_id,revision,fields:initial.fields};
 snapshots.set(snapshot,{root,...initial});return snapshot;
}
export function formCaptureInfo(snapshot){return {omitted:snapshots.get(snapshot)?.omitted||0};}
function validate(snapshot,proposal){
 if(!plain(proposal)||proposal.draft_only!==true||proposal.form_id!==snapshot?.form_id||proposal.revision!==snapshot?.revision||!Array.isArray(proposal.changes)||!proposal.changes.length||proposal.changes.length>64)return null;
 const fields=new Map(snapshot.fields.map(field=>[field.id,field])),seen=new Set(),changes=[];
 for(const change of proposal.changes){
  if(!plain(change)||Object.keys(change).some(key=>!['field_id','value'].includes(key))||seen.has(change.field_id))return null;
  const field=fields.get(change.field_id),value=change.value;if(!field)return null;seen.add(change.field_id);
  if(field.type==='checkbox'&&typeof value!=='boolean'||field.type==='text'&&(typeof value!=='string'||value.length>4000)||field.type==='select'&&(typeof value!=='string'||!field.options?.includes(value))||field.type==='number'&&(typeof value!=='number'||!Number.isFinite(value)||field.min!==undefined&&value<field.min||field.max!==undefined&&value>field.max))return null;
  changes.push({field,value,before:field.value});
 }
 return changes;
}
/** Returns a data-only diff; no values are written while a proposal is displayed. */
export function describeProposal(snapshot,proposal){const changes=validate(snapshot,proposal);return changes?.map(({field,value,before})=>({id:field.id,label:field.label,type:field.type,before,after:value}))||null;}
function setter(node,property){
 let prototype=Object.getPrototypeOf(node);
 while(prototype){const descriptor=Object.getOwnPropertyDescriptor(prototype,property);if(descriptor?.set)return descriptor.set;prototype=Object.getPrototypeOf(prototype);}
 return null;
}
/** Applies user-reviewed draft values only. It never clicks a button, submits, or calls an API. */
export async function applyProposal(root,snapshot,proposal){
 const binding=snapshots.get(snapshot),changes=validate(snapshot,proposal);
 if(!changes)return {applied:false,reason:'invalid'};
 if(!binding||root!==binding.root)return {applied:false,reason:'stale'};
 const current=await captureForm(root),after=collect(root);
 if(!current||!after||current.form_id!==snapshot.form_id||current.revision!==snapshot.revision||after.signature!==binding.signature||after.nodes.length!==binding.nodes.length||after.nodes.some((node,index)=>node!==binding.nodes[index]))return {applied:false,reason:'stale'};
 const prepared=changes.map(change=>{const index=snapshot.fields.findIndex(field=>field.id===change.field.id),node=binding.nodes[index],property=change.field.type==='checkbox'?'checked':'value';return {...change,node,property,set:setter(node,property)};});
 if(prepared.some(change=>!change.set))return {applied:false,reason:'unavailable'};
 // Time remains a text field in the public protocol. Check its trusted DOM subtype
 // for the entire batch before dispatching any draft event or writing any value.
 if(prepared.some(change=>change.node.type==='time'&&!/^(?:[01]\d|2[0-3]):[0-5]\d$/.test(change.value)))return {applied:false,reason:'invalid'};
 const expected=JSON.parse(binding.signature);
 const fresh=()=>{const value=collect(root);return value&&value.signature===JSON.stringify(expected)&&value.nodes.length===binding.nodes.length&&value.nodes.every((node,index)=>node===binding.nodes[index]);};
 let updated=0;const stale=()=>{invalidateForm(root);return {applied:false,reason:'stale',...(updated?{updated}:{})};};
 try{
  for(const change of prepared){
   if(!fresh()||!root.isConnected||!change.node.isConnected||root.contains&&!root.contains(change.node))return stale();
   for(const field of [...expected.fields,...expected.observed.map(item=>item.field)])if(field.id===change.field.id)field.value=change.value;
   change.set.call(change.node,change.value);updated++;const EventClass=change.node.ownerDocument?.defaultView?.Event||globalThis.Event;change.node.dispatchEvent(new EventClass('input',{bubbles:true}));
   // A synchronous input handler may change steps or repurpose a still-connected node.
   if(!fresh())return stale();
   change.node.dispatchEvent(new EventClass('change',{bubbles:true}));
   if(!fresh())return stale();
  }
  invalidateForm(root);return {applied:true,reason:'applied'};
 }catch{invalidateForm(root);return {applied:false,reason:'update_failed',...(updated?{updated}:{})};}
}
