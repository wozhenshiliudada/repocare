"""Bounded read-only delivery proof. Never repairs, merges, runs CI or closes."""
from __future__ import annotations

from datetime import datetime
import re
import time
from urllib.parse import quote

from .dashboard import read_json
from .integration import fingerprint as integration_fingerprint
from .issue_intake import IssueReader, project_issue, fingerprint, STAMP
from .models import SafetyError
from .recovery import process_identity

STAGES = {'ready-for-agent', 'in-progress', 'awaiting-review'}
REJECT = {'blocked', 'needs-triage', 'duplicate', 'invalid', 'wontfix', 'cancelled', 'canceled'}


def require(condition, message):
    if not condition:
        raise SafetyError(message)


def positive(value):
    return type(value) is int and 1 <= value < 2**63


def stamp(value):
    require(isinstance(value, str) and STAMP.fullmatch(value), 'Invalid remote evidence timestamp')
    datetime.fromisoformat(value.replace('Z', '+00:00'))
    return value


def acceptance_policy(workflow_ids, events=('push',)):
    require(isinstance(workflow_ids, (tuple, list)) and 1 <= len(workflow_ids) <= 16
            and all(positive(v) for v in workflow_ids) and len(set(workflow_ids)) == len(workflow_ids),
            'Explicit trusted Actions workflow IDs required')
    require(isinstance(events, (tuple, list)) and events and set(events) <= {'push', 'workflow_dispatch'},
            'Explicit accepted workflow events required')
    core = {'schema_version': 1, 'workflow_ids': sorted(workflow_ids), 'events': sorted(set(events)),
            'default_branch_only': True, 'all_jobs_successful': True}
    return {**core, 'policy_sha256': fingerprint(core)}


class ReadWindow:
    def __init__(self, remote):
        self.remote, self.calls, self.deadline = remote, 0, time.monotonic()+120
        self.events = {}

    def api(self, path):
        require(self.calls < 96 and time.monotonic() < self.deadline, 'Acceptance read window exhausted; no write')
        self.calls += 1
        value = self.remote.read(path)
        require(time.monotonic() < self.deadline, 'Acceptance read timed out; no write')
        if '/events?' in path:
            self.events[path] = value
        return value

    def pages(self, prefix, field=None):
        rows, total = [], None
        for page in range(1, 4):
            value = self.api(prefix+str(page))
            if field:
                require(isinstance(value, dict) and type(value.get('total_count')) is int, 'Malformed evidence pagination')
                require(total is None or total == value['total_count'], 'Remote page count changed')
                total = value['total_count']
                require(0 <= total <= 90, 'Evidence pagination exceeds completeness bound')
                value = value.get(field)
            require(isinstance(value, list) and len(value) <= 30 and all(isinstance(r, dict) for r in value),
                    'Malformed remote evidence page')
            rows += value
            if len(value) < 30 or (field and len(rows) == total):
                require(not field or len(rows) == total, 'Incomplete evidence pagination')
                return rows
        raise SafetyError('Incomplete remote evidence pagination; no write')


def stopped(row):
    owner, child = row.get('owner_process'), row.get('child_process')
    require(not owner or process_identity(owner['pid']) != owner, 'Original remote writer is still alive')
    require(not child or process_identity(child['pid']) != child, 'Original Git child is still alive')
    require(not row.get('stage', '').endswith('_starting') and row.get('stage') != 'push_running',
            'Original transport needs manual process verification')


