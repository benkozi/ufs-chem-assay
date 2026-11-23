# always do

- include updating design.md as part of the implementation
- update the harness tests (`src/tests/ufs_chem_assay`) in addition to any changes to test_driver_combos.py
- update README.md with any necessary documentation changes in case of an api adjustment
- use pydantic models as opposed to dataclasses
  - all pydantic fields should include a description like `... = Field(description="<description content here>", ...`
- do *not* add driver bugs to known bugs in `README.md` unless explicitly told to do so
- use a test-driven development, red-green-refactor approach for all fixes and features (when possible)
- maintain original design sections when refining design docs - create an appendix
  - summarize conversational updates in the appendix following original refinement target
  - refinements never create files. they only edit the target document that is being refined.
  - `design.md` may be updated during the _implementation_ phase only.
- when using python `typing`, avoid `Any` as much as possible
- **never, ever, ever** commit code or use git actions that perform updates - the user always updates
- always prefer the harness's logging implementation over raw python print statements
- no need for wrapping when writing to the `./design` folder. wrapping will be handled by the user's ide
- persist the agy session id and resume command in any design/refined files for resumability

# session

- **Session ID**: `5b8828d0-1bd9-4b6d-9259-4e5762769b1e`
- **Resume Command**: `agy --resume 5b8828d0-1bd9-4b6d-9259-4e5762769b1e`

## testing

- not necessary for design documents in the `spike` folder - code *should not* change for spikes
- *all* suites should pass `--dry-run`
- run `simple-maccity-suite.yaml` without `--dry-run` for integration testing with the driver
- only run examples when requested to do so
- no need to run tests for spikes/documentation-only tasks
- pre-commit hooks pass

# requirements

- i ran this command:
```
cd $HARNESS && git pull && { uv run ufs-chem-assay run     --config-file=config/gaea-c7.yaml     --application all     --root-dir $ROOT     --stage source --stage build --stage test &&   uv run ufs-chem-assay watch --root-dir $ROOT --only tests; } 2>&1   | tee $ROOT/logs/assay-$(date +%Y%m%d-%H%M%S).log.
```

i'm not sure what's going on.
- here is the command to copy logs again:
```
bash -s <<'EOF'
set -euo pipefail
shopt -s nullglob
_bw=~/l/c7-epic-proj-shared/git/benkozi-work
_wa=/gpfs/f7/epic/proj-shared/Benjamin.Koziol/sandbox/work-ufs-chem-assay
_harness=/gpfs/f7/epic/proj-shared/Benjamin.Koziol/git/ufs-chem-assay
_date=$(date +%Y%m%d-%H%M)
_dst=$_bw/transfer/assay-$_date

mkdir -p "$_dst/scripts" "$_dst/cmake"
cd "$_wa"
cp -r logs "$_dst/"                                                # stage logs
cp scripts/*.yaml scripts/*.sh scripts/*.sbatch "$_dst/scripts/"   # stages.yaml, run-config.yaml, rendered scripts
for t in */tests; do                                               # every toolchain's tests root
  mkdir -p "$_dst/$(dirname "$t")" && cp -r "$t" "$_dst/$t"
done
for c in */*/build*/CMakeCache.txt; do                             # each build's configured options
  cp --parents "$c" "$_dst/cmake/"
done
(cd "$_harness" && uv run --no-sync ufs-chem-assay report --root-dir "$_wa") \
  > "$_dst/report.txt" 2>&1 || true                                # exits 1 when it finds issues

cd "$_bw/transfer"
git pull
git add -f "assay-$_date"
git commit -m "assay $_date"
git push
echo "pushed $_dst ($(du -sh "$_dst" | cut -f1))"
EOF
```

- let's formalize these run scripts as bash scripts. i'm going to be using them all the time. 
- maybe have them source a shared environment that is easy to edit/maintain?

# Refined design (2026-11-23)

The two snippets in the requirements become two scripts in the harness repo, `ops/assay-run.sh` and `ops/assay-transfer.sh`, which source one small environment file per machine. Nothing in the harness CLI changes: the scripts call `ufs-chem-assay run`, `watch` and `report` as the snippets do, and fix what the snippets got wrong (§1).

## 1. What is going on with the snippets

Read against the last three C7 transfers (`assay-20261123-1143`, `-1238`, `-1316`) and the harness code. Four things, two of them visible in the transfers:

