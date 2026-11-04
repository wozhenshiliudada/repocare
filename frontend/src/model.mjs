import {ui} from './ui-language.mjs';
// Read-only projection: identifiers come from original records, never create task authority.
export function taskRows(state) {
  const rows=new Map(), cards=state?.recovery_cards||[], plans=state?.plans||[], runs=state?.runs||[];
  const key=(kind,id)=>cards.find(c=>c.kind===kind&&c.id===id)?.task?.id||kind+':'+id;
  for(const plan of plans){
    const id=key('plan',plan.id);
    const row=rows.get(id)||{id,title:plan.plan?.issue||ui("维护方案"),plans:[],runs:[],selection:{kind:'plan',id:plan.id}};
    row.plans.push(plan);row.status=plan.status;row.selection={kind:'plan',id:plan.id};rows.set(id,row);
  }
  for(const run of runs){
    const linked=plans.find(p=>p.run_id===run.run_id);
    const id=linked?key('plan',linked.id):key('run',run.run_id);
    const row=rows.get(id)||{id,title:run.issue||run.plan?.issue||ui("维护运行"),plans:[],runs:[]};
    row.runs.push(run);row.status=run.status;row.selection={kind:'run',id:run.run_id};rows.set(id,row);
  }
  for(const c of cards.filter(c=>c.kind==='task')){
    const id=c.task?.id||'task:'+c.id;
    if(!rows.has(id))rows.set(id,{id,title:ui("未完成任务登记"),plans:[],runs:[],status:c.status||'interrupted',selection:{kind:'task',id:c.id}});
  }
  for(const execution of state?.issue_workbench?.executions||[]){
    const id=execution.task_id||'issue:'+execution.id;
    const job=(state.issue_workbench.investigations||[]).find(j=>j.id===execution.investigation_id);
    rows.set(id,{id,title:'#'+execution.issue_number+' '+(job?.snapshot?.issue?.title||ui("Issue 维护")),
      plans:[],runs:[],status:execution.status,
      selection:{kind:'issue',id:execution.id,number:execution.issue_number}});
  }
  return [...rows.values()].reverse();
}

// A completed operation identifies its own original plan/run; list order is not identity.
export function operationTask(state, result) {
  const rows=taskRows(state);
  if(result?.plan_id)return rows.find(row=>row.plans.some(plan=>plan.id===result.plan_id))||null;
  if(result?.run_id)return rows.find(row=>row.runs.some(run=>run.run_id===result.run_id))||null;
  return null;
}

export function graphLanes(commits) {
  const lanes=[], result=[];
  for(const commit of commits){
    let lane=lanes.indexOf(commit.sha);
    if(lane<0){lane=lanes.indexOf(null);if(lane<0)lane=lanes.length;lanes[lane]=commit.sha;}
    const before=[...lanes];lanes[lane]=null;
    const edges=[];
    for(const [index,parent] of commit.parents.entries()){
      let target=lanes.indexOf(parent);
      if(target<0){target=index===0&&lanes[lane]===null?lane:lanes.indexOf(null);if(target<0)target=lanes.length;lanes[target]=parent;}
      edges.push({parent,from:lane,to:target});
    }
    result.push({...commit,lane,before,after:[...lanes],edges});
  }
  return result;
}

export function loadPreferences(storage) {
  try{const value=JSON.parse(storage.getItem('repocare.ui.v1')||'null');
    if(value?.version===1&&['light','dark','system'].includes(value.theme)&&[13,15,17].includes(value.fontSize))
      return {version:1,page:['overview','tasks','issues','events','memory','delivery','configuration','service'].includes(value.page)?value.page:'overview',theme:value.theme,fontSize:value.fontSize,repository:typeof value.repository==='string'?value.repository:null,task:typeof value.task==='string'?value.task:null,panel:['code','issues','pulls','actions',null].includes(value.panel)?value.panel:null};
  }catch{}
  return {version:1,theme:'light',fontSize:15,repository:null,task:null,panel:null};
}

export function fileTree(paths) {
  const root=new Map();
  for(const path of paths){
    const parts=path.split('/');let children=root;
    parts.forEach((name,index)=>{let entry=children.get(name);
      if(!entry){entry={name,path:parts.slice(0,index+1).join('/'),children:new Map()};children.set(name,entry);}
      if(index===parts.length-1)entry.file=true;
      children=entry.children;
    });
  }
  return root;
}
