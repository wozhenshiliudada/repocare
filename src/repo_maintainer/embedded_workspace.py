"""Same-listener dispatch to owned sessions, preserving each session's boundary."""
import re
import uuid
from .local_ui import create_app
from .models import SafetyError

class EmbeddedWorkspace:
    def __init__(self, session, port):
        from .git import RepositoryGateway
        from .task_session import repository_identity
        from .github_connection import github_url
        self.session = session
        repository = RepositoryGateway(session.repo)
        self.repository_id = repository_identity(repository.root, repository.common_dir)
        try:
            self.github_url = github_url(repository.command('config','--get','remote.origin.url',check=False).decode().strip())[1]
        except (SafetyError, ValueError):
            self.github_url = ''
        self.identity = uuid.uuid4().hex
        self.port = port
        self.app = create_app(session, port)

    @property
    def url(self):
        return f'http://127.0.0.1:{self.port}/#workspace={self.identity}'

    def view(self):
        with self.session.lock:
            return {'id':self.identity, 'repository':str(self.session.repo),
                    'repository_id':self.repository_id, 'github_url':self.github_url,
                    'token':self.session.token, 'api_prefix':f'/api/workspaces/{self.identity}/',
                    'active':self.session.active,
                    'model_profile_id':getattr(self.session,'model_profile_id','legacy'),
                    'model_name':getattr(self.session,'model_name','原监控模型'),
                    'model_selectable':hasattr(self.session,'model_profile_id')}

    def close(self):
        self.session.close()  # Called by the original creator worker; no new listener or thread.

class WorkspaceRouter:
    path = re.compile(r'/api/workspaces/([a-f0-9]{32})/(state|service|actions/[a-z_]+)\Z')
    def __init__(self, workbench, fallback):
        self.workbench, self.fallback = workbench, fallback

    async def __call__(self, scope, receive, send):
        match = self.path.fullmatch(scope.get('path','')) if scope['type']=='http' else None
        if match:
            with self.workbench.lock:
                workspace = self.workbench.workspaces.get(match[1])
            if workspace:
                # Retain Host/Origin/Authorization; the selected session authenticates its own token.
                path = '/api/'+match[2]
                nested = {**scope, 'path':path, 'raw_path':path.encode('ascii')}
                await workspace.app(nested, receive, send)
                return
        await self.fallback(scope, receive, send)
