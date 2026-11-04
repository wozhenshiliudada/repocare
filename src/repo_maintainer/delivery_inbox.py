"""Read-only delivery projection of original journals and fixed run evidence."""
import json
import os
import re
import sqlite3

from .dashboard import pick,read_json,unlinked
from .issue_intake import fingerprint
from .models import SafetyError,digest
from .monitor_history import journal,object_at,budget_at,identity
from .store import redact
from .target_environment import classify

MAX_ITEMS=100


def rows(db,kind):
    values=db.execute('SELECT body FROM objects WHERE kind=? ORDER BY rowid LIMIT 101',(kind,)).fetchall()
    if len(values)>100:raise SafetyError('原记录超过有界读取范围')
    return [json.loads(v[0]) for v in values]


def report(directory,expected_source,run_id,base_sha,execution=None):
    identity(run_id,32)
    record,sha=read_json(unlinked(directory/run_id/'run.json'))
    if record.get('run_id')!=run_id or record.get('source_repo')!=expected_source or record.get('base_sha')!=base_sha:
        raise SafetyError('原报告与任务、来源或提交不符')
    if execution:
        expected={k:execution[k] for k in ('issue_number','issue_token','task_id','rules_sha256')}
        expected.update(execution_id=execution['id'],policy_binding_sha256=execution['policy_binding']['binding_sha256'])
        if record.get('issue_execution')!=expected:raise SafetyError('原Issue/run反向绑定不符')
    checks=record.get('candidate_tests',[])
    if not isinstance(checks,list) or len(checks)>100 or any(not isinstance(t,dict) for t in checks):
        raise SafetyError('原检查记录无效')
    # Older runs retain raw output rather than classified checks. Derive the
    # same conservative summary without changing their bytes or evidence SHA.
    checks=[classify(t) if 'category' not in t else t for t in checks]
    complete=bool(record.get('finished_at'))
    passed=complete and bool(checks) and all(type(t.get('returncode')) is int and t['returncode']==0
        and t.get('timed_out') is False and t.get('category')=='passed' for t in checks)
    count=sum(t['tests_run'] for t in checks if type(t.get('tests_run')) is int and t['tests_run']>=0)
    return {'run_id':run_id,'base_sha':base_sha,'report_sha256':sha,'report_status':record.get('status'),
        'finished':complete,'checks_passed':passed,'tests_run':count,'effective_test_count_known':all(type(t.get('tests_run')) is int for t in checks),
        'source_unchanged':record.get('source_unchanged') is True,
        'checks':[pick(t,'command returncode timed_out tests_run category') for t in checks],
        'diff_excerpt':str(record.get('diff',''))[:16000],
        'diff_excerpt_truncated':len(str(record.get('diff','')))>16000,
        'local_publication':pick(record,'integration_branch integration_commit'),
        'recorded_at':record.get('finished_at'),
        'title':str(record.get('issue','')).split('\n')[0][:240]}


def delivery_stage(db,execution_id):
    result={}
    for kind,key in [('issue_publication','push'),('issue_delivery','pr'),('issue_closure','closure')]:
        found=db.execute('SELECT body FROM objects WHERE kind=? AND id=?',(kind,execution_id)).fetchone()
        if found:
            value=json.loads(found[0]);receipt=value.get('receipt') or {}
            result[key]={'status':value.get('status'),'receipt':pick(receipt,'url number sha ref')}
    return result


