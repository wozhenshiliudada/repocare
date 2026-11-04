# Optional CodeGraph prototype provenance

- Real dependency: `@colbymchenry/codegraph@1.6.2`; npm gitHead `6560052a6f856855d3f71eee838fd66ccfa4285d`.
- Source: https://github.com/colbymchenry/codegraph/tree/6560052a6f856855d3f71eee838fd66ccfa4285d . Full upstream MIT copyright/license preserved in CODEGRAPH-LICENSE.
- Separate optional runtime: runtime/codegraph/package.json and package-lock.json; `npm ci --ignore-scripts --no-audit --no-fund`, no global installer/configuration.
- npm shim and matching Windows x64 platform package were installed. Lockfile preserves official tarball URLs and integrity. Platform metadata unpackedSize is 265095603 bytes; main shim 1208076 bytes. Installed regular-file content totals 266306633 bytes in this run (not disk allocation/download size).
- Adapter explicitly resolves this runtime's matching platform library, rather than npm-sdk's home-cache fallback. It calls real CodeGraph init/indexAll/getNodesInFiles/getOutgoingEdgesFrom on selected source copies only. No upstream class implementation is copied into RepoCare.
- RepoCare's own contribution: explicit bounded tracked-file selection, safe new artifact location, original Git/source binding, copied-byte checks, strict exported graph, query receipts and invalidation. Structural metadata is neither approval nor a confirmed policy decision.
- provenance.json lists 12 visible bundled dependency license files copied verbatim from the installed platform's lib/node_modules. These copies are evidence of preserved visible notices, not a completed audit of every grammar/native/Rust/Node binary in the upstream bundle. Complete redistribution/SBOM review remains pending.
- No speed/cost benefit is claimed from upstream benchmarks; this run used self-authored source and no model requests. Pi runtime and its lockfile were not changed.
