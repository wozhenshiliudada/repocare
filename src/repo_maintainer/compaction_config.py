"""Explicit future-task context settings. Unknown capacity cannot enable compaction."""
from .runtime_limits import DEFAULT_MAX_OUTPUT_TOKENS, MAX_SAFE_TOKEN_COUNT
from .task_session import require

FIELDS={'schema_version','enabled','context_window_tokens','trigger_tokens','keep_recent_tokens','summary_max_output_tokens'}


def validate_config(value):
    require(isinstance(value,dict) and set(value)==FIELDS and type(value['schema_version']) is int
            and value['schema_version']==1 and type(value['enabled']) is bool,'Invalid context settings')
    for key in FIELDS-{'schema_version','enabled'}:require(type(value[key]) is int,'Context settings require integer token counts')
    window,trigger,keep,output=(value[k] for k in ('context_window_tokens','trigger_tokens','keep_recent_tokens','summary_max_output_tokens'))
    require(all(0<=count<=MAX_SAFE_TOKEN_COUNT for count in (window,trigger,keep))
            and 1<=output<=MAX_SAFE_TOKEN_COUNT,'Context settings require safe integer token counts')
    if window:
        require(window>=4096 and 128<=keep<trigger and trigger+output+1024<window,
                'Reserve room for summary output and the retained context')
    else:require(not value['enabled'] and trigger==0 and keep==0,'Unknown context capacity cannot enable compaction')
    return value


def default_config(provider):
    _context,binding=provider.task_identity()
    known=provider.mock_url is not None or (binding['model']=='mimo-v2.6-flash' and
        binding['endpoint'][1] in {'api.xiaomimimo.com','token-plan-cn.xiaomimimo.com'})
    window=1048576 if known else 0
    return {'schema_version':1,'enabled':False,'context_window_tokens':window,
            'trigger_tokens':720000 if window else 0,'keep_recent_tokens':16000 if window else 0,
            'summary_max_output_tokens':DEFAULT_MAX_OUTPUT_TOKENS}
