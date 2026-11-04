import './tsx-register.mjs';
import {test} from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
const {ModelReasoningPicker}=await import('../src/ModelReasoningPicker.tsx');
const nodes=(node,check)=>!node||typeof node!=='object'?[]:Array.isArray(node)?node.flatMap(value=>nodes(value,check)):[...(check(node)?[node]:[]),...nodes(node.props?.children??node.children,check)];
const text=node=>node==null||typeof node==='boolean'?'':typeof node!=='object'?String(node):Array.isArray(node)?node.map(text).join(''):text(node.props?.children??node.children);
const model={id:'one',name:'Primary model',model:'api-primary',selectable:true,reasoning_protocol:'openai',reasoning_effort:'high',reasoning_levels:[{level:'max',api_value:'provider-max'},{level:'off',api_value:null},{level:'high',api_value:'provider-high'}]};
const other={...model,id:'two',name:'Other model',model:'api-other'};
function environment(width=390,height=700){
 const previous={document:globalThis.document,window:globalThis.window},events=new Map(),bounds={left:24,top:550,bottom:580};
 const document={body:{nodeType:1},activeElement:null,addEventListener:(type,fn)=>events.set(type,fn),removeEventListener:(type,fn)=>{if(events.get(type)===fn)events.delete(type);}};
 globalThis.document=document;globalThis.window={innerWidth:width,innerHeight:height,addEventListener(){},removeEventListener(){}};
 return {document,events,bounds,restore(){Object.assign(globalThis,previous);}};
}
function component(env,initial={}){
 const hooks=[],internal=React.__CLIENT_INTERNALS_DO_NOT_USE_OR_WARN_USERS_THEY_CANNOT_UPGRADE;let cursor=0,dirty=false,tree,effects=[],props={profiles:[model,other],value:model.id,effort:'high',language:'en',onModelChange:id=>{props={...props,value:id};},onEffortChange:effort=>{props={...props,effort};},...initial};
 const same=(a,b)=>a&&b&&a.length===b.length&&a.every((v,i)=>Object.is(v,b[i]));
 const dispatcher={useId(){const index=cursor++;return hooks[index]??=`picker-${index}`;},useState(initial){const index=cursor++;hooks[index]??={value:typeof initial==='function'?initial():initial};return [hooks[index].value,next=>{const value=typeof next==='function'?next(hooks[index].value):next;if(!Object.is(value,hooks[index].value)){hooks[index].value=value;dirty=true;}}];},useRef(value){const index=cursor++;return hooks[index]??={current:value};},useEffect(fn,deps){const index=cursor++;if(!hooks[index]||!same(hooks[index].deps,deps)){const cleanup=hooks[index]?.cleanup;hooks[index]={deps};effects.push(()=>{cleanup?.();hooks[index].cleanup=fn();});}}};
 function refresh(patch){if(patch)props={...props,...patch};let rounds=0;do{assert.ok(++rounds<30);dirty=false;cursor=0;effects=[];const old=internal.H;internal.H=dispatcher;try{tree=ModelReasoningPicker(props);}finally{internal.H=old;}
  const all=nodes(tree,node=>typeof node.type==='string'),dom=new Map();
  for(const element of all){const ref=element.props.ref,node=ref?.current||{focus(){env.document.activeElement=this;},getBoundingClientRect:()=>env.bounds};node.tagName=element.type.toUpperCase();dom.set(element,node);if(ref)ref.current=node;}
  for(const element of all){const node=dom.get(element),children=nodes(element,item=>dom.has(item)).map(item=>dom.get(item));node.contains=target=>children.includes(target);node.querySelectorAll=()=>nodes(element,item=>['button','input','summary'].includes(item.type)&&!item.props.disabled).map(item=>dom.get(item));node.querySelector=selector=>{const found=nodes(element,item=>item.props?.className===selector.slice(1))[0];return dom.get(found);};}
  for(const fn of effects)fn();
 }while(dirty);return tree;}
 refresh();return {refresh,tree:()=>tree,find:check=>nodes(tree,check),button:label=>nodes(tree,node=>node.type==='button'&&(node.props['aria-label']===label||text(node)===label))[0],trigger:()=>nodes(tree,node=>node.props?.className==='model-picker-trigger')[0],slider:()=>nodes(tree,node=>node.type==='input'&&node.props.type==='range')[0],open(){this.trigger().props.onClick();refresh();},unmount(){for(const hook of hooks)hook?.cleanup?.();}};
}
const key=key=>({key,preventDefault(){this.prevented=true;},stopPropagation(){}});

