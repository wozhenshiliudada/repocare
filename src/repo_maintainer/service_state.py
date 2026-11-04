"""Read-only startup recovery and a fingerprint of the code loaded by this service."""
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

from .dashboard import unlinked
from .models import SafetyError
from .runtime_limits import MAX_REQUESTS, MAX_OUTPUT_TOKENS, PLANNING_TIMEOUT_SECONDS
from .maintenance_limits import capacity_view
from .store import now


def saved_budget(root):
    path = unlinked(root / 'ui-state' / 'registry.sqlite3')
    if not path.is_file():
        raise SafetyError('No existing journal to resume; do not create a replacement budget')
    db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
    try:
        if db.execute('PRAGMA user_version').fetchone()[0] != 1 or db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise SafetyError('Existing journal is invalid')
        row = db.execute('SELECT ceiling,used,held FROM budget WHERE id=1').fetchone()
        metadata = json.loads(db.execute('SELECT body FROM metadata').fetchone()[0])
        if (not row or any(type(value) is not int or value < 0 for value in row)
                or not 1 <= row[0] <= MAX_REQUESTS or row[1] + row[2] > row[0]
                or metadata['provider']['request_budget'] != row[0]):
            raise SafetyError('Existing budget and scope disagree')
        return row[0]
    finally:
        db.close()


class RuntimeInfo:
    def __init__(self):
        self.package_root = Path(__file__).resolve().parent
        self.source_layout = self.package_root.parent.name == 'src'
        self.root = self.package_root.parents[1] if self.source_layout else self.package_root.parent
        self.started_at = now()
        from .pi_distribution import locate_runtime,PiDistributionError
        try:self.pi_runtime=locate_runtime(package_root=self.package_root,
                    repo_root=self.root if self.source_layout else None)
        except PiDistributionError:self.pi_runtime=None
        runtime_root=self.pi_runtime.root if self.pi_runtime else self.root/'runtime/pi'
        package = runtime_root/'node_modules/@earendil-works/pi-coding-agent/package.json'
        self.pi_package_version = json.loads(package.read_text())['version'] if package.is_file() else None
        self.loaded = self._fingerprint()

    def _fingerprint(self):
        files = list(self.package_root.glob('*.py'))
        files += [self.package_root/'pi_runtime_limits.json',
                  self.package_root/'maintenance_limits.json']
        runtime_root=self.pi_runtime.root if self.pi_runtime else self.root/'runtime/pi'
        files += list(runtime_root.glob('*.mjs'))
        files += [p for p in (runtime_root/'MANIFEST.json',runtime_root/'package.json') if p.is_file()]
        # A packaged Pi runtime participates in the same drift detection as
        # the source runtime. Older UI-only wheels may still lack it.
        lock = runtime_root/'package-lock.json'
        # Web tool adapters execute these trusted local entrypoints, never target-project scripts.
        for name in ('scripts/python_binding_check.py', 'runtime/codegraph/extract.cjs',
                     'runtime/codegraph/package-lock.json'):
            entry = self.root / name
            if entry.is_file():
                files.append(entry)
        if self.source_layout or lock.exists():
            files.append(lock)
        package = runtime_root/'node_modules/@earendil-works/pi-coding-agent/package.json'
        if package.is_file():
            files.append(package)
        for folder in ('local_ui_assets', 'workbench_assets'):
            files += [path for path in (self.package_root/folder).iterdir() if path.is_file()]
        frontend = self.package_root/'workbench_ui'
        if frontend.is_dir():
            files += [path for path in frontend.rglob('*') if path.is_file() and path.suffix in {'.html','.js','.css'}]
        help_assets=self.package_root/'help_assets'
        if help_assets.is_dir():
            files += [path for path in help_assets.iterdir() if path.is_file() and path.suffix in {'.md','.json'}]
        content = [[path.relative_to(self.root).as_posix(), hashlib.sha256(path.read_bytes()).hexdigest()]
                   for path in sorted(files)]
        return hashlib.sha256(json.dumps(content).encode()).hexdigest()

    def view(self):
        try:
            changed = self._fingerprint() != self.loaded
        except OSError:
            changed = True
        return {'build_sha256': self.loaded, 'started_at': self.started_at, 'code_changed': changed,
                'python': sys.version.split()[0], 'pi_expected_version': '1.0.4', 'pi_package_version':self.pi_package_version,
                'max_output_tokens': MAX_OUTPUT_TOKENS, 'planning_timeout_seconds': PLANNING_TIMEOUT_SECONDS,
                'default_request_budget': MAX_REQUESTS, 'revision_protocol': 1,
                'resource_limits':capacity_view(), 'limits_require_restart':True,
                'existing_budget_auto_increased':False}

    def require_current(self):
        if self.view()['code_changed']:
            raise SafetyError('Service code changed; restart with the existing journal before a new operation')
