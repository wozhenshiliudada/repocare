"""Optional bounded CodeGraph export. Structure facts do not grant permission."""
from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess

from .git import RepositoryGateway
from .legacy_archive import archive_root
from .models import SafetyError, digest, safe_context_path
from .recovery import unlinked
from .source_memory import canonical
from .ui_backup import encoded, json_value, present, read_bytes, write

MANIFEST = 'structure-manifest.json'
GRAPH = 'structure-graph.json'
ENGINE = {'name': '@colbymchenry/codegraph', 'version': '1.6.2'}
KINDS = set('file module class struct interface trait protocol function method property field variable constant enum enum_member type_alias namespace parameter import export route component union'.split())
EDGES = set('contains calls imports exports extends implements references type_of returns instantiates overrides decorates navigates'.split())
HEX = re.compile(r'[0-9a-f]{64}')
MAX_FILE = 64*1024


def directory(path):
    root = archive_root(path)
    for parent in (root, *root.parents):
        if parent.name.casefold() in {'ui-state','report-memory-cases','memory-collections'} or present(parent/'run.json'):
            raise SafetyError('Structure artifacts cannot occupy existing runtime or memory namespaces')
    return root


def paths(values):
    if not isinstance(values, list) or not 1 <= len(values) <= 20:
        raise SafetyError('Select 1 to 20 exact source files')
    for value in values:
        if not isinstance(value, str) or len(value)>512 or any(ord(c)<32 for c in value):
            raise SafetyError('Invalid structure source path')
        safe_context_path(value)
        if (Path(value).suffix not in {'.py','.js','.ts'} or any(
                part.casefold() in {'node_modules','vendor','build','dist','.codegraph','.pi','ui-state'}
                or part.casefold().startswith(('.env','credentials','secrets')) for part in value.split('/'))):
            raise SafetyError('Select only explicit supported application source paths')
    if len({p.casefold() for p in values}) != len(values):
        raise SafetyError('Duplicate structure paths')
    return sorted(values)


def capture(repo, selected):
    gateway = RepositoryGateway(unlinked(repo))
    if gateway.command('status','--porcelain=v1','-z','--untracked-files=all'):
        raise SafetyError('Structure source checkout must be clean; preserve dirty work')
    tracked = set(os.fsdecode(p) for p in gateway.command('ls-files','-z').split(b'\0') if p)
    content, rows = {}, []
    for name in selected:
        if name not in tracked:
            raise SafetyError('Structure sources must be tracked files')
        data = read_bytes(gateway.safe_file(name),MAX_FILE)
        text = data.decode('utf-8')
        if '\x00' in text or not text.splitlines():
            raise SafetyError('Structure sources must be nonempty UTF-8 text')
        content[name] = data
        # Tree-sitter file nodes include the final empty EOF row after a newline.
        rows.append({'path':name,'source_sha256':digest(data),'lines':text.count('\n')+1})
    identity = {'root_sha256':digest(str(gateway.root).encode()),
                'common_dir_sha256':digest(str(gateway.common_dir).encode()),'head':gateway.head,
                'branch':gateway.command('symbolic-ref','-q','HEAD',check=False).decode().strip(), 'files':rows}
    return identity, content


def text(value, limit=512):
    return isinstance(value,str) and 0<len(value)<=limit and not any(ord(c)<32 for c in value)


