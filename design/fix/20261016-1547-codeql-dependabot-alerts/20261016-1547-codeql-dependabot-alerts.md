# Fix: respond to the open CodeQL and Dependabot alerts — bump two transitive pins, pin third-party actions by commit, break the one real import cycle, and dismiss the false positives with written reasons

Written 2026-10-16 for https://github.com/benkozi/ufs-chem-assay/issues/30 ("Respond to codeql and dependabot alerts"). Security > Dependabot lists 10 open alerts, all in `uv.lock`; Security > Code scanning lists 53 open CodeQL alerts on `develop` (and one already fixed). Nothing is exploitable as the harness is used, but every alert is either a real pin to move, a real structural flaw, or a false positive that deserves a recorded reason rather than an open count that grows with each weekly scan.

# session

- **Session ID**: `b03367c8-266b-4687-b1c9-e7611c068512` (Claude Code, desktop app)
- **Resume Command**: `claude --resume b03367c8-266b-4687-b1c9-e7611c068512`

## Goal

1. **Dependabot: zero open alerts** on `develop` after the lock refresh lands.
2. **CodeQL: zero open alerts** on `develop`: every one is either fixed in code, fixed in the workflows, or dismissed in the UI as a false positive with a one-line reason that this document also records.
3. **No weakening of the scan**: no rule is disabled, no path is excluded; the CodeQL workflow is unchanged.
4. **The fixes are the kind that stay fixed**: SHA pins that Dependabot keeps current, an import graph that cannot re-grow the cycle without a test failing, and a module name that no tool can confuse with PyYAML.

## Pre-design audit (fresh facts, 2026-10-16, read with the user's new PAT via `gh api`)

### Dependabot (10 open, `repos/benkozi/ufs-chem-assay/dependabot/alerts`)

| Package | Locked | First patched | Alerts | Pulled in by |
|---|---|---|---|---|
| tornado | 6.5.7 | 6.5.9 | 6 (3 high, 2 medium, 1 low): GHSA-chx6-46f5-w4vp, GHSA-c2m8-h5v5-343r, GHSA-mpf4-983q-p7j4, GHSA-3hv7-mjh2-fv65, GHSA-8423-8fgw-73vq, GHSA-wwv5-g3v4-889x | `distributed` ← `dask[distributed]` (a runtime dependency) |
| virtualenv | 21.6.1 | 21.7.13 | 4 (3 high, 1 medium): GHSA-p58f-9548-mpm2, GHSA-x78j-v8h9-3j2q, GHSA-94p9-xgh2-xp45, GHSA-9h9j-4vrj-gf7g | `pre-commit` (the `dev` group only) |

