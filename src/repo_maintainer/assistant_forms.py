"""Data-only form suggestions. This module has no controller or execution handle."""
import json
import math
import re

from .models import SafetyError

_ID = re.compile(r'[a-f0-9]{32}\Z')
_SHA = re.compile(r'[a-f0-9]{64}\Z')
_PRIVATE = re.compile(r'api.?key|token|secret|password|credential|confirm|approv|submit|next.?step|授权|批准|确认|同意|下一步|密钥|密码|凭据', re.I)


def form_snapshot(value):
    if value is None:
        return None
    if (not isinstance(value, dict) or set(value) != {'schema_version','form_id','revision','fields'}
            or type(value['schema_version']) is not int or value['schema_version'] != 1
            or not isinstance(value['form_id'], str) or not _ID.fullmatch(value['form_id'])
            or not isinstance(value['revision'], str) or not _SHA.fullmatch(value['revision'])
            or not isinstance(value['fields'], list) or len(value['fields']) > 64
            or len(json.dumps(value, ensure_ascii=False).encode()) > 100000):
        raise SafetyError('表单快照无效；助手只能建议当前非秘密草稿')
    seen = set()
    for field in value['fields']:
        if (not isinstance(field, dict) or not {'id','label','type','value'} <= set(field)
                or set(field) - {'id','label','type','value','options','option_labels','min','max'}
                or not isinstance(field['id'], str) or not _ID.fullmatch(field['id']) or field['id'] in seen
                or not isinstance(field['label'], str) or not 1 <= len(field['label']) <= 200
                or _PRIVATE.search(field['label'])
                or not isinstance(field['type'],str) or field['type'] not in {'text','number','select','checkbox'}):
            raise SafetyError('表单字段不在允许的草稿范围；确认、授权和密钥须由用户填写')
        seen.add(field['id'])
        if 'options' in field and (field['type'] != 'select' or not isinstance(field['options'],list)
                or len(field['options']) > 256 or not all(isinstance(s,str) and len(s)<=2048 for s in field['options'])):
            raise SafetyError('表单选项无效')
        if 'option_labels' in field and (field['type'] != 'select' or 'options' not in field
                or not isinstance(field['option_labels'],list) or len(field['option_labels']) > 100
                or len(field['option_labels']) != len(field['options'])
                or not all(isinstance(s,str) and len(s)<=256 for s in field['option_labels'])):
            raise SafetyError('表单选项说明无效')
        for key in ('min','max'):
            if key in field and (field['type']!='number' or type(field[key]) not in {int,float}
                                 or not math.isfinite(field[key])):
                raise SafetyError('数字字段边界无效')
        if 'min' in field and 'max' in field and field['min']>field['max']:
            raise SafetyError('数字字段边界顺序无效')
        _field_value(field, field['value'], current=True)
    return value


def _field_value(field, value, *, current=False):
    kind = field['type']
    if kind == 'checkbox':
        valid = type(value) is bool
    elif kind == 'number':
        valid = (current and value == '') or (type(value) in {int,float} and math.isfinite(value)
            and ('min' not in field or value >= field['min']) and ('max' not in field or value <= field['max']))
    else:
        valid = isinstance(value,str) and len(value) <= 8192 and '\x00' not in value
        if kind == 'select' and not (current and value == ''):
            valid = valid and value in field.get('options',[])
    if not valid:
        raise SafetyError('建议值不符合当前表单类型、选项或范围')


def form_proposal(value, snapshot):
    snapshot = form_snapshot(snapshot)
    if (snapshot is None or not isinstance(value,dict)
            or set(value) != {'form_id','revision','changes','explanation'}
            or value['form_id'] != snapshot['form_id'] or value['revision'] != snapshot['revision']
            or not isinstance(value['explanation'],str) or len(value['explanation']) > 4000
            or not isinstance(value['changes'],list) or not 1 <= len(value['changes']) <= 64):
        raise SafetyError('建议必须绑定本次表单，不能提交动作或改变步骤')
    fields = {field['id']:field for field in snapshot['fields']}
    seen = set()
    for change in value['changes']:
        if (not isinstance(change,dict) or set(change) != {'field_id','value'}
                or not isinstance(change['field_id'],str) or change['field_id'] not in fields
                or change['field_id'] in seen):
            raise SafetyError('建议包含未知、重复或不可填写字段')
        seen.add(change['field_id'])
        _field_value(fields[change['field_id']],change['value'])
    return {**value,'draft_only':True}
