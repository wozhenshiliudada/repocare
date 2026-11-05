"""Pinned environment discovery and explicitly granted local preparation/checks.

Local workspaces/venvs isolate state, not hostile code or OS/network access.
Untrusted project execution remains blocked without explicit local-code consent.
"""
from __future__ import annotations
from .maintenance_limits import LIMITS

import json
import argparse
import os
from pathlib import Path
import re
import shutil
import sys
import uuid
import tomllib
from urllib.parse import urlsplit

from .check_discovery import discover, is_test, CONFIGS, CODE_SUFFIXES
from .dashboard import unlinked
from .git_investigation import _capture
from .issue_intake import fingerprint
from .models import SafetyError, digest
from .source_discovery import CommittedTree
from .store import now, redact
from .workspace_inventory import inventory

METADATA = CONFIGS | {'package-lock.json', 'npm-shrinkwrap.json', 'uv.lock', 'poetry.lock',
                      'pdm.lock', '.python-version', '.node-version', '.npmrc'}
REQUIREMENT = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]*(?:\[[A-Za-z0-9_,.-]+\])?(?:\s*(?:===|==|~=|!=|>=|<=|>|<)[A-Za-z0-9.*+!_-]+(?:\s*,\s*(?:==|!=|>=|<=|>|<|~=)[A-Za-z0-9.*+!_-]+)*)?\Z')




def require(condition, message):
    if not condition:
        raise SafetyError(message)