test('closed picker renders one compact semantic trigger, never a select or a model request',()=>{
 let writes=0;const html=renderToStaticMarkup(React.createElement(ModelReasoningPicker,{profiles:[model],value:model.id,effort:'high',language:'en',onModelChange(){writes++;},onEffortChange(){writes++;},onApply(){writes++;},applyLabel:'Apply'}));
 assert.equal(writes,0);assert.equal((html.match(/<button/g)||[]).length,1);assert.match(html,/aria-haspopup="dialog"/);assert.match(html,/aria-expanded="false"/);assert.doesNotMatch(html,/<select|type="range"|role="dialog"/);
});

test('card uses only configured discrete choices in canonical order without changing API mappings',()=>{
 const env=environment(),before=JSON.stringify(model),changes=[],ui=component(env,{onEffortChange:effort=>changes.push(effort)});
 try{ui.open();const slider=ui.slider();assert.equal(slider.props.min,0);assert.equal(slider.props.max,2);assert.equal(slider.props.step,1);assert.equal(slider.props.value,1);assert.equal(slider.props['aria-valuetext'],'high → provider-high');slider.props.onChange({target:{value:'2'}});assert.deepEqual(changes,['max']);assert.equal(JSON.stringify(model),before);assert.deepEqual(ui.find(node=>node.props?.className==='model-picker-ticks')[0].props.children.map(row=>text(row)),['off','high','max']);}
 finally{ui.unmount();env.restore();}
});

test('slider Arrow keys, Home and End follow configured choices and expose the actual API label',()=>{
 const env=environment(),ui=component(env);
 try{ui.open();for(const [pressed,effort,index,caption] of [['End','max',2,'max → provider-max'],['ArrowLeft','high',1,'high → provider-high'],['Home','off',0,'off → omitted (provider default)'],['ArrowRight','high',1,'high → provider-high']]){const event=key(pressed);ui.slider().props.onKeyDown(event);ui.refresh();assert.equal(event.prevented,true);assert.equal(ui.slider().props.value,index);assert.equal(ui.slider().props['aria-valuetext'],caption);assert.match(text(ui.tree()),new RegExp(effort));}}
 finally{ui.unmount();env.restore();}
});

test('clicking the model opens search and checkmarks; archived and unavailable rows never enter the list',()=>{
 const env=environment(),chosen=[],ui=component(env,{profiles:[model,other,{...model,id:'archived',name:'Retired',archived:true},{...model,id:'bad',name:'Unavailable',selectable:false}],onModelChange:id=>chosen.push(id)});
 try{ui.open();ui.button('Models').props.onClick();ui.refresh();assert.equal(ui.find(node=>node.props?.role==='radio').length,2);assert.equal(ui.find(node=>node.props?.role==='radio'&&node.props['aria-checked'])[0].key,'one');const input=ui.find(node=>node.type==='input'&&node.props.type==='search')[0];assert.equal(input.props.ref.current,env.document.activeElement);input.props.onChange({target:{value:'API-OTHER'}});ui.refresh();const rows=ui.find(node=>node.props?.role==='radio');assert.equal(rows.length,1);assert.match(text(rows[0]),/Other model/);rows[0].props.onClick();ui.refresh();assert.deepEqual(chosen,['two']);assert.ok(ui.slider());assert.doesNotMatch(text(ui.tree()),/Retired|Unavailable/);}
 finally{ui.unmount();env.restore();}
});

test('Escape restores trigger focus, outside click closes, and disabled busy state closes the card',()=>{
 const env=environment(),ui=component(env);
 try{ui.open();env.events.get('keydown')(key('Escape'));ui.refresh();assert.equal(ui.trigger().props['aria-expanded'],false);assert.equal(env.document.activeElement,ui.trigger().props.ref.current);assert.equal(env.events.has('pointerdown'),false);ui.open();env.events.get('pointerdown')({target:{}});ui.refresh();assert.equal(ui.trigger().props['aria-expanded'],false);ui.open();ui.refresh({disabled:true});assert.equal(ui.trigger().props['aria-expanded'],false);assert.equal(ui.trigger().props.disabled,true);}
 finally{ui.unmount();env.restore();}
});

