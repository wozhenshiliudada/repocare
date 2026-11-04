import {build} from 'vite';
import {copyFile,mkdir,readdir} from 'node:fs/promises';
import path from 'node:path';
const out=path.resolve('../.demo/watch-browser');
await mkdir(out,{recursive:true});
await build({configFile:false,build:{outDir:out,emptyOutDir:false,rolldownOptions:{input:'test/acceptance-watch-browser.html'}}});
const assets=path.resolve('../src/repo_maintainer/workbench_ui/assets');
const css=(await readdir(assets)).find(n=>n.endsWith('.css'));
await copyFile(path.join(assets,css),path.join(out,'fixture.css'));
