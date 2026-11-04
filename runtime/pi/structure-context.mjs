// Independent bounded structure parser. Receipts bind bytes; they are not signatures.
import {createHash} from 'node:crypto';
const ordered=value=>Array.isArray(value)?value.map(ordered):value&&typeof value==='object'
 ?Object.fromEntries(Object.keys(value).sort().map(key=>[key,ordered(value[key])])):value;
export const structureHash=value=>createHash('sha256').update(JSON.stringify(ordered(value))).digest('hex');
const hash=value=>createHash('sha256').update(value).digest('hex');
const keys=(value,names)=>value&&typeof value==='object'&&!Array.isArray(value)
 &&Object.keys(value).sort().join(',')===names.split(' ').sort().join(',');
const hex=value=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value);
const commit=value=>typeof value==='string'&&/^(?:[a-f0-9]{40}|[a-f0-9]{64})$/.test(value);
const text=(value,max)=>typeof value==='string'&&Array.from(value).length>=1&&Array.from(value).length<=max&&!/[\x00-\x1f]/.test(value);
const kinds=new Set('file module class struct interface trait protocol function method property field variable constant enum enum_member type_alias namespace parameter import export route component union'.split(' '));
const edgeKinds=new Set('contains calls imports exports extends implements references type_of returns instantiates overrides decorates navigates'.split(' '));
function sourcePath(value){
 return text(value,512)&&!/[\\:]/.test(value)&&/\.(?:py|js|ts)$/.test(value)
  &&!value.split('/').some(p=>!p||p==='.'||p==='..'||['.git','node_modules','vendor','build','dist','.codegraph','.pi','ui-state'].includes(p.toLowerCase())
    ||/^(?:\.env|credentials|secrets)/i.test(p));
}

export function verifiedStructure(value,issue,planningBase,files){
 if(value===undefined)return null;
 const fail=()=>{throw new Error('Invalid structure context');};
 if(!keys(value,'schema_version engine source manifest_sha256 manifest_file_sha256 graph_sha256 planning_base_sha planning_files_sha256 issue_sha256 query query_sha256 hits edges related_nodes structural_claims_only runtime_authorized context_sha256')
  ||value.schema_version!==1||!keys(value.engine,'name version')||value.engine.name!=='@colbymchenry/codegraph'||value.engine.version!=='1.6.2'
  ||value.structural_claims_only!==true||value.runtime_authorized!==false||!commit(value.planning_base_sha)||value.planning_base_sha!==planningBase
  ||typeof issue!=='string'||!hex(value.issue_sha256)||value.issue_sha256!==hash(issue)
  ||!text(value.query,128)||!hex(value.query_sha256)||value.query_sha256!==hash(value.query)
  ||!['manifest_sha256','manifest_file_sha256','graph_sha256','context_sha256','planning_files_sha256'].every(k=>hex(value[k])))fail();
 if(!Array.isArray(files)||files.length<1||files.length>20||files.some(f=>!f||typeof f.path!=='string'||typeof f.content!=='string')
  ||structureHash(files.map(f=>({path:f.path,source_sha256:hash(f.content)})))!==value.planning_files_sha256)fail();
 const source=value.source;
 if(!keys(source,'root_sha256 common_dir_sha256 head branch files')||!hex(source.root_sha256)||!hex(source.common_dir_sha256)
  ||!commit(source.head)||!(source.branch===''||text(source.branch,512))||!Array.isArray(source.files)||!source.files.length||source.files.length>20)fail();
 const paths=new Map(),folded=new Set();
 for(const file of source.files){
  if(!keys(file,'path source_sha256 lines')||!sourcePath(file.path)||!hex(file.source_sha256)
   ||!Number.isSafeInteger(file.lines)||file.lines<1||file.lines>65537||folded.has(file.path.toLowerCase()))fail();
  folded.add(file.path.toLowerCase());paths.set(file.path,file);
 }
 if(!Array.isArray(value.hits)||value.hits.length>5||!Array.isArray(value.edges)||value.edges.length>50
  ||!Array.isArray(value.related_nodes)||value.related_nodes.length>50)fail();
 const nodes=new Map(),hits=new Set();
 for(const [group,rows] of [['hits',value.hits],['related',value.related_nodes]])for(const node of rows){
  const file=paths.get(node?.path),language={'.py':'python','.js':'javascript','.ts':'typescript'}[pathSuffix(node?.path)];
  if(!keys(node,'id kind name qualified_name path language start_line end_line source_sha256')
   ||!text(node.id,256)||nodes.has(node.id)||!kinds.has(node.kind)||!text(node.name,512)||!text(node.qualified_name,1024)
   ||!file||node.language!==language||node.source_sha256!==file.source_sha256
   ||!Number.isSafeInteger(node.start_line)||!Number.isSafeInteger(node.end_line)
   ||node.start_line<1||node.end_line<node.start_line||node.end_line>file.lines)fail();
  nodes.set(node.id,node);if(group==='hits')hits.add(node.id);
 }
 const edges=new Set(),used=new Set();
 for(const edge of value.edges){
  if(!keys(edge,'source target kind')||!nodes.has(edge.source)||!nodes.has(edge.target)||!edgeKinds.has(edge.kind)
   ||!(hits.has(edge.source)||hits.has(edge.target)))fail();
  const id=JSON.stringify([edge.source,edge.target,edge.kind]);if(edges.has(id))fail();
  edges.add(id);used.add(edge.source);used.add(edge.target);
 }
 if(value.related_nodes.some(n=>!used.has(n.id)))fail();
 const {context_sha256,...payload}=value;
 if(Buffer.byteLength(JSON.stringify(ordered(value)))>256*1024||structureHash(payload)!==context_sha256)fail();
 return value; // Original-source authenticity is independently checked by Python.
}
function pathSuffix(value){return typeof value==='string'?value.slice(value.lastIndexOf('.')):'';}
