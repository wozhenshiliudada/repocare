import path from 'node:path';
import fs from 'node:fs';
import {build} from 'vite';

const candidate=process.argv[2],demo=path.resolve('../.demo');
if(!candidate||!path.isAbsolute(candidate))throw Error('Pass a new absolute output directory inside this worktree .demo');
const out=path.resolve(candidate);
if(!out.startsWith(demo+path.sep)||fs.existsSync(out))throw Error('Output must be a new owned .demo subdirectory');
fs.mkdirSync(out,{recursive:true});
await build({root:path.resolve('test'),configFile:false,logLevel:'error',build:{outDir:out,emptyOutDir:false,
 rolldownOptions:{input:path.resolve('test/ci-browser.html')}}});
process.stdout.write(JSON.stringify({synthetic:true,output:out,entry:'ci-browser.html',network_adapter:false})+'\n');
