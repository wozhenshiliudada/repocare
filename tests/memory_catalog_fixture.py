"""Synthetic adapter-contract data, not evidence of extraction or model quality."""
from repo_maintainer.models import digest
from repo_maintainer.task_session import fingerprint


def fixture():
    task='a'*32;run='b'*32;knowledge='c'*32
    files=[{'path':'calculator.py','content':'def add(a, b):\n    return a - b\n','before_sha256':digest(b'def add(a, b):\n    return a - b\n'),'editable':True}]
    hashes={f['path']:f['before_sha256'] for f in files};base='d'*40;repo='e'*64
    frame={'repository_sha256':repo,'policy_sha256':'f'*64,'source_binding_sha256':'1'*64,'task_type':'bug_fix','invocation_id':'2'*32}
    def document(identity):
        return {'id':identity,'topic':'add','path':'notes/加法.md','start_line':1,'end_line':3,'kind':'document','classification':'confirmed',
                'classification_origin':'source_marker','source_sha256':'3'*64,'excerpt_sha256':'4'*64,'excerpt':'Synthetic declared rule'}
    newer,older=document('rule-new'),document('rule-old')
    relation={'id':'claim','kind':'explicit_supersedes_claim','newer':{k:v for k,v in newer.items() if k not in {'excerpt','excerpt_sha256'}},
              'older':{k:v for k,v in older.items() if k not in {'excerpt','excerpt_sha256'}},'statement_line':2,
              'statement_sha256':digest(b'Supersedes: rule-new -> rule-old')}
    source={'schema_version':3,'source_schema_version':1,'index_sha256':'5'*64,'query_sha256':digest(b'add'),'hits':[newer,older],
            'decision_relations':{'schema_version':1,'manifest_sha256':'6'*64,'claims_only':True,'runtime_authorized':False,'relations':[relation]}}
    episode={'schema_version':2,'repository_sha256':repo,'run_id':run,'task_id':'7'*32,'classification':'historical','runtime_authorized':False,
             'issue':'addition with signed operands','base_sha':base,'recorded_status':'discarded','outcome':'test_failed',
             'rounds':[{'number':1,'plan_sha256':'8'*64,'diagnosis':'Signed operands fail','verification':'failed','paths':['calculator.py'],
                        'test_exits':[1],'test_timeouts':[False],'change_excerpt':'return abs(a)+abs(b)','failure_excerpt':'negative operand assertion'}],
             'policy_sha256':frame['policy_sha256'],'source_files':hashes,'evidence':{'run.json':'9'*64,'repair-execution.json':'a'*64,'report.md':'b'*64}}
    experience={'hits':[{'id':run,'payload_sha256':fingerprint(episode),'review_version':1,'episode':episode,'applicability':'matching_original_source'}]}
    note={'kind':'procedure','title':'Add signed operands','summary':'Check original negative operand assertions.',
          'triggers':['addition failure'],'preconditions':['Read current source'],'steps':['Preserve signs'],
          'verification':['Use authorized tests'],'stop_conditions':['Stop if source changed']}
    payload={'schema_version':1,'repository_sha256':repo,'classification':'proposed','runtime_authorized':False,'note':note,
             'source_sha256':'c'*64,'refs':[{'id':run,'payload_sha256':fingerprint(episode),'review_version':1}],
             'source_files':hashes,'policy_sha256':frame['policy_sha256'],'parent':None}
    learned={'hits':[{'id':knowledge,'payload_sha256':fingerprint(payload),'review_version':1,'knowledge':payload,'applicability':'matching_original_source'}]}
    body={'kind':'note','title':'Add signed operands','detail':'Keep the original tests.','stage':'open','proof':'user_declaration',
          'task_id':None,'run_id':None,'plan_id':None,'rounds':None,'test_exits':[],'priority':2}
    maintenance={'hits':[{'id':'note:'+'d'*32,'body':body,'body_sha256':fingerprint(body),
                         'source_ref':{'kind':'note','id':'d'*32,'sha256':'d'*64},'classification':'proposed'}]}
    node={'id':'add','kind':'function','name':'add','qualified_name':'calculator.add','path':'calculator.py','language':'python',
          'start_line':1,'end_line':2,'source_sha256':hashes['calculator.py']}
    structure={'hits':[node],'related_nodes':[],'edges':[{'source':'add','target':'add','kind':'calls'}],'graph_sha256':'e'*64}
    blocks={'source':source,'experience':experience,'maintenance':maintenance,'knowledge':learned,'structure':structure}
    for value in blocks.values():value['context_sha256']=fingerprint(value)
    return {'frame':frame,'issue':'add','base':base,'files':files,'blocks':blocks,'allowance':50,'task_id':task}
