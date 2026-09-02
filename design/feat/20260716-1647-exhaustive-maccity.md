# Feature: exhaustive run-only maccity suite

## Goal

An **exhaustive, run-only** suite sweeping every enum value across every
dimension of the simple-maccity scenario — pass/fail per combination is the
only question, with plotting, baselines, and statistics all off. Because
the Python enums are hand-mirrored from the C++ implementation, the feature
starts with an **enum audit against the driver code**; the suite then uses
**regex value expansion** (`".*"`) so it never needs editing as enums grow,
and a **`test-report.csv`** summarizes every combination's outcome.
Inapplicable value combinations may fail the driver — that is the suite's
data, not defects to fix here (a spike mindset for the results).

## Enum audit (C++ ground truth vs the runner's Python enums)

Traced through `cece_config_parser.cpp`, `cece_config_validator.cpp`,
`cece_regridder_utils.cpp`, `cece_driver_facade.cpp`, and helm's `dagr`:

| Enum | Driver-accepted values (standalone path) | Python enum today | Verdict |
|---|---|---|---|
| `Operation` | `add`, `replace` (validator-enforced) | add, replace | **complete** |
| `Category` | **never validated** — free-form pass-through string | 6 values (7 after this feature: `undefined` added) | complete (any string passes; see runtime note) |
| `VdistMethod` | `single`, `range`, `pressure`, `height`, `pbl` — **lowercase** (parser-matched; see finding 3) | PBL, HEIGHT, PRESSURE (uppercase) | **missing `single`, `range`; existing values are the wrong case** |
| `Taxmode` | **inert in standalone mode** (zero facade reads; flows only to TIDE/dagr in NUOPC mode, where dagr accepts `clamp`/`cycle`) | cycle, extend | complete for sweeping; both trivially pass |
| `Tintalgo` | `linear`, `nearest` (facade; default nearest) | linear, nearest | **complete** |
| `Mapalgo` | canonical: `passthrough`, `nn`, `bilinear`, `cubic`, `conss`, `consd` (+aliases) | consd, bilinear, passthrough, consf, nn, redist | **missing `cubic`, `conss`; `consf`/`redist` are not driver values** |

Two findings beyond value lists:

1. **`consf`/`redist` don't fail — they silently regrid with the default
   method**: the regridder's if/else chain has no unknown-value error
   branch, so an unrecognized `mapalgo` keeps the default
   `InterpolationMethod`. Silent wrong-algorithm is worse than failure and
   would pollute a run-only pass/fail report with false passes.
   **Recommendation: remove `consf` and `redist` from the Python enum**
   (they were modeling artifacts; nothing checked-in sweeps them).
2. **The driver parses vdist as a *nested* block**
   (`vdist: {method, layer_start, layer_end, p_start, p_end, h_start,
   h_end}` — `cece_config_parser.cpp:95`), while `docs/configuration.md`
   documents flat `vdist_*` keys and the runner's `SpeciesEntry` models the
   flat form. **Flat vdist keys are silently ignored by the driver** —
   every vdist value the runner could sweep to date would have been a
   driver-level no-op (vdist was never integration-swept, so this went
   unseen). The runner must move to the nested schema for the exhaustive
   sweep to actually exercise vertical distribution. The stale flat table
   in `docs/configuration.md` is a driver-side doc bug to flag separately.
3. **The vdist validator is dead code in standalone mode, and the parser
   matches lowercase** (implementation-time finding): the validator's
   `ValidateVerticalDistribution` (and its operation check) walk a
   top-level `layers[i].vertical_distribution` schema that does not exist
   in the driver config (`species.<name>[i].vdist`), so the uppercase
   `SINGLE|RANGE|...` whitelist never fires — the **parser is the ground
   truth**, and it compares `"range"`, `"pressure"`, `"height"`, `"pbl"`
   in lowercase with a **silent fallthrough to SINGLE** for anything else
   (including `"single"` itself, which is only reachable via the
   fallthrough, and any typo or uppercase value). The Python enum values
   therefore must be lowercase — the current uppercase `PBL`/`HEIGHT`/
   `PRESSURE` would all silently run as SINGLE.

## Runner model updates (prerequisite, TDD'd)

- `Mapalgo`: add `cubic`, `conss`; remove `consf`, `redist` (finding 1).
- `Category`: add `undefined` — category is a pure label the driver never
  reads, so execution is identical for every value; `undefined` lets suites
  that need the dimension without meaning pin it instead of sweeping it.
- `VdistMethod`: add `single`, `range`; all values lowercase per finding 3
  (`single`, `range`, `pressure`, `height`, `pbl`). Combo name segments
  follow (`co.vd-height`), so vdist combo ids change — nothing checked-in
  swept vdist, so no baseline references break.
