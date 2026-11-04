"""Rebuild W0 metadata selection from separately downloaded, pinned official files.

Uses the existing evaluation environment's pyarrow only for Parquet reading; it is
not a RepoCare runtime dependency. No network, container or model calls are made.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import tomllib

from opc_w0_support import SEED, audit_selection, fingerprint, patch_statistics, public_prompt, select_live

PRO_REVISION = '66f92766bba642462d4bbe5479e83f91f9211862'
LIVE_CODE_REVISION = '62d02936a0726fbf4597b05bd07b09fb99eddae6'
LICENSE_NAMES = {
    'Pyomo/pyomo': 'BSD-3-Clause', 'slackapi/python-slack-sdk': 'MIT',
    'sooperset/mcp-atlassian': 'MIT', 'beeware/briefcase': 'BSD-3-Clause',
    'playcanvas/engine': 'MIT', 'aralroca/next-translate': 'MIT',
    'ChainSafe/lodestar': 'Apache-2.0', 'recharts/recharts': 'MIT',
}


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def build(root, destination, verify=False):
    import pyarrow.parquet as pq
    root, destination = Path(root).resolve(), Path(destination).resolve()
    if not verify and (destination.exists() or (root / 'agent-inputs-v1.json').exists()):
        raise ValueError('Selection/export exists; verify it instead of overwriting')
    licenses = {r['repo']: r for r in load(root / 'license-results.json')}
    tasks, packets, source_pins = [], {}, {}
    for lang, count, development_count in [('python', 4, 2), ('js', 2, 1), ('ts', 2, 1)]:
        pin = load(root / (lang + '-source-pin.json'))
        raw = (root / (lang + '.parquet')).read_bytes()
        if hashlib.sha256(raw).hexdigest() != pin['sha256']:
            raise ValueError('Official dataset changed')
        source_pins[lang] = pin
        rows = pq.read_table(root / (lang + '.parquet')).to_pylist()
        # SDV's fixed-base LICENSE is BSL 1.1, outside the first-batch license filter.
        chosen = select_live(rows, count, excluded_repositories=['sdv-dev/SDV'])
        for index, row in enumerate(chosen):
            license_row = licenses[row['repo']]
            if hashlib.sha256(Path(license_row['file']).read_bytes()).hexdigest() != license_row['sha256']:
                raise ValueError('Source license changed')
            packet = public_prompt(row)
            packets[row['instance_id']] = packet
            tasks.append({
                'family': 'live', 'role': 'development' if index < development_count else 'holdout',
                'language': lang, 'instance_id': row['instance_id'], 'repository': row['repo'],
                'base_commit': row['base_commit'], 'source_revision': pin['revision'],
                'dataset': pin['dataset'], 'dataset_file': pin['path'],
                'created_at': str(row['created_at']),
                'repository_license': LICENSE_NAMES[row['repo']],
                'license_url': license_row['url'], 'license_sha256': license_row['sha256'],
                'public_prompt_sha256': fingerprint(packet),
                'difficulty_metadata': patch_statistics(row['patch']),
                'docker_image': row.get('docker_image'),
                'environment_verified': False, 'agent_rollouts': 0,
                'image_digest': None,
                'remaining_gates': ['image_and_base_sha', 'resource_and_capacity',
                                    'empty_patch_red', 'reference_patch_green', 'no_solution_exposure'],
            })
    hard_ids = (root / 'pro-hard51.txt').read_text(encoding='utf-8').splitlines()
    for repo, language in [('ansible__ansible', 'python'), ('NodeBB__NodeBB', 'js')]:
        chosen = min((v for v in hard_ids if v.startswith('instance_' + repo + '-')),
                     key=lambda v: hashlib.sha256((SEED + v).encode()).hexdigest())
        folder = root / 'pro' / chosen
        text = (folder / 'instruction.md').read_bytes().decode('utf-8')
        config = tomllib.loads((folder / 'task.toml').read_text(encoding='utf-8'))
        packet = public_prompt({'instruction': text}, 'pro')
        packets[chosen] = packet
        tasks.append({'family': 'pro', 'role': 'challenge', 'language': language,
                      'instance_id': chosen, 'repository': repo.replace('__', '/'),
                      'base_commit': None, 'base_commit_status': 'must_read_actual_official_image',
                      'source_revision': PRO_REVISION, 'dataset': 'scaleapi/SWE-bench_Pro-os/v2',
                      'dataset_file': 'v2/tasks/' + chosen,
                      'repository_license': 'pending_fixed_image_checkout',
                      'public_prompt_sha256': fingerprint(packet),
                      'docker_image': config['environment']['docker_image'],
                      'official_resources': {k: config['environment'][k]
                                             for k in ['cpus', 'memory_mb', 'storage_mb']},
                      'official_agent_timeout_seconds': config['agent']['timeout_sec'],
                      'environment_verified': False, 'agent_rollouts': 0, 'image_digest': None,
                      'remaining_gates': ['fixed_base_and_license', 'image_digest', 'resource_and_capacity',
                                          'empty_patch_red', 'reference_patch_green', 'no_solution_exposure']})
    selection = {
        'schema_version': 1, 'status': 'metadata_frozen_environment_pending',
        'paid_authorized': False, 'seed': SEED, 'top_k': 5,
        'sampling': 'python=4,js=2,ts=2; one repo per language; stable SHA256 order; no agent outcomes',
        'eligibility': {'implementation_files_min': 2, 'changed_lines': [15,600],
                        'issue_characters': [100,16000], 'requires_fail_to_pass': True,
                        'requires_pass_to_pass': True},
        'exclusions': [{'instance_id': 'sdv-dev__SDV-2570', 'repository': 'sdv-dev/SDV',
                        'reason': 'fixed-base BSL-1.1 not in first-batch permissive-license filter',
                        'license_sha256': licenses['sdv-dev/SDV']['sha256']}],
        'source_pins': source_pins, 'live_code_revision': LIVE_CODE_REVISION,
        'tasks': tasks,
        'protocol': {
            'official_live_agent_fields': ['problem_statement'],
            'official_pro_agent_fields': ['instruction'],
            'hidden_grader_feedback_to_agent': False,
            'product_trigger_or_input_changes_are_adapted_not_official_scores': True,
            'old_ledgers_or_remaining_budgets_reused': False,
            'pro_50min_is_upstream_not_a_change_to_our_30min_setting': True,
        },
        'limitations': [
            'Public tasks may already be present in model training data; no contamination-free claim.',
            'Holdout means no RepoCare tuning on those tasks, not unseen by model pretraining.',
            'Fixed June-2025 Python shard is a bounded selection pool, not a latest-task claim.',
            'Multi-file reference edits are a selection proxy, not proof all those edits are required.',
            'No official image or empty/reference test control has run in this W0 preparation.',
            'Current repository/file-size/snapshot caps may block selected tasks; count capacity failures.',
            'No baseline, model strength or production-autonomy result is inferred from metadata.',
        ],
    }
    audit_selection(selection)
    if verify:
        if load(destination) != selection or load(root / 'agent-inputs-v1.json') != packets:
            raise ValueError('Frozen selection or public projection differs from pinned sources')
        return selection
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(selection, ensure_ascii=False, indent=2)+'\n', encoding='utf-8', newline='\n')
    # Authoring repository and judge data are never mounted in a target agent's workspace.
    (root / 'agent-inputs-v1.json').write_text(json.dumps(packets, ensure_ascii=False, indent=2)+'\n', encoding='utf-8', newline='\n')
    return selection


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--verify', action='store_true', help='Recompute and compare existing files without writing')
    args = parser.parse_args()
    selection = build(args.source_root, args.output, verify=args.verify)
    print(json.dumps(audit_selection(selection)))
