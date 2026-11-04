"""Journal-local maintenance facts and versioned human priorities. No execution authority."""
import json
import re
import sys
import uuid
from .dashboard import read_json
from .git import RepositoryGateway
from .models import SafetyError,digest
from .store import now,redact
from .task_session import fingerprint,require,TASK_ID

ERRORS=(SafetyError,ValueError,KeyError,TypeError,OSError)
KINDS={'note','plan','run','checkpoint'}
LIMIT=100
MAX_NOTE_VERSIONS=1000


def clean(value,maximum):
    require(isinstance(value,str) and value.strip() and len(value)<=maximum and
            not re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f]',value) and redact(value)==value,'Invalid maintenance text or recognized secret')
    return value


def item(kind,identity,source,*,title,detail,stage,proof,task_id=None,run_id=None,plan_id=None,rounds=None,exits=(),priority=1):
    body={'kind':kind,'title':re.sub(r'[\x00-\x1f]',' ',redact(title))[:1000],
          'detail':re.sub(r'[\x00-\x1f]',' ',redact(detail))[:1200],'stage':stage,'proof':proof,
          'task_id':task_id,'run_id':run_id,'plan_id':plan_id,'rounds':rounds,'test_exits':list(exits),'priority':priority}
    return {'id':kind+':'+identity,'body':body,'body_sha256':fingerprint(body),
            'source_ref':{'kind':kind,'id':identity,'sha256':fingerprint(source)},
            'classification':'proposed' if kind=='note' else 'historical'}


