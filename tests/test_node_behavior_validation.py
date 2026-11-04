"""Fixed Node data cases; local runtime and disposable projects only."""
import copy
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from repo_maintainer.behavior_validation import (
    compare_behavior_validation, make_contract, run_behavior_validation, validate_receipt)
from repo_maintainer.models import SafetyError


class NodeBehaviorValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = self.root/'workspace'; self.workspace.mkdir()
        self.artifacts = self.root/'artifacts'; self.artifacts.mkdir()
        self.node = str(Path(shutil.which('node')).resolve())

    def contract(self, **changes):
        case = {'id':'sum','module':'calc.mjs','callable':'total','args':[2.0,3],
                'kwargs':{},'expect':{'kind':'json','value':5.0},'missing_behavior':False, **changes}
        return make_contract(repository_id='a'*64,base_sha='b'*40,request_sha256='c'*64,
                             requirement='Add the two numbers',cases=[case])

    def run_contract(self, contract=None, phase='baseline', **options):
        return run_behavior_validation(contract or self.contract(),self.workspace,self.artifacts,
            node=self.node,phase=phase,environment_sha256='e'*64,**options)

    def write(self, text, module='calc.mjs'):
        (self.workspace/module).write_text(text,encoding='utf-8')

    def test_actual_esm_async_red_green_and_bound_receipt(self):
        self.write('export async function total(a,b) { return a-b; }')
        contract=self.contract(); self.assertEqual('node-behavior-v1',contract['kind'])
        before=self.run_contract(contract)
        self.assertEqual('test_failed',before['category'])
        self.write('export async function total(a,b) { return a+b; }')
        after=self.run_contract(contract,'candidate')
        self.assertTrue(compare_behavior_validation(before,after)['candidate_verified'])
        validate_receipt(after)
        path=Path(after['artifact_directory'])/'sum.actual.json'
        path.write_text('{}')
        with self.assertRaises(SafetyError): validate_receipt(after)

    def test_commonjs_and_original_native_typescript_subset(self):
        for module,text in [('calc.cjs','exports.total=(a,b)=>a+b;'),
            ('calc.js','export function total(a,b) { return a+b; }'),
            ('calc.ts','export function total(a:number,b:number):number { return a+b; }'),
            ('calc.cts','exports.total=(a:number,b:number):number=>a+b;'),
            ('calc.mts','export function total(a:number,b:number):number { return a+b; }')]:
            with self.subTest(module=module):
                self.write(text,module)
                self.assertEqual('passed',self.run_contract(self.contract(module=module))['category'])

    def test_nested_json_number_type_semantics_do_not_equate_boolean_and_number(self):
        self.write('export function total(){return {items:[1,{word:"你好",ok:true}],none:null};}')
        contract=self.contract(expect={'kind':'json','value':{'items':[1.0,{'word':'你好','ok':True}],'none':None}})
        self.assertEqual('passed',self.run_contract(contract)['category'])
        contract=self.contract(expect={'kind':'json','value':{'items':[1,{'word':'你好','ok':1}],'none':None}})
        self.assertEqual('test_failed',self.run_contract(contract)['category'])

    def test_syntax_dependency_and_unsupported_typescript_are_not_business_red(self):
        for module,text in [('calc.mjs','export function total( broken'),
            ('calc.mjs',"import 'repocare_missing_dependency_fixture'; export const total=()=>5;"),
            ('calc.mts','enum NumberValue { Five=5 }; export const total=()=>NumberValue.Five;')]:
            with self.subTest(text=text):
                self.write(text,module)
                result=self.run_contract(self.contract(module=module))
                self.assertEqual('inconclusive',result['category'])
                self.assertEqual(0,result['counts']['failed'])

    def test_missing_export_requires_explicit_business_expectation(self):
        self.write('export const existing=7;')
        self.assertEqual('inconclusive',self.run_contract()['category'])
        self.assertEqual('test_failed',self.run_contract(self.contract(missing_behavior=True))['category'])

    def test_spoofed_stdout_and_early_exit_do_not_pass(self):
        self.write("console.log('Ran 99 tests OK'); export const total=()=>0;")
        self.assertEqual('test_failed',self.run_contract()['category'])
        self.write('export const total=()=>process.exit(0);')
        self.assertEqual('inconclusive',self.run_contract()['category'])

    def test_node_timeout_never_drops_later_cases(self):
        self.write('export function total(){while(true){}}')
        contract=self.contract()
        cases=[contract['cases'][0],{**contract['cases'][0],'id':'second'}]
        contract=make_contract(repository_id='a'*64,base_sha='b'*40,request_sha256='c'*64,
                               requirement='Both declared cases must run',cases=cases)
        result=self.run_contract(contract,timeout_seconds=0.25)
        self.assertEqual('inconclusive',result['category'])
        self.assertEqual({'total':2,'passed':0,'failed':0,'inconclusive':2},result['counts'])
        self.assertEqual(['timeout','timeout'],[row['category'] for row in result['cases']])

    def test_non_json_results_and_loader_errors_inside_function_are_inconclusive(self):
        for body in ['undefined','NaN','BigInt(5)','new Date()','({get x(){return 5;}})',
                     'Array(2)','({value:undefined})','Number.MAX_SAFE_INTEGER+1']:
            self.write('export const total=()=>('+body+');')
            self.assertEqual('invalid_result',self.run_contract()['cases'][0]['category'],body)
        self.write("export async function total(){return import('repocare_missing_dependency_fixture');}")
        self.assertEqual('import_error',self.run_contract()['cases'][0]['category'])

    def test_exception_contract_does_not_accept_syntax_error_as_business_oracle(self):
        self.write("export function total(){throw new RangeError('invalid input');}")
        self.assertEqual('passed',self.run_contract(self.contract(expect={'kind':'exception','name':'RangeError'}))['category'])
        self.write("export function total(){throw new SyntaxError('invalid program');}")
        result=self.run_contract(self.contract(expect={'kind':'exception','name':'SyntaxError'}))
        self.assertEqual('inconclusive',result['category'])

    def test_other_dynamic_loader_failures_cannot_satisfy_expected_error(self):
        package=self.workspace/'node_modules/repocare-loader-fixture';package.mkdir(parents=True)
        (package/'package.json').write_bytes(b'{"name":"repocare-loader-fixture","exports":{}}')
        for specifier in ['node:repocare_missing_builtin_fixture','repocare-loader-fixture/private']:
            with self.subTest(specifier=specifier):
                self.write('export async function total(){return import('+json.dumps(specifier)+');}')
                result=self.run_contract(self.contract(expect={'kind':'exception','name':'Error'}))
                self.assertEqual('inconclusive',result['category'])
                self.assertEqual(0,result['counts']['failed'])
        self.write("export function total(){throw Object.assign(new Error('coded failure'),{code:'EACCES'});}")
        result=self.run_contract(self.contract(expect={'kind':'exception','name':'Error'}))
        self.assertEqual('execution_error',result['cases'][0]['category'])
        self.write("export function total(){throw {name:'RangeError'};}")
        result=self.run_contract(self.contract(expect={'kind':'exception','name':'RangeError'}))
        self.assertEqual('inconclusive',result['category'])

    def test_reject_commands_kwargs_mixed_runtime_and_unsupported_extensions(self):
        for change in [{'module':'calc.tsx'},{'module':'calc.d.ts'},{'module':'calc.d.mts'},
                       {'module':'node -e evil'},{'callable':'total()'},
                       {'script':'console.log(5)'},
                       {'kwargs':{'a':2}},{'args':[9007199254740992.0]}]:
            with self.subTest(change=change),self.assertRaises(SafetyError): self.contract(**change)
        contract=self.contract()
        mixed=copy.deepcopy(contract['cases']); mixed.append({**mixed[0],'id':'py','module':'calc.py'})
        with self.assertRaises(SafetyError):
            make_contract(repository_id='a'*64,base_sha='b'*40,request_sha256='c'*64,
                          requirement='mixed runtimes unsupported',cases=mixed)
