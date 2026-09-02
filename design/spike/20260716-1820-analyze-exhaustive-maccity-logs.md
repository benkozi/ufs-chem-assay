# Spike: analyze the exhaustive maccity run's driver logs

## Goal

Execute the exhaustive maccity sweep **for real** (the first full run — all
prior work was dry-run only) and audit the driver's execution evidence:
captured logs, exit codes, timeouts, and output-file assertions. The
question is credibility, not correctness of values — *it says it works, but
does it?* — so the spike looks only at execution behavior (including
warnings, which may indicate driver problems), never at NetCDF contents.
The deliverable is a markdown report under
`design/spike_artifacts/`. **No code changes**: if the
analysis reveals work requiring code, the report surfaces it as feature
requirements and the spike is revisited afterward.

## The run

### Suite: an asserted variant of the exhaustive suite

The note's command names `exhaustive-maccity-run-only-suite.yaml`, but it
also asks for suite assertions (file count, etc.) — and assertions are
suite-yaml state with no CLI override. Editing the run-only suite would
contradict its name and documented purpose, so the spike adds a checked-in
**variant** (a config addition, not a code change):

```yaml
# src/tests/config/suite/exhaustive-maccity-asserted-suite.yaml
name: exhaustive-maccity-asserted
config_path: ../cece/simple-maccity.yaml
timeout_s: 10
# assertions: defaults apply — validate_file_count and validate_filenames
# true, expected_nc_file_count derived (3 for the 3-hour base config).
# No species block: attribute checks inspect output contents, out of scope.
analysis:
  compute_descriptive_stats: false
plotting:
  enabled: false
  gif_enabled: false
sweep: # identical to the run-only suite: ".*" everywhere, category pinned
  ...
```

The file-count and filename assertions are execution-level evidence (did
the driver write what its exit code claims?) at zero added runtime; they
skip automatically for combos whose driver run failed.

### Command

```sh
cd <repo root>
uv run pytest src/tests/test_driver_combos.py \
  --suite-config=src/tests/config/suite/exhaustive-maccity-asserted-suite.yaml \
  -vs 2>&1 | tee <scratch>/exhaustive-session.log
```

- 240 combinations × ~6–7 s ≈ **25–30 min serial upper bound** (failures
  exit faster; hangs cap at the 10 s timeout).
- Output root stays the pytest temp default (240 combo dirs of NetCDF do
  not belong in the checkout); every combo's `<combo_id>.out` persists
  there, and the teed session log captures the live `-vs` stream plus the
  runner's per-combo duration lines.
- Prerequisite: the `cece/cece-dev` image and a **fresh driver binary**
  (`scripts/build-and-test-container.py` first if in doubt — analyzing a
  stale binary would poison every finding).

## Analysis method (throwaway tooling only)

Ad-hoc greps and pandas one-liners over the run's artifacts — scratch
scripts live outside the repo and are not checked in ("no code changes"
covers runner code; the analysis leaves no code behind):

1. **`test-report.csv`** — the outcome matrix: pass/fail/skip counts per
   test family; join with `combos.csv` to attribute failures to swept
   dimensions (e.g. *all* `map-cubic` fail vs. scattered noise). The
   spike's headline table.
2. **Exit-code and assertion failures** — for each failing
   `test_driver_execution`: classify from the `.out` tail (driver error
   message, MPI abort, timeout with no output). For each file-count or
   filename failure on a *passing* driver run: the driver claimed success
   but wrote the wrong files — exactly the fishy case the spike hunts.
3. **Log-pattern sweep across all 240 `.out` files**, passing runs
   included:
   - `[DRIVER ERROR]`, `ERROR`, `WARN`/`WARNING`, `abort`, `exception`,
     `nan`/`inf`, `Kokkos`, `MPI_` anomalies;
   - absence of the normal completion line
     (`CECE Finalize completed successfully`) in a run that exited 0;
   - zero-byte or near-empty `.out` files;
   - line-count/byte-size outliers (a combo logging 10× the median is
     abnormal even if it passed).
4. **Timing** — per-combo durations from the session log: timeouts,
   near-timeout outliers, and any dimension systematically slower.
5. **Warning triage** — every distinct warning pattern gets a verdict in
   the report (benign / suspicious / needs-feature), per the note: warnings
   may indicate driver problems.

## The report

`design/spike_artifacts/` (new directory):

```
design/spike_artifacts/
  20260716-1820-exhaustive-maccity-log-analysis.md   # the deliverable
  20260716-1820-exhaustive-maccity/                  # provenance (small)
    run.yaml  combos.csv  test-report.csv
```

Report structure:

- **Run metadata** — run_id (ULID), date, suite, image, driver build
  provenance, combo count, wall time.
- **Outcome summary** — the test-report matrix by family and by swept
  dimension; totals front and center.
- **Failures** — one subsection per failure *class* (not per combo):
  dimensions affected, representative `.out` excerpt, classification.
- **Abnormal output in passing runs** — the "says it works, but does it?"
  section: warnings catalog (pattern, count, example combos, verdict),
  missing-completion-line cases, size/timing outliers.
- **Surfaced feature requirements** — anything needing code changes, as
  concrete requirements ready to become `design/feat/` notes. The spike
  changes no code regardless of what it finds.

Raw `.out` files and NetCDF stay in the pytest temp root (referenced by
run_id in the report); only the markdown and the three small provenance
files are checked in.

## Non-goals

- No NetCDF content inspection (values, attributes, statistics) — the
  species-attribute assertions stay off and stats/plots stay disabled.
- No fixing: driver bugs, runner gaps, and doc issues found here become
  surfaced requirements, not patches.
- No README/design.md ripples beyond the variant suite's existence — the
  asserted suite is documented by its own comments and this spike note (it
  is spike tooling, not a headline feature).

## Acceptance criteria

- The asserted exhaustive suite runs to completion (all 240 combos
  attempted; pytest exits with whatever pass/fail truth it found).
- Every failure and every distinct warning pattern in the 240 `.out` files
  is classified in the report — nothing labeled "unexplained" without a
  surfaced follow-up.
- The report and provenance files exist under `design/spike_artifacts/`;
  no runner code changed (`git diff` touches only the new suite yaml, the
  spike doc, and spike artifacts).

---

# appendix: original notes

- run this command:
```
uv run pytest src/tests/test_driver_combos.py \
  --suite-config=src/tests/config/suite/exhaustive-maccity-run-only-suite.yaml -vs
```
- analyze log output from the cece driver
- report any failures or abnormal output
  - include warnings as they may indiate problems in the driver
- enable suite "assertions". file count, etc.
- create a markdown report in design/spike_artifacts summarizing failures
- no code changes! if code changes are required, surface requirements and we'll make feature and revisit the spike
- goal: identify anything fishy in the cece execution. it says it works, but does it?
  - only looking at execution, no interest in the output contents at this pointK
