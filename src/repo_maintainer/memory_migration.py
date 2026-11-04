"""Explicit archive material transfer. Never restore a journal, approval or budget."""
from pathlib import Path
import re
from .git import RepositoryGateway
from .legacy_archive import NAMESPACE,archive_root,verify_legacy_run
from .models import SafetyError,digest
from .recovery import unlinked
from .store import now,redact
from .task_session import fingerprint,repository_identity,require
from .ui_backup import encoded,identity,json_value,read_bytes,verify_ui_backup,write

KINDS={'repository_knowledge','repository_experience','maintenance_note','run_report'}
NAME=re.compile(r'[a-z0-9][a-z0-9_-]{0,49}')


def exact(value,fields):return isinstance(value,dict) and set(value)==set(fields.split())


def redact_material(text):
    text=redact(text)
    return re.sub(r'(?i)([#?&](?:session|token|access_token)=)[A-Za-z0-9._~+/%=-]{16,512}',r'\1[REDACTED]',text)


def _report(directory,key,body):
    text=read_bytes(directory/'files'/key/'report.md',512*1024).decode('utf-8')
    return {'recorded_status':body.get('status'),'report':text}


def _archive(kind,root,key):
    root=unlinked(root);identity(key)
    require(isinstance(kind,str) and kind in {'ui_backup','legacy_archive'},'Choose a UI backup or legacy archive')
    if kind=='ui_backup':
        verify_ui_backup(root,key);directory=unlinked(root/'ui-backups'/key)
        snapshot_raw=read_bytes(directory/'snapshot.json',64*1024*1024);data=json_value(snapshot_raw)
        rows={(r['kind'],r['id']):r['body'] for r in data['objects']};snapshot_sha=digest(snapshot_raw)
    else:
        verify_legacy_run(root,key);directory=unlinked(root/NAMESPACE/key);snapshot_sha=None
        manifest=json_value(read_bytes(directory/'manifest.json'))
        rows={('run',manifest['run_id']):{'status':manifest['record']['recorded_status']}}
    descriptor={'kind':kind,'root':str(root),'id':key,'manifest_sha256':digest(read_bytes(directory/'manifest.json')),
                'snapshot_sha256':snapshot_sha}
    materials={};excluded=[]
    for (record_kind,record_id),body in rows.items():
        if record_kind not in {'repository_knowledge','repository_experience','maintenance_note','run'}:continue
        material_kind='run_report' if record_kind=='run' else record_kind
        try:
            identity(record_id)
            if record_kind=='repository_knowledge':
                payload=body['payload'];content={k:payload.get(k) for k in
                    ('note','classification','source_sha256','refs','source_files','policy_sha256','parent')}
                title=payload.get('note',{}).get('title','Archived knowledge')
            elif record_kind=='repository_experience':
                payload=body['payload'];content={k:payload.get(k) for k in
                    ('issue','classification','outcome','recorded_status','rounds','base_sha','source_files','policy_sha256','evidence')}
                title=payload.get('issue','Archived experience')
            elif record_kind=='maintenance_note':
                version=rows[('maintenance_note_version',body['version_id'])]
                require(fingerprint(version)==body['payload_sha256'],'Archived note head differs from retained version')
                content={k:version.get(k) for k in ('title','detail','status','priority','classification','version','previous','previous_sha256')}
                title=version.get('title','Archived maintenance declaration')
            else:
                content=_report(directory,record_id,body);title='Archived run report '+record_id[:8]
            value={'original_kind':material_kind,'original_id':record_id,'original_record_sha256':fingerprint(body),
                   'recorded_review':body.get('review'),'recorded_lifecycle':body.get('lifecycle'),
                   'verification':'archived_bytes_only_not_current_applicability','runtime_authorized':False,'data':content}
            # Redaction is part of the reproducible material projection, not a rewritten archive.
            value=json_value(redact_material(encoded(value).decode()).encode())
            require(len(encoded(value))<=512*1024,'Selected archived material exceeds bounded size')
            materials[(material_kind,record_id)]={'title':redact_material(str(title))[:160],'value':value,'sha256':fingerprint(value)}
        except (SafetyError,OSError,KeyError,ValueError,TypeError,AttributeError) as error:
            excluded.append({'kind':material_kind,'id':record_id,'reason':redact(str(error))[:300]})
    return descriptor,materials,excluded


def preview_migration(kind,root,key):
    source,materials,excluded=_archive(kind,root,key)
    return {'status':'archive_material_preview','source':source,
            'items':[{'kind':k,'id':i,'title':v['title'],'item_sha256':v['sha256']}
                     for (k,i),v in sorted(materials.items())],
            'excluded':excluded,'runtime_authorized':False,'extra_model_requests':0,
            'review_policy':'historical_reference_only_no_approval_or_budget_transfer'}


