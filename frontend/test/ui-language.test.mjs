import {test} from 'node:test';
import assert from 'node:assert/strict';
import {setUiLanguage,getUiLanguage,ui,uiLabels} from '../src/ui-language.mjs';
import {statusLabel} from '../src/workbench-model.mjs';
import {serviceSituation} from '../src/service-readiness-model.mjs';
import {readinessDisplayText} from '../src/readiness-ui-copy.mjs';

test('language changes apply to labels created before selection without altering raw content',()=>{
 const labels=uiLabels({title:'服务管理',nested:['连接仓库']});
 const raw='用户原文、仓库模型输出 <secret>';
 try{
  setUiLanguage('en');assert.equal(getUiLanguage(),'en');
  assert.equal(ui('服务管理'),'Service management');
  assert.equal(labels.title,'Service management');assert.equal(labels.nested[0],'Connect repository');
  assert.equal(ui(raw),raw);assert.equal(statusLabel('running'),'Running');
  assert.equal(serviceSituation(null).label,'Reading service status');
  setUiLanguage('zh-CN');assert.equal(labels.title,'服务管理');assert.equal(statusLabel('running'),'执行中');
 }finally{setUiLanguage('zh-CN');}
});

test('explicit pairs and unsupported locale fall back without executing content',()=>{
 try{setUiLanguage('en');assert.equal(ui('标题','Title'),'Title');
  setUiLanguage('<script>en</script>');assert.equal(getUiLanguage(),'zh-CN');assert.equal(ui('标题','Title'),'标题');
 }finally{setUiLanguage('zh-CN');}
});

test('service check localization accepts only known fixed source copy and preserves the raw receipt',()=>{
 const row={id:'python',label:'Python 版本',detail:'服务管理',next_action:'使用 Python 3.11 或更新版本。'},before=JSON.stringify(row);
 try{setUiLanguage('en');assert.equal(readinessDisplayText(row,'label'),'Python version');
  assert.equal(readinessDisplayText(row,'next_action'),'Use Python 3.11 or newer.');
  assert.equal(readinessDisplayText(row,'detail'),'服务管理');
  assert.equal(readinessDisplayText({...row,id:'arbitrary'},'label'),'Python 版本');
  assert.equal(JSON.stringify(row),before);
 }finally{setUiLanguage('zh-CN');}
});
