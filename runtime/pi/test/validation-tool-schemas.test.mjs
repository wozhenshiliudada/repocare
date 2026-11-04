import test from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {validateToolArguments} from '@earendil-works/pi-ai/utils/validation';
import {validationToolSchemas,validateValidationProposal,validationProposalCases,matchesValidationReceipt,
  VALIDATION_PURPOSE,VALIDATION_DATA_SCHEMA} from '../validation-tool-schemas.mjs';
import {repairToolSchemas} from '../repair-tool-schemas.mjs';
import {matchesSchemaRejection} from '../schema-rejection.mjs';
import {planningTaskSession} from '../task-session.mjs';
import {experienceHash} from '../experience-context.mjs';

const paths=['calculator.py'];
const sample=()=>({requirement:'Return signed addition',rationale:'Addition requirement',limitations:'Python only',
  clarification:false,cases:[{id:'negative',module:'calculator.py',callable:'add',args:[-2,1],kwargs:{},
    expect:{kind:'json',value:-1},missing_behavior:false}]});
const receipt=args=>({accepted:true,purpose:VALIDATION_PURPOSE,proposal_sha256:experienceHash(args),runtime_authorized:false});

test('validation tools are separate and proposal validator returns identical JSON data',()=>{
  assert.deepEqual(Object.keys(validationToolSchemas).sort(),['propose_validation','read_source']);
  assert.deepEqual(Object.keys(repairToolSchemas).sort(),
    ['propose_repair','read_approved_source','read_source','search_approved_sources']);
  const data=sample();assert.equal(validateValidationProposal(data,paths),data);
  assert.equal(matchesValidationReceipt({name:'propose_validation',arguments:data},receipt(data),paths),true);
});

test('node data proposals accept JS/native TS exports and reject mixed, declarations and keyword calls',()=>{
 for(const module of ['calc.js','calc.mjs','calc.cjs','calc.ts','calc.mts','calc.cts']){
  const data=sample();data.cases[0].module=module;
  assert.equal(validateValidationProposal(data,[module],VALIDATION_DATA_SCHEMA),data);
  const keyword=structuredClone(data);keyword.cases[0].kwargs={a:1};
  assert.throws(()=>validateValidationProposal(keyword,[module],VALIDATION_DATA_SCHEMA));
 }
 for(const module of ['calc.tsx','calc.d.ts','calc.d.mts','calc.d.cts']){
  const data=sample();data.cases[0].module=module;
  assert.throws(()=>validateValidationProposal(data,[module],VALIDATION_DATA_SCHEMA));
 }
 const mixed=sample();mixed.cases.push({...mixed.cases[0],id:'node',module:'calc.js'});
 assert.throws(()=>validateValidationProposal(mixed,['calculator.py','calc.js'],VALIDATION_DATA_SCHEMA));
});

