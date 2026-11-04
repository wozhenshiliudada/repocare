import test from 'node:test';
import assert from 'node:assert/strict';
import {usageText} from '../src/request-usage.mjs';
import {monitorsForWorkspace} from '../src/operation-model.mjs';
import {setUiLanguage} from '../src/ui-language.mjs';

test('overview uses separate original monitor and manual counts scoped to the selected repository',()=>{
 setUiLanguage('en');
 const budget={request_budget:100,requests_used:0,requests_remaining:100,requests_reserved_unknown:0,
  requests_reserved_active:0,requests_ended_unknown:0};
 const monitor={id:'match',spec:{path:'/quantbull',branch:'main'},...budget,
  requests_used:2,requests_remaining:95,requests_reserved_active:3,requests_reserved_unknown:3};
 const hub={monitors:[monitor,{...monitor,id:'other',spec:{path:'/other'}}]};
 const rows=monitorsForWorkspace(hub,{repository:'/quantbull'},'http://localhost');
 assert.deepEqual(rows,[monitor]);
 assert.match(usageText(budget),/Used 0 .* Available 100/);
 assert.match(usageText(rows[0]),/Used 2 .* In-flight reservations 3 .* Ended, unverified 0 .* Available 95/);
});

test('legacy aggregate remains ambiguous; ended unknown is distinct on a current response',()=>{
 setUiLanguage('en');
 const legacy={request_budget:100,requests_used:0,requests_remaining:0,requests_reserved_unknown:100};
 assert.match(usageText(legacy),/Reserved or unverified 100/);
 assert.doesNotMatch(usageText(legacy),/In-flight|Ended/);
 assert.match(usageText({...legacy,requests_reserved_active:0,requests_ended_unknown:100}),
  /In-flight reservations 0 .* Ended, unverified 100/);
});