def _select(spec):
    require(exact(spec,'schema_version source items') and type(spec['schema_version']) is int and spec['schema_version']==1
            and isinstance(spec['items'],list) and 1<=len(spec['items'])<=20,'Select 1-20 explicit archive materials')
    source=spec['source']
    require(exact(source,'kind root id manifest_sha256 snapshot_sha256') and isinstance(source['root'],str)
            and Path(source['root']).is_absolute(),'Invalid selected archive descriptor')
    actual,materials,_=_archive(source['kind'],Path(source['root']),source['id'])
    require(source==actual,'Selected archive receipt changed')
    names=set();originals=set();selected=[]
    for item in spec['items']:
        require(exact(item,'kind id name topic item_sha256'),'Invalid material selection fields')
        identity(item['id'])
        require(isinstance(item['kind'],str) and item['kind'] in KINDS
                and all(isinstance(item[k],str) and NAME.fullmatch(item[k]) for k in ('name','topic'))
                and item['name'] not in names and (item['kind'],item['id']) not in originals,'Invalid or duplicate material selection')
        identity(item['id']);material=materials.get((item['kind'],item['id']))
        require(material and item['item_sha256']==material['sha256'],'Selected archived material changed or is unavailable')
        selected.append({'selection':item,'material':material['value']});names.add(item['name']);originals.add((item['kind'],item['id']))
    require(len(encoded(selected))<=512*1024,'Total selected materials exceed bounded size')
    return selected


def _parts(value):
    lines=[]
    for raw in encoded(value).decode().splitlines():
        current='';size=0
        for char in raw:
            length=len(char.encode())
            if size+length>1800:lines.append(current);current='';size=0
            current+=char;size+=length
        lines.append(current)
    parts=[];current=[];size=0
    for line in lines:
        length=len(line.encode())+1
        if current and (size+length>4800 or len(current)>=75):parts.append('\n'.join(current));current=[];size=0
        current.append(line);size+=length
    if current:parts.append('\n'.join(current))
    return parts


def _render(spec,selected):
    entries=[];files={}
    for value in selected:
        item=value['selection'];parts=_parts(value['material'])
        for number,part in enumerate(parts,1):
            name=item['name']+f'-p{number:02}';path=name+'.md'
            text=(f'# Imported historical reference {name}\n\n'
                  f'Archive SHA256: {spec["source"]["manifest_sha256"]}\n'
                  f'Original material: {item["kind"]}/{item["id"]}\n'
                  f'Material SHA256: {item["item_sha256"]}; part {number}/{len(parts)}\n'
                  'Historical reference only. Old review, approvals, tasks and budgets are not transferred.\n'
                  'Display wraps long lines; the full projection is in materials.json.\n\n```text\n'+part+'\n```\n')
            require(len(text.encode())<=6000 and len(text.splitlines())<=100,'Historical material chunk exceeds source protocol limits')
            files[path]=text.encode()
            entries.append({'id':name,'topic':item['topic'],'path':path,'start_line':1,'end_line':len(text.splitlines()),
                            'kind':'case','classification':'historical'})
    require(1<=len(entries)<=50,'Selected materials need more than 50 source entries; select fewer materials')
    return entries,files


def _target(repository):
    repo=RepositoryGateway(unlinked(repository))
    return repository_identity(repo.root,repo.common_dir)


def _binding(catalog):
    require(exact(catalog,'schema_version entries migration_source') and type(catalog['schema_version']) is int
            and catalog['schema_version']==4,'Invalid migrated source catalog')
    value=catalog['migration_source']
    require(exact(value,'selection_sha256 materials_sha256 files target_repository_sha256 intent_sha256')
            and all(isinstance(value[k],str) and re.fullmatch('[a-f0-9]{64}',value[k]) for k in
                    ('selection_sha256','materials_sha256','target_repository_sha256','intent_sha256'))
            and isinstance(value['files'],dict) and 1<=len(value['files'])<=50,'Invalid migration provenance fields')
    return value


def protect_migration_outputs(root,repository,index,catalog,*additional_outputs):
    binding=_binding(catalog);require(binding['target_repository_sha256']==_target(repository),'Migration target repository changed')
    spec=json_value(read_bytes(root/'selection.json',128*1024))
    require(exact(spec,'schema_version source items') and isinstance(spec['source'],dict)
            and isinstance(spec['source'].get('root'),str) and Path(spec['source']['root']).is_absolute(),'Invalid migration source scope')
    source=unlinked(Path(spec['source']['root']))
    for output in (root,index,*additional_outputs):
        require(not output.is_relative_to(source) and not source.is_relative_to(output),'Migration outputs overlap original archive/journal')


