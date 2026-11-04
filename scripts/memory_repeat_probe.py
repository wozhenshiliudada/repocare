"""Trusted container entry point. Imports the uploaded candidate only in Docker."""
import hashlib
import io
import json
from pathlib import Path
import sys
import traceback
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from memory_repeat_cases import CASES, KIND, PRESERVATION, feature_tests

def probe(case):
    if case not in CASES:
        raise ValueError('Unknown fixed case')
    sys.path.insert(0, '/candidate')
    namespace = {'CASE': case}
    results = {}
    import_error = None
    try:
        exec(compile(feature_tests(case) + '\n' + PRESERVATION, '<fixed-evaluation-checks>', 'exec'), namespace)
    except Exception:
        import_error = traceback.format_exc()[-12000:]
    for name, test_class in (('FEATURE', 'FeatureChecks'), ('PRESERVATION', 'PreservationChecks')):
        if import_error is not None:
            # Import failures are code failures, not successful avoidance or Docker failures.
            import ast
            source = feature_tests(case) if name == 'FEATURE' else PRESERVATION
            expected = sorted(node.name for node in ast.walk(ast.parse(source))
                              if isinstance(node, ast.FunctionDef) and node.name.startswith('test_'))
            results[name] = {'success': [], 'failure': expected, 'errors': expected, 'output': import_error}
            continue
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(namespace[test_class])
        stream = io.StringIO()
        result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
        failures = [test.id().split('.')[-1] for test, _ in result.failures + result.errors]
        expected = unittest.defaultTestLoader.getTestCaseNames(namespace[test_class])
        if result.skipped or result.testsRun != len(expected):
            raise ValueError('Skipped or missing fixed tests')
        results[name] = {'success': [test for test in expected if test not in failures], 'failure': failures,
                         'errors': [test.id().split('.')[-1] for test, _ in result.errors],
                         'output': stream.getvalue()[-12000:]}
    value = {'kind': KIND, 'case': case,
             'source_sha256': hashlib.sha256(Path('/candidate/printer.py').read_bytes()).hexdigest(),
             'tests_status': results,
             'repeat_error': ('test_unique_declarations' in results['PRESERVATION']['failure']
                              and 'test_unique_declarations' not in results['PRESERVATION']['errors']),
             'model_requests': 0, 'official_score': False}
    value['passed'] = all(not row['failure'] for row in results.values())
    print(json.dumps(value))
    return 0 if value['passed'] else 1

if __name__ == '__main__':
    raise SystemExit(probe(sys.argv[1]))