1. **`report.txt` is always `bash: line 19: uv: command not found`.** The transfer snippet runs in `bash -s`, a non-interactive shell that reads no profile. It sees `uv` only if the shell that started it exported a `PATH` holding `~/.local/bin`, and the run snippet's `export ... PATH="$HOME/.local/bin:$PATH"` lives in a different shell (the one that ran the harness). The same gap would bite next: without `UV_CACHE_DIR` and `UV_PYTHON_INSTALL_DIR`, `uv` would look for its cache and Python under `$HOME`, not under `$ROOT`. Fix: both scripts source the same environment (§3), so `uv` and its directories are the same in each.
2. **The transcript is missing from the transfers.** The `| tee $ROOT/logs/assay-<stamp>.log` should have put a file in `logs/`, and no transfer has one. Every transfer shows `$ROOT` reset between runs: the source stage logs `Cloning into ...` each time (it clones only when `<root>/oneapi/<App>/.git` is missing), `logs/` holds only that run's stage logs, and each `benchmarks.csv` has one row. That is how you test: a fresh root each time, by setting `ROOT` to a new path or removing the old one (Q3). With a fresh root, `$ROOT/logs` does not exist when `tee` opens its file: `tee` prints `No such file or directory` once, at the top, and passes everything through to the terminal only (the harness creates `logs/` later, at the first stage). `assay-run.sh` creates `logs/` before opening the transcript (§4.1).
3. **`run ... && watch ...` skips `watch` after any failure.** Since failure isolation (2026-11-20), `run` still submits the test jobs of every build that passed, then exits with the first failure's status. The `&&` then never starts `watch`, so those jobs are submitted, run, and never evaluated or reported in that session. `assay-run.sh` runs `watch` whatever `run` returned, when the run had a stage to wait for, and exits with the worse of the two (§4.1).
4. **`watch --only tests` is no longer needed.** It worked around C7's nameless session (no `harness.submit_only`) making a plain `watch` refuse. Since 2026-11-23 `watch` leaves out what the run did not start, so a plain `watch` follows the test runs alone on a source/build/test run, and also a session when one is run. The script drops `--only tests`.

Also, the preamble's `module reset` and the `LD_LIBRARY_PATH` / `PYTHONPATH` echo: every stage script starts with its own `module reset`, so the preamble's reset matters only for `uv` and `git` in the calling shell. It stays as a recorded check: the transcript's header prints both variables, so a run from a polluted shell is visible in the transfer.

## 2. Layout

```text
ops/
  env.sh            # the shared environment: every path and choice, sourced by both scripts
  assay-run.sh      # the run snippet: module reset, git pull, run, watch, with a transcript
  assay-transfer.sh # the transfer snippet: copy the evidence, report, commit and push
  README.md         # the daily loop and what to edit in env.sh
```

Three files of shell and a README, written fresh from the two snippets in the requirements. They do what the snippets do, with the §1 fixes and no more. `ops/` rather than `scripts/`, which would read as `<root>/scripts` (the rendered stage scripts).

## 3. `ops/env.sh`, the shared environment

The snippets' exports and paths, in one place, each a default an exported variable overrides (`: "${ASSAY_ROOT:=...}"`), so `ASSAY_STAGES="build test" ops/assay-run.sh` changes one value for one run:

```bash
# ops/env.sh: sourced by ops/assay-run.sh and ops/assay-transfer.sh. Edit the values here.
: "${ASSAY_HARNESS:=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"  # this checkout
: "${ASSAY_ROOT:=/gpfs/f7/epic/proj-shared/Benjamin.Koziol/sandbox/work-ufs-chem-assay}"
: "${ASSAY_CONFIG:=config/gaea-c7.yaml}"                                 # relative to ASSAY_HARNESS
: "${ASSAY_APPLICATIONS:=all}"
: "${ASSAY_STAGES:=source build test}"
: "${ASSAY_TRANSFER_REPO:=$HOME/l/c7-epic-proj-shared/git/benkozi-work/transfer}"
export UV_CACHE_DIR="${UV_CACHE_DIR:-/gpfs/f7/epic/proj-shared/Benjamin.Koziol/sandbox/uv-cache}"
export UV_PYTHON_INSTALL_DIR="${UV_PYTHON_INSTALL_DIR:-/gpfs/f7/epic/proj-shared/Benjamin.Koziol/sandbox/uv-python}"
case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) export PATH="$HOME/.local/bin:$PATH" ;; esac
```

Variables only: no `set -e`, no `module` calls, no output, so it can also be sourced in an interactive shell before a `uv run ufs-chem-assay report ...` by hand. The values are yours and C7's as the snippets have them (Q2), with one change: `uv`'s cache and Python live beside the roots, not inside one (the snippet had `$ROOT/uv-cache` and `$ROOT/uv-python`). A fresh root per test (Q3) would otherwise download Python and every wheel again each run, and removing a root would remove the `uv` that runs the harness.

## 4. The scripts

Each is the snippet it replaces, as a file: `set -euo pipefail`, `source "$(dirname "$0")/env.sh"`, then the snippet's commands with the snippet's variables renamed to the env's.

### 4.1 `ops/assay-run.sh`

