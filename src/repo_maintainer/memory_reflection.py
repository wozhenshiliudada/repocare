"""Explicit Pi reflection with the original ledger and bounded data tools."""
from .knowledge_schema import validate_note
from .pi_auxiliary import invoke
from .task_session import fingerprint, require


def reflect(provider, source, guard):
    def validate(data,evidence,context):
        require(evidence.get('active_tools')==['propose_memory'] and
                evidence.get('reflection_context_sha256')==source['context_sha256'],'Reflection source receipt differs')
        candidate=validate_note(data['candidate'])
        require(data['candidate_sha256']==fingerprint(candidate)==evidence['knowledge_candidate_sha256'],
                'Reflection candidate receipt differs')
        return {'candidate':candidate,'evidence':evidence,'model':context['model'],'candidate_sha256':fingerprint(candidate)}
    return invoke(provider,'reflect.mjs','memory_reflection',{'source':source},guard,validate)
