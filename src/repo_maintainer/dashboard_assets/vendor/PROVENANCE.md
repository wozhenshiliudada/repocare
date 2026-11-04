# Vendored browser diff renderer

- Upstream: https://github.com/rtfpessoa/diff2html
- Version: 3.4.56; npm gitHead 41fdbdb9cb226a88b5eeafe5231121a7eea91983.
- Retrieved 2026-10-08 using npm pack --ignore-scripts. No lifecycle scripts executed.
- npm tarball integrity verified: sha512-u9gfn+BlbHcyO7vItCIC4z49LJDUt31tODzOfAuJ5R1E7IdlRL6KjugcB9zOpejD+XiR+dDZbsnHSQ3g6A/u8A==
- diff2html.min.js SHA256 a2110a09cee157bd5466da77be02107ac81a0baa2bc1f3fe81aac8183314598e.
- diff2html.min.css SHA256 d3ecc0e9b2b1e5c8466c19de29bed052fd0863475d25829ecc858446efded372.
- These two files are unchanged published browser bundles, not built locally.
- Licenses: diff2html MIT, bundled diff BSD-3-Clause, bundled Hogan Apache-2.0. Notices and complete license text are included both here and in exported HTML.
- Additional license copies were extracted from diff@8.0.3 and @profoundlogic/hogan@3.0.4. These satisfy the declared dependency ranges; exact bundle-internal dependency versions were not independently established.
- This package is used only for read-only diff display. The browser wrapper allowlists generated DOM elements/attributes. No CDN, telemetry or runtime network requests are introduced.