The run snippet, in order:

1. `module reset`, then echo `LD_LIBRARY_PATH` and `PYTHONPATH`, as the snippet does.
2. `mkdir -p "$ASSAY_ROOT/logs"` (§1.2), then the rest of the script to the terminal and to `$ASSAY_ROOT/logs/assay-run-<YYYYmmdd-HHMMSS>.log` (`exec > >(tee -a "$transcript") 2>&1`). No stage script is named `assay-run`, so the report's log lookup never takes the transcript for a stage log.
3. `cd "$ASSAY_HARNESS" && git pull`.
4. `uv run ufs-chem-assay run --config-file="$ASSAY_CONFIG" --application $ASSAY_APPLICATIONS --root-dir "$ASSAY_ROOT"`, one `--stage` per word of `ASSAY_STAGES`, then any arguments given to the script (`ops/assay-run.sh --toolchain oneapi`). Its status is kept (§1.3).
5. `uv run ufs-chem-assay watch --root-dir "$ASSAY_ROOT"` (no `--only tests`, §1.4), whatever `run` returned, as long as `run` wrote `scripts/run-config.yaml` (a config error leaves nothing to watch).
6. The last line: `assay-run: run exit N, watch exit M; transcript <path>`. The exit status is `run`'s when nonzero, else `watch`'s.

### 4.2 `ops/assay-transfer.sh`

The transfer snippet's body, unchanged in what it copies, with `_wa`, `_harness` and `_bw/transfer` replaced by `ASSAY_ROOT`, `ASSAY_HARNESS` and `ASSAY_TRANSFER_REPO`. The one change is the report: `(cd "$ASSAY_HARNESS" && uv run --no-sync ufs-chem-assay report --root-dir "$ASSAY_ROOT")`, which now finds `uv` and its cache because `env.sh` set them (§1.1). The `logs/` copy carries the run transcripts. An optional first argument names the directory instead of `assay-<YYYYmmdd-HHMM>`.

## 5. The daily loop, after this

A fresh root is one export; both scripts read it, so the run and its transfer always name the same root:

```bash
cd /gpfs/f7/epic/proj-shared/Benjamin.Koziol/git/ufs-chem-assay
export ASSAY_ROOT=/gpfs/f7/epic/proj-shared/Benjamin.Koziol/sandbox/work-ufs-chem-assay-20261123a
ops/assay-run.sh
ops/assay-transfer.sh
```

Without the export, both use `env.sh`'s `ASSAY_ROOT`. `ops/README.md` says the same, and `docs/kokkos-runbook.md` §8 points at it.

## 6. Tests

`src/tests/ufs_chem_assay/test_ops_scripts.py` runs both scripts with bash, `ASSAY_*` exported to a scratch root and transfer repo, and `uv`, `git` and `module` as stubs on `PATH` that record their arguments and exit as told:

- `assay-run.sh`: the transcript created when `logs/` is missing, `run` called with each stage and the script's arguments, `watch` called after a failed `run` and the worse status returned, no `watch` when `run` wrote no effective config, an exported `ASSAY_STAGES` winning over `env.sh`;
- `assay-transfer.sh`: the copied tree, `report.txt` holding the report even when `report` exits 1, and `git add`, `commit` and `push` in the transfer repo.

Python coverage stays 100% (the scripts are not Python).

## 7. Implementation plan (TDD)

One chunk: red, the §6 tests; green, `ops/env.sh`, `ops/assay-run.sh`, `ops/assay-transfer.sh`; docs, `ops/README.md`, a README pointer, `docs/kokkos-runbook.md` §8 and `design/design.md` (the layout). Verification: the harness suite at 100%, every suite `--dry-run`, `simple-maccity` without `--dry-run`, pre-commit.

## 8. Questions, resolved (round 2)

"recommendations are okay. for 3 - i have been removing the root and starting fresh for testing. hence, it's just setting ROOT=/path/to/whatever"

1. **Location:** `ops/`.
2. **The values in `env.sh`:** tracked, with your paths.
3. **The reset:** none in the scripts. A fresh root is a new `ASSAY_ROOT` (§5), or removing the old one by hand. Two consequences are taken into the design: `uv`'s cache and Python move out of the root (§3), and the transcript's `mkdir -p logs` (§4.1) is what makes a fresh root's transcript appear.
4. **`watch` after a failed `run`:** always.

# Appendix A — refinement log

