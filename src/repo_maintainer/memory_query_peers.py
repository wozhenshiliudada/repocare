"""Bounded same-topic comparisons for read-only search, never model selection.

Different classes/types are not proof of a semantic contradiction. Keep their
original facts visible when top-k hides peers, without choosing an effective rule.
"""
from copy import deepcopy
import json
from .task_session import fingerprint

MAX_BYTES = 32768
MAX_GROUPS = 10
MAX_PEERS = 8


def topic_peers(rows, hits):
    selected = {r['object_id'] for r in hits}
    topics = list(dict.fromkeys(r['topic'] for r in hits))
    by_topic = {topic: [r for r in rows if r['topic'] == topic] for topic in topics}
    candidates = [topic for topic in topics if len(by_topic[topic]) > 1
                  and any(r['object_id'] not in selected for r in by_topic[topic])]
    total_peers = sum(sum(r['object_id'] not in selected for r in by_topic[t]) for t in candidates)
    packet = {'schema_version': 1, 'groups': [], 'available_groups': len(candidates),
              'returned_groups': 0, 'omitted_groups': len(candidates), 'available_peers': total_peers,
              'returned_peers': 0, 'omitted_peers': total_peers, 'truncated': bool(candidates),
              'semantic_conflict_detection': False, 'selection_changed': False, 'runtime_authorized': False}

    def fits(value):
        # Leave room for final aggregate counters, without truncating a record.
        return len(json.dumps(value, ensure_ascii=False).encode()) <= MAX_BYTES - 256

    for topic in candidates:
        if len(packet['groups']) >= MAX_GROUPS: break
        members = by_topic[topic]
        shown_types = {r['adapter'] for r in members if r['object_id'] in selected}
        peers = sorted((r for r in members if r['object_id'] not in selected),
                       key=lambda r: (r['adapter'] in shown_types, r['object_id']))
        group = {'topic': topic, 'all_items_sha256': fingerprint(members), 'total_items': len(members),
                 'in_results': len(members) - len(peers), 'available_peers': len(peers),
                 'classifications': sorted({r['classification'] for r in members if r['classification'] is not None}),
                 'adapters': sorted({r['adapter'] for r in members}),
                 'cross_type': len({r['adapter'] for r in members}) > 1,
                 'mixed_decisions': any(r['topic_group']['mixed_decisions'] for r in members),
                 'peers': [], 'returned_peers': 0, 'omitted_peers': len(peers)}
        candidate = {**packet, 'groups': [*packet['groups'], group]}
        if not fits(candidate): continue
        for row in peers:
            if len(group['peers']) == MAX_PEERS: break
            value = deepcopy(row)
            value['in_context'] = value['runtime_authorized'] = False
            proposed = {**group, 'peers': [*group['peers'], value]}
            if not fits({**packet, 'groups': [*packet['groups'], proposed]}): continue
            group = proposed
        group.update(returned_peers=len(group['peers']), omitted_peers=len(peers) - len(group['peers']))
        packet['groups'].append(group)
    packet['returned_groups'] = len(packet['groups'])
    packet['omitted_groups'] -= packet['returned_groups']
    packet['returned_peers'] = sum(g['returned_peers'] for g in packet['groups'])
    packet['omitted_peers'] -= packet['returned_peers']
    packet['truncated'] = bool(packet['omitted_groups'] or packet['omitted_peers'])
    return packet
