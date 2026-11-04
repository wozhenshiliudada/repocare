"""Explicit adapter from granted target environments to the original repair journal."""
from .models import SafetyError
from .target_environment import TargetEnvironment, validate_grant
from .workspace_inventory import inventory


class JournaledEnvironment(TargetEnvironment):
    def __init__(self, source, workspace, owner, plan, grant, journal):
        self.journal=journal
        super().__init__(source,workspace,owner,plan,grant)

    def process(self, argv, *, on_start=None, timeout=None):
        # Include installs and identity probes, not only the visible test command,
        # in the existing controller's recovery journal.
        self.journal.start('environment_command')
        def started(pid):
            self.journal.pid(pid)
            if on_start is not None:
                on_start(pid)
        result=super().process(argv,on_start=started,timeout=timeout)
        self.journal.done(result)
        return result


class GrantedEnvironment:
    """Trusted controller input. Constructing the factory grants no model requests."""
    def __init__(self, plan, grant):
        # Fail before a caller can enter model planning with no execution grant.
        validate_grant(plan,grant)
        if ((plan['runtime']=='python' and plan['requirements']) or plan['npm_lock']) and not grant['allow_install']:
            raise SafetyError('Discovered dependencies require explicit installation permission')
        self.plan,self.grant=plan,grant

    def __call__(self, source, workspace, owner, journal):
        before,files=workspace.snapshot(),inventory(workspace.root)
        environment=JournaledEnvironment(source,workspace,owner,self.plan,self.grant,journal).prepare()
        after=inventory(workspace.root)
        changed={name for name in files.keys()|after.keys() if files.get(name)!=after.get(name)}
        # Node may install only into its dependency directory; Python installs
        # outside the worktree. Neither may alter project files or Git metadata.
        allowed=all(name=='node_modules' or name.startswith('node_modules/') for name in changed)
        if workspace.snapshot()!=before or (changed and (self.plan['runtime']!='node' or not allowed)):
            raise SafetyError('Environment preparation changed files outside its dependency directory')
        return environment
