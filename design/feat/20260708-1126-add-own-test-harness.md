# Feature: the runner's own test harness (mocked, no docker)

## Goal

The runner has accumulated real logic — combination enumeration, config
generation, path/search-path resolution, timeout capping, result capture,
assertions — and it should be tested in its own right, fast and without
docker. Additionally, as the number of suites grows, full integration runs
stop being the everyday verification tool (one suite will not cover all
combinations); the harness tests become the thing that always runs.

## Current behavior

The only tests are the integration tests (`test_driver_combos.py`): every
verification of runner behavior requires launching real containers and a
working driver build.

## Design

### Layout

```
<repo root>/src/tests/
  ufs_chem_assay/             # harness tests: the runner testing itself
    conftest.py               # fixtures: paths to the checked-in maccity configs
    test_combos.py            # enumeration, naming, build_config
    test_suite_config.py      # loading, config_path/search-path resolution
    test_resolution.py        # output-root and suite-path resolution rules
    test_runner.py            # command construction, run_driver w/ mocked process
    test_assertions.py        # derivation + file-count assertion
    test_maccity_pipeline.py  # the full maccity suite flow, process call mocked
  config/...                  # existing checked-in configs (reused by harness tests)
  conftest.py                 # existing integration conftest
  test_driver_combos.py       # existing integration tests (real docker)
```

With two `conftest.py` files in the tree, **no test module may `import
conftest`** — the name is ambiguous (whichever conftest pytest registered
first wins). Anything a test needs to import lives in a real `src/` module:
`DriverRunResult` moved from the integration conftest into `runner.py` for
exactly this reason.

The directory is named `ufs_chem_assay` (the harness name, underscored because
mypy requires a valid package name); it matches the `ufs-chem-assay` logger
namespace. It carries
no `__init__.py` today — pytest imports test modules by file. Test module
basenames are unique across the tree, which rootdir-style collection
requires.

### Mocking strategy

- **`pytest-mock` (`mocker: MockerFixture`) for all mocking** — no bare
  `unittest.mock` imports in test code. Added as a dependency.
- The seam is the process call: patch `runner.subprocess.check_output`. No
  `docker run` ever executes; everything above that line (command
  construction, `.out` writing on success *and* failure, exception
  propagation) runs for real.
- Failure modes are simulated by setting the mock's `side_effect` to
  `CalledProcessError` / `TimeoutExpired` (with `output=` payloads), which
  exercises the failure paths the integration suite can only hit when the
  driver actually misbehaves.

### What the harness covers

- **`combos`**: the maccity sweep enumerates to exactly
  `map-bilinear`, `map-consd`, `map-passthrough`; multi-dimension sweeps
  produce the cartesian product with canonical name ordering; an empty sweep
  yields no combos; `build_config` applies the swept value at the right
  injection point, sets `output.directory`, and supplies vdist companion
  fields when sweeping `VdistMethod`.
- **`suite_config`**: suite-relative `config_path` resolution, search-path
  prepending (including `..` walking out — the accepted behavior), absolute
  paths used as-is, `FileNotFoundError` on a missing target, assertions
  defaults when the section is absent.
- **`runner`**: `build_command` places the `/work` mount, optional output
  mount, env vars, image, and yaml argument correctly; `run_driver` writes
  `.out` and returns on success, and writes `.out` then re-raises on
  `CalledProcessError` / `TimeoutExpired`.
- **`assertions`**: derived count for the maccity config is 1; 0 when
  `output` is absent or disabled; multi-step/frequency arithmetic; the
  assertion passes/fails against fabricated `*.nc` files in `tmp_path`.
- **`test_maccity_pipeline`** — the note's "run everything expected from the
  maccity suite": load the checked-in `simple-maccity-suite.yaml`, enumerate
  its combos, generate all three configs into `tmp_path`, invoke
  `run_driver` per combo with the process call mocked, fabricate the
  expected `.nc` file per combo, and run the file-count assertion — the full
  pipeline, seconds not minutes, no docker.

### Testability refactor: path resolution moves out of conftest

`_resolve_output_roots` and `_resolve_suite_path` are pure functions living
in the integration `conftest.py`, where harness tests cannot cleanly import
them. They move to a small `src/resolution.py` (conftest imports from
there). No behavior change — this is purely to make the resolution rules
unit-testable.

### Selecting harness vs. integration tests

Both remain under `testpaths`, so plain `uv run pytest` runs everything —
unchanged default. Slicing is by path, no markers needed yet:

```sh
uv run pytest src/tests/ufs_chem_assay    # harness only: fast, no docker
uv run pytest src/tests/test_driver_combos.py  # integration only
```

When suite count grows and the default should flip to harness-only, that is
a one-line `testpaths` change plus an explicit integration command —
deferred until it hurts.

## Non-goals

- No mocking inside the integration tests — they keep exercising the real
  driver end to end.
- No coverage tooling / thresholds in this feature.
- No pytester-based self-hosting tests of the conftest plugin machinery
  (fixture parametrization, session hooks); the harness tests target the
  importable modules. Revisit if conftest logic grows further.

## Acceptance criteria

- `uv run pytest src/tests/ufs_chem_assay` passes in a few seconds with
  docker unavailable (no `docker` invocation anywhere in the harness run).
- The pipeline test executes all three maccity combos against the checked-in
  suite/config files with `check_output` mocked, including generated-yaml
  content and a passing file-count assertion per combo.
- Failure-path tests prove `.out` is written when the (mocked) process call
  raises `CalledProcessError` and `TimeoutExpired`.
- Plain `uv run pytest` (integration + harness together) still passes.
- `pytest-mock` is a project dependency; all mocking goes through `mocker`.
