// Fixed controller invocation. Project text/stdout never decides assertions.
// Normal authorized Node execution; this is not an OS or malicious-code sandbox.
import {readFileSync, writeFileSync, existsSync} from 'node:fs';
import {resolve, extname} from 'node:path';
import {pathToFileURL} from 'node:url';
import {createHash} from 'node:crypto';

const [requestPath, resultPath] = process.argv.slice(2);
const raw = readFileSync(requestPath);
const request = JSON.parse(raw.toString('utf8'));
const envelope = Object.fromEntries(['schema_version','case_id','token','contract_sha256'].map(k=>[k,request[k]]));
envelope.request_sha256 = createHash('sha256').update(raw).digest('hex');
// Capture primitives before executing target code; no target-side monkeypatch
// or console output can replace a successful invocation receipt accidentally.
const encode = JSON.stringify, own = Object.getOwnPropertyDescriptors;
const prototype = Object.getPrototypeOf, keys = Reflect.ownKeys;
const finish = process.exit.bind(process);
const NativeError = Error;
const jsonValue = value => {
  let nodes=0;
  const visit=(v,depth=0)=>{
    if(++nodes>4096||depth>12)return false;
    if(v===null||typeof v==='boolean')return true;
    if(typeof v==='number')return Number.isFinite(v)&&(!Number.isInteger(v)||Number.isSafeInteger(v));
    if(typeof v==='string')return !v.includes('\0');
    if(typeof v!=='object')return false;
    const descriptors=own(v);
    if(Array.isArray(v)){
      if(keys(v).length!==v.length+1)return false; // no holes or custom properties
      for(let i=0;i<v.length;i++)if(!descriptors[i]||!('value' in descriptors[i])||!visit(descriptors[i].value,depth+1))return false;
      return true;
    }
    if(prototype(v)!==Object.prototype&&prototype(v)!==null)return false;
    for(const key of keys(v)){
      if(typeof key!=='string'||key.includes('\0')||!descriptors[key].enumerable
        ||!('value' in descriptors[key])||!visit(descriptors[key].value,depth+1))return false;
    }
    return true;
  };
  try{return visit(value)&&Buffer.byteLength(encode(value))<=32768;}catch{return false;}
};
const target=resolve(request.workspace,request.module);
let actual={kind:'missing_module'};
if(existsSync(target)){
  let module;
  try {
    // Only the original Node native type-erasure subset is supported. A
    // missing native capability or syntax requiring transforms blocks import.
    if(['.ts','.mts','.cts'].includes(extname(target))&&process.features.typescript!=='strip')throw new Error('Native TypeScript stripping unavailable');
    module=await import(pathToFileURL(target).href);
  } catch { actual={kind:'import_error'}; }
  if(module){
    // Named ESM export, or an own public CommonJS module.exports property.
    // No property chains, dynamic getter traversal, eval, loader or scripts.
    let descriptor=own(module)[request.callable];
    if(!descriptor&&module.default&&typeof module.default==='object')descriptor=own(module.default)[request.callable];
    const fn=descriptor&&'value' in descriptor?descriptor.value:undefined;
    if(typeof fn!=='function')actual={kind:'missing_callable'};
    else {
      try {
        const value=await fn(...request.args);
        actual=jsonValue(value)?{kind:'json',value}:{kind:'invalid_result'};
      } catch(error) {
        // Runtime import errors and syntax failures cannot be business reds,
        // including when the case explicitly expects one of these exceptions.
        const code=error?.code,name=error?.name;
        // Node loader codes span more than MODULE_NOT_FOUND: package exports,
        // builtin modules, import attributes, require/ESM and TS stripping all
        // fail closed. Do not erase a loader code to generic Error and then
        // accidentally satisfy an expected business Error by its name.
        const loader=typeof code==='string'&&(code==='MODULE_NOT_FOUND'||code.startsWith('ERR_')&&
          /IMPORT|MODULE|PACKAGE|REQUIRE|LOADER|TYPESCRIPT|FILE_EXTENSION|ESM/.test(code));
        if(error instanceof NativeError&&(loader||name==='SyntaxError'))actual={kind:'import_error'};
        // A name-only oracle cannot distinguish other coded environment/OS
        // failures from business errors. Leave every coded exception unknown.
        else if(error instanceof NativeError&&code!==undefined)actual={kind:'execution_error'};
        else actual=error instanceof NativeError&&typeof name==='string'&&/^(?:[A-Za-z][A-Za-z0-9_]*\.)*[A-Za-z][A-Za-z0-9_]{0,79}$/.test(name)
          ?{kind:'exception',name}:{kind:'execution_error'};
      }
    }
  }
}
envelope.actual=actual;
try {
  let encoded=encode(envelope);
  if(Buffer.byteLength(encoded)>65536){envelope.actual={kind:'invalid_result'};encoded=encode(envelope);}
  writeFileSync(resultPath,encoded,{flag:'wx'});
} catch {finish(4);}
// Open timers/handles in target modules do not turn an already captured result
// into an unbounded runner. An unsettled Promise remains subject to parent timeout.
finish(0);
