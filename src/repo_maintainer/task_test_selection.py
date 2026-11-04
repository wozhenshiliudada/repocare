"""Derive bounded test inputs from pinned investigation evidence, never its argv."""
from pathlib import PurePosixPath

from .check_discovery import CODE_SUFFIXES, is_test
from .issue_intake import fingerprint
from .models import SafetyError


class NoTaskTests(SafetyError):
    """No pinned check exists; controller may propose behavior, not waive checks."""


def require(condition, message):
    if not condition:
        raise SafetyError(message)


def select_tests(tree, report, *, allow_empty=False):
    require(isinstance(report, dict) and report.get('schema_version') == 1
            and report.get('runtime_authorized') is False
            and report.get('git', {}).get('head_sha') == tree.head,
            'Task investigation target differs')
    require(len(str(report).encode('utf-8')) <= 524288, 'Task investigation exceeds bound')
    discovery = report.get('discovery')
    require(isinstance(discovery, dict) and discovery.get('target_sha') == tree.head,
            'Task investigation source discovery missing or stale')
    rows, locations = report.get('file_candidates'), discovery.get('verified_locations', [])
    require(isinstance(rows, list) and len(rows) <= 12
            and isinstance(locations, list) and len(locations) <= 100,
            'Task investigation candidates exceed bound')
    tests = sorted(n for n in tree.entries if is_test(n) and PurePosixPath(n).suffix in CODE_SUFFIXES)
    ranked, required = [], []

    def verify(row):
        name = row['path']
        tree.read(name)
        actual = tree.pin(name)
        require(all(row.get(k) == actual[k] for k in ('commit_sha', 'blob', 'sha256')),
                'Task test pin differs from actual committed bytes')
        return name

    # Every observed failing test location takes priority. More than three is an
    # explicit capacity stop; do not make a known failure disappear to fit a limit.
    for row in locations:
        require(isinstance(row, dict), 'Invalid task location')
        if row.get('path') and is_test(row['path']):
            require(row.get('status') == 'verified_location', 'Unverified required test location')
            name = verify(row)
            require(name in tests, 'Required test is outside supported scoped sources')
            if name not in required:
                required.append(name)
    require(len(required) <= 3, 'Too many required test locations; scope review required')
    for row in rows:
        require(isinstance(row, dict) and isinstance(row.get('path'), str), 'Invalid task candidate')
        name = row['path']
        if not is_test(name):
            continue
        require(row.get('role') == 'test', 'Task candidate test role differs')
        name = verify(row)
        require(name in tests, 'Task test is outside supported scoped sources')
        if name not in ranked:
            ranked.append(name)
    selected = (required + [n for n in ranked if n not in required])[:3]
    if not selected and allow_empty is not True:
        raise NoTaskTests('No pinned task test found; investigate before environment preparation')
    return {'kind': 'task-tests-v1', 'investigation_sha256': fingerprint(report),
            'selected_tests': selected, 'required_test_locations': required,
            'unselected_candidates': [n for n in ranked if n not in selected],
            'catalog_test_count': len(tests), 'unselected_test_count': len(tests) - len(selected),
            'catalog_truncated': tree.tree_truncated, 'complete_repository_review': False,
            'runtime_authorized': False,
            'interpretation': 'At most three evidence-ranked test files. Remaining tests are not covered; '
                              'test imports/hooks may execute other trusted project code.'}


def focused_checks(discovery, selection):
    """Existing static detection, with fixed narrow templates; no broad fallback."""
    tests = selection['selected_tests']
    recommendations = discovery['recommendations']
    unittest_sources = {n for r in recommendations if r['id'].startswith('unittest-') for n in r['sources']}
    python = [n for n in tests if n.endswith('.py')]
    node = [n for n in tests if n.endswith(('.js', '.mjs', '.cjs'))]
    require(len(python) + len(node) == len(tests), 'Task test language requires explicit configuration')
    result = []

    def add(identity, argv, sources):
        result.append({'id': identity, 'title': '任务相关功能测试', 'argv': argv, 'sources': sources,
            'detail': '只发现所选测试文件；不代表全仓覆盖。',
            'prerequisites': '需要已获许可的可信项目代码和依赖环境。', 'verification': 'not_run'})

    if python and all(n in unittest_sources for n in python):
        for i, name in enumerate(python):
            path = PurePosixPath(name)
            # unittest imports modules by Python identifier. Do not propose a
            # zero-test command for bracket/glob/option-like basenames.
            require(path.stem.isidentifier(), 'Unsupported unittest module filename')
            add('unittest-' + str(i), ['{python}', '-m', 'unittest', 'discover',
                '--start-directory=' + str(path.parent), '--pattern=' + path.name, '-v'], [name])
    elif python:
        pytest = next((r for r in recommendations if r['id'] == 'pytest'), None)
        require(pytest is not None, 'Task Python tests have no reliable fixed check template')
        add('pytest', ['{python}', '-m', 'pytest', '-q', '--', *('./' + n for n in python)], python)
    if node:
        native = next((r for r in recommendations if r['id'] == 'node-native'), None)
        require(native is not None and all(n in native['argv'][2:] for n in node),
                'Task Node tests require a supported node:test template')
        add('node-native', ['node', '--test', *('./' + n for n in node)], node)
    return result
