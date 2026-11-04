import React,{useEffect,useId,useRef,useState} from 'react';
import {createPortal} from 'react-dom';
import {CANONICAL_REASONING_LEVELS,reasoningChoices,reasoningCaption} from './model-reasoning.mjs';
import {modelPickerCopy} from './model-picker-copy.mjs';
import './model-picker.css';

type Profile=Record<string,any>;
export type ModelReasoningPickerProps={profiles:Profile[];value:string;effort:string;defaultId?:string;disabled?:boolean;pending?:boolean;language?:string;label?:string;onModelChange:(id:string)=>void;onEffortChange:(effort:string)=>void;onApply?:()=>void|Promise<void>;applyLabel?:string;applyDisabled?:boolean;notice?:string};

/** A single compact trigger; opening and editing the card never prepares a session. */
export function ModelReasoningPicker({profiles,value,effort,defaultId,disabled=false,pending=false,language='zh-CN',label,onModelChange,onEffortChange,onApply,applyLabel,applyDisabled=false,notice}:ModelReasoningPickerProps){
 const copy=modelPickerCopy[language==='en'?'en':'zh-CN'],id=useId();
 const [open,setOpen]=useState(false),[view,setView]=useState<'models'|'depth'>('depth'),[query,setQuery]=useState(''),[position,setPosition]=useState<React.CSSProperties>({left:8,top:8,width:300,maxHeight:400});
 const trigger=useRef<HTMLButtonElement>(null),card=useRef<HTMLDivElement>(null),search=useRef<HTMLInputElement>(null),restore=useRef(false);
 const selected=profiles.find(profile=>profile.id===value),choices=reasoningChoices(selected).sort((a,b)=>CANONICAL_REASONING_LEVELS.indexOf(a.level)-CANONICAL_REASONING_LEVELS.indexOf(b.level));
 const rows=profiles.filter(profile=>profile.selectable&&!profile.archived&&`${profile.name} ${profile.model}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()));
 const caption=choices.find(row=>row.level===effort);
 function close(focus=false){restore.current=focus;setOpen(false);}
 useEffect(()=>{if(disabled)close();},[disabled]);
 useEffect(()=>{
  if(!open){if(restore.current){trigger.current?.focus();restore.current=false;}return;}
  function place(){const bounds=trigger.current?.getBoundingClientRect();if(!bounds)return;const gap=8,width=Math.min(320,window.innerWidth-gap*2),above=bounds.top-gap,below=window.innerHeight-bounds.bottom-gap,useAbove=above>below;
   const maxHeight=Math.max(0,Math.min(420,(useAbove?above:below)-gap));
   setPosition({left:Math.max(gap,Math.min(bounds.left,window.innerWidth-width-gap)),...(useAbove?{bottom:window.innerHeight-bounds.top+gap}:{top:bounds.bottom+gap}),width,maxHeight});}
  function outside(event:Event){const target=event.target as Node;if(!card.current?.contains(target)&&!trigger.current?.contains(target))close();}
  function key(event:KeyboardEvent){if(event.key==='Escape'){event.preventDefault();event.stopPropagation();close(true);}}
  place();document.addEventListener('pointerdown',outside);document.addEventListener('keydown',key);window.addEventListener('resize',place);window.addEventListener('scroll',place,true);
  if(view==='models')search.current?.focus();else card.current?.querySelector<HTMLButtonElement>('.model-picker-model')?.focus();
  return()=>{document.removeEventListener('pointerdown',outside);document.removeEventListener('keydown',key);window.removeEventListener('resize',place);window.removeEventListener('scroll',place,true);};
 },[open,view]);
 function navigate(event:React.KeyboardEvent<HTMLDivElement>){
  if(event.key==='Escape'){event.preventDefault();event.stopPropagation();close(true);return;}
  const controls=Array.from(card.current?.querySelectorAll<HTMLElement>('button:not(:disabled),input:not(:disabled),summary')||[]);if(!controls.length)return;
  const index=controls.indexOf(document.activeElement as HTMLElement);
  if(event.key==='Tab'){event.preventDefault();controls[(index+(event.shiftKey?-1:1)+controls.length)%controls.length].focus();}
  else if(['ArrowDown','ArrowUp','Home','End'].includes(event.key)&&(event.target as HTMLElement).tagName!=='INPUT'){
   event.preventDefault();controls[event.key==='Home'?0:event.key==='End'?controls.length-1:(index+(event.key==='ArrowUp'?-1:1)+controls.length)%controls.length].focus();
  }
 }
 function changeEffort(index:number){const row=choices[index];if(!disabled&&selected?.selectable&&!selected?.archived&&row)onEffortChange(row.level);}
 function slideKey(event:React.KeyboardEvent<HTMLInputElement>){
  const index=Math.max(0,choices.findIndex(row=>row.level===effort));
  const next=event.key==='Home'?0:event.key==='End'?choices.length-1:['ArrowLeft','ArrowDown'].includes(event.key)?Math.max(0,index-1):['ArrowRight','ArrowUp'].includes(event.key)?Math.min(choices.length-1,index+1):null;
  if(next!==null){event.preventDefault();event.stopPropagation();if(choices.length>1)changeEffort(next);}
 }
 const panel=open&&typeof document!=='undefined'?<div ref={card} id={id} className="model-picker-card" role="dialog" aria-label={label||copy.choose} style={position} onKeyDown={navigate}>
  <div className="model-picker-heading"><strong>{view==='models'?copy.models:caption?copy.depth+' · '+effort:copy.unconfigured}</strong><button type="button" className="model-picker-close" aria-label={copy.close} onClick={()=>close(true)}>×</button></div>
  {view==='models'?<><input ref={search} className="model-picker-search" type="search" aria-label={copy.search} placeholder={copy.search} value={query} onChange={event=>setQuery(event.target.value)}/>
   <div className="model-picker-options" role="radiogroup" aria-label={copy.models}>{rows.map(profile=><button type="button" role="radio" aria-checked={profile.id===value} className="model-picker-option" key={profile.id} onClick={()=>{if(!disabled){onModelChange(profile.id);setView('depth');}}}><span><strong>{profile.name||profile.model}</strong><small>{profile.model}{profile.id===defaultId?' · '+copy.default:''}</small></span><span aria-hidden="true">{profile.id===value?'✓':''}</span></button>)}{!rows.length&&<p className="model-picker-hint">{copy.empty}</p>}</div>
   <button type="button" className="model-picker-back" onClick={()=>setView('depth')}>‹ {copy.depth}</button>
  </>:<><button type="button" className="model-picker-model" aria-label={copy.models} title={selected?.model} onClick={()=>setView('models')}><span><strong>{selected?.name||selected?.model||copy.choose}</strong></span><span aria-hidden="true">›</span></button>
   {!!choices.length&&<><input type="range" className="model-picker-slider" aria-label={copy.depth} aria-valuetext={caption?reasoningCaption(caption,selected,language):copy.unconfigured} min={0} max={Math.max(0,choices.length-1)} step={1} value={Math.max(0,choices.findIndex(row=>row.level===effort))} disabled={disabled||choices.length<2||!selected?.selectable||selected?.archived} onChange={event=>changeEffort(Number(event.target.value))} onKeyDown={slideKey}/><div className="model-picker-ticks" aria-hidden="true">{choices.map(row=><span key={row.level} className={row.level===effort?'selected':''}>{row.level}</span>)}</div></>}
   {!choices.length&&<p className="model-picker-hint">{copy.configure}</p>}
  </>}
  {notice&&<details className="model-picker-details"><summary>{copy.details}</summary><p className="model-picker-hint">{notice}</p></details>}
  {onApply&&<button type="button" className="model-picker-apply primary" disabled={disabled||applyDisabled} onClick={()=>{if(disabled||applyDisabled)return;close(true);return onApply();}}>{applyLabel}</button>}
 </div>:null;
 return <div className="model-reasoning-picker"><button ref={trigger} type="button" className="model-picker-trigger" aria-label={label||copy.choose} aria-describedby={pending?id+'-pending':undefined} aria-haspopup="dialog" aria-expanded={open} aria-controls={open?id:undefined} disabled={disabled} title={selected?`${selected.name} · ${selected.model}${caption?' · '+reasoningCaption(caption,selected,language):''}`:copy.choose} onClick={()=>{setView('depth');setQuery('');setOpen(!open);}}><span className="model-picker-name">{selected?.name||selected?.model||copy.choose}</span><span className="model-picker-effort">{caption?effort:copy.unconfigured}</span>{pending&&<span id={id+'-pending'} className="model-picker-pending">{copy.pending}</span>}<span aria-hidden="true">⌄</span></button>{panel&&createPortal(panel,document.body)}</div>;
}
