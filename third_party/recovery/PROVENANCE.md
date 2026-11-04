# Recovery dependencies

2026-10-08: upstream GitHub and licenses were checked before adding recovery.
These are imported dependencies, not vendored implementations. Full licenses
from the installed official wheels are retained here.

| Component | Version | Upstream | License | Local wheel SHA256 |
|---|---|---|---|---|
| filelock | 4.0.12 | https://github.com/tox-dev/filelock | MIT | 5f17ee83ecee8a6f3e389c75822fb70a1c2f0506b99438a6dffa1d56793588c8 |
| psutil | 7.2.2 | https://github.com/giampaolo/psutil | BSD-3-Clause | eb7e81434c8d223ec4a219b5fc1c47d0417b12be7ea866e24fb5ad6e84b3d988 |

Artifacts: `filelock-4.0.12-py3-none-any.whl` and
`psutil-7.2.2-cp37-abi3-win_amd64.whl`, downloaded from official PyPI.
The latter is the Windows x64 artifact used locally; Linux CI chooses its own
platform wheel. These checksums document local provenance, not a cross-platform
dependency lock or proof of remote CI execution.

Local validation installed into a workspace-only dependency directory, without
changing the user's global Python environment. Reproduction installs pinned
dependencies with `python -m pip install -e .` (prefer a new virtual environment).
No third-party release lifecycle scripts were run for these wheel installs.

filelock supplies native Windows/POSIX locks. The adapter explicitly disables
soft fallback and keeps the lock file on release to avoid splitting POSIX lock
ownership across different inodes. psutil is used only to inspect test PID and
creation time; the recovery command does not kill processes.

Pi durable and Git Town were read as design references; no source from them was
copied. Existing Pi AgentSession remains the agent core.
