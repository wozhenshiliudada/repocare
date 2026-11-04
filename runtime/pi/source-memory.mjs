import {createHash} from 'node:crypto';
import {verifiedRelations} from './decision-context.mjs';

const hash=value=>createHash('sha256').update(value).digest('hex');
const ordered=value=>Array.isArray(value)?value.map(ordered):value&&typeof value==='object'
  ?Object.fromEntries(Object.keys(value).sort().map(key=>[key,ordered(value[key])])):value;
const keys=(value,names)=>value&&typeof value==='object'&&!Array.isArray(value)
  &&Object.keys(value).sort().join(',')===names.split(' ').sort().join(',');
const hex=value=>typeof value==='string'&&/^[0-9a-f]{64}$/.test(value);
const id=value=>typeof value==='string'&&/^[a-z0-9][a-z0-9_-]{0,63}$/.test(value);

export function verifiedMemory(value,issue){
  if(value===undefined)return undefined;
  if(value?.schema_version===3){
    if(!keys(value,'schema_version source_schema_version index_sha256 query_sha256 hits decision_relations context_sha256')
      ||![1,2].includes(value.source_schema_version)||!hex(value.context_sha256)
      ||Buffer.byteLength(JSON.stringify(value))>256*1024)throw new Error('Invalid relation memory envelope');
    const {context_sha256,...data}=value;
    if(hash(JSON.stringify(ordered(data)))!==context_sha256)throw new Error('Relation memory fingerprint mismatch');
    const base={schema_version:value.source_schema_version,index_sha256:value.index_sha256,
      query_sha256:value.query_sha256,hits:value.hits};
    verifiedMemory({...base,context_sha256:hash(JSON.stringify(ordered(base)))},issue);
    verifiedRelations(value.decision_relations,value.hits,value.source_schema_version);
    return value;
  }
  if(!keys(value,'schema_version index_sha256 query_sha256 hits context_sha256')||![1,2].includes(value.schema_version)
    ||!hex(value.index_sha256)||!hex(value.query_sha256)||!hex(value.context_sha256)
    ||value.query_sha256!==hash(issue)||!Array.isArray(value.hits)||value.hits.length>5)
    throw new Error('Invalid source memory envelope');
  const {context_sha256,...data}=value;
  if(hash(JSON.stringify(ordered(data)))!==context_sha256)throw new Error('Source memory fingerprint mismatch');
  const seen=new Set();
  for(const row of value.hits){
    if(!keys(row,'id topic path start_line end_line kind classification source_sha256 excerpt_sha256 classification_origin excerpt'+(value.schema_version===2?' origin':''))
      ||!id(row.id)||seen.has(row.id)||!id(row.topic)||!hex(row.source_sha256)||!hex(row.excerpt_sha256)
      ||!Number.isInteger(row.start_line)||!Number.isInteger(row.end_line)||row.start_line<1
      ||row.end_line<row.start_line||row.end_line-row.start_line>=100
      ||typeof row.path!=='string'||row.path.length>512||!row.path.endsWith('.md')
      ||row.path.split('/').some(p=>!p||p==='.'||p==='..'||p.toLowerCase()==='.git'
        ||/^\.env(?:\.|$)|^(?:credentials|secrets)|\.(?:key|pem)$/i.test(p))||/[\\:\0]/.test(row.path)
      ||typeof row.excerpt!=='string'||!row.excerpt||Buffer.byteLength(row.excerpt)>6000
      ||hash(row.excerpt)!==row.excerpt_sha256)
      throw new Error('Invalid source memory citation');
    const decision=['confirmed','proposed','pending','superseded'].includes(row.classification);
    if(!(decision&&row.kind==='document'&&row.classification_origin==='source_marker'
      ||row.classification==='reference'&&row.kind==='document'&&row.classification_origin==='curated_reference'
      ||row.classification==='historical'&&row.kind==='case'&&row.classification_origin==='historical_report'))
      throw new Error('Invalid source memory classification');
    if(value.schema_version===2){
      const origin=row.origin;
      if(!keys(origin,'member_id entry_id path catalog_sha256 sources_sha256 kind run_id')
        ||!id(origin.member_id)||!id(origin.entry_id)||origin.path!==row.path
        ||!hex(origin.catalog_sha256)||!hex(origin.sources_sha256)
        ||!(origin.kind==='catalog'&&origin.run_id===null
          ||origin.kind==='report_case'&&row.kind==='case'&&row.classification==='historical'
            &&typeof origin.run_id==='string'&&/^[0-9a-f]{32}$/.test(origin.run_id)))
        throw new Error('Invalid collection memory origin');
    }
    seen.add(row.id);
  }
  return value;
}