def verify_migrated_sources(root,catalog):
    binding=_binding(catalog)
    raw=read_bytes(root/'selection.json',128*1024);require(digest(raw)==binding['selection_sha256'],'Migration selection changed')
    spec=json_value(raw);selected=_select(spec);entries,files=_render(spec,selected)
    require(encoded(selected)==read_bytes(root/'materials.json',512*1024)
            and digest(encoded(selected))==binding['materials_sha256'] and entries==catalog['entries']
            and {name:digest(content) for name,content in files.items()}==binding['files'],'Migration material projection changed')
    for name,content in files.items():require(read_bytes(root/name,6000)==content,'Migrated reference text changed')
    intent_raw=read_bytes(root/'intent.json',128*1024);intent=json_value(intent_raw)
    require(exact(intent,'schema_version purpose runtime_authorized budget approvals native selection_sha256 target_repository_sha256 created_at')
            and type(intent['schema_version']) is int and intent['schema_version']==1 and intent['purpose']=='historical_memory_migration'
            and intent['runtime_authorized'] is False and intent['budget']=='not_imported' and intent['approvals']=='not_imported'
            and intent['native']=='not_imported' and intent['selection_sha256']==binding['selection_sha256']
            and intent['target_repository_sha256']==binding['target_repository_sha256']
            and isinstance(intent['created_at'],str) and 0<len(intent['created_at'])<=80
            and digest(intent_raw)==binding['intent_sha256'],'Migration intent differs')
    expected={'intent.json','selection.json','materials.json','memory-catalog.json',*files}
    require({p.name for p in root.iterdir()}==expected and all(unlinked(p).is_file() for p in root.iterdir()),
            'Unexpected migrated corpus file')
    require(_select(spec)==selected and read_bytes(root/'selection.json',128*1024)==raw,'Archive changed during material verification')
    return {'materials':len(selected),'entries':len(entries),'runtime_authorized':False}


def migrate_memory(selection_file,root,repository,index):
    from .source_memory import CATALOG,build_memory,location,search_memory
    raw=read_bytes(unlinked(selection_file),128*1024);spec=json_value(raw);selected=_select(spec);entries,files=_render(spec,selected)
    root=archive_root(root);root,repository,index=location(root,repository,index);target=_target(repository)
    source=unlinked(Path(spec['source']['root']))
    for output in (root,index):
        require(not output.is_relative_to(source) and not source.is_relative_to(output),'Migration outputs overlap original archive/journal')
        require(not unlinked(selection_file).is_relative_to(output),'Migration output overlaps selection input')
    require(not root.is_relative_to(repository) and not repository.is_relative_to(root) and not root.is_relative_to(index),
            'Select independent corpus, repository and index paths')
    if root.exists() or index.exists():
        require(root.is_dir() and index.is_file(),'Incomplete output retained; choose new paths or inspect the partial export')
        catalog=json_value(read_bytes(root/CATALOG,128*1024));verify_migrated_sources(root,catalog)
        require(catalog['migration_source']['selection_sha256']==digest(raw),'Existing output belongs to another selection')
        protect_migration_outputs(root,repository,index,catalog);search_memory(root,repository,index,'historical')
        return {'status':'migration_already_ready','runtime_authorized':False,'entries':len(entries),'extra_model_requests':0}
    root.mkdir(parents=True,exist_ok=False)
    intent={'schema_version':1,'purpose':'historical_memory_migration','runtime_authorized':False,'budget':'not_imported',
            'approvals':'not_imported','native':'not_imported','selection_sha256':digest(raw),'target_repository_sha256':target,'created_at':now()}
    write(root/'intent.json',encoded(intent));write(root/'selection.json',raw);write(root/'materials.json',encoded(selected))
    for name,content in files.items():write(root/name,content)
    require(_select(spec)==selected,'Source archive changed while exporting; partial corpus retained')
    catalog={'schema_version':4,'entries':entries,'migration_source':{'selection_sha256':digest(raw),
             'materials_sha256':digest(encoded(selected)),'files':{name:digest(content) for name,content in files.items()},
             'target_repository_sha256':target,'intent_sha256':digest(encoded(intent))}}
    write(root/CATALOG,encoded(catalog));built=build_memory(root,repository,index)
    return {**built,'status':'historical_memory_migration_ready','materials':len(selected),'source_root':str(root),'index':str(index),
            'budget':'not_imported','approvals':'not_imported','native':'not_imported','extra_model_requests':0}