class MaintenanceMemory:
    def __init__(self,session):
        self.session=session;self.registry=session.registry;self.experience=session.experience
        self.repository=self.experience.repository

    def settings(self):
        saved=self.registry.load('maintenance_settings').get('default')
        require(saved is None or (set(saved)=={'schema_version','enabled'} and type(saved['schema_version']) is int
                and saved['schema_version']==1 and type(saved['enabled']) is bool),
                'Invalid maintenance settings')
        return bool(saved and saved['enabled'])

    def revision(self):
        return fingerprint({'notes':self.registry.load('maintenance_note'),'settings':self.registry.load('maintenance_settings'),
                            'session':self.session.token})

    def configure(self,enabled,revision):
        require(type(enabled) is bool and revision==self.revision(),'Maintenance settings changed')
        self.registry.put('maintenance_settings','default',{'schema_version':1,'enabled':enabled})
        return {'maintenance_enabled_for_new_tasks':enabled}

    def save_note(self,identity,revision,title,detail,status,priority):
        require(revision==self.revision(),'Maintenance revision changed; review original notes again')
        clean(title,200);clean(detail,1200)
        require(status in {'open','deferred','closed_by_user'} and type(priority) is int and 0<=priority<=3,
                'Invalid declared maintenance priority or state')
        heads=self.registry.load('maintenance_note')
        require(identity is None or isinstance(identity,str) and TASK_ID.fullmatch(identity) and identity in heads,
                'Unknown original maintenance note')
        require(identity is not None or len(heads)<LIMIT,'Maintenance note limit reached; original history retained')
        identity=identity or uuid.uuid4().hex;old=heads.get(identity)
        if old:self.resolve('note',identity)
        require(old is None or old['version']<MAX_NOTE_VERSIONS,'Maintenance version limit reached; original history retained')
        version_id=uuid.uuid4().hex
        value={'schema_version':1,'repository_sha256':self.repository,'note_id':identity,
               'version':old['version']+1 if old else 1,'previous':old['version_id'] if old else None,
               'previous_sha256':old['payload_sha256'] if old else None,
               'title':title,'detail':detail,'status':status,'priority':priority,
               'classification':'proposed','runtime_authorized':False,'recorded_at':now()}
        head={'version_id':version_id,'version':value['version'],'payload_sha256':fingerprint(value)}
        with self.registry.transaction() as db:
            rows=db.execute("SELECT id,body FROM objects WHERE kind='maintenance_note'").fetchall()
            require({i:json.loads(body) for i,body in rows}==heads,'Maintenance note compare-and-swap failed')
            self.registry.put('maintenance_note_version',version_id,value,db)
            self.registry.put('maintenance_note',identity,head,db)
        return {'note_id':identity,'version':value['version'],'status':status,'runtime_authorized':False}

    def resolve(self,kind,identity):
        require(kind in KINDS and isinstance(identity,str) and TASK_ID.fullmatch(identity),'Invalid maintenance reference')
        if kind=='note':
            head=self.registry.load('maintenance_note')[identity]
            require(set(head)=={'version_id','version','payload_sha256'} and type(head['version']) is int and head['version']>0,
                    'Invalid maintenance note head')
            value=self.registry.load('maintenance_note_version')[head['version_id']]
            require(set(value)=={'schema_version','repository_sha256','note_id','version','previous','previous_sha256','title','detail','status',
                                'priority','classification','runtime_authorized','recorded_at'} and type(value['schema_version']) is int
                    and value['schema_version']==1 and type(value['version']) is int and value['version']<=MAX_NOTE_VERSIONS
                    and value['repository_sha256']==self.repository and value['note_id']==identity and value['version']==head['version']
                    and head['payload_sha256']==fingerprint(value) and value['classification']=='proposed'
                    and value['runtime_authorized'] is False,'Maintenance declaration changed')
            clean(value['title'],200);clean(value['detail'],1200)
            require(value['status'] in {'open','deferred','closed_by_user'} and type(value['priority']) is int
                    and 0<=value['priority']<=3,'Invalid maintenance declaration state')
            # Version history remains retained; a declaration never certifies a code fix.
            versions=self.registry.load('maintenance_note_version');cursor=value;visited=set()
            while cursor['version']>1:
                previous=cursor['previous'];require(isinstance(previous,str) and TASK_ID.fullmatch(previous)
                    and previous not in visited and len(visited)<MAX_NOTE_VERSIONS,'Invalid maintenance declaration history')
                visited.add(previous);parent=versions.get(previous)
                require(parent and type(parent.get('version')) is int and parent['version']==cursor['version']-1 and parent.get('note_id')==identity
                    and parent.get('repository_sha256')==self.repository and fingerprint(parent)==cursor['previous_sha256'],
                    'Maintenance declaration history is missing or changed')
                cursor=parent
            require(cursor['version']==1 and cursor['previous'] is None and cursor['previous_sha256'] is None,
                    'Invalid first maintenance declaration version')
            return item(kind,identity,{'head':head,'version':value},title=value['title'],detail=value['detail'],
                        stage=value['status'],proof='user_declaration',priority=value['priority'])
        if kind=='run':
            require(identity in self.session.runs,'Unregistered original maintenance run')
            value=self.experience.original(identity)
            last=value['rounds'][-1] if value['rounds'] else None
            return item(kind,identity,value,title=value['issue'],detail='Original candidate result; publication and merge are separate.',
                        stage=value['outcome'],proof='actual_test' if last and last['verification'] in {'passed','failed'} else 'registration',
                        task_id=value['task_id'],run_id=identity,rounds=len(value['rounds']),exits=last['test_exits'] if last else ())
        if kind=='plan':
            require(identity in self.session.plans,'Unregistered original maintenance plan')
            saved=self.session.plans[identity];data,sha=read_json(saved['path'])
            registered=self.registry.load('plan')[identity]
            require(registered['file_sha']==saved['file_sha'] and registered['view']==saved['view']
                    and saved['view']['status'] in {'awaiting_approval','consumed'},'Maintenance plan registration changed')
            from .pi_provider import decode_plan
            plan=decode_plan(data);self.session.policy.validate(plan)
            require(sha==saved['file_sha'] and plan.fingerprint==data['plan_sha256']==saved['view']['fingerprint'],
                    'Original maintenance plan evidence changed')
            link=self.registry.load('pi_task_link').get('plan:'+identity)
            require(link and set(link)=={'task_id'} and TASK_ID.fullmatch(link['task_id']),'Original maintenance plan task missing')
            return item(kind,identity,{'file_sha256':sha,'status':saved['view']['status'],'task':link},
                        title=plan.issue,detail='Proposal only; original approval is not inherited.',stage=saved['view']['status'],
                        proof='registration',task_id=link['task_id'],plan_id=identity)
        record=self.registry.load('pi_task')[identity]
        record=self.session.task_planner.store._load(identity,record['scope'])
        require(record['revision']>0,'No completed checkpoint registration')
        titles=[v['view']['plan']['issue'] for i,v in self.session.plans.items()
                if self.registry.load('pi_task_link').get('plan:'+i)=={'task_id':identity}]
        title=titles[0] if titles else 'Registered previous task checkpoint'
        require(not titles or all(fingerprint(t)==record['scope']['issue_sha256'] for t in titles),
                'Maintenance checkpoint original issue changed')
        return item(kind,identity,record,title=title,
                    detail='Metadata cursor only; native history must be independently verified before continuation.',
                    stage='registered_checkpoint',proof='registration',task_id=identity,rounds=record['revision'])

    def snapshot(self):
        rows=[];excluded=[]
        collections=[('note','maintenance_note'),('plan','plan'),('run','run'),('checkpoint','pi_task')]
        for kind,source in collections:
            entries=self.registry.load(source)
            require(len(entries)<=LIMIT,'Maintenance projection exceeds bounded original record count')
            for identity in sorted(entries):
                if kind=='checkpoint' and not entries[identity].get('revision'):continue
                try:rows.append(self.resolve(kind,identity))
                except ERRORS as error:excluded.append({'kind':kind,'id':identity,'reason':redact(str(error))[:300]})
        issues=[r for r in rows if (r['body']['kind']=='note' and r['body']['stage']!='closed_by_user')
                or (r['body']['kind']=='plan' and r['body']['stage']=='awaiting_approval')
                or (r['body']['kind']=='run' and r['body']['stage'] in {'test_failed','candidate_test_passed'})]
        packet={'schema_version':1,'repository_sha256':self.repository,'observations':rows,
                'cursors':[r for r in rows if r['body']['kind']=='checkpoint'],'issues':issues,
                'fixes':[r for r in rows if r['body']['kind']=='run'],
                'checks':[r for r in rows if r['body']['proof']=='actual_test'],
                'completed_actions':[r for r in rows if r['body']['proof']=='actual_test'],
                'priorities':[r for r in rows if r['body']['kind']=='note' and r['body']['stage']=='open'],
                'excluded':excluded,'runtime_authorized':False}
        # completed_actions means the recorded test attempt finished, never that a fix was merged.
        packet['snapshot_sha256']=fingerprint(packet)
        return packet

    def view(self):
        if self.session.active:return {'status':'busy','runtime_authorized':False}
        try:return {'status':'verified','enabled':self.settings(),'expected_revision':self.revision(),**self.snapshot()}
        except ERRORS as error:return {'status':'blocked','reason':redact(str(error)),'runtime_authorized':False}

    def search(self,issue):
        require(isinstance(issue,str) and 0<len(issue)<=2000,'Invalid maintenance query')
        packet=self.snapshot();candidates={r['id']:r for r in [*packet['issues'],*packet['cursors']]}
        excluded=list(packet['excluded'])
        for key,row in list(candidates.items()):
            if row['body']['kind'] in {'plan','run'} and row['body']['task_id'] is None:
                excluded.append({'kind':row['body']['kind'],'id':row['source_ref']['id'],
                                 'reason':'Historical record has no original native task binding; reference only, not loaded'})
                del candidates[key]
        from .semantic_retrieval import session_rank
        matches,_,strategy=session_rank(self.session,issue,{i:r['body']['title']+'\n'+r['body']['detail'] for i,r in candidates.items()})
        order=[identity for _,identity in matches]
        order += [r['id'] for r in sorted(packet['priorities'],key=lambda r:(-r['body']['priority'],r['id'])) if r['id'] not in order]
        hits=[];size=0
        for identity in order:
            row=candidates[identity];length=len(json.dumps(row,ensure_ascii=False).encode())
            if size+length>12000:continue
            hits.append(row);size+=length
            if len(hits)==5:break
        return {'hits':hits,'excluded':excluded,'snapshot_sha256':packet['snapshot_sha256'],'runtime_authorized':False,'retrieval':strategy}

    def begin(self,identity,issue,continuation):
        if continuation or not self.settings():return
        selected=self.search(issue)
        hits=[r for r in selected['hits'] if r['body']['task_id']!=identity]
        pin={'schema_version':1,'task_id':identity,'repository_sha256':self.repository,
             'issue_sha256':digest(issue.encode()),'source_files':self.experience.files(),'hits':hits}
        self.registry.put('maintenance_task',identity,{'payload':pin,'payload_sha256':fingerprint(pin)})
        from .semantic_retrieval import record_selection
        record_selection(self.session,identity,issue,'maintenance',{**selected,'hits':hits})

    def verify_task(self,identity,issue):
        saved=self.registry.load('maintenance_task').get(identity)
        receipts=[a.get('evidence',{}).get('maintenance_context_sha256') for a in self.registry.load('attempt').values()
                  if a.get('evidence',{}).get('task_id')==identity and a.get('evidence',{}).get('maintenance_context_sha256')]
        if saved is None:
            require(not receipts,'Original maintenance selection is missing');return None
        pin=saved['payload']
        require(set(saved)=={'payload','payload_sha256'} and saved['payload_sha256']==fingerprint(pin)
                and set(pin)=={'schema_version','task_id','repository_sha256','issue_sha256','source_files','hits'}
                and pin['schema_version']==1 and pin['task_id']==identity and pin['repository_sha256']==self.repository
                and pin['issue_sha256']==digest(issue.encode()) and pin['source_files']==self.experience.files()
                and isinstance(pin['hits'],list) and len(pin['hits'])<=5,'Maintenance task selection changed')
        for attempt in self.registry.load('attempt').values():
            evidence=attempt.get('evidence',{})
            if evidence.get('task_id')==identity and evidence.get('maintenance_context_sha256'):
                require(evidence.get('maintenance_selection_sha256')==saved['payload_sha256'],
                        'Maintenance selection differs from original metered planning receipt')
        for row in pin['hits']:
            try:
                ref=row['source_ref'];current=self.resolve(ref['kind'],ref['id'])
            except ERRORS:
                raise SafetyError('Original maintenance evidence is unavailable; old selection retained') from None
            require(current==row,'Original maintenance evidence or note version changed')
        return pin

    def context(self,issue,repository,identity=None):
        planner = self.session.task_planner
        identity = identity or (planner.identity if planner else None)
        pin=self.verify_task(identity,issue) if identity else None
        if pin is None:return None
        paths=(*self.session.policy.allowed_paths,*self.session.provider.inner.readonly_paths)
        files=[{'path':p,'content':repository.read(p).decode('utf-8'),'before_sha256':digest(repository.read(p)),
                'editable':p in self.session.policy.allowed_paths} for p in paths]
        body={'schema_version':1,'task_id':identity,'repository_sha256':self.repository,'issue_sha256':digest(issue.encode()),
              'planning_base_sha':repository.head,'planning_files_sha256':digest(json.dumps(files,ensure_ascii=False,separators=(',',':')).encode()),
              'selection_files_sha256':fingerprint(pin['source_files']),'selection_sha256':fingerprint(pin),
              'runtime_authorized':False,'hits':pin['hits']}
        return {**body,'context_sha256':fingerprint(body)}

    def checked_plan(self,data,issue):
        evidence=data.get('provider_evidence',{});identity=evidence.get('task_session',{}).get('task_id')
        pin=self.verify_task(identity,issue) if identity else None
        current=self.context(issue,RepositoryGateway(self.session.repo),identity) if pin else None
        require(evidence.get('maintenance_context_sha256')==(current['context_sha256'] if current else None),
                'Saved maintenance context receipt differs')


