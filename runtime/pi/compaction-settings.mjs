export function verifiedContextPolicy(v){
 if(v===undefined)return null;
 const fields=['schema_version','enabled','context_window_tokens','trigger_tokens','keep_recent_tokens','summary_max_output_tokens'];
 const fail=()=>{throw new Error('Invalid controlled context policy');};
 if(!v||Object.keys(v).sort().join()!==fields.sort().join()||v.schema_version!==1||typeof v.enabled!=='boolean')fail();
 for(const key of fields.filter(k=>!['schema_version','enabled'].includes(k)))if(!Number.isSafeInteger(v[key]))fail();
 const w=v.context_window_tokens,t=v.trigger_tokens,k=v.keep_recent_tokens,o=v.summary_max_output_tokens;
 if(w<0||t<0||k<0||o<1)fail();
 if(w){if(w<4096||k<128||k>=t||o+1024>=w-t)fail();}
 else if(v.enabled||t||k)fail();
 return v;
}
export const inputUpperEstimate=context=>Buffer.byteLength(JSON.stringify(context))+4096;
