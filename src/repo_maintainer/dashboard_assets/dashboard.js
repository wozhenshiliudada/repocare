"use strict";
(() => {
  const data = JSON.parse(document.getElementById("evidence").textContent);
  const runs = data.runs;
  let selected = runs[0]?.run_id, tab = "overview", sideBySide = true;
  const labels = {candidate_ready:"候选待审阅",awaiting_approval:"等待批准",rejected:"已拒绝",rolled_back:"已回退",discarded:"已清理",integrated:"已发布分支",prepared:"已封装待批准",published:"已发布分支",preparing:"正在准备",publishing:"发布待恢复",approve:"人工批准",policy_scope:"自主授权",pending:"待批准",reject:"拒绝",passed:"测试通过",failed:"测试失败"};
  const label = v => labels[v] || v || "未记录";
  Object.assign(labels,{aborted:"准备已中止",recovering:"正在恢复",cleanup_required:"清理待核验",integration_preparation_aborted:"准备恢复 / 已中止"});
  const el = (tag, text, cls) => { const n=document.createElement(tag); if(text!==undefined)n.textContent=String(text); if(cls)n.className=cls; return n; };
  const append = (parent, ...children) => { parent.append(...children); return parent; };
  const pill = (text, tone="") => el("span",text,"pill "+tone);
  const pretty = value => JSON.stringify(value,null,2);
  const hash = value => el("code",value || "尚未生成","hash");
  function field(title,value){return append(el("div",undefined,"field"),el("small",title),el("code",value ?? "未记录"));}
  function panel(title){const n=el("section",undefined,"panel"); if(title)n.append(el("h3",title)); return n;}
  function testList(values, title){
    const box=panel(title);
    if(!values?.length){box.append(el("p","未执行 / 未记录","muted"));return box;}
    for(const t of values){
      const ok=t.returncode===0 && t.timed_out===false;
      box.append(append(el("div",undefined,"test-summary"),pill(ok?"通过":"失败 / 未知",ok?"":"bad"),el("code","exit "+t.returncode+(t.timed_out?" · 超时":"")+" · "+(t.duration_seconds??"未计量")+" s")));
      const d=el("details",undefined,"test-output");
      append(d,el("summary",pretty(t.command)),el("pre",(t.stdout||"")+"\n"+(t.stderr||"")));if(t.output_truncated)d.append(el("p","原记录输出已截断。","muted"));box.append(d);
    }return box;
  }
  function renderDiff(diff){
    const container=el("div",undefined,"diff-view");
    if(!diff){container.append(el("p","未记录差异。","muted"));return container;}
    if(diff.length>300000){container.append(el("p","差异较大，使用文本查看。","muted"),el("pre",diff));return container;}
    try {
      const template=document.createElement("template");
      template.innerHTML=Diff2Html.html(diff,{drawFileList:false,outputFormat:sideBySide?"side-by-side":"line-by-line",matching:"none",diffMaxChanges:3000,diffMaxLineLength:10000});
      // Defense in depth around a third-party HTML renderer: inert parse, allowlist.
      const allowed=new Set(["DIV","SPAN","TABLE","TBODY","THEAD","TR","TD","TH","PRE","CODE","INS","DEL","A"]);
      for(const node of [...template.content.querySelectorAll("*")]){
        if(!allowed.has(node.tagName)){node.remove();continue;}
        for(const attr of [...node.attributes])if(!["class","id","title","colspan","rowspan"].includes(attr.name))node.removeAttribute(attr.name);
      }
      container.append(template.content);
    }catch {container.append(el("pre",diff));}
    return container;
  }
  function approvalBlock(title,fingerprint,authorization,matches){
    const box=el("div",undefined,"approval-block");box.append(el("h3",title),hash(fingerprint));
    box.append(pill(label(authorization?.decision)),pill(matches===true?"快照指纹一致":matches===false?"快照指纹不一致":"未核验",matches===false?"bad":"warn"));
    if(authorization?.actor)box.append(el("p","记录操作者："+authorization.actor));
    return box;
  }
  function renderList(){
    const search=document.getElementById("search").value.toLowerCase(), filter=document.getElementById("filter").value;
    const list=document.getElementById("run-list");list.replaceChildren();
    const visible=runs.filter(r=>(filter==="all"||r.status===filter)&&(r.run_id+" "+r.issue).toLowerCase().includes(search));
    if(!visible.some(r=>r.run_id===selected))selected=visible[0]?.run_id;
    for(const r of visible){
      const b=append(el("button",undefined,"run-button"+(r.run_id===selected?" active":"")),el("strong",r.issue||r.run_id),el("small",label(r.status)+" · "+r.run_id.slice(0,8)));
      b.setAttribute("aria-pressed",r.run_id===selected?"true":"false");b.addEventListener("click",()=>{selected=r.run_id;renderList();});list.append(b);
    }
    if(!visible.length)list.append(el("p","无匹配运行。","muted"));renderDetail();
  }
  function renderDetail(){
    const detail=document.getElementById("detail");detail.replaceChildren();
    const r=runs.find(r=>r.run_id===selected);if(!r){detail.append(el("div","请选择运行档案。","empty"));return;}
    const head=panel();
    head.append(append(el("div",undefined,"panel-top"),append(el("div"),el("h2",r.issue||"维护任务"),el("div",r.run_id,"id")),pill(label(r.status))));
    head.append(append(el("div",undefined,"tags"),pill(r.mode==="auto"?"自主修改":r.mode==="approval"?"人工审批":"模式未记录"),pill("源工作区记录："+(r.source_unchanged===true?"不变":r.source_unchanged===false?"有变化":"未核验"),r.source_unchanged===true?"":"bad"),pill(r.review?"发布："+label(r.review.status):"发布尚未准备","warn")));
    head.append(append(el("div",undefined,"key-grid"),field("基线提交",r.base_sha),field("源仓库（历史路径）",r.source_repo),field("候选分支",r.branch),field("最终记录时间",r.recorded_at)));
    detail.append(head);
    const nav=el("nav",undefined,"tabs");nav.setAttribute("aria-label","证据视图");
    for(const [key,title] of [["overview","测试与过程"],["diff","逐轮差异"],["approval","双层审批"],["audit","审计与回退"]]){
      const b=el("button",title,tab===key?"active":"");b.setAttribute("aria-pressed",tab===key?"true":"false");b.addEventListener("click",()=>{tab=key;renderDetail();});nav.append(b);
    }detail.append(nav);
    const content=el("div",undefined,"tab-panel");content.dataset.tab=tab;detail.append(content);
    if(tab==="overview"){
      content.append(append(el("div",undefined,"two-cols"),testList(r.baseline_tests,"01 / 基线测试"),testList(r.candidate_tests,"02 / 最后候选测试")));
      const p=panel("修复轮次");
      for(const round of r.rounds){
        const block=el("div",undefined,"round");append(block,el("h3","第 "+round.number+" 轮"),pill(label(round.authorization?.decision)),pill(label(round.verification)),el("p",round.plan.diagnosis||"诊断未记录"),el("p","修改文件："+round.paths.join("、")),el("p","模型来源："+(round.provider_evidence.transport||round.plan.provider||"未记录")+" · 请求数："+(round.provider_evidence.requests??"未计量")+" · 费用未在此核验","muted"));
        block.append(testList(round.tests,"该轮测试"));p.append(block);
      }if(!r.rounds.length)p.append(el("p","尚无修复轮次记录。"));content.append(p);
      if(r.review){
        content.append(testList(r.review.tests,"03 / 不可变提交重测"));
        if(["aborted","preparing","recovering","cleanup_required","failed"].includes(r.review.status))content.append(el("p","准备未完成或已中止；已记录的重测结果不能用于发布，重新准备须重跑全部测试。","notice"));
      }
    }else if(tab==="diff"){
      const p=panel("差异审阅 / diff2html");
      for(const [side,text] of [[true,"并排"],[false,"逐行"]]){const b=el("button",text,"layout-button"+(side===sideBySide?" active":""));b.addEventListener("click",()=>{sideBySide=side;renderDetail();});p.append(b);}
      for(const round of r.rounds){p.append(el("h3","第 "+round.number+" 轮 · "+label(round.verification)),renderDiff(round.diff));}
      if(!r.rounds.length)p.append(renderDiff(r.diff));
      if(r.review?.diff)p.append(el("h3","封装提交差异"),renderDiff(r.review.diff));content.append(p);
    }else if(tab==="approval"){
      const p=panel("编辑授权与发布授权");p.append(el("p","两种指纹对应不同动作。快照一致不能证明当前候选仍可发布。"));
      for(const round of r.rounds)p.append(approvalBlock("编辑 / 第 "+round.number+" 轮",round.plan_sha256,round.authorization,round.plan_hash_matches));
      if(!r.rounds.length)p.append(approvalBlock("编辑方案",r.plan_sha256,r.authorization,null));
      if(r.review){
        const v=r.review;p.append(approvalBlock("独立发布 / "+label(v.status),v.review_sha256,v.authorization,v.hash_matches));
        p.append(append(el("div",undefined,"key-grid"),field("不可变提交",v.core.commit),field("目标新分支",v.core.target_branch),field("Tree",v.core.tree),field("diff 指纹一致",v.diff_hash_matches===true?"是":v.diff_hash_matches===false?"否":"未核验")));
        if(v.hash_matches===false||v.diff_hash_matches===false)p.append(el("p","证据指纹不一致，请核查原始记录；本页面无法授权。","notice"));
      }else p.append(el("p","尚未准备独立发布审批。成功候选也不等于批准发布。","notice"));
      p.append(el("p","实际发布须先审阅原始 integration-review.json / integration.diff，再通过 CLI 提交当前完整指纹。CLI 会核验 Git 状态；页面不提供执行按钮。","actions-note"));content.append(p);
    }else{
      const p=panel("状态时间线");const timeline=el("ol",undefined,"timeline");
      for(const event of r.events)timeline.append(append(el("li"),el("time",event.time||"时间未记录"),el("strong",label(event.stage)),el("p",event.reason||event.error||"")));p.append(timeline);content.append(p);
      const recovery=panel("回退 / 清理 / 发布记录");recovery.append(el("pre",pretty({status:r.status,recovery:r.recovery,integration_preparation:r.integration_preparation,preparation_stage:r.review?.operation_stage,preparation_cleanup_error:r.review?.cleanup_error,approval_outcome:r.approval_outcome,integration:r.integration,integration_branch:r.integration_branch,integration_commit:r.integration_commit,review_cleanup:r.review?.cleanup,error:r.error})));
      recovery.append(el("p","已清理不等于修复失败；发布分支可能保留。请结合逐轮测试和审批记录判断。","muted"));content.append(recovery);
      const provenance=panel("证据来源");provenance.append(field("读取时 run.json 的 SHA256",r.record_sha256),el("p","仅为文件摘要和历史内容展示，未验证操作者身份或当前 refs。"),el("pre",pretty(r.policy)));content.append(provenance);
    }
  }
  document.getElementById("generated").textContent=data.generated_at;
  document.getElementById("run-count").textContent=runs.length;
  const metrics=document.getElementById("metrics");
  const green=runs.filter(r=>r.candidate_tests?.length&&r.candidate_tests.every(t=>t.returncode===0&&t.timed_out===false)).length;
  for(const [title,value,note] of [["运行记录",runs.length,"仅选定的本地档案"],["候选测试全通过",green,"不是模型通用成功率"],["回退记录",runs.filter(r=>r.status==="rolled_back").length,"查看失败与清理证据"],["发布记录",runs.filter(r=>r.review?.status==="published").length,"历史记录 / 未实时核验"]]){
    metrics.append(append(el("div",undefined,"metric"),el("small",title),el("strong",value),el("span",note)));
  }
  document.getElementById("search").addEventListener("input",renderList);
  document.getElementById("filter").addEventListener("change",renderList);
  renderList();
})();
