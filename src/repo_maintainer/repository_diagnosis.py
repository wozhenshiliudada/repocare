"""Read-only W1 report assembly; evidence is neither reproduction nor permission."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re

from .git import RepositoryGateway
from .git_investigation import SHA, allowed_path, investigate_git, validate_roots
from .models import SafetyError, digest, safe_context_path
from .pi_revision import failure_summary
from .store import redact

MAX_REPORT = 262144
MAX_STREAM = 32768


def relative_location(raw, repository_root, read_roots):
    """Lexical hint only; Git/file existence must be verified by the later search layer."""
    value = raw.replace('\\', '/')
    root = str(repository_root).replace('\\', '/').rstrip('/')
    if value.casefold().startswith(root.casefold() + '/'):
        value = value[len(root)+1:]
    if not allowed_path(value, read_roots):
        return None
    return value


def diagnostic_lines(text):
    for number, line in enumerate(text.splitlines(), 1):
        if len(line.encode('utf-8')) < MAX_STREAM:
            try:
                item = json.loads(line)
            except (ValueError, RecursionError):
                item = None
            if isinstance(item, dict) and isinstance(item.get('failure_excerpt'), str):
                for nested, content in enumerate(item['failure_excerpt'].splitlines(), 1):
                    yield number, {'json_field':'failure_excerpt','nested_line':nested}, content
                continue
        yield number, None, line


def failure_evidence(checks, *, target_sha, repository_root, read_roots):
    if not isinstance(target_sha, str) or not SHA.fullmatch(target_sha) or not isinstance(checks, list) or len(checks) > 10:
        raise SafetyError('Invalid bounded failure report')
    roots = validate_roots(read_roots)
    prepared, streams, observations, outcomes = [], [], [], []
    raw_size = 0
    for index, check in enumerate(checks, 1):
        if not isinstance(check, dict):
            raise SafetyError('Invalid check record')
        command = check.get('command')
        if (not isinstance(command, list) or not 1 <= len(command) <= 64
                or any(not isinstance(arg, str) or not arg or len(arg) > 4096 or '\x00' in arg for arg in command)
                or type(check.get('returncode')) is not int
                or type(check.get('timed_out',False)) is not bool
                or type(check.get('output_truncated',False)) is not bool):
            raise SafetyError('Malformed check outcome')
        projected = {'command':[redact(arg) for arg in command], 'returncode':check['returncode'],
                     'timed_out':check.get('timed_out',False), 'output_truncated':check.get('output_truncated',False)}
        zero, import_error, test_signal = False, False, False
        for stream in ('stdout','stderr'):
            text = check.get(stream,'')
            if not isinstance(text,str):
                raise SafetyError('Check output must be text')
            raw = text.encode('utf-8')
            raw_size += len(raw)
            if raw_size > MAX_REPORT:
                raise SafetyError('Failure report exceeds bounded input size')
            prefix = raw[:MAX_STREAM].decode('utf-8',errors='replace')
            projected[stream] = redact(prefix)
            source_id = f'check-{index}-{stream}'
            streams.append({'id':source_id,'fragment_sha256':digest(raw),'fragment_bytes':len(raw),
                            'parsed_prefix_bytes':min(MAX_STREAM,len(raw)),
                            'prefix_truncated':len(raw)>MAX_STREAM,
                            'upstream_truncated':check.get('output_truncated',False),
                            'line_numbers_relative_to':'supplied_fragment',
                            'entire_original_log_preserved':False})
            for line_no, projection, line in diagnostic_lines(prefix):
                stripped = line.strip()
                zero |= bool(re.search(r'Ran 0 tests|no tests ran|collected 0 items|^# tests 0\b',stripped))
                import_error |= bool(re.search(r'\b(ModuleNotFoundError|ImportError):',stripped))
                kind, values = None, {}
                location = re.search(r'File "([^"]+)", line (\d+)',line)
                if location is None:
                    location = re.search(r'((?:[A-Za-z]:)?[^\s()"<>]+\.(?:py|js|mjs|ts|tsx)):(\d+)(?::\d+)?',line)
                if location:
                    kind = 'location'
                    path = relative_location(location[1],repository_root,roots)
                    values = {'path':path,'line':int(location[2]),'side':'head',
                              'source_path_verified':False,
                              'scope_status':'eligible_hint' if path else 'outside_scope_or_unmapped'}
                elif re.search(r'\b(?:\w+\.)*\w*(?:Error|Exception):',stripped):
                    kind = 'exception'
                elif re.match(r'^(?:FAILED |FAIL: |ERROR: |not ok \d+)',stripped):
                    kind = 'test_identifier'
                    test_signal = True
                elif re.search(r'\bassert\b|\bAssertionError\b|DID NOT RAISE',stripped):
                    kind = 'assertion'
                    test_signal = True
                if kind and len(observations) < 60:
                    observations.append({'kind':kind,'source_id':source_id,'saved_fragment_line':line_no,
                                         'projection':projection,'excerpt':redact(stripped)[:300],**values})
        prepared.append(projected)
        if projected['timed_out']:
            outcome = 'reported_timeout'
        elif zero:
            outcome = 'no_tests_signal'
        elif import_error:
            outcome = 'possible_environment_or_import_error'
        elif projected['returncode'] != 0:
            outcome = 'reported_test_failure' if test_signal else 'reported_failure_unclassified'
        else:
            outcome = 'reported_exit_zero_not_proof_of_coverage'
        outcomes.append({'check_index':index,'returncode':projected['returncode'],'category':outcome})
    summary = redact(failure_summary(prepared))
    return {'schema_version':1,'target_sha':target_sha,'runtime_authorized':False,
            'checks_executed_by_this_module':False,'outcomes':outcomes,'sources':streams,
            'observations':observations,'summary':summary,'summary_sha256':digest(summary.encode()),
            'observation_limit_reached':len(observations)>=60,
            'interpretation':'Untrusted reported output; hints are not root-cause, reproduction, test coverage or authority.'}


def read_failure_report(path, target_sha):
    path = Path(path)
    safe_context_path(path.name)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_REPORT:
        raise SafetyError('Expected a bounded regular failure-report file')
    raw = path.read_bytes()
    if len(raw) > MAX_REPORT:
        raise SafetyError('Failure report grew beyond its limit')
    try:
        data = json.loads(raw)
    except (ValueError, RecursionError):
        raise SafetyError('Invalid failure report JSON') from None
    if not isinstance(data,dict) or set(data) != {'target_sha','checks'} or data['target_sha'] != target_sha:
        raise SafetyError('Failure report does not bind the selected target SHA')
    return data['checks'], {'sha256':digest(raw),'bytes':len(raw),'binding':'declared_target_not_independently_reproduced'}


def build_report(repository, *, head_sha, read_roots, base_sha=None, mode='commit', checks=None,
                 search=False, query=''):
    observed_head = repository.head
    git = investigate_git(repository, head_sha=head_sha,read_roots=read_roots,base_sha=base_sha,mode=mode)
    failures = failure_evidence(checks if checks is not None else [],target_sha=head_sha,repository_root=repository.root,read_roots=read_roots)
    discovery = None
    if search:
        from .source_discovery import discover_sources
        discovery = discover_sources(repository, git, failures, read_roots, query=query)
    if repository.head != observed_head:
        raise SafetyError('Repository HEAD changed before diagnosis report completion')
    return {'schema_version':1,'runtime_authorized':False,'git':git,'failures':failures,
            'next_action':'review_discovery' if discovery else 'investigate',
            'file_candidates':discovery['file_candidates'] if discovery else [],
            'test_candidates':discovery['test_candidates'] if discovery else [],
            'discovery':discovery,
            'coverage':{'search_performed':search,'semantic_review_performed':False,
                        'reproduction_performed':False},
            'interpretation':'Read-only evidence. Policy compilation, semantic review and repair are separate steps.'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo',required=True)
    parser.add_argument('--head',required=True,help='Full commit SHA, not a mutable ref')
    parser.add_argument('--base')
    parser.add_argument('--mode',choices=['commit','range','branch'],default='commit')
    parser.add_argument('--read-root',action='append',default=[])
    parser.add_argument('--failure-report')
    parser.add_argument('--discover', action='store_true', help='Search the bounded committed tree and recommend existing checks')
    parser.add_argument('--query', default='', help='Optional plain-text literal search hints, not a command')
    parser.add_argument('--output',required=True,help='New JSON outside the source repository')
    args = parser.parse_args(argv)
    repo = RepositoryGateway(Path(args.repo))
    output = Path(args.output).resolve()
    if output.exists() or output.is_relative_to(repo.root) or output.is_relative_to(repo.common_dir):
        raise SafetyError('Report must be a new file outside source and Git metadata')
    checks, receipt = read_failure_report(args.failure_report,args.head) if args.failure_report else ([],None)
    report = build_report(repo,head_sha=args.head,base_sha=args.base,mode=args.mode,
                          read_roots=args.read_root,checks=checks,search=args.discover,query=args.query)
    report['failure_file_receipt'] = receipt
    with output.open('x',encoding='utf-8',newline='\n') as stream:
        json.dump(report,stream,ensure_ascii=False,indent=2)
        stream.write('\n')
    print(json.dumps({'report':str(output),'runtime_authorized':False,'model_calls':0}))


if __name__ == '__main__':
    main()
