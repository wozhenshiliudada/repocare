"""Draft-only form boundary; no controller, account or network fixture is involved."""
import copy
import math
import unittest

from repo_maintainer.assistant_forms import form_snapshot, form_proposal
from repo_maintainer.models import SafetyError


def snapshot():
    return {'schema_version': 1, 'form_id': 'a' * 32, 'revision': 'b' * 64, 'fields': [
        {'id': '1' * 32, 'label': '需求描述', 'type': 'text', 'value': ''},
        {'id': '2' * 32, 'label': '请求额度', 'type': 'number', 'value': '', 'min': 1, 'max': 500},
        {'id': '3' * 32, 'label': '运行方式', 'type': 'select', 'value': '', 'options': ['人工', '只读']},
        {'id': '4' * 32, 'label': '显示详细说明', 'type': 'checkbox', 'value': False}]}


def proposal(form=None, changes=None):
    form = form or snapshot()
    return {'form_id': form['form_id'], 'revision': form['revision'], 'changes': changes or [
        {'field_id': '1' * 32, 'value': '检查加法需求'}, {'field_id': '2' * 32, 'value': 20},
        {'field_id': '3' * 32, 'value': '只读'}, {'field_id': '4' * 32, 'value': True}],
        'explanation': '仅建议草稿，由用户核对后填写。'}


class AssistantFormsTests(unittest.TestCase):
    def test_opaque_select_labels_are_data_and_changes_still_require_actual_values(self):
        form=snapshot()
        field=form['fields'][2]
        field.update(value='a'*32, options=['a'*32,'b'*32], option_labels=['Model A','Model B'])
        original=copy.deepcopy(form)
        self.assertIs(form,form_snapshot(form))
        result=form_proposal(proposal(form,[{'field_id':field['id'],'value':'b'*32}]),form)
        self.assertEqual(result['changes'],[{'field_id':field['id'],'value':'b'*32}])
        self.assertEqual(form,original)
        with self.assertRaises(SafetyError):
            form_proposal(proposal(form,[{'field_id':field['id'],'value':'Model B'}]),form)
        del field['option_labels']
        self.assertIs(form,form_snapshot(form))

    def test_option_label_metadata_rejects_wrong_types_counts_and_unbounded_captions(self):
        for labels in ('Model A', [1], [True], ['a'], ['a','b','c'], ['x'*257,'b']):
            with self.subTest(labels=labels):
                form=snapshot();form['fields'][2]['option_labels']=labels
                with self.assertRaises(SafetyError):form_snapshot(form)
        form=snapshot();form['fields'][0]['option_labels']=[]
        with self.assertRaises(SafetyError):form_snapshot(form)
        form=snapshot();del form['fields'][2]['options'];form['fields'][2]['option_labels']=[]
        with self.assertRaises(SafetyError):form_snapshot(form)
        form=snapshot();field=form['fields'][2]
        field.update(options=[str(n) for n in range(101)],option_labels=['label']*101,value='0')
        with self.assertRaises(SafetyError):form_snapshot(form)

    def test_valid_draft_preserves_original_snapshot_and_has_no_execution_receipt(self):
        form = snapshot(); before = copy.deepcopy(form); candidate = proposal(form)
        self.assertIs(form, form_snapshot(form))
        result = form_proposal(candidate, form)
        self.assertEqual(before, form)
        self.assertEqual({**candidate, 'draft_only': True}, result)
        self.assertTrue({'action', 'applied', 'submitted', 'approved', 'next_step', 'authorization'}.isdisjoint(result))
        self.assertIsNone(form_snapshot(None))

    def test_unknown_secret_approval_and_action_fields_are_rejected(self):
        for label in ('API Key', 'access token', 'password', 'credential', 'secret', '密钥', '密码',
                      '同意条款', '确认', '批准运行', '授权', '下一步', 'Submit', 'Approve', 'Confirm'):
            form = snapshot(); form['fields'][0]['label'] = label
            with self.subTest(label=label), self.assertRaises(SafetyError):
                form_snapshot(form)
        for mutate in (lambda v: v.update(action='approve'), lambda v: v.update(api_key='synthetic'),
                       lambda v: v['fields'][0].update(action='next_step'),
                       lambda v: v['fields'][0].update(secret=False),
                       lambda v: v['fields'].append(copy.deepcopy(v['fields'][0]))):
            form = snapshot(); mutate(form)
            with self.subTest(form=form), self.assertRaises(SafetyError):
                form_snapshot(form)
        for mutate in (lambda v: v.update(action='submit'), lambda v: v.update(approved=True),
                       lambda v: v['changes'][0].update(action='next_step'),
                       lambda v: v['changes'][0].update(field_id='f' * 32),
                       lambda v: v['changes'].append(copy.deepcopy(v['changes'][0]))):
            value = proposal(); mutate(value)
            with self.subTest(value=value), self.assertRaises(SafetyError):
                form_proposal(value, snapshot())

    def test_stale_form_wrong_binding_and_missing_snapshot_never_produce_a_draft(self):
        for changes in ({'form_id': 'c' * 32}, {'revision': 'd' * 64}, {'revision': None}, {'changes': []}):
            with self.subTest(changes=changes), self.assertRaises(SafetyError):
                form_proposal({**proposal(), **changes}, snapshot())
        with self.assertRaises(SafetyError):
            form_proposal(proposal(), None)

    def test_values_obey_type_finiteness_options_and_current_bounds(self):
        cases = [('1' * 32, 1), ('1' * 32, 'x\x00y'), ('1' * 32, 'x' * 8193),
                 ('2' * 32, True), ('2' * 32, '20'), ('2' * 32, ''), ('2' * 32, float('nan')),
                 ('2' * 32, float('inf')), ('2' * 32, 0), ('2' * 32, 501),
                 ('3' * 32, '自动批准'), ('3' * 32, 1), ('4' * 32, 1), ('4' * 32, 'true')]
        for identity, value in cases:
            with self.subTest(identity=identity, value=value), self.assertRaises(SafetyError):
                form_proposal(proposal(changes=[{'field_id': identity, 'value': value}]), snapshot())

    def test_malformed_json_types_and_inverted_bounds_fail_with_safety_errors(self):
        for mutate in (lambda v: v.update(schema_version=True), lambda v: v.update(fields={}),
                       lambda v: v['fields'][0].update(type=[]),
                       lambda v: v['fields'][0].update(type={'text': True}),
                       lambda v: v['fields'][2].update(options=[False]),
                       lambda v: v['fields'][1].update(min=True),
                       lambda v: v['fields'][1].update(min=math.inf),
                       lambda v: v['fields'][1].update(min=10, max=1)):
            form = snapshot(); mutate(form)
            with self.subTest(form=form), self.assertRaises(SafetyError):
                form_snapshot(form)


if __name__ == '__main__':
    unittest.main()
