import {test} from 'node:test';
import assert from 'node:assert/strict';
import {deliveryNotices,notifyDelivery} from '../src/delivery-model.mjs';
const row=(id,status='candidate_review')=>({id,notice_id:id,status});
test('initial history is silent, a new candidate alerts once and refresh does not replay it',()=>{
 const first=deliveryNotices({feed_id:'root',items:[row('old')]},null);
 assert.deepEqual(first.alerts,[]);
 const next=deliveryNotices({feed_id:'root',items:[row('old'),row('new')]},first.state);
 assert.deepEqual(next.alerts.map(r=>r.id),['new']);
 assert.deepEqual(deliveryNotices({feed_id:'root',items:[row('new')]},next.state).alerts,[]);
});
test('new origins seed independently and in-progress records do not alert',()=>{
 const first=deliveryNotices({feed_id:'a',items:[]},null);
 assert.equal(deliveryNotices({feed_id:'b',items:[row('history')]},first.state).alerts.length,0);
 assert.equal(deliveryNotices({feed_id:'a',items:[row('running','in_progress')]},first.state).alerts.length,0);
});
test('desktop output needs pre-existing permission and does not request it or include private titles',()=>{
 class Notify{static permission='default';constructor(title,options){Notify.sent.push({title,options});}static sent=[];}
 assert.equal(notifyDelivery([row('n')],Notify),0);
 Notify.permission='granted';assert.equal(notifyDelivery([{...row('n'),title:'PRIVATE_SOURCE_TEXT'}],Notify),1);
 assert.doesNotMatch(JSON.stringify(Notify.sent),/PRIVATE_SOURCE_TEXT/);
 assert.equal(notifyDelivery([row('n')],null),0);
});
