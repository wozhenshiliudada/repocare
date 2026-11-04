// Local validation only: no AgentSession, credentials, provider, tools or filesystem actions.
import {pathToFileURL} from 'node:url';
import {readFileSync} from 'node:fs';
import {validateToolArguments} from '@earendil-works/pi-ai/utils/validation';
import {repairToolSchemas} from './repair-tool-schemas.mjs';
import {validationSchemas, matchesValidationReceipt, VALIDATION_PURPOSE,VALIDATION_DATA_SCHEMA} from './validation-tool-schemas.mjs';

const installed=JSON.parse(readFileSync(new URL('../package.json',import.meta.resolve('@earendil-works/pi-ai')),'utf8'));
if (installed.name!=='@earendil-works/pi-ai' || installed.version!=='1.0.4') throw new Error('Expected pinned Pi AI 1.0.4');

export function matchesSchemaRejection(call, text, purpose='repair', schema='python-behavior-v1') {
  const schemas=purpose==='repair'?repairToolSchemas:purpose===VALIDATION_PURPOSE?validationSchemas(schema):null;
  if (!schemas || !call || !Object.hasOwn(schemas, call.name)) return false;
  try {
    validateToolArguments({name:call.name, parameters:schemas[call.name]}, call);
    return false; // Schema-valid calls can never use an SDK rejection to bypass scope checks.
  } catch (error) {
    return typeof text === 'string' && text.startsWith(`Validation failed for tool "${call.name}":\n`)
      && error instanceof Error && error.message === text;
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    let input='';
    process.stdin.setEncoding('utf8');
    for await (const chunk of process.stdin) {
      input+=chunk;
      if (Buffer.byteLength(input)>64*1024*1024) throw new Error('Schema receipt input too large');
    }
    const args=process.argv.slice(2);
    if(args.length && (args.length!==2||args[0]!=='--purpose'))throw new Error('Invalid stage selector');
    const purpose=args.length?args[1]:'repair';
    if(!['repair',VALIDATION_PURPOSE].includes(purpose))throw new Error('Unknown stage');
    const body=JSON.parse(input);
    const pairs=purpose==='repair'?body:body?.rejections;
    const schema=body?.validation_schema??'python-behavior-v1';
    if (!Array.isArray(pairs) || pairs.length>20000) throw new Error('Invalid schema receipt batch');
    if(purpose===VALIDATION_PURPOSE && (!body||!['paths,proposals,rejections','paths,proposals,rejections,validation_schema'].includes(Object.keys(body).sort().join())
      ||!['python-behavior-v1',VALIDATION_DATA_SCHEMA].includes(schema)
      ||!Array.isArray(body.proposals)||body.proposals.length>20000||!Array.isArray(body.paths)))
      throw new Error('Invalid proposal receipt batch');
    process.stdout.write(JSON.stringify(pairs.every(pair=>matchesSchemaRejection(pair.call,pair.text,purpose,schema))
      &&(purpose==='repair'||body.proposals.every(pair=>matchesValidationReceipt(pair.call,pair.receipt,body.paths,schema)))));
  } catch {
    // Never emit untrusted arguments, file contents or dependency errors to the caller.
    process.exitCode=1;
  }
}
