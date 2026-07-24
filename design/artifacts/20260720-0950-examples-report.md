# CECE shipped-examples report — downloads, execution, consolidation

Produced by the first real `--run-examples` session
(`uv run pytest src/tests/test_examples.py --run-examples`), per
`design/feat/20260720-0950-optional-run-examples.md`. **No CECE file was
modified**; every fix below is prescribed, not applied.

- CECE checkout: `/Users/bkoziol/sandbox/git-benkozi/CECE`, branch
  `fix/all-examples-pass` @ `3f546b1` (content-identical to its `helm`
  branch point)
- Runner: `feat/initial-impl` at the `--run-examples` implementation
- Result: **downloads 6/6 ok; example executions 0/6 pass** (all exit 133)

> **Correction (2026-07-20, consolidation fix)**: "downloads 6/6 ok" below
> is wrong. The scripts lacked `set -e`, so a script's exit status was its
> *last* fetch only — ex2's EMEP fetch (404: EMEP is not in the bucket)
> and ex6's EDGAR fetch (404: `v2014-10` does not exist) failed silently
> behind later successful fetches. Discovered and fixed during
> `design/fix/20260720-1500-fix-cece-examples.md` (scripts now use
> `set -euo pipefail`).

## 1. Data downloads — all six succeeded

Every `scripts/data_download/download_ex{1..6}.sh` exited 0 (invoked with
cwd = the CECE root, which the scripts assume). The July-17 concern that
"some scripts might not work" did **not** materialize: the scripts now
route through `scripts/download_hemco_data.py`, and `data/` was populated
(`MACCity_4x5.nc`, CEDS/GFED/AEIC files, `speciation/`, masks, …).

One latent gap remains, currently unreachable (see §2):

- **`hourly` scale-factor data has no download source.**
  `cece_config_ex1.yaml` / `cece_config_ex2.yaml` declare
  `meteorology: hourly_scalfact: HOURLY_SCALFACT`, but no
  `data_download` script fetches an hourly file (`data/` contains none).
  If the schema fixes below land, ex1/ex2 will start failing on missing
  input data next. Prescribed fix: add the hourly fetch to the ex1/ex2
  scripts, or drop `hourly_scalfact` from those examples.

## 2. Example executions — all six crash identically

Every example fails the same way: the driver banner prints, the config is
opened, grid parsing falls back to defaults (`Parsed nx = 4, ny = 4,
grid_name = ''` — the configs have no `driver:` block), then:

```
terminate called after throwing an instance of
  'YAML::TypedBadConversion<std::__cxx11::basic_string<...>>'
  what():  bad conversion
*** Process received signal *** Signal: Aborted (6)
```

Container exit 133 for all six. Full outputs were captured per example as
`examples/<stem>.out` during the run (regenerable any time with
`--run-examples`).

**Root cause (one bucket): the stale example schema.** All six configs
declare their streams under `cdeps_inline_config:` and lack
`driver:`/`output:` blocks; the parser reads `cece_data:` (0 of 6 configs
have it) and hits a required string it cannot convert. The examples ship
in a schema the driver no longer reads.

**Secondary finding — driver robustness**: a config in the wrong schema
should be a clean, named-field parse error, not an uncaught
`YAML::TypedBadConversion` → `abort()` with an MPI backtrace. Prescribed
fix (driver repo): catch YAML conversion errors in the config parser and
report the offending key/path with nonzero exit.

**Prescribed fix for the examples**: port `scripts/examples/ex1..6` to
the current schema (`cece_data:` streams + `driver:` + `output:` blocks —
the repo-root `examples/` set and the runner's
`src/tests/config/cece/simple-maccity.yaml` show the current shape), or
adopt consolidation below and delete the stale set outright.

## 3. Consolidation recommendations (duplication)

Three overlapping artifact sets exist in CECE today:

| Location | Contents | State |
|---|---|---|
| `scripts/examples/` | ex1–ex6 | stale `cdeps_inline_config` schema; crashes the driver |
| `examples/` (repo root) | ex1–ex7 + `advanced` + own README | current schema, but README stale and (per July-17 audit) absolute HPC input paths |
| `scripts/download_ex*.sh` vs `scripts/data_download/` | 6 scripts each | ex2–ex6 byte-identical duplicates; **ex1's two copies differ** |

Recommendations, in order:

1. **One example directory.** Keep repo-root `examples/` as the single
   set; delete `scripts/examples/` (its six configs are strictly worse
   than the root set's current-schema counterparts). If ex-numbered
   parity matters, port anything unique first — nothing observed here
   suggests there is anything unique.
2. **One download location.** Keep `scripts/data_download/` (authoritative
   per the design notes, and the set this runner exercises); delete the
   six root-level `scripts/download_ex*.sh` duplicates. Reconcile ex1
   first — its two copies differ, and only one can be right.
3. **Make examples runnable from data the scripts fetch.** Replace the
   root set's absolute HPC paths with `data/`-relative paths covered by
   `data_download` scripts (adding the missing `hourly` source, §1), so
   "download then run" works on any machine.
4. **Keep them passing.** Once consolidated, this runner's
   `--run-examples` is the regression check — the examples become tested
   artifacts instead of drifting documentation. The
   `fix/all-examples-pass` branch is the natural home for all of the
   above.

## 4. Runner-side artifacts

- Per-example captured output: `examples/<stem>.out` under the session
  output root; session summary: `examples/examples-report.md` (both
  regenerable via `--run-examples`).
- The run used a temporary output root under the CECE checkout, removed
  after this report was written; `data/` downloads remain (gitignored,
  reused by future runs).