def history(delivery, identity):
    registry, execution = delivery.registry, delivery.execution
    run = execution.view(identity)
    require(run['status'] in {'awaiting-review', 'delivered'}, 'Original execution has no successful candidate')
    draft = delivery.view(identity)
    core, binding = draft['core'], draft['core']['binding']
    require(draft['status'] in {'awaiting-review', 'remote-created-review-required', 'remote-closed-review',
                                'creation_unknown', 'create_intent'}, 'Original PR write intent required')
    require(draft['approval_sha256'] == fingerprint(core) == draft.get('authorization', {}).get('approval_sha256'),
            'Original PR payload has no matching publication authorization')
    stopped(draft)
    with registry.transaction() as db:
        head = execution._load('issue_execution_head', run['issue_key'], db)
        job = execution._load('issue_diagnosis', run['investigation_id'], db)
        publication = execution._load('issue_publication', identity, db)
    require(head and head['execution_id'] == identity and job, 'Original Issue claim changed')
    original = job['snapshot']
    require(fingerprint(execution.spec) == run['spec_sha256'] == original['spec_sha256'], 'Original repository/trust scope changed')
    require(binding['repository'] == execution.spec['repository'] and binding['repository_id'] == run['repository_id']
            and binding['repository_id'] == original['repository_id']
            and binding['issue_number'] == run['issue_number'] == original['issue']['number']
            and binding['issue_token'] == run['issue_token'] == original['token']
            and binding['execution_id'] == identity and binding['run_id'] == run['run_id']
            and binding['task_id'] == run['task_id'] and binding['rules_sha256'] == run['rules_sha256']
            and binding['policy_binding_sha256'] == run['policy_binding']['binding_sha256'], 'Original delivery association differs')
    directory = execution.runs/run['run_id']
    record = read_json(directory/'run.json')[0]
    review = read_json(directory/'integration-review.json')[0]
    expected = {key: binding[key] for key in ('execution_id', 'issue_number', 'issue_token', 'task_id',
                                             'rules_sha256', 'policy_binding_sha256')}
    require(record.get('issue_execution') == expected and record.get('integration_commit') == binding['commit']
            and record.get('base_sha') == binding['base_sha'] == original['head_sha']
            and record.get('integration_branch') == binding['head']
            and record.get('integration_review_sha256') == binding['review_sha256']
            and review.get('status') == 'published' and integration_fingerprint(review['review']) == binding['review_sha256']
            and review.get('authorization', {}).get('decision') == 'approve'
            and review['authorization'].get('review_sha256') == binding['review_sha256']
            and review['review'].get('run_id') == binding['run_id']
            and review['review'].get('commit') == binding['commit']
            and review['review'].get('base_sha') == binding['base_sha']
            and review['review'].get('target_branch') == binding['head']
            and review['review'].get('diff_sha256') == binding['diff_sha256'], 'Historical sealed publication differs')
    if publication:
        stopped(publication)
        require(publication.get('authorization', {}).get('approval_sha256') == fingerprint(publication['core'])
                and publication['core']['binding'] == binding, 'Original branch publication intent differs')
    receipt = draft.get('receipt')
    if receipt:
        require(receipt['head_sha'] == binding['commit'] and receipt['payload_sha256'] == fingerprint(core['payload']),
                'Historical PR receipt differs')
    result = {'binding': binding, 'original': original, 'payload': core['payload'], 'receipt': receipt,
              'pr_authorization_sha256': draft['approval_sha256']}
    return result


def issue_snapshot(reader, spec, original, registry=None):
    reader.events.clear()
    value = IssueReader(reader, spec, registry=registry).snapshot(original['issue']['number'])
    require(value.get('events_complete') is True and value.get('repository_id') == original['repository_id'],
            'Incomplete or different repository/Issue evidence')
    issue = value['issue']
    require(issue.get('id') == original['issue']['id'] and issue.get('raw_text_sha256') == original['issue']['raw_text_sha256'],
            'Issue was edited; original repair does not authorize its new meaning')
    labels = set(issue['labels'])
    require(not labels & REJECT and len(labels & STAGES) == 1, 'Issue is cancelled/blocked or has ambiguous task stage')
    previous, current = original['readiness_events'], value['readiness_events']
    progression = (len(current) == len(previous)+1 and current[:-1] == previous
                   and current[-1]['event'] == 'unlabeled'
                   and current[-1]['actor'] in spec['trusted_label_actors']
                   and bool(labels & {'in-progress', 'awaiting-review'}) and 'ready-for-agent' not in labels)
    require((current == previous and 'ready-for-agent' in labels) or progression,
            'Issue readiness was revoked/reissued outside its original task')
    raw = reader.api('/repos/'+spec['repository']+'/issues/'+str(issue['number']))
    projected = project_issue(raw, spec['repository'], issue['number'])
    if registry is not None:
        from .issue_comment_continuity import guard, normalize
        with guard(registry):
            projected = normalize({**value, 'issue':projected}, registry)['issue']
    require(projected == issue, 'Issue changed during acceptance read')
    events = []
    for path, rows in sorted(reader.events.items()):
        if '/issues/'+str(issue['number'])+'/events?' in path:
            for row in rows:
                require(positive(row.get('id')) and isinstance(row.get('event'), str), 'Malformed Issue lifecycle event')
                at = stamp(row.get('created_at'))
                if at >= original['issue']['updated_at']:
                    require(row['event'] != 'reopened', 'Issue was reopened; do not close using its old task')
                    require(issue['state'] != 'open' or row['event'] != 'closed', 'Issue was manually closed/reopened')
                events.append({'id': row['id'], 'event': row['event'], 'created_at': at})
    actor = raw.get('closed_by')
    if actor is not None:
        require(isinstance(actor, dict) and positive(actor.get('id')) and isinstance(actor.get('login'), str), 'Malformed closing actor')
        actor = {'id': actor['id'], 'login': actor['login'].casefold()}
    closed_at = raw.get('closed_at')
    if closed_at is not None:
        stamp(closed_at)
    return {'issue': issue, 'head_sha': value['head_sha'], 'events': sorted(events, key=lambda r: r['id']),
            'readiness': current, 'closed_by': actor, 'closed_at': closed_at, 'state_reason': raw.get('state_reason')}


