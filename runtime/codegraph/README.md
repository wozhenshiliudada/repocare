# Optional CodeGraph runtime

Pinned actual upstream library: `@colbymchenry/codegraph@1.6.2` and the matching platform package. This is independent of runtime/pi and the default Python dependencies.

```powershell
npm --prefix runtime/codegraph ci --ignore-scripts --no-audit --no-fund
python maintain.py structure-build --repo CLEAN_REPO --spec EXPLICIT_SELECTION_JSON --output NEW_EXTERNAL_DIRECTORY
```

Requires Node22.19–24.x, existing Python dependencies and Git. Windows x64 platform content is approximately252.82MiB before adding the shim; packaging/licensing and the actual measurement are documented in [CODEGRAPH_READONLY](../../docs/digital-media-contest/CODEGRAPH_READONLY.md) and [provenance](../../third_party/codegraph/PROVENANCE.md).

The adapter runs real init/indexAll only against Python's new selected-source copy. It resolves absolute package paths inside this runtime, refusing NODE_PATH, parent packages and the SDK home-cache fallback. Telemetry/update/daemon environment switches are disabled; no global CLI install, agent configuration, MCP server or watcher is started. Query/verify read the completed export and pinned original/copy bytes without launching this extractor.

This is a CLI structure prototype. Pi, GUI, old approvals, source decision classification and durable budgets do not load this graph. Actual extraction/CLI evidence and remaining limitations are linked from the protocol document; no performance or real-model benefit is claimed.
