import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readinessGroups,serviceReadiness,serviceSituation} from '../src/service-readiness-model.mjs';

const now=Date.parse('2026-10-28T00:00:00+00:00');
const inspect=(value,clock=now)=>serviceReadiness(value,clock);
const required={python:'installation',git:'installation',node:'runtime',pi_lock:'runtime',ui_index:'assets',
 python_dependencies:'installation',pi_dependencies:'runtime',ui_assets:'assets',provider_config:'provider'};
function report(){return {schema_version:1,status:'ready',checked_at:'2026-10-28T00:00:00+00:00',stale:false,valid_for_seconds:30,
 scope:'local_only',network_checked:false,model_calls:0,
 checks:Object.entries(required).map(([id,group])=>({id,group,status:'ready',label:'本地检查',detail:'固定说明',next_action:''}))};}

test('a reachable idle service is a connection receipt and never model verification',()=>{
 const situation=serviceSituation({});
 assert.match(situation.label,/已连接/);assert.match(situation.detail,/单独核对/);
 assert.doesNotMatch(situation.label,/已核验|模型可用|齐备/);
 assert.match(inspect(undefined).modelLabel,/尚未验证/);
});
test('old service responses, malformed schemas and incomplete checks never become ready',()=>{
 for(const value of [undefined,{}, {...report(),checks:[]},{...report(),checks:report().checks.slice(1)},
  {...report(),scope:'network'},{...report(),network_checked:true},{...report(),model_calls:1},
  {...report(),checked_at:null},{...report(),checks:[null]}, {...report(),status:'invented'}]){
  assert.notEqual(inspect(value).status,'ready');
 }
});
test('complete local checks explicitly retain unverified model connectivity',()=>{
 const value=inspect(report());
 assert.equal(value.status,'ready');assert.equal(value.label,'本地条件齐备');
 assert.equal(value.modelLabel,'模型请求尚未验证');
 const stale=inspect({...report(),stale:true});
 assert.equal(stale.stale,true);assert.match(stale.label,/过期/);
});
test('a blocked runtime or configuration wins over a claimed top-level ready',()=>{
 for(const group of ['runtime','provider']){
  const value=report();value.checks.find(row=>row.group===group).status='blocked';
  assert.equal(inspect(value).status,'blocked');
 }
 const unknown=report();unknown.checks[0].status='unknown';
 assert.equal(inspect(unknown).status,'unknown');
});

test('four ready groups cannot replace missing exact installation checks',()=>{
 const value=report();value.checks=value.checks.filter(row=>['python','node','ui_index','provider_config'].includes(row.id));
 assert.equal(new Set(value.checks.map(row=>row.group)).size,readinessGroups.length);
 assert.equal(inspect(value).status,'unknown');
 for(const id of Object.keys(required)){
  const missing=report();missing.checks=missing.checks.filter(row=>row.id!==id);
  assert.equal(inspect(missing).status,'unknown',id);
 }
});

test('duplicates, mismatched groups and unsafe row fields fail closed',()=>{
 const duplicate=report();duplicate.checks.push({...duplicate.checks[0]});
 assert.equal(inspect(duplicate).status,'unknown');
 for(const field of ['id','group','status','label','detail','next_action']){
  for(const invalid of [null,{},[],1]){
   const value=report();value.checks[0][field]=invalid;
   assert.equal(inspect(value).status,'unknown',field);
  }
 }
 const wrong=report();wrong.checks[0].group='provider';assert.equal(inspect(wrong).status,'unknown');
 const unknown=report();unknown.checks[0].id='invented';assert.equal(inspect(unknown).status,'unknown');
 const long=report();long.checks[0].detail='x'.repeat(2001);assert.equal(inspect(long).status,'unknown');
 const controls=report();controls.checks[0].label='private\u0000data';assert.equal(inspect(controls).status,'unknown');
});

test('readiness ages locally at the advertised deadline without a new response',()=>{
 const value=report();
 assert.equal(inspect(value,now+29999).status,'ready');
 const expired=inspect(value,now+30000);
 assert.equal(expired.status,'unknown');assert.equal(expired.stale,true);assert.match(expired.label,/过期/);
 assert.equal(inspect(value,now+3600000).status,'unknown');
 const short=report();short.valid_for_seconds=5;
 assert.equal(inspect(short,now+5000).stale,true);
});