def discover_environment(repository, head_sha, read_roots, *, investigation=None, behavior_contract=None):
    """No installs/imports/test processes. Only committed, scoped, bounded inputs."""
    tree = CommittedTree(repository, head_sha, read_roots)
    if behavior_contract is not None:
        from .workflow_behavior import validate_scope, command_for, check_id
        validate_scope(behavior_contract, repository, head_sha)
        require(all(case['module'] in tree.entries for case in behavior_contract['cases']),
                'Behavior cases must target existing files inside original read roots')
    names = sorted(n for n in tree.entries if n in METADATA or n.startswith('.github/workflows/')
                   or (is_test(n) and Path(n).suffix in CODE_SUFFIXES))
    selection = None
    if investigation is not None:
        from .task_test_selection import select_tests
        selection = select_tests(tree, investigation, allow_empty=behavior_contract is not None)
        names = sorted(n for n in names if n in METADATA or n.startswith('.github/workflows/')
                       or n in selection['selected_tests'])
        # A complete, empty committed test catalog is not a focused subset.
        # Keep nonempty/truncated selections exact: matching behavior cases do
        # not excuse omitting an existing regression suite or required test.
        if (behavior_contract is not None and selection['catalog_test_count'] == 0
                and not selection['catalog_truncated'] and not selection['selected_tests']
                and not selection['required_test_locations'] and not selection['unselected_candidates']):
            selection = None
    require(not tree.tree_truncated and len(names) <= 40, 'Environment discovery capacity exceeded; no full plan')
    texts, warnings, requirements, python_constraint = {}, [], [], None
    for name in names:
        texts[name] = tree.read(name).decode('utf-8')
    checks = discover(tree, names)
    recommendations = checks['recommendations']
    if selection is not None:
        from .task_test_selection import focused_checks
        recommendations = focused_checks(checks, selection)
    # Prefer functional suites. Build/lint can be evidence, never fabricated tests.
    selected = [r for r in recommendations if r['id'].startswith(('unittest-', 'pytest', 'node-native'))]
    if not selected:
        selected = [r for r in recommendations if r['id'] == 'npm-test']
    selected = selected[:5]
    if behavior_contract is not None:
        selected.append({'id': check_id(behavior_contract),
                         'title': '固定需求行为验收', 'argv': list(command_for(behavior_contract)),
                         'sources': sorted({case['module'] for case in behavior_contract['cases']}),
                         'detail': '控制器固定行为案例；不执行此标记为Shell命令。',
                         'prerequisites': '需要原可信本地执行许可，基线与候选固定同一契约。',
                         'verification': 'not_run'})
    python = (behavior_contract is not None and behavior_contract['kind'] == 'python-behavior-v1') or any(
        r['argv'][0] == '{python}' for r in selected)
    node = (behavior_contract is not None and behavior_contract['kind'] == 'node-behavior-v1') or any(
        r['argv'][0] in {'node', 'npm', 'npm.cmd'} for r in selected)
    blocked = []
    if python and node:
        blocked.append('Mixed Python/Node environment requires an explicit combined configuration')
    if python:
        for name in ('requirements.txt', 'requirements-dev.txt'):
            for line in texts.get(name, '').splitlines():
                value = line.split(' #', 1)[0].strip()
                if not value or value.startswith('#'):
                    continue
                if not REQUIREMENT.fullmatch(value):
                    blocked.append('Unsupported requirement syntax in '+name+'; no free-form pip options')
                else:
                    requirements.append(value)
        if 'pyproject.toml' in texts:
            try:
                project = tomllib.loads(texts['pyproject.toml']).get('project', {})
                python_constraint = project.get('requires-python')
                dependencies = project.get('dependencies', [])
                require(isinstance(dependencies, list) and len(dependencies) <= 100,
                        'Invalid project dependencies')
                for value in dependencies:
                    if not isinstance(value, str) or not REQUIREMENT.fullmatch(value):
                        blocked.append('Unsupported pyproject dependency; review required')
                    else:
                        requirements.append(value)
                if project.get('dynamic'):
                    blocked.append('Dynamic project metadata requires a separately approved build')
            except (ValueError, TypeError, AttributeError):
                blocked.append('Cannot parse pyproject.toml')
        if any(n in texts for n in ('uv.lock', 'poetry.lock', 'pdm.lock')):
            blocked.append('Alternate Python lock manager detected; do not silently ignore its lock')
    package, lock = {}, None
    if node:
        try:
            package = json.loads(texts.get('package.json', '{}'))
            if package.get('packageManager') and not package['packageManager'].startswith('npm@'):
                blocked.append('Project declares a package manager other than npm')
            if '.npmrc' in texts:
                blocked.append('Project npm configuration requires explicit review')
            if 'package-lock.json' in texts:
                lock = json.loads(texts['package-lock.json'])
                require(lock.get('lockfileVersion') in (2, 3) and isinstance(lock.get('packages'), dict),
                        'Unsupported npm lock format')
            elif package.get('dependencies') or package.get('devDependencies'):
                blocked.append('Node dependencies require an existing package-lock.json')
        except (ValueError, TypeError, AttributeError):
            blocked.append('Invalid package metadata')
        if lock:
            for item in lock['packages'].values():
                resolved = item.get('resolved') if isinstance(item, dict) else None
                if isinstance(resolved,str) and resolved.startswith('file:'):
                    name = resolved[5:]
                    if not name.endswith('.tgz') or name not in tree.entries:
                        blocked.append('Local npm artifact is not an allowed tracked tarball')
                    else:
                        tree.read(name)  # Raw artifact pin, never decode/import it.
    if not selected:
        blocked.append('No reliable functional check discovered')
    warnings += checks['warnings']
    if any(n.startswith('README') for n in texts):
        warnings.append('README is evidence only; its shell commands are never executed')
    pins = [tree.pin(n) for n in sorted(tree.cache)]
    decision_inputs = [p for p in pins if not Path(p['path']).name.startswith('README')
                       and not p['path'].startswith('.github/workflows/')]
    ci_hints = []
    for name,text in texts.items():
        if name.startswith('.github/workflows/'):
            for number,line in enumerate(text.splitlines(),1):
                if re.search(r'python-version:|node-version:|uses:.*setup-|\brun:',line) and len(ci_hints)<30:
                    ci_hints.append({'path':name,'line':number,'excerpt':redact(line)[:300]})
    plan = {'schema_version': 1, 'kind': 'target_environment', 'runtime_authorized': False,
            'head_sha': head_sha, 'read_roots': list(read_roots),
            'repository_id': digest(os.path.normcase(str(repository.common_dir)).encode()),
            'runtime': 'python' if python else 'node' if node else 'unknown',
            'python_constraint':python_constraint,
            'requirements': list(dict.fromkeys(requirements)), 'package': package, 'npm_lock': lock,
            'checks': selected, 'inputs': pins, 'decision_inputs':decision_inputs, 'ci_hints':ci_hints,
            'warnings': warnings, 'blockers': blocked, 'status': 'proposed' if not blocked else 'environment_blocked',
            'support': 'Static flat requirements/PEP621 dependencies or locked npm; no arbitrary setup/README scripts.'}
    if selection is not None:
        plan['selection'] = selection
        plan['warnings'].append('Task subset only: other repository tests have not been selected or run.')
    if behavior_contract is not None:
        import copy
        plan['behavior_contract'] = copy.deepcopy(behavior_contract)
    plan['plan_sha256'] = fingerprint(plan)
    return plan


