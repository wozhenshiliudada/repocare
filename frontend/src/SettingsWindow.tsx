import React,{useEffect,useRef,useState} from 'react';
import {ModelProfilesSettings} from './ProviderSettings';
import {applyPreferences,normalizePreferences,NOTIFICATION_EVENTS,t,writePreferences} from './preferences.mjs';
import './settings.css';

type Data=Record<string,any>;
export type SettingsWindowProps={prefs:Data;onPrefsChange:(value:Data)=>void;request:(path:string,token?:string,body?:Data)=>Promise<Data>;
 initialProfiles?:Data;onProfilesChange?:(value:Data)=>void;onClose:()=>void;assistant?:React.ReactNode;onOpenAssistant?:()=>void;initialCategory?:string};
function notificationPermission(){return typeof Notification==='undefined'?'unsupported':Notification.permission;}
function SettingsIcon({name}:{name:string}){
 const path=name==='general'?<><circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1 1m12 12 1 1M5 19l1-1M18 6l1-1"/></>:name==='models'?<><rect x="4" y="4" width="16" height="16" rx="4"/><path d="M8 9h8M8 13h8M8 17h4"/></>:<><path d="M5 17h14l-2-4V9a5 5 0 0 0-10 0v4Z"/><path d="M10 20h4"/></>;
 return <svg aria-hidden="true" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">{path}</svg>;
}
export function SettingsWindow({prefs,onPrefsChange,request,initialProfiles,onProfilesChange,onClose,assistant,onOpenAssistant,initialCategory='general'}:SettingsWindowProps){
 const [draft,setDraft]=useState(()=>normalizePreferences(prefs));
 const safe=normalizePreferences(draft),dirty=JSON.stringify(safe)!==JSON.stringify(normalizePreferences(prefs)),[category,setCategory]=useState(['general','models','notifications'].includes(initialCategory)?initialCategory:'general'),[storageFailed,setStorageFailed]=useState(false);
 useEffect(()=>{setDraft(normalizePreferences(prefs));},[prefs]);
 useEffect(()=>{setCategory(['general','models','notifications'].includes(initialCategory)?initialCategory:'general');},[initialCategory]);
 const [permission,setPermission]=useState(notificationPermission),[permissionBusy,setPermissionBusy]=useState(false),[permissionError,setPermissionError]=useState(false);
 const dialog=useRef<HTMLElement>(null),closeButton=useRef<HTMLButtonElement>(null),alive=useRef(true),requesting=useRef(false);
 const tr=(key:string)=>t(prefs.language||'zh-CN',key),n=safe.notifications;
 useEffect(()=>{
  alive.current=true;const previous=document.activeElement as HTMLElement|null;closeButton.current?.focus();
  const update=()=>setPermission(notificationPermission());window.addEventListener('focus',update);
  return()=>{alive.current=false;window.removeEventListener('focus',update);if(previous?.isConnected)previous.focus();};
 },[]);
 function change(patch:Data){setDraft(previous=>normalizePreferences({...normalizePreferences(previous),...patch}));setStorageFailed(false);}
 function savePreferences(){applyPreferences(safe);setStorageFailed(!writePreferences(safe));onPrefsChange({...prefs,...safe});}
 function notification(patch:Data){
  setDraft(previous=>{const current=normalizePreferences(previous),notifications=current.notifications;
   return normalizePreferences({...current,notifications:{...notifications,...patch,events:{...notifications.events,...patch.events},quietHours:{...notifications.quietHours,...patch.quietHours}}});
  });setStorageFailed(false);
 }
 async function requestPermission(){
  if(requesting.current||typeof Notification==='undefined'||Notification.permission==='denied')return;
  requesting.current=true;setPermissionBusy(true);setPermissionError(false);
  try{const result=await Notification.requestPermission();if(alive.current)setPermission(result);}
  catch{if(alive.current)setPermissionError(true);}
  finally{requesting.current=false;if(alive.current)setPermissionBusy(false);}
 }
 function keyboard(event:React.KeyboardEvent){
  if(event.defaultPrevented)return;
  if(event.key==='Escape'){event.preventDefault();event.stopPropagation();onClose();return;}
  if(event.key!=='Tab')return;
  const controls=[...(dialog.current?.querySelectorAll<HTMLElement>('button:not(:disabled),input:not(:disabled),select:not(:disabled),textarea:not(:disabled),[tabindex="0"]')||[])].filter(element=>!element.closest('[hidden]'));
  const first=controls[0],last=controls.at(-1),active=document.activeElement;
  if(event.shiftKey&&(active===first||!dialog.current?.contains(active))){event.preventDefault();last?.focus();}
  else if(!event.shiftKey&&(active===last||!dialog.current?.contains(active))){event.preventDefault();first?.focus();}
 }
 return <div className="settings-window-backdrop" onClick={event=>{if(event.target===event.currentTarget)onClose();}}>
  <section ref={dialog} className={'settings-window'+(assistant?' has-assistant':'')} role="dialog" aria-modal="true" aria-labelledby="settings-window-title" onKeyDown={keyboard}>
   <aside className="settings-sidebar"><h2 id="settings-window-title">{tr('app.settings')}</h2><nav aria-label={tr('settings.categories')}>
    {['general','models','notifications'].map(id=><button type="button" key={id} aria-current={category===id?'page':undefined} onClick={()=>setCategory(id)}><SettingsIcon name={id}/><span>{tr('settings.'+id)}</span></button>)}
   </nav></aside>
   <div className="settings-main"><header className="settings-window-header"><h2>{tr('settings.'+category)}</h2><div className="settings-header-actions">{onOpenAssistant&&<button type="button" aria-expanded={Boolean(assistant)} onClick={onOpenAssistant}>{prefs.language==='en'?'Ask assistant':'询问助手'}</button>}<button ref={closeButton} className="settings-close" type="button" aria-label={tr('settings.close')} onClick={onClose}>×</button></div></header>
    <div className="settings-scroll" key={category}>
     {category==='general'&&<section aria-label={tr('settings.general')}><p className="settings-description">{tr('settings.general.description')}</p>
      <label className="settings-row"><span>{tr('settings.theme')}</span><select data-form-assist-draft="true" aria-label={tr('settings.theme')} value={safe.theme} onChange={event=>change({theme:event.target.value})}>{['light','dark','system'].map(theme=><option key={theme} value={theme}>{tr('settings.'+theme)}</option>)}</select></label>
      <label className="settings-row"><span>{tr('settings.fontSize')}</span><select data-form-assist-draft="true" aria-label={tr('settings.fontSize')} value={safe.fontSize} onChange={event=>change({fontSize:Number(event.target.value)})}>{[13,15,17].map(size=><option key={size} value={size}>{size}px</option>)}</select></label>
      <label className="settings-row"><span>{tr('settings.language')}</span><select data-form-assist-draft="true" aria-label={tr('settings.language')} value={safe.language} onChange={event=>change({language:event.target.value})}><option value="zh-CN">简体中文</option><option value="en">English</option></select></label>
     </section>}
     {category==='models'&&<ModelProfilesSettings request={request} initialProfiles={initialProfiles} onProfilesChange={onProfilesChange} language={prefs.language||'zh-CN'}/>}
     {category==='notifications'&&<section aria-label={tr('settings.notifications')}><p className="settings-description">{tr('notifications.description')}</p>
      <fieldset className="notification-options"><legend>{tr('notifications.events')}</legend>{NOTIFICATION_EVENTS.map(event=><label className="settings-check" key={event}><span>{tr('notifications.'+event)}</span><input data-form-assist-draft="true" type="checkbox" checked={n.events[event]===true} onChange={e=>notification({events:{[event]:e.currentTarget.checked}})} onInput={e=>notification({events:{[event]:e.currentTarget.checked}})}/></label>)}</fieldset>
      <fieldset className="notification-options"><legend>{tr('notifications.channels')}</legend>{['inApp','desktop','sound'].map(channel=><label className="settings-check" key={channel}><span>{tr('notifications.'+channel)}</span><input data-form-assist-draft="true" type="checkbox" checked={Boolean((n as Data)[channel])} disabled={channel==='desktop'&&permission==='unsupported'} onChange={e=>notification({[channel]:e.currentTarget.checked})} onInput={e=>notification({[channel]:e.currentTarget.checked})}/></label>)}
       <div className="desktop-permission"><p>{tr('notifications.permission')} · <span role="status">{tr('notifications.permission.'+permission)}</span></p>{permission==='default'&&<button type="button" disabled={permissionBusy} onClick={requestPermission}>{tr(permissionBusy?'notifications.requesting':'notifications.request')}</button>}<p className="caption">{tr('notifications.permissionHint')}</p>{permissionError&&<p role="alert" className="error-text">{tr('notifications.requestFailed')}</p>}</div>
      </fieldset>
      <fieldset className="notification-options"><legend>{tr('notifications.quiet')}</legend><label className="settings-check"><span>{tr('notifications.quietEnabled')}</span><input data-form-assist-draft="true" type="checkbox" checked={n.quietHours.enabled} onChange={e=>notification({quietHours:{enabled:e.currentTarget.checked}})} onInput={e=>notification({quietHours:{enabled:e.currentTarget.checked}})}/></label><div className="quiet-hours-fields"><label>{tr('notifications.start')}<input data-form-assist-draft="true" type="time" aria-label={tr('notifications.start')} value={n.quietHours.start} disabled={!n.quietHours.enabled} onChange={e=>notification({quietHours:{start:e.target.value}})} onInput={e=>notification({quietHours:{start:e.currentTarget.value}})}/></label><label>{tr('notifications.end')}<input data-form-assist-draft="true" type="time" aria-label={tr('notifications.end')} value={n.quietHours.end} disabled={!n.quietHours.enabled} onChange={e=>notification({quietHours:{end:e.target.value}})} onInput={e=>notification({quietHours:{end:e.currentTarget.value}})}/></label></div><p className="caption">{tr('notifications.quietHint')}</p></fieldset>
     </section>}
     {category!=='models'&&<button type="button" className="primary settings-save" disabled={!dirty&&!storageFailed} onClick={savePreferences}>{prefs.language==='en'?'Save settings':'保存设置'}</button>}
     <p className={'settings-persistence caption'+(storageFailed?' error-text':'')} role={storageFailed?'alert':undefined}>{storageFailed?tr('settings.storageFailed'):dirty?(prefs.language==='en'?'Changes are drafts. Click “Save settings” to apply them.':'更改仍是草稿，点击“保存设置”后生效。'):(prefs.language==='en'?'Preferences are saved in this browser.':'偏好保存在此浏览器。')}</p>
    </div>
   </div>
   {assistant&&<div className="settings-assistant">{assistant}</div>}
  </section>
 </div>;
}
