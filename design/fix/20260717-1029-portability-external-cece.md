# Fix: `CECE_ROOT_DIR` for the externalized CECE checkout

## Goal

The runner now lives in its own repository, separate from the CECE driver
repo. Replace the derive-from-this-checkout `root` setting with an explicit
`root_dir` setting that points at the CECE repository root on the host — the
directory that maps to `/work` in `docker run`. It is provided by an
optional `--cece-root-dir` pytest flag, falling back to the
`CECE_ROOT_DIR` environment variable when the flag is absent. Driver
execution **requires** one of the two; there is no longer any sensible
default to derive.

## Pre-design audit of the current root handling

1. **The default is now silently wrong.** `settings.py:7` computes
   `_REPO_ROOT = Path(__file__).resolve().parents[2]` with a comment
   ("CECE repo root is two levels up from src/") that was true only when
   the runner lived inside the CECE repo. In this repository the same
   expression resolves to the *parent of the runner checkout*
   (`~/sandbox/git-benkozi`), so an unconfigured run would mount an
   unrelated directory tree at `/work` — a wrong-answer default, worse
   than no default.
2. **Two consumers of `settings.root`**, both host-side:
   - `runner.py:41` — the docker bind mount `f"{settings.root}:/work"`.
   - `conftest.py:114` via `resolve_output_roots(option, settings.root)`
     ([resolution.py:14](src/resolution.py)) — an explicit relative
     `--combo-output-root` resolves to `<root>/<relative>` on the host so
     it lands inside the `/work` mount.
3. **Nothing else derives paths from the checkout location.** Suite/config
   lookup goes through `config_search_path` / `suite_config_search_path`;
   `driver_path` (`./build/cece_standalone_driver`) and generated config
   paths are container-side, relative to `/work`, and stay correct as long
   as `/work` is the CECE root.
4. **Existing touch points to migrate**: the dry-run harness test sets
   `CECE_ROOT` ([test_dry_run.py:18](src/tests/ufs_chem_assay/test_dry_run.py:18));
   unit tests construct `Settings(root=...)`
   ([test_runner.py:14](src/tests/ufs_chem_assay/test_runner.py:14),
   [test_maccity_pipeline.py:26](src/tests/ufs_chem_assay/test_maccity_pipeline.py:26));
   `README.md` and `design.md` document `CECE_ROOT` in their env-var
   tables and the docker-invocation section.

## Design

### Settings: `root` → `root_dir`, no default

In `Settings` ([settings.py](src/settings.py)):

- Rename the field `root` to `root_dir`, so the `CECE_` prefix yields
  **`CECE_ROOT_DIR`** (naming also now matches `baseline_root_dir`).
- Type `Path | None = Field(None, description=...)` — unset means "not
  configured", never a guessed path. Delete `_REPO_ROOT` and its stale
  comment.
- The description states the contract: host path of the CECE repository
  root; mounted at `/work` in the driver container; required to execute
  the driver.
- Add `frozen=True` to `SettingsConfigDict`. `Settings` is constructed
  once at sessionstart and passed around as read-only configuration; the
  flag wiring below deliberately resolves everything at construction, and
  freezing makes that single-resolution-point guarantee structural rather
  than conventional. (Audited: all existing code and tests construct
  `Settings(...)` with kwargs; nothing assigns to fields
  post-construction, so freezing breaks no current usage.)

`Path | None` rather than a required field keeps `Settings()`
constructible without environment setup, which pure unit tests and
docker-free `--dry-run` sessions rely on. Requiredness is enforced at the
points that actually need the value (below), with clear errors, instead of
at import time everywhere.

### CLI flag: `--cece-root-dir` (overrides the env var)

New optional pytest flag in `pytest_addoption`
([conftest.py](src/tests/conftest.py)), beside `--suite-config` /
`--combo-output-root`:

- `--cece-root-dir=PATH` — host path of the CECE repository root.
  Precedence: **flag > `CECE_ROOT_DIR` env var > unset**.
- Wiring uses pydantic-settings' native precedence (init kwargs beat env
  vars) rather than post-construction mutation: `pytest_sessionstart`
  builds `Settings(root_dir=option)` when the flag is given and a plain
  `Settings()` otherwise. `root_dir` stays the single source of truth —
  every downstream consumer keeps reading `settings.root_dir` and never
  sees the flag, so there is exactly one resolution point.

