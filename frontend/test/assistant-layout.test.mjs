// Static grid/cascade checks, not a substitute for the production browser audit.
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import postcss from 'postcss';

const sources=['../src/style.css','../src/workspace.css','../src/assistant.css'].map((file,index)=>
 readFileSync(index===2&&process.env.PI_UI_LAYOUT_ASSISTANT_CSS?process.env.PI_UI_LAYOUT_ASSISTANT_CSS:new URL(file,import.meta.url),'utf8'));
const rules=sources.flatMap(source=>{const result=[];postcss.parse(source).walkRules(rule=>result.push(rule));return result;});
const element=(classes,parent=null)=>({classes:classes.split(' '),parent});
function matches(node,selector){
 const parts=selector.trim().replaceAll('>',' > ').split(/\s+/);let cursor=parts.length-1,current=node;
 const simple=(item,part)=>item&&/^([.][\w-]+)+$/.test(part)&&part.slice(1).split('.').every(name=>item.classes.includes(name));
 if(!simple(current,parts[cursor--]))return false;
 while(cursor>=0){
  if(parts[cursor]==='>'){cursor--;current=current.parent;if(!simple(current,parts[cursor--]))return false;}
  else{const part=parts[cursor--];do{current=current.parent;}while(current&&!simple(current,part));if(!current)return false;}
 }
 return true;
}
function active(rule,width){
 for(let parent=rule.parent;parent;parent=parent.parent)if(parent.type==='atrule'){
  if(parent.name!=='media')return false;
  const bounds=[...parent.params.matchAll(/\((min|max)-width:\s*([\d.]+)px\)/g)];
  if(!bounds.length||bounds.some(([,side,limit])=>side==='min'?width<Number(limit):width>Number(limit)))return false;
 }
 return true;
}
function style(node,width){
 const values={},priorities={};let order=0;
 for(const rule of rules){
  order++;if(!active(rule,width))continue;
  const selectors=rule.selectors.filter(selector=>matches(node,selector));if(!selectors.length)continue;
  const specificity=Math.max(...selectors.map(selector=>(selector.match(/[.][\w-]+/g)||[]).length));
  for(const declaration of rule.nodes)if(declaration.type==='decl'){
   const priority=(declaration.important?1000000:0)+specificity*10000+order;
   if(priority>=(priorities[declaration.prop]??-1)){priorities[declaration.prop]=priority;values[declaration.prop]=declaration.value;}
  }
 }
 return values;
}
const tracks=value=>value.match(/minmax\([^)]*\)|[^\s]+/g);
function range(value,count){
 const [first,last]=value.split('/').map(part=>part.trim()),line=value=>Number(value)<0?count+2+Number(value):Number(value),start=line(first);
 return [start,last?.startsWith('span ')?start+Number(last.slice(5)):last?line(last):start+1];
}
function grid(width,flags){
 const root=element('app '+flags),rootStyle=style(root,width),columns=tracks(rootStyle['grid-template-columns']);
 const panes=['workspace',...(flags.includes('has-inspector')?['inspector']:[]),...(flags.includes('has-assistant')?['assistant-dock']:[]),'rail','statusbar'];
 return {rootStyle,columns,panes:panes.map(name=>{
  const value=style(element(name,root),width);
  return {name,value,column:range(value['grid-column'],columns.length),row:range(value['grid-row'],tracks(rootStyle['grid-template-rows']).length)};
 })};
}
const intersects=(a,b)=>a[0]<b[1]&&b[0]<a[1];
function assertSeparate(layout){
 for(const pane of layout.panes)assert.ok(!['absolute','fixed'].includes(pane.value.position),`${pane.name} must participate in the grid`);
 for(let first=0;first<layout.panes.length;first++)for(let next=first+1;next<layout.panes.length;next++){
  const a=layout.panes[first],b=layout.panes[next];
  assert.ok(!(intersects(a.column,b.column)&&intersects(a.row,b.row)),`${a.name} and ${b.name} share a grid cell`);
 }
}
function minimum(track,height){
 const value=track.startsWith('minmax(')?track.slice(7).split(',')[0]:track;
 return value.endsWith('px')?parseFloat(value):value.endsWith('dvh')?parseFloat(value)*height/100:0;
}

for(const width of [320,759,760,800,1099,1100,1280,1439,1440,1920]){
 test(`${width}px: open assistant and repository inspector occupy separate grid areas`,()=>{
  const layout=grid(width,'has-assistant has-inspector');assertSeparate(layout);
  assert.ok(layout.columns.reduce((sum,track)=>sum+minimum(track,900),0)<=width,'grid minimum widths exceed the viewport');
  const assistant=layout.panes.find(pane=>pane.name==='assistant-dock'),inspector=layout.panes.find(pane=>pane.name==='inspector');
  if(width>=1440){assert.equal(assistant.row[0],1);assert.equal(inspector.row[0],1);assert.ok(assistant.column[0]>inspector.column[0]);}
  if(width<1100){
   assert.ok(inspector.row[0]>1);assert.ok(assistant.row[0]>inspector.row[0]);
   assert.equal(layout.rootStyle.height,'auto');assert.equal(layout.rootStyle.overflow,'visible');
   assert.ok(tracks(layout.rootStyle['grid-template-rows']).reduce((sum,track)=>sum+minimum(track,900),0)>900,'fixture must exercise the page-scrolling case');
  }
 });
 test(`${width}px: assistant-only layout retains independent workspace and utility rail`,()=>assertSeparate(grid(width,'has-assistant')));
}
