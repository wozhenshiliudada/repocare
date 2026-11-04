// Transform TSX in memory for real SSR/component-handler tests. No Vite build,
// dist writes, provider, browser process or application service is involved.
import {registerHooks} from 'node:module';
import {existsSync,readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {transformSync} from 'rolldown/utils';

registerHooks({
 resolve(specifier,context,nextResolve){
  if(specifier.startsWith('.')){
   for(const suffix of ['.tsx','.ts']){
    const url=new URL(specifier+suffix,context.parentURL);
    if(existsSync(url))return {url:url.href,shortCircuit:true};
   }
  }
  return nextResolve(specifier,context);
 },
 load(url,context,nextLoad){
  // Component-handler tests have no browser stylesheet loader. Production
  // styles remain bundled by Vite; importing one here must have no side effect.
  if(url.endsWith('.css'))return {format:'module',source:'export default {};',shortCircuit:true};
  if(/\.(tsx|ts)$/.test(url)){
   const result=transformSync(fileURLToPath(url),readFileSync(new URL(url),'utf8'),{jsx:{runtime:'classic'}});
   if(result.errors?.length)throw Error(JSON.stringify(result.errors));
   return {format:'module',source:result.code,shortCircuit:true};
  }
  return nextLoad(url,context);
 },
});
