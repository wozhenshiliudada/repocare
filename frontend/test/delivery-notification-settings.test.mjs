import {test} from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import './tsx-register.mjs';
import {dispatchNotification} from '../src/notifications.mjs';
const {useDeliveryFeed,DeliveryInbox}=await import('../src/DeliveryInbox.tsx');
const nodes=(n,check)=>!n||typeof n!=='object'?[]:Array.isArray(n)?n.flatMap(v=>nodes(v,check)):[...(check(n)?[n]:[]),...nodes(n.props?.children,check)];
function hooks(render){
 const values=[],internal=React.__CLIENT_INTERNALS_DO_NOT_USE_OR_WARN_USERS_THEY_CANNOT_UPGRADE;let cursor=0,result,dirty=false,effects=[];
 const dispatcher={useState(initial){const index=cursor++;values[index]??={value:typeof initial==='function'?initial():initial};return [values[index].value,next=>{values[index].value=typeof next==='function'?next(values[index].value):next;dirty=true;}];},useRef(value){const index=cursor++;return values[index]??={current:value};},useEffect(fn,deps){const index=cursor++;if(!values[index]){values[index]={deps};effects.push(()=>values[index].cleanup=fn());}}};
 function refresh(){let rounds=0;do{assert.ok(++rounds<30);cursor=0;dirty=false;effects=[];const previous=internal.H;internal.H=dispatcher;try{result=render();}finally{internal.H=previous;}for(const effect of effects)effect();}while(dirty);return result;}
 refresh();return {refresh,result:()=>result,close(){for(const value of values)value?.cleanup?.();}};
}
test('the product delivery hook suppresses the old desktop flag and applies unified notification preferences',async()=>{
 const previous={sessionStorage:globalThis.sessionStorage,Notification:globalThis.Notification,setInterval:globalThis.setInterval,clearInterval:globalThis.clearInterval},storage=new Map([['repocare.delivery.desktop.v1','true']]);let desktop=0,permissionRequests=0,inApp=0,callbacks=0,snapshot={schema_version:1,read_only:true,feed_id:'root',items:[]};
 class Notify{static permission='granted';static requestPermission(){permissionRequests++;}constructor(){desktop++;}}
 globalThis.sessionStorage={getItem:key=>storage.get(key)||null,setItem:(key,value)=>storage.set(key,value)};globalThis.Notification=Notify;globalThis.setInterval=()=>1;globalThis.clearInterval=()=>{};
 const preferences={notifications:{inApp:false,desktop:false,sound:false}},ui=hooks(()=>useDeliveryFeed(async()=>snapshot,{preferences,onNotifications:events=>{callbacks++;for(const row of events)dispatchNotification({id:row.notice_id,type:'completion',kind:'delivery'},preferences,{NotificationClass:Notify,onInApp:()=>inApp++});}}));
 try{for(let i=0;i<6;i++)await Promise.resolve();ui.refresh();snapshot={...snapshot,items:[{id:'new',notice_id:'new-notice',status:'candidate_review'}]};await ui.result().refresh();ui.refresh();assert.equal(callbacks,1);assert.equal(desktop,0);assert.equal(permissionRequests,0);assert.equal(inApp,0);assert.equal(ui.result().alerts.length,1);}finally{ui.close();Object.assign(globalThis,previous);}
});
test('unified delivery UI opens the notification settings instead of requesting browser permission',()=>{
 let opens=0;const props={p:{hub:{jobs:[]},notificationPreferences:{notifications:{desktop:false}},onOpenNotifications:()=>opens++,request:async()=>({}),refresh:async()=>{}}};
 const html=renderToStaticMarkup(React.createElement(DeliveryInbox,props));assert.match(html,/通知设置/);assert.doesNotMatch(html,/允许本页面交付通知/);
 const ui=hooks(()=>DeliveryInbox(props));try{const button=nodes(ui.result(),node=>node.type==='button'&&node.props.children==='通知设置')[0];button.props.onClick();assert.equal(opens,1);}finally{ui.close();}
});
