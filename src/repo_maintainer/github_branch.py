"""Create-only native Git transport through a sterile carrier and fixed GCM.

The original object store is an alternate for reading, never a Git config source.
Public operations accept GitHub HTTPS only. No branch update, delete or retry.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import tempfile

from .github_connection import GitHubConnection, github_url, branch_name
from .git_investigation import _capture
from .models import SafetyError, digest
from .recovery import unlinked, process_identity


def require(condition, message):
    if not condition:
        raise SafetyError(message)


def fields(repository, base, head):
    slug, canonical = github_url('https://github.com/'+repository)
    branch_name(base)
    require(isinstance(head, str) and re.fullmatch(r'integration/[a-f0-9]{32}', head),
            'Only the exact run-owned integration branch is publishable')
    require(base != head, 'Publication cannot target the base branch')
    return slug, canonical


def facts(result):
    return {key: result[key] for key in ('returncode', 'timed_out', 'truncated', 'receipt')} | {
        'stderr_sha256': digest(result['stderr'])}


class GitHubBranchTransport:
    def environment(self, objects=None):
        allowed = {'PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'COMSPEC', 'PATHEXT',
                   'HOME', 'USERPROFILE', 'LOCALAPPDATA', 'APPDATA', 'PROGRAMDATA', 'SYSTEMDRIVE',
                   'PROGRAMFILES', 'PROGRAMFILES(X86)', 'COMMONPROGRAMFILES',
                   'LANG', 'LC_ALL', 'LC_CTYPE', 'XDG_CONFIG_HOME',
                   'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY', 'SSL_CERT_FILE', 'SSL_CERT_DIR',
                   'GIT_TERMINAL_PROMPT', 'GIT_CONFIG_NOSYSTEM', 'GIT_CONFIG_GLOBAL', 'GIT_OPTIONAL_LOCKS'}
        env = {key: value for key, value in GitHubConnection().environment().items() if key.upper() in allowed}
        env.update(GCM_INTERACTIVE='never', GCM_GUI_PROMPT='false')
        if objects is not None:
            path = unlinked(Path(objects))
            require(path.is_dir() and os.pathsep not in str(path), 'Invalid original object store')
            env['GIT_ALTERNATE_OBJECT_DIRECTORIES'] = str(path)
        return env

    def command(self, directory, *args):
        return ['git', '-c', 'core.longpaths=true', '-c', 'core.hooksPath='+os.devnull,
                '-c', 'core.fsmonitor=false', '-c', 'credential.helper=manager',
                '-c', 'credential.interactive=never', '-c', 'http.followRedirects=false',
                '-c', 'http.sslVerify=true', '-c', 'http.extraHeader=',
                '-c', 'protocol.allow=never', '-c', 'protocol.https.allow=always',
                '-c', 'push.gpgSign=false', '-c', 'push.followTags=false',
                '-C', str(directory), *args]

    def _capture(self, directory, args, *, objects=None, stage=None, event=None):
        if event:
            event(stage+'_starting', None)
        def started(process):
            identity = process_identity(process.pid)
            if identity is None:
                # poll() checks the actual Popen child handle, not a recycled
                # PID. A fast init may have exited before psutil can inspect it.
                code = process.poll()
                require(code is not None, 'Git child process identity unavailable; preserve launch gap')
                identity = {'pid': process.pid, 'completed_before_identity': True, 'returncode': code}
            if event:
                event(stage+'_running', identity)
        result = _capture(self.command(directory, *args), directory, self.environment(objects),
                          max_bytes=65536, timeout=120, timeout_limit=120,
                          on_process=started if event else None)
        if event:
            event(stage+'_done', facts(result))
        require(result['returncode'] == 0 and not result['timed_out'] and not result['truncated'],
                'Git transport did not confirm success; preserve outcome without retry')
        return result

    def _initialize(self, directory, *, event=None):
        directory = unlinked(directory)
        require(not directory.exists(), 'Transport carrier already exists; never adopt or overwrite it')
        directory.mkdir()
        template = unlinked(directory/'empty-template')
        template.mkdir()
        self._capture(directory, ['init', '--bare', '--object-format=sha1', '--template='+str(template), 'carrier.git'],
                      stage='init', event=event)
        return unlinked(directory/'carrier.git')

    def refs(self, repository, base, head, owner):
        _, url = fields(repository, base, head)
        owner = unlinked(Path(owner))
        # A read-only query needs no source objects/config. Its temporary files
        # are exclusively created here and never share the publication carrier.
        with tempfile.TemporaryDirectory(prefix='remote-read-', dir=owner) as name:
            directory = self._initialize(Path(name)/'query')
            result = self._capture(directory, ['ls-remote', '--refs', '--', url,
                                                'refs/heads/'+base, 'refs/heads/'+head])
        result_refs = {}
        expected = {'refs/heads/'+base: 'base', 'refs/heads/'+head: 'head'}
        for line in result['stdout'].decode('ascii').splitlines():
            parts = line.split('\t')
            require(len(parts) == 2 and re.fullmatch('[a-f0-9]{40}', parts[0])
                    and parts[1] in expected and expected[parts[1]] not in result_refs,
                    'Malformed or unexpected remote reference')
            result_refs[expected[parts[1]]] = parts[0]
        require('base' in result_refs, 'Remote base branch is missing')
        return {'base': result_refs['base'], 'head': result_refs.get('head')}

    def push(self, repository, base, head, commit, objects, directory, event):
        _, url = fields(repository, base, head)
        require(isinstance(commit, str) and re.fullmatch('[a-f0-9]{40}', commit), 'Exact sealed SHA required')
        original_objects = unlinked(Path(objects))
        require(original_objects.is_dir(), 'Original object store unavailable')
        carrier = self._initialize(unlinked(Path(directory)), event=event)
        # Explicit empty expected value means the named ref MUST NOT EXIST.
        # One full SHA/refspec, no '+', tracking setup, tags, mirror or deletion.
        result = self._capture(carrier, ['push', '--porcelain', '--no-verify', '--no-follow-tags',
            '--recurse-submodules=no', '--force-with-lease=refs/heads/'+head+':',
            '--', url, commit+':refs/heads/'+head], objects=original_objects, stage='push', event=event)
        return facts(result)