- `SpeciesEntry`: replace the flat `vdist_*` fields with a nested
  `vdist: Vdist | None` sub-model mirroring the driver parser
  (`method` required; `layer_start`/`layer_end` ints;
  `p_start`/`p_end`/`h_start`/`h_end` floats; all described).
  `combos.py`'s vdist apply targets the nested model and supplies
  per-method companions: height → `h_start/h_end` (0.0/100.0 m);
  pressure → `p_start/p_end` (100000.0/90000.0 Pa); range →
  `layer_start/layer_end` (0/2 — inclusive 0-based model-level indices in
  the stacking engine); single → `layer_start` (0); pbl → none.

## Sweep value regexes: `.*` means "all values, forever"

Sweep fields accept a **regex string as an alternative to the value list**:

```yaml
mapalgo: ".*"          # every Mapalgo value, including ones added later
tintalgo: "l.*"        # every value fullmatching the regex
```

- Types become `list[Enum] | str`; a model validator expands the string via
  `re.fullmatch` against the enum's values into the **sorted value list**
  at load time — downstream (enumeration, ids, selectors) is untouched, and
  `run.yaml`'s resolved-suite manifest records the *expanded* lists, so an
  exhaustive run remains exactly reproducible even after enums grow.
- A regex matching zero values is a load error (same fail-loud posture as
  everywhere else); invalid regexes fail in the validator.

## Run-only controls

- New `Assertions.validate_file_count: bool = True` (mirrors
  `validate_filenames`) so the count assertion can be switched off; with
  both false and no `species` block, only `test_driver_execution` runs per
  combination.
- The suite disables everything else: `analysis.compute_descriptive_stats:
  false`, `plotting.enabled: false`, no `baseline_comparisons`.

## The suite: `exhaustive-maccity-run-only-suite.yaml`

```yaml
name: exhaustive-maccity-run-only
config_path: ../cece/simple-maccity.yaml
timeout_s: 10
assertions:
  validate_file_count: false
  validate_filenames: false
analysis:
  compute_descriptive_stats: false
plotting:
  enabled: false
  gif_enabled: false
sweep:
  cece_data:
    streams:
      - name: MACCITY
        taxmode: ".*"
        tintalgo: ".*"
        mapalgo: ".*"
  species:
    co:
      - operation: ".*"
        category: [undefined]   # driver-inert label: pinned, never swept
        vdist_method: ".*"
```

**Size and runtime**: after the enum updates — 2 (op) × 1 (cat, pinned) ×
5 (vd) × 2 (tax) × 2 (tint) × 6 (map) = **240 combinations**; at the
observed ~6–7 s per driver run, **≈ 25–30 minutes serial** as an upper
bound — expected failures exit faster and hangs are capped at the 10 s
timeout, so the real run will likely be shorter. (Measured afterward: the
first real run — spike 20260716-1820 — averaged **0.51 s per combo, ~2 min
total**; the 6–7 s observation was stale. Exhaustive real runs are cheap.) This is an on-demand suite
(`--suite-config=...exhaustive...`), never the default. `category` is
driver-inert (finding above): sweeping it would multiply runtime ×7 for
identical execution, so it is **pinned to the new `undefined` label**
rather than swept (the original design swept it — 1,440 combos — before
this was trimmed); switching the pin back to `".*"` restores the full
product if the label dimension ever gains driver semantics. Failures from
inapplicable value combinations are expected and are the suite's data, not
defects to fix here.

## `--dry-run`: everything up to the driver

A new pytest option (registered alongside `--suite-config` in the combo
group) that runs the **entire session except driver execution** — the
exhaustive suite must be verifiable without producing hours of real data:

```
pytest --dry-run --suite-config=.../exhaustive-maccity-run-only-suite.yaml src/tests
```

- Everything before the container still happens for real: suite load, regex
  expansion, combo enumeration and ids, selector/baseline resolution, output
  root guard, `run.yaml`, `combos.csv`, and **every combination's generated
  driver config** (`<combo_id>/<combo_id>.yaml`) — the `generated_combos`
  fixture builds all configs up front, so a dry run exercises the full
  generation path.
- The `driver_run` fixture calls `pytest.skip("dry run: driver execution
  skipped")` immediately before the docker invocation. Pytest caches the
  session-fixture outcome per combination, so every dependent test skips
  with that reason — nothing is reported as passed that didn't actually
  run, and a skip-only session exits 0.
- `pytest_sessionfinish` is naturally a no-op (no stats/comparison CSVs
  exist to concatenate; no plots render); the lazy dask client never
  starts; docker is never touched — dry runs work on hosts without the
  image.
- `test-report.csv` **is still written**, with every combo-test row
  `skipped` — a dry run of the exhaustive suite validates the report
  machinery at the full 240-combination scale in seconds.
- General feature, not exhaustive-specific: useful for validating any new
  suite yaml (ids, selector resolution, config generation) before paying
  for containers.

