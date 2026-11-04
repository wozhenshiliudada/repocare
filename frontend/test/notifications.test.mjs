import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createNotificationTracker,dispatchNotification,playNotificationSound,notificationText} from '../src/notifications.mjs';
test('per-workspace first reads seed history; new approvals and terminal runs alert once across polling and status reappearance',()=>{
 const tracker=createNotificationTracker(),old={plans:[{id:'p0',status:'awaiting_approval'}],runs:[{run_id:'r0',status:'candidate_ready'}],jobs:[]};assert.deepEqual(tracker.workspace('a',old),[]);
 const updated={plans:[...old.plans,{id:'p1',status:'awaiting_approval'}],runs:[...old.runs,{run_id:'r1',status:'running'}],jobs:[{id:'j1',status:'running'}]};assert.deepEqual(tracker.workspace('a',updated).map(event=>event.type),['approval']);assert.deepEqual(tracker.workspace('a',updated),[]);
 updated.runs[1].status='candidate_ready';updated.jobs[0].status='failed';assert.deepEqual(tracker.workspace('a',updated).map(event=>event.type),['completion','failure']);tracker.workspace('a',old);assert.deepEqual(tracker.workspace('a',updated),[]);assert.deepEqual(tracker.workspace('b',updated),[]);
});
test('hub failures and connection outages deduplicate independently without replaying initial successful state',()=>{
 const tracker=createNotificationTracker();assert.deepEqual(tracker.hub({jobs:[{id:'old',status:'failed'}]}),[]);assert.equal(tracker.hub({jobs:[{id:'new',status:'failed'}]})[0].workspace_id,undefined);
 assert.deepEqual(tracker.service(true),[]);assert.equal(tracker.service(false).length,1);assert.deepEqual(tracker.service(false),[]);assert.equal(tracker.service(true).length,1);assert.deepEqual(tracker.service(true),[]);assert.equal(tracker.service(false).length,1);
});
test('dispatch obeys event/channel preferences, never requests permission and makes desktop clicks read-only callbacks',()=>{
 const notices=[],opens=[],desktop=[];let sounds=0,permissions=0;
 class Notify{static permission='granted';static requestPermission(){permissions++;}constructor(title,options){this.title=title;this.options=options;desktop.push(this);}close(){this.closed=true;}}
 const event={id:'approval:1',type:'approval',kind:'plan',workspace_id:'a'},prefs={language:'en',notifications:{desktop:true,sound:true}};
 assert.deepEqual(dispatchNotification(event,prefs,{NotificationClass:Notify,playSound:()=>{sounds++;},onInApp:notice=>notices.push(notice),onOpen:notice=>opens.push(notice)}),{inApp:true,desktop:true,sound:true,quiet:false});assert.equal(permissions,0);assert.equal(sounds,1);desktop[0].onclick();assert.equal(opens[0].id,event.id);assert.equal(desktop[0].closed,true);
 assert.deepEqual(dispatchNotification(event,{notifications:{events:{approval:false},desktop:true,sound:true}},{NotificationClass:Notify,playSound:()=>{sounds++;},onInApp:notice=>notices.push(notice)}),{inApp:false,desktop:false,sound:false,quiet:false});assert.equal(desktop.length,1);assert.equal(notices.length,1);
});
test('quiet hours retain in-app history while suppressing granted desktop and audio; denied permission stays silent',()=>{
 let desktop=0,sounds=0,inApp=0;class Notify{static permission='granted';constructor(){desktop++;}}
 const event={id:'run:1',type:'completion',kind:'run',status:'candidate_ready'},prefs={notifications:{desktop:true,sound:true,quietHours:{enabled:true,start:'22:00',end:'08:00'}}};
 const result=dispatchNotification(event,prefs,{NotificationClass:Notify,now:new Date(2026,9,30,23),playSound:()=>{sounds++;},onInApp:()=>{inApp++;}});assert.deepEqual(result,{inApp:true,desktop:false,sound:false,quiet:true});assert.equal(desktop,0);assert.equal(sounds,0);assert.equal(inApp,1);
 Notify.permission='denied';assert.equal(dispatchNotification(event,{notifications:{desktop:true}},{NotificationClass:Notify}).desktop,false);assert.equal(desktop,0);
});
test('notification text describes candidates as awaiting acceptance and does not expose raw job errors',()=>{
 const text=notificationText({type:'completion',kind:'run',status:'candidate_ready',repository:'C:/private/repo',error:'private-key-value'},'en');assert.match(text.title,/repo/);assert.match(text.body,/still requires acceptance/);assert.doesNotMatch(JSON.stringify(text),/C:|private-key-value/);
});
test('optional WebAudio uses a short tone and closes only its own context',()=>{
 const actions=[];class Audio{currentTime=0;destination={};createOscillator(){return {frequency:{},connect(){},start(){actions.push('start');},stop(time){actions.push(['stop',time]);this.onended();}};}createGain(){return {gain:{setValueAtTime(){},exponentialRampToValueAtTime(){}},connect(){}};}close(){actions.push('close');}}
 assert.equal(playNotificationSound(Audio),true);assert.deepEqual(actions,['start',['stop',0.15],'close']);assert.equal(playNotificationSound(null),false);
});