1. Neither package is imported by the harness. tornado is the transport of dask's distributed scheduler, which the harness starts locally (no listening on a shared interface); the issues are DoS, decompression and symlink traversal in HTTP handlers. virtualenv is pre-commit's hook-environment builder; the issues are injection through activation scripts and unverified seed wheels. Exposure is the developer's machine and the CI container, not any service.
2. `uv lock --dry-run --upgrade-package tornado --upgrade-package virtualenv` resolves cleanly: tornado 6.5.7 → **6.5.10**, virtualenv 21.6.1 → **21.14.5**, and virtualenv's dependency python-discovery 1.4.4 → 1.6.3. Nothing else moves. The lock was not written.
3. Every CI job installs with `uv sync --frozen` (ci.yaml, integration.yaml, the Dockerfile), so the lock file is the only thing that has to change; the toolchain image rebuilds from it on the PR.
4. `.github/dependabot.yml` already has a weekly grouped `uv` updater (`chore(deps)` titles; the last grouped bump was PR #26). It would carry these two in its next run; this fix pre-empts it rather than waiting. Whether Dependabot *security* updates are enabled could not be read (the PAT gets 403 on `automated-security-fixes`); there are no open Dependabot PRs.

### CodeQL (53 open, `repos/benkozi/ufs-chem-assay/code-scanning/alerts`, all on `refs/heads/develop`)

5. **`actions/unpinned-tag` — 12 medium warnings.** Every one is a *third-party* action on a tag: `docker/setup-buildx-action@v4` (ci.yaml:36, :54, :115; integration.yaml:84), `docker/build-push-action@v7` (ci.yaml:38, :58, :119; integration.yaml:86), `astral-sh/setup-uv@v10.3.0` (integration.yaml:161), `python-semantic-release/python-semantic-release@v10` (semantic-release.yml:151, :170, :187). GitHub-owned actions (`actions/*`, `github/codeql-action/*`) on majors are not flagged by this rule. The one "fixed" alert (#9) was `setup-uv@v10.2.0`: Dependabot's weekly bump rewrote that line to `v10.3.0`, which closed #9 and opened #54 on the same step — an exact version tag does not satisfy the rule; only a commit hash does. Current tag → commit resolution (`git ls-remote --tags`, 2026-10-16):

   | Action | Tag today | Commit | Exact release at that commit |
   |---|---|---|---|
   | docker/setup-buildx-action | v4 | `f87e5991a6d7451dcb8d9637bfbc97413f497069` | v4.4.1 |
   | docker/build-push-action | v7 | `c3c9e263c25d99ce0380d002d59b67737d91b0dc` | v7.4.0 |
   | astral-sh/setup-uv | v10.3.0 | `1c37ad07a6a961277cf70c0d37d6f313000f5884` | v10.3.0 |
   | python-semantic-release/python-semantic-release | v10 | `b700dbeb1f431a0fcc30a79f3f0869407fe07278` | v10.7.0 |

   Dependabot's `github-actions` updater (already configured, weekly, grouped, `chore(ci)`) understands `uses: owner/repo@<sha> # vX.Y.Z` and bumps the hash and the comment together.
6. **`py/jinja2/autoescape-false` — 1 high error, `src/runner.py:82`.** `Environment(undefined=StrictUndefined, keep_trailing_newline=True).from_string(text)` renders `src/templates/driver-job.sbatch.j2`: a `#!/bin/bash` Slurm batch script (`#SBATCH` lines, `set -euo pipefail`, `module use {{ root_dir }}/modulefiles`, `srun … {{ command }}`). The inputs are the harness's own validated config, never user-facing HTML. Turning autoescape on would rewrite `&&`, `>`, `<` and quotes in `command` and `directives` as HTML entities and break every job script. A true positive for the rule's pattern; not a vulnerability.
7. **`py/unsafe-cyclic-import` — 13 errors, and `py/cyclic-import` — 22 notes.** All 35 come from two edges, and the messages name them:
   - **The lazy registry edge (the real one).** `RunConfig.checkout_dir(name)` (`src/cli/run_config.py:293-301`) does `from applications.registry import get_application` inside the method to look up the adapter's `checkout_dirname`. `applications/registry.py` imports `applications.cece.application`, which imports `cli.stages`, `combos`, and every module in `applications/cece/`; `cli.stages` imports `cli.run_config` at module level. So `cli.run_config → applications.registry → applications.cece.application → cli.stages → cli.run_config` is a cycle, and every `applications/cece/*` module sits on it. CodeQL counts the function-local import as an import-time edge. That edge alone accounts for: all 22 `py/cyclic-import` notes (#28–#49: `applications/cece/{application,assertions,cli,combos,config,settings,suite}.py`, `applications/registry.py:16-17`, `cli/run_config.py:296`) and the three `py/unsafe-cyclic-import` errors at `cli/stages.py:24` (#25–#27: `HARNESS_ROOT`, `RunConfig`, `search_path_value` "may not be defined if cli.run_config is imported before cli.stages"). At runtime the import happens when `checkout_dir` is *called*, after every module is fully loaded, so nothing fails today; the structure is still a cycle, and the registry docstring's own rule ("importing applications.base … never imports an adapter") is violated one level up, in the module that *every* adapter imports.
   - **The `TYPE_CHECKING` block in `applications/base.py:25-29`** imports `RunConfig`, `Stage`, `Combo`, `Dimension`, `ParameterRow`, `SuiteConfig` for the adapter's method signatures only; `from __future__ import annotations` keeps every use (lines 180, 222-265: annotations and a `ClassVar[type[SuiteConfig]]` annotation) unevaluated. mypy (strict, per design.md "Type checking") sees the names; the interpreter never executes the import. CodeQL's query does not honour the guard and reports the 2-cycles `applications.base ↔ cli.run_config`, `↔ cli.stages`, `↔ combos` as unsafe: #15–#19 (`applications/base.py:26-28`), #20 (`combos.py:24`), #21 (`cli/run_config.py:14`), #22–#24 (`cli/stages.py:23`). Ten alerts, all false positives by construction. (Curiously the fourth guarded import, `models.suite_config`, is not reported, so the query's exact criterion is not fully known; the implementation re-reads the PR's scan rather than predicting it.)
8. **`py/import-own-module` — 1 note, `src/models/yaml.py:13`.** The module is named `yaml.py` and does `import yaml` (PyYAML). Under pytest's `pythonpath = ["src"]` and the installed package, `yaml` resolves to PyYAML; CodeQL's extractor resolves it to the file itself. The same confusion is real for anyone running Python with `src/models` on `sys.path` (a script in that directory, an IDE "run file"). Importers: `src/cli/override.py:11` (`load_yaml`) and `src/applications/cece/config.py:12` (`StrictBoolLoader`); no test imports it by module path.
9. **`py/ineffectual-statement` — 2 notes, `src/applications/base.py:104, 107`.** The `...` bodies of `DriverConfig.from_yaml` / `to_yaml` (`@abstractmethod`). They are the only two `: ...` bodies in the file; every other abstract method in `base.py` has a docstring body.
10. **`py/empty-except` — 1 note, `.github/workflows/scripts/report_release.py:156`.** `except OSError: pass` around the `pyproject.toml` fallback read in the version probe: intentional (no pyproject here → the function returns `"None"`), but uncommented. Ruff's SIM105 does not fire because the `try` body is more than one statement.
11. **`py/unused-global-variable` — 1 note, `src/tests/conftest.py:43`.** `_TESTS_ROOT = Path(__file__).resolve().parent` is never read (the comment above it explains a path the code no longer takes). vulture is in the dev group but did not report it; not investigated.
12. **The CodeQL workflow** (`codeql.yaml`): `security-and-quality` query suite, languages `python` + `actions`, on PRs, pushes to develop/main, and weekly. No `config-file`, no `.github/codeql/`. Alerts are keyed by fingerprint, so a UI dismissal survives line moves and re-analysis; a dismissed alert reopens only if the code at that location changes materially.
13. **Local state**: `develop` at `c1479f8` (`0.1.0-rc.7`), clean. `develop` is protected (PR required), so the change goes in on a branch; `fix:` in the PR title releases `0.1.0-rc.8` (precedent: PR #26 `fix(deps): …`). The user commits and pushes.

## Design

### 0. Decisions

1. **Refresh the two pins now** with `uv lock --upgrade-package tornado --upgrade-package virtualenv` (exactly the dry run in audit 2). Not `uv lock --upgrade` (the weekly grouped PR is the place for everything else) and not a `pyproject.toml` floor (neither package is a direct dependency; a floor would be a lie about what the harness needs).
2. **Pin the 12 third-party action references to full commit SHAs with a `# vX.Y.Z` trailing comment** (audit 5's table). GitHub-owned actions stay on major tags: the rule does not flag them, Dependabot's existing comment describes exactly that policy, and mixing styles per owner is a deliberate line ("third-party = hash") rather than drift. Dependabot keeps the hashes current.
3. **Break the real cycle by giving `RunConfig.checkout_dir` the adapter instead of its name.** `checkout_dir(self, app: Application) -> Path` returns `self.applications[app.name].clone_dir or self.root_dir / app.checkout_dirname`; the lazy import of `applications.registry` goes away and `cli.run_config` imports nothing from `applications` but `applications.base` (already at module level). Every caller already holds the adapter (audit: `cli/stages.py:120` has `app`; `applications/cece/cli.py` is called from `CeceApplication.stage_lines`, which is `self`). A new test pins the graph: importing `cli.run_config` in a fresh interpreter must not load `applications.registry` or any `applications.cece` module.
4. **Keep the `TYPE_CHECKING` block in `applications/base.py` as it is** and dismiss whatever CodeQL still reports there after decision 3 as *false positive*, with the reason "import is under `if TYPE_CHECKING:`; names are used in annotations only (`from __future__ import annotations`); mypy strict enforces the guard". Considered and rejected: moving the shared types into a leaf module (large churn to silence a tool that is wrong), string-quoting the annotations instead of importing (mypy loses the names), and excluding the query in a CodeQL config (hides the next real cycle; decision 3 shows the query earns its keep).
5. **Dismiss the Jinja2 alert as false positive** with the reason "renders a bash/Slurm batch script, not HTML; autoescape would corrupt shell operators; inputs are the harness's validated config". Add a two-line comment at the `Environment(...)` call saying the same, so the next reader (and the next scan reviewer) does not have to rediscover it. Considered and rejected: `autoescape=select_autoescape(default=False)` — it would satisfy the query's pattern while changing nothing, which is the dishonest kind of fix.
6. **Rename `src/models/yaml.py` → `src/models/strict_yaml.py`** (it is the strict-boolean YAML loader; the name says so) and update the two importers. Considered and rejected: dismissing the alert (the shadowing hazard in audit 8 is real for humans, not only for CodeQL).
7. **Give the two `DriverConfig` abstract methods one-line docstring bodies** in place of `...`, matching every other abstract method in the file.
8. **Comment the empty `except`** in `report_release.py`: `# no pyproject.toml in cwd (or unreadable): fall through to "None"`. No behaviour change.
9. **Delete `_TESTS_ROOT`** and its comment from `conftest.py`.
10. **No change to `codeql.yaml`, no CodeQL config file, no `# codeql[...]`/`# lgtm[...]` suppression comments.** The two dismissal classes (decisions 4, 5) are the only alerts that outlive this fix, and the UI records who dismissed them and why; this document is the durable copy.

### 1. `uv.lock`

```bash
uv lock --upgrade-package tornado --upgrade-package virtualenv
```

Expected diff: tornado 6.5.7 → 6.5.10, virtualenv 21.6.1 → 21.14.5, python-discovery 1.4.4 → 1.6.3, and nothing else (if anything else moves, stop and look: the dry run on 2026-10-16 moved exactly these three). `uv sync --frozen` afterwards; the harness suite runs against the new scheduler build.

### 2. Workflows — third-party actions on hashes

Each flagged line becomes `uses: <owner>/<repo>@<sha> # <exact version>`; the hashes and versions are audit 5's table, re-resolved at implementation time in case a release landed in between (`git ls-remote --tags https://github.com/<owner>/<repo>` and match the peeled `^{}` SHA of the major tag against the exact-version tags):

- `ci.yaml:36, :54, :115` → `docker/setup-buildx-action@f87e5991a6d7451dcb8d9637bfbc97413f497069 # v4.4.1`
- `ci.yaml:38, :58, :119` and `integration.yaml:86` → `docker/build-push-action@c3c9e263c25d99ce0380d002d59b67737d91b0dc # v7.4.0`
- `integration.yaml:84` → `docker/setup-buildx-action@f87e5991a6d7451dcb8d9637bfbc97413f497069 # v4.4.1`
- `integration.yaml:161` → `astral-sh/setup-uv@1c37ad07a6a961277cf70c0d37d6f313000f5884 # v10.3.0`
- `semantic-release.yml:151, :170, :187` → `python-semantic-release/python-semantic-release@b700dbeb1f431a0fcc30a79f3f0869407fe07278 # v10.7.0`

`.github/dependabot.yml`'s header comment gains the policy in one clause: third-party actions are pinned by commit (Dependabot bumps the hash and its `# vX.Y.Z` comment together); GitHub-owned actions stay on majors. yamlfix/yamllint (pre-commit) must accept the trailing comments; actionlint in CI's container validates the `uses:` syntax.

### 3. `cli/run_config.py` and its callers — the cycle

```python
# cli/run_config.py (imports: Application joins ApplicationRunSection from applications.base)
    def checkout_dir(self, app: Application) -> Path:
        """The application's checkout: its section's clone_dir, else
        <root_dir>/<the adapter's checkout_dirname>."""
        section = self.applications[app.name]
        return section.clone_dir or self.root_dir / app.checkout_dirname
```

The function-local `from applications.registry import get_application` is deleted; `application_names` and the rest are untouched. Callers:

- `cli/stages.py:120`: `config.checkout_dir(app)`.
- `applications/cece/cli.py`: `stage_lines(stage, config, section)` computes the checkout once — it needs the adapter, so `CeceApplication.stage_lines` passes `self` and `stage_lines` gains an `app: Application` parameter (or, equivalently, the adapter computes `config.checkout_dir(self)` and passes `checkout: Path`; the implementation picks whichever reads better at the three sites `:46`, `:53`, `:82`, where `config.checkout_dir("cece")` becomes the local `checkout`). The literal `"cece"` disappears from the module, which is a small correctness gain of its own: the adapter's `name` is the only spelling.
- Tests: `test_run_config.py:29, :126, :175` pass the registered cece adapter (`get_application("cece")` — the test module may import the registry; the test is a leaf); `:325` passes the stub adapter from `stubs.py` (`checkout_dirname = "STUB"`); `test_stages.py:71, :93, :117, :135, :184` likewise.
- **New test** (`src/tests/ufs_chem_assay/test_import_graph.py`): in a subprocess with `PYTHONPATH=src`, build a `RunConfig` with the stub adapter's section, call `checkout_dir(StubApplication())`, then print the sorted entries of `sys.modules` that are `applications.registry` or start with `applications.cece`; the test asserts the list is empty. A plain `import cli.run_config` is already clean today (the import is lazy); the *call* is what loads the registry, so the test is red before the fix and green after, and it stays the guard against the edge coming back by any route.

Expected CodeQL effect: the 22 `py/cyclic-import` notes and `py/unsafe-cyclic-import` #25–#27 close on the PR's scan. The ten `TYPE_CHECKING` ones (#15–#24) are expected to remain → decision 4.

### 4. `src/runner.py:82` — Jinja2

```python
    # Not HTML: the template is a bash/Slurm batch script and the values are
    # the harness's own validated config. Autoescaping would corrupt shell
    # operators and quotes; CodeQL's py/jinja2/autoescape-false is dismissed
    # as a false positive for this site (design/fix/20261016-1547-…).
    template = Environment(
        undefined=StrictUndefined, keep_trailing_newline=True
    ).from_string(text)
```

Then the user dismisses alert #13 in Security > Code scanning: reason **False positive**, comment as in decision 5.

### 5. `src/models/yaml.py` → `src/models/strict_yaml.py`

`git mv`; `from models.strict_yaml import load_yaml` in `cli/override.py`, `from models.strict_yaml import StrictBoolLoader` in `applications/cece/config.py`. The module docstring is already the right description. `design.md`'s code layout (if it lists the file) and any README mention follow during implementation.

### 6. `src/applications/base.py:104, 107`

```python
    @classmethod
    @abstractmethod
    def from_yaml(cls, path: Path) -> Self:
        """Parse and validate the driver's YAML at path."""

    @abstractmethod
    def to_yaml(self, path: Path) -> None:
        """Write the validated config as the driver's YAML at path."""
```

### 7. `report_release.py:156` and `conftest.py:43`

The `except OSError:` gets the comment from decision 8 above the `pass`; the `_TESTS_ROOT` assignment and its two comment lines are deleted.

### 8. Dismissals (user, in the GitHub UI, after the PR's CodeQL run)

| Alerts | Reason | Comment |
|---|---|---|
| #13 `py/jinja2/autoescape-false` | False positive | Renders a bash/Slurm batch script, not HTML; autoescape would corrupt shell operators; inputs are validated harness config. See design/fix/20261016-1547-codeql-dependabot-alerts. |
| whichever of #15–#24 `py/unsafe-cyclic-import` remain | False positive | Import is under `if TYPE_CHECKING:`; the names are used only in annotations (`from __future__ import annotations`); mypy strict enforces the guard. Same design doc. |

Alerts on `develop` close on their own once the fixing commit is on `develop` (the merge); dismissals can be done on the PR's alerts and carry over by fingerprint.

### 9. `design.md` (implementation phase)

- **CI and releases**: one bullet — third-party actions are pinned by commit with a version comment that Dependabot maintains; GitHub-owned actions on majors; CodeQL's two standing false-positive classes and where their reasons live (this document).
- **Code layout**: `models/strict_yaml.py` replaces `models/yaml.py`; and the import rule in one sentence: `cli.run_config` takes the adapter where it needs adapter facts and never imports the registry, which keeps `cli.*` ← `applications.cece.*` one-directional. Pointer to this document.

## Implementation plan

1. Branch `fix/codeql-dependabot-alerts` from `develop`.
2. TDD for the cycle (§3): the import-graph test first → red; `checkout_dir(app)` and the callers → green; existing `test_run_config` / `test_stages` cases updated to pass adapters.
3. Rename (§5), docstrings (§6), comments and deletion (§7), the Jinja2 comment (§4).
4. Workflows (§2), `dependabot.yml` comment.
5. `uv lock --upgrade-package …` (§1), `uv sync --frozen`.
6. `design.md` (§9); README only if it names `models/yaml.py`.
7. `uv run pytest src/tests/ufs_chem_assay --cov` (100 % stays 100 %), `uv run pre-commit run --all-files`, actionlint in CI's container (`ci.yaml`'s pre-commit job runs it on the PR anyway), `uv run ufs-chem-assay … --dry-run` for every suite (the stage scripts are the deliverable the `checkout_dir` change touches).
8. PR titled `fix: respond to CodeQL and Dependabot alerts` with `Closes #30`; the user commits, pushes, opens it.
9. After the PR's `codeql` check: compare the PR's alert list with §8's expectation; dismiss per §8. After merge: Security tab shows 0 open Dependabot, 0 open CodeQL on `develop`.

## Verification

- `gh api --paginate 'repos/benkozi/ufs-chem-assay/dependabot/alerts?state=open'` → `[]` once the lock is on `develop`.
- `gh api --paginate 'repos/benkozi/ufs-chem-assay/code-scanning/alerts?state=open'` → `[]` on `develop`; `state=dismissed` lists exactly #13 plus the remaining `TYPE_CHECKING` set, each with `dismissed_reason: "false positive"` and a comment.
- The import-graph test is the regression guard for the cycle; `uv run ufs-chem-assay … --dry-run` output for the Ursa and local run configs is byte-identical before and after (the rendered `CECE_ROOT_DIR` export and the `module use`/clone lines come from the same values by a different route).
- CI green on the PR with the hashed actions (the buildx/build-push steps and the release preview run the pinned code).

## Risks and notes

- **Hash pins make the release action's version explicit** (`v10.7.0`): a python-semantic-release patch release no longer arrives silently; it arrives as Dependabot's weekly `chore(ci)` PR. That is the point, and it is the same cadence the `uv` group already has.
- **`checkout_dir` signature change** is internal (no `[project.scripts]` entry or README API exposes it) but touches two adapters' worth of call sites and nine test lines; the dry-run byte comparison in Verification is what proves nothing moved.
- **The TYPE_CHECKING dismissals** are the one place this fix accepts a standing disagreement with the scanner. If a future CodeQL pack learns the guard, the alerts close on their own; if it instead reports *new* guarded imports elsewhere, the same reason applies and should be dismissed the same way, not worked around in code.
- tornado 6.5.10 and virtualenv 21.14.5 are well past the first-patched versions; the dry run chose the newest compatible, which is also what the weekly PR would pick.

## Open questions

None blocking. One judgement call surfaced in decision 2 (pin all actions vs third-party only); the design picks third-party only and says why. If the user prefers one style for every `uses:`, the GitHub-owned ones (`actions/checkout@v7`, `actions/cache/*@v6`, `actions/upload-artifact@v7`, `actions/create-github-app-token@v3`, `actions/dependency-review-action@v5`, `github/codeql-action/*@v4`) get the same treatment in the same PR and nothing else changes.

## Future work

- Enable ruff's `TC` rules (`flake8-type-checking`) so that typing-only imports are *required* to live under the guard; it makes decision 4's premise machine-checked rather than a convention.
- If Dependabot security updates turn out to be disabled for the repository (audit 4 could not read the setting), enable them: a security alert then gets its own PR the day it is published instead of waiting for the weekly group.

# appendix: original notes

## always do

- include updating design.md as part of the implementation
- update the harness tests (`src/tests/ufs_chem_assay`) in addition to any changes to test_driver_combos.py
- update README.md with any necessary documentation changes in case of an api adjustment
- use pydantic models as opposed to dataclasses
  - all pydantic fields should include a description like `... = Field(description="<description content here>", ...`
- do *not* add driver bugs to known bugs in `README.md` unless explicitly told to do so
- use a test-driven development, red-green-refactor approach for all fixes and features (when possible)
- maintain original design sections when refining design docs - create an appendix
  - summarize conversational updates in the appendix following original refinement target
  - refinements never create files. they only edit the target document that is being refined.
  - `design.md` may be updated during the _implementation_ phase only.
- when using python `typing`, avoid `Any` as much as possible
- **never, ever, ever** commit code or use git actions that perform updates - the user always updates
- always prefer the harness's logging implementation over raw python print statements
- no need for wrapping when writing to the `./design` folder. wrapping will be handled by the user's ide
- persist the agy session id and resume command in any design/refined files for resumability

## session

- **Session ID**: `b03367c8-266b-4687-b1c9-e7611c068512` (Claude Code, desktop app)
- **Resume Command**: `claude --resume b03367c8-266b-4687-b1c9-e7611c068512`

### testing

- not necessary for design documents in the `spike` folder - code *should not* change for spikes
- *all* suites should pass `--dry-run`
- run `simple-maccity-suite.yaml` without `--dry-run` for integration testing with the driver
- only run examples when requested to do so
- no need to run tests for spikes/documentation-only tasks
- pre-commit hooks pass

## requirements

User request (2026-10-16, verbatim): "i update my personal access token. can you read dependabot and codeql alerts with gh cli? https://github.com/benkozi/ufs-chem-assay" and then "okay, let's create fix design document referencing this issue: https://github.com/benkozi/ufs-chem-assay/issues/30. no implementation yet."

## conversational updates

- 2026-10-16 (creation): the alert inventory was read in the session before writing; no refinement round requested yet.
