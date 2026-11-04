// A distinct, read-only planning purpose. These schemas never extend repair tools.
import {Type} from 'typebox';
import {Compile} from 'typebox/compile';
import {repairToolSchemas} from './repair-tool-schemas.mjs';
import {experienceHash} from './experience-context.mjs';

export const VALIDATION_PURPOSE = 'validation-proposal-v1';
export const VALIDATION_DATA_SCHEMA = 'data-behavior-v2';
const publicName = '^[A-Za-z][A-Za-z0-9_]{0,79}$';
const identifier = () => Type.String({pattern:publicName});
const caseSchema = (data=false) => Type.Object({
  id:identifier(), module:Type.String({maxLength:256,pattern:data?'\\.(?:py|js|mjs|cjs|ts|mts|cts)$':'\\.py$'}), callable:identifier(),
  args:Type.Array(Type.Unknown()), kwargs:Type.Record(Type.String({pattern:publicName}),Type.Unknown(),{additionalProperties:false}),
  expect:Type.Union([
    Type.Object({kind:Type.Literal('json'),value:Type.Unknown()},{additionalProperties:false}),
    Type.Object({kind:Type.Literal('exception'),name:Type.String({maxLength:160,
      pattern:'^(?:[A-Za-z][A-Za-z0-9_]*\\.)*[A-Za-z][A-Za-z0-9_]{0,79}$'})},{additionalProperties:false}),
  ]), missing_behavior:Type.Boolean(),
},{additionalProperties:false});
const proposal = (clarification, rawCases=false, data=false) => Type.Object({
  requirement:Type.String({minLength:1,maxLength:8192}),
  rationale:Type.String({minLength:1,maxLength:8192}), limitations:Type.String({maxLength:8192}),
  clarification:Type.Literal(clarification),
  ...(rawCases?{cases_json:Type.String({minLength:2,maxLength:262144})}:
    {cases:Type.Array(caseSchema(data),{minItems:clarification?0:1,maxItems:24})}),
},{additionalProperties:false});
export const validationToolSchemas = {
  read_source:{...repairToolSchemas.read_source,additionalProperties:false},
  propose_validation:Type.Union([proposal(false),proposal(true),proposal(false,true),proposal(true,true)]),
};
export const dataValidationToolSchemas = {
  read_source:{...repairToolSchemas.read_source,additionalProperties:false},
  propose_validation:Type.Union([proposal(false,false,true),proposal(true,false,true),proposal(false,true,true),proposal(true,true,true)]),
};
export function validationSchemas(schema='python-behavior-v1') {
  if(schema==='python-behavior-v1')return validationToolSchemas;
  if(schema===VALIDATION_DATA_SCHEMA)return dataValidationToolSchemas;
  throw new Error('Unsupported validation schema');
}
const validators = new Map([
  ['python-behavior-v1',[Compile(validationToolSchemas.propose_validation),Compile(Type.Array(caseSchema(),{maxItems:24}))]],
  [VALIDATION_DATA_SCHEMA,[Compile(dataValidationToolSchemas.propose_validation),Compile(Type.Array(caseSchema(true),{maxItems:24}))]],
]);
const fail = () => {throw new Error('Invalid validation proposal');};
const boundedJson = (value, rawCases=false) => {
  let nodes=0;
  const visit=(v,depth=0)=>{
    if(++nodes>4096||depth>12)fail();
    if(v===null||typeof v==='boolean')return;
    if(typeof v==='number'){
      if(!Number.isFinite(v)||(!rawCases&&Number.isInteger(v)&&!Number.isSafeInteger(v)))fail();return;
    }
    if(typeof v==='string'){if(v.includes('\0'))fail();return;}
    if(Array.isArray(v)){for(const x of v)visit(x,depth+1);return;}
    if(v&&typeof v==='object'&&Object.getPrototypeOf(v)===Object.prototype){
      for(const [k,x] of Object.entries(v)){if(k.includes('\0'))fail();visit(x,depth+1);}return;
    }
    fail();
  };
  visit(value);
  // Raw numeric tokens belong to the Python controller. JS would turn 1e20
  // into an integer and expand it to 21 digits, changing both type and size.
  if(!rawCases&&Buffer.byteLength(JSON.stringify(value))>32768)fail();
};

// Returns the same JSON data. The controller independently constructs its contract.
export function validationProposalCases(value) {
  try {return Object.hasOwn(value,'cases_json')?JSON.parse(value.cases_json):value.cases;}
  catch {fail();}
}
export function validateValidationProposal(value, paths, schema='python-behavior-v1') {
  if(!validators.has(schema))fail();
  const [validator,casesValidator]=validators.get(schema);
  if(!validator.Check(value)||!Array.isArray(paths)||!paths.length||new Set(paths).size!==paths.length
    ||!paths.every(p=>typeof p==='string')||Buffer.byteLength(JSON.stringify(value))>262144)fail();
  for(const field of ['requirement','rationale','limitations'])
    if(value[field].includes('\0')||Buffer.byteLength(value[field])>8192)fail();
  if(!value.requirement.trim()||!value.rationale.trim())fail();
  const cases=validationProposalCases(value);
  const rawCases=Object.hasOwn(value,'cases_json');
  if(rawCases&&Buffer.byteLength(value.cases_json)>262144)fail();
  if(!casesValidator.Check(cases)||(!value.clarification&&!cases.length))fail();
  const ids=new Set();
  const runtimes=new Set();
  for(const c of cases){
    if(ids.has(c.id)||!paths.includes(c.module)||/\.d\.(?:ts|mts|cts)$/.test(c.module))fail();
    ids.add(c.id);
    const node=!c.module.endsWith('.py');runtimes.add(node?'node':'python');
    if(node&&Object.keys(c.kwargs).length)fail();
    boundedJson(c.args,rawCases);boundedJson(c.kwargs,rawCases);
    if(c.expect.kind==='json')boundedJson(c.expect.value,rawCases);
  }
  if(runtimes.size>1)fail();
  return value;
}

export function matchesValidationReceipt(call, receipt, paths, schema='python-behavior-v1') {
  try {
    if(call?.name!=='propose_validation')return false;
    validateValidationProposal(call.arguments,paths,schema);
    return receipt&&Object.keys(receipt).sort().join()==='accepted,proposal_sha256,purpose,runtime_authorized'
      &&receipt.accepted===true&&receipt.purpose===VALIDATION_PURPOSE&&receipt.runtime_authorized===false
      &&receipt.proposal_sha256===experienceHash(call.arguments);
  } catch {return false;}
}
