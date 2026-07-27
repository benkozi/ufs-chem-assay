# Feature: `--suite-config` may match multiple suites (multi-suite sessions)

## Goal

Let one pytest invocation run several suites: `--suite-config` keeps its
selector semantics (existing file path, or regex fullmatched against
discovered suite files) but **multiple matches become a multi-suite
session** instead of an error. The motivating command:

```sh
uv run pytest src/tests/test_driver_combos.py --suite-config='ex[0-9]-suite.yaml'
```

runs all seven example suites in one session. Every suite gets the full,
unchanged per-suite pipeline — enumeration, generated configs, driver
runs, assertions, stats, plots, baselines — and the output root stays
**flat**: one combo directory per combination regardless of how many
suites ran. Enabling that flatness, `combo_id` becomes a **runtime
ULID** (the content hash and its cross-run-stable-id property are
removed — joins happen on the recorded combo parameters instead).

## Pre-design audit (fresh facts, 2026-07-24)

Verified on runner `feat/initial-impl` (post examples-as-suites).

1. **Selection**: `resolution.select_suite` fullmatches the regex
   against file names / root-relative paths and raises on zero *and*
   on multiple matches. The multiple-match error is the only thing
   standing between the motivating command and a multi-suite run.
2. **Session state is single-suite by construction** (`_SUITE` stash
   in `src/tests/conftest.py`): `pytest_sessionstart` loads exactly one
   suite, enumerates once, resolves baselines once; the session-scoped
   fixtures `suite_assertions`, `suite_analysis`, `run_timeout_s`,
   `baseline_comparisons`, and `run_context` all read that one suite;
   `combo_roots` writes one `run.yaml` (`RunManifest` holds one
   `SuiteConfig`) and one `combos.csv`; `generated_combos` maps
   `combo_id -> GeneratedCombo`; `pytest_sessionfinish` globs
   `*/*-stats.csv` at the output root.
3. **Today's `combo_id` is a content hash of the combo name** — every
   sweep-less suite's single combo is `base` with the *identical* id,
   and identical sweeps in two suites collide the same way, so a flat
   shared root is impossible with hash ids. Switching the id to a
   runtime ULID (user direction) dissolves the collision entirely and
   keeps the root flat for any number of suites; the hash's advertised
   cross-run join-key property (README, design.md) is deliberately
   given up — `combos.csv` and the stats CSVs already record `combo`
   (canonical name) and `suite` on every row, so cross-run joins on
   the actual combo parameters remain direct.
4. **Species parametrization is a cross product**:
   `pytest_generate_tests` parametrizes `driver_run` and
   `species_name` independently — correct for one suite, wrong for
   many (suite A's species would pair with suite B's combos). Joint
   parametrization is required; pytest's combined-id convention
   (`combo-species`, dash-joined) means hand-built pair ids can
   preserve today's test ids exactly.
5. **Stats rows are already suite-stamped**: `RunContext` writes
   `run_id` + `suite` into every stats row, so cross-suite
   concatenation stays unambiguous. `TestReportRow` has no suite
   column yet (`pytest_name`, `combo_id`, `combo`, `result`).
6. **`run_timeout_s` and assertions are per-suite config** — the
   examples-as-suites arc deliberately put timeouts in each suite
   file, so a multi-suite session must apply each combo's own suite
   values, not the first suite's.
7. **`combos.csv` records swept dimensions only** — unswept sweepable
   fields (a pinned `taxmode` beside a swept `mapalgo`) appear nowhere
   but the generated config YAML, and a sweep-less combo emits zero
   rows entirely. With opaque ULID directory names and joins moving to
   combo parameters, that is doubly insufficient: directories become
   undereferenceable and parameter-level joins only work for whatever
   happened to be swept. The effective value of every sweepable
   dimension is cheaply available from each combo's generated config.

## Design

The organizing idea: replace "the suite" with an ordered list of
per-suite contexts, hang everything a test needs off the parametrized
unit instead of session-global fixtures, and let runtime ULIDs make
combo directories unique so the root never needs nesting. A
single-match session behaves as today except where this design says
otherwise (ULID directory names, the `run.yaml` schema, and the new
report/CSV columns).

### Phase 1 — selection returns every match (red-green)

Red: unit tests — one match returns a one-element list (existing
single-match tests adjusted); multiple matches return all, sorted by
file name; zero matches still raises with the candidate listing; an
existing file path returns a one-element list.

