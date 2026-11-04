import {test} from 'node:test';
import assert from 'node:assert/strict';
import {watchNames,watchDeadline,watchCanResume,watchLabel} from '../src/acceptance-watch-model.mjs';
test('watch expiry is an explicit bounded UTC time and invalid durations cannot silently extend it',()=>{
 const now=Date.parse('2026-10-27T00:00:00Z');
 assert.equal(watchDeadline(30,now),'2026-10-27T00:30:00Z');
 for(const minutes of [0,1,1441,NaN,Infinity,30.5,'30'])assert.throws(()=>watchDeadline(minutes,now));
});
test('resume requires a paused unexpired watch with history room, never an old green receipt',()=>{
 const now=Date.parse('2026-10-27T00:00:00Z');
 const row={enabled:false,status:'restart_paused',observations:[],core:{expires_at:watchDeadline(30,now)}};
 assert.equal(watchCanResume(row,now),true);
 for(const patch of [{enabled:true},{status:'verified'},{status:'scope_changed'},{observations:Array(100).fill('x')},
  {core:{expires_at:'2026-10-26T23:59:59Z'}}])assert.equal(watchCanResume({...row,...patch},now),false);
 assert.equal(watchNames.checks_failed,'最新检查未通过');assert.notEqual(watchNames.checks_missing,watchNames.verified);
 assert.equal(watchLabel({...row,enabled:true,status:'verified'},now+3600000),'观察已到期，等待后台收尾');
});

test('legacy paused terminal receipts still require new scope confirmation',()=>{
 const now=Date.parse('2026-10-27T00:00:00Z');
 for(const terminal of ['scope_changed','closed_without_merge','expired','capacity_reached']){
  for(const status of ['paused','restart_paused','blocked']){
   const row={enabled:false,status,observations:['original'],core:{expires_at:watchDeadline(30,now)},
    last_observation:{outcome:{status:terminal}}};
   assert.equal(watchCanResume(row,now),false);
   assert.equal(watchLabel(row,now),watchNames[terminal]);
  }
 }
 const normal={enabled:false,status:'paused',observations:[],core:{expires_at:watchDeadline(30,now)},
  last_observation:{outcome:{status:'checks_failed'}}};
 assert.equal(watchCanResume(normal,now),true);
 assert.equal(watchLabel(normal,now),watchNames.paused);
});