def validate_graph(value, source):
    if (not isinstance(value,dict) or set(value)!={'schema_version','engine','nodes','edges','omitted_external_edges'}
            or type(value['schema_version']) is not int or value['schema_version']!=1 or value['engine']!=ENGINE
            or not isinstance(value['nodes'],list) or not 1<=len(value['nodes'])<=1000
            or not isinstance(value['edges'],list) or len(value['edges'])>4000
            or type(value['omitted_external_edges']) is not int or not 0<=value['omitted_external_edges']<=4000):
        raise SafetyError('Invalid bounded structure graph')
    files = {r['path']:r for r in source['files']}
    nodes = {}
    for row in value['nodes']:
        if (not isinstance(row,dict) or set(row)!={'id','kind','name','qualified_name','path','language','start_line','end_line'}
                or not text(row['id'],256) or row['id'] in nodes or not isinstance(row['kind'],str) or row['kind'] not in KINDS
                or not text(row['name']) or not text(row['qualified_name'],1024)
                or not isinstance(row['path'],str) or row['path'] not in files
                or not isinstance(row['language'],str) or row['language'] not in {'python','javascript','typescript'}
                or type(row['start_line']) is not int or type(row['end_line']) is not int
                or not 1<=row['start_line']<=row['end_line']<=files[row['path']]['lines']):
            raise SafetyError('Invalid structure node or source range')
        nodes[row['id']] = row
    if {n['path'] for n in nodes.values()} != set(files):
        raise SafetyError('Structure graph does not cover the selected file scope')
    seen = set()
    for edge in value['edges']:
        if (not isinstance(edge,dict) or set(edge)!={'source','target','kind'}
                or not isinstance(edge['source'],str) or not isinstance(edge['target'],str)
                or edge['source'] not in nodes or edge['target'] not in nodes
                or not isinstance(edge['kind'],str) or edge['kind'] not in EDGES):
            raise SafetyError('Invalid structure edge or missing endpoint')
        key = (edge['source'],edge['target'],edge['kind'])
        if key in seen:
            raise SafetyError('Duplicate structure edge')
        seen.add(key)
    return value


def run_engine(snapshot, selected, graph):
    adapter = Path(__file__).resolve().parents[2]/'runtime/codegraph/extract.cjs'
    request = {'snapshot':str(snapshot),'files':selected,'output':str(graph)}
    env = {**os.environ,'CODEGRAPH_TELEMETRY':'0','DO_NOT_TRACK':'1','CODEGRAPH_NO_UPDATE_CHECK':'1','CODEGRAPH_NO_DAEMON':'1'}
    try:
        result = subprocess.run(['node',str(adapter)],input=canonical(request),stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE,cwd=adapter.parent,env=env,timeout=120)
    except subprocess.TimeoutExpired as error:
        raise SafetyError('CodeGraph extraction timed out; retain incomplete artifacts') from error
    if result.returncode:
        write(graph.parent/'engine-stderr.txt',result.stderr[-32768:])
        raise SafetyError('CodeGraph extraction failed; retain incomplete artifacts and check optional runtime')


def build_structure(spec, repo, output):
    selection = json_value(read_bytes(spec,32768))
    if (not isinstance(selection,dict) or set(selection)!={'schema_version','paths'}
            or type(selection['schema_version']) is not int or selection['schema_version']!=1):
        raise SafetyError('Explicit structure selection schema1 required')
    selected = paths(selection['paths'])
    source, content = capture(repo,selected)
    root = directory(output)
    if present(root):
        raise SafetyError('Structure output must be a new directory; retain existing artifacts')
    root.mkdir(parents=True)
    write(root/'intent.json',encoded({'schema_version':1,'source':source,'engine':ENGINE,'runtime_authorized':False}))
    snapshot = root/'snapshot'
    for name,data in content.items(): write(snapshot/name,data)
    run_engine(snapshot,selected,root/GRAPH)
    raw = read_bytes(root/GRAPH,1024*1024)
    graph = validate_graph(json_value(raw),source)
    if capture(repo,selected)[0]!=source or any(read_bytes(snapshot/name,MAX_FILE)!=data for name,data in content.items()):
        raise SafetyError('Structure source changed during indexing; retain incomplete artifacts')
    data = {'schema_version':1,'source':source,'engine':ENGINE,'graph_sha256':digest(raw),
            'structural_claims_only':True,'runtime_authorized':False}
    write(root/MANIFEST,encoded({**data,'manifest_sha256':digest(canonical(data))}))
    verify_structure(root,repo)
    return {'status':'structure_index_created','files':len(selected),'nodes':len(graph['nodes']),
            'edges':len(graph['edges']),'runtime_authorized':False}


