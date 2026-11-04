"""Non-following junction detection, including supported Python 3.11 hosts."""
from pathlib import Path
import stat


def is_junction(path: Path) -> bool:
    native = getattr(path, 'is_junction', None)
    if native is not None:
        return native()
    # Python 3.11 exposes the Windows reparse tag through lstat even though
    # pathlib has no is_junction method. Never resolve or read the target.
    try:
        entry = path.lstat()
    except (FileNotFoundError, NotADirectoryError):
        return False
    tag = getattr(entry, 'st_reparse_tag', None)
    # POSIX lstat has no reparse tag, and POSIX stat has no Windows constant.
    # Short-circuit before reading that constant on the Python 3.11 fallback.
    return tag is not None and tag == stat.IO_REPARSE_TAG_MOUNT_POINT
