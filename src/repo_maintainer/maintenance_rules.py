"""Compile bounded investigation evidence into the existing Policy, without execution."""
from __future__ import annotations
from .maintenance_limits import LIMITS

from dataclasses import asdict
import argparse
import json
import os
import re
from pathlib import Path

from .check_discovery import is_test
from .dashboard import unlinked
from .git_investigation import allowed_path, validate_roots
from .github_connection import github_url
from .issue_intake import fingerprint
from .models import Policy, SafetyError, digest
from .source_discovery import CommittedTree
from .target_environment import discover_environment




def require(value, message):
    if not value:
        raise SafetyError(message)


def maintenance_rules(repository, *, read_roots, write_roots, check_ids,
                      mode='approval', allow_auto=False,
                      timeout_seconds=30, max_files=LIMITS["source_context_max_files"], actor='local-operator'):
    """Maintainer configuration only; never infer it from an Issue or repository text."""
    reads, writes = validate_roots(read_roots), validate_roots(write_roots)
    require(reads and writes and all(allowed_path(p, reads) for p in writes if p!='.')
            and ('.' not in writes or '.' in reads), 'Write roots must be within explicit read roots')
    require(mode in {'approval','auto'} and type(allow_auto) is bool and (mode!='auto' or allow_auto),
            'Autonomous execution requires a maintainer rule')
    require(type(max_files) is int and 1<=max_files<=LIMITS["source_context_max_files"], 'Pi file count exceeds configured capacity')
    require(type(timeout_seconds) in (int,float) and 0<timeout_seconds<=LIMITS["test_timeout_max_seconds"], 'Invalid test timeout')
    require(isinstance(check_ids,(list,tuple)) and (1<=len(check_ids)<=5
            or len(check_ids)==6 and sum(isinstance(s,str) and bool(re.fullmatch(r'behavior-[a-f0-9]{64}',s)) for s in check_ids)==1)
            and all(isinstance(s,str) and s and len(s)<=80 for s in check_ids)
            and len(set(check_ids))==len(check_ids), 'Explicit discovered-check identifiers required')
    require(isinstance(actor,str) and 0<len(actor)<=100, 'Maintainer identity required')
    value={'schema_version':1,'repository_id':digest(os.path.normcase(str(repository.common_dir)).encode()),
           'read_roots':list(reads),'write_roots':list(writes),'check_ids':list(check_ids),
           'mode':mode,'allow_auto':allow_auto,
           'timeout_seconds':timeout_seconds,'max_files':max_files,'actor':actor}
    return {**value,'rules_sha256':fingerprint(value)}


def load_rules(path, repository):
    path=unlinked(Path(path))
    require(not path.is_relative_to(repository.root) and not path.is_relative_to(repository.common_dir),
            'Maintainer rules must be outside repository and Git metadata')
    require(path.is_file() and path.stat().st_size<=32768,'Invalid maintainer rules file')
    value=json.loads(path.read_text(encoding='utf-8'))
    validate_rules(value, repository)
    return value


def validate_rules(rules, repository):
    fields=('read_roots','write_roots','check_ids','mode','allow_auto',
            'timeout_seconds','max_files','actor')
    require(isinstance(rules,dict) and set(rules)=={'schema_version','repository_id','rules_sha256',*fields},
            'Unexpected maintainer rule schema')
    expected=maintenance_rules(repository,**{k:rules[k] for k in fields})
    require(expected==rules,'Maintainer rules changed or belong to another repository')


def compile_policy(repository, snapshot, report, environment_plan, rules):
    """No provider, shell, budget or Native calls. Check actual bytes, not claimed pins."""
    validate_rules(rules,repository)
    require(snapshot.get('status')=='ready','Issue is not currently eligible for execution')
    require(snapshot.get('token')==fingerprint({k:v for k,v in snapshot.items()
               if k not in {'token','status','runtime_authorized','latest_readiness_event'}}),
            'Issue snapshot binding changed')
    head=snapshot['head_sha']
    origin=repository.command('config','--get','remote.origin.url').decode().strip()
    require(github_url(origin)[0].casefold()==snapshot['repository'], 'Issue/source origin differs')
    policy,binding=compile_investigation_policy(repository,head,report,environment_plan,rules)
    binding={**binding,'issue_token':snapshot['token'],'repository_id':snapshot['repository_id'],
             'issue_number':snapshot['issue']['number']}
    return policy,{**binding,'binding_sha256':fingerprint(binding)}


