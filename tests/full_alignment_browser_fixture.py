"""Disposable local Pi-type sessions without invoking any model or real GitHub."""
import json
from pathlib import Path
import socket
import sys
import time
import uuid

import uvicorn

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from repo_maintainer.demo import create_fixture,DEMO_TEST
from repo_maintainer.git import RepositoryGateway
from repo_maintainer.models import Policy,digest
from repo_maintainer.pi_provider import PiProvider
from repo_maintainer.provider import MockProvider
from repo_maintainer.local_ui import UISession
from repo_maintainer.repository_workbench import RepositoryWorkbench,inspect_repository,create_workbench_app
from repo_maintainer.source_memory import build_memory
from repo_maintainer.legacy_archive import import_legacy_run
from repo_maintainer.memory_migration import preview_migration
from test_repository_workbench import FakeGitHub

root=Path(sys.argv[1]).resolve()
if root.exists():raise RuntimeError('New disposable fixture directory required')
root.mkdir(parents=True)
repo=create_fixture(root/'calculator-demo')
fresh=create_fixture(root/'new-source-demo')
for index in range(230):
    path=repo/'docs'/f'page{index:04}.md';path.parent.mkdir(exist_ok=True);path.write_text('Synthetic tracked page\n')
(repo/'docs/large-readable.txt').write_text('x'*120000)
gateway=RepositoryGateway(repo);gateway.command('add','docs')
gateway.command('-c','user.name=Disposable fixture','-c','user.email=fixture@example.invalid','commit','-m','Synthetic paginated files')
docs=root/'documents';docs.mkdir()
entries=[]
for name,body,status in [('old','状态：确认\nOld approval rule.\n','confirmed'),
                         ('new','状态：提议\nNew proposed alternative.\n\nSupersedes: new -> old\n','proposed')]:
    (docs/(name+'.md')).write_bytes(body.encode())
    entries.append({'id':name,'topic':'policy','path':name+'.md','start_line':1,'end_line':len(body.splitlines()),'kind':'document','classification':status})
(docs/'memory-catalog.json').write_text(json.dumps({'schema_version':1,'entries':entries}),encoding='utf-8')
index=root/'documents.memory.sqlite3';build_memory(docs,repo,index)
fresh_index=root/'fresh.memory.sqlite3';build_memory(docs,fresh,fresh_index)
relations=root/'relations.json'
relations.write_text(json.dumps({'schema_version':1,'relations':[{'id':'alternative','newer_id':'new','older_id':'old','statement_line':4,
    'newer_sha256':digest((docs/'new.md').read_bytes()),'older_sha256':digest((docs/'old.md').read_bytes()),
    'statement_sha256':digest(b'Supersedes: new -> old')}]}))
selection=root/'compose.json';selection.write_text(json.dumps({'schema_version':1,'members':[{'id':'docs','source_root':str(docs)}],
    'selections':[{'id':'current','member_id':'docs','entry_id':'new'}]}))
run=root/'historical-runs'/('a'*32);run.mkdir(parents=True)
(run/'run.json').write_text(json.dumps({'schema_version':2,'run_id':run.name,'status':'discarded','mode':'approval','finished_at':'historical','source_repo':str(repo)}))
(run/'report.md').write_text('# Historical approval\nOld approvals do not authorize new work.\n',encoding='utf-8')
archive=root/'archive';archived=import_legacy_run(run,archive)
migration=preview_migration('legacy_archive',archive,archived['archive_id'])
migration_file=root/'migration.json'
migration_file.write_text(json.dumps({'schema_version':1,'source':migration['source'],
    'items':[{k:row[k] for k in ('kind','id','item_sha256')}|{'name':'history'+str(i),'topic':'policy'} for i,row in enumerate(migration['items'])]}))
offline_provider=MockProvider();offline_provider.max_requests=6
offline=UISession(fresh,Policy(('calculator.py',),(DEMO_TEST,),readonly_paths=('tests/test_calculator.py',)),offline_provider,root/'offline-runs')
offline.close()
bad_model=root/'uninstalled-model.json';bad_model.write_text('{"schema_version":1}')
fixture={'root':str(root),'repository':str(repo),'fresh_repository':str(fresh),'documents':str(docs),'index':str(index),
    'fresh_index':str(fresh_index),'relations':str(relations),'compose':str(selection),'run':str(run),'migration':str(migration_file),
    'offline_runs':str(root/'offline-runs'),'bad_model':str(bad_model)}
(root/'fixture.json').write_text(json.dumps(fixture,indent=2))
hub=None
with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as listener:
    listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
    try:
        hub=RepositoryWorkbench(root/'state',provider_factory=lambda policy:PiProvider(policy.allowed_paths,
            mock_url='http://127.0.0.1:1',readonly_paths=policy.readonly_paths,max_requests=6),github=FakeGitHub(),request_budget=6,ui='react')
        hub.port=port
        view=hub.provider_settings.view()
        hub.provider_settings.save({'revision':view['revision'],'base_url':'https://example.invalid/v1',
                                    'model':'synthetic-no-calls','api_key':'disposable-synthetic-key'})
        source=inspect_repository(repo)
        job=hub.submit(uuid.uuid4().hex,'connect',{'path':str(repo),'revision':source['revision'],'allowed_paths':['calculator.py'],
            'readonly_paths':['tests/test_calculator.py'],'allowed_tests':[list(DEMO_TEST)],'timeout_seconds':30,'allow_auto':False,'confirmed':True})
        while hub.jobs[job['id']]['status']=='running':time.sleep(.02)
        if hub.jobs[job['id']]['status']!='finished':raise RuntimeError(hub.jobs[job['id']]['result'])
        hub.url=f'http://127.0.0.1:{port}/#session={hub.token}'
        print(hub.url,flush=True)
        server=uvicorn.Server(uvicorn.Config(create_workbench_app(hub,port),access_log=False,proxy_headers=False,ws='none',log_level='warning'))
        hub.stop_callback=lambda:setattr(server,'should_exit',True)
        server.run(sockets=[listener])
    finally:
        if hub is not None:hub.close()
