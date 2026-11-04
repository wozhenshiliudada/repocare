import {createHash} from 'node:crypto';
import {readSnapshot} from './snapshot-edits.mjs';
const hash=value=>createHash('sha256').update(value).digest('hex');

export function verifiedRevision(context, request) {
  if (context == null) return null;
  const keys='base_sha,history,issue_sha256,round,schema_version,snapshot_sha256,test_focus';
  const snapshots=request.files.map(file=>[file.path,file.before_sha256,file.editable!==false]);
  if (Object.keys(context).sort().join(',')!==keys || context.schema_version!==1
      || context.issue_sha256!==hash(request.issue) || context.base_sha!==request.base_sha
      || context.snapshot_sha256!==hash(JSON.stringify(snapshots))
      || !Number.isInteger(context.round) || context.round<1
      || !Array.isArray(context.history) || !context.history.length || context.history.length>3
      || context.history.at(-1).plan_sha256!==request.feedback?.previous_plan_sha256
      || context.round!==request.feedback?.round || Buffer.byteLength(JSON.stringify(context))>48000)
    throw new Error('Invalid task-bound revision context');
  return context;
}

export class SnapshotReads {
  constructor() {this.cache=new Map();this.reads=[];this.requests=0;this.repeats=0;this.rereads=0;}
  read(file,params) {
    if (params.reread!==undefined && typeof params.reread!=='boolean') throw new Error('reread must be boolean');
    const value=readSnapshot(file,params); // Always validate authorization/range first.
    const signature=JSON.stringify([value.path,value.before_sha256,value.start_line??null,
      value.end_line??null,params.query??null,
      params.query!==undefined?[params.start_line??1,params.end_line??null]:null,value.content]);
    this.requests++;
    const previous=this.cache.get(signature);
    if (previous) this.repeats++;
    if (params.reread===true) this.rereads++;
    const readId=previous??`read-${this.cache.size+1}`;
    this.cache.set(signature,readId);
    const receipt={path:file.path,before_sha256:file.before_sha256,start_line:value.start_line??null,
      end_line:value.end_line??null,query:params.query??null};
    this.reads=this.reads.filter(item=>JSON.stringify(item)!==JSON.stringify(receipt));
    this.reads.push(receipt);
    this.reads=this.reads.slice(-12);
    if (previous && params.reread!==true) {
      const {content,...metadata}=value;
      return {...metadata,read_id:readId,repeat_read:true,content_omitted:true,
        guidance:'This exact snapshot range/query was already returned in this session. Use its earlier content and analyze the failure, inspect a different range/query, or set reread=true if you need the text again.'};
    }
    return {...value,read_id:readId,repeat_read:Boolean(previous),content_omitted:false};
  }
  stats() {return {read_requests:this.requests,read_repeats:this.repeats,explicit_rereads:this.rereads};}
  summary() {return {reads:this.reads};}
}
