# Local approval UI dependency provenance

Reviewed on 2026-10-09 before implementation. Dependencies are used through public Python APIs, not copied framework code. Complete license files are copied byte-for-byte from installed official PyPI wheel distributions. `checksums.json` records their SHA-256 and the three direct dependency wheel digests; wheels themselves remain outside Git.

| Dependency | Pinned version | License | Actual use |
|---|---|---|---|
| [Starlette](https://github.com/Kludex/starlette) | 1.7.0 | BSD-3-Clause | ASGI routing, requests, responses and TestClient |
| [Uvicorn](https://github.com/Kludex/uvicorn) | 0.53.0 | BSD-3-Clause | Loopback HTTP server, without proxy headers or WebSockets |
| [HTTPX](https://github.com/encode/httpx) | 0.28.1 | BSD-3-Clause | TestClient only; Starlette emits a deprecation warning, tests still pass |

Direct checks: [Starlette license](https://github.com/Kludex/starlette/blob/main/LICENSE.md), [Uvicorn license](https://github.com/Kludex/uvicorn/blob/main/LICENSE.md), [Starlette PyPI](https://pypi.org/project/starlette/1.7.0/), [Uvicorn PyPI](https://pypi.org/project/uvicorn/0.53.0/), [HTTPX PyPI](https://pypi.org/project/httpx/0.28.1/).

This validation resolved these transitive distributions (evidence, not additional project pins): anyio 4.15.1 MIT; click 8.5.0 BSD-3-Clause; h11 0.16.0 MIT; typing_extensions 4.16.0 PSF-2.0; httpcore 1.0.9 BSD-3-Clause; idna 3.20 BSD-3-Clause; certifi 2026.7.22 MPL-2.0. Their complete wheel licenses are retained here too. HTTPX/httpcore/certifi are test dependencies, not a UI model HTTP implementation. New installations can resolve different compatible transitives.

Successful download source: `https://pypi.org/simple`, binary wheels only, workspace-only installation. Initial restricted-environment access to the configured mirror failed. SHA-256 identifies recorded bytes, not an independent publisher signature.

The project implements the browser capability boundary, serialized/idempotent jobs, two approvals, revision checks and plain JavaScript UI. Pi AgentSession and the existing Python Git/test/recovery workflows remain the execution core. Existing diff2html is reused with safe rendering; its provenance remains in `src/repo_maintainer/dashboard_assets/vendor/PROVENANCE.md`.

FastAPI (MIT) and Litestar (MIT) were evaluated as references but not installed, copied or invoked. Framework choice is an engineering decision, not a user-confirmed library selection. The project's own license remains undecided.
