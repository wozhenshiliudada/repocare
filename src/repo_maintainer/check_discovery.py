"""Bounded, local discovery. Repository content is evidence, never an instruction."""
from __future__ import annotations
from .maintenance_limits import LIMITS

import ast
import configparser
import json
import os
import re
import tomllib
from pathlib import PurePosixPath

from .models import SafetyError, digest

CONFIGS = {'package.json', 'pyproject.toml', 'pytest.ini', 'setup.cfg', 'tox.ini',
           'requirements.txt', 'requirements-dev.txt', 'README.md', 'README.rst', 'README'}
CODE_SUFFIXES = {'.py', '.js', '.mjs', '.cjs', '.ts', '.mts', '.cts', '.tsx', '.jsx'}




def is_test(name):
    path = PurePosixPath(name)
    return (any(p in {'test', 'tests', '__tests__'} for p in path.parts)
            or path.name.startswith('test_') or bool(re.search(r'\.(test|spec)\.', path.name)))


def discover(repository, files):
    """Read at most 40 metadata/test files within the configured per-file bound; do not import project code."""
    configs = sorted(n for n in files if n in CONFIGS or n.startswith('.github/workflows/'))
    test_files = sorted(n for n in files if is_test(n) and PurePosixPath(n).suffix in CODE_SUFFIXES)
    selected = list(dict.fromkeys(configs + test_files))[:40]
    inputs, texts, warnings = {}, {}, []
    for name in selected:
        raw = repository.read(name)
        if len(raw) > LIMITS["source_file_max_bytes"]:
            raise SafetyError('检查配置超出读取上限，请重新检查仓库')
        inputs[name] = digest(raw)
        try:
            texts[name] = raw.decode('utf-8')
        except UnicodeDecodeError:
            warnings.append(f'{name} 不是 UTF-8 文本，未解析。')
    if len(configs) + len(test_files) > 40:
        warnings.append('检查线索较多，仅解析前40个文件；结果可能不完整。')
    recommendations = []

    def add(identity, title, argv, sources, detail, prerequisites):
        recommendations.append({'id': identity, 'title': title, 'argv': argv,
            'sources': sources, 'detail': detail, 'prerequisites': prerequisites,
            'verification': 'not_run'})

    unittest_files, pytest_files = [], []
    for name in test_files:
        if name not in texts or not name.endswith('.py'):
            continue
        try:
            tree = ast.parse(texts[name])
        except (SyntaxError, ValueError, RecursionError):
            continue
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(a.name.split('.')[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.add((node.module or '').split('.')[0])
        case_names = {'TestCase'}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == 'unittest':
                case_names.update(a.asname or a.name for a in node.names if a.name == 'TestCase')
        has_cases = any(isinstance(node, ast.ClassDef) and
            any((isinstance(b, ast.Attribute) and b.attr == 'TestCase') or
                (isinstance(b, ast.Name) and b.id in case_names) for b in node.bases) and
            any(isinstance(f, ast.FunctionDef) and f.name.startswith('test') for f in node.body)
            for node in ast.walk(tree))
        if 'unittest' in imports and has_cases and PurePosixPath(name).name.startswith('test'):
            unittest_files.append(name)
        if 'pytest' in imports or any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                                     and n.name.startswith('test_') for n in tree.body):
            pytest_files.append(name)
    config_pytest = []
    if 'pyproject.toml' in texts:
        try:
            value = tomllib.loads(texts['pyproject.toml'])
            if isinstance(value.get('tool', {}).get('pytest'), dict):
                config_pytest.append('pyproject.toml')
        except (ValueError, TypeError, AttributeError):
            warnings.append('pyproject.toml 无法解析，未从中推断命令。')
    for name, section in [('pytest.ini', 'pytest'), ('tox.ini', 'pytest'), ('setup.cfg', 'tool:pytest')]:
        if name in texts:
            try:
                parser = configparser.ConfigParser(interpolation=None)
                parser.read_string(texts[name])
                if parser.has_section(section):
                    config_pytest.append(name)
            except configparser.Error:
                warnings.append(f'{name} 无法解析，未从中推断命令。')
    if unittest_files:
        # One discover root per observed test tree; no broad top-level zero-test success.
        def test_root(name):
            parts = PurePosixPath(name).parts
            for index, part in enumerate(parts[:-1]):
                if part in {'tests', 'test'}:
                    return '/'.join(parts[:index + 1])
            return str(PurePosixPath(name).parent)
        roots = sorted({test_root(n) for n in unittest_files})[:3]
        for index, root in enumerate(roots):
            add('unittest-' + str(index), 'Python 功能测试（unittest）',
                ['{python}', '-m', 'unittest', 'discover', '-s', root, '-v'],
                [n for n in unittest_files if test_root(n) == root][:5],
                f'运行 {root} 中的测试，比较功能结果是否符合已有断言。',
                '需要项目所需依赖；发现命令不代表环境就绪或已有测试通过。')
    if pytest_files or config_pytest:
        add('pytest', 'Python 功能测试（pytest）', ['{python}', '-m', 'pytest', '-q'],
            (config_pytest + pytest_files)[:5], '运行项目的 pytest 测试；具体范围由项目配置决定。',
            '需要 pytest 和项目依赖；插件及 conftest.py 也属于将执行的仓库代码。')
    if 'package.json' in texts:
        try:
            package = json.loads(texts['package.json'])
            scripts = package.get('scripts', {})
            if not isinstance(scripts, dict):
                raise ValueError()
            for key, title in [('test', 'Node 功能测试'), ('build', '项目构建检查'), ('lint', '代码规范检查')]:
                script = scripts.get(key)
                if isinstance(script, str) and script.strip() and 'no test specified' not in script.lower():
                    add('npm-' + key, title,
                        ['npm.cmd' if os.name == 'nt' else 'npm', '--ignore-scripts', 'run', key],
                        ['package.json'], f'执行项目声明的 {key} 脚本。' +
                        ('构建/规范通过不等于功能没有 bug。' if key != 'test' else ''),
                        '需要本机 npm 和已有依赖；显式脚本仍会执行仓库代码，不会自动安装依赖。')
        except (ValueError, TypeError, AttributeError):
            warnings.append('package.json 无法解析，未从中推断命令。')
    node_files = [n for n in test_files if n in texts and n.endswith(('.js', '.mjs', '.cjs'))
                  and re.search(r"(?:from\s*|require\s*\()\s*['\"]node:test['\"]", texts[n])]
    if node_files:
        add('node-native', 'Node 原生功能测试', ['node', '--test', *node_files[:8]], node_files[:5],
            '运行已识别的 node:test 测试文件（最多8个），不代表覆盖整个仓库。', '需要支持 node:test 的 Node 版本及项目依赖。')
    if any(n.startswith('.github/workflows/') for n in configs):
        warnings.append('发现 GitHub CI 配置；复杂服务、矩阵及远程命令未自动转换，请结合 CI 手动确认。')
    if any(n.startswith('README') for n in configs):
        warnings.append('发现 README，可用于确认环境说明；不会执行其中的自由文本命令。')
    if 'tox.ini' in configs:
        warnings.append('发现 tox 配置；本轮不创建 tox 环境、不安装依赖，复杂环境需手动配置。')
    editable = [n for n in files if PurePosixPath(n).suffix in CODE_SUFFIXES and not is_test(n)]
    packages = {n.split('/')[0] for n in files if n.count('/') == 1 and n.endswith('/__init__.py')}
    auxiliary = {'examples', 'example', 'docs', 'doc', 'build_tools', 'scripts', 'benchmarks', 'benchmark',
                 'tools', 'vendor', 'third_party'}
    def source_rank(name):
        path = PurePosixPath(name)
        top = path.parts[0]
        if top in auxiliary or path.name in {'setup.py', 'conftest.py', 'conf.py'}:
            return (3, name)
        if top in {'src', 'lib', 'app', 'server', 'client'} or top in packages:
            return (0, name)
        return (1 if len(path.parts) == 1 else 2, name)
    editable.sort(key=source_rank)
    readonly = list(dict.fromkeys(test_files + [n for n in configs if not n.startswith('.github/')]))
    editable = editable[:min(12, max(0, 20 - min(len(readonly), 8)))]
    if len([n for n in files if PurePosixPath(n).suffix in CODE_SUFFIXES and not is_test(n)]) > len(editable):
        warnings.append('建议范围只覆盖部分源码，优先核心包；不支持把“检查整个仓库所有问题”当作完整扫描。请按具体问题调整范围。')
    readonly = readonly[:20 - len(editable)]
    return {'schema_version': 1, 'recommendations': recommendations[:8], 'inputs': inputs,
            'warnings': warnings, 'suggested_editable': editable, 'suggested_readonly': readonly,
            'test_files_found': len(test_files), 'verification': 'not_run',
            'status': 'recommended' if recommendations else 'no_reliable_command',
            'scope_note': '这是有界建议，不是全仓库授权；请确认，必要时在高级设置调整。'}


def analyze_readonly(repository, source):
    """Local syntax/coverage-gap report. No model, test process, edits or success badge."""
    files = source['files']
    names = [n for n in files if n.endswith('.py')][:20]
    results, pins = [], {}
    for name in names:
        raw = repository.read(name)
        if len(raw) > LIMITS["source_file_max_bytes"]:
            raise SafetyError('分析文件超出读取上限')
        pins[name] = digest(raw)
        try:
            ast.parse(raw, filename=name)
        except SyntaxError as error:
            results.append({'file': name, 'status': 'syntax_error', 'line': error.lineno,
                            'detail': '发现 Python 语法错误；未执行代码，未修改文件。'})
        except (ValueError, RecursionError):
            results.append({'file': name, 'status': 'unreadable', 'detail': '语法解析未完成。'})
        else:
            results.append({'file': name, 'status': 'parsed', 'detail': '语法可解析；不代表功能正确。'})
    for name, pin in pins.items():
        if digest(repository.read(name)) != pin:
            raise SafetyError('分析期间源码已变化，请重新检查')
    return {'mode': 'local_readonly', 'verification': 'not_run', 'paid_model_requests': 0,
            'commands_executed': 0, 'files_changed': 0, 'syntax_results': results,
            'analyzed_files': names, 'repository_revision': source['revision'],
            'source_hashes': pins, 'check_status': source['checks']['status'],
            'summary': '只读分析完成；未运行功能测试，项目仍尚未验证。',
            'next_steps': ['确认项目启动方式和依赖环境。',
                           '选一个具体问题，写下输入、预期结果与实际结果。',
                           '为该问题补充可审阅的测试或确认人工验证步骤；本轮不会自动生成或修改测试。',
                           '确认有效检查和维护范围后，再进入原审批修复流程。']}
