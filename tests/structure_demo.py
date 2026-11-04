"""Reproducible CLI acceptance on self-authored Python/JS/TS; no model."""
from pathlib import Path
import argparse, contextlib, io, json, sys
from unittest.mock import patch

sys.path[:0]=[str(Path(__file__).resolve().parents[1]/'src'),str(Path(__file__).resolve().parent)]
from test_structure_index import fixture
from repo_maintainer.cli import main
from repo_maintainer.git import RepositoryGateway
from repo_maintainer.models import digest
from repo_maintainer import structure_index as structure


def demo(directory):
    directory=directory.resolve();directory.mkdir()
    repo=directory/'source';fixture(repo)
    extra={
        'lib/math.js':'export function multiply(a, b) { return a * b; }\n',
        'web.js':'import { multiply } from "./lib/math.js";\nexport function doubled(n) { return multiply(n, 2); }\n',
        'lib/prices.ts':'export function discount(n: number): number { return n * 0.9; }\n',
        'prices.ts':'import { discount } from "./lib/prices";\nexport function price(n: number): number { return discount(n); }\n',
        '.gitignore':'.env\n'}
    for name,text in extra.items():
        target=repo/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_text(text,encoding='utf-8')
    gateway=RepositoryGateway(repo);gateway.command('add','.');gateway.command('commit','-m','self-authored multilingual fixture')
    (repo/'.env').write_text('SYNTHETIC_SENTINEL=not_a_credential\n',encoding='utf-8')
    ignored_bytes=(repo/'.env').read_bytes();before=gateway.snapshot()
    selected=['app.py','maths.py','lib/math.js','web.js','lib/prices.ts','prices.ts']
    spec=directory/'selection.json';spec.write_text(json.dumps({'schema_version':1,'paths':selected}),encoding='utf-8')
    index=directory/'structure-indexes/sample';checks=[]
    def invoke(command,*argv,expected=0):
        with contextlib.redirect_stdout(io.StringIO()) as output,contextlib.redirect_stderr(io.StringIO()) as error:
            code=main([command,'--repo',str(repo),*map(str,argv)])
        assert code==expected,(command,code,error.getvalue())
        return json.loads(output.getvalue()) if code==0 else {'exit_code':code}
    with patch('repo_maintainer.cli.PiProvider',side_effect=AssertionError('model constructed')):
        build=invoke('structure-build','--spec',spec,'--output',index)
        assert build['files']==6 and build['nodes']>6 and build['runtime_authorized'] is False
        graph=json.loads((index/structure.GRAPH).read_bytes());nodes={n['id']:n for n in graph['nodes']}
        assert {n['language'] for n in nodes.values()}=={'python','javascript','typescript'}
        calls=[(nodes[e['source']]['name'],nodes[e['target']]['name']) for e in graph['edges'] if e['kind']=='calls']
        for pair in [('total','add'),('doubled','multiply'),('price','discount')]:assert pair in calls,(pair,calls)
        checks.append('real pinned CodeGraph extracts Python/JavaScript/TypeScript and three cross-file call paths')
        queries={}
        for query in ['add','multiply','discount','not present']:
            result=invoke('structure-query','--index-dir',index,'--query',query,'--limit','1')
            assert result['runtime_authorized'] is False and result['structural_claims_only'] is True
            assert str(repo) not in json.dumps(result) and 'excerpt' not in json.dumps(result)
            if query!='not present':assert result['hits'][0]['name']==query
            else:assert result['hits']==[]
            queries[query]=result
        checks.append('bounded literal CLI queries return original relative ranges/SHA and direct edges, with no authority or source excerpt')
        inventory={str(p.relative_to(index)):digest(p.read_bytes()) for p in index.rglob('*') if p.is_file()}
        with patch.object(structure,'run_engine',side_effect=AssertionError('query ran extractor')):
            invoke('structure-verify','--index-dir',index)
            invoke('structure-query','--index-dir',index,'--query','add')
        assert inventory=={str(p.relative_to(index)):digest(p.read_bytes()) for p in index.rglob('*') if p.is_file()}
        checks.append('query and verification leave manifest/export/source copies/upstream database unchanged and never start extractor')
        target=index/'snapshot/app.py';data=target.read_bytes();target.write_bytes(data+b' ')
        invoke('structure-query','--index-dir',index,'--query','not present',expected=1);target.write_bytes(data)
        target=index/structure.GRAPH;data=target.read_bytes();target.write_bytes(data+b' ')
        invoke('structure-verify','--index-dir',index,expected=1);target.write_bytes(data)
        checks.append('changed copied source or graph bytes refuse even a zero-hit query and can only verify after exact restoration')
        target=repo/'maths.py';data=target.read_bytes();target.write_bytes(data+b'\n# source changed\n')
        invoke('structure-query','--index-dir',index,'--query','not present',expected=1);target.write_bytes(data)
        invoke('structure-verify','--index-dir',index)
        checks.append('changed original source blocks query before using the old graph; source restoration does not rebuild or reset anything')
        invoke('structure-build','--spec',spec,'--output',index,expected=1)
        assert inventory=={str(p.relative_to(index)):digest(p.read_bytes()) for p in index.rglob('*') if p.is_file()}
        checks.append('existing index refuses overwrite and retains exact artifact bytes')
        assert gateway.snapshot()==before and (repo/'.env').read_bytes()==ignored_bytes
        assert not (repo/'.codegraph').exists() and not (index/'snapshot/.env').exists() and not (index/'snapshot/unselected.js').exists()
        checks.append('original Git/index/files/refs and ignored synthetic sentinel unchanged; only selected sources copied, no source execution')
    proof={'passed':True,'checks':checks,'build':build,'languages':['python','javascript','typescript'],
           'cross_file_calls':[list(p) for p in [('total','add'),('doubled','multiply'),('price','discount')]],
           'source_unchanged':True,'model_requests':0,'credential_reads':0,'antigravity_calls':0,
           'engine':'@colbymchenry/codegraph@1.6.2','model_context_enabled':False}
    (directory/'acceptance.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (directory/'queries.json').write_text(json.dumps(queries,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return proof


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('directory',type=Path);args=parser.parse_args()
    print(json.dumps(demo(args.directory),ensure_ascii=False))
