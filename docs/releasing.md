# Releasing (publishing the libraries)

This repo publishes five packages — four to **PyPI** and one to **npm** — via
`.github/workflows/release.yml`. Publishing is **opt-in and gated**: the workflow
only runs on a `vX.Y.Z` tag or a manual dispatch, and the manual dispatch
defaults to `dry-run` (build + validate, no upload). Nothing publishes until the
one-time setup below is done **and** you cut a tag.

## What ships
| Package | Registry | Install |
| --- | --- | --- |
| `agent-capture` (recorder SDK) | PyPI | `pip install agent-capture` |
| `agent-capture-reporter` | PyPI | `pip install agent-capture-reporter` |
| `agent-capture-ledger` | PyPI | `pip install agent-capture-ledger` |
| `agent-capture-enforcement` | PyPI | `pip install agent-capture-enforcement` |
| `@agent-capture/sdk` (TS recorder) | npm | `npm install @agent-capture/sdk` |

The ledger + enforcement *also* publish **container images** to GHCR on a tag —
that's their primary deploy unit (the PyPI packages are how their CLIs install
and how the images build):

```
ghcr.io/<owner>/agent-capture-ledger:X.Y.Z
ghcr.io/<owner>/agent-capture-enforcement:X.Y.Z   (+ :latest)
```
GHCR auth uses the workflow's built-in `GITHUB_TOKEN` — **no extra secret**. The
**first** push of each image is created `private`; set it `public` once in the
repo's *Packages* settings if vendors should pull it anonymously.

## One-time setup (you, the maintainer)
1. **Claim the names.** Register each PyPI project name and the npm scope
   (`@agent-capture`). Names are first-come on both registries.
2. **PyPI Trusted Publishing (recommended — no tokens in the repo).** For each
   PyPI project, add a *trusted publisher*: this repo, workflow `release.yml`,
   environment (leave blank unless you add one). Then the `Publish to PyPI` step
   authenticates via OIDC — no secret needed.
   - Alternative: create a PyPI API token and adapt the publish step to use it.
3. **npm token.** Create an npm automation token and add it as the repo secret
   **`NPM_TOKEN`** (the npm job reads it as `NODE_AUTH_TOKEN`).
4. **(Optional) extra gate.** Create a GitHub Environment named `release` with
   required reviewers and attach it to the `python`/`npm` jobs for a manual
   approval before any publish.

## Test the pipeline WITHOUT publishing
- **Locally (highest fidelity):**
  ```bash
  uv build --all-packages --out-dir dist     # build all 4 wheels + sdists
  uvx twine check dist/*                      # validate upload metadata
  python -m venv /tmp/v && /tmp/v/bin/pip install dist/agent_capture-*.whl
  /tmp/v/bin/python -c "import agent_capture; print(agent_capture.__version__)"
  ```
- **Dry run in CI:** Actions → *release* → *Run workflow* → `target = dry-run`
  (builds + `twine check`, uploads nothing).
- **TestPyPI dry run:** same, `target = testpypi` (requires a TestPyPI trusted
  publisher / token). Then `pip install -i https://test.pypi.org/simple/ agent-capture`.

## Cut a real release
1. Bump the version in **all** package manifests so they match the tag:
   `packages/python/pyproject.toml`, `packages/ledger/pyproject.toml`,
   `packages/reporter/pyproject.toml`, `packages/enforcement/pyproject.toml`,
   and `packages/typescript/package.json` (keep `__version__` constants in sync).
2. Move the `CHANGELOG.md` `[Unreleased]` entries under a new `## [X.Y.Z]` heading.
3. Commit, then tag and push:
   ```bash
   git tag vX.Y.Z && git push origin vX.Y.Z
   ```
4. The tag triggers `release.yml`, which builds all packages and publishes to
   **PyPI** + **npm**. Watch the run; verify `pip install agent-capture==X.Y.Z`.

> Versions are immutable on PyPI/npm — you cannot re-upload the same version.
> Bump-and-tag for any fix.
