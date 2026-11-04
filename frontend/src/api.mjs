import {ui} from './ui-language.mjs';
// A mutation is sent once. A lost response never grants permission to replay it.
export class ApiError extends Error {
  constructor(message, status=0, code='transport_unknown', uncertain=false) {
    super(message); this.name='ApiError'; this.status=status; this.code=code; this.uncertain=uncertain;
  }
}
export async function apiRequest(path, token, body, {fetcher=globalThis.fetch, expire=()=>{}}={}) {
  let response;
  try {
    response=await fetcher(path,{method:body===undefined?'GET':'POST',headers:{Authorization:'Bearer '+token,
      ...(body===undefined?{}:{'Content-Type':'application/json'})},...(body===undefined?{}:{body:JSON.stringify(body)})});
  } catch {
    throw new ApiError(body===undefined?ui("连接中断，请手动刷新。"):ui("未收到操作回执，结果未知。请核对原记录，勿重复提交。"),0,'transport_unknown',body!==undefined);
  }
  let value;
  try { value=await response.json(); } catch {
    throw new ApiError(ui("服务回执无法读取，请核对原操作记录。"),response.status,'invalid_response',body!==undefined);
  }
  if(!value||typeof value!=='object'||Array.isArray(value))throw new ApiError(ui("服务回执结构异常，请核对原记录。"),response.status,'invalid_response',body!==undefined);
  if(response.status===401&&!value.code)expire();
  if(!response.ok)throw new ApiError(value.error||ui("请求未完成"),response.status,value.code||'request_rejected',body!==undefined&&response.status>=500);
  return value;
}