def validate_plan(plan):
    core = {k: v for k, v in plan.items() if k != 'plan_sha256'}
    require(plan.get('schema_version') == 1 and plan.get('kind') == 'target_environment'
            and plan.get('plan_sha256') == fingerprint(core), 'Environment plan changed')
    require(plan['status'] == 'proposed' and not plan['blockers'], 'Environment plan is blocked')
    from .workflow_behavior import is_behavior, command_for
    markers = [tuple(row['argv']) for row in plan['checks'] if is_behavior(row['argv'])]
    if plan.get('behavior_contract') is not None:
        contract = plan['behavior_contract']
        runtime = 'node' if contract['kind'] == 'node-behavior-v1' else 'python'
        require(plan['runtime'] == runtime and contract['base_sha'] == plan['head_sha']
                and markers == [command_for(contract)], 'Environment behavior contract/check scope differs')
    else:
        require(not markers, 'Environment behavior contract cannot be removed')


def validate_grant(plan, grant):
    validate_plan(plan)
    require(grant.get('grant_sha256') == fingerprint({k:v for k,v in grant.items() if k != 'grant_sha256'})
            and grant.get('plan_sha256') == plan['plan_sha256'] and grant.get('allow_local_code') is True,
            'Explicit trusted-local execution grant required')


def requires_npm(plan):
    if plan['runtime'] != 'node':
        return False
    engines = plan['package'].get('engines', {})
    # Node engine constraints use the semver implementation shipped with npm.
    return bool(plan['npm_lock'] or (isinstance(engines, dict) and engines.get('node'))
                or any(check['argv'][0] in {'npm', 'npm.cmd'} for check in plan['checks']))


def make_grant(plan, *, allow_local_code=False, allow_install=False, allow_network=False,
               wheelhouse=None, index_url='https://pypi.org/simple', registry='https://registry.npmjs.org',
               timeout=120, actor='local-operator'):
    """Maintainer input, never inferred from repository text or an Issue label."""
    validate_plan(plan)
    require(all(type(v) is bool for v in (allow_local_code, allow_install, allow_network)), 'Invalid grant flags')
    require(type(timeout) in (int, float) and 0 < timeout <= LIMITS["test_timeout_max_seconds"], 'Invalid environment timeout')
    for endpoint in (index_url, registry):
        url = urlsplit(endpoint)
        require(url.scheme == 'https' and url.hostname and not (url.username or url.password or url.query or url.fragment),
                'Environment registries require credential-free HTTPS URLs')
    wheels = {}
    if wheelhouse:
        root = unlinked(Path(wheelhouse))
        require(root.is_dir(), 'Wheelhouse missing')
        for path in root.iterdir():
            require(len(wheels) < 100 and unlinked(path).is_file() and path.name.endswith('.whl'),
                    'Wheelhouse must contain only bounded regular wheels')
            require(path.stat().st_size <= 64*1024*1024, 'Wheel exceeds resource limit')
            wheels[path.name] = digest(path.read_bytes())
        wheelhouse = str(root)
    npm = shutil.which('npm.cmd' if os.name == 'nt' else 'npm') if requires_npm(plan) else None
    npm_cli = None
    if npm:
        executable = Path(npm).resolve()
        candidate = executable if executable.suffix=='.js' else executable.parent/'node_modules/npm/bin/npm-cli.js'
        if candidate.is_file():
            npm_cli = str(candidate.resolve())
    grant = {'schema_version': 1, 'plan_sha256': plan['plan_sha256'], 'actor': actor,
             'allow_local_code': allow_local_code, 'allow_install': allow_install,
             'allow_network': allow_network, 'wheelhouse': wheelhouse, 'wheel_pins': wheels,
             'index_url': index_url, 'npm_registry': registry, 'timeout_seconds': timeout,
             'python': str(Path(sys.executable).resolve()),
             'node': str(Path(shutil.which('node')).resolve()) if shutil.which('node') else None, 'npm_cli': npm_cli,
             'isolation': 'explicitly_trusted_local_workspace', 'os_network_isolated': False}
    grant['grant_sha256'] = fingerprint(grant)
    return grant


