"""Safe terminal conversation counts, independently checked against local Native bytes."""
import hashlib
import json
import re

from .models import SafetyError
from .runtime_limits import MAX_SAFE_TOKEN_COUNT
from .assistant_limits import MAX_ASSISTANT_NATIVE_BYTES

CATEGORIES = {'invalid_model', 'auth', 'network', 'rate_limit', 'unsupported_parameters', 'provider_error'}
HINTS = {
    'invalid_model': ('模型名称或访问权限无效。请从服务商目录核对准确模型 ID，并另存配置后再发送。', 'The model ID or access is invalid. Select the exact provider model ID, save a new configuration, then send again.'),
    'auth': ('模型身份验证失败。请检查该配置的密钥及服务权限，再另存配置。', 'Model authentication failed. Check this configuration’s API key and permissions, then save a new configuration.'),
    'network': ('模型网络连接失败。请检查服务地址及网络，再手动发送。', 'The model connection failed. Check the endpoint and network, then send manually.'),
    'rate_limit': ('服务商限制了请求频率或额度。请检查服务商额度，稍后手动发送。', 'The provider limited requests or quota. Check provider quota and send manually later.'),
    'unsupported_parameters': ('服务商不支持当前请求参数。请核对思考协议、思考等级和输出长度，再另存配置。', 'The provider rejected request parameters. Check reasoning protocol, effort and output length, then save a new configuration.'),
    'provider_error': ('模型服务返回错误。请检查服务状态及配置后再手动发送。', 'The model service returned an error. Check service status and configuration, then send manually.'),
}


def has_native_checkpoint(raw, expected_sha256):
    checkpoint=hashlib.sha256()
    for line in raw.splitlines(keepends=True):
        checkpoint.update(line)
        if line.endswith(b'\n') and checkpoint.hexdigest()==expected_sha256:
            return True
    return False


def classify_error(message):
    """Only fixed categories leave this function; provider text stays in Native."""
    if not isinstance(message, str) or not message or re.search(r'timeout|timed out|aborted|aborterror', message, re.I):
        return None
    if re.search(r'^\s*(401|403)\b|authentication|unauthorized|invalid.{0,16}api.?key', message, re.I):
        return 'auth'
    if re.search(r'^\s*429\b|rate.?limit|quota', message, re.I):
        return 'rate_limit'
    if re.search(r'unsupportedmodel|model[_ -]?(?:not[_ -]?(?:exist|found)|unsupported)|(?:unknown|invalid|unsupported).{0,24}model|model.{0,32}(?:not exist|not found|does not exist|not available)', message, re.I):
        return 'invalid_model'
    if re.search(r'ECONN|ENOTFOUND|EAI_AGAIN|fetch failed|connection error|connection (?:refused|reset)|network', message, re.I):
        return 'network'
    if re.search(r'^\s*400\b', message) and re.search(r'parameter|reasoning|thinking|max_tokens|max_completion|temperature|invalid.{0,16}argument', message, re.I):
        return 'unsupported_parameters'
    return 'provider_error'


def validate_terminal_receipt(value, allowance):
    fields = {'schema_version', 'purpose', 'status', 'failure_category', 'requests', 'requests_started',
              'request_id', 'session_id', 'profile_sha256', 'prompt_sha256', 'native_file', 'native_sha256',
              'terminal_entry_id', 'terminal_error_sha256', 'max_output_tokens', 'terminal_count'}
    def require(condition):
        if not condition:
            raise SafetyError('会话终态计数回执不匹配；保留未知占用')
    require(isinstance(value, dict) and set(value) == fields)
    require(type(value['schema_version']) is int and value['schema_version'] == 1
            and value['purpose'] == 'conversation-v1' and value['status'] == 'failed'
            and value['failure_category'] in CATEGORIES)
    require(type(value['requests']) is int and 1 <= value['requests'] <= allowance
            and type(value['requests_started']) is int and value['requests_started'] == value['requests'])
    require(isinstance(value['terminal_count'], dict)
            and value['terminal_count'] == {'completed_streams': value['requests'], 'native_assistant_messages': value['requests']}
            and all(type(n) is int for n in value['terminal_count'].values()))
    for name in ('request_id', 'session_id'):
        require(isinstance(value[name], str) and re.fullmatch('[a-f0-9]{32}', value[name]))
    for name in ('profile_sha256', 'prompt_sha256', 'native_sha256', 'terminal_error_sha256'):
        require(isinstance(value[name], str) and re.fullmatch('[a-f0-9]{64}', value[name]))
    require(isinstance(value['native_file'], str) and re.fullmatch(r'[a-zA-Z0-9_.+-]{1,180}\.jsonl', value['native_file']))
    require(isinstance(value['terminal_entry_id'], str) and re.fullmatch('[a-f0-9]{8}', value['terminal_entry_id']))
    require(type(value['max_output_tokens']) is int and 1 <= value['max_output_tokens'] <= MAX_SAFE_TOKEN_COUNT)
    return value


