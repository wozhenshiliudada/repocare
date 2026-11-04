"""Bounded filesystem evidence shared by repair and preparation recovery."""
import os
from pathlib import Path, PurePosixPath
from .models import SafetyError, canonical_path, file_digest
from .recovery import unlinked
from .maintenance_limits import LIMITS


def tracked_capacity(repository):
    """Read names/sizes before a paid plan; actual candidate inventory stays mandatory.

    Git checkout copies tracked files, not the source .git objects/ignored runtime.
    Reserve 4KiB for its .git marker. No blobs are read and no model is started.
    """
    names = [name.decode('utf-8') for name in repository.command('ls-files', '-z').split(b'\0') if name]
    total, entries = 4096, {'.git'}
    result = {'status': 'fits', 'tracked_files': len(names), 'entries_observed': 1,
              'bytes_observed': total, 'complete': False,
              'max_entries': LIMITS['workspace_max_entries'], 'max_bytes': LIMITS['workspace_max_bytes'],
              'runtime_authorized': False, 'model_calls': 0}
    if total > result['max_bytes']:
        result['status'] = 'capacity_blocked'
        return result
    for name in names:
        canonical_path(name)
        entries.add(name)
        entries.update(str(p) for p in PurePosixPath(name).parents if str(p) != '.')
        path = unlinked(repository.root / name)
        if not path.is_file():
            raise SafetyError('Candidate preflight needs regular tracked files')
        total += path.stat().st_size
        result.update(entries_observed=len(entries), bytes_observed=total)
        if len(entries) > result['max_entries'] or total > result['max_bytes']:
            result['status'] = 'capacity_blocked'
            return result
    result['complete'] = True
    return result


def inventory(root: Path) -> dict:
    """Bounded manifest includes ignored files, .git identity and empty directories."""
    result, total = {}, 0
    def failed(error):
        raise error
    for parent, directories, files in os.walk(unlinked(root), followlinks=False, onerror=failed):
        for name in sorted(directories + files):
            path = unlinked(Path(parent) / name)
            relative = path.relative_to(root).as_posix()
            if len(result) >= LIMITS['workspace_max_entries']:
                raise SafetyError("Candidate inventory exceeds its file limit; preserve it")
            if path.is_dir():
                result[relative] = {"directory": True}
            elif path.is_file():
                size = path.stat().st_size
                total += size
                if total > LIMITS['workspace_max_bytes']:
                    raise SafetyError("Candidate inventory exceeds its byte limit; preserve it")
                result[relative] = {"sha256": file_digest(path), "mode": path.stat().st_mode}
            else:
                raise SafetyError("Unknown candidate filesystem entry; preserve it")
    return result