def compile_investigation_policy(repository, head, report, environment_plan, rules):
    """Shared byte/scope compiler, without claiming Issue eligibility or execution authority.

    The caller supplies and binds its own trigger (Issue or original monitor event).
    This function alone grants no runtime, command, budget or autonomous permission.
    """
    validate_rules(rules,repository)
    require(repository.head==head and report.get('git',{}).get('head_sha')==head,
            'Investigation/source/Issue target differs')
    require(report.get('schema_version')==1 and report.get('runtime_authorized') is False,
            'Expected a read-only investigation report')
    # Rebuild the plan from the actual committed inputs. A hash supplied by an Issue
    # or model is not a signature and cannot authorize a substituted shell command.
    selection=environment_plan.get('selection')
    require(selection is None or (isinstance(selection,dict) and selection.get('kind')=='task-tests-v1'),
            'Unknown environment task selection')
    fresh=discover_environment(repository,head,rules['read_roots'],
                               investigation=report if selection is not None else None,
                               behavior_contract=environment_plan.get('behavior_contract'))
    require(fresh==environment_plan and fresh['status']=='proposed', 'Environment proposal is stale or blocked')
    checks={c['id']:c for c in fresh['checks']}
    if fresh.get('behavior_contract') is not None:
        from .workflow_behavior import check_id
        require(check_id(fresh['behavior_contract']) in rules['check_ids'],
                'Maintainer rules must explicitly include this behavior contract')
    require(all(i in checks for i in rules['check_ids']), 'Selected checks are outside maintainer rules')
    if selection is not None:
        require(set(checks)==set(rules['check_ids']),
                'Task checks require scope review; selected tests cannot be silently dropped')
    tree=CommittedTree(repository,head,rules['read_roots'])
    candidates=report.get('file_candidates')
    require(isinstance(candidates,list) and 1<=len(candidates)<=12,'No bounded file candidates')
    edits, reads, pins, excluded = [], [], {}, []
    protected={p['path'] for p in fresh['decision_inputs']}

    def verify(pin):
        require(isinstance(pin,dict) and isinstance(pin.get('path'),str), 'Invalid source candidate')
        path=pin['path']
        raw=tree.read(path)
        actual=tree.pin(path)
        require(all(pin.get(k)==actual[k] for k in ('commit_sha','blob','sha256')),
                'Source candidate pin differs from actual committed bytes')
        pins[path]=actual
        return path

    for row in candidates:
        path=verify(row)
        if allowed_path(path,rules['write_roots']) and not is_test(path) and path not in protected:
            require(path not in edits,'Duplicate source candidate')
            edits.append(path)
        else:
            excluded.append(path)
            if path not in reads:
                reads.append(path)
    # Tests/metadata stay visible to Pi but are read-only. Configuration changes
    # require a new environment plan; widening write roots alone cannot evade that.
    for pin in fresh['inputs']:
        path=verify(pin)
        if path not in edits and path not in reads:
            reads.append(path)
    require(edits,'No editable implementation within maintenance rules; request scope review')
    require(len(edits)+len(reads)<=rules['max_files'],'Investigation context exceeds original Pi file limit')
    policy=Policy(tuple(edits),tuple(tuple(checks[i]['argv']) for i in rules['check_ids']),
                  rules['timeout_seconds'],tuple(reads))
    policy.validate_scope()
    require(repository.head==head,'Source changed during policy compilation')
    binding={'schema_version':1,'base_sha':head,'rules_sha256':rules['rules_sha256'],
             'environment_plan_sha256':fresh['plan_sha256'],'policy':asdict(policy),
             'input_bindings':[pins[k] for k in sorted(pins)],'excluded_write_candidates':excluded,
             'mode':rules['mode'],'provider_budget':'original_registry_only',
             'runtime_authorized':False,'requires_execution_grant':True,
             'remote_freshness_verified':False,
             'interpretation':'Compiled scope only. No model call, test, filesystem edit, push or Issue closure.'}
    return policy,binding


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo',type=Path,required=True)
    parser.add_argument('--rules',type=Path,required=True)
    parser.add_argument('--issue-snapshot',type=Path,required=True)
    parser.add_argument('--investigation',type=Path,required=True)
    parser.add_argument('--environment-plan',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args(argv)
    from .git import RepositoryGateway
    repository=RepositoryGateway(args.repo)
    output=unlinked(args.output)
    require(not output.exists() and not output.is_relative_to(repository.root)
            and not output.is_relative_to(repository.common_dir), 'Policy report must be new and outside source/Git')
    def load(path):
        path=unlinked(path)
        require(path.is_file() and path.stat().st_size<=262144,'Policy input exceeds bounded JSON size')
        return json.loads(path.read_text(encoding='utf-8'))
    _,binding=compile_policy(repository,load(args.issue_snapshot),load(args.investigation),
                             load(args.environment_plan),load_rules(args.rules,repository))
    with output.open('x',encoding='utf-8') as stream:
        json.dump(binding,stream,ensure_ascii=False,indent=2)
    print(json.dumps({'output':str(output),'runtime_authorized':False,'model_requests':0}))


if __name__=='__main__':
    main()
