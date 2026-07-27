# Fix: consolidate CECE examples until `--run-examples` passes

## Goal

Execute the consolidation prescribed by
`design/artifacts/20260720-0950-examples-report.md` in the CECE checkout
(branch `fix/all-examples-pass`): one example set, one download location,
every discovered example green under the runner's `--run-examples` gate.
Work happens in CECE's **examples and scripts only — never `CECE/src`**
(the driver-robustness finding from the report stays unfixed here). The
runner is the verification harness and changes only where consolidation
forces it (one discovery constant — see below). Fundamental blockers
(broken downloads, data with no source) are reported back, not papered
over.

## Pre-design audit

Fresh facts from the CECE checkout (`fix/all-examples-pass`), 2026-07-20
afternoon — **the root `examples/` set was edited this morning** and has
moved since the examples report:

1. **Root `examples/` is the living, current-schema set**: ex1–ex7 +
   `advanced` + `megan3` (9 configs; 8 `cece_data:`, 9 `driver:` blocks).
   ex2–ex6 already reference container-style `/work/data/*.nc` paths —
   they are runner-ready. ex1 and ex7 (and `advanced`) still carry
   absolute `/gpfs/...` HTAP paths and `hourly` scale-factor references.
   `scripts/examples/` (ex1–ex6) remains the stale `cdeps_inline_config`
   set that crashes the driver — strictly worse, nothing unique.