def load_structure(root,repo):
    root = directory(root)
    raw = read_bytes(root/MANIFEST,32768)
    manifest = json_value(raw)
    if (not isinstance(manifest,dict) or set(manifest)!={'schema_version','source','engine','graph_sha256',
            'structural_claims_only','runtime_authorized','manifest_sha256'}
            or type(manifest['schema_version']) is not int or manifest['schema_version']!=1
            or manifest['engine']!=ENGINE or manifest['structural_claims_only'] is not True
            or manifest['runtime_authorized'] is not False
            or not isinstance(manifest['graph_sha256'],str) or not HEX.fullmatch(manifest['graph_sha256'])
            or manifest['manifest_sha256']!=digest(canonical({k:v for k,v in manifest.items() if k!='manifest_sha256'}))):
        raise SafetyError('Invalid structure manifest or fingerprint')
    source = manifest['source']
    if (not isinstance(source,dict) or set(source)!={'root_sha256','common_dir_sha256','head','branch','files'}
            or not isinstance(source['files'],list) or not 1<=len(source['files'])<=20
            or not all(isinstance(source[k],str) and HEX.fullmatch(source[k]) for k in ('root_sha256','common_dir_sha256'))
            or not isinstance(source['head'],str) or not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}',source['head'])
            or not isinstance(source['branch'],str)):
        raise SafetyError('Invalid structure source binding')
    for row in source['files']:
        if (not isinstance(row,dict) or set(row)!={'path','source_sha256','lines'}
                or not isinstance(row['source_sha256'],str) or not HEX.fullmatch(row['source_sha256'])
                or type(row['lines']) is not int or row['lines']<1):
            raise SafetyError('Invalid structure file binding')
    selected = paths([r['path'] for r in source['files']])
    current, content = capture(repo,selected)
    if current!=source:
        raise SafetyError('Structure source binding changed; explicitly build a new index')
    for name,data in content.items():
        if read_bytes(root/'snapshot'/name,MAX_FILE)!=data:
            raise SafetyError('Structure source copy changed')
    graph_raw = read_bytes(root/GRAPH,1024*1024)
    if digest(graph_raw)!=manifest['graph_sha256']:
        raise SafetyError('Structure graph changed')
    return root,manifest,validate_graph(json_value(graph_raw),source),raw,graph_raw,selected


def unchanged(root,repo,manifest,raw,graph_raw,selected):
    source,content=capture(repo,selected)
    if (read_bytes(root/MANIFEST,32768)!=raw or read_bytes(root/GRAPH,1024*1024)!=graph_raw
            or source!=manifest['source']
            or any(read_bytes(root/'snapshot'/name,MAX_FILE)!=data for name,data in content.items())):
        raise SafetyError('Structure evidence changed during verification or query')


def verify_structure(root,repo):
    root,manifest,graph,raw,graph_raw,selected = load_structure(root,repo)
    unchanged(root,repo,manifest,raw,graph_raw,selected)
    return {'status':'verified_structure_index','files':len(selected),'nodes':len(graph['nodes']),
            'edges':len(graph['edges']),'graph_sha256':manifest['graph_sha256'],
            'manifest_sha256':manifest['manifest_sha256'],'structural_claims_only':True,'runtime_authorized':False}


def query_structure(root,repo,query,limit=5):
    if not text(query,128) or type(limit) is not int or not 1<=limit<=5:
        raise SafetyError('Structure query requires bounded literal text and limit 1 to 5')
    root,manifest,graph,raw,graph_raw,selected = load_structure(root,repo)
    needle = query.casefold()
    matches = sorted((n for n in graph['nodes'] if needle in n['name'].casefold()
                      or needle in n['qualified_name'].casefold() or needle in n['path'].casefold()),
                     key=lambda n:(n['name'].casefold()!=needle,n['path'],n['start_line'],n['id']))[:limit]
    ids = {n['id'] for n in matches}
    edges = [e for e in graph['edges'] if e['source'] in ids or e['target'] in ids][:50]
    related_ids = {e[k] for e in edges for k in ('source','target')}-ids
    fingerprints = {row['path']:row['source_sha256'] for row in manifest['source']['files']}
    def project(node): return {**node,'source_sha256':fingerprints[node['path']]}
    result = {'schema_version':1,'status':'verified_structure_query','query_sha256':digest(query.encode()),
              'manifest_sha256':manifest['manifest_sha256'],'graph_sha256':manifest['graph_sha256'],
              'hits':[project(n) for n in matches], 'edges':edges,
              'related_nodes':[project(n) for n in graph['nodes'] if n['id'] in related_ids],
              'structural_claims_only':True,'runtime_authorized':False}
    unchanged(root,repo,manifest,raw,graph_raw,selected)
    result={**result,'context_sha256':digest(canonical(result))}
    if len(canonical(result))>256*1024:
        raise SafetyError('Structure query result exceeds its bounded size')
    return result