### Fail fast, before anything runs

Both checks live in [conftest.py](src/tests/conftest.py), run against the
resolved `root_dir` (whichever source supplied it), and raise
`pytest.UsageError` naming both `--cece-root-dir` and `CECE_ROOT_DIR`:

- **Driver execution**: `root_dir` must be set and be an existing
  directory. This check runs in `pytest_collection_modifyitems`, **only
  when a collected item requests `driver_run`** and `--dry-run` is off —
  not unconditionally in `pytest_sessionstart`, because the root conftest
  loads for every pytest invocation, including docker-free harness-only
  runs (`pytest src/tests/ufs_chem_assay`) that must stay green with no
  environment. Collection time is still before any test executes, so the
  fail-fast property holds — and it beats an obscure docker mount error
  three fixtures deep.
- **Explicit `--combo-output-root`**: resolving it requires the root
  (audit #2), including under `--dry-run` (the artifacts land at
  `<root_dir>/<relative>` on the host). This check stays in
  `pytest_sessionstart`, where the option is resolved. Unset `root_dir`
  with this option is a `UsageError`; `resolve_output_roots` itself keeps
  its pure `(option, cece_root)` signature and stays None-free.

A bare `--dry-run` with the default pytest-tmp output root therefore still
passes with **no environment at all** — config generation and artifact
checks need no CECE checkout, and "*all* suites should pass `--dry-run`"
stays true on machines without one. Likewise the harness-only unit suite
runs with no environment.

### Migration of existing usage

- `runner.py:41` mount becomes `f"{settings.root_dir}:/work"` (behavior
  unchanged; `build_command` is only reached when the sessionstart check
  has already guaranteed a set, existing `root_dir`).
- `conftest.py:114` passes `settings.root_dir` after the None check.
- Tests: `CECE_ROOT` → `CECE_ROOT_DIR` in the dry-run harness env;
  `Settings(root=...)` → `Settings(root_dir=...)` in unit tests.
- Local/integration setup for this machine: either
  `CECE_ROOT_DIR=/Users/bkoziol/sandbox/git-benkozi/CECE` in the
  environment or `--cece-root-dir=/Users/bkoziol/sandbox/git-benkozi/CECE`
  per invocation. (Optional `.env`-file support via
  `SettingsConfigDict(env_file=...)` is noted as a possible later
  convenience, not built here.)

## TDD plan (red first)

- `Settings` unit tests: `CECE_ROOT_DIR` env var populates `root_dir`;
  unset env → `root_dir is None`; an explicit `Settings(root_dir=...)`
  beats a set `CECE_ROOT_DIR` (the precedence the flag wiring relies on);
  the old `CECE_ROOT` variable no longer has any effect; assigning to a
  field on a constructed `Settings` raises (frozen).
- `build_command` tests updated to construct `Settings(root_dir=...)` and
  keep asserting the `<root_dir>:/work` mount verbatim.
- Sessionstart guards, via pytest subprocess (same harness style as the
  dry-run test, no docker):
  - no `--dry-run`, `CECE_ROOT_DIR` unset → immediate `UsageError`
    mentioning `CECE_ROOT_DIR`; no artifacts generated.
  - no `--dry-run`, `CECE_ROOT_DIR` set to a nonexistent path → same.
  - `--dry-run` + `--combo-output-root=combo_runs`, `CECE_ROOT_DIR` unset
    → `UsageError`.
  - `--dry-run` with default output root and **no** `CECE_*` env → passes.
  - `--cece-root-dir` satisfies the requirement with no env var set; with
    **both** given, artifacts land under the flag's path (flag wins).
- Existing dry-run harness test switches its env key to `CECE_ROOT_DIR`
  and stays green.

Integration: `simple-maccity-suite.yaml` without `--dry-run`, with
`CECE_ROOT_DIR=/Users/bkoziol/sandbox/git-benkozi/CECE`, per the standing
testing rules.

## Ripples (standing process rules)

