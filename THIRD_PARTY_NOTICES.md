# Third-party notices

YesCC itself is licensed under MIT; see `LICENSE`. This inventory covers the
runtime dependencies pinned in `uv.lock` for the 0.1.0rc3 wheel evaluation.
Dependencies are installed separately and are **not vendored in the YesCC wheel**.
Each dependency retains its own license; the YesCC MIT license does not replace it.

| Distribution | Reviewed version | License recorded in distribution |
| --- | --- | --- |
| anyio | 4.16.0 | MIT |
| certifi | 2026.10.23 | MPL-2.0 |
| h11 | 0.16.0 | MIT |
| httpcore | 1.0.9 | BSD-3-Clause |
| httpx | 0.28.1 | BSD-3-Clause |
| idna | 3.20 | BSD-3-Clause |
| platformdirs | 4.14.3 | MIT |
| tree-sitter | 0.26.0 | MIT (license file; metadata omits identifier) |
| tree-sitter-c | 0.24.2 | MIT |
| typing-extensions | 4.16.0 | PSF-2.0 (full bundled historical notices retained) |

The original installed license texts, package versions, source project links and
SHA-256 hashes are recorded by `bench/package_smoke.py` in the package evidence
under `docs/评测/`. CI checks the actual clean installation, including the presence
of license files and changes to the reviewed license identifiers. This inventory
is scoped to those exact runtime artifacts, not all possible versions allowed by
`pyproject.toml`, optional development tools, or future standalone executables.

Before distributing a standalone executable, collect the original notices and
license texts of the bundled Python runtime, libraries, freezer and data files.
Certifi includes a Mozilla-derived certificate bundle: retain its license and
provide the covered source as required by [MPL 2.0 §3](https://www.mozilla.org/en-US/MPL/2.0/).
Python's original historical notices must also be retained; see the
[Python license documentation](https://docs.python.org/3/license.html).
Standalone binary distribution remains a separate, unfinished release check.
