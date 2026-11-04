"""New pair guard/receipt tests; no model, secret reads or host SymPy imports."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import swebench_cl_pair_evaluation as adapter
from swebench_cl_pair_specs import PAIRS,KIND
from swebench_cl_pair_behavior import verified
from repo_maintainer.task_session import fingerprint

class ReceiptTests(unittest.TestCase):
    def value(self):
        return {'kind':KIND,'pair':'evalf','base_commit':PAIRS['evalf']['bases'][PAIRS['evalf']['history']],
         'patch_sha256':'a'*64,'cases':[{'case':name,'passed':True,'diagnostic':''} for name in PAIRS['evalf']['cases']],
         'passed':3,'total':3,'model_requests':0,'official_score':False}
    def check(self,value,code=0):return verified(value,code,'evalf',self.value()['base_commit'],'a'*64)
    def test_valid_checks_never_claim_official_or_model_calls(self):self.assertEqual(self.value(),self.check(self.value()))
    def test_real_failure_requires_matching_actual_exit(self):
        value=self.value();value['cases'][0].update(passed=False,diagnostic='AssertionError');value['passed']=2
        self.check(value,1)
        with self.assertRaises(ValueError):self.check(value,0)
    def test_forged_counts_and_missing_duplicate_or_extra_cases_fail(self):
        for change in (lambda v:v.update(passed=True),lambda v:v.update(total=4),lambda v:v['cases'].pop(),
                       lambda v:v['cases'].append(v['cases'][0]),lambda v:v['cases'][0].update(passed=1)):
            value=self.value();change(value)
            with self.subTest(value=value),self.assertRaises(ValueError):self.check(value)
    def test_foreign_binding_paid_or_official_claims_rejected(self):
        for key,value in {'kind':'foreign','pair':'printing','base_commit':'b'*40,'patch_sha256':'b'*64,'model_requests':1,'official_score':True}.items():
            with self.subTest(key=key),self.assertRaises(ValueError):self.check({**self.value(),key:value})

class PairBindingTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.batch=Path(self.temp.name).resolve()
        self.root=self.batch/'pairs/evalf';(self.root/'prepared/agent').mkdir(parents=True)
        self.plan={'kind':'swebench-cl-two-pair-development-v1','root':str(self.batch),'pair_names':list(PAIRS),
            'request_ceiling':200,'per_pair_ceiling':100,'credentials_read':False,'paid_requests':0}
        self.value={'schema_version':1,'kind':'swebench-cl-new-pair-v1','name':'evalf','spec':copy.deepcopy(PAIRS['evalf']),
            'batch_root':str(self.batch),'batch_plan_sha256':fingerprint(self.plan)}
        self.save('batch-plan.json',self.plan,self.batch);self.save('pair-spec.json',self.value)
        self.save('prepared/preparation.json',{'history':PAIRS['evalf']['history'],'target':PAIRS['evalf']['target']})
        self.agents=[{'instance_id':identity,'repo':'sympy/sympy','base_commit':base,'problem_statement':'Public problem'}
            for identity,base in PAIRS['evalf']['bases'].items()]
        self.save('prepared/agent/tasks.json',self.agents)
    def save(self,name,value,root=None):(root or self.root).joinpath(name).write_text(json.dumps(value),encoding='utf-8')
    def test_two_pairs_are_disjoint_and_do_not_reuse_known_old_instances(self):
        ids=[s[key] for s in PAIRS.values() for key in ('history','target')]
        self.assertEqual(4,len(set(ids)));self.assertFalse(set(ids)&{'sympy__sympy-17655','sympy__sympy-22714'})
        self.assertEqual(self.value,adapter.binding(self.root))
    def test_modified_batch_and_source_scope_fail_before_provider_construction(self):
        for change in (lambda v:v['spec']['allowed_paths'].append('.env'),lambda v:v.update(batch_plan_sha256='a'*64),lambda v:v.update(schema_version=True)):
            value=copy.deepcopy(self.value);change(value);self.save('pair-spec.json',value)
            with self.assertRaises(RuntimeError):adapter.configured(self.root)
        self.save('pair-spec.json',self.value);self.plan['request_ceiling']=201;self.save('batch-plan.json',self.plan,self.batch)
        with self.assertRaises(RuntimeError):adapter.binding(self.root)
    def test_gold_or_hints_cannot_enter_agent_projection_even_with_valid_spec(self):
        self.agents[0]['patch']='CANARY';self.save('prepared/agent/tasks.json',self.agents)
        with self.assertRaises(RuntimeError):adapter.binding(self.root)
    def test_legacy_core_is_private_and_other_pairs_do_not_change_original_constants(self):
        import swebench_cl_evaluation as original
        before=original.IDS;core=adapter.configured(self.root)
        self.assertEqual(before,original.IDS);self.assertEqual(tuple(PAIRS['evalf']['bases']),core.IDS)
        self.assertEqual(('A_existing_baseline','B_operator_selected_history','C_automatic_retrieval'),core.ARMS)
        self.assertIs(core.history.__globals__,core.__dict__)
        self.assertIn('scripts/swebench_cl_pair_evaluation.py',core.code_hashes())
    def test_missing_preservation_controls_cannot_freeze_or_read_model_config(self):
        self.save('image-lock.json',{})
        with patch.object(adapter,'behavior_controls',side_effect=RuntimeError('missing controls')):
            with self.assertRaises(RuntimeError):adapter.freeze(self.root,Path(sys.executable))

if __name__=='__main__':unittest.main()