- **`design.md`**: settings table row `root` → `root_dir` /
  `CECE_ROOT_DIR` with "unset — required to run the driver" as the
  default; `--cece-root-dir` (with its flag-over-env precedence) in the
  pytest-options list; the docker-invocation section's "the `root`
  setting, `CECE_ROOT`" wording; a sentence that the runner is a separate
  repository and `/work` is the *external* CECE checkout.
- **`README.md`**: env-var table row `CECE_ROOT` → `CECE_ROOT_DIR`
  (default "unset — required for driver execution");
  `--cece-root-dir` added to Options; prerequisites clarified — the
  driver image/build live in the CECE checkout that `root_dir` points at,
  not in this repo; a setup example `export CECE_ROOT_DIR=/path/to/CECE`
  (or the flag equivalent). This is an API adjustment, so the README
  update is mandatory.
- Pydantic field with description; TDD red-green; harness tests
  updated alongside any `test_driver_combos.py`-facing change.

## Acceptance criteria

- The host CECE root — what `/work` maps to in `docker run` — comes from
  `--cece-root-dir` when given, else `CECE_ROOT_DIR`; the flag wins when
  both are set, and no other source configures it.
- With neither provided: bare `--dry-run` (default output root) passes
  with no environment; any driver-executing session or explicit
  `--combo-output-root` fails immediately with a `UsageError` that names
  `--cece-root-dir` and `CECE_ROOT_DIR`.
- No code path derives the CECE root from this checkout's location
  (`_REPO_ROOT` is gone), and `Settings` is frozen — the root cannot be
  reassigned after sessionstart resolves it.
- Full unit/harness suite green without docker; integration run of
  `simple-maccity-suite.yaml` green with
  `CECE_ROOT_DIR=/Users/bkoziol/sandbox/git-benkozi/CECE`.
- `design.md` and `README.md` reflect the rename and the external-checkout
  prerequisite.

## Implementation notes

- **`ruff.toml` was also broken by the repo move**: it extended
  `../ruff.toml` (the CECE repo root config), which no longer exists above
  this repository, so ruff could not run at all. The parent's only content
  was spack-specific per-file-ignores irrelevant here; `ruff.toml` is now
  self-contained (`target-version = "py311"`). Pre-existing formatting
  drift in three untouched files (`comparison.py`, `plotting.py`,
  `test_comparison.py`) was left out of this change and surfaced as a
  separate task.
- **Integration verified** against
  `CECE_ROOT_DIR=/Users/bkoziol/sandbox/git-benkozi/CECE`: 18/18 tests of
  `simple-maccity-suite.yaml` pass with the real driver in docker. The
  baseline-comparison tests additionally need
  `CECE_BASELINE_ROOT_DIR=/Users/bkoziol/Library/CloudStorage/Dropbox/rlps/rsandbox/cece-baselines`
  on this machine (the pre-existing baseline setting, unrelated to this
  change — without it those three tests fail with the expected
  baseline-not-found message).
- The exhaustive suite passes `--dry-run` (1440 skips) via
  `--cece-root-dir` alone, and the harness suite (133 tests) passes with
  no `CECE_*` environment at all.

---

# appendix: original notes

## always do

- include updating design.md as part of the implementation
- update the harness tests (`src/tests/ufs_chem_assay`) in addition to any changes to test_driver_combos.py
- update README.md with any necessary documentation changes in case of an api adjustment
- use pydantic models as opposed to dataclasses
  - all pydantic fields should include a description like `... = Field(description="<description content here>", ...`
- do *not* add driver bugs to known bugs in `README.md` unless explicitly told to do so
- use a test-driven development, red-green-refactor approach for all fixes and features (when possible)
- maintain original `always do` and `requirements` sections when refining design docs

### testing

- *all* suites should pass `--dry-run`
- run `simple-maccity-suite.yaml` without `--dry-run` for integration testing with the driver
- only run examples when requested to do so
- no need to run tests for spikes/documentation-only tasks

## requirements

- i moved the test runner to its own repository
- when running the driver, a CECE_ROOT_DIR environment variable must be set to the root of the CECE repository
- add this to settings
- for testing use this as the cece_root_dir: /Users/bkoziol/sandbox/git-benkozi/CECE
- the root dir is what /work should map to for docker run
- add --cece-root-dir as an optional flag to the runner; if not set, use the environment variable
