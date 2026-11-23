# ops: the daily run and transfer

Two scripts for running the harness on a machine and sending what the run
left behind to the transfer repo, plus the environment both read.

| File | What it does |
|---|---|
| `env.sh` | Every path and choice: the harness checkout, the root, the run config, the applications and stages, the transfer repo, `uv`'s cache and Python. Edit the values here. |
| `assay-run.sh` | `module reset`, `git pull`, `ufs-chem-assay run`, then `ufs-chem-assay watch`, all shown and written to `<root>/logs/assay-run-<YYYYmmdd-HHMMSS>.log`. |
| `assay-transfer.sh` | Copies the root's logs, rendered scripts and records, tests roots and CMake caches to `<transfer repo>/<name>`, adds the harness's `report`, then commits and pushes. |

## The daily loop

```bash
cd /gpfs/f7/epic/proj-shared/Benjamin.Koziol/git/ufs-chem-assay
export ASSAY_ROOT=/gpfs/f7/epic/proj-shared/Benjamin.Koziol/sandbox/work-ufs-chem-assay-20261123a  # a fresh root
ops/assay-run.sh
ops/assay-transfer.sh
```

Without the export both scripts use `env.sh`'s `ASSAY_ROOT`. Export it once
per root, so the run and its transfer always name the same one.

## Changing a value

Every value in `env.sh` is a default, and an exported variable wins, for one
command or for the shell:

```bash
ASSAY_STAGES="build test" ops/assay-run.sh               # rebuild and retest, no clone
ASSAY_APPLICATIONS="catchem" ops/assay-run.sh --build musica
ops/assay-transfer.sh assay-20261123-musica-retry         # name the transfer directory
```

Arguments after `assay-run.sh` go to `ufs-chem-assay run` (`--toolchain`,
`--build`, `--override ...`).

## What `assay-run.sh` does about failures

- `watch` runs whatever `run` returned: after a failed build `run` has still
  submitted the other builds' test jobs, and `watch` evaluates and reports
  them.
- `watch` is skipped when `run` wrote no `scripts/run-config.yaml` (a config
  error) or when no stage selected leaves anything to wait for (no `test`,
  `harness` or `resume`).
- The last line says both: `assay-run: run exit N, watch exit M; transcript
  <path>`. The exit status is `run`'s when it failed, else `watch`'s.

Every harness line in the transcript names its command (`[run]`, `[watch]`),
and every stage log in `<root>/logs` starts with a header naming the command
and the run.

Design: `design/feat/20261113-1348-build-test-kokkos/20261123-1123-scripts-to-run-post-logs.md`.
