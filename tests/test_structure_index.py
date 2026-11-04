"""Source preservation, real upstream extraction and closed structural queries."""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import subprocess
import shutil
import tempfile
import unittest
from unittest.mock import patch

from repo_maintainer import structure_index as structure
from repo_maintainer.cli import main
from repo_maintainer.git import RepositoryGateway
from repo_maintainer.models import SafetyError, digest
from repo_maintainer.source_memory import canonical


def fixture(root):
    root.mkdir()
    (root/'maths.py').write_text('def add(a, b):\n    return a + b\n',encoding='utf-8')
    (root/'app.py').write_text('from maths import add\n\ndef total(a, b):\n    return add(a, b)\n',encoding='utf-8')
    (root/'unselected.js').write_text('throw new Error("must not execute");\n',encoding='utf-8')
    for argv in [('init',),('config','core.autocrlf','false'),('config','user.name','Fixture'),
                 ('config','user.email','fixture@example.invalid'),('add','.'),('commit','-m','fixture')]:
        subprocess.run(['git','-C',str(root),*argv],check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)


class StructureIndexTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name);self.repo=self.base/'source';fixture(self.repo)
        self.spec=self.base/'selection.json';self.output=self.base/'structure-indexes'/'sample'
        self.spec.write_text(json.dumps({'schema_version':1,'paths':['app.py','maths.py']}),encoding='utf-8')

    @staticmethod
    def fake_engine(snapshot,selected,output):
        nodes=[dict(id='add',kind='function',name='add',qualified_name='add',path='maths.py',language='python',start_line=1,end_line=2),
               dict(id='total',kind='function',name='total',qualified_name='total',path='app.py',language='python',start_line=3,end_line=4)]
        graph=dict(schema_version=1,engine=structure.ENGINE,nodes=nodes,
                   edges=[dict(source='total',target='add',kind='calls')],omitted_external_edges=0)
        output.write_bytes(canonical(graph))

    def build(self):
        with patch.object(structure,'run_engine',side_effect=self.fake_engine):
            return structure.build_structure(self.spec,self.repo,self.output)

    def graph(self): return json.loads((self.output/structure.GRAPH).read_bytes())
    def manifest(self): return json.loads((self.output/structure.MANIFEST).read_bytes())

    def reseal(self, graph=None, manifest=None):
        value=manifest or self.manifest()
        if graph is not None:
            data=canonical(graph);(self.output/structure.GRAPH).write_bytes(data);value['graph_sha256']=digest(data)
        value.pop('manifest_sha256',None);value['manifest_sha256']=digest(canonical(value))
        (self.output/structure.MANIFEST).write_bytes(canonical(value))

    def test_explicit_source_copy_queries_original_ranges_and_zero_authority(self):
        before=RepositoryGateway(self.repo).snapshot();self.build()
        files={str(p.relative_to(self.output)):p.read_bytes() for p in self.output.rglob('*') if p.is_file()}
        with (patch.object(structure,'run_engine',side_effect=AssertionError('query invoked engine')),
              patch('repo_maintainer.pi_provider.PiProvider',side_effect=AssertionError('provider created'))):
            result=structure.query_structure(self.output,self.repo,'add',1)
            verified=structure.verify_structure(self.output,self.repo)
        self.assertEqual('add',result['hits'][0]['id']);self.assertEqual(['total'],[r['id'] for r in result['related_nodes']])
        self.assertTrue(result['structural_claims_only']);self.assertFalse(result['runtime_authorized'])
        self.assertNotIn('excerpt',result['hits'][0]);self.assertNotIn(str(self.repo),json.dumps(result))
        self.assertEqual(digest(canonical({k:v for k,v in result.items() if k!='context_sha256'})),result['context_sha256'])
        self.assertEqual('verified_structure_index',verified['status'])
        self.assertEqual(files,{str(p.relative_to(self.output)):p.read_bytes() for p in self.output.rglob('*') if p.is_file()})
        self.assertEqual(before,RepositoryGateway(self.repo).snapshot());self.assertFalse((self.repo/'.codegraph').exists())
        self.assertFalse((self.output/'snapshot/unselected.js').exists())

    def test_bad_selection_unknown_fields_duplicates_traversal_and_sensitive_paths(self):
        for value in [{'schema_version':True,'paths':['app.py']},{'schema_version':1,'paths':['app.py'],'approved':True},
                      {'schema_version':1,'paths':[]},{'schema_version':1,'paths':['app.py','APP.py']},
                      {'schema_version':1,'paths':['../app.py']},{'schema_version':1,'paths':['.env.py']},
                      {'schema_version':1,'paths':['secrets/app.py']},{'schema_version':1,'paths':['node_modules/app.py']},
                      {'schema_version':1,'paths':['app.py\n']},{'schema_version':1,'paths':['readme.md']},
                      {'schema_version':1,'paths':['x'+str(i)+'.py' for i in range(21)]}]:
            with self.subTest(value=value):
                self.spec.write_bytes(canonical(value))
                with patch.object(structure,'run_engine',side_effect=AssertionError('engine started')):
                    with self.assertRaises(SafetyError): structure.build_structure(self.spec,self.repo,self.output)
                self.assertFalse(self.output.exists())

    def test_dirty_or_untracked_source_is_rejected_before_creation(self):
        (self.repo/'untracked.py').write_text('x=1',encoding='utf-8')
        with self.assertRaises(SafetyError): self.build()
        self.assertFalse(self.output.exists())

    def test_existing_output_and_git_or_runtime_namespace_is_never_overwritten(self):
        self.build();before=(self.output/structure.MANIFEST).read_bytes()
        with self.assertRaises(SafetyError): self.build()
        self.assertEqual(before,(self.output/structure.MANIFEST).read_bytes())
        for target in [self.repo/'new-index',self.repo/'.git/new-index',self.base/'ui-state/new',self.base/'memory-collections/new']:
            with self.subTest(target=target), self.assertRaises(SafetyError): structure.build_structure(self.spec,self.repo,target)
            self.assertFalse(target.exists())

    def test_selected_source_missing_or_dirty_invalidates_even_zero_hits(self):
        self.build();(self.repo/'maths.py').write_text('def add(a,b): return 0',encoding='utf-8')
        with self.assertRaises(SafetyError): structure.query_structure(self.output,self.repo,'not found')
        (self.repo/'maths.py').unlink()
        with self.assertRaises(SafetyError): structure.verify_structure(self.output,self.repo)

    def test_committed_change_head_branch_and_foreign_repository_are_rejected(self):
        self.build();source=RepositoryGateway(self.repo)
        original_branch=source.command('symbolic-ref','--short','HEAD').decode().strip()
        source.command('branch','other');source.command('switch','other')
        with self.assertRaises(SafetyError): structure.verify_structure(self.output,self.repo)
        source.command('switch',original_branch)
        source.command('commit','--allow-empty','-m','changed commit')
        with self.assertRaises(SafetyError): structure.query_structure(self.output,self.repo,'add')
        other=self.base/'other';fixture(other)
        with self.assertRaises(SafetyError): structure.verify_structure(self.output,other)

    def test_graph_copy_manifest_and_unmatched_source_tampering_rejected(self):
        self.build()
        for path in [self.output/structure.GRAPH,self.output/structure.MANIFEST,self.output/'snapshot/app.py',self.output/'snapshot/maths.py']:
            data=path.read_bytes()
            if path.name==structure.MANIFEST:
                value=json.loads(data);value['manifest_sha256']='0'*64;path.write_bytes(canonical(value))
            else:path.write_bytes(data+b' ')
            with self.subTest(path=path), self.assertRaises(SafetyError): structure.query_structure(self.output,self.repo,'missing')
            path.write_bytes(data)

    def test_resealed_permission_fields_and_malformed_source_bindings_are_rejected(self):
        self.build();original=(self.output/structure.MANIFEST).read_bytes()
        for mutate in [lambda m:m.update(runtime_authorized=True),lambda m:m.update(structural_claims_only=False),
                       lambda m:m.update(approved=True),lambda m:m['source']['files'][0].update(lines=True),
                       lambda m:m['source'].update(files=[None]),lambda m:m['source'].update(root_sha256=[]),
                       lambda m:m.update(schema_version=True),lambda m:m['engine'].update(version='new')]:
            value=json.loads(original);mutate(value);self.reseal(manifest=value)
            with self.assertRaises(SafetyError): structure.verify_structure(self.output,self.repo)
            (self.output/structure.MANIFEST).write_bytes(original)

    def test_resealed_graph_bad_ranges_paths_endpoints_unknown_fields_and_types(self):
        self.build();original=self.graph()
        mutations=[lambda g:g['nodes'][0].update(start_line=True),lambda g:g['nodes'][0].update(end_line=999),
                   lambda g:g['nodes'][0].update(path='../outside.py'),lambda g:g['nodes'][0].update(kind=[]),
                   lambda g:g['nodes'][0].update(language=[]),lambda g:g['nodes'][0].update(approved=True),
                   lambda g:g['nodes'].append(copy.deepcopy(g['nodes'][0])),lambda g:g['nodes'].pop(),
                   lambda g:g['edges'][0].update(target='missing'),lambda g:g['edges'][0].update(kind=[]),
                   lambda g:g['edges'].append(copy.deepcopy(g['edges'][0])),lambda g:g.update(schema_version=True),
                   lambda g:g.update(nodes=[None]),lambda g:g.update(omitted_external_edges=-1)]
        for mutate in mutations:
            value=copy.deepcopy(original);mutate(value);self.reseal(graph=value)
            with self.assertRaises(SafetyError): structure.query_structure(self.output,self.repo,'add')
        self.reseal(graph=original)

    def test_invalid_queries_are_literals_bounded_and_verified_on_zero_hits(self):
        self.build()
        for query,limit in [('',1),('x'*129,1),('bad\nquery',1),('add',True),('add',0),('add',6)]:
            with self.subTest(query=query,limit=limit), self.assertRaises(SafetyError): structure.query_structure(self.output,self.repo,query,limit)
        result=structure.query_structure(self.output,self.repo,'SELECT * FROM nodes; DROP TABLE nodes;')
        self.assertEqual([],result['hits']);self.assertEqual([],result['edges'])
        self.assertEqual([],structure.query_structure(self.output,self.repo,'absent')['hits'])

    def test_source_change_during_engine_preserves_incomplete_output(self):
        def changing(snapshot,selected,output):
            self.fake_engine(snapshot,selected,output)
            (self.repo/'maths.py').write_text('changed',encoding='utf-8')
        with patch.object(structure,'run_engine',side_effect=changing), self.assertRaises(SafetyError): structure.build_structure(self.spec,self.repo,self.output)
        self.assertTrue((self.output/'intent.json').exists());self.assertFalse((self.output/structure.MANIFEST).exists())
        with self.assertRaises(SafetyError): structure.query_structure(self.output,self.repo,'add')

    def test_failed_engine_does_not_create_success_manifest_or_retry_existing_output(self):
        with patch.object(structure,'run_engine',side_effect=SafetyError('failed')),self.assertRaises(SafetyError): structure.build_structure(self.spec,self.repo,self.output)
        self.assertFalse((self.output/structure.MANIFEST).exists());self.assertTrue((self.output/'snapshot/app.py').exists())
        with patch.object(structure,'run_engine',side_effect=AssertionError('retried')),self.assertRaises(SafetyError): self.build()

    def test_change_after_initial_query_verification_prevents_result(self):
        self.build();real=structure.capture;calls=0
        def changed(repo,selected):
            nonlocal calls
            calls+=1
            if calls==2:(self.output/'snapshot/maths.py').write_text('changed',encoding='utf-8')
            return real(repo,selected)
        with patch.object(structure,'capture',side_effect=changed),self.assertRaises(SafetyError): structure.query_structure(self.output,self.repo,'absent')

    def test_symlink_artifact_or_selected_source_is_rejected(self):
        self.build();copy_file=self.output/'snapshot/maths.py';original=copy_file.read_bytes();copy_file.unlink()
        # Link creation needs privileges on Windows; junctions use the same validator.
        with patch.object(Path,'is_symlink',side_effect=lambda:True),self.assertRaises(SafetyError): structure.verify_structure(self.output,self.repo)
        copy_file.write_bytes(original)

    def test_cli_build_query_verify_and_failure_do_not_construct_provider(self):
        with patch('repo_maintainer.cli.PiProvider',side_effect=AssertionError('model invoked')):
            with patch.object(structure,'run_engine',side_effect=self.fake_engine),contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(0,main(['structure-build','--repo',str(self.repo),'--spec',str(self.spec),'--output',str(self.output)]))
                self.assertEqual('structure_index_created',json.loads(output.getvalue())['status'])
            for command in ['structure-query','structure-verify']:
                argv=[command,'--repo',str(self.repo),'--index-dir',str(self.output)]
                if command=='structure-query':argv+=['--query','add','--limit','1']
                with contextlib.redirect_stdout(io.StringIO()) as output:self.assertEqual(0,main(argv))
                self.assertFalse(json.loads(output.getvalue())['runtime_authorized'])
            with contextlib.redirect_stderr(io.StringIO()):self.assertEqual(1,main(['structure-query','--repo',str(self.repo),'--index-dir',str(self.output),'--query','']))

    @unittest.skipUnless((Path(__file__).resolve().parents[1]/
                         'runtime/codegraph/node_modules/@colbymchenry/codegraph/package.json').is_file(),
                         'Optional pinned CodeGraph runtime not installed; see runtime/codegraph/README.md')
    def test_real_pinned_codegraph_extracts_cross_file_calls_and_terminal_eof_rows(self):
        (self.repo/'app.py').write_text('from maths import add\n\ndef total(a, b):\n    return add(a, b) + add(b, a)\n',encoding='utf-8')
        gateway=RepositoryGateway(self.repo);gateway.command('add','app.py');gateway.command('commit','-m','repeated calls fixture')
        before=RepositoryGateway(self.repo).snapshot()
        result=structure.build_structure(self.spec,self.repo,self.output)
        self.assertGreater(result['nodes'],2)
        graph=self.graph();nodes={n['id']:n for n in graph['nodes']}
        self.assertTrue(any(e['kind']=='calls' and nodes[e['source']]['name']=='total' and nodes[e['target']]['name']=='add' for e in graph['edges']))
        file_node=next(n for n in graph['nodes'] if n['kind']=='file' and n['path']=='maths.py')
        self.assertEqual(3,file_node['end_line'])
        result=structure.query_structure(self.output,self.repo,'add',1)
        self.assertEqual('add',result['hits'][0]['name']);self.assertTrue(result['edges'])
        self.assertEqual(before,RepositoryGateway(self.repo).snapshot())
        self.assertFalse((self.repo/'.codegraph').exists());self.assertFalse(result['runtime_authorized'])

    def test_size_non_utf8_and_binary_sources_rejected_before_engine(self):
        original=(self.repo/'maths.py').read_bytes()
        for data in [b'x'*(structure.MAX_FILE+1),b'\xff',b'pass\x00',b'']:
            (self.repo/'maths.py').write_bytes(data)
            gateway=RepositoryGateway(self.repo);gateway.command('add','maths.py');gateway.command('commit','-m','invalid fixture')
            with patch.object(structure,'run_engine',side_effect=AssertionError('engine started')):
                with self.assertRaises((SafetyError,UnicodeError)): structure.build_structure(self.spec,self.repo,self.output)
            self.assertFalse(self.output.exists())
        (self.repo/'maths.py').write_bytes(original)

    def test_duplicate_json_fields_and_engine_failure_protocol_never_create_manifest(self):
        self.spec.write_bytes(b'{"schema_version":1,"paths":["app.py"],"paths":["maths.py"]}')
        with self.assertRaises(SafetyError): self.build()
        self.assertFalse(self.output.exists())
        snapshot=self.base/'fake-snapshot';snapshot.mkdir()
        fake_result=subprocess.CompletedProcess([],1,b'',b'engine unavailable')
        with patch.object(subprocess,'run',return_value=fake_result),self.assertRaises(SafetyError):
            structure.run_engine(snapshot,['app.py'],self.base/structure.GRAPH)
        self.assertEqual(b'engine unavailable',(self.base/'engine-stderr.txt').read_bytes())

    def test_graph_count_limits_and_recursive_calls_are_structural_data(self):
        self.build();graph=self.graph();source=self.manifest()['source']
        for field,count in [('nodes',1001),('edges',4001)]:
            value=copy.deepcopy(graph);value[field]=[value[field][0]]*count
            with self.subTest(field=field),self.assertRaises(SafetyError): structure.validate_graph(value,source)
        graph['edges'].append({'source':'add','target':'add','kind':'calls'})
        self.assertIs(graph,structure.validate_graph(graph,source))

    def test_missing_local_package_cannot_fall_back_to_node_path_or_parent_packages(self):
        runtime=Path(__file__).resolve().parents[1]/'runtime/codegraph'
        empty=self.base/'missing-runtime';empty.mkdir()
        driver=empty/'extract.cjs';shutil.copyfile(runtime/'extract.cjs',driver)
        snapshot=self.base/'no-fallback-snapshot';snapshot.mkdir()
        request={'snapshot':str(snapshot),'output':str(self.base/'unexpected.json'),'files':['app.py']}
        result=subprocess.run(['node',str(driver)],input=canonical(request),stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                              env={**os.environ,'NODE_PATH':str(runtime/'node_modules')},timeout=15)
        self.assertNotEqual(0,result.returncode);self.assertIn(b'Cannot find module',result.stderr)
        self.assertFalse((snapshot/'.codegraph').exists());self.assertFalse((self.base/'unexpected.json').exists())
