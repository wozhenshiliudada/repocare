"""Fixed controller subprocess: report actual values, never decide assertions.

This file must remain standalone stdlib code: the selected project interpreter
need not have RepoCare installed. Project stdout/stderr are diagnostic data only.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import sys


def _json_value(value, depth=0, budget=None):
    if budget is None:
        budget = [4096]
    budget[0] -= 1
    if budget[0] < 0 or depth > 12:
        return False
    kind = type(value)
    if value is None or kind is bool:
        return True
    if kind is int:
        return abs(value) <= 9007199254740991
    if kind is float:
        return math.isfinite(value)
    if kind is str:
        return '\x00' not in value
    if kind is list:
        return all(_json_value(v, depth + 1, budget) for v in value)
    if kind is dict:
        return all(type(k) is str and '\x00' not in k and _json_value(v, depth + 1, budget) for k, v in value.items())
    return False


def main():
    request_path, result_path = Path(sys.argv[1]), Path(sys.argv[2])
    request_raw = request_path.read_bytes()
    request = json.loads(request_raw)
    # Capture artifact functions before importing project code. Output text never
    # becomes a success receipt, including print('Ran 1 test ... OK'). This is not
    # isolation from deliberately malicious same-user code or filesystem access.
    dumps, write_open, finish = json.dumps, open, os._exit
    flush_stdout, flush_stderr = sys.stdout.flush, sys.stderr.flush
    request_sha = hashlib.sha256(request_raw).hexdigest()
    envelope = {k: request[k] for k in ('schema_version', 'case_id', 'token', 'contract_sha256')}
    envelope['request_sha256'] = request_sha
    root = Path(request['workspace'])
    target = root / request['module']
    sys.dont_write_bytecode = True
    sys.path[:0] = [str(root), str(root / 'src')]
    actual = {'kind': 'missing_module'}
    if target.is_file():
        parts = list(Path(request['module']).with_suffix('').parts)
        if parts and parts[0] == 'src':
            parts = parts[1:]
        name = '.'.join(parts)
        try:
            spec = importlib.util.spec_from_file_location(name, target)
            if spec is None or spec.loader is None:
                raise ImportError('Unsupported Python source')
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            # Execute current source bytes directly. -B prevents cache writes but
            # does not prevent SourceFileLoader from reading an old .pyc whose
            # timestamp/size still matches an equal-size candidate edit.
            exec(compile(target.read_bytes(), str(target), 'exec'), vars(module))
        except BaseException:
            actual = {'kind': 'import_error'}
        else:
            function = vars(module).get(request['callable'])
            if function is None or not callable(function):
                actual = {'kind': 'missing_callable'}
            else:
                try:
                    value = function(*request['args'], **request['kwargs'])
                except Exception as error:
                    kind = type(error)
                    actual = {'kind': 'exception', 'name': kind.__module__ + '.' + kind.__qualname__}
                except BaseException:
                    actual = {'kind': 'execution_error'}
                else:
                    actual = {'kind': 'json', 'value': value} if _json_value(value) else {'kind': 'invalid_result'}
                    if actual['kind'] == 'json':
                        try:
                            if len(dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                                         allow_nan=False).encode('utf-8')) > 32768:
                                actual = {'kind': 'invalid_result'}
                        except BaseException:
                            actual = {'kind': 'invalid_result'}
    envelope['actual'] = actual
    try:
        encoded = dumps(envelope, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode('utf-8')
        if len(encoded) > 65536:
            envelope['actual'] = {'kind': 'invalid_result'}
            encoded = dumps(envelope, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode('utf-8')
        with write_open(result_path, 'xb') as stream:
            stream.write(encoded)
            stream.flush()
    except BaseException:
        finish(4)
    for flush in (flush_stdout, flush_stderr):
        try:
            flush()
        except BaseException:
            pass
    finish(0)


if __name__ == '__main__':
    main()
