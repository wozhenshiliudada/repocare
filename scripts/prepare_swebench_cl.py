"""Judge-side, zero-model preparation for a small SWE-Bench-CL pilot.

Never imports the upstream Agent, runs downloaded code, reads credentials, or
includes gold-derived fields in the allowlisted agent task projection.
"""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def check(condition, message):
    if not condition:
        raise ValueError(message)


def agent_task(row):
    metadata = row['metadata']
    check(re.fullmatch(r'[0-9a-f]{40}', metadata['base_commit']), 'Invalid base commit')
    # Allowlisting is intentional: dependencies/modified_files use the gold patch.
    return {key: metadata[key] for key in ('instance_id', 'repo', 'base_commit')} | {
        'problem_statement': row['task']['problem_statement']}


def tasks(dataset):
    result = {}
    for sequence in dataset['sequences']:
        for row in sequence['tasks']:
            identifier = row['metadata']['instance_id']
            check(identifier not in result, 'Duplicate task identity')
            check(sequence['repo'] == row['metadata']['repo'], 'Sequence repository mismatch')
            agent_task(row)
            result[identifier] = row
    return result


def validate_pair(history, target):
    h, t = history['metadata'], target['metadata']
    check(h['instance_id'] != t['instance_id'] and h['repo'] == t['repo'], 'Invalid task pair')
    start, end = datetime.fromisoformat(h['created_at']), datetime.fromisoformat(t['created_at'])
    check(start.tzinfo is not None and end.tzinfo is not None and start < end, 'History is not strictly earlier')
    check(all(0 < len(row['task']['problem_statement']) <= 2000 for row in (history, target)),
          'Original issue exceeds the existing UI limit; do not truncate it silently')


def prepare(dataset_path, official_path, output, history_id, target_id):
    check(not output.exists(), 'Preparation output exists; inspect it instead of overwriting')
    raw, official_raw = dataset_path.read_bytes(), official_path.read_bytes()
    dataset, official = json.loads(raw), json.loads(official_raw)
    indexed = tasks(dataset)
    history, target = indexed[history_id], indexed[target_id]
    validate_pair(history, target)
    official_index = {row['instance_id']: row for row in official}
    check(len(official_index) == len(official), 'Duplicate official task identity')
    judge, agents = [], []
    for row in (history, target):
        projection = agent_task(row)
        original = official_index[projection['instance_id']]
        check(all(original[key] == value for key, value in projection.items()), 'Official task differs from curriculum')
        check(all(original[key] == row['evaluation'][key] for key in ('patch', 'test_patch')),
              'Official judge patch differs from curriculum')
        check(all(isinstance(original[key], list) and original[key] for key in ('FAIL_TO_PASS', 'PASS_TO_PASS')),
              'Official test expectations missing')
        agents.append(projection)
        judge.append(original)
    output.mkdir(parents=True)
    files = {'agent/tasks.json': agents, 'judge/tasks.json': judge}
    hashes = {}
    for name, value in files.items():
        blob = (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode()
        path = output / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(blob)
        hashes[name] = sha(blob)
    result = {'schema_version': 1, 'kind': 'swebench-cl-pilot-preparation',
              'dataset_sha256': sha(raw), 'official_sha256': sha(official_raw),
              'files': hashes, 'history': history_id, 'target': target_id,
              'repository': history['metadata']['repo'],
              'history_created_at': history['metadata']['created_at'],
              'target_created_at': target['metadata']['created_at'],
              'paid_requests': 0, 'credentials_read': False,
              'conditions': ['A_existing_baseline', 'B_operator_selected_history', 'C_automatic_retrieval'],
              'limitations': ['One pair is a development probe, not an official score or proof of general benefit',
                              'Pair selection used judge-side file overlap and environment compatibility',
                              'Current pilot history pool contains only one real prior task',
                              'Upstream sequence positions are not globally chronological',
                              'No paid execution authorization is implied by preparation']}
    (output / 'preparation.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--curriculum', type=Path, required=True)
    parser.add_argument('--official', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--history', required=True)
    parser.add_argument('--target', required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.curriculum, args.official, args.output, args.history, args.target)))
