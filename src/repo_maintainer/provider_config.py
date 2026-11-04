"""Load an explicitly named local credentials file as data, never as shell code."""
from pathlib import Path
import json
import re
from urllib.parse import urlsplit

from .models import SafetyError
from .runtime_limits import MAX_OUTPUT_TOKENS, MAX_SAFE_TOKEN_COUNT


PROVIDER_REQUIRED_FIELDS = {"PI_MAINTENANCE_API_KEY", "PI_MAINTENANCE_BASE_URL", "PI_MAINTENANCE_MODEL"}
MODEL_LIMIT_FIELDS = {"PI_MAINTENANCE_CONTEXT_WINDOW_TOKENS", "PI_MAINTENANCE_MAX_OUTPUT_TOKENS"}
REASONING_SETTINGS_FIELDS = {"PI_MAINTENANCE_REASONING_PROTOCOL", "PI_MAINTENANCE_REASONING_EFFORT"}
REASONING_LEVELS_FIELD = 'PI_MAINTENANCE_REASONING_LEVELS'
REASONING_FIELDS = REASONING_SETTINGS_FIELDS | {REASONING_LEVELS_FIELD}
PROVIDER_FIELDS = PROVIDER_REQUIRED_FIELDS | MODEL_LIMIT_FIELDS | REASONING_FIELDS
MAX_CONTEXT_WINDOW_TOKENS = MAX_SAFE_TOKEN_COUNT
_TOKEN_INTEGER = re.compile(r"[1-9][0-9]{0,15}\Z")
REASONING_EFFORTS = {
    'none': ('off',),
    'openai': ('off', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'),
    'deepseek': ('off', 'low', 'high', 'max'),
}
CANONICAL_REASONING_LEVELS = REASONING_EFFORTS['openai']


def _reasoning_pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise SafetyError('Duplicate reasoning mapping field')
        result[key] = value
    return result


def validate_reasoning_levels(levels, protocol):
    if not isinstance(levels, list) or not 1 <= len(levels) <= 7:
        raise SafetyError('Reasoning levels must be a nonempty subset of the seven supported levels')
    seen = set()
    for row in levels:
        if (not isinstance(row, dict) or set(row) != {'level', 'api_value'}
                or not isinstance(row['level'], str) or row['level'] not in CANONICAL_REASONING_LEVELS
                or row['level'] in seen):
            raise SafetyError('Invalid or duplicated reasoning level')
        seen.add(row['level'])
        value = row['api_value']
        if value is not None and (not isinstance(value, str) or not 1 <= len(value) <= 64
                or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value)):
            raise SafetyError('Invalid reasoning API value')
        if (protocol == 'none' and (row['level'] != 'off' or value is not None)
                or protocol == 'deepseek' and row['level'] == 'off' and value is not None
                or protocol == 'openai' and row['level'] == 'off' and value not in (None, 'none')):
            raise SafetyError('Invalid off-level mapping for this reasoning protocol')
    return levels


def resolve_model_reasoning(config):
    """Only explicit paired settings may change a profile's reasoning behavior."""
    present = REASONING_FIELDS.intersection(config)
    if not present:
        return {'reasoning_protocol': None, 'reasoning_effort': None, 'reasoning_explicit': False,
                'reasoning_levels': None}
    protocol = config.get('PI_MAINTENANCE_REASONING_PROTOCOL')
    effort = config.get('PI_MAINTENANCE_REASONING_EFFORT')
    if (not REASONING_SETTINGS_FIELDS <= present or not isinstance(protocol, str) or not isinstance(effort, str)
            or protocol not in REASONING_EFFORTS):
        raise SafetyError('Unsupported or incomplete model reasoning settings')
    levels = None
    if REASONING_LEVELS_FIELD in config:
        try:
            levels = validate_reasoning_levels(json.loads(config[REASONING_LEVELS_FIELD], object_pairs_hook=_reasoning_pairs), protocol)
        except (ValueError, TypeError, RecursionError):
            raise SafetyError('Invalid reasoning level mappings') from None
    allowed = [row['level'] for row in levels] if levels is not None else REASONING_EFFORTS[protocol]
    if effort not in allowed:
        raise SafetyError('Selected reasoning level is not configured for this model')
    return {'reasoning_protocol': protocol, 'reasoning_effort': effort, 'reasoning_explicit': True,
            'reasoning_levels': levels}