Green: `select_suite` becomes `select_suites(option, search_paths) ->
list[Path]` (same discovery, dedupe, and error text for zero matches;
the multiple-match error is deleted). Duplicate suite *names* among
the selected files are rejected at sessionstart (suite is a join
column everywhere), with a message naming both files.

### Phase 2 — `combo_id` becomes a runtime ULID (red-green)

Red: unit tests — enumeration assigns each combo a distinct 26-char
ULID; two enumerations of the same sweep get different ids (no
determinism); `combos.csv` carries one row per (target, sweepable
field) per combo with effective values from the generated config — a
sweep-less combo included, its rows all `swept=false`; the hash
property is gone.

Green:

- `Combo` gains `combo_id: str` assigned at enumeration
  (`enumerate_combos` mints one `ULID()` per combo; runtime-only,
  never from configuration — same rule as `run_id`). The
  `hashlib`-based property is deleted. `Combo.name` stays the
  canonical, deterministic combination string and remains the pytest
  id and the human-readable join key.
- **`combos.csv` becomes the complete effective-parameter table**
  (audit item 7): for every combo, one row per (target, field) across
  *all* sweepable dimensions — each stream × `taxmode`/`tintalgo`/
  `mapalgo`, each species entry (existing `co`, `co-1` target naming)
  × `operation`/`category`/`vdist_method` — with `value` read from
  the combo's **generated** config, so swept rows carry the swept
  value and pinned rows the base value; unset optional fields (e.g. a
  species entry without `category` or `vdist`) record an empty value.
  New columns: `suite`, and `swept` (bool: was this row a sweep
  dimension). Sweep-less combos thereby get real rows — full
  parameter capture, no placeholder. Since values come from generated
  configs, the CSV is written once generation has run (still up
  front, before any driver executes; a run that dies midway keeps its
  records).
- Documentation claims about stable cross-run ids (README Results,
  design.md naming section) are rewritten: ULIDs order by creation
  time within a run; cross-run joins use `suite` + `combo` (name) or
  the parameter columns — "joining directly on the combo parameters"
  is the supported pattern, and it now covers pinned parameters and
  base combos, not just swept ones.

### Phase 3 — per-suite contexts + parametrization (red-green)

Red: harness tests driving `pytest_sessionstart`/`generate_tests`
against two small fabricated suites — combined combo params carry
suite-qualified ids `<suite>/<combo>` when N > 1 and today's plain
`<combo>` ids when N = 1; species pairs never cross suites; per-combo
timeout/assertions come from the owning suite.

Green:

- **`SuiteContext`** (pydantic, frozen): the resolved per-suite bundle
  — `suite: SuiteConfig`, `combos: list[Combo]`,
  `baselines: dict[str, BaselineComparison]`. `pytest_sessionstart`
  builds one per selected file (same loading, validation, and
  UsageError conversion as today, applied per suite) into
  `_SUITE_CONTEXTS: list[SuiteContext]`; `_SUITE` is deleted.
