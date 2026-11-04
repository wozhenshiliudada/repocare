"""Optional, explicit-source Pyright check. Never imports the checked project.

Use as an additional fixed Policy test in a NEW task, after checking its baseline.
0 = no selected static errors, 1 = static errors, 2 = unsupported/infra failure.
This partial static view is not a runtime environment or a correctness proof.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import tempfile
import tokenize
import io

VERSION = '1.1.414'
TOOL = Path(__file__).resolve().parents[1] / 'runtime/pyright'
RULES = ('reportCallIssue', 'reportAttributeAccessIssue',
         'reportUndefinedVariable', 'reportUnboundVariable')
MAX_OUTPUT = 2 * 1024 * 1024


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sources(root, names, checks):
    require(0 < len(names) <= 50 and len(names) == len({name.casefold() for name in names}),
            'Require 1..50 distinct explicit sources')
    require(0 < len(checks) <= 20 and len(checks) == len(set(checks)) and set(checks) <= set(names),
            'Require 1..20 distinct checked files included in sources')
    result = {}
    for name in names:
        parts = PurePosixPath(name).parts
        require(isinstance(name, str) and parts and '/'.join(parts) == name
                and not name.startswith('/') and not any(p in ('.', '..') or ':' in p or '\\' in p
                    or any(c in p for c in '*?[]') for p in parts)
                and PurePosixPath(name).suffix in ('.py', '.pyi'), 'Invalid explicit Python source path')
        current = root
        for part in parts:
            current = current / part
            info = current.lstat()
            require(not stat.S_ISLNK(info.st_mode) and not getattr(info, 'st_file_attributes', 0) & 0x400,
                    'Linked source paths are unsupported')
        require(current.is_file() and current.resolve().is_relative_to(root), 'Source must stay inside root')
        data = current.read_bytes()
        require(len(data) <= 100000, 'Source exceeds 100000 bytes')
        # A candidate must not change this test's configuration using source comments.
        # Any/unknown types can still hide mistakes: the receipt explicitly says so.
        for token in tokenize.tokenize(io.BytesIO(data).readline):
            if token.type == tokenize.COMMENT:
                require(not re.search(r'(?i)\bpyright\s*:', token.string),
                        'Inline type-check suppression/configuration is unsupported')
        result[name] = data
    return result


def configuration(stage, checks, version, platform):
    require(re.fullmatch(r'3\.(?:1[1-4])', version) is not None, 'Supported static Python versions: 3.11..3.14')
    require(platform in ('Linux', 'Windows', 'Darwin'), 'Explicit static platform required')
    # An empty site-packages dir prevents interpreter search. The explicit nonexistent
    # pythonpath is a second guard against PATH-based interpreter fallback.
    (stage / 'environments/empty/lib/site-packages').mkdir(parents=True)
    (stage / 'empty-stubs').mkdir()
    config = {'include': ['source/' + name for name in checks],
              'executionEnvironments': [{'root': 'source', 'extraPaths': []}],
              'pythonVersion': version, 'pythonPlatform': platform,
              'venvPath': 'environments', 'venv': 'empty', 'stubPath': 'empty-stubs',
              'useLibraryCodeForTypes': True, 'typeCheckingMode': 'off',
              'enableTypeIgnoreComments': False,
              'reportMissingImports': 'warning', 'reportUnknownMemberType': 'warning',
              'reportUnknownVariableType': 'warning'}
    config.update({rule: 'error' for rule in RULES})
    return config


def verified_output(value, code, view, checks):
    require(code in (0, 1) and isinstance(value, dict) and value.get('version') == VERSION,
            'Checker failed or version changed')
    summary, rows = value.get('summary'), value.get('generalDiagnostics')
    require(isinstance(summary, dict) and isinstance(rows, list) and len(rows) <= 10000
            and type(summary.get('filesAnalyzed')) is int and summary['filesAnalyzed'] == len(checks),
            'Incomplete checker coverage')
    counts = {level: 0 for level in ('error', 'warning', 'information')}
    cleaned = []
    for row in rows:
        require(isinstance(row, dict) and row.get('severity') in counts and isinstance(row.get('message'), str)
                and isinstance(row.get('file'), str), 'Malformed checker diagnostic')
        file = Path(row['file']).resolve()
        require(file.is_relative_to(view), 'Checker diagnostic outside explicit source view')
        name = file.relative_to(view).as_posix()
        require(name in checks, 'Checker reported a file outside selected checks')
        start = row.get('range', {}).get('start', {})
        require(type(start.get('line')) is int and start['line'] >= 0
                and type(start.get('character')) is int and start['character'] >= 0,
                'Malformed checker location')
        counts[row['severity']] += 1
        cleaned.append({'path': name, 'line': start['line'] + 1, 'column': start['character'] + 1,
                        'severity': row['severity'], 'rule': row.get('rule'), 'message': row['message'][:2000]})
    for level, total in counts.items():
        require(type(summary.get(level + 'Count')) is int and summary[level + 'Count'] == total,
                'Checker counts disagree')
    require(code == (1 if counts['error'] else 0), 'Checker exit/count disagreement')
    return sorted(cleaned, key=lambda row: (row['severity'] != 'error', row['path'], row['line'])), counts


def evaluate(root, names, checks, node, version, platform):
    root = Path(root).resolve(strict=True)
    node = Path(node)
    require(node.is_absolute() and node.is_file() and not node.resolve().is_relative_to(root),
            'Explicit trusted Node executable outside checked root required')
    require(not TOOL.resolve().is_relative_to(root), 'Checker must be outside checked root')
    package = json.loads((TOOL / 'node_modules/pyright/package.json').read_text(encoding='utf-8'))
    lock = json.loads((TOOL / 'package-lock.json').read_text(encoding='utf-8'))
    require(package.get('version') == VERSION
            and lock['packages']['node_modules/pyright']['version'] == VERSION, 'Install the pinned optional checker')
    before = sources(root, names, checks)
    with tempfile.TemporaryDirectory(prefix='repocare-static-') as temp:
        stage = Path(temp).resolve()
        require(not stage.is_relative_to(root), 'Static scratch directory must be outside checked root')
        view = stage / 'source'
        for name, data in before.items():
            target = view / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        config = configuration(stage, checks, version, platform)
        path = stage / 'pyrightconfig.json'
        path.write_text(json.dumps(config), encoding='utf-8')
        command = [str(node), '--max-old-space-size=512', str(TOOL / 'node_modules/pyright/index.js'),
                   '--project', str(path), '--outputjson', '--pythonpath', str(stage / 'NO_INTERPRETER')]
        # No model keys, NODE_OPTIONS, PYTHONPATH, repo PATH, or user config inherited.
        env = {key: value for key, value in os.environ.items()
               if key.upper() in ('SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP')}
        with (stage / 'stdout').open('wb') as stdout, (stage / 'stderr').open('wb') as stderr:
            process = subprocess.run(command, cwd=stage, env=env, stdout=stdout, stderr=stderr, timeout=60)
        require((stage / 'stdout').stat().st_size <= MAX_OUTPUT
                and (stage / 'stderr').stat().st_size <= MAX_OUTPUT, 'Checker output too large')
        diagnostic = (stage / 'stderr').read_text(encoding='utf-8', errors='replace').strip()
        require(not diagnostic, 'Checker configuration/infra diagnostics: ' + diagnostic[:1800])
        rows, counts = verified_output(json.loads((stage / 'stdout').read_text(encoding='utf-8')),
                                       process.returncode, view, checks)
        require(sources(root, names, checks) == before, 'Explicit source changed during static check')
    errors = [row for row in rows if row['severity'] == 'error']
    result = {'schema_version': 1, 'kind': 'python-binding-check', 'tool': 'pyright', 'version': VERSION,
              'status': 'static_errors' if errors else 'no_selected_static_errors',
              'environment': {'python_version': version, 'platform': platform,
                              'scope': 'explicit-source-and-bundled-typeshed', 'installed_packages': False},
              'sources': {name: hashlib.sha256(data).hexdigest() for name, data in before.items()},
              'checked_files': checks, 'counts': counts, 'diagnostics': [],
              'diagnostics_truncated': False, 'semantic_correctness_proven': False,
              'unknown_or_any_may_hide_errors': True, 'model_requests': 0,
              'failure_excerpt': '\n'.join(f"{r['path']}:{r['line']} {r['rule']}: {r['message']}" for r in errors[:4])[:1800]}
    # Preserve valid JSON within the original runner's 20000-byte stdout limit.
    require(len(json.dumps(result, ensure_ascii=True).encode()) <= 16000, 'Source receipt exceeds output budget')
    for row in rows[:40]:
        result['diagnostics'].append(row)
        if len(json.dumps(result, ensure_ascii=True).encode()) > 16000:
            result['diagnostics'].pop()
            break
    result['diagnostics_truncated'] = len(result['diagnostics']) < len(rows)
    return result, 1 if errors else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', action='append', required=True, help='Explicit copied .py/.pyi path, repeatable')
    parser.add_argument('--check', action='append', required=True, help='Selected checked source, repeatable')
    parser.add_argument('--node', required=True, help='Absolute trusted Node executable outside the target')
    parser.add_argument('--python-version', required=True)
    parser.add_argument('--python-platform', required=True, choices=('Linux', 'Windows', 'Darwin'))
    args = parser.parse_args()
    try:
        result, code = evaluate(Path.cwd(), args.source, args.check, args.node, args.python_version, args.python_platform)
    except (OSError, ValueError, KeyError, tokenize.TokenError, SyntaxError, subprocess.SubprocessError) as exc:
        result, code = {'kind': 'python-binding-check', 'status': 'infrastructure_error',
                        'failure_excerpt': str(exc)[:1800], 'model_requests': 0,
                        'semantic_correctness_proven': False}, 2
    print(json.dumps(result, ensure_ascii=True))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