2. **Download scripts fetch by the OLD numbering.** Aggregate fetched set:
   MACCity_4x5, EMEP.2000.co, Canada_mask, AEIC, GFED4_199701,
   MACCity_anthro_NOx/SO2, ALK4_CEDS_1970, EDGAR_v43.NOx.POW. Against the
   *root* set: ex2/ex3/ex5/ex6 fully covered; **ex4 needs
   `HTAPv3_NO_0.1x0.1_2018.nc`, which nothing fetches** (its script
   fetches GFED4+MACCity — the old ex4's needs); ex1/ex7 need the five
   HTAP v2015-03 NO sector files + hourly, none fetched.
3. **Fetch source**: `scripts/download_hemco_data.py` → the public
   `geos-chem` S3 bucket over HTTPS. Whether HTAP v2015-03 sector files,
   HTAPv3 2018, and hourly scale factors exist there is a cheap HTTP
   probe at implementation time (the HEMCO catalog nominally carries
   HTAP; plausible but unverified).
4. **`cece_driver_ex{1,2,3,4,6}.cfg` are dead.** Legacy NUOPC
   driver-timing configs (`timesteps:`, `start_date:` …) superseded by
   the YAML `driver:` block. Zero references in `src/`, `scripts/`,
   `CMakeLists.txt`, or `examples/README.md` (the CMake grep hit is
   `cece_driver_facade.cpp`, a source file; remaining mentions are
   historical docs/specs — records, fine to leave). **Answer to the
   requirement's question: not needed — remove them.**
5. **Duplicate root-level `scripts/download_ex*.sh`** persist (ex1's copy
   differs from `data_download`'s); `examples/README.md` is from June 30
   and predates today's config edits.
6. **Runner coupling**: discovery globs
   `<root_dir>/scripts/examples/cece_config_ex*.yaml`
   (`examples.py::_EXAMPLES_SUBDIR`). Consolidating onto root
   `examples/` therefore requires exactly one runner-side change (the
   constant), plus the harness tests/docs that mention the path. The
   glob (`cece_config_ex*`) will then discover **ex1–ex7** — all seven
   are in the pass gate; `advanced` and `megan3` don't match the pattern
   and stay out of it.

## Design

### Canonical layout (CECE side)

- **Keep**: root `examples/` (configs + README) and
  `scripts/data_download/` (+ `download_hemco_data.py`).
- **Delete**: `scripts/examples/` (all six stale, all crash);
  root-level `scripts/download_ex{1..6}.sh` duplicates (the ex1
  divergence is resolved by deletion — `data_download`'s helper-based
  copy is the good one); `examples/cece_driver_*.cfg` (audit #4).
- `examples/README.md` rewritten for the final set: per-example intent,
  the download-then-run flow (`scripts/data_download/download_exN.sh`
  then docker with the repo mounted at `/work`), and the runner's
  `--run-examples` as the regression gate.

### Download-script realignment (CECE side)

Rewrite each `scripts/data_download/download_exN.sh` to fetch exactly
what **root** `cece_config_exN.yaml` needs (audit #2), via the existing
`download_hemco_data.py`. Shared files across examples are fine to fetch
repeatedly (the helper hits the same paths; caching is its concern, not
ours). ex4's `HTAPv3_NO_0.1x0.1_2018.nc` and ex1/ex7's HTAP v2015-03
sector files + hourly scale factors get HTTP-probed against the
geos-chem bucket first.

### Revision (2026-07-20 pm): HTAPv3 for ex1/ex7, not EDGAR

Direction from review: **don't move the examples off HTAP-family data.**
The first implementation pass substituted EDGAR v4.3 sector files (ladder
rung 2); it is replaced by `HEMCO/HTAPv3/v2022-12/` — the direct successor
to the original HTAP v2015-03:

- One file per year, `HTAPv3_NO_0.1x0.1_<year>.nc`, sectors as variables
  (`NO_TRA`, `NO_SHP`, `NO_RCO`, `NO_IND`, `NO_ENE`, …) — a 1:1 match for
  the original five sector streams, which keep their HTAP names and all
  read the same file with different variable mappings (precedent: the
  original ex1's CAMS monthly file served two streams).
- Years 2000–2018 available; ex1/ex7 simulate 2010, so
  `2010/HTAPv3_NO_0.1x0.1_2010.nc` makes the year window trivial.
- Monthly records restore ex7's `cadence: "monthly"` on the sector
  streams (the annual EDGAR data had forced legacy cycling).
- The CAMS-TEMPO temporal weights remain publicly unavailable; the
  EDGAR-hourly + GEIA-monthly scale-factor stand-ins **stay for now**
  while the user evaluates other CAMS options — noted in the configs.

### Revision 2 (2026-07-20 eve): CAMS-TEMPO restored, expected-fail

Further direction: **no dataset substitution for the temporal weights
either.** The EDGAR-hourly/GEIA-monthly stand-ins are removed and the
original CAMS-TEMPO v3.1 streams restored verbatim in ex1/ex7 (all four:
hourly, weekly, monthly, monthly_ind; `/work/data/` paths):

- `download_ex{1,7}.sh` fetch the CAMS files from **aspirational
  geos-chem keys** (`HEMCO/CAMS-TEMPO/v3.1-2021/<file>`) that 404 today —
  the moment the data is published at those keys (the user is asking
  their team for public sources), the scripts start working unchanged.
  Available files (HTAPv3) are fetched *before* the CAMS lines, so under
  `set -e` a CAMS 404 fails the script honestly without blocking them.
- Consequence, accepted explicitly: **ex1/ex7 fail the `--run-examples`
  gate** (missing input files) until a CAMS source exists — recorded in
  the session examples-report and the examples README, never masked
  (no xfail). The gate's green set is ex2–ex6.
- A search of `noaa-ufs-srw-pds/experiment-user-cases/
  release-public-v3.0.0/fix/fix_emis` (25k keys) found **no CAMS/TEMPO
  data** — but it does carry the original `HTAP/v2015-03` sector files,
  noted as an alternative sector source if HTAPv3 is ever unwanted
  (not used: staying on the geos-chem bucket per direction).
- **CAMS weights located (2026-07-20, revision 2a)**: the user placed
  the v3.1 weight files in `data/` — `_hourly_fixtime_utc.nc` (148 MB;
  verified: `FH_weekday_tro`, 24 records, 0.1°, 2021), `_weekly.nc`
  (6 MB) and `_monthly.nc` (1.17 GB) under their canonical names, plus a
  34 MB `_hourly.nc` local-time variant (unused). The `_utc` name was
  adopted as canonical for the hourly stream (ex1/ex7 configs +
  `download_ex{1,7}.sh` aspirational keys).
- **New blocker (2a): AMIO staging capacity, a src-side limit.** With the
  data present, ex1 advances — all five HTAPv3 sector reads succeed —
  then dies with `AMIO_ERR_STAGING_BACKPRESSURE`: the facade hardcodes
  the AMIO read manifest (`staging_pool: buffer_count 8,
  buffer_capacity_bytes 256 MB, staging_timeout_ms 30000`), and
  `FH_weekday_tro` (24 × 3600×1800 ≈ 622 MB uncompressed) can never fit
  a buffer; `FM_tro` (~311 MB) will hit the same wall, only the weekly
  file fits. No driver-YAML knob exists — resolving needs a src change
  (configurable staging pool and/or per-record staging for
  whole-variable weight reads). ex1/ex7 therefore remain expected-fail,
  now blocked on the driver rather than on data availability — a
  candidate finding to fold into the AMIO spike
  (`design/spike/20260720-1654-…`) or file as its own CECE issue.
  *(SUPERSEDED by revision 2b below: the backpressure was caused by a
  dimensionally mis-ordered input file, not the staging pool — the
  misattributing error message is now an error-clarity finding in the
  AMIO spike.)*

### Gate state after the 2026-07-21 merges

PR #59/#64 landed and `feature/helm` was merged into the working branch;
the driver was rebuilt on the merged codebase. Interim state: ex3–ex6
green, ex2 flaky (post-merge race), ex1/ex7 apparently blocked on
staging backpressure.

### Revision 2b (2026-07-21 eve): backpressure was the data, not the pool
— **gate 7/7 GREEN**

The user spotted that the `_utc` hourly file had **wrong dimension
ordering**; reverting the hourly stream to the correctly-ordered
`…_hourly.nc` copy (configs + download keys; the `_utc` file was
removed from `data/`) made **ex1 and ex7 pass — full gate 7/7**,
including clean reads of the 6 MB weekly and 1.17 GB monthly files.

Post-mortem: the `AMIO_ERR_STAGING_BACKPRESSURE` on the `t=0` read was
**misattribution** — a dimensionally mis-ordered input made AMIO stage
something pathological until the pool starved, and the error surfaced as
a resource complaint with no hint of the data-layout cause. Both staging
hypotheses (capacity H4a, pool-leak H4b) were wrong; the finding
transfers to the AMIO spike as a **diagnosability issue** (validate/report
dimension ordering instead of timing out with backpressure).

Remaining truths: the ex2 intermittent race stands (it simply didn't
fire in the green run) — spike scope; and CAMS-TEMPO still has **no
public download source** (the aspirational keys 404), so
download-then-run from a fresh machine fails for ex1/ex7 until the data
is published — local `data/` copies are required.

### Revision 2c (2026-07-21): sector data reverted to the original
EDGAR-HTAP v2015-03 files

The user placed the **original five sector files** in `data/`
(`EDGAR_HTAP_NO_{TRANSPORT,SHIPS,RESIDENTIAL,INDUSTRY,ENERGY}.generic.01x01.nc`
— verified: variable `emi_no`, 36 monthly records 2008–2010, 0.1°) and
directed a revert from the HTAPv3 substitution: ex1/ex7 return to the
original five-stream, five-file layout. Details:

- **Public source exists for these** — unlike CAMS, the exact files live
  in `noaa-ufs-srw-pds` under
  `experiment-user-cases/release-public-v3.0.0/fix/fix_emis/HTAP/v2015-03/NO/`
  (HEAD-verified). `download_hemco_data.py` is geos-chem-only, so
  `download_ex{1,7}.sh` fetch the sector files with direct `curl -L -f`
  against that bucket; the CAMS fetches stay aspirational
  (geos-chem keys, expected 404) and remain last.
- Year window set to the data's honest span (`yearFirst 2008,
  yearLast 2010`, `yearAlign 2020` per the established convention —
  the original's `yearFirst 2000` claimed years the files don't have);
  ex7's sector streams regain `cadence: "monthly"` (the files are
  monthly, as the original assumed).
- Species/field names were never renamed away from `HTAP_*`, so only the
  stream `file:`/year blocks change. `HTAPv3_NO_0.1x0.1_2010.nc` (now
  unused) is removed from the `data/` cache; ex4 keeps its own HTAPv3
  2018 file.

### Revision 2d (2026-07-21): CECE docs decoupled from the runner

Per direction, **CECE content no longer mentions the combo-test-runner**:
the `examples/README.md` intro drops the runner link and
`--run-examples` invocation ("expected to run green" stands on its own),
and ex7's known-bug comment drops its pointer to the runner's examples
report. CECE's docs describe the examples in their own terms; the
gate relationship (`--run-examples` exercising `examples/`) is recorded
runner-side only (this doc, `design.md`, the runner README). Untracked
local run artifacts (`ctr-output/`) were not touched.

### Making ex1/ex7 (and ex4's data) pass — decision ladder

In order, per missing dataset:

1. **Fetchable from the bucket** → add it to the example's download
   script and repoint the config `file:` entries from `/gpfs/...` to
   `/work/data/...`. Config edits are sanctioned ("okay to update
   configurations in CECE/examples").
2. **Not fetchable, equivalent available** → substitute a dataset the
   bucket does carry, preserving the example's teaching intent (ex1/ex7
   demonstrate multi-sector NO with masks/scale factors — any
   multi-sector inventory in the bucket serves), and note the
   substitution in the config's comments and README.
3. **Neither** → the requirement's report-back clause: the example is
   recorded as fundamentally blocked (what's missing, where it was
   probed), left failing honestly, and flagged in the implementation
   notes — no masking, no xfail.

ex1 vs ex7 look like near-duplicates (same HTAP sector stack); if
implementation confirms, fold ex7 into ex1 (or re-scope ex7 to something
distinct) rather than maintaining two copies of the same lesson — fewer,
meaningful examples is the point of consolidation. `advanced`/`megan3`
are outside the pass gate; they get the same `/gpfs` fix **only if** the
data falls out of the ex1/ex7 work for free, otherwise they are left
untouched and noted.

### Runner side (the one deliberate change)

- `examples.py`: `_EXAMPLES_SUBDIR` becomes the public
  `EXAMPLES_SUBDIR = Path("examples")` — public so the gating harness
  builds its fabricated tree from the constant instead of hardcoding the
  path (today's tests hardcode `scripts/examples`).
- Docs that state the path (`design.md`, `README.md`, `--run-examples`
  help text) updated to match. Nothing else: gating, downloads,
  reporting, and the collection guard are location-agnostic already.
- This is the resolution of "in theory, there should be no changes":
  the theory assumed examples stay under `scripts/examples/`; keeping
  the stale location alive just to avoid a one-constant change would
  invert the priorities.

## Plan

TDD applies to the runner change; the CECE work is configuration whose
red/green loop **is** `--run-examples` itself:

1. Red (runner): gating tests build the fake tree from `EXAMPLES_SUBDIR`
   and fail while the constant still says `scripts/examples`; flip the
   constant; harness green.
2. Probe the bucket (HTTP) for every missing dataset; walk the decision
   ladder per example; rewrite download scripts; repoint configs.
3. Delete: `scripts/examples/`, root `scripts/download_ex*.sh`,
   `examples/cece_driver_*.cfg`. Rewrite `examples/README.md`.
4. Gate: from the runner repo, `uv run pytest src/tests/test_examples.py
   --run-examples` → every discovered example passes (downloads
   included, from a clean `data/` state if feasible to prove
   reproducibility). The session `examples-report.md` records the green
   run.
5. Standing checks: harness suite, both exhaustive suites `--dry-run`,
   `simple-maccity-suite.yaml` integration, pre-commit hooks — all green.
6. Report-back section in this doc's implementation notes for anything
   that hit rung 3 of the ladder.

## Ripples (standing process rules)

- **`design.md`** (runner): the `--run-examples` bullet and layout
  references say `examples/` instead of `scripts/examples/`.
- **`README.md`** (runner): same path correction in the Options bullet.
- `design/artifacts/20260720-0950-examples-report.md` stays as-is — it is
  the record this fix executes; this doc's implementation notes record
  the outcome (including the cfg-removal answer and any rung-3 blockers).
- CECE-side commits happen on `fix/all-examples-pass` in the CECE repo
  (its own history; this repo only records the design + notes).
- Pre-commit hooks pass; pydantic/TDD rules apply to the (one) runner
  change.

## Acceptance criteria

- One example location (`examples/`), one download location
  (`scripts/data_download/`); `scripts/examples/`, the root duplicate
  download scripts, and `cece_driver_*.cfg` are gone from CECE.
- Each `download_exN.sh` fetches exactly what root `cece_config_exN.yaml`
  needs; no config references `/gpfs` or any path a fresh
  `download-then-run` cannot satisfy — except examples explicitly
  recorded as blocked (ladder rung 3), of which there are ideally none.
- `uv run pytest src/tests/test_examples.py --run-examples` discovers
  ex1–ex7 in `examples/` and **all pass** (or the blocked exceptions are
  reported in the implementation notes with probe evidence).
- `CECE/src` untouched; runner diff is the discovery constant, its
  tests, and docs.
- Runner harness, dry-runs, integration, and pre-commit all green.

## Implementation notes

**Outcome: 7/7 examples pass** — `uv run pytest src/tests/test_examples.py
--run-examples` → `7 passed in 73s`, with a static check confirming every
config's `/work/data/` reference is fetched by its own download script.

**Report-back items (fundamental issues, per the requirement):**

- **HTAP v2015-03 and CAMS-TEMPO v3.1 are not in the geos-chem bucket**
  (verified by S3 listings). ex1/ex7 were rewritten (ladder rung 2) onto
  EDGAR v4.3 per-sector NOx (`emi_nox`; TRO/TNG/RCO/IND/POW, ~340 MB
  total) plus EDGAR hourly (`NOXscale`, 24 records) and GEIA monthly
  (`NOXrat`, 12 records) scale factors; ex2's EMEP stream became CEDS 1970
  CO. Each config carries a substitution note.
- **No public weekly-weight product exists** — the weekly scale factor /
  weekly-cadence demo was dropped from ex1/ex7 (commented in the configs
  and the examples README).
- **Driver bug (not fixed — no src changes): `amio_worker_threads: >= 2`
  segfaults during stream ingest.** Isolated by config bisection on ex7
  (all-cadences-no-threads passes; threads-no-monthly-cadence still
  crashes; `1` passes). ex7 pins the value to 1 with a KNOWN DRIVER BUG
  comment.
- **ex4's `physics_schemes` were fictional** (`GFED`,
  `DayCycleAndSeasonVariation` — neither registered; the driver aborts).
  The block was removed; ex4 is now the high-resolution-regrid example
  (HTAPv3 2018 `NO_SHP`, 450 MB, confirmed in the bucket at
  `v2022-12/2018`). Physics demonstrations remain in
  `advanced`/`megan3`, outside the gate.
- **The July-20 morning report's "downloads 6/6 ok" was wrong**: the old
  scripts lacked `set -e`, so only the last fetch's status surfaced —
  ex2's EMEP and ex6's EDGAR fetches had 404'd silently (a correction
  addendum was added to that artifact). The rewritten scripts use
  `set -euo pipefail`, self-locate to the repo root, and **skip fetches
  whose target already exists** (an ~800 MB re-download per
  `--run-examples` session otherwise).

**Other deltas from the design:**

- ex7 is *not* an ex1 duplicate (it adds `cadence` handling and
  `amio_worker_threads`) — both kept, no fold.
- A `download_ex7.sh` was created (ex7 previously had no script).
- ex6's variable names were wrong against the real files
  (`NOx_POW`→`emi_nox`, `ALK4_butanes`→`ALK4_butanes_agr`); ex7's output
  pattern said `cece_ex1_` (copy-paste) — fixed.
- Deletions executed: `scripts/examples/` (6 stale configs),
  `scripts/download_ex{1..6}.sh` duplicates, `examples/cece_driver_*.cfg`
  (5 dead files). `examples/README.md` rewritten (lesson table,
  download-then-run flow, known-issue note).
- Runner diff as designed: `EXAMPLES_SUBDIR` (public, `examples/`),
  harness tests built from the constant (+ a location-pinning test), docs.

**Verified**: examples gate 7/7; harness 159/159 (no docker); mypy clean;
pre-commit all hooks green; exhaustive dry-run 1440 skips;
`simple-maccity-suite.yaml` integration 18/18. CECE work committed on
`fix/all-examples-pass` (first pass only; see revision below).

**Revision outcome (2026-07-20 pm, per the HTAPv3 revision above):**
ex1/ex7 and `download_ex{1,7}.sh` reworked onto
`HTAPv3_NO_0.1x0.1_2010.nc` (443 MB, one file, five sector streams via
variables `NO_TRA`/`NO_SHP`/`NO_RCO`/`NO_IND`/`NO_ENE`, monthly cadence
restored on ex7's sector streams, trivial 2010 year window). Original
HTAP stream/field names restored. Scale-factor stand-ins retained pending
the user's CAMS investigation. Gate re-verified: **7/7 in 90s**; script
coverage check green; the four now-unused EDGAR sector files were removed
from the (gitignored) `data/` cache — `EDGAR_v43.NOx.POW` stays for ex6.
This revision is uncommitted in the CECE working tree (no-commit rule).

**Revision 2 outcome (2026-07-20 eve, CAMS-TEMPO restored):** ex1/ex7
carry their original four CAMS-TEMPO weight streams again (verbatim,
`/work/data/` paths; ex7 with the hourly/weekly/monthly cadence
annotations restored) and the EDGAR-hourly/GEIA-monthly stand-ins are
gone (configs, scripts, and the cached data files). `download_ex{1,7}.sh`
fetch HTAPv3 first, then the three CAMS files from aspirational
`HEMCO/CAMS-TEMPO/v3.1-2021/` keys — verified behavior: exits 1 on the
CAMS 404 with no empty-file artifacts (the cache guard stays sound), and
starts working unchanged once data appears at those keys. Gate:
**5 passed, ex1/ex7 failed as expected** — the driver now fails cleanly
naming the missing CAMS file, and the session examples-report records the
download 404s verbatim. The `noaa-ufs-srw-pds` search (no CAMS; original
HTAP v2015-03 present) is recorded in Revision 2's design notes. All docs
(CECE examples README, runner README/design.md) state the ex2–ex6 green
set and the pending-CAMS failures.

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
- maintain original `always do` and `requirements` sections when refining design docs
- when using python `typing`, avoid `Any` as much as possible

### testing

- *all* suites should pass `--dry-run`
- run `simple-maccity-suite.yaml` without `--dry-run` for integration testing with the driver
- only run examples when requested to do so
- no need to run tests for spikes/documentation-only tasks
- pre-commit hooks pass

## requirements

- follow the recommendations from design/feat/20260720-0950-optional-run-examples.md
- fix and consolidate cece examples until they all pass
- DO NOT modify CECE/src code
- report back if there are fundamental issues that cannot be addressed (broken downloads or missing data)
- it's okay to update configurations in CECE/examples
- in theory, there should be no changes to the cece-combo-test-runner
- are the CECE files examples/cece_driver_*.cfg needed? maybe we can remove them