def classify(result):
    text = result['stdout']+'\n'+result['stderr']
    counts = [int(value) for groups in re.findall(r'Ran (\d+) tests?|(?:#|ℹ) tests (\d+)', text)
              for value in groups if value]
    if not counts:
        counts = [int(n) for n in re.findall(r'\b(\d+) (?:passed|failed|errors?|skipped)\b', text)]
    count = sum(counts) if counts else None
    skipped = [int(value) for groups in re.findall(r'skipped=(\d+)|(?:#|ℹ) skipped (\d+)|(\d+) skipped',text)
               for value in groups if value]
    if result['timed_out']:
        status = 'timeout'
    elif result.get('output_truncated'):
        status = 'inconclusive_truncated'
    elif re.search(r'ModuleNotFoundError|ImportError:|ERR_MODULE_NOT_FOUND|MODULE_NOT_FOUND|Cannot find (?:module|package)', text):
        status = 'environment_blocked'
    elif count == 0 or re.search(r'no tests ran|collected 0 items', text):
        status = 'zero_tests'
    elif count is not None and skipped and max(skipped)>=count:
        status = 'zero_effective_tests'
    elif count is None:
        status = 'inconclusive_no_test_count'
    else:
        status = 'passed' if result['returncode'] == 0 else 'test_failed'
    return {**result, 'category': status, 'tests_run': count}