def projection(hub):
    """No Git, network, provider/session allocation, claims or journal writes."""
    with hub.lock:
        workspaces={str(unlinked(w.session.runs_dir)):w.identity for w in hub.workspaces.values()}
    items=[];errors=[];truncated=False;groups=0
    def add(origin,budget,load,stage=None,observed_changed=False):
        nonlocal truncated
        if len(items)>=MAX_ITEMS:truncated=True;return
        stable=fingerprint({k:v for k,v in origin.items() if k!='workspace_id'})
        item={'id':stable,'origin':origin,'budget':budget,'delivery':stage or {},
              'read_only':True,'runtime_authorized':False,'freshness':'not_revalidated',
              'source_changed_observed':observed_changed}
        try:
            evidence=load();item['evidence']=evidence
            candidate=evidence['report_status'] in {'candidate_ready','integrated'} and evidence['checks_passed'] and evidence['source_unchanged']
            item['status']='candidate_review' if candidate and not observed_changed else 'needs_review'
            if not evidence['finished']:item['status']='in_progress'
            if not observed_changed and (stage or {}).get('closure',{}).get('status')=='closed-verified':
                item['status']='delivery_recorded'
            item['title']=evidence['title'] or '原维护任务'
        except (SafetyError,OSError,ValueError,KeyError,TypeError):
            item.update(status='evidence_unavailable',title='原维护证据待核验',evidence=None)
        item['revision']=fingerprint(item)
        notice_delivery=dict(item['delivery'])
        if notice_delivery.get('acceptance_watch'):
            watch=notice_delivery['acceptance_watch']
            notice_delivery['acceptance_watch']={k:watch.get(k) for k in ('id','status','enabled','notification')}
            if watch.get('status')=='checking' and watch.get('last_observation'):
                notice_delivery['acceptance_watch']['status']=watch['last_observation']['outcome']['status']
        item['notice_id']=fingerprint([stable,item['status'],(item.get('evidence') or {}).get('report_sha256'),
                                      notice_delivery,observed_changed])
        items.append(item)

    # Only fixed original project registrations; never recurse into workspaces/Git.
    projects=unlinked(hub.root/'projects')
    if projects.is_dir():
        for path in sorted(projects.iterdir()):
            if not re.fullmatch('[a-f0-9]{16}',path.name):continue
            if groups>=20:truncated=True;break
            groups+=1
            try:
                project,_=read_json(unlinked(path/'project.json'))
                source=project['repository'];root=unlinked(path/'runs')
                if path.name!=digest(os.path.normcase(source).encode())[:16]:raise SafetyError('原项目登记归属不符')
                with journal(root) as db:
                    meta=json.loads(db.execute('SELECT body FROM metadata').fetchone()[0])
                    if meta.get('repository')!=source or meta.get('policy')!=project['policy']:raise SafetyError('原项目/账本绑定不符')
                    budget=budget_at(db);executions=rows(db,'issue_execution')
                    observations=rows(db,'issue_watch_observation')
                    for execution in executions:
                        eid=identity(execution['id'],32);rid=identity(execution['run_id'],32)
                        base=execution['source_before']['head']
                        if not re.fullmatch('[a-f0-9]{40}',base):raise SafetyError('原提交无效')
                        origin={'kind':'issue','project_id':path.name,'repository':source,'execution_id':eid,
                                'issue_number':execution['issue_number'],'run_id':rid,'task_id':execution.get('task_id'),
                                'workspace_id':workspaces.get(str(root))}
                        changed=any(v['number']==execution['issue_number'] and v['token']!=execution['issue_token'] for v in observations)
                        head=db.execute("SELECT body FROM objects WHERE kind='issue_execution_head' AND id=?",(execution['issue_key'],)).fetchone()
                        changed=changed or bool(head and json.loads(head[0])['execution_id']!=eid)
                        add(origin,budget,lambda e=execution:report(root,source,e['run_id'],e['source_before']['head'],e),
                            delivery_stage(db,eid),changed)
            except (SafetyError,OSError,ValueError,KeyError,TypeError,sqlite3.Error):
                errors.append({'kind':'issue','id':path.name,'message':'原项目或账本不可读；不新建替代记录'})

    with hub.monitor.lock:monitors=list(hub.monitor.records)
    for mid in monitors:
        if groups>=30:truncated=True;break
        groups+=1
        try:
            identity(mid,16);root=unlinked(hub.monitor.root/mid)
            with journal(root) as db:
                meta=json.loads(db.execute('SELECT body FROM metadata').fetchone()[0])
                if meta.get('monitor')!=hub.monitor.records[mid]['spec']:raise SafetyError('原监控/账本绑定不符')
                budget=budget_at(db);events=rows(db,'event');stages={}
                for kind,key in [('monitor_publication','push'),('monitor_delivery','pr')]:
                    for stage in rows(db,kind):
                        rid=identity(stage['id'],32)
                        stages.setdefault(rid,{})[key]={'status':stage.get('status'),
                            'receipt':pick(stage.get('receipt') or {},'url number sha ref')}
                for stage in rows(db,'monitor_acceptance'):
                    rid=identity(stage['run_id'],32);proof=stage.get('proof') or {}
                    stages.setdefault(rid,{})['acceptance']={'status':stage.get('status'),'id':stage['id'],
                        'receipt':{'default_head_sha':proof.get('default_head_sha'),
                                   'url':(proof.get('pr') or {}).get('url')},'error':stage.get('error')}
                from .acceptance_watch import projection as watch_projection
                for stage in rows(db,'monitor_acceptance_watch'):
                    rid=identity(stage['run_id'],32)
                    if stage.get('last_observation') and object_at(db,'monitor_acceptance_observation',
                            stage['last_observation']['id'])!=stage['last_observation']:
                        raise SafetyError('原观察回执缺失或变化')
                    stages.setdefault(rid,{})['acceptance_watch']=watch_projection(stage)
            for event in events:
                eid=identity(event['id'],16);base=event['sha']
                if not re.fullmatch('[a-f0-9]{40}',base):raise SafetyError('原事件提交无效')
                if eid!=digest(base.encode())[:16]:raise SafetyError('原事件与提交身份不符')
                run_root=unlinked(root/'r'/eid) if event.get('root_execution') else unlinked(root/'events'/eid/'runs')
                if not (run_root/'ui-state/registry.sqlite3').is_file():continue
                with journal(run_root) as db:
                    registered=rows(db,'run')
                source=str(unlinked(root/'events'/eid/'source'))
                for saved in registered:
                    rid=identity(saved['run_id'],32)
                    origin={'kind':'monitor','monitor_id':mid,'event_id':eid,'run_id':rid,
                            'repository':hub.monitor.records[mid]['spec']['path'],
                            'workspace_id':workspaces.get(str(run_root))}
                    changed=bool(hub.monitor.records[mid].get('latest_sha') and hub.monitor.records[mid]['latest_sha']!=base)
                    changed=changed or bool(event.get('root_preparation_historical'))
                    add(origin,budget,lambda r=rid:report(run_root,source,r,base),stage=stages.get(rid),observed_changed=changed)
        except (SafetyError,OSError,ValueError,KeyError,TypeError,sqlite3.Error):
            errors.append({'kind':'monitor','id':mid,'message':'原监控或报告不可读；请核对原历史'})
    value={'schema_version':1,'feed_id':fingerprint(str(hub.root)),'read_only':True,'runtime_authorized':False,
           'model_requests':0,'items':items,'errors':errors,'truncated':truncated,'complete':not truncated and not errors,
           'budget_semantics':'each original journal separately; never sum or recreate budgets',
           'freshness':'recorded evidence only; original controllers revalidate before any operation'}
    raw=redact(json.dumps(value,ensure_ascii=False))
    if len(raw.encode())>1024*1024:raise SafetyError('交付待办超过1MiB；原记录保留')
    return json.loads(raw)