- **Parametrized unit**: `driver_run` params become `(context, combo)`
  pairs in suite order. Ids: `combo.name` when one suite,
  `f"{suite.name}/{combo.name}"` when several (`-k ex3` then selects
  a suite's tests naturally).
- **Per-suite values via the callspec**: `suite_assertions`,
  `suite_analysis`, `baseline_comparisons`, `run_context`, and
  `run_timeout_s` stop being session-global: each derives the owning
  `SuiteContext` from the requesting item's `driver_run` param
  (`request.node.callspec`). Test function signatures do not change.
  `run_timeout_s` applies `min(context.suite.timeout_s,
  settings.run_timeout_s)` per combo.
- **Joint species parametrization**: when a test requests
  `species_name`, generate `(context, combo, species)` triples —
  combos pair only with their own suite's configured species — and
  parametrize `driver_run` + `species_name` together with hand-built
  ids `f"{combo_id_part}-{species}"`, preserving today's id text in
  the single-suite case.
- **`DriverRunResult` unchanged** (it already carries combo, dirs,
  config, error); the driver-run fixture looks up the pair's context
  for generation and timeout.

### Phase 4 — flat session artifacts across suites (red-green)

Red: dry-run harness tests over two fabricated suites (one sweep-less)
asserting the layout below — flat combo directories, one `run.yaml`
recording both resolved suites, one `combos.csv` dereferencing every
directory including the sweep-less one; a single-suite dry-run
asserting the same shape with one suite.

Green:

- **Layout — flat for any N** (ULIDs make directories unique):

  ```
  <output-root>/
    run.yaml                     # session run_id + ALL resolved suites, in order
    combos.csv                   # effective-parameter table: run_id, combo_id (ULID),
                                 #   suite, name, target, field, value, swept —
                                 #   every sweepable dimension of every combo
    test-report.csv              # session-wide; rows gain a suite column
    descriptive_stats.csv        # all suites concatenated (rows are suite-stamped)
    stats-comparison.csv         # likewise, when any comparisons ran
    01K0Z8FJ.../                 # one directory per combination (combo ULID),
      ...                        #   per-combo artifacts unchanged
  ```

- **`RunManifest` schema change**: `suite: SuiteConfig` becomes
  `suites: list[SuiteConfig]` (resolved, in selection order) — a
  one-element list for single-suite runs. Recorded as a breaking
  `run.yaml` change in README.
- **`TestReportRow` gains `suite`** (`Field(description=...)`); the
  makereport wrapper reads the pair param.
- **`pytest_sessionfinish`** stays a single flat pass: the existing
  `*/*-stats.csv` globs already sweep every combo directory whatever
  its suite; concatenated CSVs distinguish suites by column. Overview
  and bias plots run per suite (color scales derive from each suite's
  own stats rows — filter the concatenated frame by suite), rendering
  into each combo's directory as today.

### Out of scope (deliberately)

- Cross-suite plot scales or cross-suite analysis beyond the
  concatenated CSVs (rows are suite-stamped; consumers can group).
- Parallel suite execution (suites run in selection order, combos in
  enumeration order, as today).
- Any change to `--run-examples`, suite file formats, or selection
  syntax (the selector grammar is untouched — only its multi-match
  behavior changes).
- Aggregate exit semantics beyond pytest's own (any failing test fails
  the run; ex7 in a multi-suite selection fails that suite's
  `test_driver_execution` and nothing else).
- Guards on broad selectors: a regex matching everything runs
  everything (see conversational updates).

## Verification

- Unit tests (new + existing) via `uv run pytest src/tests/combo_test_runner`.
- All checked-in suites pass `--dry-run` individually, plus
  multi-suite dry-runs: `--suite-config='ex[0-9]-suite.yaml'`
  (7 suites × 1 combo) and
  `--suite-config='(simple-maccity|ex3)-suite.yaml'` (mixed sweep and
  sweep-less), asserting the flat layout, the multi-suite `run.yaml`,
  and qualified ids.
- `simple-maccity-suite.yaml` without `--dry-run` (single-suite
  integration; artifacts as today modulo ULID directory names, the
  `run.yaml` suites list, and the new columns).
- Examples run only when requested; when requested, the motivating
  command end-to-end.
- Pre-commit hooks pass.

## Constraints

- pydantic models with `Field(description=...)`; avoid `Any`.
- Never commit — the user commits.
- README.md (`--suite-config` docs, Results layout, id semantics,
  report columns) and `design/design.md` (combination naming/ids
  section, directory layout) updated as part of implementation.

## Acceptance criteria

1. `--suite-config='ex[0-9]-suite.yaml'` runs all seven example suites
   in one session with suite-qualified test ids and a flat output
   root; zero matches still fail with the candidate listing.
2. Every combo directory is a runtime ULID; `combos.csv` records the
   effective value of every sweepable dimension for every combo
   (pinned and swept, `swept` flagged, `suite` on each row) — a
   sweep-less combo's full parameter set included, so e.g. `mapalgo`
   is joinable for a `base` combo; no hashing remains in `combos.py`.
3. Every combo runs under its own suite's timeout, assertions,
   analysis/plotting switches, and baselines; species assertions never
   cross suites.
4. `run.yaml` records the session ULID plus all resolved suites in
   selection order; `test-report.csv` rows carry `suite`.
5. Duplicate suite names among the matches fail at sessionstart naming
   the files; single-match sessions keep today's test-id text.
6. All checked-in suites stay green under `--dry-run`; maccity
   integration stays green.

## Implementation notes

**Outcome: implemented 2026-07-24; all phases green.**

- **Phase 1**: `resolution.select_suites` returns every match sorted by
  (file name, path); the multiple-match error is gone, zero-match error
  text unchanged. Duplicate suite names among the matches are a
  sessionstart UsageError naming both files.
- **Phase 2**: `Combo` carries `combo_id: str` minted as `str(ULID())`
  in `enumerate_combos`; the `hashlib` property (and import) deleted.
  `write_combos_csv` takes `(suite_name, combo, generated_config)`
  entries and writes the effective-parameter table
  (`run_id, combo_id, suite, name, target, field, value, swept`) via
  `_effective_parameter_rows` — species targets (sorted, `co`/`co-1`
  naming) then streams (sorted), fields in the canonical order; unset
  optionals record `""`. Verified: a maccity combo emits 6 rows
  (swept `mapalgo` True, pinned `taxmode`/`tintalgo`/`operation`
  False), a sweep-less combo its full all-pinned set.
