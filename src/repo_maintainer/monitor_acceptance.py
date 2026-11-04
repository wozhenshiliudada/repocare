"""Explicit historical monitor delivery verification. Remote reads only; no models."""
import json,re,uuid
from urllib.parse import quote
from .dashboard import read_json
from .integration import fingerprint as review_fingerprint
from .issue_acceptance import ReadWindow,acceptance_policy,merged_pull,merged_graph,workflows,stopped,require,positive
from .issue_intake import fingerprint
from .monitor_history import identity,journal,object_at
from .store import now,redact

KIND='monitor_acceptance'


def association(delivery,run_id):
    """Historical authorization survives checkout advancement or candidate cleanup."""
    identity(run_id,32)
    row=delivery.view(run_id);core=row['core'];b=core['binding']
    require(row['status'] in {'awaiting-review','remote-created-review-required','remote-closed-review',
        'creation_unknown','create_intent'},'原监控PR尚无已批准的创建意图')
    require(row['approval_sha256']==fingerprint(core)==row.get('authorization',{}).get('approval_sha256'),
        '原监控PR准确批准缺失或变化')
    stopped(row)
    event=delivery.registry.load('event').get(delivery.eid)
    require(event and event['sha']==b['base_sha'],'原监控事件与发布基线不同')
    controls=[event.get('root_execution')]+list(delivery.registry.load('monitor_continuation').values())
    keys=('binding','job_id','plan_id','run_id','parent_run_id','parent_record_sha256','task','original_intent_sha256')
    matches=[c for c in controls if c and fingerprint({k:c.get(k) for k in keys})==b['control_sha256']]
    require(len(matches)==1 and fingerprint(matches[0]['binding'])==b['controller_sha256'],
        '原监控执行历史关联缺失或不同')
    rule=delivery.registry.load('monitor_root_rule_version').get(b['root_rules_sha256'])
    require(rule and rule['sha256']==fingerprint({k:v for k,v in rule.items() if k!='sha256'}),
        '原目录规则版本不可核验')
    root,directory=delivery._paths(run_id)
    record,record_sha=read_json(directory/'run.json');review,_=read_json(directory/'integration-review.json')
    cleaned=(record.get('status')=='discarded' and record.get('source_unchanged') is True
        and record.get('recovery')=={'result':'candidate_removed','source_reset':False})
    require((record_sha==b['run_record_sha256'] or cleaned)
        and fingerprint(record.get('policy'))==b['policy_sha256']
        and fingerprint(record.get('candidate_tests'))==b['candidate_tests_sha256']
        and fingerprint(record.get('environment'))==b['environment_sha256']
        and record.get('run_id')==run_id
        and record.get('integration_commit')==b['commit'] and record.get('base_sha')==b['base_sha']
        and record.get('integration_branch')==b['head'] and record.get('integration_review_sha256')==b['review_sha256']
        and review.get('status')=='published' and review_fingerprint(review['review'])==b['review_sha256']
        and review.get('authorization',{}).get('decision')=='approve'
        and review['authorization'].get('review_sha256')==b['review_sha256']
        and all(review['review'].get(k)==b[v] for k,v in [('run_id','run_id'),('commit','commit'),
            ('base_sha','base_sha'),('target_branch','head'),('diff_sha256','diff_sha256')]),
        '原报告或封装批准与历史交付不同')
    with journal(root) as db:
        meta=json.loads(db.execute('SELECT body FROM metadata').fetchone()[0])
        require(meta.get('repository')==record['source_repo'] and meta.get('policy')==record['policy']
            and meta.get('shared_budget')==str(delivery.registry.path),'原事件日志/范围/共享账关联不同')
        require(object_at(db,'run',run_id).get('run_id')==run_id,'原运行登记缺失')
    receipt=row.get('receipt')
    if receipt:require(receipt['head_sha']==b['commit'] and receipt['payload_sha256']==fingerprint(core['payload']),
        '原PR回执与批准内容不同')
    return {'binding':b,'payload':core['payload'],'receipt':receipt,
        'pr_authorization_sha256':row['approval_sha256'],'marker_prefix':'repocare-monitor-delivery:',
        'current_report_sha256':record_sha,'registered_cleanup':cleaned,
        'original_ci_workflow_id':(matches[0]['binding'].get('ci_failure') or {}).get('workflow_id')}


