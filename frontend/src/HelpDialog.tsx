import React,{useEffect,useRef,useState} from 'react';
import {assistantCopy} from './AssistantPane';
import {catalogDisplayText} from './catalog-language.mjs';
import './assistant.css';
type Data=Record<string,any>;
export type HelpDialogProps={request:(path:string,token?:string,body?:Data)=>Promise<Data>;language:string;onClose:()=>void;navigate?:(page:string)=>void};
export function HelpDialog({request,language,onClose,navigate}:HelpDialogProps){
 const [help,setHelp]=useState<Data|null>(null),[query,setQuery]=useState(''),[error,setError]=useState(false),[busy,setBusy]=useState(true),[download,setDownload]=useState('');
 const dialog=useRef<HTMLElement>(null),close=useRef<HTMLButtonElement>(null),alive=useRef(true),generation=useRef(0);
 const tr=(key:string)=>assistantCopy(language,key);
 async function read(){const version=++generation.current;setBusy(true);setError(false);
  try{const value=await request('/api/help?language='+encodeURIComponent(language));if(alive.current&&generation.current===version){if(typeof value.content!=='string'||!Array.isArray(value.catalog))throw Error('invalid help');setHelp(value);}}
  catch{if(alive.current&&generation.current===version)setError(true);}
  finally{if(alive.current&&generation.current===version)setBusy(false);}
 }
 useEffect(()=>{alive.current=true;const previous=document.activeElement as HTMLElement|null;close.current?.focus();return()=>{alive.current=false;generation.current++;if(previous?.isConnected)previous.focus();};},[]);
 useEffect(()=>{setHelp(null);void read();},[language]);
 useEffect(()=>{if(!help){setDownload('');return;}const url=URL.createObjectURL(new Blob([help.content],{type:'text/markdown;charset=utf-8'}));setDownload(url);return()=>{URL.revokeObjectURL(url);};},[help]);
 function keyboard(event:React.KeyboardEvent){
  if(event.key==='Escape'){event.preventDefault();event.stopPropagation();onClose();return;}
  if(event.key!=='Tab')return;
  const controls=[...(dialog.current?.querySelectorAll<HTMLElement>('button:not(:disabled),input:not(:disabled),a[href],select:not(:disabled),[tabindex="0"]')||[])],first=controls[0],last=controls.at(-1),active=document.activeElement;
  if(event.shiftKey&&(active===first||!dialog.current?.contains(active))){event.preventDefault();last?.focus();}
  else if(!event.shiftKey&&(active===last||!dialog.current?.contains(active))){event.preventDefault();first?.focus();}
 }
 const search=query.trim().toLocaleLowerCase(),catalog=(help?.catalog||[]).map((item:Data)=>({...item,displayTitle:catalogDisplayText(item.title,language),displayDescription:catalogDisplayText(item.description,language)})).filter((item:Data)=>!search||[item.title,item.description,item.displayTitle,item.displayDescription,item.id].join(' ').toLocaleLowerCase().includes(search));
 const content=typeof help?.content==='string'?help.content:'',sections=search?content.split(/\n(?=#{1,3} )/).filter(section=>section.toLocaleLowerCase().includes(search)).join('\n\n'):content;
 return <div className="help-dialog-backdrop" onClick={event=>{if(event.target===event.currentTarget)onClose();}}><section ref={dialog} className="help-dialog" role="dialog" aria-modal="true" aria-labelledby="help-dialog-title" onKeyDown={keyboard} data-form-assist="exclude">
  <header><h2 id="help-dialog-title">{help?.title||tr('helpTitle')}</h2><button ref={close} type="button" aria-label={tr('helpClose')} onClick={onClose}>×</button></header>
  {help?.illustrated_guide===true&&<div className="help-illustrated"><div><strong>{language==='en'?'Illustrated walkthrough':'一步一图使用教程'}</strong><p>{language==='en'?'Follow the Chinese screenshots from setup to repository maintenance.':'从配置模型、连接仓库，到审批和自动维护，按实际界面一步步操作。'}</p></div><div className="help-illustrated-links"><a href="/tutorial/index.html" target="_blank" rel="noopener noreferrer">{language==='en'?'Open illustrated guide ↗':'打开图文教程 ↗'}</a><a href="/tutorial/repocare-guide.zip" download>{language==='en'?'Download offline ZIP':'下载离线 ZIP'}</a></div></div>}
  <div className="help-toolbar"><input type="search" aria-label={tr('search')} placeholder={tr('search')} value={query} onChange={event=>setQuery(event.target.value)}/>{download&&<a className="help-download" href={download} download={'RepoCare-'+language+'.md'}>{tr('download')}</a>}</div>
  <div className="help-dialog-scroll">{busy&&<p role="status">{tr('helpLoading')}</p>}{error&&<div><p role="alert" className="error-text">{tr('helpError')}</p><button type="button" onClick={read}>{tr('retry')}</button></div>}
   {help&&<><section className="help-catalog" aria-labelledby="help-catalog-title"><h3 id="help-catalog-title">{tr('catalog')}</h3>{catalog.length?<ul>{catalog.map((item:Data)=><li key={item.id}><div><strong>{item.displayTitle}</strong><span className="small-tag">{tr(({implemented:'available',available:'available',partial:'catalogPartial',planned:'planned',unverified:'unverified'} as Data)[item.availability]||String(item.availability||'unverified'))}</span></div><p>{item.displayDescription}</p>{navigate&&item.page&&<button type="button" onClick={()=>{navigate(item.page);onClose();}}>{tr('openPage')}</button>}</li>)}</ul>:<p>{tr('noMatches')}</p>}</section><pre className="help-markdown">{sections||tr('noMatches')}</pre></>}
  </div>
 </section></div>;
}
