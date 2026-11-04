"""Original archives -> explicitly selected history -> fixed new target, never runtime restore."""
import contextlib
import copy
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import test_ui_backup as backup_fixture
from repo_maintainer import memory_migration as migration
from repo_maintainer.cli import main
from repo_maintainer.demo import create_fixture
from repo_maintainer.models import SafetyError,digest
from repo_maintainer.source_memory import CATALOG,build_memory,search_memory,sources
from repo_maintainer.task_session import fingerprint
from repo_maintainer.ui_backup import backup_ui_history,encoded
from repo_maintainer.ui_registry import UIRegistry


class MigrationTests(unittest.TestCase):
    def setUp(self):
        backup_fixture.UIBackupTests.setUp(self)
        self.target=create_fixture(self.root/'target')
        registry=UIRegistry(self.runs,self.binding)
        note={'note':{'title':'Addition negative operands','summary':'Keep operand signs; independently verify current tests.'},
              'classification':'proposed','source_sha256':'1'*64,'refs':[],'source_files':{},'policy_sha256':'2'*64,'parent':None}
        registry.put('repository_knowledge','1'*32,{'payload':note,'review':'accepted','lifecycle':'active'})
        registry.put('repository_experience','2'*32,{'payload':{'issue':'Addition negative operands','outcome':'test_failed'},'review':'accepted'})
        version={'title':'Follow addition','detail':'Check the new repository independently.','status':'open','priority':1,
                 'classification':'proposed','version':1,'previous':None,'previous_sha256':None}
        registry.put('maintenance_note_version','4'*32,version)
        registry.put('maintenance_note','3'*32,{'version_id':'4'*32,'version':1,'payload_sha256':fingerprint(version)})
        registry.close();self.original_db=self.db_path.read_bytes()
        self.backup=backup_ui_history(self.runs);self.backup_dir=self.runs/'ui-backups'/self.backup['backup_id']
        self.preview=migration.preview_migration('ui_backup',self.runs,self.backup['backup_id'])
        self.spec={'schema_version':1,'source':self.preview['source'],'items':[
            {k:row[k] for k in ('kind','id','item_sha256')}|{'name':'item'+str(n),'topic':'addition'}
            for n,row in enumerate(self.preview['items'])]}
        self.selection=self.root/'selection.json';self.selection.write_bytes(encoded(self.spec))
        self.corpus=self.root/'imported';self.index=self.root/'imported.memory.sqlite3'

    def migrate(self):return migration.migrate_memory(self.selection,self.corpus,self.target,self.index)

    def unchanged(self):self.assertEqual(self.original_db,self.db_path.read_bytes())

    def test_explicit_four_kinds_are_historical_redacted_and_no_authority_is_copied(self):
        self.assertEqual(migration.KINDS,{i['kind'] for i in self.preview['items']})
        result=self.migrate();self.assertEqual('historical_memory_migration_ready',result['status'])
        self.assertEqual('not_imported',result['budget']);self.assertFalse(result['runtime_authorized'])
        catalog=json.loads((self.corpus/CATALOG).read_bytes());self.assertEqual(4,catalog['schema_version'])
        _,rows=sources(self.corpus);self.assertTrue(all(r['classification']=='historical' and r['kind']=='case' for r in rows))
        self.assertFalse((self.corpus/'ui-state').exists());self.assertFalse((self.corpus/'task-sessions').exists())
        self.assertTrue(search_memory(self.corpus,self.target,self.index,'addition')['context']['hits'])
        material=json.loads((self.corpus/'materials.json').read_bytes())
        learned=next(r for r in material if r['selection']['kind']=='repository_knowledge')['material']
        self.assertEqual('accepted',learned['recorded_review']);self.assertFalse(learned['runtime_authorized'])
        self.assertEqual('archived_bytes_only_not_current_applicability',learned['verification']);self.unchanged()

    def test_same_selection_same_outputs_is_idempotent_and_different_selection_never_overwrites(self):
        self.migrate();before={p.name:p.read_bytes() for p in self.corpus.iterdir()};index=self.index.read_bytes()
        self.assertEqual('migration_already_ready',self.migrate()['status'])
        self.assertEqual(before,{p.name:p.read_bytes() for p in self.corpus.iterdir()});self.assertEqual(index,self.index.read_bytes())
        self.spec['items']=self.spec['items'][:1];self.selection.write_bytes(encoded(self.spec))
        with self.assertRaises(SafetyError):self.migrate()
        self.assertEqual(before,{p.name:p.read_bytes() for p in self.corpus.iterdir()});self.assertEqual(index,self.index.read_bytes());self.unchanged()

    def test_changed_archive_or_rehashed_material_cannot_silently_become_current_memory(self):
        self.migrate();report=self.backup_dir/'files'/self.run_id/'report.md';raw=report.read_bytes();report.write_bytes(raw+b'changed')
        with self.assertRaises(SafetyError):search_memory(self.corpus,self.target,self.index,'addition')
        report.write_bytes(raw);catalog=json.loads((self.corpus/CATALOG).read_bytes());path=self.corpus/catalog['entries'][0]['path']
        path.write_bytes(path.read_bytes()+b'approved now\n');catalog['migration_source']['files'][path.name]=digest(path.read_bytes())
        (self.corpus/CATALOG).write_bytes(encoded(catalog))
        with self.assertRaises(SafetyError):sources(self.corpus)
        self.unchanged()

    def test_target_and_original_archive_overlap_are_blocked_before_writes(self):
        self.migrate();other=create_fixture(self.root/'other')
        with self.assertRaises(SafetyError):search_memory(self.corpus,other,self.index,'addition')
        new_index=self.root/'other.memory.sqlite3'
        with self.assertRaises(SafetyError):build_memory(self.corpus,other,new_index)
        self.assertFalse(new_index.exists())
        output=self.runs/'bad-output'
        with self.assertRaises(SafetyError):migration.migrate_memory(self.selection,output,self.target,self.root/'bad.memory.sqlite3')
        self.assertFalse(output.exists());self.unchanged()

    def test_partial_export_is_retained_and_does_not_mutate_the_original(self):
        original=migration.write;counter=0
        def fail(path,content):
            nonlocal counter
            counter+=1
            if counter==3:raise SafetyError('injected write failure')
            return original(path,content)
        with patch.object(migration,'write',side_effect=fail):
            with self.assertRaises(SafetyError):self.migrate()
        self.assertTrue((self.corpus/'intent.json').is_file());self.assertFalse((self.corpus/CATALOG).exists())
        before={p.name:p.read_bytes() for p in self.corpus.iterdir()}
        with self.assertRaises(SafetyError):self.migrate()
        self.assertEqual(before,{p.name:p.read_bytes() for p in self.corpus.iterdir()});self.unchanged()

    def test_long_unicode_material_is_split_without_dropping_the_full_projection(self):
        # Archived raw claims need not satisfy the current knowledge schema; they stay historical.
        self.spec['items']=[next(i for i in self.spec['items'] if i['kind']=='repository_knowledge')]
        registry=UIRegistry(self.runs,self.binding);entry=registry.load('repository_knowledge')['1'*32]
        text='符号与负数验证。'*800;entry['payload']['note']['summary']=text
        registry.put('repository_knowledge','1'*32,entry);registry.close();self.original_db=self.db_path.read_bytes()
        backup=backup_ui_history(self.runs);preview=migration.preview_migration('ui_backup',self.runs,backup['backup_id'])
        row=next(r for r in preview['items'] if r['kind']=='repository_knowledge')
        self.spec['source']=preview['source'];self.spec['items'][0]['item_sha256']=row['item_sha256'];self.selection.write_bytes(encoded(self.spec))
        self.migrate();_,rows=sources(self.corpus);self.assertGreater(len(rows),1)
        self.assertTrue(all(len(r['excerpt'].encode())<=6000 and r['end_line']<=100 for r in rows))
        material=json.loads((self.corpus/'materials.json').read_bytes());self.assertEqual(text,material[0]['material']['data']['note']['summary'])
        self.unchanged()

    def test_recognized_credentials_are_redacted_in_export_but_original_archive_is_preserved(self):
        fake='sk-'+('syntheticfixture'*3)
        registry=UIRegistry(self.runs,self.binding);entry=registry.load('repository_knowledge')['1'*32]
        capability='f0'*32
        entry['payload']['note']['summary']='Historical log: Bearer syntheticcredential123 '+fake+' http://127.0.0.1:1/#session='+capability
        registry.put('repository_knowledge','1'*32,entry);registry.close();self.original_db=self.db_path.read_bytes()
        receipt=backup_ui_history(self.runs);preview=migration.preview_migration('ui_backup',self.runs,receipt['backup_id'])
        archived=self.runs/'ui-backups'/receipt['backup_id']/'snapshot.json';archived_bytes=archived.read_bytes()
        self.assertIn(capability,archived_bytes.decode('utf-8'))
        row=next(r for r in preview['items'] if r['kind']=='repository_knowledge')
        spec={'schema_version':1,'source':preview['source'],'items':[{k:row[k] for k in ('kind','id','item_sha256')}|{'name':'redacted','topic':'addition'}]}
        self.selection.write_bytes(encoded(spec));self.migrate()
        output=(self.corpus/'materials.json').read_text(encoding='utf-8');self.assertNotIn(fake,output)
        self.assertNotIn('syntheticcredential123',output);self.assertIn('[REDACTED]',output)
        self.assertNotIn(capability,output)
        self.assertEqual(archived_bytes,archived.read_bytes())
        self.unchanged()

    def test_old_markdown_classifications_and_fts_survive_explicit_reindex_for_the_target(self):
        from repo_maintainer.memory_collection import compose_memory
        original_repo=create_fixture(self.root/'old-doc-target');docs=self.root/'old-documents';docs.mkdir()
        rows=[]
        for name,label,marker in [('rule','confirmed','确认'),('suggestion','proposed','提议')]:
            (docs/(name+'.md')).write_text('状态：'+marker+'\naddition '+name+' reference.\n',encoding='utf-8')
            rows.append({'id':name,'topic':name,'path':name+'.md','start_line':1,'end_line':2,'kind':'document','classification':label})
        (docs/CATALOG).write_bytes(encoded({'schema_version':1,'entries':rows}));old_index=self.root/'old.memory.sqlite3'
        build_memory(docs,original_repo,old_index);before={p.name:p.read_bytes() for p in docs.iterdir()};old=old_index.read_bytes()
        spec={'schema_version':1,'members':[{'id':'old','source_root':str(docs)}],
              'selections':[{'id':r['id'],'member_id':'old','entry_id':r['id']} for r in rows]}
        selection=self.root/'compose.json';selection.write_bytes(encoded(spec));root=self.root/'copied';index=self.root/'copied.memory.sqlite3'
        compose_memory(selection,root,self.target,index)
        hits=search_memory(root,self.target,index,'addition')['context']['hits']
        self.assertEqual({'confirmed','proposed'},{h['classification'] for h in hits})
        self.assertEqual(before,{p.name:p.read_bytes() for p in docs.iterdir()});self.assertEqual(old,old_index.read_bytes())
        (docs/'rule.md').write_bytes((docs/'rule.md').read_bytes()+b'new source revision\n')
        with self.assertRaises(SafetyError):search_memory(root,self.target,index,'addition')
        self.assertEqual(old,old_index.read_bytes());self.unchanged()

    def test_collection_can_compose_migrated_history_but_cannot_write_into_its_archive(self):
        from repo_maintainer.memory_collection import compose_memory
        self.migrate();entry=json.loads((self.corpus/CATALOG).read_bytes())['entries'][0]
        spec={'schema_version':1,'members':[{'id':'old','source_root':str(self.corpus)}],
              'selections':[{'id':'history','member_id':'old','entry_id':entry['id']}]}
        selection=self.root/'compose.json';selection.write_bytes(encoded(spec));root=self.root/'composed';index=self.root/'composed.memory.sqlite3'
        compose_memory(selection,root,self.target,index)
        context=search_memory(root,self.target,index,'historical')['context'];self.assertEqual(2,context['schema_version'])
        self.assertEqual('historical',context['hits'][0]['classification']);self.assertEqual('catalog',context['hits'][0]['origin']['kind'])
        bad=self.runs/'bad.memory.sqlite3'
        with self.assertRaises(SafetyError):build_memory(root,self.target,bad)
        self.assertFalse(bad.exists());self.unchanged()

    def test_legacy_report_without_native_or_task_becomes_history_and_never_a_runnable_task(self):
        from repo_maintainer.legacy_archive import import_legacy_run
        source=self.root/('5'*32);source.mkdir();(source/'report.md').write_text('# Legacy addition\n\nNo original task or budget record exists.\n')
        archive=self.root/'legacy';receipt=import_legacy_run(source,archive)
        preview=migration.preview_migration('legacy_archive',archive,receipt['archive_id']);row=preview['items'][0]
        spec={'schema_version':1,'source':preview['source'],'items':[{k:row[k] for k in ('kind','id','item_sha256')}|{'name':'legacy','topic':'addition'}]}
        self.selection.write_bytes(encoded(spec));self.migrate()
        self.assertTrue(search_memory(self.corpus,self.target,self.index,'addition')['context']['hits'])
        self.assertFalse((self.corpus/'task-sessions').exists());self.unchanged()

    def test_cli_preview_and_selected_export_do_not_restore_original_unknown_budget(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(0,main(['memory-migration-preview','--source-kind','ui_backup','--archive-root',str(self.runs),'--archive-id',self.backup['backup_id']]))
        self.assertEqual(self.preview,json.loads(output.getvalue()))
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(0,main(['memory-migrate','--selection',str(self.selection),'--source-root',str(self.corpus),'--repo',str(self.target),'--index',str(self.index)]))
        self.assertEqual('not_imported',json.loads(output.getvalue())['budget']);self.unchanged()

    def test_invalid_selections_and_receipt_changes_fail_before_creating_outputs(self):
        for mutate in [lambda s:s['items'][0].update(name='../bad'),lambda s:s['items'].append(copy.deepcopy(s['items'][0])),
                       lambda s:s['source'].update(manifest_sha256='0'*64),lambda s:s['items'][0].update(kind='pi_task')]:
            spec=copy.deepcopy(self.spec);mutate(spec);self.selection.write_bytes(encoded(spec))
            with self.assertRaises(SafetyError):self.migrate()
            self.assertFalse(self.corpus.exists());self.assertFalse(self.index.exists())
        self.unchanged()

    def test_malformed_or_unknown_migration_catalog_is_a_safety_error_and_never_writes(self):
        self.migrate();file=self.corpus/CATALOG;original=file.read_bytes();index=self.index.read_bytes()
        cases=[b'{"schema_version":4}',encoded({'schema_version':4,'entries':[],'migration_source':None}),original+b'{}']
        for raw in cases:
            file.write_bytes(raw)
            try:
                with self.assertRaises(SafetyError):search_memory(self.corpus,self.target,self.index,'addition')
            finally:file.write_bytes(original)
            self.assertEqual(index,self.index.read_bytes())
        self.unchanged()