class MonitorAcceptance:
    def __init__(self,delivery,remote=None):
        from .github_acceptance import GitHubAcceptance
        self.delivery,self.registry=delivery,delivery.registry
        self.remote=remote if remote is not None else GitHubAcceptance()

    def view(self,acceptance_id):
        identity(acceptance_id,32)
        row=self.registry.load(KIND).get(acceptance_id)
        require(row,'未知监控验收记录')
        self.delivery._record_origin({'core':{'binding':row['core']['binding']}},row['run_id'])
        return {**row,'challenge':fingerprint(row)}

    def _scope(self,run_id,reader):
        original=association(self.delivery,run_id);b=original['binding'];base='/repos/'+b['repository']
        meta=reader.api(base)
        require(meta.get('id')==b['repository_id'] and str(meta.get('full_name','')).casefold()==b['repository']
            and meta.get('default_branch')==b['base'],'只验收原仓库的实际默认分支')
        pr=merged_pull(reader,original)
        branch=reader.api(base+'/branches/'+quote(b['base'],safe=''))
        head=branch.get('commit',{}).get('sha')
        require(branch.get('name')==b['base'] and isinstance(head,str) and re.fullmatch('[a-f0-9]{40}',head),
            '默认分支准确提交不可核验')
        rows=reader.pages(base+'/actions/runs?branch='+quote(b['base'],safe='')+'&head_sha='+head+'&per_page=30&page=','workflow_runs')
        require(rows and len({r.get('id') for r in rows})==len(rows),'缺少完整且唯一的交付版本CI运行')
        observed=[]
        for r in rows:
            require(all(positive(r.get(k)) for k in ('id','workflow_id','run_number','run_attempt'))
                and r.get('head_sha')==head and r.get('head_branch')==b['base']
                and (r.get('repository') or {}).get('id')==b['repository_id'],'验收工作流来源或版本不同')
            observed.append({k:r.get(k) for k in ('id','workflow_id','run_number','run_attempt','event','status','conclusion','updated_at')})
        ids=sorted({r['workflow_id'] for r in observed})
        required=original['original_ci_workflow_id']
        require(required is None or required in ids,'原失败工作流在准确交付版本尚无运行')
        policy=acceptance_policy(ids)
        return {'binding':b,'association_sha256':fingerprint(original),'pr':pr,'default_head_sha':head,
            'acceptance_policy':policy,'observed_workflows':sorted(observed,key=lambda r:r['id']),
            'original_ci_workflow_id':required,
            'coverage':'all_observed_workflows_at_this_head_plus_original_ci_if_present',
            'remote_writes':False,'local_tests_executed':False},original

    def prepare(self,run_id):
        core,_=self._scope(run_id,ReadWindow(self.remote))
        require(len(json.dumps(core).encode())<512*1024,'监控验收提案超过界限')
        aid=uuid.uuid4().hex
        with self.registry.transaction() as db:
            require(db.execute('SELECT count(*) FROM objects WHERE kind=?',(KIND,)).fetchone()[0]<100,
                '原验收记录达到容量；保留历史')
            self.registry.put(KIND,aid,{'id':aid,'run_id':run_id,'status':'prepared','revision':0,
                'core':core,'approval_sha256':fingerprint(core),'created_at':now()},db)
        return self.view(aid)

    def verify(self,aid,challenge,approval_sha256):
        row=self.view(aid)
        require(row['status']=='prepared' and row['challenge']==challenge
            and row['approval_sha256']==approval_sha256==fingerprint(row['core']),'准确验收范围批准缺失或已使用')
        with self.registry.transaction() as db:
            current=json.loads(db.execute('SELECT body FROM objects WHERE kind=? AND id=?',(KIND,aid)).fetchone()[0])
            require(fingerprint(current)==challenge,'另一操作者已处理此验收')
            current.update(status='checking',revision=1,authorization={'approval_sha256':approval_sha256,'actor':'local-workbench'})
            self.registry.put(KIND,aid,current,db)
        try:
            reader=ReadWindow(self.remote);core,original=self._scope(row['run_id'],reader)
            require(core==row['core'],'PR/默认分支/CI/历史来源已变化；请重新预览')
            graph=merged_graph(reader,core['binding'],core['pr'],core['default_head_sha'])
            checked=workflows(reader,core['binding'],core['default_head_sha'],core['acceptance_policy'])
            after,_=self._scope(row['run_id'],reader)
            require(after==core,'验收期间版本或CI变化；不能记为交付完成')
            proof={'default_head_sha':core['default_head_sha'],'pr':core['pr'],'inclusion':graph,
                'workflows':checked,'local_tests_executed':False,'remote_writes':0}
            status,error='delivery-verified',None
        except Exception as caught:
            proof=None;status,error='not-verified',redact(type(caught).__name__+': '+str(caught))
        with self.registry.transaction() as db:
            current=json.loads(db.execute('SELECT body FROM objects WHERE kind=? AND id=?',(KIND,aid)).fetchone()[0])
            require(current['status']=='checking','原验收意图已变化')
            current.update(status=status,revision=2,proof=proof,error=error,observed_at=now())
            self.registry.put(KIND,aid,current,db)
        return self.view(aid)