test('future, malformed, absent timestamps and legacy or invalid lifetime cannot be ready',()=>{
 for(const checked_at of [null,{},'not-a-date','2026','2026-10-28T00:00:01+00:00']){
  const value=inspect({...report(),checked_at});assert.equal(value.status,'unknown');assert.equal(value.checkedAt,null);
 }
 for(const valid_for_seconds of [undefined,null,true,NaN,Infinity,0,-1,3601,'30']){
  assert.equal(inspect({...report(),valid_for_seconds}).status,'unknown');
 }
 assert.equal(inspect(report(),NaN).status,'unknown');
});
test('next step distinguishes connection failure, drift, execution, monitoring and idle',()=>{
 assert.match(serviceSituation(null).label,/正在读取/);
 const disconnected=serviceSituation({},true);
 assert.match(disconnected.label,/中断/);
 assert.match(disconnected.next,/原 --home.*gateway status.*gateway open/);
 assert.match(serviceSituation({stopping:true}).label,/正在停止/);
 const drift=serviceSituation({runtime:{code_changed:true},workspace_active:true});
 assert.match(drift.label,/版本已变化/);assert.match(drift.next,/原状态目录/);
 for(const field of ['workspace_active','history_active','active']){
  const value=serviceSituation({[field]:true});assert.match(value.label,/执行/);assert.match(value.next,/原停止入口/);
 }
 for(const field of ['monitor_enabled','acceptance_watch_active']){
  const value=serviceSituation({[field]:true});assert.match(value.label,/观察已启用/);assert.match(value.next,/暂停/);
 }
});

// Runtime rows captured from the installed-wheel HTTP receipt on 2026-10-29.
// Keep its provider blocked and evaluate against a fixed clock, without networking.
function capturedWheelReport(){
 const value=report();value.status='blocked';
 value.checks.splice(5,0,
  {id:'pi_runtime',group:'runtime',status:'ready',label:'Pi 运行资源',detail:'本地检查通过。',next_action:''},
  {id:'pi_resources',group:'runtime',status:'ready',label:'Pi 资源完整性',detail:'本地检查通过。',next_action:''});
 Object.assign(value.checks.find(row=>row.id==='provider_config'),{status:'blocked',
  label:'模型配置文件',detail:'示例内容尚未替换，请填写自己的密钥、地址和模型。',
  next_action:'在本机编辑启动时指定的模型配置文件，然后重新检查。'});
 return value;
}

test('captured installed-wheel readiness displays Pi resources and blocked configuration',()=>{
 const value=capturedWheelReport(),result=inspect(value);
 assert.equal(result.status,'blocked');
 assert.equal(result.label,'本地条件待补齐');
 assert.deepEqual(result.checks,value.checks);
 assert.equal(result.checks.find(row=>row.id==='provider_config').status,'blocked');
 assert.equal(result.modelLabel,'模型请求尚未验证');
});

test('recognized Pi resource rows preserve runtime failures and local-only boundaries',()=>{
 const complete=capturedWheelReport();complete.status='ready';complete.checks.forEach(row=>row.status='ready');
 assert.equal(inspect(complete).status,'ready');
 for(const id of ['pi_runtime','pi_resources']){
  const blocked=structuredClone(complete);blocked.checks.find(row=>row.id===id).status='blocked';
  assert.equal(inspect(blocked).status,'blocked',id);
  const wrong=structuredClone(complete);wrong.checks.find(row=>row.id===id).group='provider';
  assert.equal(inspect(wrong).status,'unknown',id);
  for(const field of ['id','group','status','label','detail','next_action']){
   const missing=structuredClone(complete);delete missing.checks.find(row=>row.id===id)[field];
   assert.equal(inspect(missing).status,'unknown',id+':'+field);
  }
 }
 const unknown=structuredClone(complete);unknown.checks.push({...unknown.checks[0],id:'pi_arbitrary'});
 assert.equal(inspect(unknown).status,'unknown');
 for(const change of [{scope:'network'},{network_checked:true},{model_calls:1}]){
  assert.equal(inspect({...complete,...change}).status,'unknown');
 }
});
