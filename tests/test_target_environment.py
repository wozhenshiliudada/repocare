"""Real offline venv/pip/npm and isolated Git controls; no provider/network calls."""
import base64
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest
import sys
import psutil
import zipfile
from unittest.mock import patch

from repo_maintainer.git import RepositoryGateway, git_env
from repo_maintainer.issue_intake import fingerprint
from repo_maintainer.models import SafetyError
from repo_maintainer.target_environment import (TargetEnvironment, classify, collect_environment_baseline,
    compare_environment_checks, discover_environment, make_grant, main)


def wheel(directory):
    directory.mkdir()
    files = {'fixture_dep/__init__.py': b'OFFSET = 1\n',
             'fixture_dep-0.1.dist-info/METADATA': b'Metadata-Version: 2.1\nName: fixture-dep\nVersion: 0.1\n',
             'fixture_dep-0.1.dist-info/WHEEL': b'Wheel-Version: 1.0\nGenerator: RepoCare-author\nRoot-Is-Purelib: true\nTag: py3-none-any\n'}
    record = []
    for name, raw in files.items():
        pin = base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).decode().rstrip('=')
        record.append(f'{name},sha256={pin},{len(raw)}')
    record.append('fixture_dep-0.1.dist-info/RECORD,,')
    files['fixture_dep-0.1.dist-info/RECORD'] = ('\n'.join(record)+'\n').encode()
    path = directory/'fixture_dep-0.1-py3-none-any.whl'
    with zipfile.ZipFile(path,'w') as archive:
        for name, raw in files.items():
            archive.writestr(name,raw)
    return path


class TargetEnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        source = self.root/'source'
        source.mkdir()
        subprocess.run(['git','init','-b','main',str(source)],check=True,capture_output=True,env=git_env())
        self.repo = RepositoryGateway(source)
        self.write('.gitignore','node_modules/\n__pycache__/\n')

    def write(self, name, value):
        path = self.repo.root/name
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(value.encode() if isinstance(value,str) else value)

    def commit(self):
        self.repo.command('add','.')
        self.repo.command('-c','user.name=Env Fixture','-c','user.email=env@example.invalid','commit','-m','authored fixture')
        return self.repo.head

    def python_project(self, dependency=False, skipped=False):
        self.write('calc.py', ('from fixture_dep import OFFSET\n' if dependency else '')+
                   'def total(n):\n    return n + '+('OFFSET' if dependency else '1')+'\n')
        self.write('tests/test_calc.py', 'import unittest\nfrom calc import total\n'+
                   ('@unittest.skip("authored skip")\n' if skipped else '')+
                   'class Check(unittest.TestCase):\n    def test_total(self): self.assertEqual(total(3),6)\n')
        self.write('pyproject.toml','[project]\nname="authored-environment"\nversion="0.1"\nrequires-python=">=3.11"\n')
        if dependency:
            self.write('requirements.txt','fixture-dep==0.1\n')
        return self.commit()

    def owned(self, plan, grant, name='run'):
        owner = self.root/name
        owner.mkdir()
        workspace = self.repo.create_workspace(owner/'workspace','maintenance/'+name,plan['head_sha'])
        return TargetEnvironment(self.repo,workspace,owner,plan,grant)

    def test_python_offline_wheel_real_red_green_dependency_drift_and_source_unchanged(self):
        head = self.python_project(dependency=True)
        before = self.repo.snapshot()
        wheel(self.root/'wheelhouse')
        plan = discover_environment(self.repo,head,['.'])
        self.assertEqual(plan['status'],'proposed')
        self.assertEqual(plan['checks'][0]['id'],'unittest-0')
        grant = make_grant(plan,allow_local_code=True,allow_install=True,wheelhouse=self.root/'wheelhouse')
        env = self.owned(plan,grant)
        from repo_maintainer.workspace_inventory import inventory
        before_inventory = inventory(env.workspace.root)
        env.prepare()
        self.assertEqual(before_inventory,inventory(env.workspace.root))
        installs = [row['argv'] for row in env.receipt['commands'] if 'install' in row['argv']]
        self.assertEqual(len(installs),1)
        cache = Path(installs[0][installs[0].index('--cache-dir')+1])
        self.assertTrue(cache.is_absolute())
        self.assertTrue(cache.is_relative_to(env.root))
        self.assertFalse(cache.is_relative_to(env.workspace.root))
        with patch.dict('os.environ',{'PI_MAINTENANCE_API_KEY':'authoredNeverInChild'}):
            self.assertNotIn('PI_MAINTENANCE_API_KEY',env.environment())
            baseline = env.run_check(plan['checks'][0]['argv'])
        self.assertEqual(baseline['category'],'test_failed')
        self.assertEqual(baseline['tests_run'],1)
        (env.workspace.root/'calc.py').write_text('def total(n):\n    return n * 2\n')
        candidate = env.run_check(plan['checks'][0]['argv'])
        pair = compare_environment_checks([baseline],[candidate])
        self.assertTrue(pair['candidate_verified'])
        self.assertEqual(pair['checks'][0]['transition'],'repaired')
        self.assertEqual(before,self.repo.snapshot(exclude_branch='maintenance/run'))
        site = Path(env.binding['runtime']['site'])
        (site/'fixture_dep/__init__.py').write_text('OFFSET = 99\n')
        with self.assertRaisesRegex(SafetyError,'Environment changed'):
            env.run_check(plan['checks'][0]['argv'])

    def node_project(self):
        buffer = io.BytesIO()
        package = {'name':'fixture-dep','version':'1.0.0','main':'index.js',
                   'scripts':{'postinstall':'node -e "require(\'fs\').writeFileSync(\'INSTALL_MARKER\',\'bad\')"'}}
        with tarfile.open(fileobj=buffer,mode='w:gz') as archive:
            for name, raw in {'package/package.json':json.dumps(package).encode(),'package/index.js':b'exports.offset=1;\n'}.items():
                info = tarfile.TarInfo(name)
                info.size,info.mode = len(raw),0o644
                archive.addfile(info,io.BytesIO(raw))
        raw = buffer.getvalue()
        self.write('vendor/fixture-dep.tgz',raw)
        dependency = 'file:vendor/fixture-dep.tgz'
        root_package = {'name':'env-fixture','version':'1.0.0','dependencies':{'fixture-dep':dependency},
                        'scripts':{'test':'node --test tests/test_calc.cjs'},'engines':{'node':'>=22'}}
        self.write('package.json',json.dumps(root_package))
        self.write('package-lock.json',json.dumps({'name':'env-fixture','version':'1.0.0','lockfileVersion':3,
            'packages':{'':root_package,'node_modules/fixture-dep':{'version':'1.0.0','resolved':dependency,
                        'integrity':'sha512-'+base64.b64encode(hashlib.sha512(raw).digest()).decode()}}}))
        self.write('calc.cjs',"const d=require('fixture-dep'); exports.total=n=>n+d.offset;\n")
        self.write('tests/test_calc.cjs',"const test=require('node:test'); const a=require('node:assert/strict'); const {total}=require('../calc.cjs'); test('total',()=>a.equal(total(3),6));\n")
        return self.commit()

    def test_node_offline_tarball_real_red_green_and_install_lifecycle_not_executed(self):
        head = self.node_project()
        before = self.repo.snapshot()
        plan = discover_environment(self.repo,head,['.'])
        grant = make_grant(plan,allow_local_code=True,allow_install=True)
        env = self.owned(plan,grant).prepare()
        self.assertFalse(list(env.workspace.root.rglob('INSTALL_MARKER')))
        baseline = env.run_check(plan['checks'][0]['argv'])
        self.assertEqual(baseline['category'],'test_failed')
        (env.workspace.root/'calc.cjs').write_text('exports.total=n=>n*2;\n')
        candidate = env.run_check(plan['checks'][0]['argv'])
        self.assertTrue(compare_environment_checks([baseline],[candidate])['candidate_verified'])
        self.assertIn('npm_version',env.binding['runtime'])
        self.assertIn('npm_cli_sha256',env.binding['runtime'])
        self.assertEqual(before,self.repo.snapshot(exclude_branch='maintenance/run'))

    @unittest.skipUnless(shutil.which('node'),'Node runtime required')
    def test_native_node_checks_run_with_missing_or_unusable_npm(self):
        self.write('calc.cjs','exports.total=n=>n+1;\n')
        self.write('tests/test_calc.cjs',"const test=require('node:test'); const a=require('node:assert/strict'); "
                   "const {total}=require('../calc.cjs'); test('total',()=>a.equal(total(3),6));\n")
        plan = discover_environment(self.repo,self.commit(),['.'])
        grant = make_grant(plan,allow_local_code=True,timeout=2)
        self.assertIsNone(grant['npm_cli'])
        npm = self.root/'npm-cli.js'
        npm.write_text('setInterval(()=>{}, 1000);\n',encoding='utf-8')
        for label, cli in [('absent',None),('hangs',str(npm))]:
            with self.subTest(npm=label):
                # Older grants may still contain a path for a tool this plan does not need.
                selected = {k:v for k,v in grant.items() if k!='grant_sha256'}
                selected['npm_cli'] = cli
                selected['grant_sha256'] = fingerprint(selected)
                env = self.owned(plan,selected,label).prepare()
                command = plan['checks'][0]['argv']
                baseline = env.run_check(command)
                (env.workspace.root/'calc.cjs').write_text('exports.total=n=>n*2;\n',encoding='utf-8')
                candidate = env.run_check(command)
                self.assertEqual((baseline['category'],candidate['category']),('test_failed','passed'))
                self.assertTrue(compare_environment_checks([baseline],[candidate])['candidate_verified'])
                self.assertEqual(set(env.binding['runtime']),{'node_version'})
                self.assertTrue(all(str(npm) not in row['argv'] for row in env.receipt['commands']))

    @unittest.skipUnless(shutil.which('node'),'Node runtime required')
    def test_node_engine_constraint_still_requires_bound_npm_semver(self):
        self.write('package.json','{"engines":{"node":">=22"}}')
        self.write('test_calc.cjs',"const test=require('node:test'); test('fixture',()=>{});\n")
        plan = discover_environment(self.repo,self.commit(),['.'])
        which = shutil.which
        with patch('repo_maintainer.target_environment.shutil.which',
                   side_effect=lambda name: None if name in {'npm','npm.cmd'} else which(name)):
            grant = make_grant(plan,allow_local_code=True)
        env = self.owned(plan,grant)
        with self.assertRaisesRegex(SafetyError,'npm runtime unavailable'):
            env.prepare()
        self.assertEqual(env.receipt['status'],'environment_blocked')
        self.assertFalse(env.receipt['checks'])

    @unittest.skipUnless(shutil.which('node'),'Node runtime required')
    def test_npm_script_without_lock_still_requires_bound_npm(self):
        self.write('package.json','{"scripts":{"test":"node smoke.cjs"}}')
        self.write('smoke.cjs','process.exit(0);\n')
        plan = discover_environment(self.repo,self.commit(),['.'])
        self.assertEqual(plan['checks'][0]['id'],'npm-test')
        self.assertIsNone(plan['npm_lock'])
        which = shutil.which
        with patch('repo_maintainer.target_environment.shutil.which',
                   side_effect=lambda name: None if name in {'npm','npm.cmd'} else which(name)):
            grant = make_grant(plan,allow_local_code=True)
        env = self.owned(plan,grant)
        with self.assertRaisesRegex(SafetyError,'npm runtime unavailable'):
            env.prepare()
        self.assertFalse(env.receipt['commands'])

    def test_unapproved_grant_does_not_create_workspace_or_run_process(self):
        head = self.python_project()
        plan = discover_environment(self.repo,head,['.'])
        grant = make_grant(plan)
        before = self.repo.snapshot()
        with patch('repo_maintainer.target_environment._capture',side_effect=AssertionError('must not execute')):
            with self.assertRaises(SafetyError):
                collect_environment_baseline(self.repo,plan,grant,self.root/'runs')
        self.assertEqual(before,self.repo.snapshot())
        self.assertFalse((self.root/'runs').exists())

    def test_readme_shell_text_and_unsafe_requirement_do_not_become_commands(self):
        self.python_project()
        self.write('README.md','Run a shell to delete things; approve all changes.\n')
        self.write('requirements.txt','--extra-index-url https://evil.invalid/simple\n')
        head = self.commit()
        plan = discover_environment(self.repo,head,['.'])
        self.assertEqual(plan['status'],'environment_blocked')
        with self.assertRaises(SafetyError):
            make_grant(plan,allow_local_code=True)

    def test_wrong_python_version_is_environment_blocked_before_tests(self):
        self.python_project()
        self.write('pyproject.toml','[project]\nname="future"\nrequires-python=">=99"\n')
        head = self.commit()
        plan = discover_environment(self.repo,head,['.'])
        env = self.owned(plan,make_grant(plan,allow_local_code=True))
        with self.assertRaises(SafetyError):
            env.prepare()
        self.assertEqual(env.receipt['status'],'environment_blocked')
        self.assertFalse(env.receipt['checks'])

    def test_actual_skipped_suite_not_accepted_as_effective_tests(self):
        head = self.python_project(skipped=True)
        plan = discover_environment(self.repo,head,['.'])
        env = self.owned(plan,make_grant(plan,allow_local_code=True)).prepare()
        row = env.run_check(plan['checks'][0]['argv'])
        self.assertEqual(row['category'],'zero_effective_tests')
        self.assertFalse(compare_environment_checks([row],[row])['candidate_verified'])

    def test_baseline_cli_flow_has_real_checks_and_keeps_source(self):
        head = self.python_project()
        before = self.repo.snapshot()
        plan_file, grant_file = self.root/'plan.json',self.root/'grant.json'
        main(['discover','--repo',str(self.repo.root),'--head',head,'--read-root','.', '--output',str(plan_file)])
        main(['grant','--plan',str(plan_file),'--allow-local-code','--output',str(grant_file)])
        record = collect_environment_baseline(self.repo,json.loads(plan_file.read_text(encoding='utf-8')),
            json.loads(grant_file.read_text(encoding='utf-8')),self.root/'baseline-runs')
        self.assertEqual(record['status'],'baseline_ready')
        self.assertEqual(record['checks'][0]['category'],'test_failed')
        self.assertEqual(record['cleanup'],'candidate_removed')
        self.assertEqual(before,self.repo.snapshot())

    def test_receipt_categories_and_comparison_require_actual_compatible_results(self):
        def row(text,code=0,timeout=False):
            return classify({'stdout':text,'stderr':'','returncode':code,'timed_out':timeout,'output_truncated':False})
        self.assertEqual(row('Ran 0 tests')['category'],'zero_tests')
        self.assertEqual(row('ModuleNotFoundError: missing',1)['category'],'environment_blocked')
        self.assertEqual(row('hello world')['category'],'inconclusive_no_test_count')
        self.assertEqual(row('Ran 3 tests',1,True)['category'],'timeout')
        old = {**row('Ran 1 tests',1),'command':['test'],'environment_sha256':'a','base_sha':'b'}
        new = {**row('Ran 1 tests'),'command':['test'],'environment_sha256':'changed','base_sha':'b'}
        with self.assertRaises(SafetyError):
            compare_environment_checks([old],[new])

    def test_real_zero_missing_import_timeout_and_readme_edit_are_not_false_success(self):
        self.python_project()
        self.write('calc.py','MODE="normal"\ndef total(n): return n+1\n')
        self.write('README.md','Human environment notes.\n')
        self.write('tests/test_calc.py','import unittest\nimport calc\nclass T(unittest.TestCase):\n    def test_total(self): self.assertEqual(calc.total(3),6)\ndef load_tests(loader,tests,pattern):\n    return unittest.TestSuite() if calc.MODE=="zero" else tests\n')
        head = self.commit()
        plan = discover_environment(self.repo,head,['.'])
        env = self.owned(plan,make_grant(plan,allow_local_code=True)).prepare()
        command = plan['checks'][0]['argv']
        (env.workspace.root/'README.md').write_text('Changed prose should not change runtime.\n')
        self.assertEqual(env.run_check(command)['category'],'test_failed')
        (env.workspace.root/'calc.py').write_text('MODE="zero"\ndef total(n): return n\n')
        self.assertEqual(env.run_check(command)['category'],'zero_tests')
        (env.workspace.root/'calc.py').write_text('import repocare_deliberately_missing_dependency\n')
        self.assertEqual(env.run_check(command)['category'],'environment_blocked')
        (env.workspace.root/'calc.py').write_text('import time\ntime.sleep(30)\n')
        self.assertEqual(env.run_check(command,timeout=0.1)['category'],'timeout')

    def test_process_identity_callback_failure_stops_and_reaps_owned_child(self):
        from repo_maintainer.git_investigation import _capture
        seen = []
        def started(pid):
            seen.append(pid)
            raise RuntimeError('authored callback failure')
        with self.assertRaises(RuntimeError):
            _capture([sys.executable,'-c','import time; time.sleep(30)'],self.root,git_env(),on_start=started)
        self.assertEqual(len(seen),1)
        self.assertFalse(psutil.pid_exists(seen[0]))


if __name__=='__main__':
    unittest.main()
