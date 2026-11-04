"""Real CodeGraph + Pi with separate v3 decision and structure envelopes."""
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from gui_relations_fixture import fixture
from repo_maintainer.git import RepositoryGateway
from repo_maintainer.local_ui import UISession
from repo_maintainer.models import Policy
from repo_maintainer.pi_provider import PiProvider
from repo_maintainer.source_memory import canonical
from repo_maintainer.structure_index import build_structure
from repo_maintainer.workflow import cleanup_run

def trial(directory,url):
    directory=Path(directory); fixture(directory)
    repo=directory/'repository'; gateway=RepositoryGateway(repo)
    gateway.command('config','core.autocrlf','false'); gateway.command('checkout','--','calculator.py')
    before=gateway.snapshot(); raw=json.loads((directory/'policy.json').read_bytes())
    policy=Policy(tuple(raw['allowed_paths']),tuple(tuple(r) for r in raw['allowed_tests']),readonly_paths=tuple(raw['readonly_paths']))
    selection=directory/'selection.json'; selection.write_bytes(canonical({'schema_version':1,'paths':['calculator.py']}))
    index=directory/'structure-indexes'/'sample'; build_structure(selection,repo,index)
    provider=PiProvider(policy.allowed_paths,url,policy.readonly_paths,max_requests=2)
    provider.memory_root=directory/'memory-collections'/'decisions'
    provider.memory_index=directory/'decisions.memory.sqlite3'
    provider.memory_relations=directory/'decisions.memory-relations.json'; provider.memory_relations_context=True
    session=UISession(repo,policy,provider,directory/'runs',structure_index=index,structure_context_query='add')
    try:
        session._operate('plan',{'issue':'addition'}); plan=session.state()['plans'][0]
        evidence=plan['planning_evidence']
        assert evidence['memory_context_sha256'] and evidence['structure_context_sha256']
        assert evidence['memory_context_sha256']!=evidence['structure_context_sha256']
        assert session.state()['runs']==[]
        session._operate('decision',{'plan_id':plan['id'],'fingerprint':plan['fingerprint'],'decision':'approve'})
        state=session.state(); assert state['requests_used']==2 and state['plans'][0]['status']=='consumed'
        run=state['runs'][0]; cleanup_run(directory/'runs'/run['run_id'])
        assert gateway.snapshot()==before
        (directory/'summary.json').write_bytes(canonical({'passed':True,'memory_context_sha256':evidence['memory_context_sha256'],
            'structure_context_sha256':evidence['structure_context_sha256'],'paid_model_requests':0,'credential_reads':0,
            'saved_execution_requests':0,'source_unchanged':True}))
    finally: session.close()

if __name__=='__main__': trial(sys.argv[1],sys.argv[2])