def merged_pull(reader, association):
    b, receipt = association['binding'], association['receipt']
    base = '/repos/'+b['repository']
    if receipt:
        number = receipt['number']
    else:
        rows = reader.pages(base+'/pulls?state=all&head='+quote(b['repository'].split('/')[0]+':'+b['head'], safe='')+'&per_page=30&page=')
        require(len(rows) == 1 and positive(rows[0].get('number')), 'No unique PR for original unknown intent')
        number = rows[0]['number']
    value = reader.api(base+'/pulls/'+str(number))
    require(isinstance(value, dict) and value.get('number') == number and positive(value.get('id'))
            and str(value.get('html_url', '')).casefold() == f"https://github.com/{b['repository']}/pull/{number}"
            and value.get('merged') is True and value.get('state') == 'closed' and value.get('draft') is False,
            'PR is not a verified merged delivery')
    if receipt:
        require(value['id'] == receipt['id'], 'PR identity changed')
    else:
        prefix=association.get('marker_prefix','repocare-delivery:')
        require(prefix in {'repocare-delivery:','repocare-monitor-delivery:'}, 'Unknown delivery marker origin')
        opening='<!-- '+prefix
        require(opening in association['payload']['body'], 'Original intent marker missing')
        marker = association['payload']['body'].split(opening, 1)[1].split(' -->', 1)[0]
        require(opening+marker+' -->' in str(value.get('body', '')), 'Unknown PR intent marker differs')
    for side, ref in (('head', b['head']), ('base', b['base'])):
        tip = value.get(side) or {}
        repo = tip.get('repo') or {}
        require(tip.get('ref') == ref and repo.get('id') == b['repository_id']
                and str(repo.get('full_name', '')).casefold() == b['repository'], 'Merged PR target/source differs')
    require(value['head'].get('sha') == b['commit'], 'PR head was changed after the verified candidate')
    sha = value.get('merge_commit_sha')
    require(isinstance(sha, str) and re.fullmatch('[a-f0-9]{40}', sha), 'Actual merged commit unavailable')
    return {'id': value['id'], 'number': number, 'url': value['html_url'], 'head_sha': b['commit'],
            'base': b['base'], 'merge_commit_sha': sha, 'merged_at': stamp(value.get('merged_at')),
            'body_sha256': fingerprint(value.get('body')), 'from_unknown_intent': receipt is None}


