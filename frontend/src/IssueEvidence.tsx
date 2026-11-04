import {ui} from './ui-language.mjs';
import React from 'react';
import {Evidence,Badge} from './WorkbenchPages';
import {issueEvidence} from './operation-model.mjs';
export function IssueEvidence({state,number}:{state:any;number:number}){
 const value=issueEvidence(state,number),snapshot=value.snapshot;
 return <div className="issue-evidence">
  {snapshot&&<section className="native-card"><header><h3>{ui("可信准入与Issue版本")}</h3><Badge value={snapshot.status}/></header><h4>{snapshot.issue?.title}</h4><p className="issue-body">{snapshot.issue?.body}</p><Evidence value={snapshot} title={ui("标签、操作者、目标提交与原始准入依据")}/></section>}
  {value.investigation&&<section className="native-card"><header><h3>{ui("调查证据")}</h3><Badge value={value.historical?'stale':value.investigation.status}/></header>{value.historical&&<p className="inline-notice">{ui("此报告属于历史版本，仅供核对，不能替代当前执行前的版本核验。")}</p>}{value.report?.report?<><p>{ui("固定目标：")}<code>{value.report.snapshot?.head_sha}</code></p><Evidence value={value.report.report} title={ui("Git差异、搜索命中、候选理由、出处及partial范围")}/></>:<p className="muted">{ui("暂无可核验的完整调查报告。不会为读取而自动重新调查。")}</p>}</section>}
  {value.prepared&&<section className="native-card"><header><h3>{ui("目标环境与许可证据")}</h3></header>{value.environment?<><Evidence value={value.environment.environment} title={ui("环境发现、依赖来源、检查与阻塞原因")}/><Evidence value={value.environment.grant} title={ui("本次原环境许可")}/><Evidence value={value.environment.binding} title={ui("环境、范围及来源指纹绑定")}/></>:<p className="muted">{ui("已有范围记录，但尚无匹配的完整环境回执；不会自动重新准备。")}</p>}</section>}
 </div>;
}
