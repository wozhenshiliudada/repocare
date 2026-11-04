import './tsx-register.mjs';
import {test} from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
const {GitHubPushStatus}=await import('../src/GitHubPushStatus.tsx');
import {serviceBlocked} from '../src/operation-model.mjs';
const render=value=>renderToStaticMarkup(React.createElement(GitHubPushStatus,{value,navigate:()=>{throw Error('no action during render');}}));

test('unconfigured push intake is not advertised as already connected',()=>{
 const html=render(null);assert.match(html,/尚未配置主动通知入口/);assert.match(html,/现有提交监控/);
 assert.doesNotMatch(html,/本机接收入口已开启/);
});
test('linked receipt retains blocked original event and never declares repair delivery',()=>{
 const html=render({accepting:true,target:{repository:'example/private',branch:'main'},
  deliveries:[{id:'d'}],work:[{id:'w',after:'a'.repeat(40),status:'event_linked',event_id:'e',
   monitor_message:'需要新的人工审批 <script>alert(1)</script>',next_step:'查看原事件'}]});
 assert.match(html,/已关联原检查事件/);assert.match(html,/通知已登记不代表检查通过或修复交付/);
 assert.match(html,/需要新的人工审批 &lt;script&gt;/);assert.doesNotMatch(html,/<script>/);
});
test('unknown receipt and forced or deleted notification keep manual next step',()=>{
 const html=render({accepting:false,deliveries:[{receipt:{status:'blocked_forced'}}],
  work:[{id:'w',status:'interrupted_no_replay',after:'b'.repeat(40),next_step:'核对原消费，不重放'}]});
 assert.match(html,/上次处理中断，保留现场/);assert.match(html,/核对原消费，不重放/);assert.match(html,/强推或分支删除/);
});
test('original safe stop is blocked while a push worker is queued',()=>{
 assert.equal(serviceBlocked({github_push_active:true}),true);
 assert.equal(serviceBlocked({github_push_active:false}),false);
});
