"""Journal-bound structural provenance with a separate explicit model projection."""
from .models import SafetyError, digest
from .pi_provider import PiProvider
from .structure_index import MANIFEST, GRAPH, directory, load_structure, unchanged, query_structure, text
from .source_memory import canonical
from .git import RepositoryGateway
from .store import redact
from .ui_backup import read_bytes


class UIStructure:
    def __init__(self, index, repository, provider, runs_dir, context_query=None):
        self.binding = None
        self.context_query = context_query
        if context_query is not None and (index is None or not text(context_query, 128)):
            raise SafetyError('Structure model context requires an explicit index and bounded query')
        if isinstance(provider, PiProvider):
            provider._structure_guard = None
            provider._structure_context = None
        if index is None:
            return
        if not isinstance(provider, PiProvider):
            raise SafetyError('UI structure requires the real Pi provider')
        self.root, self.repository = directory(index), repository
        self.planning_paths = (*provider.allowed_paths, *provider.readonly_paths)
        if self.root.is_relative_to(runs_dir) or runs_dir.is_relative_to(self.root):
            raise SafetyError('Structure index and UI runs must have separate directories')
        self.binding = {'schema_version': 1, 'path_sha256': digest(str(self.root).encode()),
                        'manifest_file_sha256': digest(read_bytes(self.root / MANIFEST, 32768)),
                        'graph_sha256': digest(read_bytes(self.root / GRAPH, 1024*1024))}
        provider._structure_guard = self.require_current
        if context_query is not None:
            self.binding['model_context'] = {'schema_version': 1, 'query_sha256': digest(context_query.encode())}
            provider._structure_context = self.context

    def pins(self):
        if (digest(read_bytes(self.root / MANIFEST, 32768)) != self.binding['manifest_file_sha256']
                or digest(read_bytes(self.root / GRAPH, 1024*1024)) != self.binding['graph_sha256']):
            raise SafetyError('UI structure files changed; preserve the bound journal')

    def require_current(self):
        if not self.binding:
            return None
        self.pins()
        root, manifest, graph, raw, graph_raw, selected = load_structure(self.root, self.repository)
        result = {'enabled': True, 'status': 'verified', 'binding': self.binding,
                  'structural_claims_only': True, 'runtime_authorized': False, 'model_context': self.context_query is not None,
                  'engine': manifest['engine'], 'head': manifest['source']['head'],
                  'files': manifest['source']['files'], 'nodes': len(graph['nodes']), 'edges': len(graph['edges']),
                  'manifest_sha256': manifest['manifest_sha256']}
        unchanged(root, self.repository, manifest, raw, graph_raw, selected)
        self.pins()
        return result

    def preview(self, query):
        if not self.binding:
            raise SafetyError('Structure index was not enabled at server startup')
        self.require_current()
        result = query_structure(self.root, self.repository, query)
        self.require_current()
        return result

    def context(self, issue, planning_base_sha, planning_repository=None):
        if self.context_query is None:
            return None
        self.require_current()
        root, manifest, graph, raw, graph_raw, selected = load_structure(self.root, self.repository)
        query = self.preview(self.context_query)
        planning_repository = planning_repository or RepositoryGateway(self.repository)
        planning_files = [{'path': path, 'source_sha256': digest(planning_repository.read(path))}
                          for path in self.planning_paths]
        value = {'schema_version': 1, 'engine': manifest['engine'], 'source': manifest['source'],
                 'manifest_sha256': manifest['manifest_sha256'],
                 'manifest_file_sha256': self.binding['manifest_file_sha256'], 'graph_sha256': manifest['graph_sha256'],
                 'planning_base_sha': planning_base_sha, 'planning_files_sha256': digest(canonical(planning_files)),
                 'issue_sha256': digest(issue.encode()),
                 'query': self.context_query, 'query_sha256': query['query_sha256'],
                 **{k: query[k] for k in ('hits', 'edges', 'related_nodes')},
                 'structural_claims_only': True, 'runtime_authorized': False}
        value['context_sha256'] = digest(canonical(value))
        if len(canonical(value)) > 256*1024:
            raise SafetyError('Structure model context exceeds its bounded size')
        unchanged(root, self.repository, manifest, raw, graph_raw, selected)
        self.pins()
        return value

    def view(self):
        if not self.binding:
            return {'enabled': False, 'status': 'disabled', 'runtime_authorized': False, 'model_context': False}
        try:
            return self.require_current()
        except (SafetyError, OSError, ValueError) as error:
            return {'enabled': True, 'status': 'invalid', 'binding': self.binding,
                    'structural_claims_only': True, 'runtime_authorized': False, 'model_context': self.context_query is not None,
                    'error': redact(str(error))}
