// Optional pinned upstream extractor. Python supplies only its new source copy.
const fs = require('node:fs');
const path = require('node:path');
process.env.CODEGRAPH_TELEMETRY = '0';
process.env.DO_NOT_TRACK = '1';
process.env.CODEGRAPH_NO_UPDATE_CHECK = '1';
process.env.CODEGRAPH_NO_DAEMON = '1';

async function extract(request) {
  if (!request || Object.keys(request).sort().join(',') !== 'files,output,snapshot'
      || !path.isAbsolute(request.snapshot) || !path.isAbsolute(request.output)
      || !Array.isArray(request.files) || !request.files.length || request.files.length > 20)
    throw new Error('Invalid explicit extraction request');
  const packages = path.join(__dirname,'node_modules','@colbymchenry');
  const pkg = require(path.join(packages,'codegraph','package.json'));
  if (pkg.version !== '1.6.2') throw new Error('CodeGraph version mismatch');
  // Resolve only this runtime's matching package, never the shim's home-cache fallback.
  const platform = path.join(packages,'codegraph-'+process.platform+'-'+process.arch);
  if (require(path.join(platform,'package.json')).version !== pkg.version) throw new Error('Platform version mismatch');
  const {CodeGraph} = require(path.join(platform,'lib','dist','index.js'));
  let cg;
  try {
    cg = await CodeGraph.init(request.snapshot);
    const result = await cg.indexAll();
    if (!result.success || result.filesErrored || result.errors?.length
        || result.filesIndexed !== request.files.length) throw new Error('Incomplete CodeGraph extraction');
    const files = cg.getFiles();
    if (files.length !== request.files.length || files.some(f => f.errors?.length)
        || files.some(f => !request.files.includes(f.path))) throw new Error('Unexpected indexed file scope');
    const raw = cg.getNodesInFiles(request.files);
    if (raw.length > 1000) throw new Error('Structure node limit exceeded');
    const ids = new Set(raw.map(n => n.id));
    const outgoing = cg.getOutgoingEdgesFrom([...ids]);
    if (outgoing.length > 4000) throw new Error('Structure edge limit exceeded');
    const nodes = raw.map(n => ({id:n.id, kind:n.kind, name:n.name, qualified_name:n.qualifiedName,
      path:n.filePath, language:n.language, start_line:n.startLine, end_line:n.endLine}));
    // This projection records the existence of a relation, not its call-site count.
    const edges = [...new Map(outgoing.filter(e => ids.has(e.source) && ids.has(e.target))
      .map(e => {const edge={source:e.source,target:e.target,kind:e.kind};return [JSON.stringify(edge),edge];})).values()];
    nodes.sort((a,b)=>a.id.localeCompare(b.id));
    edges.sort((a,b)=>JSON.stringify(a).localeCompare(JSON.stringify(b)));
    const graph = {schema_version:1, engine:{name:pkg.name,version:pkg.version}, nodes,edges,
      omitted_external_edges:outgoing.length-edges.length};
    const data = JSON.stringify(graph);
    if (Buffer.byteLength(data)>1024*1024) throw new Error('Structure graph size exceeded');
    fs.writeFileSync(request.output,data,{flag:'wx'});
    return {status:'extracted',files:files.length,nodes:nodes.length,edges:edges.length};
  } finally { if(cg)cg.close(); }
}

if (require.main === module) {
  let input='';process.stdin.setEncoding('utf8');
  process.stdin.on('data',part=>{input+=part;if(input.length>32768)process.exit(1);});
  process.stdin.on('end',async()=>{
    try { const result=await extract(JSON.parse(input));process.stdout.write(JSON.stringify(result)); }
    catch(error) { process.stderr.write(String(error.message));process.exitCode=1; }
  });
}
module.exports={extract};
