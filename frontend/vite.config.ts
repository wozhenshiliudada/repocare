import {defineConfig} from 'vite';
import react from '@vitejs/plugin-react';
import {createHash} from 'node:crypto';
import {readFileSync} from 'node:fs';

const guide=new URL('../docs/tutorials/click-guide-20261031/',import.meta.url);

export default defineConfig({
  plugins:[react(),{
    name:'repocare-illustrated-guide',
    apply:'build',
    generateBundle(){
      const manifest=readFileSync(new URL('MANIFEST.json',guide));
      const {files,unique_screenshots}=JSON.parse(manifest.toString('utf8'));
      const names=new Set<string>();
      for(const {path,bytes,sha256} of files){
        if(!/^(?:index\.html|README\.md|images\/[0-9]+-[a-z0-9-]+\.jpg)$/.test(path)||names.has(path))throw Error('Invalid illustrated guide asset');
        names.add(path);
        const source=readFileSync(new URL(path,guide));
        if(source.length!==bytes||createHash('sha256').update(source).digest('hex')!==sha256)throw Error('Illustrated guide asset differs from manifest: '+path);
        this.emitFile({type:'asset',fileName:'tutorial/'+path,source});
      }
      if(!names.has('index.html')||!names.has('README.md')||names.size!==unique_screenshots+2)throw Error('Incomplete illustrated guide');
      this.emitFile({type:'asset',fileName:'tutorial/MANIFEST.json',source:manifest});
    },
  }],
  build:{outDir:'../src/repo_maintainer/workbench_ui',emptyOutDir:true,assetsInlineLimit:0,sourcemap:false},
});
