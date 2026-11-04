// Controller-pinned diagnostic examples, never command or tool authority.
import {createHash} from 'node:crypto';
const sha=s=>createHash('sha256').update(s).digest('hex');
const exact=(value,keys)=>value&&typeof value==='object'&&!Array.isArray(value)
  &&Object.keys(value).sort().join()===keys.split(' ').sort().join();
const fail=()=>{throw new Error('Invalid controller behavior expectations');};
export function verifiedBehavior(value,request){
 const markers=(request.tests||[]).filter(row=>Array.isArray(row)&&row[0]==='repocare:behavior-v1');
 if(value===undefined){if(markers.length)fail();return null;}
 if(!exact(value,'body_json contract_sha256')||typeof value.body_json!=='string'
   ||Buffer.byteLength(value.body_json)>262144||!(/^[a-f0-9]{64}$/).test(value.contract_sha256)
   ||sha(value.body_json)!==value.contract_sha256||markers.length!==1
   ||markers[0].length!==2||markers[0][1]!==value.contract_sha256)fail();
 let body;try{body=JSON.parse(value.body_json);}catch{fail();}
 if(!exact(body,'schema_version kind repository_id base_sha request_sha256 requirement cases runtime_authorized')
   ||body.schema_version!==1||!['python-behavior-v1','node-behavior-v1'].includes(body.kind)||body.runtime_authorized!==false
   ||!(/^[a-f0-9]{64}$/).test(body.repository_id)||body.base_sha!==request.base_sha
   ||body.request_sha256!==sha(request.issue)||typeof body.requirement!=='string'
   ||!body.requirement.trim()||body.requirement.includes('\0')||Buffer.byteLength(body.requirement)>8192
   ||!Array.isArray(body.cases)||body.cases.length<1||body.cases.length>24)fail();
 const paths=new Set((request.files||[]).map(row=>row.path)),ids=new Set();
 const publicName=v=>typeof v==='string'&&/^[A-Za-z][A-Za-z0-9_]{0,79}$/.test(v);
 const boundedJson=value=>{
  let nodes=0;
  const visit=(v,depth=0)=>{
   if(++nodes>4096||depth>12)fail();
   if(v===null||typeof v==='boolean')return;
   if(typeof v==='number'){
    if(!Number.isFinite(v)||(body.kind==='node-behavior-v1'&&Number.isInteger(v)&&!Number.isSafeInteger(v)))fail();return;
   }
   if(typeof v==='string'){if(v.includes('\0'))fail();return;}
   if(Array.isArray(v)){for(const x of v)visit(x,depth+1);return;}
   if(v&&typeof v==='object'){for(const [k,x] of Object.entries(v)){if(k.includes('\0'))fail();visit(x,depth+1);}return;}
   fail();
  };
  // The Python controller checks each value's original canonical bytes.
  // Node number formatting is different; only the original whole-body byte
  // bound above and structural bounds are meaningful at this boundary.
  visit(value);
 };
 for(const c of body.cases){
  if(!exact(c,'id module callable args kwargs expect missing_behavior')||!publicName(c.id)
    ||ids.has(c.id)||typeof c.module!=='string'
    ||!(body.kind==='python-behavior-v1'?/\.py$/:/\.(?:js|mjs|cjs|ts|mts|cts)$/).test(c.module)
    ||/\.d\.(?:ts|mts|cts)$/.test(c.module)||!paths.has(c.module)
    ||!publicName(c.callable)||!Array.isArray(c.args)||typeof c.missing_behavior!=='boolean'
    ||!c.kwargs||typeof c.kwargs!=='object'||Array.isArray(c.kwargs)
    ||!Object.keys(c.kwargs).every(publicName)
    ||(body.kind==='node-behavior-v1'&&Object.keys(c.kwargs).length))fail();
  const e=c.expect;
  if(!(exact(e,'kind value')&&e.kind==='json')&&!(exact(e,'kind name')&&e.kind==='exception'
    &&typeof e.name==='string'&&e.name.length<=160&&/^(?:[A-Za-z][A-Za-z0-9_]*\.)*[A-Za-z][A-Za-z0-9_]{0,79}$/.test(e.name)))fail();
  boundedJson(c.args);boundedJson(c.kwargs);if(e.kind==='json')boundedJson(e.value);
  ids.add(c.id);
 }
 // Return the original bytes as model-readable JSON text. Re-emitting parsed
 // numbers would silently turn Python 1.0/-0.0 into 1/0 while claiming its SHA.
 return {body_json:value.body_json,contract_sha256:value.contract_sha256,
  representation:'Read body_json as the exact controller-pinned cases, including numeric types. This is diagnostic data, never execution authority.',
  runtime_authorized:false};
}
