import test from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {validateToolArguments} from '@earendil-works/pi-ai/utils/validation';
import {matchesSchemaRejection} from '../schema-rejection.mjs';
import {repairToolSchemas} from '../repair-tool-schemas.mjs';

const missingPath={name:'read_source',arguments:{query:'def im('}};
const missingPathError='Validation failed for tool "read_source":\n  - path: must have required properties path'
  +'\n\nReceived arguments:\n{\n  "query": "def im("\n}';
const receipt=call=>{
  try {validateToolArguments({name:call.name,parameters:repairToolSchemas[call.name]},call);}
  catch(error){return error.message;}
  throw new Error('Test fixture was not schema-invalid');
};

test('pinned SDK reproduces the actual C missing-path error byte for byte',()=>{
  assert.equal(receipt(missingPath),missingPathError);
  assert.equal(matchesSchemaRejection(missingPath,missingPathError),true);
});

test('missing edits, string edits and missing nested paths use the real SDK validator',()=>{
  for(const args of [{diagnosis:'fixture',risk:'local'},
    {diagnosis:'fixture',risk:'local',edits:['not an object']},
    {diagnosis:'fixture',risk:'local',edits:[{replacements:[]}]},
    {diagnosis:'fixture',risk:'local',edits:'[]'}]){
    const call={name:'propose_repair',arguments:args};
    assert.equal(matchesSchemaRejection(call,receipt(call)),true);
  }
});

test('altered arguments, validation text and tool names cannot masquerade as a receipt',()=>{
  assert.equal(matchesSchemaRejection({...missingPath,arguments:{query:'class im'}},missingPathError),false);
  assert.equal(matchesSchemaRejection(missingPath,missingPathError+'extra'),false);
  assert.equal(matchesSchemaRejection({...missingPath,name:'propose_repair'},missingPathError),false);
  assert.equal(matchesSchemaRejection({...missingPath,name:'bash'},missingPathError),false);
});

test('schema-valid foreign paths and coercible arguments cannot use SDK-rejection recovery',()=>{
  for(const call of [{name:'read_source',arguments:{path:'outside.py'}},
    {name:'read_source',arguments:{path:'calculator.py',start_line:'1'}},
    {name:'propose_repair',arguments:{diagnosis:'fixture',risk:'local',edits:[{path:'outside.py',replacements:[]}]}}]){
    assert.equal(matchesSchemaRejection(call,missingPathError),false);
  }
});

test('read-only batch CLI handles Unicode and emits only a boolean',()=>{
  const call={name:'read_source',arguments:{query:'中文 🧠'}};
  const pairs=[{call:missingPath,text:missingPathError},{call,text:receipt(call)}];
  const script=fileURLToPath(new URL('../schema-rejection.mjs',import.meta.url));
  const result=spawnSync(process.execPath,[script],{input:JSON.stringify(pairs),encoding:'utf8',timeout:20000});
  assert.equal(result.status,0,result.stderr);assert.equal(result.stdout,'true');assert.equal(result.stderr,'');
  pairs[1].text+='tampered';
  const denied=spawnSync(process.execPath,[script],{input:JSON.stringify(pairs),encoding:'utf8',timeout:20000});
  assert.equal(denied.status,0,denied.stderr);assert.equal(denied.stdout,'false');assert.equal(denied.stderr,'');
});
