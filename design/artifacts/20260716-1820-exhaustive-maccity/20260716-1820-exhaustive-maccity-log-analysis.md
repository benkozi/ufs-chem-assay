# Exhaustive maccity execution audit — log analysis report

Spike: `design/spike/20260716-1820-analyze-exhaustive-maccity-logs.md`.
First real execution of the exhaustive sweep; question under test: *the
driver says it works — does it?* Scope is execution evidence only (exit
codes, logs, file existence, byte-level output identity); no NetCDF value
or attribute inspection.

## Run metadata

| | |
|---|---|
| run_id | `01KXPMQEHK6WCFA17CG5BXHR0B` |
| date | 2026-07-16, 18:38:27 → 18:40:32 (wall **125.26 s**) |
| suite | `exhaustive-maccity-asserted` (240 combinations; sweep identical to the run-only suite, file-count + filename assertions enabled) |
| image / driver | `cece/cece-dev` (built 2026-07-13); `build/cece_standalone_driver` built 2026-07-16 10:42 — fresher than the last C++ change (2026-07-15, `a08efc7`) |
| provenance | `20260716-1820-exhaustive-maccity/` (run.yaml, combos.csv, test-report.csv); raw `.out`/`.nc` in pytest tmp `pytest-43/combo_runs0`, not retained in git |

## Outcome summary

**Every combination passed everything that ran.** No failures, no timeouts,
no missing or misnamed output files.

| test family | passed | failed | skipped |
|---|---|---|---|
| test_driver_execution | 240 | 0 | 0 |
| test_nc_file_count | 240 | 0 | 0 |
| test_nc_filenames | 240 | 0 | 0 |
| test_species_attributes / baseline / stats | 0 | 0 | 720 (disabled by design) |

- Every combo wrote exactly 3 NetCDF files with the expected hour-1..3
  names.
- Every log contains the normal completion line (`CECE Finalize completed
  successfully`); none are empty or truncated.
- Timing: mean **0.51 s** per driver run, max 1.3 s — nowhere near the 10 s
  timeout. (The design's 25–30 min estimate assumed the stale ~6–7 s/combo
  observation; the real exhaustive run costs ~2 minutes. Exhaustive real
  runs are cheap.)

## Failures

None.

## Warnings

Exactly one distinct warning pattern across all 240 logs:

```
Kokkos::OpenMP::initialize WARNING: OMP_PROC_BIND environment variable not set
```

×240 (once per run, from Kokkos initialization). **Verdict: benign** —
standard Kokkos advice because the container doesn't set `OMP_PROC_BIND`;
it does not indicate a driver defect. Optional cleanup: the runner could
pass `-e OMP_PROC_BIND=false` (Kokkos' own recommendation for unit-test
contexts) to silence it. Surfaced as a nice-to-have, not a problem.

## Abnormal output in passing runs — the fishy section

The runs are *too* uniform. Every one of the 240 logs is **exactly 109
lines** with ~identical content and ~identical runtime, across 6 regridders,
5 vertical-distribution methods, 2 temporal interpolations, and 2 taxmodes.
Two structural findings explain most of it; one finding remains genuinely
suspicious.

### F1 — The driver logs nothing about its effective configuration (observability gap)

No log mentions `mapalgo`/regridding, `tintalgo`, `taxmode`, or `vdist` in
any form — grep over all 240 files finds zero occurrences. The logs cannot
distinguish a cubic-regrid PBL-vdist run from a passthrough single-level
run. Exit code 0 plus a config echo of *nothing* is weak evidence of
"working". This is the root reason the spike had to fall back on output
hashing (F2) for credibility evidence.

### F2 — Output-identity experiment: only 2 distinct outputs in 240 combos

SHA-256 over each combo's concatenated `.nc` bytes (byte identity is
execution evidence, not content inspection):

| dimension varied | effect on output bytes |
|---|---|
| `mapalgo` | **only dimension with any effect** — `conss` produces one output; `passthrough`, `nn`, `bilinear`, `cubic`, `consd` are **bit-identical** to each other |
| `tintalgo` (linear vs nearest) | none — bit-identical (suspicious, see F3) |
| `taxmode` | none — expected: taxmode is inert in standalone mode (enum audit) |
| `operation` (add vs replace) | none — legitimate: with a single species entry the two operations coincide |
| `vdist_method` (all 5) | none — structurally inevitable, see F4 |

The mapalgo collapse is *probably* legitimate: the scenario's source grid
(`MACCity_4x5.nc`, 72×46) exactly matches the target grid (72×46), so
nearest/bilinear/cubic/1st-order-conservative all reduce to the identity
map; only 2nd-order-conservative (`conss`) adds gradient terms. But the
consequence stands: **this scenario cannot distinguish 5 of the 6
regridders** — the exhaustive sweep exercises their code paths but proves
nothing about them.

### F3 — `tintalgo: linear` and `nearest` are bit-identical (suspicious)

At a 3-hour offset inside a monthly dataset, linear interpolation between
bracketing records should differ from nearest-record — unless the driver is
effectively sampling a single record (endpoint clamping? one record loaded?
degenerate weights from the odd `yearAlign: 2020` vs data years 2000–2010
under `cycle`?). Nothing in the logs says which records or weights were
used (F1), so this cannot be resolved from execution evidence. **This is
the one finding that may indicate temporal interpolation is not actually
functioning in the standalone driver.**

### F4 — vdist effects are structurally unobservable in this scenario

The output files have `lev = 1`: a single vertical level. Whatever the five
vdist methods do internally, a one-level output cannot show it — identical
bytes across vdist methods are inevitable, not evidence of a defect. Combined
with F1 (no vdist logging) and the enum audit's silent-fallthrough finding
(unknown methods silently run as `single`), there is currently **no way to
verify vertical distribution executes at all** in the standalone driver.

## Surfaced feature requirements (no code changed in this spike)

1. **Driver observability** (driver-side): log the effective per-stream
   regrid method, temporal interpolation choice (and per-step bracketing
   records/weights, at least at debug level), and per-entry vdist method +
   parameters at startup. Directly resolves F1 and would settle F3.
2. **Grid-mismatch scenario** (runner): a base config whose source and
   target grids differ, so the regridders produce distinguishable outputs
   and `mapalgo` sweeps become meaningful (F2).
3. **Temporal-interpolation probe** (runner, pending req 1): a scenario
   where `linear` vs `nearest` provably must differ (e.g. run time centered
   between two records); assert outputs differ across tintalgo. Would turn
   F3 from suspicion into a pass/fail test.
4. **Multi-level scenario** (runner + possibly driver): output with
   `lev > 1` so vdist methods produce observable differences (F4).
5. **Optional**: pass `OMP_PROC_BIND=false` into the container to silence
   the sole warning; update the feat design's runtime estimate (real cost
   is ~2 min, not 25–30 min).

## Verdict

Execution mechanics are solid: 240/240 clean exits, correct file counts and
names, no timeouts, deterministic-looking behavior, one benign warning. But
the spike's core question — *it says it works, but does it?* — is only
half-answered: for `tintalgo`, `vdist`, and 5 of 6 `mapalgo` values, this
scenario produces **no evidence that the option changes execution at all**,
and the driver's silence (F1) leaves no way to tell "correctly identical"
from "silently ignored". The requirements above are the path to closing
that gap.