def monitor_projection(record,events):
    """Pure projection of the original monitor, never a cursor or diagnosis write."""
    spec=record['spec'];checks=[];issues=[]
    for event in events:
        tests=event.get('tests',[])
        require(re.fullmatch('[a-f0-9]{40}',event['sha']) and event['id']==digest(event['sha'].encode())[:16],
                'Monitor maintenance event identity changed')
        expected=[[sys.executable if v=='{python}' else v for v in command] for command in spec['allowed_tests']]
        complete=(event['status'] in {'healthy','ci_attention','diagnosing','awaiting_approval','stale'}
                  and [t.get('command') for t in tests]==expected and len(tests)==len(expected) and bool(tests) and all(type(t.get('returncode')) is int
                  and type(t.get('timed_out')) is bool and not t['timed_out'] for t in tests))
        row={'event_id':event['id'],'commit':event['sha'],'source_sha256':fingerprint({k:v for k,v in event.items()
             if k not in {'launch_url','can_open','runs','display_status'}}),'recorded_status':event['status'],
             'completed_local_checks':complete,'test_exits':[t.get('returncode') for t in tests],
             'runtime_authorized':False}
        if complete:checks.append(row)
        if event['status'] not in {'healthy'}:issues.append(row)
    packet={'schema_version':1,'observations':{'head_commit':record.get('latest_sha'),'observed_at':record.get('last_checked')},
            'cursors':{'completed_local_checks':checks,'scope_sha256':fingerprint(spec)},'issues':issues,
            'fixes':[r for r in issues if r['recorded_status']=='awaiting_approval'],'checks':checks,
            'completed_actions':[{'kind':'local_check','event_id':r['event_id'],'commit':r['commit']} for r in checks],
            'priorities':[r for r in issues if r['commit']==record.get('latest_sha')],'runtime_authorized':False}
    return {**packet,'snapshot_sha256':fingerprint(packet)}
