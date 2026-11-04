"""Unpaid local SSE preflight. Requires Pi/Node and normal project Python dependencies.

Does not require .env.mimo.local to exist and never reads credentials.
"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from repo_maintainer.memory_ablation import ScriptTransport, EvaluationLedger, require, write_json
from repo_maintainer.memory_long_ablation import SUMMARY
from memory_temporal_evaluation import PROJECT, specification, historical, measure


class TemporalTransport(ScriptTransport):
    proposal_round = 0

    def response_delta(self, body, slot):
        if not body.get('tools'):
            return {'role': 'assistant', 'content': SUMMARY}, 'stop'
        return super().response_delta(body, slot)

    def response(self, body):
        names = [t['function']['name'] for t in body['tools']]
        if names == ['propose_memory']:
            return 'propose_memory', {
                'kind': 'procedure', 'title': 'Negative and mixed operands in arithmetic',
                'summary': 'The historical candidate passed its authorized tests in isolation, then was discarded without merging.',
                'triggers': ['negative and mixed operands in arithmetic'],
                'preconditions': ['Inspect the current requirement and revalidate historical evidence.'],
                'steps': ['Read the current source and tests.', 'Check negative and mixed operands.'],
                'verification': ['Run the currently authorized tests.'],
                'stop_conditions': ['Stop on changed scope or unknown consumption.']}
        require(sorted(names) == ['propose_repair', 'read_source'], 'Unexpected fixture capabilities')
        users = [i for i, m in enumerate(body['messages']) if m['role'] == 'user']
        current = body['messages'][users[-1] + 1:]
        read = next((m for m in current if m['role'] == 'tool' and 'before_sha256' in m.get('content', '')), None)
        if read is None:
            return 'read_source', {'path': 'calculator.py'}
        source = json.loads(read['content'])['content']
        old = next(line.strip() for line in source.splitlines() if line.strip().startswith('return '))
        patch = 'return a + b' if self.phase == 'history' else ('return a + b', 'return b', 'return a * b')[self.proposal_round]
        if self.phase != 'history': self.proposal_round += 1
        return 'propose_repair', {'diagnosis': 'Fixed public synthetic temporal pilot', 'risk': 'Disposable fixture only',
                'edits': [{'path': 'calculator.py', 'replacements': [{'oldText': old, 'newText': patch}]}]}


def preflight(root):
    require(not root.exists(), 'Choose a new output directory; previous runs are retained')
    root.mkdir(parents=True)
    manifest = specification(PROJECT / '.env.mimo.local')
    write_json(root / 'manifest.json', manifest)
    ledger = EvaluationLedger(root / 'transport.sqlite3', 100)
    model = TemporalTransport(ledger); model.cell, model.phase = 'history', 'history'
    try:
        first = historical(root, manifest, model)
        # Only this fixed synthetic candidate has programmatic fixture review.
        result = measure(root, manifest, first['review_sha256'], model)
        require(result['completed'] and result['passed_arms'] == 5, 'Incomplete synthetic preflight')
        require(not model.errors and all(r['status'] == 'sent' for r in ledger.rows()), 'Incomplete synthetic transport')
        require(result['budget']['requests_used'] == len(ledger.rows()) and not result['budget']['requests_reserved_unknown'],
                'Original/HTTP request totals differ')
        value = {'passed': True, 'result': result, 'http_requests': len(ledger.rows()), 'paid_requests': 0,
                 'credential_read': False, 'model_benefit_claim': False}
        write_json(root / 'preflight-result.json', value)
        return value
    finally:
        model.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    print(json.dumps(preflight(parser.parse_args().output.resolve())))
