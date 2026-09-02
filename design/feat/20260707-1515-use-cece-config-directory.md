# Feature: base CECE config as a file, referenced from the suite config

## Goal

Make the base driver configuration a checked-in YAML file selected by the
suite config, instead of a `CeceConfig` hardcoded in `combos.py`. A suite
file then fully describes a run — *which base scenario* (`config_path`) plus
*which sweep* — and new scenarios become new YAML files rather than code
changes.

## Current behavior

- `combos.py:base_config()` constructs the baseline `CeceConfig` in code
  (single species `co`, single MACCITY stream); the suite config only carries
  the sweep.
- The suite file lives at `suite.yaml` (repo root) (project root), which
  is also the `--suite-config` default.

## Design

### `config_path` on the suite config

`SuiteConfig` gains a required `config_path: Path` field naming the base
CECE driver config:

```yaml
# simple-maccity-suite.yaml
config_path: ../cece/simple-maccity.yaml
sweep:
  mapalgo: [bilinear, consd, passthrough]
```

**Relative paths resolve against the suite file's own directory** (not the
process cwd), so the suite file and the configs it references move together
as a unit. `SuiteConfig.from_yaml(path)` resolves `config_path` to an
absolute host path at load time and fails immediately (pydantic validation)
if the file does not exist — before any containers run.

### Search-path settings

Two new optional settings on `settings.py` (unset by default) let configs
live in directories outside the checked-in `config/` tree — e.g. a shared
scenario library — while suites and CLI options refer to them by name:

| Setting                    | Env var                          | Default |
|----------------------------|----------------------------------|---------|
| `config_search_path`       | `CECE_CONFIG_SEARCH_PATH`        | unset   |
| `suite_config_search_path` | `CECE_SUITE_CONFIG_SEARCH_PATH`  | unset   |

When set, a search path is **prepended to the provided path**, which is kept
whole — nested directories and extension included — so config trees can be
organized hierarchically under the search directory:

- `suite_config_search_path` set → the `--suite-config` value `X` resolves to
  `<suite_config_search_path>/<X>` (e.g. `nightly/simple-maccity-suite.yaml`
  under the search dir).
- `config_search_path` set → the suite's `config_path` value `Y` resolves to
  `<config_search_path>/<Y>` (the suite-relative resolution above is
  skipped).

An absolute provided path is used as-is; a search path only applies to
relative values. A provided path containing `..` may resolve outside the
search directory (e.g. the default suite's `../cece/simple-maccity.yaml`) —
this is intentional and accepted: the provided path is prepended verbatim,
with no containment check. When the search paths are unset (default), behavior is
exactly as described above. Existence is still checked at load time either
way, so a path missing from the search directory fails before any docker
runs.

### Base config loading

`combos.build_config()` starts from `CeceConfig.from_yaml(config_path)`
instead of the in-code `base_config()`, which is deleted. Loading per combo
(or one load + `model_copy(deep=True)` per combo) keeps combos isolated.
This stays inside the existing hard requirement: the file is read via
`CeceConfig.from_yaml()` and mutated/written only through the model — the
YAML file *is* validated pydantic input, not a template to string-edit.

The initial base config file is the current in-code baseline serialized to
`simple-maccity.yaml` — behavior of the initial suite is unchanged.

### File layout and moves

```
src/tests/config/
  cece/
    simple-maccity.yaml         # base driver config (was combos.base_config())
  suite/
    simple-maccity-suite.yaml   # was suite.yaml at the repo root
```

- `suite.yaml` moves to `src/tests/config/suite/simple-maccity-suite.yaml`.
- The `--suite-config` default becomes that path.
- The `-suite` filename suffix distinguishes suite files from driver configs
  at a glance; the shared `simple-maccity` stem ties the pair together.

Both subdirectories share the `src/tests/config/` parent (resolved: `config`
over `cfg`), giving the tidy relative reference `../cece/simple-maccity.yaml`
from the suite file.

### Per-combination timeout

The suite config gains a required `timeout_s` field: the per-combination
timeout in seconds, passed to the driver `subprocess` call. The settings
value `run_timeout_s` (`CECE_RUN_TIMEOUT_S`, default 300) acts as a cap: it
overrides the suite value **only when it is smaller**, i.e.

```
effective timeout = min(suite.timeout_s, settings.run_timeout_s)
```

The initial suite sets `timeout_s: 5` — five seconds per combination.

**Timeouts firing is acceptable for this iteration.** The driver under test
currently has issues and may hang; the goal right now is that the suite
*executes* — configs generate, containers launch, and a hung driver is
reliably killed after the effective timeout. A timed-out combination fails
its test (`subprocess.TimeoutExpired` propagates, `.out` is still written
with whatever output was captured), and a run whose tests fail only by
timeout is considered a successful outcome for this iteration. Pass/fail
semantics are unchanged — exit 0 passes; the future evaluation step will
revisit expected-failure handling.

### Ripple effects

- `conftest.py`: the `--suite-config` default changes, its resolution
  consults `suite_config_search_path` when set, and the effective timeout is
  computed from suite + settings.
- `settings.py`: the two new optional search-path fields; `run_timeout_s`
  becomes the cap on the suite-level `timeout_s`.
- `runner.py`: `run_driver` takes the effective timeout instead of reading
  `settings.run_timeout_s` directly.
- Main `design.md` needs updating: the "Base configuration" section currently
  states the base config is defined in code with zero runtime dependency on
  files elsewhere in the repo. The zero-dependency rationale is preserved —
  the config file lives inside this repository — but the mechanism
  changes to file-based; the suite-configuration section gains `config_path`.
- README: mention `config_path` and the new default suite path.

## Non-goals

- No per-combo or per-dimension base-config overrides (still future work in
  the main design doc).
- No search path / config directory scanning — `config_path` is an explicit
  reference, one base config per suite file.

## Acceptance criteria

- `uv run pytest` (no options) uses
  `src/tests/config/suite/simple-maccity-suite.yaml`, which references
  `src/tests/config/cece/simple-maccity.yaml` relatively, and **all tests pass**
  with unchanged behavior (same combos, same generated configs).
- A suite file with a missing/typo'd `config_path` fails at session start
  with a clear validation error, before any docker runs.
- With `CECE_SUITE_CONFIG_SEARCH_PATH` and/or `CECE_CONFIG_SEARCH_PATH`
  set to directories containing copies of the configs (possibly in nested
  subdirectories), relative suite/config paths resolve under those
  directories and all tests pass; unset, behavior is unchanged.
- `suite.yaml` (repo root) no longer exists; `combos.base_config()` is
  removed.
- Each combination runs with a 5-second effective timeout
  (`min(suite timeout_s=5, settings run_timeout_s=300)`). The suite
  *executes* end to end; combinations that time out fail with
  `TimeoutExpired` and still write their `.out` — for this iteration that
  counts as success.
