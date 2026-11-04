"""Read-only peer visibility must not change ranking, authority or old pins."""
from copy import deepcopy
import json
import unittest

from memory_catalog_fixture import fixture
from repo_maintainer.memory_items import project_contexts, ADAPTERS
from repo_maintainer.memory_query_peers import topic_peers, MAX_BYTES, MAX_GROUPS, MAX_PEERS


class PeerTests(unittest.TestCase):
    def rows(self):
        data=fixture(); rows=project_contexts(data['frame']['repository_sha256'],
                    {f['path']:f['before_sha256'] for f in data['files']},data['blocks'])
        for row in rows:
            if row['adapter'] in (ADAPTERS[0],ADAPTERS[2],ADAPTERS[5]):row['topic']='same topic'
        return rows

    def test_top_one_keeps_hidden_cross_type_peers_classification_review_and_provenance(self):
        rows=self.rows();hit=next(r for r in rows if r['adapter']==ADAPTERS[0]);before=deepcopy(rows)
        result=topic_peers(rows,[hit]);group=result['groups'][0]
        self.assertEqual(['confirmed','proposed'],group['classifications']);self.assertTrue(group['cross_type'])
        learned=next(p for p in group['peers'] if p['adapter']==ADAPTERS[5])
        self.assertEqual(('proposed','accepted','valid'),tuple(learned[k] for k in ('classification','review_state','freshness')))
        self.assertTrue(learned['source_refs']);self.assertFalse(learned['in_context']);self.assertFalse(learned['runtime_authorized'])
        self.assertEqual(before,rows);self.assertFalse(result['selection_changed']);self.assertFalse(result['semantic_conflict_detection'])
        self.assertFalse(group['mixed_decisions'],'Cross-type classes alone do not prove competing decisions')

    def test_bounds_explicitly_count_omissions_instead_of_truncating_a_peer(self):
        rows=self.rows();base=next(r for r in rows if r['adapter']==ADAPTERS[0]);rows=[base]
        for index in range(20):
            item=deepcopy(base);item.update(object_id='peer-'+str(index),adapter=ADAPTERS[5]);rows.append(item)
        result=topic_peers(rows,[base]);group=result['groups'][0]
        self.assertEqual(20,group['available_peers']);self.assertEqual(MAX_PEERS,group['returned_peers'])
        self.assertEqual(20-MAX_PEERS,group['omitted_peers']);self.assertTrue(result['truncated'])
        huge=deepcopy(rows[1]);huge['object_id']='huge';huge['summary']={'text':'大'*MAX_BYTES}
        result=topic_peers([base,huge],[base]);self.assertEqual([],result['groups'][0]['peers'])
        self.assertEqual(1,result['omitted_peers']);self.assertLessEqual(len(json.dumps(result,ensure_ascii=False).encode()),MAX_BYTES)

    def test_group_limit_and_full_snapshot_hash_do_not_hide_unshown_group_counts(self):
        rows=[];hits=[]
        for index in range(MAX_GROUPS+3):
            first=deepcopy(self.rows()[0]);first.update(topic='topic-'+str(index),object_id='shown-'+str(index))
            other=deepcopy(first);other['object_id']='other-'+str(index);rows.extend([first,other]);hits.append(first)
        result=topic_peers(rows,hits)
        self.assertEqual(MAX_GROUPS,result['returned_groups']);self.assertEqual(3,result['omitted_groups'])
        self.assertEqual(3,result['omitted_peers']);self.assertEqual(64,len(result['groups'][0]['all_items_sha256']))
        self.assertEqual(result,topic_peers(rows,hits))

    def test_unrelated_and_already_shown_items_do_not_appear_as_hidden_peers(self):
        rows=self.rows();group=[r for r in rows if r['topic']=='same topic']
        self.assertEqual([],topic_peers(rows,group)['groups']);self.assertEqual([],topic_peers(rows,[])['groups'])
        before=topic_peers(rows,[group[0]])
        other=next(r for r in rows if r['topic']!='same topic');other['summary']={'unrelated':'changed'}
        self.assertEqual(before,topic_peers(rows,[group[0]]))

    def test_mutating_returned_peer_cannot_change_original_context(self):
        rows=self.rows();before=deepcopy(rows);hit=next(r for r in rows if r['adapter']==ADAPTERS[0])
        result=topic_peers(rows,[hit]);result['groups'][0]['peers'][0]['summary'].clear()
        result['groups'][0]['peers'][0]['source_refs'].clear()
        self.assertEqual(before,rows)