- **Phase 3**: `SuiteContext` (frozen pydantic; `InstanceOf[Combo]`)
  built per selected suite at sessionstart into `_SUITE_CONTEXTS`
  (`_SUITE`/`_COMBOS`/`_BASELINES` deleted). `driver_run` params are
  `(context, combo)` pairs; ids plain `combo.name` for one suite,
  `<suite>/<combo>` for several. `suite_assertions`, `suite_analysis`,
  `baseline_comparisons`, `run_context` became function-scoped,
  deriving the owning context from `request.node.callspec` — test
  signatures unchanged. The `run_timeout_s` session fixture is gone
  (scope mismatch with per-suite values); `driver_run` computes
  `min(context.suite.timeout_s, settings.run_timeout_s)` inline.
  Species use joint parametrization
  (`metafunc.parametrize("driver_run,species_name", triples, ...)`)
  with hand-built `<combo>-<species>` ids preserving single-suite id
  text exactly.
- **Phase 4**: `RunManifest.suite` → `suites: list[SuiteConfig]`
  (selection order). `TestReportRow` gained `suite` (column order:
  pytest_name, suite, combo_id, combo, result). combos.csv writing
  moved from `combo_roots` into `generated_combos` (values come from
  generated configs; still before any driver executes).
  `pytest_sessionfinish` concats once over the flat glob, then plots
  per suite from that suite's frame slice; `render_all_plots` now
  derives combo directories **from the frame's combo_id column**
  instead of iterating every root subdirectory — the fix that keeps a
  flat multi-suite root from cross-contaminating scales or
  double-rendering. Bias plots likewise per-suite via each context's
  resolved baselines and comparison slice.
- **Test-count semantics change (intentional, surfaced by red)**: the
  old cross-product manufactured per-combo *skipped* species items for
  suites with no species assertions (exhaustive suites: 240 phantom
  items → "1440 skipped"; now "1201": 240×5 + one empty-parameter-set
  item). Joint parametrization generates species cases only where
  configured — no cross-suite leakage, no phantoms.
- **Verification**: 203 unit tests green (updated: selection returns
  lists; ULID shape/uniqueness; parameter-table columns and pinned/
  swept/empty values; sweep-less full-row coverage; manifest suites
  list round-trip; report suite column; multi-match subprocess dry-run
  "1218 skipped" with the counting worked out in-test). All 10 suites
  dry-run green individually; `ex[0-9]-suite.yaml` multi dry-run = 36;
  mixed `(simple-maccity|ex3)` = 23 with artifacts inspected on a
  fabricated checkout (run.yaml suites in selection order, 24-row
  parameter table, `ex3/base`-qualified report rows, 4 flat ULID
  dirs). `simple-maccity` docker integration **18/18**. Pre-commit
  hooks all pass. Examples not executed (run only when requested);
  the motivating `ex[0-9]` command end-to-end awaits a requested run.
- **Docs**: README (`--suite-config` multi-match semantics + no-guard
  note, Results tree and id/join semantics rewritten);
  `design/design.md` (naming/ids section, layout, `--suite-config`
  option description); conftest `--suite-config` help text.

---

# appendix: original notes

## always do

- include updating design.md as part of the implementation
- update combo-test-runner tests in addition to any changes to test_driver_combos.py
- update README.md with any necessary documentation changes in case of an api adjustment
- use pydantic models as opposed to dataclasses
  - all pydantic fields should include a description like `... = Field(description="<description content here>", ...`
- do *not* add driver bugs to known bugs in `README.md` unless explicitly told to do so
- use a test-driven development, red-green-refactor approach for all fixes and features (when possible)
- maintain original design sections when refining design docs - create an appendix
  - summarize conversational updates in the appendix following original refinement target
- when using python `typing`, avoid `Any` as much as possible
- **never, ever, ever** commit code - the user always commits

## testing

- not necessary for design documents in the `spike` folder - code *should not* change for spikes
- *all* suites should pass `--dry-run`
- run `simple-maccity-suite.yaml` without `--dry-run` for integration testing with the driver
- only run examples when requested to do so
- no need to run tests for spikes/documentation-only tasks
- pre-commit hooks pass

## requirements