def workflows(reader, binding, head, policy):
    base = '/repos/'+binding['repository']
    rows = reader.pages(base+'/actions/runs?branch='+quote(binding['base'], safe='')+'&head_sha='+head+'&per_page=30&page=', 'workflow_runs')
    chosen, seen = {}, set()
    for row in rows:
        require(positive(row.get('id')) and row['id'] not in seen, 'Duplicate workflow run identity')
        seen.add(row['id'])
        if row.get('workflow_id') not in policy['workflow_ids']:
            continue
        require(positive(row.get('workflow_id')) and row.get('head_sha') == head and row.get('head_branch') == binding['base']
                and (row.get('repository') or {}).get('id') == binding['repository_id']
                and positive(row.get('run_number')) and positive(row.get('run_attempt')), 'Workflow version/source differs')
        key = (row['run_number'], row['run_attempt'])
        old = chosen.get(row['workflow_id'])
        require(not old or key != (old['run_number'], old['run_attempt']), 'Ambiguous latest workflow run')
        if not old or key > (old['run_number'], old['run_attempt']):
            chosen[row['workflow_id']] = row
    require(set(chosen) == set(policy['workflow_ids']), 'Required acceptance workflows are missing')
    result = []
    for workflow_id in sorted(chosen):
        row = chosen[workflow_id]
        require(row.get('event') in policy['events'] and row.get('status') == 'completed'
                and row.get('conclusion') == 'success', 'Latest acceptance workflow has not passed the trusted event policy')
        jobs = reader.pages(base+f"/actions/runs/{row['id']}/attempts/{row['run_attempt']}/jobs?per_page=30&page=", 'jobs')
        require(jobs and len({job.get('id') for job in jobs}) == len(jobs), 'No complete unique acceptance jobs')
        checked = []
        for job in jobs:
            require(positive(job.get('id')) and positive(job.get('run_id')) and job.get('run_id') == row['id']
                    and job.get('head_sha') == head and job.get('run_attempt', row['run_attempt']) == row['run_attempt']
                    and job.get('status') == 'completed' and job.get('conclusion') == 'success',
                    'Acceptance job was skipped/failed/pending or belongs to another version')
            checked.append({'id': job['id'], 'name': str(job.get('name', ''))[:200],
                            'status': 'completed', 'conclusion': 'success',
                            'completed_at': stamp(job.get('completed_at'))})
        result.append({'workflow_id': workflow_id, 'id': row['id'], 'run_number': row['run_number'],
                       'run_attempt': row['run_attempt'], 'head_sha': head, 'event': row['event'],
                       'status': 'completed', 'conclusion': 'success',
                       'updated_at': stamp(row.get('updated_at')), 'jobs': sorted(checked, key=lambda j: j['id'])})
    return result


def merged_graph(reader, b, pull, head):
    path = '/repos/'+b['repository']+'/compare/'+pull['merge_commit_sha']+'...'+head
    comparison = reader.api(path+'?per_page=1')
    require(str(comparison.get('url', '')).casefold() == 'https://api.github.com'+path
            and comparison.get('base_commit', {}).get('sha') == pull['merge_commit_sha']
            and comparison.get('merge_base_commit', {}).get('sha') == pull['merge_commit_sha']
            and comparison.get('behind_by') == 0
            and ((comparison.get('status') == 'identical' and pull['merge_commit_sha'] == head and comparison.get('ahead_by') == 0)
                 or (comparison.get('status') == 'ahead' and pull['merge_commit_sha'] != head
                     and type(comparison.get('ahead_by')) is int and comparison['ahead_by'] > 0)),
            'Default branch does not provably contain the delivered merge commit')
    return {'status': comparison['status'], 'merge_commit_sha': pull['merge_commit_sha'],
            'default_head_sha': head}


def observe(delivery, remote, identity, policy):
    require(policy == acceptance_policy(policy['workflow_ids'], policy['events']), 'Acceptance rules changed')
    association = history(delivery, identity)
    b, original = association['binding'], association['original']
    reader = ReadWindow(remote)
    meta = reader.api('/repos/'+b['repository'])
    require(meta.get('id') == b['repository_id'] and str(meta.get('full_name', '')).casefold() == b['repository']
            and meta.get('default_branch') == b['base'], 'Only actual default-branch delivery can close this Issue')
    account = reader.api('/user')
    require(positive(account.get('id')) and isinstance(account.get('login'), str), 'Current GitHub actor unavailable')
    actor = {'id': account['id'], 'login': account['login'].casefold()}
    issue = issue_snapshot(reader, delivery.execution.spec, original, delivery.registry)
    pull = merged_pull(reader, association)
    head = issue['head_sha']
    inclusion = merged_graph(reader, b, pull, head)
    accepted = workflows(reader, b, head, policy)
    require(merged_pull(reader, association) == pull
            and issue_snapshot(reader, delivery.execution.spec, original, delivery.registry) == issue,
            'Delivery/Issue changed during evidence collection')
    latest_meta = reader.api('/repos/'+b['repository'])
    require(latest_meta.get('id') == b['repository_id'] and latest_meta.get('default_branch') == b['base'], 'Default repository changed')
    return {'association_sha256': fingerprint(association), 'binding': b, 'issue': issue, 'pr': pull,
            'default_head_sha': head, 'workflows': accepted, 'policy_sha256': policy['policy_sha256'],
            'github_actor': actor, 'inclusion': inclusion}