test('legacy validation proposal schema still rejects an in-scope JS case',()=>{
 const data=sample();data.cases[0].module='calculator.js';
 assert.throws(()=>validateToolArguments({name:'propose_validation',parameters:validationToolSchemas.propose_validation},
   {name:'propose_validation',arguments:data}));
});
test('investigation schemas are exact, bounded and separate from validation tools',()=>{
  for(const [name,args] of [['search_approved_sources',{query:'OFFSET_'}],
    ['search_approved_sources',{query:'OFFSET_',max_hits:100}],
    ['read_approved_source',{path:'logic/hidden.py'}],
    ['read_approved_source',{path:'logic/hidden.py',start_line:1,max_lines:200}]]){
    assert.doesNotThrow(()=>validateToolArguments({name,parameters:repairToolSchemas[name]}, {name,arguments:args}));
    assert.equal(name in validationToolSchemas,false);
  }
  for(const [name,args] of [['search_approved_sources',{}],['search_approved_sources',{query:''}],
    ['search_approved_sources',{query:'x',max_hits:101}],['read_approved_source',{}],
    ['read_approved_source',{path:''}],['read_approved_source',{path:'x',max_lines:201}]]){
    assert.throws(()=>validateToolArguments({name,parameters:repairToolSchemas[name]}, {name,arguments:args}));
  }
});
test('clarification may submit zero cases, otherwise one through twenty four required',()=>{
  const data={...sample(),clarification:true,cases:[]};assert.equal(validateValidationProposal(data,paths),data);
  assert.throws(()=>validateValidationProposal({...data,clarification:false},paths));
  assert.throws(()=>validateValidationProposal({...data,cases:Array(25).fill(sample().cases[0])},paths));
});
test('exact fields, source modules, bounded JSON and current case semantics are enforced',()=>{
  for(const change of [d=>d.edits=[],d=>d.clarification='false',d=>d.cases[0].extra=true,
    d=>d.cases[0].module='outside.py',d=>d.cases[0].module='calculator.js',d=>d.cases.push(structuredClone(d.cases[0])),
    d=>d.cases[0].kwargs={_private:1},d=>d.cases[0].expect={kind:'exception',name:'bad-name'},
    d=>d.cases[0].args=[NaN],d=>d.cases[0].args=[Number.MAX_SAFE_INTEGER+1],
    d=>d.rationale=' ',d=>d.requirement='x'.repeat(8193),d=>d.limitations='\0',
    d=>d.cases[0].expect.value='x'.repeat(32769)]){
    const data=sample();change(data);assert.throws(()=>validateValidationProposal(data,paths));
  }
});
test('raw cases preserve floating point spelling and remain mutually exclusive with cases',()=>{
  const data=sample(),cases=data.cases;delete data.cases;
  data.cases_json=JSON.stringify(cases).replace('[-2,1]','[-0.0,1.0]');
  assert.equal(validateValidationProposal(data,paths),data);
  assert.equal(Object.is(validationProposalCases(data)[0].args[0],-0),true);
  assert.equal(data.cases_json.includes('1.0'),true);
  assert.throws(()=>validateValidationProposal({...data,cases},paths));
  assert.throws(()=>validateValidationProposal({...data,cases_json:'{}'},paths));
  assert.throws(()=>validateValidationProposal({...data,cases_json:'not JSON'},paths));
});
test('raw exponent tokens preserve Python numeric type and per-value canonical size authority',()=>{
  const data=sample(),cases=data.cases;delete data.cases;
  data.cases_json=JSON.stringify(cases).replace('[-2,1]',`[${Array(1500).fill('1e20').join(',')}]`)
    .replace('"value":-1','"value":1e20');
  assert.equal(validateValidationProposal(data,paths),data);
  assert.equal(validationProposalCases(data)[0].args.length,1500);
  assert.equal(validationProposalCases(data)[0].expect.value,1e20);
  assert.ok(Buffer.byteLength(JSON.stringify(validationProposalCases(data)[0].args))>32768);
  const ordinary=sample();ordinary.cases[0].args=[1e20];
  assert.throws(()=>validateValidationProposal(ordinary,paths));
});
test('accepted receipt binds original args, purpose and read-only stage',()=>{
  const data=sample(),call={name:'propose_validation',arguments:data};
  for(const patch of [{purpose:'repair'},{runtime_authorized:true},{extra:0},{proposal_sha256:'0'.repeat(64)},
    {accepted:false}])assert.equal(matchesValidationReceipt(call,{...receipt(data),...patch},paths),false);
  assert.equal(matchesValidationReceipt({...call,arguments:{...data,limitations:'changed'}},receipt(data),paths),false);
});
test('SDK rejection receipts cannot choose or cross stage tool qualifications',()=>{
  const call={name:'propose_validation',arguments:{requirement:'missing fields'}};
  let error;try{validateToolArguments({name:call.name,parameters:validationToolSchemas[call.name]},call);}
  catch(e){error=e.message;}
  assert.equal(matchesSchemaRejection(call,error,VALIDATION_PURPOSE),true);
  assert.equal(matchesSchemaRejection(call,error),false);
  assert.equal(matchesSchemaRejection({name:'propose_repair',arguments:{}},error,VALIDATION_PURPOSE),false);
  const script=fileURLToPath(new URL('../schema-rejection.mjs',import.meta.url));
  const payload={paths,rejections:[{call,text:error}],proposals:[]};
  const result=spawnSync(process.execPath,[script,'--purpose',VALIDATION_PURPOSE],{input:JSON.stringify(payload),encoding:'utf8'});
  assert.equal(result.status,0);assert.equal(result.stdout,'true');assert.equal(result.stderr,'');
  const own=spawnSync(process.execPath,[script],{input:JSON.stringify({...payload,purpose:VALIDATION_PURPOSE}),encoding:'utf8'});
  assert.notEqual(own.status,0);assert.equal(own.stdout,'');
});
test('purpose context is checked before either open or create SessionManager operation',async()=>{
  for(const mode of ['open','create']){
    const legacy={provider:'p',model:'m',paths};
    const validation={...legacy,schema_version:2,purpose:VALIDATION_PURPOSE};
    for(const [context,purpose] of [[legacy,VALIDATION_PURPOSE],[validation,'repair'],
      [{...validation,purpose:'repair'},VALIDATION_PURPOSE],[{...validation,schema_version:true},VALIDATION_PURPOSE]])
      await assert.rejects(planningTaskSession({mode,binding:{},context},'p','m',paths,purpose),/context changed/);
    await assert.rejects(planningTaskSession({mode,binding:{},context:validation},'p','other',paths,VALIDATION_PURPOSE),/context changed/);
    for(const invalidPaths of [[],['calculator.py','CALCULATOR.py'],Array(21).fill('calculator.py')])
      await assert.rejects(planningTaskSession({mode,binding:{},context:{...validation,paths:invalidPaths}},
        'p','m',invalidPaths,VALIDATION_PURPOSE),/context changed/);
  }
});