- **2026-11-23, round 1** ("let's refine this doc ... i'm not sure what's going on with the scripts"). Read the requirements' two snippets against the transfers `assay-20261123-1143`, `-1238` and `-1316` and the harness. Findings (§1): the transfer's `report.txt` fails because `bash -s` reads no profile and the `uv` exports live in the run's shell; the missing transcript fits a reset `$ROOT` (fresh clones, one run's logs and one benchmark row in every transfer) leaving no `logs/` when `tee` opens its file; `run && watch` leaves the test jobs of passing builds unevaluated after any failure; `watch --only tests` is obsolete since the stage-scope change. Designed (§2–§7): `ops/` with a per-machine env file plus an untracked `local.sh`, a `lib.sh` with the `assay()` wrapper, `assay-run.sh` (optional `--clean`, the transcript opened after `mkdir -p logs`, a header, `git pull --ff-only`, `run`, then `watch` whatever `run` returned, the worse status) and `assay-transfer.sh` (the snippet's copy, the report in the same environment, commit and push); tests that run the scripts against stubbed `uv`, `git` and `module`; two chunks. Six questions (§8). The session placeholders now hold this session's id. No other file was created or changed.
- **2026-11-23, round 1, revised** ("i wouldn't recommend modeling too much after build-test-kokkos.sh or upload-logs.sh. we should start clean with the snippets in the design doc"). §2–§8 rewritten from the two snippets alone: three files (`ops/env.sh`, `ops/assay-run.sh`, `ops/assay-transfer.sh`) and a README; `env.sh` is the snippets' values and exports as overridable defaults; each script is its snippet with the §1 fixes (`mkdir -p logs` before the transcript, `watch` whatever `run` returned and without `--only tests`, the report in the sourced environment). Dropped from the first draft: the hostname-selected env files, `local.sh`, `lib.sh` and its `assay()` wrapper, `--clean` (now Q3), `--dry-run`, `--no-pull`, `--no-watch`, `--name`, the destination and root guards, the `shellcheck` and other-machine questions, and the two-chunk plan (one chunk now). §1 (the findings) is unchanged.
- **2026-11-23, round 2: the questions answered** ("recommendations are okay. for 3 - i have been removing the root and starting fresh for testing. hence, it's just setting ROOT=/path/to/whatever"). §8 becomes the resolved list: `ops/`, a tracked `env.sh` with your paths, `watch` always. For Q3 there is no reset step: a fresh root is a new `ASSAY_ROOT`. Taken into the design from that: §1.2 now names the fresh root as the reason `logs/` is missing when `tee` opens its file; §3 moves `UV_CACHE_DIR` and `UV_PYTHON_INSTALL_DIR` out of the root (to `/gpfs/f7/epic/proj-shared/Benjamin.Koziol/sandbox/uv-cache` and `uv-python`), since inside a fresh root they would be downloaded again every run and removed with the root; §5's loop exports `ASSAY_ROOT` once so the run and its transfer name the same root. The design is ready for implementation (§7, one chunk).
- **2026-11-23, implemented** ("Yes, implement it"). As §2–§5 describe: `ops/env.sh`, `ops/assay-run.sh`, `ops/assay-transfer.sh` (both executable) and `ops/README.md`. Where the implementation differs from the text: (1) the transcript is `_main "$@" 2>&1 | tee -a "$transcript"` with the exit status from `PIPESTATUS`, not `exec > >(tee ...)`, so the transcript is complete when the script exits (bash 3.2, the macOS one the tests run under, cannot wait on a process substitution); `module reset` and the `LD_LIBRARY_PATH` / `PYTHONPATH` echo are inside it, after `mkdir -p logs`, so the transcript records them. (2) `watch` is also skipped when no selected stage is `test`, `harness` or `resume`: a build-only run would otherwise end with `watch`'s "nothing to watch" and exit 1. (3) `module` comes from `$MODULESHOME/init/bash` when the shell has not defined it (a script does not inherit the login shell's function), and its absence is a line, not an error. (4) The transfer's CMake caches are copied with `mkdir -p` + `cp` (macOS `cp` has no `--parents`), and the commit message is the directory name (`assay-20261123-1316`) rather than `assay <date>`. The scripts avoid bash-4 features (no `mapfile`, no empty arrays under `set -u`). Test-first: `src/tests/ufs_chem_assay/test_ops_scripts.py`, 8 tests with `uv`, `git` and `module` stubs on `PATH` and a scratch `HOME` (env.sh prepends `$HOME/.local/bin`, which must not hold a real `uv`): the transcript in a fresh root and its header and last line, the call order (`module reset`, `git pull`, `run` with each stage and the script's arguments, `watch`), exported values winning, `watch` after a failed `run` and the worse status, no `watch` without an effective config or for a build-only run; the transferred tree, `report.txt` when `report` exits 1, the `git` calls, the default name. The real scripts were not run here (`assay-run.sh` pulls the checkout). Docs: `ops/README.md`, README (the RDHPC section), `docs/kokkos-runbook.md` §8 (the scripts, and the by-hand commands without `--only tests`), `design/design.md` (the layout).