## The report: `test-report.csv`

A **general session feature** (the exhaustive suite is the motivator): a
`pytest_runtest_logreport`-based collector records the outcome of every
combo-parameterized test, and `pytest_sessionfinish` writes
`<output-root>/test-report.csv`:

| pytest_name | combo_id | combo | result |
|---|---|---|---|
| `test_driver_execution[MACCITY.map-consd__...]` | 9004a4… | MACCITY.map-consd__… | passed |

- `result` ∈ passed / failed / skipped (skips reported honestly — run-only
  suites still skip the disabled assertion tests).
- The combo comes from the item's `driver_run` param (the `Combo` object —
  no id parsing); non-combo tests (harness) are not reported.
- Written whenever combinations ran, for every suite — small, always-useful
  artifact alongside `run.yaml`/`combos.csv`.
- The row model and CSV writer live in a new `report.py`
  (`TestReportRow` pydantic model + `write_test_report_csv` +
  `worst_result` outcome precedence: failed > skipped > passed across a
  test's setup/call/teardown phases) so the harness tests them directly;
  the conftest hook (`pytest_runtest_makereport` hookwrapper) only collects.

## TDD plan (red first)

- Enum updates: harness tests asserting the new members exist and
  `consf`/`redist` are gone; nested `Vdist` round-trips through
  `CeceConfig` and `build_config` supplies the right companions per method
  (all five).
- Regex expansion: `".*"` expands to the full sorted value list; a partial
  regex expands to its matches; zero-match and invalid regexes rejected;
  expansion visible in the dumped (resolved) suite.
- `validate_file_count: false` skips the count test with an explicit
  reason (mirroring the filenames flag tests).
- Report: fabricated outcomes → CSV rows with the four columns; skipped
  and failed represented; non-combo tests absent.
- `--dry-run`: a subprocess pytest run of the real integration suite
  (simple-maccity, temp output root, no mocking) exits 0 with every test
  skipped; `run.yaml`, `combos.csv`, every `<combo_id>/<combo_id>.yaml`,
  and an all-`skipped` `test-report.csv` exist; **no** `.out` or `.nc`
  files anywhere — proving docker was never invoked.
- Suite parses; selector/resolution features unaffected.

## Ripples (standing process rules)

- **`design.md`**: sweep section documents regex value expansion; the
  assertions example gains `validate_file_count`; artifacts layout gains
  `test-report.csv`; combination-space table updated to the corrected enum
  sets; the `SpeciesEntry` vdist restructuring noted in the base-config
  section.
- **`README.md`**: `test-report.csv` in the results layout; the exhaustive
  suite mentioned as the on-demand example of `--suite-config`; regex
  sweep values in the suite-option description; `--dry-run` in the CLI
  options with the exhaustive dry run as its example.
- Driver-side: file the `docs/configuration.md` flat-vdist doc bug
  separately (out of runner scope).
- Pydantic models with described fields; TDD red-green.

## Acceptance criteria

- Harness passes without docker, covering the matrix above (harness tests
  never require a driver call).
- The exhaustive suite **collects 240 combinations** with only
  `test_driver_execution` active per combo (the disabled assertion tests
  collect and skip).
- **All real driver execution is deferred for this feature** — no smoke
  slice, no full run. Any attempted execution of the integration suite
  during implementation carries `--dry-run`. The full-scale validation is
  a `--dry-run` of the exhaustive suite, completing in seconds: all 240
  configs generated on disk, `test-report.csv` at the output root with one
  all-`skipped` row per combo-test — validating generation and report
  machinery at full scale without producing real data. The real run stays
  a deliberate, user-initiated step for later (and will likely undershoot
  the serial estimate: inapplicable combinations that fail exit fast, and
  hangs are capped at the 10 s timeout).
- The regular `simple-maccity` suite keeps all current outcomes.

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
- maintain original `always do` and `requirements` sections when refining design docs. add these as an appendix.

## requirements

- create an exhaustive test suite based on simple-maccity-suite
- first, revisit the cece code to look for enums we are missing at the python level
  - remember the python enums are not connected to the c++ implementation
  - you may have to dig into extern code - take your time
- after updating the python enums, create the exhaustive test suite
- turn off all plotting, baselines, and statistics
  - this is a *run-only* test suite - we just want to know if the combinations passes or fails
- call the suite "exhaustive-maccity-run-only-suite.yaml"
- in fact, let's add a way to indicate all enum values should be tested. maybe a regex filter for the enums? `.*` would indicate all enums
  - the goal is to make this exhaustive suite always applicable. we don't want to update it every time we add a new enum
- there may be enum values that are not applicable and the driver will fail but that's okay. they can fail. think of this as spike in some ways
- we're going to need a "report" summarizing the results of the exhaustive test suite
  - this should be a csv with the pytest name, the combo_id, the combo name, and the result (pass/fail)