def verify_terminal_native(value, allowance, *, directory, record, payload, profile_sha256, history_context=None):
    """A protocol-shaped dictionary alone never proves a counted failure."""
    from .dashboard import unlinked
    value = validate_terminal_receipt(value, allowance)
    if (value['request_id'] != payload['request_id'] or value['session_id'] != record['id']
            or value['profile_sha256'] != profile_sha256
            or (record['native_file'] is not None and value['native_file'] != record['native_file'])):
        raise SafetyError('会话终态归属不匹配；保留未知占用')
    path = unlinked(directory/'sessions'/value['native_file'])
    if not path.is_file() or path.stat().st_size > MAX_ASSISTANT_NATIVE_BYTES:
        raise SafetyError('会话终态Native缺失；保留未知占用')
    raw = path.read_bytes()
    sha = lambda data: hashlib.sha256(data).hexdigest()
    if sha(raw) != value['native_sha256'] or not raw.endswith(b'\n'):
        raise SafetyError('会话终态Native指纹不匹配；保留未知占用')
    entries = [json.loads(line) for line in raw.splitlines() if line.strip()]
    # The original checkpoint is a complete byte prefix, never a rewritten snapshot.
    if record['native_file']:
        cut = 0
        checkpoint = hashlib.sha256()
        for line in raw.splitlines(keepends=True):
            cut += len(line)
            checkpoint.update(line)
            if checkpoint.hexdigest() == record['native_sha256']:
                break
        else:
            raise SafetyError('原会话Native前缀变化；保留未知占用')
        entries = [json.loads(line) for line in raw[cut:].splitlines() if line.strip()]
    users = [entry['message'] for entry in entries if entry.get('type') == 'message' and entry.get('message', {}).get('role') == 'user']
    assistants = [entry for entry in entries if entry.get('type') == 'message' and entry.get('message', {}).get('role') == 'assistant']
    if len(users) != 1 or len(assistants) != value['requests'] or not entries or entries[-1] != assistants[-1]:
        raise SafetyError('会话终态Native计数不匹配；保留未知占用')
    content = users[0].get('content')
    if (not isinstance(content, list) or len(content) != 1 or content[0].get('type') != 'text'
            or not isinstance(content[0].get('text'), str)):
        raise SafetyError('会话终态输入不匹配；保留未知占用')
    prompt = content[0]['text']
    expected = {'user_message': payload['text'], 'current_form': payload['form_context'], 'operation_id': payload['request_id']}
    if history_context is not None:
        expected['history_context'] = history_context
    if sha(prompt.encode()) != value['prompt_sha256'] or json.loads(prompt) != expected:
        raise SafetyError('会话终态输入绑定不匹配；保留未知占用')
    last = assistants[-1]
    if (last.get('id') != value['terminal_entry_id'] or last['message'].get('stopReason') != 'error'
            or classify_error(last['message'].get('errorMessage')) != value['failure_category']
            or sha(last['message']['errorMessage'].encode()) != value['terminal_error_sha256']
            or any(entry['message'].get('provider') != 'repocare-assistant'
                   or entry['message'].get('stopReason') not in {'stop', 'toolUse', 'error'} for entry in assistants)):
        raise SafetyError('会话终态错误绑定不匹配；保留未知占用')
    return value
