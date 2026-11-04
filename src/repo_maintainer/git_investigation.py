"""Bounded, committed-version Git evidence. No checkout, fetch, edits or approval."""
from __future__ import annotations
from .maintenance_limits import LIMITS

import os
import re
import subprocess
import threading
import time

from .git import git_env
from .models import SafetyError, digest, safe_context_path
from .runner import stop_tree
from .store import redact

SHA = re.compile(r'[0-9a-f]{40}\Z')




def _capture(argv, cwd, env, *, max_bytes=65536, timeout=10, on_start=None, timeout_limit=30, on_process=None,
             own_windows_job=False):
    """Bound both pipes while reading, rather than slice an unlimited capture later."""
    if type(max_bytes) is not int or not 256 <= max_bytes <= LIMITS["catalog_max_path_bytes"] * 2:
        raise SafetyError('Invalid investigation byte limit')
    if (type(timeout_limit) is not int or not 1 <= timeout_limit <= max(LIMITS["test_timeout_max_seconds"], LIMITS["discovery_seconds"]) or
            not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or not 0 < timeout <= timeout_limit):
        raise SafetyError('Invalid investigation timeout')
    options = dict(cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if own_windows_job and os.name == 'nt':
        from .windows_job import windows_job_process
        with windows_job_process(argv, **options) as (process, stop):
            return _capture_started(process, max_bytes, timeout, on_start, on_process, stop, close_on_exit=True)
    process = subprocess.Popen(argv, **options,
                               start_new_session=os.name != 'nt',
                               creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0)
    return _capture_started(process, max_bytes, timeout, on_start, on_process, lambda: stop_tree(process))


def _capture_started(process, max_bytes, timeout, on_start, on_process, stop, *, close_on_exit=False):
    if on_start is not None or on_process is not None:
        try:
            if on_process is not None:
                on_process(process)
            if on_start is not None:
                on_start(process.pid)
        except BaseException:
            stop()
            process.wait(timeout=5)
            process.stdout.close()
            process.stderr.close()
            raise
    exceeded = threading.Event()
    buffers, seen = [bytearray(), bytearray()], [0, 0]

    def drain(index, stream):
        with stream:
            while chunk := stream.read1(8192):
                seen[index] += len(chunk)
                remaining = max_bytes - len(buffers[index])
                buffers[index].extend(chunk[:remaining])
                if len(chunk) > remaining:
                    exceeded.set()

    threads = [threading.Thread(target=drain, args=(i, stream), daemon=True)
               for i, stream in enumerate((process.stdout, process.stderr))]
    for thread in threads:
        thread.start()
    deadline, timed_out = time.monotonic() + timeout, False
    try:
        while process.poll() is None:
            if exceeded.is_set():
                stop()
                break
            if time.monotonic() >= deadline:
                timed_out = True
                stop()
                break
            exceeded.wait(min(0.02, max(0, deadline - time.monotonic())))
        if close_on_exit:
            stop()  # Root exit alone does not close descendants' inherited pipe handles.
        process.wait(timeout=5)
    except BaseException:
        stop()
        raise
    finally:
        deadline = time.monotonic() + 5
        for thread in threads:
            thread.join(timeout=max(0, deadline - time.monotonic()) if close_on_exit else 5)
    if any(thread.is_alive() for thread in threads):
        raise SafetyError('Investigation output did not terminate')
    raw = bytes(buffers[0])
    return {'stdout': raw, 'stderr': bytes(buffers[1]), 'returncode': process.returncode,
            'timed_out': timed_out, 'truncated': exceeded.is_set(),
            'receipt': {'captured_prefix_sha256': digest(raw), 'captured_bytes': len(raw),
                        'observed_stdout_bytes': seen[0], 'observed_stderr_bytes': seen[1],
                        'entire_output_preserved': not exceeded.is_set() and not timed_out}}


def _read_git(repository, *args, max_bytes=65536, timeout=10):
    # Arguments are constructed below, never a shell command supplied by a model/log.
    argv = ['git', '--no-pager', '--literal-pathspecs', '-c', 'core.hooksPath=' + os.devnull,
            '-c', 'core.fsmonitor=false', '-c', 'core.quotepath=false', '-c',
            'i18n.logOutputEncoding=utf-8', '-C', str(repository.root), *args]
    env = {key: value for key, value in git_env().items() if key.upper() in {
        'PATH','SYSTEMROOT','WINDIR','TEMP','TMP','COMSPEC','PATHEXT','LANG','LC_ALL','LC_CTYPE',
        'GIT_TERMINAL_PROMPT','GIT_CONFIG_NOSYSTEM','GIT_CONFIG_GLOBAL','GIT_OPTIONAL_LOCKS'}}
    env.update(GIT_NO_LAZY_FETCH='1', GIT_NO_REPLACE_OBJECTS='1', LC_ALL='C')
    result = _capture(argv, repository.root, env, max_bytes=max_bytes, timeout=timeout)
    if repository.audit is not None:
        repository.audit.append({'command': argv, 'returncode': result['returncode'],
                                 'timed_out': result['timed_out'], 'truncated': result['truncated'],
                                 'output_receipt': result['receipt']})
    return result


def required(result):
    if result['timed_out'] or result['truncated'] or result['returncode']:
        raise SafetyError('Git evidence incomplete or unavailable; no complete comparison produced')
    return result['stdout']


def commit_sha(repository, value):
    if not isinstance(value, str) or not SHA.fullmatch(value):
        raise SafetyError('Investigation requires a complete lowercase commit SHA')
    resolved = required(_read_git(repository, 'rev-parse', '--verify', value + '^{commit}', max_bytes=1024)).decode().strip()
    if resolved != value:
        raise SafetyError('Commit identity differs')
    return value


def allowed_path(path, roots):
    """Pure lexical scope; no untrusted pathname is opened in the host filesystem."""
    try:
        safe_context_path(path)
        for part in path.split('/'):
            safe_context_path(part)
    except SafetyError:
        return False
    return any(root == '.' or path == root or path.startswith(root + '/') for root in roots)


def validate_roots(roots):
    if not isinstance(roots, (list, tuple)) or len(roots) > 20:
        raise SafetyError('At most 20 explicit read roots are supported')
    for root in roots:
        if not isinstance(root, str):
            raise SafetyError('Read roots must be strings')
        if root != '.':
            safe_context_path(root)
    if len({r.casefold() for r in roots}) != len(roots):
        raise SafetyError('Duplicate read roots')
    return tuple(roots)


def history(repository, head, limit=5):
    if type(limit) is not int or not 1 <= limit <= 20:
        raise SafetyError('History limit must be 1..20')
    result = _read_git(repository, 'log', '--no-notes', '--format=%H%x00%P%x00%ct%x00%s',
                      '-z', '-n', str(limit + 1), head, '--', max_bytes=32768)
    parts = result['stdout'].split(b'\0')
    rows = []
    for i in range(0, len(parts) - 3, 4):
        identity, parents, timestamp, subject = parts[i:i+4]
        try:
            identity = identity.decode('ascii')
            parent_ids = parents.decode('ascii').split()
            stamp = int(timestamp)
        except (UnicodeError, ValueError):
            raise SafetyError('Malformed Git history evidence') from None
        if not SHA.fullmatch(identity) or any(not SHA.fullmatch(p) for p in parent_ids):
            raise SafetyError('Malformed Git history identity')
        subject_text = redact(subject.decode('utf-8', errors='replace'))
        rows.append({'sha': identity, 'parents': parent_ids, 'commit_time': stamp,
                     'subject': subject_text[:500], 'subject_truncated':len(subject_text)>500,
                     'subject_original_sha256':digest(subject)})
    return {'commits': rows[:limit], 'truncated': result['truncated'] or len(rows) > limit,
            'timed_out': result['timed_out'], 'returncode': result['returncode'],
            'receipt': result['receipt']}


def changed_files(repository, base, head, roots, limit=50):
    if type(limit) is not int or not 1 <= limit <= 100:
        raise SafetyError('Changed-file limit must be 1..100')
    if not roots:
        return {'files': [], 'truncated': False, 'omitted_or_blocked': 0, 'scope_empty': True}
    result = _read_git(repository, 'diff', '--raw', '-z', '--no-abbrev', '--no-ext-diff',
                      '--no-textconv', '--find-renames=50%', '-l200', base, head, '--')
    if result['timed_out'] or (result['returncode'] and not result['truncated']):
        raise SafetyError('Changed-file evidence unavailable')
    parts, rows, blocked, i = result['stdout'].split(b'\0'), [], 0, 0
    while i < len(parts) and parts[i]:
        if i == len(parts) - 1 and result['truncated']:
            break
        header = parts[i].split()
        if len(header) != 5 or not header[0].startswith(b':'):
            raise SafetyError('Malformed changed-file record')
        rename = header[4][:1] in {b'R', b'C'}
        needed = 3 if rename else 2
        if i + needed >= len(parts):
            if not result['truncated']:
                raise SafetyError('Incomplete changed-file record')
            break  # Partial records are omitted, never treated as complete filenames.
        try:
            old = parts[i+1].decode('utf-8')
            new = parts[i+2].decode('utf-8') if rename else old
            old_mode, new_mode = header[0][1:].decode(), header[1].decode()
            old_blob, new_blob = header[2].decode(), header[3].decode()
        except UnicodeError:
            blocked += 1
            i += needed
            continue
        i += needed
        if (not all(allowed_path(p, roots) for p in (old, new))
                or old_mode not in {'000000','100644','100755'}
                or new_mode not in {'000000','100644','100755'}):
            blocked += 1
            continue
        if not SHA.fullmatch(old_blob) or not SHA.fullmatch(new_blob):
            raise SafetyError('Malformed file blob identity')
        rows.append({'status': header[4].decode('ascii'), 'base_path': old, 'head_path': new,
                     'base_blob': old_blob, 'head_blob': new_blob,
                     'base_mode': old_mode, 'head_mode': new_mode})
    return {'files': rows[:limit], 'truncated': result['truncated'] or len(rows) > limit,
            'omitted_or_blocked': blocked, 'scope_empty': False, 'receipt': result['receipt'],
            'counts_cover':'parsed_metadata_prefix_only',
            'rename_detection_may_be_incomplete': bool(result['stderr'])}


def diff_excerpt(repository, base, head, paths, *, max_bytes=16384, max_lines=160):
    if type(max_lines) is not int or not 1 <= max_lines <= 500:
        raise SafetyError('Diff line limit must be 1..500')
    result = _read_git(repository, 'diff', '--no-ext-diff', '--no-textconv', '--no-color',
                      '--no-renames', '--unified=3', base, head, '--', *paths, max_bytes=max_bytes)
    if result['timed_out'] or (result['returncode'] and not result['truncated']):
        raise SafetyError('Diff evidence unavailable')
    raw = result['stdout']
    lines = raw.decode('utf-8', errors='replace').splitlines()
    excerpt = redact('\n'.join(lines[:max_lines]))
    hunks = []
    for line in lines[:max_lines]:
        match = re.match(r'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@', line)
        if match:
            hunks.append({'base_start':int(match[1]), 'base_count':int(match[2] or 1),
                          'head_start':int(match[3]), 'head_count':int(match[4] or 1)})
    return {'text': excerpt, 'text_sha256': digest(excerpt.encode('utf-8')), 'hunks':hunks,
            'truncated': result['truncated'] or len(lines) > max_lines,
            'raw_receipt':result['receipt'], 'binary_notice':any(l.startswith('Binary files ') for l in lines)}


def investigate_git(repository, *, head_sha, read_roots, base_sha=None, mode='commit',
                    history_limit=5, file_limit=30, diff_limit=5, diff_bytes=16384):
    if mode not in {'commit','range','branch'} or type(diff_limit) is not int or not 0 <= diff_limit <= 10:
        raise SafetyError('Unsupported investigation mode or diff limit')
    roots = validate_roots(read_roots)
    observed_head = repository.head
    head = commit_sha(repository, head_sha)
    parents = required(_read_git(repository, 'show', '-s', '--format=%P', head, max_bytes=4096)).decode().split()
    if any(not SHA.fullmatch(p) for p in parents):
        raise SafetyError('Malformed parent identities')
    base, status, reference, relation = None, 'compared', None, 'unknown'
    if mode == 'commit':
        if base_sha is not None:
            base = commit_sha(repository, base_sha)
            if base not in parents:
                raise SafetyError('Commit review base must be an explicit parent; use range for other comparisons')
        elif len(parents) == 1:
            base = parents[0]
        else:
            status = 'root_commit_no_parent' if not parents else 'merge_commit_requires_explicit_parent'
        if base:
            relation = 'explicit_parent'
    else:
        reference = commit_sha(repository, base_sha)
        base = reference
        if mode == 'branch':
            bases = required(_read_git(repository, 'merge-base', '--all', reference, head, max_bytes=4096)).decode().split()
            if len(bases) != 1 or not SHA.fullmatch(bases[0]):
                raise SafetyError('Branch comparison requires one unambiguous merge base')
            base = bases[0]
            relation = 'merge_base'
        else:
            ancestry = _read_git(repository, 'merge-base', '--is-ancestor', base, head, max_bytes=1024)
            if ancestry['timed_out'] or ancestry['truncated'] or ancestry['returncode'] not in {0,1}:
                raise SafetyError('Ancestry evidence unavailable')
            relation = 'ancestor' if ancestry['returncode'] == 0 else 'not_ancestor'
    change = changed_files(repository, base, head, roots, file_limit) if base else None
    diffs = []
    if change:
        for row in change['files'][:diff_limit]:
            paths = list(dict.fromkeys([row['base_path'], row['head_path']]))
            diffs.append({'base_path':row['base_path'], 'head_path':row['head_path'],
                          'base_blob':row['base_blob'], 'head_blob':row['head_blob'],
                          **diff_excerpt(repository, base, head, paths, max_bytes=diff_bytes)})
    result = {'schema_version':1, 'runtime_authorized':False,
              'repository_id':digest(os.path.normcase(str(repository.common_dir)).encode()),
              'observed_checkout_head':observed_head,
              'mode':mode, 'comparison_status':status, 'base_sha':base, 'head_sha':head,
              'reference_sha':reference, 'head_parents':parents, 'history':history(repository, head, history_limit),
              'base_relation':relation,
              'changes':change, 'diffs':diffs,
              'coverage':{'read_roots':list(roots), 'diffs_omitted_from_selected_files':max(0,len(change['files'])-len(diffs)) if change else 0,
                          'complete_repository_review':False},
              'last_comparable_success':{'status':'unknown','reason':'No verified matching check/environment receipts supplied'},
              'causality':'Version differences are evidence, not proof of the introducing commit.'}
    if repository.head != observed_head:
        raise SafetyError('Repository HEAD changed during investigation; retry only with a new explicit snapshot')
    return result
