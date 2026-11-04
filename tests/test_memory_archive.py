"""Archival compatibility for task/experience/knowledge metadata; never a restore gate."""
import json
import unittest
from unittest.mock import patch
import test_ui_backup as backup_fixture
from repo_maintainer import ui_backup
from repo_maintainer.models import SafetyError


class MemoryArchiveTests(unittest.TestCase):
    setUp=backup_fixture.UIBackupTests.setUp
    backup=backup_fixture.UIBackupTests.backup
    unchanged=backup_fixture.UIBackupTests.unchanged

    def native(self):
        identity='f'*32
        directory=self.runs/'task-sessions'/identity/'sessions';directory.mkdir(parents=True)
        # Deliberately forensic bytes: archiving must not call this a verified/resumable checkpoint.
        raw=b'{"incomplete historical native evidence":true}\n'
        (directory/'fixture.jsonl').write_bytes(raw)
        (directory.parent/'workspace').mkdir()
        (directory.parent/'workspace'/'.env').write_text('never-archive-this-workspace')
        self.registry.put('pi_task',identity,{'task_id':identity,'checkpoint':{'file':'fixture.jsonl'}})
        self.registry.put('pi_task_link','run:'+self.run_id,{'task_id':identity})
        self.registry.put('experience_settings','default',{'schema_version':1,'enabled':True})
        self.registry.put('maintenance_settings','default',{'schema_version':1,'enabled':True})
        for kind in ('repository_experience','repository_knowledge','experience_review','knowledge_review','knowledge_task',
                     'maintenance_note','maintenance_note_version','maintenance_task'):
            self.registry.put(kind,identity,{'fixture_metadata':'historical; no authority'})
        self.original_db=self.db_path.read_bytes()
        return identity,directory,raw

    def test_archive_preserves_registered_native_and_memory_but_no_workspace_or_authority(self):
        identity,directory,raw=self.native();result=self.backup();self.unchanged()
        self.assertEqual(1,result['native_session_files']);self.assertTrue(result['native_history_is_archival_only'])
        self.assertFalse(result['runtime_authorized']);self.assertEqual((2,4),(result['requests_used'],result['requests_reserved_unknown']))
        self.assertEqual(raw,(self.backup_dir/'files'/'task-sessions'/identity/'sessions/fixture.jsonl').read_bytes())
        self.assertFalse((self.backup_dir/'files'/'task-sessions'/identity/'workspace').exists())
        data=json.loads((self.backup_dir/'snapshot.json').read_text())
        self.assertIn('repository_knowledge',[r['kind'] for r in data['objects']])
        self.assertIn('maintenance_note_version',[r['kind'] for r in data['objects']])
        self.assertEqual(result,ui_backup.verify_ui_backup(self.runs,result['backup_id']))

    def test_archived_native_corruption_or_original_change_is_detected(self):
        identity,directory,raw=self.native();result=self.backup()
        target=self.backup_dir/'files/task-sessions'/identity/'sessions/fixture.jsonl';target.write_bytes(raw+b' changed')
        with self.assertRaises(SafetyError):ui_backup.verify_ui_backup(self.runs,result['backup_id'])
        self.assertEqual(raw,(directory/'fixture.jsonl').read_bytes());self.unchanged()

    def test_unknown_metadata_and_path_escape_are_rejected_without_reading_external_files(self):
        identity,directory,raw=self.native()
        self.registry.put('pi_task',identity,{'task_id':identity,'checkpoint':{'file':'../../.env'}})
        with self.assertRaises(SafetyError):self.backup()
        self.registry.put('pi_task',identity,{'task_id':identity,'checkpoint':{'file':'fixture.jsonl'}})
        self.registry.put('unrecognized_memory_backend',identity,{'root':'../../private'})
        with self.assertRaises(SafetyError):self.backup()
        self.assertEqual(raw,(directory/'fixture.jsonl').read_bytes())

    def test_cooperative_native_writer_blocks_archive_and_preserves_unknown_consumption(self):
        identity,directory,raw=self.native()
        from filelock import FileLock
        with FileLock(str(directory.parent/'session.lock'),timeout=0):
            with self.assertRaises(SafetyError):self.backup()
        result=self.backup();self.assertEqual(4,result['requests_reserved_unknown']);self.unchanged()