class TargetEnvironment:
    """Prepare only a controller-owned original worktree; keeps immutable runtime evidence."""
    def __init__(self, source, workspace, owner_dir, plan, grant):
        validate_plan(plan)
        self.source, self.workspace = source, workspace
        self.owner = unlinked(Path(owner_dir))
        self.root = unlinked(self.owner/'environment')
        require(workspace.root == self.owner/'workspace' and workspace.root != source.root
                and workspace.common_dir == source.common_dir and workspace.head == plan['head_sha'],
                'Environment requires the controller-owned worktree at the pinned commit')
        require(not self.owner.is_relative_to(source.root) and not self.owner.is_relative_to(source.common_dir)
                and plan['repository_id']==digest(os.path.normcase(str(source.common_dir)).encode()),
                'Environment owner/source identity differs')
        validate_grant(plan,grant)
        require(not self.root.exists(), 'Never replay environment preparation over an existing intent')
        self.root.mkdir(parents=True)
        self.plan, self.grant = plan, grant
        self.python, self.receipt = None, {'schema_version':1, 'status':'preparing',
            'plan':plan, 'grant':grant, 'commands':[], 'checks':[], 'started_at':now(),
            'runtime_authorized':False, 'isolation':grant['isolation'], 'os_network_isolated':False}
        self.save()

    def save(self):
        path = self.root/'environment.json'
        temporary = self.root/'environment.json.tmp'
        temporary.write_text(redact(json.dumps(self.receipt, ensure_ascii=False, indent=2))+'\n', encoding='utf-8')
        temporary.replace(path)

    def environment(self):
        env = {k:v for k,v in os.environ.items() if k.upper() in {'PATH','SYSTEMROOT','WINDIR','COMSPEC','PATHEXT','LANG'}}
        env.update(HOME=str(self.root/'home'), USERPROFILE=str(self.root/'home'),
                   TMP=str(self.root/'tmp'), TEMP=str(self.root/'tmp'), PYTHONDONTWRITEBYTECODE='1',
                   PYTHONIOENCODING='utf-8', PYTHONNOUSERSITE='1', PYTEST_ADDOPTS='-p no:cacheprovider',
                   PIP_CONFIG_FILE=os.devnull, PIP_NO_INPUT='1',
                   PYTHONPATH=os.pathsep.join(str(p) for p in (self.workspace.root, self.workspace.root/'src') if p.is_dir()))
        for name in ('home','tmp'):
            (self.root/name).mkdir(exist_ok=True)
        return env

    def process(self, argv, *, on_start=None, timeout=None):
        seconds = self.grant['timeout_seconds'] if timeout is None else min(timeout,self.grant['timeout_seconds'])
        row = {'argv':list(argv), 'status':'started', 'time':now(), 'timeout_seconds':seconds}
        self.receipt['commands'].append(row)
        self.save()
        def started(pid):
            from .recovery import process_identity
            row['process_identity'] = process_identity(pid)
            self.save()
            if on_start is not None:
                on_start(pid)
        raw = _capture(list(argv), self.workspace.root, self.environment(), max_bytes=262144,
                       timeout=seconds, on_start=started, timeout_limit=LIMITS["test_timeout_max_seconds"])
        result = {'command':list(argv), 'returncode':raw['returncode'], 'timed_out':raw['timed_out'],
                  'output_truncated':raw['truncated'], 'stdout':redact(raw['stdout'].decode('utf-8', errors='replace')),
                  'stderr':redact(raw['stderr'].decode('utf-8', errors='replace')), 'output_receipt':raw['receipt']}
        row.update(status='finished', result=result)
        self.save()
        return result

    def checked(self, argv):
        result = self.process(argv)
        require(result['returncode'] == 0 and not result['timed_out'] and not result['output_truncated'],
                'Environment setup/probe failed; inspect recorded outcome')
        return result['stdout']

    def verify_inputs(self):
        for pin in self.plan['decision_inputs']:
            require(digest(self.workspace.read(pin['path'])) == pin['sha256'],
                    'Environment/configuration/check inputs changed; comparison invalid')

    def prepare(self):
        try:
            self.verify_inputs()
            if self.plan['runtime'] == 'python':
                runtime = self.root/'venv'
                self.checked([self.grant['python'],'-I','-B','-m','venv','--copies',str(runtime)])
                self.python = str(unlinked(runtime/('Scripts/python.exe' if os.name == 'nt' else 'bin/python')))
                if self.plan['python_constraint']:
                    self.checked([self.python,'-I','-B','-c',
                        'import sys,platform; from pip._vendor.packaging.specifiers import SpecifierSet; sys.exit(0 if SpecifierSet(sys.argv[1]).contains(platform.python_version()) else 2)',
                        self.plan['python_constraint']])
                if self.plan['requirements']:
                    require(self.grant['allow_install'], 'Package installation is not granted')
                    argv = [self.python,'-I','-B','-m','pip','--isolated','install','--disable-pip-version-check',
                            '--no-compile','--only-binary=:all:',
                            '--cache-dir',str(self.root/'pip-cache'),
                            '--report',str(self.root/'pip-report.json')]
                    if self.grant['allow_network']:
                        argv += ['--index-url',self.grant['index_url']]
                    else:
                        require(self.grant['wheelhouse'] and self.grant['wheel_pins'], 'Offline wheelhouse required')
                        wheels = self.root/'wheels'
                        wheels.mkdir()
                        for name,pin in self.grant['wheel_pins'].items():
                            require(Path(name).name == name and name.endswith('.whl'), 'Invalid pinned wheel path')
                            raw = unlinked(Path(self.grant['wheelhouse'])/name).read_bytes()
                            require(digest(raw) == pin, 'Approved wheel artifact changed')
                            (wheels/name).write_bytes(raw)
                        argv += ['--no-index','--find-links',str(wheels)]
                    self.checked([*argv,*self.plan['requirements']])
            else:
                require(self.grant['node'], 'Node runtime unavailable')
                if requires_npm(self.plan):
                    require(self.grant['npm_cli'], 'npm runtime unavailable for the approved environment')
                contract = self.plan.get('behavior_contract')
                if contract and any(Path(c['module']).suffix in {'.ts','.mts','.cts'} for c in contract['cases']):
                    probe = json.loads(self.checked([self.grant['node'], '-p',
                        'JSON.stringify({version:process.versions.node,typescript:process.features.typescript})']))
                    version = tuple(int(part) for part in probe['version'].split('.'))
                    require(version >= (22,18,0) and probe.get('typescript') == 'strip',
                            'Unsupported TypeScript runtime: original Node native stripping is required')
                require(not (self.workspace.root/'.npmrc').exists(), 'Project npm configuration is not approved')
                constraint = self.plan['package'].get('engines',{}).get('node')
                if constraint:
                    semver = Path(self.grant['npm_cli']).parent.parent/'node_modules/semver'
                    self.checked([self.grant['node'],'-e',
                        'process.exit(require(process.argv[1]).satisfies(process.version,process.argv[2])?0:2)',str(semver),constraint])
                lock = self.plan['npm_lock']
                if lock:
                    require(self.grant['allow_install'], 'npm installation is not granted')
                    registry_host = urlsplit(self.grant['npm_registry']).netloc
                    for item in lock['packages'].values():
                        require(isinstance(item,dict) and not item.get('link'), 'Linked npm dependencies need review')
                        resolved = item.get('resolved')
                        if not resolved:
                            continue
                        if resolved.startswith('file:'):
                            name = resolved[5:]
                            require(name.endswith('.tgz') and any(p['path']==name for p in self.plan['inputs']),
                                    'Local npm tarball must be tracked, scoped and pinned')
                        else:
                            url = urlsplit(resolved)
                            require(self.grant['allow_network'] and url.scheme=='https' and url.netloc==registry_host
                                    and not (url.username or url.password or url.query or url.fragment),
                                    'npm dependency transport is outside the grant')
                    for name in ('user.npmrc','global.npmrc'):
                        (self.root/name).write_text('',encoding='utf-8')
                    argv = [self.grant['node'],self.grant['npm_cli'],'ci','--ignore-scripts','--no-audit','--no-fund',
                            '--registry',self.grant['npm_registry'],'--cache',str(self.root/'npm-cache'),
                            '--userconfig',str(self.root/'user.npmrc'),'--globalconfig',str(self.root/'global.npmrc')]
                    if not self.grant['allow_network']:
                        argv += ['--offline']
                    self.checked(argv)
            self.binding = self.identity()
            self.receipt.update(status='prepared', environment_binding=self.binding,
                                environment_sha256=fingerprint(self.binding))
            self.save()
        except Exception as error:
            self.receipt.update(status='environment_blocked', error_type=type(error).__name__, message=redact(str(error)))
            self.save()
            raise
        return self

    def identity(self):
        self.verify_inputs()
        if self.plan['runtime'] == 'python':
            value = json.loads(self.checked([self.python,'-I','-B','-c',
                'import sys,sysconfig,json,platform,importlib.metadata as m; print(json.dumps({"version":sys.version,"platform":platform.platform(),"machine":platform.machine(),"executable":sys.executable,"site":sysconfig.get_path("purelib"),"packages":sorted((d.metadata["Name"],d.version) for d in m.distributions())}))']))
            python = unlinked(Path(self.python))
            site = unlinked(Path(value['site']))
            require(unlinked(Path(value['executable'])) == python
                    and site.is_relative_to(unlinked(self.root/'venv')), 'Prepared venv path differs')
            previous_runtime = self.receipt.get('environment_binding', {}).get('runtime')
            if previous_runtime is None or 'venv_executable_sha256' in previous_runtime:
                # Windows venv uses a launcher distinct from the granted interpreter.
                value.update(executable=str(python), site=str(site), venv_executable_sha256=digest(python.read_bytes()))
            dependencies = inventory(site)
            executable = self.grant['python']
        else:
            value = {'node_version':self.checked([self.grant['node'],'--version']).strip()}
            original = self.receipt.get('environment_binding', {}).get('runtime', {})
            if requires_npm(self.plan) or 'npm_cli_sha256' in original:
                # Reopened receipts that already bound npm keep their exact comparison.
                value.update(npm_version=self.checked([self.grant['node'],self.grant['npm_cli'],'--version']).strip(),
                             npm_cli_sha256=digest(unlinked(Path(self.grant['npm_cli'])).read_bytes()))
            node_modules = unlinked(self.workspace.root/'node_modules')
            dependencies = inventory(node_modules) if node_modules.exists() else {}
            executable = self.grant['node']
        return {'runtime':value, 'runtime_executable_sha256':digest(unlinked(Path(executable)).read_bytes()),
                'dependencies_sha256':fingerprint(dependencies),
                'inputs_sha256':fingerprint(self.plan['decision_inputs']), 'checks_sha256':fingerprint(self.plan['checks']),
                'base_sha':self.plan['head_sha'], 'grant_sha256':self.grant['grant_sha256']}

    def check_argv(self, command):
        require(list(command) in [r['argv'] for r in self.plan['checks']], 'Check is outside the approved environment plan')
        from .workflow_behavior import is_behavior, command_for
        if is_behavior(command):
            require(tuple(command) == command_for(self.plan['behavior_contract']), 'Behavior check binding differs')
            return list(command)  # Controller marker, never sent to process().
        argv = [self.python if a=='{python}' else a for a in command]
        if argv[0]=='node':
            argv[0] = self.grant['node']
        elif argv[0] in {'npm','npm.cmd'}:
            argv = [self.grant['node'],self.grant['npm_cli'],*argv[1:]]
        return argv

    def revision_checks(self, results, commands):
        """Data projection only; bind actual receipts before using policy templates."""
        require(self.receipt['status']=='prepared' and self.identity()==self.binding,
                'Environment changed before revision projection')
        require(results and len(results)==len(commands), 'Incomplete revision check receipts')
        projected=[]
        for result,command in zip(results,commands):
            argv=self.check_argv(command)
            actual={k:v for k,v in result.items() if k not in {'environment_sha256','base_sha','input_sha256','time'}}
            require(result.get('command')==argv and result.get('base_sha')==self.plan['head_sha']
                    and result.get('environment_sha256')==fingerprint(self.binding)
                    and actual in self.receipt['checks'], 'Revision result is not an original environment check')
            projected.append({**result,'command':[sys.executable if p=='{python}' else p for p in command],
                              'execution_command':list(argv)})
        return projected

    def run_check(self, command, workspace=None, timeout=None, on_start=None, *, behavior_phase='candidate'):
        require(workspace is None or Path(workspace).resolve()==self.workspace.root, 'Environment belongs to another worktree')
        require(list(command) in [r['argv'] for r in self.plan['checks']], 'Check is outside the approved environment plan')
        require(self.receipt['status']=='prepared' and self.identity()==self.binding, 'Environment changed before check')
        argv = self.check_argv(command)
        from .workflow_behavior import is_behavior, run_environment_check
        result = (run_environment_check(self, command, timeout=timeout, on_start=on_start, phase=behavior_phase)
                  if is_behavior(command) else classify(self.process(argv,on_start=on_start,timeout=timeout)))
        require(self.identity()==self.binding, 'Environment changed during check; outcome is not comparable')
        self.receipt['checks'].append(result)
        self.save()
        return {**result, 'environment_sha256':fingerprint(self.binding), 'base_sha':self.plan['head_sha'],
                'input_sha256':fingerprint(self.workspace.snapshot()['files']), 'time':now()}


