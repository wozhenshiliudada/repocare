import {ui,uiLabels} from './ui-language.mjs';
import React from 'react';
type Data=Record<string,any>;
const labels:Record<string,string>=uiLabels({received:'已登记，等待原流程检查',dispatch_intent:'已进入原流程',
 event_linked:'已关联原检查事件',blocked:'需要处理原许可或环境',superseded:'通知版本已过时',
 failed:'来源核验或检查未完成',interrupted_no_replay:'上次处理中断，保留现场'});
export function GitHubPushStatus({value,navigate}:{value?:Data|null;navigate:(page:string)=>void}){
 if(!value)return <section className="native-card" aria-label={ui("GitHub推送通知")}><h2>{ui("GitHub 推送通知")}</h2><p>{ui("尚未配置主动通知入口。现有提交监控仍按原规则轮询。")}</p><p>{ui("首次接入需要指定已有仓库监控和独立签名密钥；真实联调还需要可达的 HTTPS 接收地址。")}</p></section>;
 const work=Array.isArray(value.work)?value.work:[],deliveries=Array.isArray(value.deliveries)?value.deliveries:[];
 return <section className="native-card" aria-label={ui("GitHub推送通知")}><header><h2>{ui("GitHub 推送通知")}</h2><span>{value.accepting?ui("本机接收入口已开启"):ui("本机接收入口已停止")}</span></header>
  <p>{value.target?.repository} · {value.target?.branch}</p><p>{ui("通知已登记不代表检查通过或修复交付。实际审批、检查和候选结果请查看原监控事件。")}</p>
  <p>{ui("已登记通知 ")}{deliveries.length}{ui(" 条 · 对应提交 ")}{work.length}{ui(" 项")}</p>
  {value.error&&<p className="inline-notice error-text" role="alert">{value.error}</p>}
  {work.slice(-20).reverse().map((row:Data)=><div className="issue-section" key={row.id}>
   <p><code>{String(row.after||'').slice(0,12)}</code> · <strong>{labels[row.status]||ui("记录需要核对")}</strong></p>
   {row.message&&<p>{row.message}</p>}{row.monitor_message&&<p>{ui("原事件：")}{row.monitor_message}</p>}
   <p><strong>{ui("下一步：")}</strong>{row.next_step||ui("核对原监控和通知记录，不自动重放。")}</p>
   {row.event_id&&<p className="caption">{ui("原事件编号：")}{row.event_id}</p>}
  </div>)}
  {deliveries.some((row:Data)=>['blocked_forced','blocked_deleted'].includes(row.receipt?.status))&&<p className="inline-notice">{ui("收到强推或分支删除通知；首版仅保留记录，不据此执行修复。")}</p>}
  <button onClick={()=>navigate('events')}>{ui("查看原监控与待办")}</button>
  <details className="evidence"><summary>{ui("本机接入信息")}</summary><p>{ui("本机地址：")}{value.local_url||ui("尚未启动")}{ui("。GitHub 不能直接访问本机回环地址，真实联调须另行配置 HTTPS 转发。")}</p></details>
 </section>;
}
