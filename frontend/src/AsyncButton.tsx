import {ui} from './ui-language.mjs';
import React,{useEffect,useRef,useState} from 'react';
import './async-button.css';

export type ButtonFeedback={pending:boolean;uncertain?:boolean};
type Props=React.ButtonHTMLAttributes<HTMLButtonElement>&{feedback?:ButtonFeedback};

// Feedback belongs to the clicked instance, not every button sharing a job queue.
// The original handler remains the only authority for requests and approvals.
export function AsyncButton({feedback,onClick,disabled,children,...props}:Props){
 const [started,setStarted]=useState(false),[awaiting,setAwaiting]=useState(false);
 const locked=useRef(false),mounted=useRef(true);
 useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;};},[]);
 const pending=started&&(awaiting||Boolean(feedback?.pending));
 useEffect(()=>{
  if(started&&!awaiting&&!feedback?.pending){
   // Native form submission runs after the click handler. Give onSubmit the
   // current event turn to establish its request before releasing ownership.
   const release=setTimeout(()=>{locked.current=false;setStarted(false);},0);
   return()=>clearTimeout(release);
  }
 },[started,awaiting,feedback?.pending]);
 async function click(event:React.MouseEvent<HTMLButtonElement>){
  if(disabled||locked.current){event.preventDefault();return;}
  locked.current=true;setStarted(true);
  // A submit-only button must keep its native default action until onSubmit runs.
  if(!onClick)return;
  setAwaiting(true);
  try{await onClick(event);}
  finally{if(mounted.current)setAwaiting(false);}
 }
 const unknown=pending&&Boolean(feedback?.uncertain);
 const waitLabel=unknown?ui("等待核验…"):ui("处理中…");
 const iconOnly=Boolean(props['aria-label'])&&typeof children==='string'&&children.length<=2;
 return <button {...props} disabled={disabled||pending} onClick={click}
  aria-label={pending&&props['aria-label']?props['aria-label']+' · '+waitLabel:props['aria-label']}
  aria-busy={pending&&!unknown?true:undefined} data-pending={pending?unknown?'uncertain':'waiting':undefined}>
  {pending?<>{!unknown&&<span className="button-spinner" aria-hidden="true"/>}{!iconOnly&&children}<span className={iconOnly?'button-wait-sr':'button-wait-label'}>{iconOnly?'':' · '}{waitLabel}</span></>:children}
 </button>;
}