def compare_environment_checks(baseline, candidate):
    require(baseline and len(baseline)==len(candidate), 'Incomplete environment check pair')
    pairs = []
    for old,new in zip(baseline,candidate):
        require(all(old.get(k)==new.get(k) for k in ('environment_sha256','base_sha','command')),
                'Baseline and candidate runtime/dependencies/locks/checks differ')
        ok = old['category'] in {'passed','test_failed'} and new['category'] in {'passed','test_failed'}
        transition = ('repaired' if old['category']=='test_failed' else 'maintained') if new['category']=='passed' else (
            'regressed' if old['category']=='passed' else 'still_failing')
        pairs.append({'transition':transition if ok else 'inconclusive',
                      'baseline_category':old['category'],'candidate_category':new['category'],
                      'baseline_input_sha256':old.get('input_sha256'),'candidate_input_sha256':new.get('input_sha256')})
    return {'schema_version':1,'checks':pairs,'comparable':all(p['transition']!='inconclusive' for p in pairs),
            'candidate_verified':all(p['transition'] in {'repaired','maintained'} for p in pairs),
            'scope':'Same environment and declared checks only; not whole-program correctness.'}


def collect_environment_baseline(repository, plan, grant, runs_root):
    """Own one fresh worktree. Installation failure retains evidence, never resets source."""
    from .store import RunStore
    validate_grant(plan,grant)
    before = repository.snapshot()
    repository.require_clean(before)
    require(before['head']==plan['head_sha'], 'Baseline must match current source commit')
    root = unlinked(Path(runs_root))
    require(not root.is_relative_to(repository.root) and not root.is_relative_to(repository.common_dir),
            'Environment runs must be outside source and Git metadata')
    identity = uuid.uuid4().hex
    owner, branch = root/identity, 'maintenance/'+identity
    owner.mkdir(parents=True,exist_ok=False)
    record = {'schema_version':1,'run_kind':'target_environment_baseline','run_id':identity,'mode':'baseline',
              'source_repo':str(repository.root),'source_before':before,'base_sha':plan['head_sha'],
              'workspace':str(owner/'workspace'),'branch':branch,'events':[], 'checks':[],
              'plan_sha256':plan['plan_sha256'],'grant_sha256':grant['grant_sha256'],
              'model_requests':0,'integration':'disabled'}
    store = RunStore(owner,record)
    store.event('environment_workspace_intent')
    workspace, safe_cleanup = None, False
    try:
        workspace = repository.create_workspace(owner/'workspace',branch,plan['head_sha'])
        env = TargetEnvironment(repository,workspace,owner,plan,grant).prepare()
        snapshot, files = workspace.snapshot(), inventory(workspace.root)
        for selected in plan['checks']:
            record['checks'].append(env.run_check(selected['argv'],behavior_phase='baseline'))
            require(workspace.snapshot()==snapshot and inventory(workspace.root)==files,
                    'Checks modified owned workspace/Git metadata; retain for inspection')
            store.save()
        safe_cleanup = True
        record['environment_sha256'] = fingerprint(env.binding)
        categories = [r['category'] for r in record['checks']]
        store.event('baseline_ready' if 'test_failed' in categories and all(c in {'passed','test_failed'} for c in categories)
                    else 'baseline_passes' if all(c=='passed' for c in categories) else 'environment_blocked')
    except (Exception,KeyboardInterrupt) as error:
        record['error'] = {'type':type(error).__name__,'detail':redact(str(error))}
        store.event('environment_blocked')
    finally:
        if safe_cleanup:
            try:
                require(workspace.snapshot()==snapshot and inventory(workspace.root)==files,
                        'Workspace changed before cleanup')
                repository.discard_candidate(workspace.root,branch,plan['head_sha'],owner)
                record['cleanup'] = 'candidate_removed'
            except Exception as error:
                record['cleanup'] = 'retained'
                record['cleanup_error_type'] = type(error).__name__
        else:
            record['cleanup'] = 'retained' if workspace else 'not_created_or_partial'
        after = repository.snapshot(exclude_branch=branch if (owner/'workspace').exists() else '')
        record.update(source_after=after,source_unchanged=before==after,finished_at=now())
        if not record['source_unchanged']:
            store.event('source_changed')
        store.save()
    return record


