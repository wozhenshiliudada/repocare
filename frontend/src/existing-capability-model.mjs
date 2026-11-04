import {ui,uiLabels} from './ui-language.mjs';
import {capabilityRoute} from './capability-routing.mjs';
const labels=uiLabels({
 'recent-projects':'已接项目列表与原检查入口',
 'large-repository':'已接分页浏览与范围连接',
 dashboard:'已接离线 HTML 导出',
 comparison:'已接已有摘要导出',
});
export function connectionLabel(workflow){
 return labels[workflow.id]||(capabilityRoute(workflow.id).label||ui("尚无专用执行接口"));
}
export const isExistingWorkflow=id=>Object.hasOwn(labels,id);
export function reportInputs(value){return value.split(/\r?\n/).map(s=>s.trim()).filter(Boolean);}
export async function downloadArtifact(artifact,token,{fetcher=globalThis.fetch,save=saveBlob}={}){
 if(!/^\/api\/exports\/[a-f0-9]{32}\/(dashboard\.html|comparison\.(json|md)|tool-result\.json|tool-report\.md)$/.test(artifact.url)
    ||artifact.url.split('/').at(-1)!==artifact.name)throw Error(ui("导出下载引用无效"));
 const response=await fetcher(artifact.url,{headers:{Authorization:'Bearer '+token}});
 if(!response.ok)throw Error(response.status===401?ui("会话已失效，请重新打开本次启动链接。"):ui("导出文件不可用，请核对原记录。"));
 const blob=await response.blob();
 if(blob.size!==artifact.bytes)throw Error(ui("下载内容与导出记录不一致"));
 await save(blob,artifact.name);
}
function saveBlob(blob,name){
 const url=URL.createObjectURL(blob),link=document.createElement('a');
 link.href=url;link.download=name;document.body.append(link);link.click();link.remove();
 setTimeout(()=>URL.revokeObjectURL(url),1000);
}
