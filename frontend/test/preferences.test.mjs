import {test} from 'node:test';
import assert from 'node:assert/strict';
import {PREFERENCES_KEY,normalizePreferences,readPreferences,writePreferences,applyPreferences,notificationDecision,isQuietHours,t,translations} from '../src/preferences.mjs';

test('persistent preferences survive a fresh read and discard credentials and unsupported values',()=>{
 const values=new Map(),storage={getItem:key=>values.get(key),setItem:(key,value)=>values.set(key,value)};
 assert.equal(writePreferences({theme:'dark',fontSize:17,language:'en',api_key:'not-a-real-key',notifications:{desktop:true,events:{failure:false}}},storage),true);
 const restored=readPreferences(storage);assert.equal(restored.theme,'dark');assert.equal(restored.fontSize,17);assert.equal(restored.language,'en');
 assert.equal(restored.notifications.desktop,true);assert.equal(restored.notifications.events.failure,false);assert.equal(restored.notifications.events.approval,true);
 assert.doesNotMatch(values.get(PREFERENCES_KEY),/not-a-real-key|api_key/);
 assert.deepEqual(normalizePreferences({theme:'invented',fontSize:100,language:'xx',notifications:{desktop:'true'}}),normalizePreferences(null));
});
test('blocked or corrupt browser storage uses defaults without throwing and reports failed writes',()=>{
 const blocked={getItem(){throw Error('blocked');},setItem(){throw Error('blocked');}};
 assert.deepEqual(readPreferences(blocked),normalizePreferences(null));assert.equal(writePreferences({},blocked),false);
 assert.deepEqual(readPreferences({getItem:()=>'{broken'}),normalizePreferences(null));
});
test('applying preferences changes document language, theme and font',()=>{
 const root={dataset:{},lang:''};applyPreferences({theme:'dark',fontSize:17,language:'en'},root);
 assert.deepEqual(root,{dataset:{theme:'dark',font:'17'},lang:'en'});
});
const local=(hour,minute=0)=>new Date(2026,9,30,hour,minute);
const preferences={notifications:{inApp:true,desktop:true,sound:true,quietHours:{enabled:true,start:'22:00',end:'08:00'}}};
test('quiet hours cross midnight, include start, exclude end, and retain in-app records',()=>{
 assert.equal(isQuietHours(preferences,local(21,59)),false);assert.equal(isQuietHours(preferences,local(22)),true);
 assert.equal(isQuietHours(preferences,local(7,59)),true);assert.equal(isQuietHours(preferences,local(8)),false);
 assert.deepEqual(notificationDecision(preferences,'approval',local(23),'granted'),{inApp:true,desktop:false,sound:false,quiet:true});
 assert.deepEqual(notificationDecision(preferences,'approval',local(9),'granted'),{inApp:true,desktop:true,sound:true,quiet:false});
});
test('equal quiet-hour boundaries mean all day; daytime range does not cover outside hours',()=>{
 assert.equal(isQuietHours({notifications:{quietHours:{enabled:true,start:'12:00',end:'12:00'}}},local(5)),true);
 const daytime={notifications:{quietHours:{enabled:true,start:'09:00',end:'17:00'}}};
 assert.equal(isQuietHours(daytime,local(9)),true);assert.equal(isQuietHours(daytime,local(17)),false);
});
test('desktop delivery requires granted permission and disabled or unknown events never dispatch',()=>{
 for(const permission of ['default','denied','unsupported'])assert.equal(notificationDecision(preferences,'completion',local(9),permission).desktop,false);
 assert.deepEqual(notificationDecision({notifications:{events:{failure:false},desktop:true,sound:true}},'failure',local(9),'granted'),{inApp:false,desktop:false,sound:false,quiet:false});
 assert.deepEqual(notificationDecision(preferences,'invented',local(9),'granted'),{inApp:false,desktop:false,sound:false,quiet:false});
});
test('English has every settings/menu/tutorial key and interpolates model lengths',()=>{
 assert.deepEqual(Object.keys(translations.en).sort(),Object.keys(translations['zh-CN']).sort());
 assert.equal(t('en','nav.memory'),'Memory and knowledge');assert.equal(t('en','app.newSession'),'New session');
 assert.equal(t('en','models.lengthSummary',{context:200000,output:16000}),'Context 200000 · Max output 16000');
 assert.equal(t('en','tutorial.task.title'),'Create a maintenance task');
});