def main(argv=None):
    from .git import RepositoryGateway
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    discover_cli = sub.add_parser('discover')
    discover_cli.add_argument('--repo', type=Path, required=True)
    discover_cli.add_argument('--head', required=True)
    discover_cli.add_argument('--read-root', action='append', default=[])
    discover_cli.add_argument('--investigation', type=Path,
                              help='Pinned W1/W2 report for bounded task-related test selection')
    discover_cli.add_argument('--output', type=Path, required=True)
    grant_cli = sub.add_parser('grant')
    grant_cli.add_argument('--plan',type=Path,required=True)
    grant_cli.add_argument('--output',type=Path,required=True)
    for name in ('allow-local-code','allow-install','allow-network'):
        grant_cli.add_argument('--'+name,action='store_true')
    grant_cli.add_argument('--wheelhouse',type=Path)
    run_cli = sub.add_parser('baseline')
    run_cli.add_argument('--repo',type=Path,required=True)
    run_cli.add_argument('--plan',type=Path,required=True)
    run_cli.add_argument('--grant',type=Path,required=True)
    run_cli.add_argument('--runs-root',type=Path,required=True)
    args = parser.parse_args(argv)
    def load(path):
        path = unlinked(path)
        require(path.stat().st_size<=1024*1024,'Environment input exceeds 1MiB')
        return json.loads(path.read_text(encoding='utf-8'))
    if args.action=='baseline':
        repository = RepositoryGateway(args.repo)
        require(not unlinked(args.grant).is_relative_to(repository.root)
                and not unlinked(args.grant).is_relative_to(repository.common_dir),
                'Maintainer grant must be outside target repository/Git metadata')
        result = collect_environment_baseline(repository,load(args.plan),load(args.grant),args.runs_root)
        print(json.dumps({'status':result['status'],'run_id':result['run_id'],
                          'source_unchanged':result['source_unchanged'],'model_requests':0}))
        return
    output = unlinked(args.output)
    require(not output.exists(),'Never overwrite environment evidence')
    if args.action=='discover':
        repository = RepositoryGateway(args.repo)
        require(not output.is_relative_to(repository.root) and not output.is_relative_to(repository.common_dir),
                'Environment proposal must be outside target source/Git metadata')
        value = discover_environment(repository,args.head,args.read_root,
                                     investigation=load(args.investigation) if args.investigation else None)
    else:
        value = make_grant(load(args.plan),allow_local_code=args.allow_local_code,allow_install=args.allow_install,
                           allow_network=args.allow_network,wheelhouse=args.wheelhouse)
    with output.open('x',encoding='utf-8') as stream:
        json.dump(value,stream,ensure_ascii=False,indent=2)
    print(json.dumps({'output':str(output),'model_requests':0}))


if __name__=='__main__':
    main()