- we need to run multiple suite configs
- see this conversation:
```
what's the simplest command using regex to run the example suites only?

A single regex can't do it in one invocation — --suite-config must fullmatch exactly one suite (multiple matches fail with a listing). So per example, the shortest regex form is:

uv run pytest src/tests/test_driver_combos.py --suite-config='ex3.*'
(ex3.* fullmatches only ex3-suite.yaml; a bare ex3 matches nothing since it's a fullmatch.)

To run all seven, the simplest is a shell loop over the regex:

for n in 1 2 3 4 5 6 7; do uv run pytest src/tests/test_driver_combos.py --suite-config="ex$n.*"; done
Each iteration is a full independent session with its own output root, run.yaml, stats, and plots. Two caveats: each example's data must be downloaded first (python3 $CECE_ROOT_DIR/examples/download-example-data.py --example exN), and ex7 will fail honestly on the missing CAMS data — so if you want the loop to keep going past it, don't add -x, and expect a nonzero exit from that iteration.

If "run them all in one command" is something you'd want often, a --suite-config mode that accepts multiple matches (or a tiny wrapper script) would be a new feature — say the word and I'll spike a design note for it.
```
- the suite-config can match multiple suite configs

## conversational updates

- 2026-07-24: **no safety guard on broad selectors** (user direction) —
  a regex matching everything runs everything; that is the point of the
  feature, and the operator is trusted. The selected-suite log line at
  session start is ordinary information, not a mitigation, and nothing
  warns or confirms.
- 2026-07-24: **`combo_id` becomes a runtime ULID; hashing removed**
  (user direction) — the content-hash id and its cross-run-stable
  join-key property are dropped in favor of joining on the recorded
  combo parameters (`suite` + `combo` name + dimension columns), which
  keeps the output root **flat** for any number of suites (no
  per-suite subtrees, which the previous draft needed to dodge hash
  collisions like every sweep-less suite's `base`). `run.yaml` becomes
  the multi-suite record (`suites` list), and `combos.csv` gains a
  `suite` column plus a guaranteed row for dimension-less combos so
  every ULID directory stays dereferenceable.
- 2026-07-24: **`combos.csv` records effective values for every
  sweepable dimension, swept or not** (user direction, superseding the
  placeholder-row idea above) — values read from each combo's
  generated config with a `swept` flag per row, so pinned parameters
  and `base` combos join on parameters exactly like swept ones
  (`mapalgo` is recorded for every stream of every combo). Base combos
  were confirmed to already receive descriptive statistics; this
  closes the parameter side of the join model.
- 2026-07-24 (post-implementation): **the first real `ex[0-9]` run
  surfaced a likely AMIO writer bug, and a new default assertion now
  catches it.** With the CAMS data pre-downloaded to `<CECE>/data`,
  all seven example suites executed; ex7's output (`amio_worker_threads:
  2`) has `nox` on dimensions `(time, lev, nox_dim2, lon)` — the
  latitude slot carries a synthetic name, so the `lat` coordinate
  (present in the file, correct length 720) is not associated and
  plotting fails with `KeyError: 'lat'`. ex1–ex6 (default threads,
  ex1 sharing the same F360 grid) are all standard — the correlation
  is the worker-thread count, not the grid. Mechanism: the writer
  enqueues async `amio_write`s for lon/lat/lev/time and then the data
  fields; the netCDF driver defines a variable's dimensions by
  length-matching *existing* dims at write-task execution, so with
  ≥ 2 workers the `nox` task can define its dims before the `lat`
  task has created `lat` (720 not found → synthetic `nox_dim2`; `lat`
  is created afterward, detached). Driver-side fix belongs to
  AMIO/CECE. Runner-side, a new always-on-by-default assertion —
  `assertions.validate_dimensions` /
  `test_nc_variable_dimensions` / `assert_output_variable_dimensions`
  — asserts every configured output variable carries exactly
  `(time, lev, lat, lon)` in every produced NetCDF (missing variable
  fails; disabled output checks nothing; the run-only exhaustive
  suite opts out). ex7 is expected to fail this test until the
  driver-side fix lands — an intended honest failure, per user
  direction. Test counts shift by one per combo (maccity dry-run 21
  rows; the multi-match subprocess expectation is now 1461).
  **Resolved later the same day**: the AMIO fix
  (design/fix/20260724-1228-wrong-dimension-names.md — canonical-first
  dimension resolution in the netCDF driver's define path) landed and
  ex7 passes 3× consecutively including the dimension assertion;
  maccity (21/21) and ex3 stay green on the rebuilt driver.