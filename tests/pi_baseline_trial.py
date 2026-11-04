"""Actual CLI + Pi + synthetic loopback model, separate test/edit approvals."""
import argparse
from contextlib import redirect_stdout
from dataclasses import asdict
import io
import json
from pathlib import Path
import re
import sys
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from repo_maintainer.baseline_probe import scope_for
from repo_maintainer.cli import main
from repo_maintainer.demo import create_fixture, DEMO_TEST
from repo_maintainer.git import RepositoryGateway
from repo_maintainer.models import Policy
from repo_maintainer.task_session import fingerprint
from repo_maintainer.workflow import cleanup_run

parser = argparse.ArgumentParser()
parser.add_argument('--url', required=True)
parser.add_argument('--directory', type=Path, required=True)
args = parser.parse_args()
args.directory.mkdir(exist_ok=False)
cases = []
for decision in ('reject', 'approve'):
    root = args.directory/decision
    root.mkdir()
    repo = create_fixture(root/'repository')
    git = RepositoryGateway(repo)
    before = git.snapshot()
    policy = Policy(('calculator.py',), (DEMO_TEST,), timeout_seconds=30.0)  # Match CLI load_policy's numeric form.
    issue = 'Repair addition; keep negative behavior'
    policy_file = root/'policy.json'
    policy_file.write_text(json.dumps(asdict(policy)), encoding='utf-8')
    scope = fingerprint(scope_for(git, issue, policy))
    decisions = []
    def approve(prompt):
        match = re.search(r"approve ([a-f0-9]{64})", prompt)
        assert match, prompt
        decisions.append(match[1])
        return 'approve '+match[1] if decision=='approve' else 'reject'
    text = io.StringIO()
    with redirect_stdout(text), patch('sys.stdin', SimpleNamespace(isatty=lambda: True)), \
            patch('builtins.input', side_effect=approve):
        code = main(['repair','--repo',str(repo),'--issue',issue,'--policy',str(policy_file),
                     '--pi-mock-url',args.url,'--request-budget','6','--max-attempts','1',
                     '--preflight-runs-dir',str(root/'probes'),'--approve-baseline',scope,
                     '--runs-dir',str(root/'repairs')])
    (root/'cli-output.log').write_text(text.getvalue(), encoding='utf-8')
    assert code==0, (decision,code,text.getvalue()[-2000:])
    probe_file, = (root/'probes').glob('*/run.json')
    run_file, = (root/'repairs').glob('*/run.json')
    probe, result = json.loads(probe_file.read_bytes()), json.loads(run_file.read_bytes())
    assert probe['status']=='baseline_ready' and probe['source_unchanged']
    assert not Path(probe['workspace']).exists() and probe['model_requests']==0
    assert len(decisions)==1 and result['plan_sha256']==decisions[0]
    expected = 'candidate_ready' if decision=='approve' else 'rejected'
    assert result['status']==expected and result['source_unchanged'], result
    assert result['provider_evidence']['baseline_context_sha256']
    if decision=='reject':
        assert result['baseline_tests']==[] and result['candidate_tests']==[]
    else:
        assert [r['returncode'] for r in result['baseline_tests']]==[1]
        assert [r['returncode'] for r in result['candidate_tests']]==[0]
        assert cleanup_run(run_file.parent)['status']=='discarded'
    assert git.snapshot()==before
    cases.append({'decision':decision,'status':result['status'],'source_unchanged':True,
                  'probe_id':probe['run_id'],'run_id':result['run_id'],
                  'baseline_context_sha256':result['provider_evidence']['baseline_context_sha256'],
                  'synthetic_requests':result['provider_evidence']['requests'],
                  'repair_baseline_exits':[r['returncode'] for r in result['baseline_tests']],
                  'candidate_exits':[r['returncode'] for r in result['candidate_tests']]})
summary = {'cases':cases,'paid_model_requests':0,'official_judge_calls':0,'new_edit_approval_per_case':True}
(args.directory/'summary.json').write_text(json.dumps(summary,indent=2), encoding='utf-8')
print(json.dumps(summary))