test('Tab wraps within the card and model-list arrows move focus without changing selection',()=>{
 const env=environment(),chosen=[],ui=component(env,{onModelChange:id=>chosen.push(id)});
 try{ui.open();ui.button('Models').props.onClick();ui.refresh();const card=ui.find(node=>node.props?.role==='dialog')[0],controls=card.props.ref.current.querySelectorAll();env.document.activeElement=controls.at(-1);card.props.onKeyDown(key('Tab'));assert.equal(env.document.activeElement,controls[0]);const event=key('ArrowDown');event.target={tagName:'BUTTON'};card.props.onKeyDown(event);assert.equal(env.document.activeElement,controls[1]);assert.deepEqual(chosen,[]);}
 finally{ui.unmount();env.restore();}
});

test('fixed portal card stays within narrow viewports and anchors next to the trigger above or below',()=>{
 for(const width of [240,320,390,800])for(const top of [20,550]){const env=environment(width),ui=component(env);env.bounds.left=width-40;env.bounds.top=top;env.bounds.bottom=top+30;
  try{ui.open();const style=ui.find(node=>node.props?.role==='dialog')[0].props.style;assert.ok(style.left>=8);assert.ok(style.left+style.width<=width-8);assert.ok(style.maxHeight<=420);assert.ok('bottom' in style?style.bottom+style.maxHeight<=700:style.top+style.maxHeight<=700);assert.equal('bottom' in style,top===550);const portal=ui.tree().props.children[1];assert.equal(portal.containerInfo,env.document.body);}
  finally{ui.unmount();env.restore();}
 }
});

test('single-level and unknown protocols cannot invent or submit unsupported thinking levels',()=>{
 const env=environment(),calls=[];let ui;
 try{ui=component(env,{profiles:[{...model,reasoning_protocol:'none',reasoning_effort:'off',reasoning_levels:[{level:'off',api_value:null}]}],effort:'off',onEffortChange:effort=>calls.push(effort)});ui.open();assert.equal(ui.slider().props.disabled,true);ui.slider().props.onKeyDown(key('End'));assert.deepEqual(calls,[]);ui.refresh({profiles:[{...model,reasoning_protocol:'unknown'}]});assert.equal(ui.slider(),undefined);assert.match(text(ui.tree()),/Configure the thinking protocol/);}
 finally{ui?.unmount();env.restore();}
});

test('Apply is explicit and the disabled callback cannot replay a preparation',async()=>{
 const env=environment(),calls=[],ui=component(env,{onApply:()=>calls.push('apply'),applyLabel:'Apply'});
 try{ui.open();assert.deepEqual(calls,[]);await ui.button('Apply').props.onClick();ui.refresh();assert.deepEqual(calls,['apply']);assert.equal(ui.trigger().props['aria-expanded'],false);ui.open();ui.refresh({applyDisabled:true});await ui.button('Apply').props.onClick();assert.deepEqual(calls,['apply']);ui.refresh({onApply:undefined});assert.equal(ui.button('Apply'),undefined);}
 finally{ui.unmount();env.restore();}
});


test('a pending draft has a small visible marker and an accessible trigger description',()=>{
 const env=environment(),ui=component(env,{pending:true});try{const marker=ui.find(node=>node.props?.className==='model-picker-pending')[0];assert.equal(text(marker),'Pending');assert.equal(ui.trigger().props['aria-describedby'],marker.props.id);ui.refresh({pending:false});assert.equal(ui.find(node=>node.props?.className==='model-picker-pending').length,0);assert.equal(ui.trigger().props['aria-describedby'],undefined);}finally{ui.unmount();env.restore();}
});


test('default strength card has one heading, a single-line model label and collapsed session explanation',()=>{
 const env=environment(),notice='Switching creates a linked conversation and keeps the original records. Only sending a message calls the model.',ui=component(env,{notice});
 try{ui.open();const card=ui.find(node=>node.props?.role==='dialog')[0],heading=ui.find(node=>node.props?.className==='model-picker-heading')[0],modelButton=ui.button('Models'),details=ui.find(node=>node.type==='details')[0];assert.equal(text(heading),'Thinking depth · high×');assert.equal(ui.find(node=>node.type==='strong'&&text(node)==='Thinking depth').length,0);assert.equal(modelButton.props.title,model.model);assert.equal(text(modelButton),'Primary model›');assert.equal(nodes(modelButton,node=>node.type==='small').length,0);assert.equal(details.props.open,undefined);assert.equal(text(details.props.children[0]),'Session details');assert.match(text(details),/linked conversation/);assert.equal(ui.find(node=>node.props?.className==='model-picker-hint').length,1);assert.doesNotMatch(text(card),/provider-high/,'API mapping stays in accessible range value rather than a second paragraph');assert.equal(ui.button('Apply'),undefined);}
 finally{ui.unmount();env.restore();}
});