def resolve_model_limits(config, *, mock_url=None, context_policy=None,
                         legacy_context_window_tokens=None, legacy_max_output_tokens=MAX_OUTPUT_TOKENS):
    """Resolve model metadata without changing the historical no-field algorithm.

    Values are user-declared capacities, not verification of a provider's limits.
    The optional environment values are canonical decimal strings. Browser input
    is checked separately before it is serialized to this immutable config.
    """
    explicit = any(field in config for field in MODEL_LIMIT_FIELDS)
    model, base_url = config.get('PI_MAINTENANCE_MODEL'), config.get('PI_MAINTENANCE_BASE_URL', '')
    profile_context = 1048576 if (mock_url or (
        model == 'mimo-v2.6-flash' and urlsplit(base_url).hostname in
        {'api.xiaomimimo.com', 'token-plan-cn.xiaomimimo.com'})) else 32768
    if legacy_context_window_tokens is None:
        legacy_context_window_tokens = profile_context
    # Once a profile declares a limit, a missing companion uses the same profile
    # default for every purpose. Historical per-operation/policy overrides apply
    # only to profiles that have never declared either field.
    inherited_context = profile_context if explicit else (
        (context_policy or {}).get('context_window_tokens') or legacy_context_window_tokens)
    values = [inherited_context, MAX_OUTPUT_TOKENS if explicit else legacy_max_output_tokens]
    for index, field in enumerate(('PI_MAINTENANCE_CONTEXT_WINDOW_TOKENS', 'PI_MAINTENANCE_MAX_OUTPUT_TOKENS')):
        if field in config:
            raw = config[field]
            if not isinstance(raw, str) or not _TOKEN_INTEGER.fullmatch(raw):
                raise SafetyError('Model token limits must be positive decimal integers')
            values[index] = int(raw)
    context, output = values
    if explicit and (type(context) is not int or type(output) is not int
                     or not 4096 <= context <= MAX_CONTEXT_WINDOW_TOKENS
                     or not 1 <= output <= MAX_SAFE_TOKEN_COUNT or output >= context):
        raise SafetyError('Unsupported model limits: context must be at least 4096, output positive and below context, both safe integers')
    return {'context_window_tokens': context, 'max_output_tokens': output, 'limits_explicit': explicit}


def load_provider_config(path: Path) -> dict[str, str]:
    if path.is_symlink() or path.stat().st_size > 65536:
        raise SafetyError("Provider configuration must be a small regular local file")
    if not path.is_file():
        raise SafetyError("Provider configuration must be a regular file")
    result = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, separator, value = line.partition("=")
        name, value = name.strip(), value.strip()
        if not separator or name not in PROVIDER_FIELDS or name in result:
            raise SafetyError("Invalid or duplicated provider configuration field")
        if value.startswith(("'", '"')):
            if len(value) < 2 or value[-1] != value[0]:
                raise SafetyError("Invalid provider configuration quoting")
            value = value[1:-1]
        if not value or any(character.isspace() for character in value) or "\x00" in value:
            raise SafetyError("Provider configuration fields must be nonempty without whitespace")
        result[name] = value
    return validate_provider_config(result)


def validate_provider_config(result: dict[str, str]) -> dict[str, str]:
    """Validate file and browser settings through the same credential boundary."""
    if not PROVIDER_REQUIRED_FIELDS <= set(result) or not set(result) <= PROVIDER_FIELDS:
        raise SafetyError("Provider configuration requires key, HTTPS endpoint and model")
    if any(not isinstance(value, str) or not value or len(value) > 8192
           or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value)
           for value in result.values()):
        raise SafetyError("Provider configuration fields must be nonempty without whitespace")
    try:
        url = urlsplit(result["PI_MAINTENANCE_BASE_URL"])
        port = url.port
    except ValueError:
        raise SafetyError("Provider endpoint must have a valid HTTPS host and port") from None
    if (url.scheme != "https" or not url.hostname or url.username or url.password
            or url.query or url.fragment or port == 0):
        raise SafetyError("Provider endpoint must be HTTPS without credentials, query or fragment")
    resolve_model_limits(result)
    resolve_model_reasoning(result)
    return result
