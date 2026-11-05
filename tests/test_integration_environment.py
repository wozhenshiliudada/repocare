"""Real offline environments and sealed commits; no provider/network calls."""
import json
import copy
import os
import subprocess
import sys
import tempfile
import psutil
from pathlib import Path
import unittest
from unittest.mock import patch

import test_workflow_environment as fixtures
from repo_maintainer.integration import prepare_integration,integrate,recover_integration
from repo_maintainer.models import Policy, SafetyError, digest
from repo_maintainer.target_environment import TargetEnvironment,discover_environment,make_grant
from repo_maintainer.workflow_environment import GrantedEnvironment
from repo_maintainer.workflow import repair
from repo_maintainer.issue_intake import fingerprint
from repo_maintainer.workflow import cleanup_run


class IntegrationEnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.case=fixtures.WorkflowEnvironmentTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.repo=self.case.repo

    def candidate(self,language='python',dependency=True,content=None):
        plan,grant,policy=self.case.setup_environment(language,dependency if language=='python' else False)
        self.before=self.repo.snapshot()
        record=self.case.run_repair(plan,grant,policy,content=content)
        self.assertEqual(record['status'],'candidate_ready',record.get('error'))
        self.record=record
        self.owner=Path(record['workspace']).parent
        self.origin=self.owner/'environment/environment.json'
        self.origin_bytes=self.origin.read_bytes()
        return record

    def target(self):
        return self.repo.command('show-ref','--verify','--hash','refs/heads/integration/'+self.record['run_id'],
                                 check=False).decode().strip()

    def test_python_reuses_verified_venv_and_sealed_tests_before_separate_publication_approval(self):
        record=self.candidate()
        origin=json.loads(self.origin_bytes)
        binding=origin['environment_binding']
        launcher=Path(binding['runtime']['executable'])
        launcher_bytes=launcher.read_bytes()
        self.assertEqual(binding['runtime']['venv_executable_sha256'],digest(launcher_bytes))
        self.assertEqual(binding['runtime_executable_sha256'],digest(Path(origin['grant']['python']).read_bytes()))
        with (patch('repo_maintainer.integration.run_test',side_effect=AssertionError('No host interpreter fallback')),
              patch('repo_maintainer.target_environment.TargetEnvironment.prepare',side_effect=AssertionError('No reinstall'))):
            review=prepare_integration(self.owner)
        self.assertEqual(review['status'],'prepared',review)
        core=review['review']
        self.assertEqual(core['tests'][0]['command'],record['candidate_tests'][0]['command'])
        self.assertEqual(core['tests'][0]['environment_sha256'],record['environment']['environment_sha256'])
        self.assertTrue(core['environment_verification']['comparison']['candidate_verified'])
        self.assertEqual(self.origin_bytes,self.origin.read_bytes())
        self.assertEqual(self.target(),'')
        self.assertEqual(integrate(self.owner)['status'],'awaiting_approval')
        launcher.write_bytes(launcher_bytes+b'changed')
        try:
            with self.assertRaisesRegex(SafetyError,'Original venv executable changed'):
                integrate(self.owner,review['review_sha256'])
            self.assertEqual(self.target(),'')
        finally:
            launcher.write_bytes(launcher_bytes)
        result=integrate(self.owner,review['review_sha256'])
        self.assertEqual(result['status'],'published')
        self.assertEqual(integrate(self.owner,review['review_sha256'])['status'],'published')
        self.assertEqual(core['commit'],self.target())
        self.assertEqual(self.before,self.repo.snapshot(exclude_branches=(record['branch'],'integration/'+record['run_id'])))
        self.assertFalse((self.owner/'integration-verification').exists())
        cleanup_run(self.owner)

    def test_src_layout_imports_come_from_sealed_code_with_original_venv_dependencies(self):
        author=self.case.case
        author.write('src/envapp/__init__.py','')
        author.write('src/envapp/calc.py','def total(n): return n+1\n')
        author.write('tests/test_calc.py','import unittest\nfrom pathlib import Path\n'
            'from envapp import calc\nclass T(unittest.TestCase):\n'
            '    def test_total(self):\n'
            '        self.assertEqual(Path(calc.__file__).parent,Path.cwd()/"src"/"envapp")\n'
            '        self.assertEqual(calc.total(3),6)\n')
        head=author.commit()
        plan=discover_environment(self.repo,head,['.'])
        policy=Policy(('src/envapp/calc.py',),tuple(tuple(c['argv']) for c in plan['checks']))
        record=repair(self.repo.root,'src layout',fixtures.AuthoredProvider('src/envapp/calc.py','def total(n): return n*2\n'),
            policy,'auto',author.root/'runs',environment_factory=GrantedEnvironment(plan,make_grant(plan,allow_local_code=True)))
        self.assertEqual(record['status'],'candidate_ready',record.get('error'))
        owner=Path(record['workspace']).parent
        review=prepare_integration(owner)
        self.assertEqual(review['status'],'prepared',review)
        receipt=json.loads((owner/review['review']['environment_verification']['receipt']).read_text(encoding='utf-8'))
        self.assertEqual(receipt['verification_workspace'],str(owner/'integration-verification'))
        self.assertTrue(all(p['status']=='finished' for p in review['environment_processes']))
        cleanup_run(owner)

    def test_legacy_python_receipt_remains_unchanged_during_sealed_verification(self):
        # Old valid Windows receipts used a venv launcher as the granted Python.
        parent = self.case.case.root/'original-runtime'
        subprocess.run([sys.executable, '-I', '-m', 'venv', '--copies', '--without-pip', str(parent)],
                       check=True, capture_output=True, timeout=60)
        executable = parent/('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        with patch('repo_maintainer.target_environment.sys.executable', str(executable)):
            plan,grant,policy=self.case.setup_environment()
        current_identity=TargetEnvironment.identity
        def legacy_identity(environment):
            binding=current_identity(environment)
            binding['runtime'].pop('venv_executable_sha256',None)
            return binding
        with patch.object(TargetEnvironment,'identity',legacy_identity):
            record=self.case.run_repair(plan,grant,policy)
        self.assertEqual(record['status'],'candidate_ready',record.get('error'))
        owner=Path(record['workspace']).parent
        self.addCleanup(cleanup_run,owner)
        receipt=owner/'environment/environment.json'
        previous=receipt.read_bytes()
        binding=record['environment']['binding']
        self.assertNotIn('venv_executable_sha256',binding['runtime'])
        review=prepare_integration(owner)
        self.assertEqual(review['status'],'prepared',review)
        self.assertEqual(review['review']['environment_verification']['binding'],binding)
        self.assertEqual(receipt.read_bytes(),previous)

    def test_dependency_drift_before_prepare_is_blocked_and_user_bytes_are_preserved(self):
        self.candidate()
        original=json.loads(self.origin_bytes)
        changed=Path(original['environment_binding']['runtime']['site'])/'fixture_dep/__init__.py'
        changed.write_text('OFFSET=999\n',encoding='utf-8')
        with self.assertRaisesRegex(SafetyError,'runtime/dependencies changed'):
            prepare_integration(self.owner)
        self.assertEqual(changed.read_text(encoding='utf-8'),'OFFSET=999\n')
        self.assertEqual(self.target(),'')
        self.assertFalse((self.owner/'integration-verification').exists())
        self.assertEqual(self.origin.read_bytes(),self.origin_bytes)

    def test_dependency_drift_after_review_cannot_publish_even_with_old_approval(self):
        self.candidate('node',content="const d=require('fixture-dep'); exports.total=n=>n*2*d.offset;\n")
        review=prepare_integration(self.owner)
        changed=self.owner/'workspace/node_modules/fixture-dep/index.js'
        changed.write_text('exports.offset=999;\n',encoding='utf-8')
        with self.assertRaisesRegex(SafetyError,'files changed'):
            integrate(self.owner,review['review_sha256'])
        self.assertEqual(self.target(),'')
        self.assertEqual(changed.read_text(encoding='utf-8'),'exports.offset=999;\n')

    def test_seal_preserves_legacy_npm_binding_for_a_native_node_plan(self):
        fixture = self.case.case
        fixture.write('calc.cjs','exports.total=n=>n+1;\n')
        fixture.write('test_calc.cjs',"const test=require('node:test'); const a=require('node:assert/strict'); "
                      "const {total}=require('./calc.cjs'); test('total',()=>a.equal(total(3),6));\n")
        plan = discover_environment(self.repo,fixture.commit(),['.'])
        policy = Policy(('calc.cjs',),tuple(tuple(c['argv']) for c in plan['checks']))
        # Generate the exact old receipt shape using real Node and npm probes.
        with patch('repo_maintainer.target_environment.requires_npm',return_value=True):
            grant = make_grant(plan,allow_local_code=True)
            record = self.case.run_repair(plan,grant,policy)
        self.assertEqual(record['status'],'candidate_ready',record.get('error'))
        owner = Path(record['workspace']).parent
        self.addCleanup(cleanup_run,owner)
        receipt = owner/'environment/environment.json'
        previous = receipt.read_bytes()
        runtime = record['environment']['binding']['runtime']
        self.assertEqual(set(runtime),{'node_version','npm_version','npm_cli_sha256'})
        review = prepare_integration(owner)
        self.assertEqual(review['status'],'prepared',review.get('message'))
        self.assertEqual(review['review']['environment_verification']['binding']['runtime'],runtime)
        self.assertEqual(receipt.read_bytes(),previous)

    def test_original_receipt_or_sealed_receipt_change_cannot_use_old_review(self):
        self.candidate(dependency=False)
        review=prepare_integration(self.owner)
        path=self.owner/review['review']['environment_verification']['receipt']
        raw=path.read_bytes()
        path.write_bytes(raw+b'\n')
        with self.assertRaisesRegex(SafetyError,'evidence changed'):
            integrate(self.owner,review['review_sha256'])
        self.assertEqual(self.target(),'')
        path.write_bytes(raw)
        self.origin.write_bytes(self.origin_bytes+b'\n')
        with self.assertRaisesRegex(SafetyError,'evidence required'):
            integrate(self.owner,review['review_sha256'])
        self.assertEqual(self.target(),'')

    def test_rehashed_plan_or_removed_environment_cannot_authorize_host_fallback(self):
        self.candidate(dependency=False)
        fake=copy.deepcopy(json.loads(self.origin_bytes))
        fake['plan']['checks'][0]['argv']=['python','-c','raise RuntimeError("injected")']
        fake['plan']['plan_sha256']=fingerprint({k:v for k,v in fake['plan'].items() if k!='plan_sha256'})
        fake['grant']['plan_sha256']=fake['plan']['plan_sha256']
        fake['grant']['grant_sha256']=fingerprint({k:v for k,v in fake['grant'].items() if k!='grant_sha256'})
        self.origin.write_text(json.dumps(fake),encoding='utf-8')
        with patch('repo_maintainer.integration.run_test',side_effect=AssertionError('No downgrade')):
            with self.assertRaises(SafetyError):
                prepare_integration(self.owner)
            self.origin.write_bytes(self.origin_bytes)
            path=self.owner/'run.json'
            record=json.loads(path.read_text(encoding='utf-8'))
            del record['environment']
            path.write_text(json.dumps(record),encoding='utf-8')
            with self.assertRaisesRegex(SafetyError,'downgrade is disabled'):
                prepare_integration(self.owner)
        self.assertEqual(self.target(),'')

    def test_sealed_test_failure_retains_error_then_explicit_recovery_aborts_without_publication(self):
        author=self.case.case
        author.python_project()
        author.write('tests/test_calc.py','import unittest\nfrom pathlib import Path\nfrom calc import total\n'
            'class T(unittest.TestCase):\n    def test_total(self):\n'
            '        self.assertNotEqual(Path.cwd().name,"integration-verification")\n'
            '        self.assertEqual(total(3),6)\n')
        head=author.commit()
        plan=discover_environment(self.repo,head,['.'])
        policy=Policy(('calc.py',),tuple(tuple(c['argv']) for c in plan['checks']))
        record=self.case.run_repair(plan,make_grant(plan,allow_local_code=True),policy)
        self.assertEqual(record['status'],'candidate_ready',record.get('error'))
        self.record,self.owner=record,Path(record['workspace']).parent
        with self.assertRaisesRegex(SafetyError,'verification failed'):
            prepare_integration(self.owner)
        review=json.loads((self.owner/'integration-review.json').read_text(encoding='utf-8'))
        self.assertEqual(review['status'],'failed',review)
        self.assertEqual(review['failed_tests'][0]['category'],'test_failed')
        self.assertFalse((self.owner/'integration-verification').exists())
        self.assertEqual(recover_integration(self.owner)['status'],'aborted')
        self.assertEqual(self.target(),'')
        cleanup_run(self.owner)

    def test_node_copies_original_dependency_bytes_and_retests_in_sealed_worktree(self):
        record=self.candidate('node',content="const d=require('fixture-dep'); exports.total=n=>n*2*d.offset;\n")
        with patch('repo_maintainer.integration.run_test',side_effect=AssertionError('No host fallback')):
            review=prepare_integration(self.owner)
        self.assertEqual(review['status'],'prepared',review)
        verified=review['review']['environment_verification']
        self.assertEqual(verified['binding'],record['environment']['binding'])
        self.assertTrue(verified['comparison']['candidate_verified'])
        self.assertGreater(verified['copied_dependency_files'],0)
        self.assertEqual(self.origin_bytes,self.origin.read_bytes())
        self.assertEqual(self.target(),'')
        self.assertFalse((self.owner/'integration-verification').exists())
        self.assertEqual(integrate(self.owner,review['review_sha256'])['status'],'published')
        cleanup_run(self.owner)

    def owner_exit(self,stage):
        self.candidate('node',content="const d=require('fixture-dep'); exports.total=n=>n*2*d.offset;\n")
        script='''
import os,sys
from pathlib import Path
from repo_maintainer import integration
original=integration.save_review
def save(directory,review):
    original(directory,review)
    early=review.get('operation_stage')=='test_finished' and review['preparation'].get('commit')
    complete=review.get('operation_stage')=='cleanup_intent' and review.get('review_sha256')
    if (sys.argv[2]=='early' and early) or (sys.argv[2]=='complete' and complete):
        os._exit(91)
integration.save_review=save
integration.prepare_integration(Path(sys.argv[1]))
'''
        process=subprocess.run([sys.executable,'-B','-c',script,str(self.owner),stage],capture_output=True,timeout=60)
        self.assertEqual(process.returncode,91,process.stderr.decode(errors='replace'))
        original=json.loads((self.owner/'integration-review.json').read_text(encoding='utf-8'))
        self.assertEqual(original['operation_stage'],'test_finished' if stage=='early' else 'cleanup_intent')
        self.assertTrue((self.owner/'integration-verification/node_modules').exists())
        self.assertIsNone(original['test_inflight'])
        result=recover_integration(self.owner)
        self.assertEqual(result['status'],'aborted',result)
        preserved=(self.owner/'integration-review.json').read_bytes()
        self.assertFalse((self.owner/'integration-verification').exists())
        review=prepare_integration(self.owner,retry_aborted=True)
        self.assertEqual(review['status'],'prepared',review)
        self.assertNotEqual(review['preparation']['attempt_id'],original['preparation']['attempt_id'])
        archive=self.owner/'integration-attempts'/original['preparation']['attempt_id']/'integration-review.json'
        self.assertEqual(archive.read_bytes(),preserved)
        self.assertEqual(self.target(),'')
        with self.assertRaises(SafetyError):
            integrate(self.owner,original.get('review_sha256','0'*64))
        self.assertEqual(integrate(self.owner,review['review_sha256'])['status'],'published')
        cleanup_run(self.owner)

    def test_real_owner_exit_after_node_copy_recovers_and_requires_new_sealed_review(self):
        self.owner_exit('early')

    def test_real_owner_exit_after_complete_review_invalidates_its_old_approval_on_retry(self):
        self.owner_exit('complete')

    def test_live_sealed_test_child_blocks_recovery_until_that_owned_process_stops(self):
        author=self.case.case
        author.python_project()
        author.write('tests/test_calc.py','import unittest,time\nfrom pathlib import Path\nfrom calc import total\n'
            'class T(unittest.TestCase):\n    def test_total(self):\n'
            '        if Path.cwd().name=="integration-verification": time.sleep(45)\n'
            '        self.assertEqual(total(3),6)\n')
        head=author.commit()
        plan=discover_environment(self.repo,head,['.'])
        policy=Policy(('calc.py',),tuple(tuple(c['argv']) for c in plan['checks']))
        self.record=self.case.run_repair(plan,make_grant(plan,allow_local_code=True),policy)
        self.assertEqual(self.record['status'],'candidate_ready',self.record.get('error'))
        self.owner=Path(self.record['workspace']).parent
        script='''
import os,sys,subprocess,tempfile
from pathlib import Path
from repo_maintainer import integration
from repo_maintainer.integration_environment import SealedEnvironment
save0=integration.save_review
process0=SealedEnvironment.process
popen0=subprocess.Popen
# The controller deliberately exits before _capture starts its pipe readers.
# A verbose unittest child otherwise gets BrokenPipeError and dies before
# reaching the sleep. Keep only this crash fixture's child output in an owned
# temporary file; production capture and process guards stay unchanged.
sink=tempfile.TemporaryFile()
def popen(argv,*args,**kwargs):
    if 'unittest' in argv:
        kwargs.update(stdout=sink,stderr=sink)
    return popen0(argv,*args,**kwargs)
subprocess.Popen=popen
def process(self,argv,**kwargs):
    self.review['author_fixture_live_check']='unittest' in argv
    return process0(self,argv,**kwargs)
def save(directory,review):
    save0(directory,review)
    if review.get('author_fixture_live_check') and review.get('operation_stage')=='test_running':
        os._exit(91)
SealedEnvironment.process=process
integration.save_review=save
integration.prepare_integration(Path(sys.argv[1]))
'''
        # The deliberately orphaned test inherits stdout/stderr. PIPE capture
        # would wait for its EOF (45 seconds), so by recovery it would be dead.
        # Regular owned files let us observe the controller exit immediately.
        with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
            result=subprocess.run([sys.executable,'-B','-c',script,str(self.owner)],
                                  stdout=output,stderr=errors,timeout=60)
            errors.seek(0)
            self.assertEqual(result.returncode,91,errors.read().decode(errors='replace'))
        review=json.loads((self.owner/'integration-review.json').read_text(encoding='utf-8'))
        identity=review['test_inflight']
        self.assertIsNotNone(identity)
        child=psutil.Process(identity['pid'])
        self.assertEqual(child.create_time(),identity['created_at'])
        try:
            with self.assertRaisesRegex(SafetyError,'still alive'):
                recover_integration(self.owner)
            self.assertTrue((self.owner/'integration-verification').exists())
        finally:
            if child.is_running() and child.create_time()==identity['created_at']:
                child.terminate()
                child.wait(timeout=10)
        self.assertEqual(recover_integration(self.owner)['status'],'aborted')
        self.assertFalse((self.owner/'integration-verification').exists())
        self.assertEqual(self.target(),'')
        cleanup_run(self.owner)


if __name__=='__main__':
    unittest.main()
