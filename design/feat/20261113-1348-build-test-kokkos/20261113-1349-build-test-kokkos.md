# Feature: port `build-test-kokkos` into the harness — a `test` stage for the applications' own suites (issue #9), formalized compilation (login vs. compute node, named toolchains, harness-owned modulefiles), and `catchem` / `kokkos-smoke` as applications (issue #16)

Refined 2026-11-13 from the requirements in Appendix A, the scripts under `design/feat/20261113-1348-build-test-kokkos/test-kokkos/` (`build-test-kokkos.sh`, `build-test-kokkos-suite.sh`, `modulefiles/*.lua`, `smoke/`, `misc.md`; `upload-logs.sh` is out of scope), issues [#16](https://github.com/benkozi/ufs-chem-assay/issues/16) and [#9](https://github.com/benkozi/ufs-chem-assay/issues/9), and an audit of the harness at `8e90751` (`src/platforms.py`, `src/cli/stages.py`, `src/cli/run_config/`, `src/cli/commands/run.py`, `src/applications/base.py`, `src/applications/cece/cli.py`, `src/runner.py`, `src/jobs.py`, `src/benchmarks.py`, `src/cli/sessions.py`, `src/cli/run_report.py`, `src/templates/driver-job.sbatch.j2`, `config/ursa.yaml`, `docs/ursa-runbook.md`).

**This refinement is deliberately iterative**: round 1 (the audit and a design with twelve open questions), round 2 (the answers folded in; §7 is the resolved list) and rounds 3–6 (seven follow-on questions, all resolved; §8). Appendix B records every answer verbatim. No design question is open; the unknowns are the machine facts of §6. Every decision below names the alternative it beat. Nothing here creates files; the `test-kokkos/` directory stays as the reference until implementation moves what it keeps.

# session

- **Session ID**: `5b8828d0-1bd9-4b6d-9259-4e5762769b1e`
- **Resume Command**: `agy --resume 5b8828d0-1bd9-4b6d-9259-4e5762769b1e`

---

## Goal

Retire the bespoke `build-test-kokkos.sh` / `build-test-kokkos-suite.sh` pair by making every one of their concerns a harness concern, so that one run config per platform (`config/ursa-kokkos.yaml`, then `config/gaea-c7.yaml`, `config/gaea-c6.yaml`) drives, from a login node, `ufs-chem-assay run --config-file=config/ursa-kokkos.yaml --application all --toolchain all`: clone CECE and CATChem at the configured refs (existing `source` stage), load a **harness-owned UFS modulefile** against a **custom spack-stack environment** (new module plumbing), configure and build each application on the **login node or on a compute node** (new build placement; a compute-node build finishes with the test stack on the login node), gate the configure log on **"the environment's Kokkos / ArborX / ESMF was consumed"** (generalized gates), **submit each application's own ctest suite as one Slurm job** that `watch` waits for and `report` summarizes with one **benchmark row per test run** (new `test` stage — issue #9, for CECE *and* CATChem), compile and run a **kokkos / kokkos-kernels smoke executable** (new `kokkos-smoke` application), and then, for CECE, run the existing driver sessions (`data` + `harness` stages) against that very build — repeatable per **toolchain** (`oneapi`, `gnu`; the name is a path segment, so toolchains never share a build) and per platform (Ursa, Gaea C7, Gaea C6 — all three in scope; the user runs on the machines and brings the logs back, §6).

Not in scope: `upload-logs.sh` (the harness's `upload-output` is the eventual home), a CATChem driver sweep (CATChem is a full application whose `stages` stop at `test`; §4), CUDA / GPU variants of the environment, and CECE's repo-owned `scripts/run-tests-slurm.sbatch` (the harness renders its own test job for every application; per-application divergence has a home in the adapter's test lines, §1).

---

## Pre-design audit

### 1. What the scripts do, in harness vocabulary

| Script concern | Where it lives in the script | Harness equivalent today | Gap |
|---|---|---|---|
| Platform block (root dir, env root, modulefile, `module purge`/`reset`, `which` probes, srun launchers, account/cluster/partition) | `ursa_*` / `gaeac6_*` variables at the top of `build-test-kokkos.sh` | `platform:` + `slurm:` section of a run config template (`config/ursa.yaml`); `Platform`/`Runtime` in `src/platforms.py` | no Gaea platforms; no "how to reset modules" fact per platform; no module path other than `<clone>/modulefiles`; no env export before `module load` |
| Compiler suite (`--compiler oneapi\|gcc`: env root + modulefile + tool probes + test env + MPI launcher + preflags switch together) | `<platform>_<compiler>_*` variables; `build-test-kokkos-suite.sh` loops over them | nothing — a run config names exactly one modulefile per application | the **toolchain** concept (§3.3) |
| Clone / update / submodule guard / log the commit | `prepare_source`, `update_source` | `cli/stages.py:source_lines` — the same semantics line for line (clone `--recurse-submodules --branch`, `--update-source` = fetch + checkout + `pull --ff-only` + submodule update, guard path, `git log -1 --oneline`) | none: the script's `prepare_source` *is* `source_lines` in bash |
| Module load + toolchain probe + cmake configure teed to `configure.log` + grep gates | `load_modules`, `print_toolchain`, `do_cece` / `do_catchem` | `applications/cece/cli.py:_build` (`module_block`, `which cmake ${CC:-} …`, `cmake … \| tee configure.log`, `grep -q "Found MPI"`, `grep -qi netcdf`) | gates are hard-coded two; `which` list is not configurable; no `-fp-model=precise` per compiler; no offline flag swap |
| Compute-node compile (`--srun-build`: configure + build behind `srun …`, MUSICA / `CECE_BUILD_TESTING` off because FetchContent needs network) | `run_build`, `*_build_launcher`, the flag swaps in `do_*` | nothing: every stage "runs where the CLI runs" (the login node) | **build placement** (§2) |
| Run the ctest suite on a compute node (default: ctest inside a 1-task srun step with fork-bootstrap env; `--direct-launch`: one `sbatch --wait` job, ctest on the node, MPI tests as `srun -n <np>` steps or `mpiexec` forks) | `run_tests`, `run_ctest`, `*_test_launcher`, `*_test_submit_flags`, `*_direct_launch_launcher`, `*_mpiexec_preflags`, `CATCHEM_SBATCH_TOPOLOGY`, CECE's `scripts/run-tests-slurm.sbatch` | nothing: slot `04` of the script numbering is reserved for it (`cli/commands/run.py:_INDEX`, "issue #9"); `test_stages.py::test_cece_tests_is_not_a_stage_of_this_feature` pins the stage list without it; `runner.render_job_script` + `templates/driver-job.sbatch.j2` already render a Slurm job around one command; `jobs.py` + `cli/sessions.py` already submit, ledger, poll and judge jobs for `watch` / `report` | the **`test` stage** (§1) |
| Smoke test of kokkos + kokkos-kernels (`find_package` with no fallback, `parallel_for` + `axpby`) | `smoke/CMakeLists.txt`, `smoke/smoke.cpp`, `do_smoke` | nothing | a harness-owned **`kokkos-smoke` application** (§4) |
| Per-run logs (`<root>/logs/<component>-<stamp>.log`, `configure.log`) | `run_component`, `tee` | `cli/shell.py:run_bash` tees every stage to `<root_dir>/logs/<script>-<stamp>.log`; `configure.log` under the build dir | none |
| Dry run that works on macOS | `--dry-run` / `run()` | `run --dry-run` renders every script and executes nothing; `test_stages.py` golden lines | none |
| Suite across apps × compilers × phases with a summary table | `build-test-kokkos-suite.sh` | `run --application all` runs stages stage-major; `report` summarizes pytest sessions only | test outcomes reported and benchmarked per run (§1.4) |
| Pinning the git URL / branch per app and platform | `CATCHEM_GIT_URL`, `CECE_BRANCH`, `<platform>_<app>_git_url` | `applications.<name>.git_url` / `ref` | none |
| Log collection into a git repo | `upload-logs.sh` | `upload-output` (S3) | excluded by the requirements |

**Conclusion of the audit**: roughly two thirds of `build-test-kokkos.sh` (source handling, module block, configure + gates, logging, dry run, platform block) already exists in the harness as `source_lines`, `module_block`, `_build`, `run_bash`, `--dry-run` and the run config; the Slurm submit / poll / judge machinery the test stage needs exists too (`jobs.py`, `cli/sessions.py`). What the harness lacks is exactly four things: a `test` stage, build placement, named toolchains with harness-owned modulefiles, and two applications without a driver sweep. Everything below is those four, kept as small as the scripts' own behavior allows.

### 2. Discrepancies inside the reference scripts (settled in round 2)

1. `test-kokkos/README.md` says CECE "is intentionally **not** covered" and that `ENABLE_ESMF` "stays OFF"; `build-test-kokkos.sh` builds CECE with `CECE_CMAKE_FLAGS=(-DENABLE_ESMF=ON)` and gates `CECE dependency ESMF: system`. **Settled**: the script is the truth; CECE's unit tests *require* `-DENABLE_ESMF=ON` and `-DCMAKE_BUILD_TYPE=Debug`, CATChem's require `Debug` (§4, the validation rule); the README note is dropped.
2. `README.md` says `--srun-build` is "off by default" and that build runs "require the root dir to not exist"; the script defaults `SRUN_BUILD="yes"` and `REUSE_BUILD="yes"` (the fresh-root check only fires under `--no-reuse-build`). The suite runner passes both flags explicitly, so the suite's behavior is unambiguous: phase 1 = compute-node compile probe, phase 2 = login-node rebuild of the tests into the same build dir, then direct-launch ctest. **Settled**: both phases are the build stage's (§2); the test stage only runs tests; the fresh-root rule is dropped (the harness has `clean_root` for the output root and never deletes a checkout).
3. The Ursa oneapi `direct_launch_launcher` is `mpiexec` (Intel MPI 2021.17 does not bootstrap through Slurm's PMI without `I_MPI_PMI_LIBRARY` + `--mpi=pmi2`; `mpiexec` forking inside the allocation is "the configuration that passed 577/577 on Ursa oneapi, 2026-09-25"), while gnu / cray-mpich use `srun`. This is a per-toolchain fact and is modeled as one (§3.3).
4. The modulefiles fall back to hard-coded env roots under `/scratch4/NCEPDEV/nems/Richard.Grubin/…` and `/gpfs/f6/epic/scratch/Richard.Grubin/…` when `TEST_KOKKOS_ENV_ROOT` is unset. Harness-owned files must not carry a person's scratch path (`test_user_docs.py::test_nothing_user_specific` is the existing rule for the user-facing tree). **Settled**: the fallbacks go; the env root variable becomes *optional* and the run config names the environment's module directory as a full path (§3.1).
5. `misc.md` is a scratch list of shell one-liners (`rm -rf <root> && git pull && bash build-test-kokkos-suite.sh cece --upload-logs`); it documents the workflow the harness replaces (`run --application all`, `watch`, `report`) and is not carried.

### 3. Harness facts the design leans on

- `Stage` (`applications/base.py`) is the ordered enum `source, build, data, harness, resume`; `cli/commands/run.py:_INDEX` maps them to script slots `01, 02, 03, 05, 06` — **slot `04` is held "for the application's own tests, issue #9"**. Stages render per application (source/build/data) or per session (harness/resume), stage-major. `selected_stages` runs every stage but `resume` by default.
- `ApplicationRunSection` (`git_url`, `ref`, `clone_dir`, `update_source`, `modulefile`, `docker_image`, `driver_path`) is subclassed per adapter (`CeceRunSection` adds `cmake_args`, `build_jobs`, `targets`); `RunConfig.applications` dispatches each `applications:` key to its adapter's model through the registry resolver. `RunConfigView` (what an adapter may read) exposes `runtime` and `checkout_dir(app)` only.
- `module_block(checkout, section)` renders `source $MODULESHOME/init/bash; module purge; module use <checkout>/modulefiles; module load <modulefile>; module list` — the build stage's and `driver-job.sbatch.j2`'s module logic (the template repeats the same four lines with `{{ root_dir }}/modulefiles`).
- `runner.render_job_script` renders one Slurm job (`#SBATCH` directives from `sbatch_directives(settings.sbatch_args)`, `--time=<minutes>`, module block, exports, `srun <srun_args> <command>`, `tic`/`toc`, `report-mem`) with `StrictUndefined`.
- `jobs.py`: `submit_job` (`sbatch --parsable`), `queue_states` (one `squeue` for many ids; "every id gone" is not an error), `wait_for_job` (deadline = submitted + `timeout_s` + `queue_wait_s`, `scancel` past it), `slurm_accounting` (`sacct`), the `JobLedger` (`<output root>/jobs.yaml`: `run_id`, `application`, `submitted_by`, `jobs: [SubmittedJob]`, `evaluated_at`, `exit_status`) written whole and atomically. `cli/sessions.py`: `WatchedSession`, `tick` (one squeue over every unevaluated ledger), `evaluate_session` (runs the resume script); `cli/commands/watch.py` loops; `cli/commands/cancel.py` scancels one ledger; `cli/run_report.py` classifies a session root from files alone into `SessionSummary` + `Issue`s with a closed `IssueKind` taxonomy. `sbatch --wait` left the driver path on 2026-11-04.
- `benchmarks.py`: `BenchmarkRow` is one *executed combination* (suite, combo, combo_id, config_sha256, nc_files, baseline links …) — a combo-shaped schema with a committed per-configuration CSV that `record-benchmarks` appends to and the user commits; `SlurmAccounting` and `parse_tic_toc` are generic.
- `Application` is one ABC with the whole sweep surface abstract (`dimensions`, `build_config`, `effective_parameters`, `expected_output_*`, `selector_matches`, `config_model`, `suite_model`, …). `MonetioApplication` shows the full shape an adapter fills.
- `Platform` is `local | ursa`; `ACCOUNTING` maps Ursa to `sacct`; `_HOSTNAME_PATTERNS` knows `ufe\d+`. The docstring already anticipates "Gaea C6/C7 join once their sacct is confirmed".
- `SlurmSection` carries `account`, `qos`, `partition`, `extra_args`, `queue_wait_s`, `poll_s`, …; `sessions:` overlay `harness:`, `slurm:` and `baselines:` only, one level deep; `runtime` may be per session (2026-11-12).
- The user-facing tree (`config/*.yaml`, `docs/*.md`) is checked by `test_user_docs.py`: no person's paths, no design references, the word "example" banned, `config/` holds exactly `local.yaml` and `ursa.yaml` today (`test_templates_live_in_config` lists them — new templates extend that list).

---

## Design

### 1. The `test` stage (issue #9)

`Stage.TEST = "test"` joins the enum between `build` and `data`, in slot `04` (`_INDEX[Stage.TEST] = 4`; the held slot is finally used). It renders once per application to `<root_dir>/scripts/04-test-<application>[-<toolchain>].sh`, runs where the CLI runs, and its body is the adapter's `stage_lines(Stage.TEST, …)`. `selected_stages` includes it by default (a plain `run` is source → build → test → data → harness); `--stage test` runs it alone against an existing build.

**The test stage only runs tests** (round 2, Q3): no cmake, no `--build`, no re-configure. Its body is a guard that the test stack exists (`[ -f <build>/CTestTestfile.cmake ] || { echo "no tests built in <build>: the build stage builds them when tests.enabled is true" >&2; exit 1; }` — the harness analogue of the script's `die "no CECE build dir"`), then the launch below. Building the tests is the build stage's job (§2).

#### 1.1 The `tests:` section

Every application section gains a `tests:` sub-section (`TestsSection(StrictModel)` in `applications/base.py`, a field `tests: TestsSection` on `ApplicationRunSection`):

```yaml
applications:
  cece:
    …
    tests:
      enabled: false         # default false: existing templates (Release builds) are unaffected; the kokkos templates set true
      filter:                # ctest -R regex; null = the full suite
      parallel:              # ctest -j N; null = serial (safe for CECE: every test declares PROCESSORS)
      ntasks: 4              # the test job's sbatch -n: the suite's largest MPI rank count
      cpus: 8                # sbatch -c
      minutes: 5             # the test job's --time (its timeout_s in the ledger)
      env:                   # exported in the test job before ctest (the toolchain's tests_env is merged in, §3.3)
        I_MPI_HYDRA_BOOTSTRAP: fork
```

`enabled: false` renders a one-line note (`tests disabled for <application>`) and submits nothing. The default flipped from round 1's `true` because of Q7: enabling CECE's tests now *requires* a Debug + ESMF build (§4), which `config/ursa.yaml` is not.

#### 1.2 Where the tests run, per runtime

- **slurm** (Ursa, Gaea): the job script is rendered beside the stage script (`<root_dir>/scripts/04-test-<application>[-<toolchain>].sbatch`; the tests root = `<root_dir>/[<toolchain>/]tests/<application>/` holds the `.out`, the junit XML, the ledger and the benchmark rows) through `runner.render_job_script` from a second template, `templates/tests-job.sbatch.j2`, that differs from the driver template in one line: the command runs **directly on the node, not behind `srun`**, because ctest is the launcher — each MPI test is its own `srun -n <np>` job step (toolchain `mpi_launcher: srun`, baked in at configure time, §3.3) or an `mpiexec` fork inside the allocation (`mpi_launcher: mpiexec`, Intel MPI on Ursa). Directives: `-A/-q/-p` from `slurm:`, `-N 1 -n <tests.ntasks> -c <tests.cpus>`, `--time=<tests.minutes>`, `slurm.extra_args` (Gaea's `--clusters=c6`), `--output=<tests root>/tests-%j.out`; the module block from §3.1 (the job needs the toolchain; `sbatch --export=ALL` is not relied on); `tests.env` exported; `tic`/`toc`/`report-mem` as in the driver template. The command: `ctest --test-dir <build> --output-on-failure --output-junit <tests root>/tests-${SLURM_JOB_ID}.xml [-R <filter>] [-j <parallel>]` (CECE, CATChem) or the `smoke` executable (§4). The stage then **submits through `ufs-chem-assay submit-tests`, which writes the ledger** (§1.3), and returns at once — `run` never blocks on Slurm (round 2, Q2: "blocking is never good with HPC"); the data and harness stages proceed while the tests queue (they do not read the test results).
- **native** (inside an allocation, or a laptop with a native build): `[<launcher>] ctest …` with `tests.env` exported, synchronous in the stage script; the junit XML is written to the same tests root. The `ursa_test_launcher` 1-task-srun-step mode of the script is this runtime with `harness.launcher: 'srun --ntasks=1 --cpus-per-task=8 …'`, and is **not** the recommended Ursa path (the job above is).
- **docker** (laptop): `docker run --rm -v <clone>:/work -w /work <image> ctest --test-dir build --output-on-failure --output-junit … ` through the existing `runner.docker_prefix`, so the local test path reuses the image CECE's own build script produced — no second container tool (round 2, Q9: decided; `scripts/build-and-test-container.py` without `--no-test` is not used).

#### 1.3 The test ledger: `watch` waits, `report` summarizes, `cancel` cancels

The driver jobs' machinery is reused rather than paralleled, with one new model because a test job is not a combination:

- `jobs.py` gains `TestJob` / `TestLedger` beside `SubmittedJob` / `JobLedger`: `TestLedger {application, toolchain: str | None, submitted_by, job: TestJob {job_id, script, out_path, timeout_s (= tests.minutes × 60), queue_wait_s (= slurm.queue_wait_s), submitted_at}, evaluated_at, exit_status, tests: TestCounts | None {total, passed, failed, skipped, ctest_s}}` written to `<tests root>/jobs.yaml` at submission.
- **The stage script submits through the harness** (round 6, §8 Q2: option two). `run` renders the job script at render time to `<root_dir>/scripts/04-test-<application>[-<toolchain>].sbatch`, beside the stage script, so `--dry-run` shows the whole job; the stage script's last line is `uv run --no-sync ufs-chem-assay submit-tests --tests-root <root> --script <that .sbatch> --application <name> [--toolchain <name>] --timeout-s <tests.minutes × 60> --queue-wait-s <slurm.queue_wait_s>` — the same way the data stage calls `ufs-chem-assay fetch`. `submit-tests` (a new subcommand, `cli/commands/submit_tests.py`) does, in one process: create the tests root (Slurm needs the `--output` directory to exist); **guard** — if the root's ledger names a job still in the queue (`queue_states`), refuse with exit 1 naming the job id and `ufs-chem-assay cancel --tests-root <root>`, unless `--resubmit`, which `scancel`s it first; if the ledger names a job that has left the queue and was never evaluated, evaluate it first (`tests.evaluate`, so its benchmark row is not lost) and log that it did; then `submit_job` (`sbatch --parsable`, logging the exact command) and the atomic ledger write — the code path the pytest session uses for driver jobs. A script run by hand therefore leaves the same ledger a `run` does, and `watch` / `report` / `cancel` see the job either way. `--dry-run` executes nothing, so nothing is submitted and no ledger is written.
- **Nothing a resubmission overwrites**: the job's files are named by job id — `tests-%j.out` and `tests-${SLURM_JOB_ID}.xml` — and `<tests root>/benchmarks.csv` grows by one row per evaluated run; the ledger is the latest job only.
- `cli/sessions.py` gains `WatchedTests` (application, toolchain, tests root) beside `WatchedSession`; `configured_tests(config, applications)` lists one per application with `tests.enabled` under the slurm runtime; `tick` includes their job ids in its single `squeue`; a test job that has left the queue is **evaluated in-process** (no resume script to run): `tests.evaluate(tests_root)` reads `sacct` (`slurm_accounting`), `tic`/`toc` from the `.out`, the junit XML into `TestCounts`, writes the benchmark row (§1.4), and rewrites the ledger with `evaluated_at` / `exit_status` (sacct's exit code; `1` when the junit shows failures and sacct says `COMPLETED 0`, which ctest does not do but a killed job might). `watch` reports `tests <application>[/<toolchain>]: 1 job: … ; evaluated (exit 0, 577/577 passed, 312 s)`; its exit status folds the test verdicts in beside the sessions'. `watch --once` reports the test jobs too.
- `report` gains one line per tests root (state from files: not submitted / waiting / evaluated; the counts; the `.out` and junit paths of the ledger's job) and two `IssueKind`s: `TESTS_FAILED` (with the failed test names from the junit, up to `--tail` — **failures only**, round 5: no slowest-tests list; durations stay in the junit and the per-run benchmark row) and `TESTS_JOB_FAILED` (Slurm state / exit, the `.out` tail). `cancel --tests-root <root>` reads a `TestLedger` as `--output-root` reads a `JobLedger`.
- Under **native / docker** the stage is synchronous and the script's last line is `ufs-chem-assay evaluate-tests --tests-root <root>` (the same `tests.evaluate`, minus `sacct`), so the benchmark row and `report`'s line exist in every runtime; the same command is the by-hand path on Slurm when `watch` is not used.

#### 1.4 Benchmarks: one row per test run

Round 2, Q10: the whole test run is the benchmarked unit; the junit XML stays under the tests root for drilling into individual tests when a regression shows. `BenchmarkRow` is combo-shaped (suite / combo / config_sha256 / nc_files / baseline links), so a test run gets its own model rather than a row of empty cells: `TestBenchmarkRow` in `benchmarks.py` — `recorded_at, application, toolchain, platform, runtime, modulefile, build_type, cmake_args (space-joined, the effective configure flags: ESMF and the rest are legible), application_commit, harness_version, harness_commit, slurm_job_id, slurm_state, exit_code, wall_s (tic/toc), queue_s, elapsed_s, max_rss_kb, tests_total, tests_passed, tests_failed, tests_skipped, ctest_s (the junit's summed test durations), tests_root` — written as `<tests root>/benchmarks.csv` by `tests.evaluate`, and appended by `record-benchmarks --tests-root <root>` to a committed `src/tests/config/<application>/tests-benchmarks.csv` — **one file per application** (round 5: "definitely per applications"), the analogue of the per-configuration file; the user commits. The `(application, toolchain, platform, build_type, application_commit)` tuple is what a regression is read against. Timing reference: Ursa oneapi, 2026-09-25, 577/577 CECE tests passed in the direct-launch configuration.

**The driver benchmarks record the build type too** (round 3, Q3): `BenchmarkRow` gains `build_type: str | None` after `application_commit` — `CMAKE_BUILD_TYPE` read from `<checkout>/build/CMakeCache.txt` (`CMAKE_BUILD_TYPE:STRING=<value>`) at session start, the way `commit_sha` reads the checkout; null when there is no cache (monetio, or a build directory elsewhere). The header check in `read_benchmarks_csv` is deliberate, so the change is a one-time migration of every committed `src/tests/config/<application>/<configuration>/benchmarks.csv`: the column inserted into the header and an empty cell into every existing row, in the same chunk, reviewed and committed by the user. From then on a Debug-build maccity row is never read beside a Release one by mistake.

**Will the tests be "a suite"?** (round 2, Q8) No: a *suite* is a pytest sweep of driver configurations (`<name>-suite.yaml`) a session enumerates, with one combination directory and one `BenchmarkRow` per combination. The application's tests are one Slurm job per (application, toolchain) with one `TestBenchmarkRow`; they have a ledger and a report line like a session, but no combinations, no baselines and no pytest. The `tests:` section of the run config is the nearest thing to their "suite file".

`test_stages.py::test_cece_tests_is_not_a_stage_of_this_feature` becomes `test_test_stage_is_slot_04_between_build_and_data` (the stage list and `_INDEX`); `README.md` and `config/ursa.yaml`'s "running it is issue #9" notes are replaced by the `tests:` keys.

### 2. Build placement: login node vs. compute node

Compilation is formalized as a per-application choice in the run config:

```yaml
applications:
  cece:
    …
    build_node: login        # login (default; network: FetchContent works) | compute (offline probe behind srun, then the login-node finish)
    build_minutes: 10        # compute only: the srun --time of each compile step
```

(`build_node: BuildNode` — a `StrEnum` `LOGIN | COMPUTE` in `applications/base.py`; `build_minutes: int` — on `ApplicationRunSection`, so every adapter's build stage can honor them.) `RunConfigView` gains `build_launcher(app) -> list[str]`: `[]` for `login`; for `compute`, `srun -A <account> -q <qos> -p <partition> <extra_args> --nodes=1 --ntasks=1 --cpus-per-task=<build_jobs> --time=<build_minutes>` from the `slurm:` section (a run config with `build_node: compute` and no `slurm:` section is a validation error, like `submit_only` without slurm). `cli/stages.py` renders the launcher prefix in front of the configure and build commands — two separate `srun` steps with separate allocations, as the script does, never one spanning both; source handling stays on the login node.

**Offline flags are the adapter's knowledge.** `Application.stage_lines(Stage.BUILD, config, section)` reads `config.build_node` (added to `RunConfigView` beside `runtime`) and adjusts its configure flags for the compute-node step: CECE adds `-DCECE_BUILD_TESTING=OFF` (GTest/RapidCheck are FetchContent'd at configure time), CATChem swaps `-DCATCHEM_BUILD_MUSICA=ON` for `OFF` (MUSICA fetches micm / tuv-x / MechanismConfiguration at configure), `kokkos-smoke` changes nothing (it fetches nothing). The build stage logs what it turned off and why, as the script's notes do.

**Two phases, one stage** (round 2, Q3 — the test stage must not compile). Under `build_node: compute` the build stage runs both phases of the suite script:

1. **Phase 1, the compute-node probe**: `srun … cmake <configure with the offline flags> | tee configure.log`, the gates, `srun … cmake --build`. "Does the env + toolchain compile this component offline, as `rt.sh` must."
2. **Phase 2, the login-node finish** — rendered only when `tests.enabled` is true: a login-node re-configure **into the same build directory** with the test stack back on (`-DCECE_BUILD_TESTING=ON`; MUSICA back `ON`), the gates again (the configure log is overwritten; the second log is the one that stays), `cmake --build` reusing the compute-built objects. With `tests.enabled: false` the stage ends after the probe, so `--stage build` on a tests-disabled section is the pure probe.

Under `build_node: login`, phase 2 *is* the build (one configure with the test stack as `cmake_args` say). Under native / docker, `build_node: compute` is a validation error (there is no compute node to go to).

The "where" is recorded: the build stage's first lines echo `build node: compute (srun …), then login (tests)` / `login`, and the effective `run-config.yaml` carries `build_node`.

### 3. Toolchains: harness-owned modulefiles, custom environments, and the compiler axis

#### 3.1 Module plumbing on the application section

Today `module_block` knows one module path, `<clone>/modulefiles`, and no exports before the load. Three keys generalize it without changing the default:

```yaml
applications:
  cece:
    modulefile: ufs_ursa.oneapi            # unchanged meaning: the name to `module load`
    modulepath:                            # dirs `module use`d first, in order; default [<clone>/modulefiles]
      - modulefiles                        # relative: under the harness checkout (the harness-owned UFS files)
      - /scratch4/NAGAPE/epic/role-epic/spack-stack/envs/ue-oneapi-2025.3.1/modules/Core   # the environment, as a full path
    module_env:                            # exported before `module load`; version pins and, optionally, the env root
      kokkos_ver: …                        # TODO(pins): §7 Q12
    toolchain_tools: [mpiicx, mpiicpx, mpiifx, cmake, ctest]   # `which` probes after the load (today: cmake $CC $CXX $FC)
```

`module_block` renders `source …/init/bash; module <init>; <exports>; module use <each path>; module load <modulefile>; module list` and the sbatch templates take the same block (the `{{ root_dir }}/modulefiles` lines in `driver-job.sbatch.j2` become a rendered `module_block` so the driver jobs load the same environment the build did — today they would not see `modulepath`/`module_env`). `module <init>` is **`purge` on Ursa and `reset` on Gaea** (purge strips Cray's `craype`/`PrgEnv` machinery): `platforms.py` gains `MODULE_INIT: dict[Platform, str]` (`URSA: "purge"`, `GAEA_C6: "reset"`, `GAEA_C7: "reset"`; absent = `purge`), a platform fact beside `ACCOUNTING`, read through `RunConfigView.module_init`.

**The modulefiles move into the harness** (round 2, Q6: top level): `modulefiles/ufs_common.lua`, `ufs_ursa.oneapi.lua`, `ufs_ursa.gnu.lua`, `ufs_gaeac6.oneapi.lua` from `test-kokkos/modulefiles/`, plus a new `ufs_gaeac7.oneapi.lua` (§6), into a top-level `modulefiles/` directory (user-facing like `config/` and `docs/`; `test_user_docs.py` extends `_user_facing_files()` to it, which forces the next point). Two edits on the way: the hard-coded fallback roots go, and **the env-root variable becomes optional** (round 2, Q6: "allowed to be empty; we'll use full paths in most cases"):

```lua
-- ufs_ursa.oneapi.lua, replacing the TEST_KOKKOS_ENV_ROOT block
local env_root = os.getenv("ASSAY_SPACK_ENV_ROOT")
if env_root and env_root ~= "" then
  prepend_path("MODULEPATH", pathJoin(env_root, "modules/Core"))
end
-- otherwise the caller has already `module use`d the environment's modules/Core (ufs-chem-assay: applications.<name>.modulepath)
```

So the recommended run config names the environment by its full `modules/Core` path in `modulepath` (as above), and `module_env` is for the `<name>_ver` pins; `ASSAY_SPACK_ENV_ROOT` in `module_env` remains the one-variable shorthand. A standalone `module load ufs_ursa.oneapi` with neither fails on the first `load("stack-intel-oneapi-compilers/…")` with Lmod's own "module unknown", which is the right message. The headers keep their "verbatim copy of ufs-weather-model `feature/ss2.x` plus N local changes" provenance; nothing else in the Lua changes (the `<name>_ver` overrides, the unversioned kokkos / kokkos-kernels loads, `try_load("arborx")` all stay — the pins are §7 Q12's TODO).

The harness checkout path for relative `modulepath` entries is `HARNESS_ROOT` (`identity.py`), the same anchor `cli/stages.py` already `cd`s to.

#### 3.2 Configure gates and compiler flags

`_build`'s two hard-coded greps generalize to a list the adapter seeds and the run config extends:

```yaml
applications:
  cece:
    configure_gates:                       # regexes the configure log must match (added to the adapter's own)
      - 'CECE dependency Kokkos: system'   # the env's Kokkos, not FetchContent
      - 'AXIS: ArborX .* found via find_package'   # this env carries arborx; the fallback is a failure here
```

Adapter-owned gates stay in code because they are the application's: CECE always requires `Found MPI` and `netcdf` (today's), and adds `CECE dependency ESMF: system` whenever `-DENABLE_ESMF=ON` is among its `cmake_args`; CATChem requires `Using system Kokkos`; `kokkos-smoke` requires `Found Kokkos` and `Found KokkosKernels`. Run-config gates are the environment's claims ("this env has arborx, consume it"), which is why they are configuration and not code. Each gate renders as today's `grep -Eq <gate> <log> || { echo "configure gate failed: <gate>" >&2; exit 1; }` followed by `grep -E <gate> <log>` so the log shows the matched line (the script prints it too).

Compiler-specific flags (`-DCMAKE_CXX_FLAGS=-fp-model=precise` for IntelLLVM, the `CeceAxisDecode` precision fix) are `cmake_args` of the toolchain (next section), appended after the application's.

#### 3.3 The compiler axis: `toolchains:` and `--toolchain` (round 2, Q1: model A)

The suite script's one switch — compiler suite = env root + modulefile + tool probes + test env + MPI launcher + preflags, "letting them drift apart is exactly the Lmod 'module(s) are unknown' failure" — gets a home: a `toolchains:` map and a `--toolchain NAME` selector. A toolchain is an overlay on the *module and compiler* keys of every application section, applied before validation like a session overlay (one level deep; `--toolchain` takes one or more names or `all`, exactly as `--application` does, and is required when the map has more than one entry):

```yaml
toolchains:                 # named compiler suites; --toolchain NAME picks one (required when several)
  oneapi:
    modulefile: ufs_ursa.oneapi
    modulepath: [modulefiles, /scratch4/NAGAPE/epic/role-epic/spack-stack/envs/ue-oneapi-2025.3.1/modules/Core]
    module_env: {}          # TODO(pins): kokkos_ver, kokkos_kernels_ver once `module avail` says (§7 Q12)
    toolchain_tools: [mpiicx, mpiicpx, mpiifx, cmake, ctest]
    cmake_args: [-DCMAKE_CXX_FLAGS=-fp-model=precise]   # appended to every application's configure
    mpi_launcher: mpiexec   # Intel MPI 2021.17 does not bootstrap through Slurm PMI: mpiexec forks inside the allocation
    tests_env: {I_MPI_HYDRA_BOOTSTRAP: fork}            # merged into every application's tests.env
    configure_gates: ['AXIS: ArborX .* found via find_package']   # both Ursa envs carry arborx
  gnu:
    modulefile: ufs_ursa.gnu
    modulepath: [modulefiles, /scratch4/NAGAPE/epic/role-epic/spack-stack/envs/ue-gcc-12.4.0/modules/Core]
    module_env: {}
    toolchain_tools: [mpicc, mpic++, mpifort, cmake, ctest]
    mpi_launcher: srun      # OpenMPI/PMIx: every MPI test is its own `srun -n <np>` step
    tests_env: {OMPI_MCA_plm: isolated}
    configure_gates: ['AXIS: ArborX .* found via find_package']
```

**Precedence, pinned** (the question chunk 5 left open in round 1): with a `toolchains:` map present, an application section that sets `modulefile`, `modulepath`, `module_env` or `toolchain_tools` itself is a **validation error** ("under toolchains:, `modulefile` belongs to the toolchain") — the point of the toolchain is that these never drift per application. The list and map keys compose: `cmake_args` = application's then toolchain's; `configure_gates` = the union; `tests.env` = application's updated by the toolchain's `tests_env`. Without a `toolchains:` map the application section carries the §3.1 keys itself and nothing changes for `config/ursa.yaml` / `config/local.yaml`.

`mpi_launcher: srun` makes every adapter's configure add `-DMPIEXEC_EXECUTABLE=$(command -v srun) -DMPIEXEC_NUMPROC_FLAG=-n` (plus `-DMPIEXEC_PREFLAGS=<mpiexec_preflags>` when set — the `--mpi=pmi2` seed for the Ursa PMI experiment); `mpiexec` leaves FindMPI's default. **The toolchain name becomes part of every path the build touches**, so two toolchains never share a checkout, a build directory, a tests root or an output root: `checkout_dir` = `<root_dir>/<toolchain>/<checkout_dirname>` (the suite script's `<base>/<app>-<comp>/` layout, one level up), tests root = `<root_dir>/<toolchain>/tests/<application>/`, scripts render as `<NN>-<stage>-<application>-<toolchain>[-<session>].sh`, and `session_output_root` gains `/<toolchain>` the way it gains `/<application>` for a comprehensive run. The effective `run-config.yaml` records the selected toolchains, and `watch` / `report` / `cancel` resolve roots with them (`--toolchain` on `watch` and `report`, defaulting to the effective config's recorded selection). Session overlays are untouched: a toolchain is a *build* axis, a session a *pytest* axis, and the two compose (`--toolchain gnu --session simple-maccity` runs the maccity sweep against the gnu build).

**`--toolchain all`: every compiler in one go** (round 4: "we'll want to typically test gcc and intel all in one go"). `--toolchain` is `action="extend", nargs="+"` like `--application`: `--toolchain oneapi`, `--toolchain oneapi gnu`, or `--toolchain all` for every key of the map in file order. With several selected, `run` is **toolchain-major**: the whole selected stage list for the first toolchain, then for the next — the suite script's compiler loop, and the order in which a toolchain's test job is in the queue while the next toolchain compiles. Rules:

- **Everything renders first.** Each toolchain's overlay is validated and every script of every toolchain is written (`<NN>-<stage>-<application>-<toolchain>…sh`, all in the one `<root_dir>/scripts/`) before anything executes, so a bad toolchain fails the run before the first clone — the existing "render and write everything first" rule, one loop further out. `--dry-run` therefore shows all of them.
- **A failing toolchain does not stop the others.** Within a toolchain a failed stage ends that toolchain's sequence as today; `run` then continues with the next toolchain and exits with the first nonzero status once all are done, after logging one line per toolchain (`oneapi: failed at 02-build-catchem-oneapi.sh (exit 2)` / `gnu: ok`). Stage-major across toolchains was the alternative; it was rejected because it would let a gnu build failure keep the oneapi tests from ever being submitted.
- **One effective config.** `<root_dir>/scripts/run-config.yaml` stays a single file — the config as written, plus `selected_toolchains: [oneapi, gnu]` — and `watch` / `report` re-apply each overlay from it. Without `--toolchain` they cover every recorded toolchain (one `squeue` per tick over all of their ledgers; one report block per toolchain; exit status over all); `--toolchain NAME` narrows.
- **Shared-node cost.** Nothing is shared between toolchains but the harness checkout and the scripts / logs directories: each has its own clones under `<root_dir>/<toolchain>/`, so `all` clones CECE and CATChem once per toolchain, as the suite script did (`<base>/<app>-<comp>/`).

The toolchain names are free keys; the templates use `oneapi` and `gnu` to match the modulefile names (`ufs_ursa.oneapi`, `ufs_ursa.gnu`) — "intel" and "gcc" in conversation.

The alternative (one template per compiler, distinct `--root-dir`s) was rejected in round 2.

### 4. Two more applications: `catchem` and `kokkos-smoke` (round 2, Q5 / Q8: full applications)

Issue #16's components are three; CECE is already an adapter and the other two are not drivers the harness sweeps. Round 1 proposed splitting the ABC (`Buildable` / `Application`); round 2 decided **both register as full `Application`s** — one ABC, one registry, one `--application` vocabulary everywhere. What that costs and how the harness keeps it honest:

- `Application` gains `stages: ClassVar[frozenset[Stage]]` (default `frozenset(Stage)`; CATChem `{SOURCE, BUILD, TEST}`; kokkos-smoke `{BUILD, TEST}`). `run` renders a stage for an application only when `stage in app.stages` — `--application all` on the kokkos template clones, builds and tests `cece`, `catchem` and `kokkos-smoke`, and runs `data` / `harness` for `cece` alone; `configured_sessions` (watch / report) lists sessions only for applications with `HARNESS` in `stages`.
- The sweep surface stays abstract. CATChem and kokkos-smoke implement it with **one shared refusal**: `applications/base.py` gets `class NoDriverSuite(ValueError)` and a module-level helper the two adapters' sweep methods call (`raise NoDriverSuite(self.name)` — "`catchem` has no driver suite: it takes part in the source, build and test stages only"), with `config_model = NoDriverConfig`, `suite_model = NoDriverSuiteConfig` (two empty `StrictModel`s in `base.py`; `from_yaml` raises the same error), `default_suite = ""`, `settings_model` a real settings class (root dir and modulefile are needed; `docker_image` / `driver_path` default `""`). `peek_application` / `load_suite` / pytest's `--application` surface that error before any enumeration; `stages` gates every CLI call site, so in normal operation the refusals are unreachable, and one test per adapter pins the message. This is the "placeholder models" option round 1 argued against; it was chosen because one kind of application is simpler to explain and to register than two, and the placeholders are centralized once in `base.py`, not written per adapter.

**`applications/catchem/`** (`application.py`, `cli.py`, `settings.py`): `checkout_dirname = "CATChem"`; `CatchemRunSection(ApplicationRunSection)` with `cmake_args` default `[-DCMAKE_BUILD_TYPE=Debug, -DCATCHEM_ENABLE_KOKKOS=ON, -DCATCHEM_BUILD_MUSICA=ON, -DCATCHEM_BUILD_NUOPC=ON]`, `build_jobs`, `targets: [all]`, `tests` default `ntasks: 1` (every CATChem test is single-rank; the 8 CPUs cover its OpenMP Kokkos tests); source stage = `source_lines(…, recurse_submodules=True, extra_checks=[guard on src/external/musica/CMakeLists.txt])`; build stage = the shared module block, probes, configure with the MUSICA swap for the compute-node probe and `ON` restored in the login-node finish (§2), gate `Using system Kokkos`, build; test stage = §1 with ctest. Refs for the kokkos work: `git@github.com:benkozi/CATChem.git` @ `feature/spack-stack-wm-tweaks` (the script's current pins; the template carries them as `git_url` / `ref`, as `config/ursa.yaml` carries CECE's).

**`applications/kokkos_smoke/`** (`application.py`, `cli.py`, `settings.py`, `cmake/CMakeLists.txt`, `cmake/smoke.cpp` — the two files from `test-kokkos/smoke/`, unchanged; `hatch` already ships `src/` whole so they are in the wheel): no source stage (its "checkout" is the harness itself and `checkout_dir` resolves to `<root_dir>/[<toolchain>/]kokkos-smoke`, which holds only `build/`); build stage = module block, probes, `cmake -S <harness>/src/applications/kokkos_smoke/cmake -B <…>/kokkos-smoke/build`, gates `Found Kokkos`, `Found KokkosKernels`, build; test stage = the `smoke` executable as the job command (`tests.ntasks: 1`), success = exit 0 and `SMOKE TEST PASSED` in the job's `.out` (a gate on the output, the test stage's one application-specific line; `tests.evaluate` reads it in place of a junit file: `total = passed = 1`). It is the only direct validation of kokkos-kernels and of "the env compiles offline" with no component caveats; the templates enable it by default.

**CECE under the kokkos templates**: `ref: feat/prefer-find-package` on `git@github.com:benkozi/CECE.git`, `cmake_args: [-DCMAKE_BUILD_TYPE=Debug, -DENABLE_ESMF=ON]`, `targets: [all]`, `tests: {enabled: true, ntasks: 4, cpus: 8, minutes: 5}`; the toolchain supplies `-fp-model=precise`, the launcher and the fork-bootstrap env.

**The Debug / ESMF rule** (round 2, Q7 and the two bullets above the answers): CECE's unit tests need `-DENABLE_ESMF=ON` and `-DCMAKE_BUILD_TYPE=Debug`; CATChem's need `Debug`. Other configurations (the production `config/ursa.yaml`, Release, no ESMF) may stay as they are **as long as they do not enable the tests**. The rule is a model validator on each adapter's run section: `tests.enabled` ⇒ the required flags are present in the *effective* `cmake_args` (application's plus toolchain's), else a validation error naming the missing flag ("cece: tests.enabled requires -DCMAKE_BUILD_TYPE=Debug and -DENABLE_ESMF=ON in cmake_args"). Consequence worth knowing: the kokkos templates' CECE driver sessions run against a Debug build, so their driver `BenchmarkRow`s are slower than `config/ursa.yaml`'s and not comparable to them — which is why `BenchmarkRow` records the build type (§1.4) and why the templates' sessions are opt-in (§5).

### 5. The run config templates: `config/ursa-kokkos.yaml` first

A third template beside `local.yaml` and `ursa.yaml` (`test_templates_live_in_config` lists it), dry-runnable on macOS, with the §3 keys in use and the production `config/ursa.yaml` untouched:

```yaml
# Build and test CECE, CATChem and the kokkos smoke project against a custom spack-stack
# environment (JCSDA/spack-stack#2081) with the real UFS modulefiles (modulefiles/). The
# standard run is the three build-and-test stages for every toolchain, one after the other:
#   ufs-chem-assay run --config-file=config/ursa-kokkos.yaml --application all --toolchain all --stage source --stage build --stage test [--dry-run]
#   ufs-chem-assay watch                           # every toolchain's test jobs (and any submit-only sessions)
#   ufs-chem-assay report                          # one block per toolchain; --toolchain oneapi narrows either
# The CECE driver sessions run against the Debug build only when asked for (opt-in):
#   ufs-chem-assay run --config-file=config/ursa-kokkos.yaml --application cece --toolchain oneapi --stage data --stage harness --session simple-maccity
platform: ursa
toolchains: {oneapi: …, gnu: …}          # §3.3 verbatim
applications:
  cece:
    git_url: git@github.com:benkozi/CECE.git
    ref: feat/prefer-find-package
    update_source: false
    cmake_args: [-DCMAKE_BUILD_TYPE=Debug, -DENABLE_ESMF=ON]   # required by tests.enabled (§4)
    build_jobs: 8
    targets: [all]
    build_node: compute                  # phase 1: the offline compile probe; phase 2: the login-node finish with the tests
    build_minutes: 10
    configure_gates: ['CECE dependency Kokkos: system']
    tests: {enabled: true, ntasks: 4, cpus: 8, minutes: 5}
  catchem:
    git_url: git@github.com:benkozi/CATChem.git
    ref: feature/spack-stack-wm-tweaks
    build_node: compute
    configure_gates: ['Using system Kokkos']
    tests: {enabled: true, ntasks: 1, cpus: 8, minutes: 5}     # cmake_args default carries Debug
  kokkos-smoke:
    build_node: compute
    tests: {enabled: true, ntasks: 1, cpus: 8, minutes: 5}
data: {warm_cartopy: true, input_search_path: []}
baselines: {enabled: false, store: s3://ufs-chem/baselines}
outputs: {store: s3://ufs-chem/assay-output}
harness: {suite_config: simple-maccity-suite.yaml, submit_only: true, dask_nworkers: 2, …}   # as config/ursa.yaml
slurm: {account: epic, qos: debug, partition: u1-compute, ntasks: 1, cpus: 8, …}
sessions:                                # opt-in (round 3): named here so --session can pick one; a plain run names the three stages above
  simple-maccity: {}
```

**Sessions are opt-in on the kokkos templates** (round 3, Q5): the template keeps its `sessions:` map so `--session` has something to name, and the documented commands pass `--stage`; no new config key. `run` without `--stage` still means every stage, as everywhere else — the opt-in is the runbook's command, not a change to `selected_stages`.

`config/gaea-c7.yaml` and `config/gaea-c6.yaml` follow the same shape with `platform: gaea-c7` / `gaea-c6`, `slurm: {partition: batch, extra_args: --clusters=c7 | --clusters=c6, account: <project>}`, one `oneapi` toolchain each (`ufs_gaeac7.oneapi` / `ufs_gaeac6.oneapi`, the machine's env `modules/Core` path, `toolchain_tools: [cc, CC, ftn, cmake, ctest]`, `mpi_launcher: srun`; C7's official spack-stack 2.2 carries the whole dependency set, so its gates match Ursa's; C6's custom env "carries no arborx today", so its toolchain omits the arborx gate). Their machine facts are §6's TODOs.

### 6. Platforms: Ursa, Gaea C7, Gaea C6 — all in scope (round 2, Q11)

`Platform.GAEA_C6 = "gaea-c6"` and `Platform.GAEA_C7 = "gaea-c7"` join the enum, with `MODULE_INIT = reset` for both. The user runs on each machine and brings the logs back; the facts below are recorded in Appendix B as they arrive and turned into code / template values. **C7 first** (it already carries a spack-stack 2.2 build), then Ursa, then C6.

- [ ] **Gaea C7 — TODO(gaea-c7)**: confirm the login-node hostname against `_HOSTNAME_PATTERNS` (2026-11-16: `gaea6\d+` and `gaea7\d+`, each with an optional domain, are in — C6's `gaea61`–`gaea68` is confirmed by the user, C7's `gaea7N` is by analogy; run `hostname` and `hostname -f` on the first C7 login and correct the pattern if they print something else — the scripts never detected the platform, `--platform` was explicit); whether `sacct` answers the `ACCOUNTING` columns; the **official spack-stack 2.2** install's root and its `modules/Core` path (round 3: it is the official install and is expected to carry every dependency — `kokkos`, `kokkos-kernels`, `arborx` — so the C7 toolchain keeps the arborx gate and the smoke test, same as Ursa; the `module avail` on first login confirms it and supplies the pins); the compiler / MPI stack module names and versions for `ufs_gaeac7.oneapi.lua` (no C7 modulefile exists in the reference set or, as far as known, upstream `feature/ss2.x` — it is written from the C6 file with the C7 facts); whether `--clusters=c7` belongs in `slurm.extra_args` (sbatch) *and* the build launcher (srun) — the script passes it to both on C6; the `account` / `qos` / `partition` triple.
- [ ] **Ursa — TODO(ursa)**: queue waits for `tests.minutes: 5` on `debug`; whether `report-mem` prints inside the test job; whether `-DMPIEXEC_PREFLAGS=--mpi=pmi2` ever makes `srun` work for Intel MPI (then `mpi_launcher: srun` for oneapi too); the kokkos / kokkos-kernels versions `module avail` shows in each env (§7 Q12).
- [ ] **Gaea C6 — TODO(gaea-c6)**: the same list as C7 against `/gpfs/f6/epic/scratch/…/ue-oneapi-2025.2.1` or its role-account successor (the reference path is a person's scratch; the template needs a shareable one).

Until a pattern is known, `platform: gaea-c7` in the template is the explicit value the design already prefers ("detection is a convenience, never load-bearing").

### 7. Round 1's questions, resolved (round 2)

| # | Question | Decision |
|---|---|---|
| 1 | Toolchain model | **A**: `toolchains:` + `--toolchain`, paths gain `/<toolchain>` (§3.3) |
| 2 | Test job synchronization | **Ledger**, never `sbatch --wait`: `watch` waits, `report` summarizes, `cancel` cancels (§1.3) |
| 3 | Phase 2 ownership | **The build stage**; the test stage only runs tests (§2) |
| 4 | CECE's repo-owned sbatch script | **Dropped**; one harness-rendered job covers CECE and CATChem; divergence lives in the adapter's test lines |
| 5 | `kokkos-smoke` | **An application**, like `catchem` (§4) |
| 6 | Modulefiles location / env-root variable | **Top-level `modulefiles/`**; `ASSAY_SPACK_ENV_ROOT` **optional**, full `modules/Core` paths in `modulepath` are the normal case (§3.1) |
| 7 | ESMF ON + Debug | **Required whenever CECE's tests are enabled** (CATChem: Debug); other configurations may turn them off only with tests disabled — a validation rule (§4) |
| 8 | Buildable / Application split | **No split**: CATChem (and kokkos-smoke) are full `Application`s with `stages` and the shared `NoDriverSuite` refusal (§4). Tests are a stage, not a suite (§1.4) |
| 9 | Docker test path | **`docker_prefix` + ctest** (§1.2) |
| 10 | Reporting / benchmarks | **One `TestBenchmarkRow` per test run**; the junit XML per run for individual tests (§1.4) |
| 11 | Gaea | **C7, Ursa, C6 all in scope**; facts are TODOs the user fills by running (§6) |
| 12 | kokkos / kokkos-kernels pins | **TODO(pins)**: `module_env: {kokkos_ver, kokkos_kernels_ver}` per toolchain once `module avail` says |

### 8. Round 2's questions and new ideas, all resolved (rounds 3–6)

Nothing is open in the design. What remains unknown is machine facts, tracked as the TODOs of §6 (hostnames, accounting, env paths, stack module names, version pins).

1. **`--toolchain all`** — **resolved (round 4)**: "yes, i like --toolchain all in that case. we'll want to typically test gcc and intel all in one go" → in scope: several names or `all`, toolchain-major, everything rendered first, a failing toolchain does not stop the others, one effective config with `selected_toolchains`, `watch` / `report` cover all by default (§3.3).
2. **Who writes the test ledger** (expanded in round 5 at the user's request; resolved in round 6: option (b)). The ledger (`<tests root>/jobs.yaml`) is the only thing that connects a submitted test job to `watch`, `report`, `cancel` and the benchmark row: a job without one is invisible to all four. Two ways to write it:
   - **(a) `run` writes it.** The stage script ends with `sbatch --parsable <tests.sbatch>` and prints the id; the `run` process reads it back and writes the ledger. Implications: `run_bash` today returns only an exit code and streams bytes to the log, so `run` must either capture and parse the script's output or read an id file the script leaves — a new stdout/file contract between a rendered script and the CLI; **a script run by hand** (the runbook's manual path, or `bash scripts/04-test-cece-oneapi.sh` to resubmit after a fix) **submits an orphan**: `watch` does not wait for it, `report` says "not submitted", `cancel` cannot find it, and no benchmark row is written unless the user builds the ledger by hand; if `run` dies between the script's `sbatch` and the ledger write (Ctrl-C, a dropped ssh session on the login node) the job is orphaned the same way; the "a test job for this root is already queued" guard has to live in `run`, so it does not protect the by-hand path either. What (a) buys: the script shows the literal `sbatch` line and no new subcommand exists.
   - **(b) The script calls the harness** (recommended): its last line is `uv run --no-sync ufs-chem-assay submit-tests --tests-root <root>`, and that command does guard → `sbatch --parsable` → ledger write in one Python process, with the `submit_job` / atomic-write code the pytest session already uses for driver jobs. Implications: the ledger exists however the script was started, so `watch` / `report` / `cancel` and the benchmark row work for by-hand runs; the orphan window shrinks to the few milliseconds between `sbatch` returning and the atomic rename, inside one process; the already-queued guard (refuse, naming the queued job id and `cancel --tests-root`, unless `--resubmit`) lives beside the submit and covers every path; the slurm script becomes symmetric with native / docker, which already end in `ufs-chem-assay evaluate-tests`. Costs: one more public subcommand to document and test; the rendered script shows `submit-tests` rather than the `sbatch` line (the command logs the exact `sbatch --parsable <path>` it runs, and `tests.sbatch` is still rendered at `run`'s render time, so `--dry-run` shows the whole job); the script needs the harness environment — which is not new: the data stage already runs `uv run --no-sync ufs-chem-assay fetch` and the harness stage `uv run --no-sync pytest` (round 2's "the stage scripts have been pure bash so far" was wrong).
   - Recommendation changed from round 2's (a) to **(b)** on the by-hand-run and crash-window implications. **Resolved (round 6)**: "okay, option two" → (b); §1.3 specifies `submit-tests`.
3. **Build type in the driver benchmarks** — **resolved (round 3)**: "good catch. yes, let's record the build type" → `BenchmarkRow.build_type` from `CMakeCache.txt`, with the one-time header migration of the committed files (§1.4).
4. **The committed test benchmarks file** — **resolved (round 5)**: "definitely per applications" → `src/tests/config/<application>/tests-benchmarks.csv` (§1.4).
5. **Driver sessions on the kokkos templates** — **resolved (round 3)**: "leave sessions as opt-in" → the templates keep the `sessions:` map, the documented commands name the three build-and-test stages, and the driver sessions run on an explicit `--stage data --stage harness --session …` (§5).
6. **`report`'s test line for individual tests** — **resolved (round 5)**: "just the failures please" → failed test names only, capped by `--tail`; no slowest-tests list (§1.3).
7. **C7 environment** — **resolved (round 3)**: "spack-stack 2.2 is the official and all deps should be in there" → no gate relaxes for C7; its toolchain mirrors Ursa's (arborx gate, smoke test on); the first `module avail` confirms and supplies the pins (§6).

---

## Implementation plan (TDD, red → green → refactor)

Seven self-contained commit chunks for one PR on `ufs-chem-assay`; no change in CECE or CATChem is required (the gates read what their configures already print). Each chunk ends with `uv run pytest src/tests/ufs_chem_assay --cov` at 100 %, `uv run pytest src/tests/test_driver_combos.py --dry-run`, every suite under `--dry-run`, and `pre-commit run --all-files`; `README.md` and `design/design.md` are updated in the chunk that changes a surface. The user commits.

- [x] **Chunk 1 — module plumbing and platforms** (implemented 2026-11-16, uncommitted; Appendix B): `modulepath`, `module_env`, `toolchain_tools`, `configure_gates` on `ApplicationRunSection`; `module_block` with `module <init>`, exports and several paths; `driver-job.sbatch.j2` renders the same block; `Platform.GAEA_C6` / `GAEA_C7`, `MODULE_INIT`; the four modulefiles moved to `modulefiles/` with the optional `ASSAY_SPACK_ENV_ROOT` and no fallback roots; `test_user_docs.py` covers `modulefiles/`.
  - Red: `test_stages.py` golden lines for a section with `modulepath: [modulefiles, /abs/modules/Core]` and `module_env` (build stage and job script), a `reset` platform, `which` over `toolchain_tools`, gates rendered and failing; `test_platforms.py` for the new members and `MODULE_INIT`; a Lua-text test that no modulefile carries a hard-coded env root and that each guards the env-root variable for empty.
- [x] **Chunk 2 — build placement** (implemented 2026-11-16, uncommitted; Appendix B): `BuildNode`, `build_node`, `build_minutes`, `RunConfigView.build_launcher` / `build_node`, the `srun` prefix on configure and build, CECE's offline flag swap, CECE's adapter-owned `CECE dependency ESMF: system` gate under `-DENABLE_ESMF=ON` (§3.2; deferred from chunk 1 so it lands with the validator that requires the flag), the login-node phase 2 inside the build stage when `tests.enabled` (which needs `TestsSection` — introduced here with `enabled` only used; the rest lands in chunk 3), the Debug / ESMF validator on `CeceRunSection`.
  - Red: golden lines for `build_node: compute` (two `srun … --time=10` prefixes, `-DCECE_BUILD_TESTING=OFF`, the note; then, with `tests.enabled`, the login re-configure with `-DCECE_BUILD_TESTING=ON`, the gates again, `cmake --build`), no phase 2 with tests disabled, the validation errors (no `slurm:`; `compute` under docker; `tests.enabled` without Debug / ESMF).
- [x] **Chunk 3 — the `test` stage for CECE (issue #9)** (implemented 2026-11-16, uncommitted; Appendix B): `Stage.TEST`, slot 04, the full `TestsSection`, `templates/tests-job.sbatch.j2`, the tests root, the three runtimes' bodies in `applications/cece/cli.py`, the built-tests guard, junit output, the job script rendered beside the stage script; `TestJob` / `TestLedger` in `jobs.py`; the `submit-tests` subcommand (tests root, the already-queued guard and `--resubmit`, evaluate-before-replace, `submit_job`, the atomic ledger write); `tests.evaluate` (sacct, tic/toc, junit → `TestCounts`, `TestBenchmarkRow`, ledger verdict) and `evaluate-tests`; `WatchedTests` in `cli/sessions.py`, `tick` over test ledgers, `watch` evaluating them in-process; `report`'s test line and the two `IssueKind`s; `cancel --tests-root`; `record-benchmarks --tests-root`; `BenchmarkRow.build_type` read from `CMakeCache.txt` at session start, with the header migration of every committed `benchmarks.csv`; `test_cece_tests_is_not_a_stage_of_this_feature` replaced.
  - Red: `test_stages.py` (slurm: the rendered `04-test-cece.sbatch` directives `-n 4 -c 8 --time=5`, `--output` under the tests root, no `srun` before `ctest`, `--output-junit` with `${SLURM_JOB_ID}`, the stage script ending in `uv run --no-sync ufs-chem-assay submit-tests …` with the timeout and queue allowance, no cmake anywhere in the script; native: launcher + env + `evaluate-tests`; docker: `docker_prefix` + `ctest`; `enabled: false` → the note), `test_cli.py` (`--stage test`, default stage list, script name `04-test-cece.sh` and its `.sbatch` beside it, both written under `--dry-run` with nothing submitted), `test_submit_tests.py` (a fake `sbatch`: the ledger written with the returned id; a live job refused with its id and the `cancel` hint; `--resubmit` cancels then submits; a gone-but-unevaluated job is evaluated first; an `sbatch` refusal leaves no ledger), `test_jobs.py` (`TestLedger` round trip), `test_sessions.py` (`tick` with a session ledger and a test ledger in one squeue; evaluation writes the row and the verdict), `test_run_report.py` (the test line; `TESTS_FAILED` from a junit with failures; `TESTS_JOB_FAILED` from sacct `TIMEOUT`), `test_benchmarks.py` (`TestBenchmarkRow` CSV round trip; the junit parser on ctest's junit shape; `build_type` from a cache file, null without one; the migrated committed files reload through the model), `test_run_config.py` (`tests:` defaults and validation).
- [x] **Chunk 4 — `catchem` and `kokkos-smoke`** (implemented 2026-11-16, uncommitted; Appendix B): `Application.stages`, `NoDriverSuite` + the two empty models in `base.py`, `stages` honored by `run` / `configured_sessions` / `peek_application` / `load_suite`; `applications/catchem/` and `applications/kokkos_smoke/` (with `cmake/` from `test-kokkos/smoke/`); registry entries; `test_import_graph.py` and `test_registry.py` extended.
  - Red: `applications/test_catchem.py` (source guard, MUSICA swap and restore, `Using system Kokkos` gate, `tests.ntasks: 1`, the Debug validator, every sweep method raises `NoDriverSuite` with the message), `applications/test_kokkos_smoke.py` (no source stage, cmake against the in-wheel project, `SMOKE TEST PASSED` gate, `tests.evaluate` without a junit), `test_cli.py` (`--application all` renders `data` / `harness` for `cece` only; a suite naming `catchem` is refused; pytest `--application catchem` is refused).
- [x] **Chunk 5 — toolchains** (implemented 2026-11-16, uncommitted; Appendix B): `ToolchainSection`, `toolchains:`, `--toolchain` (names or `all`) on `run` / `watch` / `report`, the toolchain-major loop in `run` (render all first, continue past a failed toolchain, one summary line each, first nonzero status), `selected_toolchains` in the effective config, the overlay onto application sections with the pinned precedence (module keys refused on the application section under a map; lists appended, gates unioned, `tests_env` merged), `mpi_launcher` → cmake flags, the `/<toolchain>` path segment in `checkout_dir`, the tests root, script names and `session_output_root`; `run-config.yaml` records the selection.
  - Red: `test_run_config.py` (the precedence rules; the refusal), `test_stages.py` (gnu renders `-DMPIEXEC_EXECUTABLE=… -DMPIEXEC_NUMPROC_FLAG=-n`; oneapi does not; `-fp-model=precise` appended after the application's args), `test_cli.py` (`--toolchain` required with two entries; an unknown name refused; `all` and `oneapi gnu` render both sets of scripts in map order before any executes; a failing first toolchain still runs the second and the exit status is the first's; paths carry the name; a config without `toolchains:` renders exactly as today; `watch` / `report` default to the recorded selection and tick / report every toolchain; `--toolchain gnu` narrows).
- [ ] **Chunk 6 — Ursa template, docs**: `config/ursa-kokkos.yaml`; `README.md` (the `test` stage, `tests:`, `build_node`, toolchains, `modulefiles/`, `watch` / `report` / `cancel` / `record-benchmarks` additions); `design/design.md` (Execution model, Applications, Code layout, the stage list, the ledger model); a `docs/kokkos-runbook.md` or an addition to `docs/ursa-runbook.md` (the manual equivalent of the rendered scripts, as the stages' docstring requires); `test-kokkos/` reduced to this design's reference (or removed once the modulefiles and smoke project have moved). Then `--dry-run` for every toolchain on macOS.
- [ ] **Chunk 7 — Gaea C7 and C6**: `modulefiles/ufs_gaeac7.oneapi.lua`, `config/gaea-c7.yaml`, `config/gaea-c6.yaml`, `_HOSTNAME_PATTERNS` and `ACCOUNTING` entries as §6's facts arrive; `test_templates_live_in_config` and `test_platforms.py` extended; `--dry-run` for both on macOS. Lands as the facts do — the C7 facts first.

**First runs** (the user, on the machines; results into Appendix B): C7 — `module avail` for the kokkos stack, then `--dry-run`, then `--stage source --stage build --stage test`, `watch`, `report`; Ursa — the same with `--toolchain all`, then the opt-in `simple-maccity` session for one toolchain; C6 last.

---

## Appendix A — Original requirements (verbatim, 2026-11-13)

```markdown
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

- **Session ID**: `<session-id>`
- **Resume Command**: `agy --resume <session-id>`

## testing

- not necessary for design documents in the `spike` folder - code *should not* change for spikes
- *all* suites should pass `--dry-run`
- run `simple-maccity-suite.yaml` without `--dry-run` for integration testing with the driver
- only run examples when requested to do so
- no need to run tests for spikes/documentation-only tasks
- pre-commit hooks pass

# requirements

- refine from https://github.com/benkozi/ufs-chem-assay/issues/16
- also wrap in https://github.com/benkozi/ufs-chem-assay/issues/9
  - we want to also run CATChem tests obviously
- scripts are here: design/feat/20261113-1348-build-test-kokkos/test-kokkos
- how do we wrap this into the harness?
- how can we leverage the current platform configurations?
- goal will be not to duplicate infrastructure if possible.
  - formalizing compilation will be a thing - login v compute node
  - initially we'll support ursa but plan to also test on gaea c6 and c7 which will have different modulefile locations
- no need to include upload-logs.sh at this point
- let's make the refinement iterative for sure, so ask questions and pose new ideas. i think a lot of code in the scripts is duplicative for what we're doing in the assay harness
```

Issue texts at refinement time — #16 "Port bespoke CECE+CATChem `build-test-kokkos.sh` script into framework" (no body); #9 "Harness should run CECE tests locally and on RDHPC": "Harness should optionally run CECE unit tests. Motivation is mostly for benchmarking but also to confirm we have a working CECE."

## Appendix B — Refinement log

- **2026-11-13, round 1**: the audit (§Pre-design audit) mapped every concern of the scripts onto the harness and found four gaps (`test` stage, build placement, toolchains + harness-owned modulefiles, build-only applications); the design filled them, recommended toolchain model A, and listed twelve questions for round 2. The answers to the "how do we wrap this / leverage platform configurations / not duplicate" requirements: the run config template *is* the platform configuration (plus two enum entries and a module-init fact), the `source` stage already is `prepare_source`, the build stage already is `load_modules` + configure + gates, and the only new executables are the two Slurm job templates' commands (`ctest`, `smoke`).
- **2026-11-13, round 2** — the user's answers, verbatim, and what changed:
  - Preamble: "we need esmf on and debug on for cece"; "we will need debug for catchem too" → the Debug / ESMF validation rule (§4), CATChem's `cmake_args` default carries `Debug`, `tests.enabled` defaults to `false` so `config/ursa.yaml` (Release) is untouched.
  - Q1 "A is fine" → §3.3 is model A only; precedence pinned (module keys refused on the application section under a map; lists appended, gates unioned, env merged); `--toolchain` on `watch` / `report`; chunk 5 unconditional.
  - Q2 "I prefer using watch/report as opposed to wait. blocking is never good with hpc in my opinion" → §1.3: `sbatch --parsable`, `TestLedger` under the tests root, `tick` covers it, in-process evaluation, `report` lines and issue kinds, `cancel --tests-root`; `run` never blocks.
  - Q3 "i think the test stage should only run tests. don't leak in any compilation operations" → §2: both phases are the build stage's, phase 2 rendered when `tests.enabled`; the test stage is guard + launch.
  - Q4 "dropping the repo-owned script is acceptable, and using one that covers cece and catchem is okay for now. however, there may likely be some divergence between the two eventually" → one `tests-job.sbatch.j2`; the command and env are the adapter's `stage_lines(Stage.TEST)`, where divergence goes without a second template.
  - Q5 "let's add kokkos-smoke as an application similar to catchem (which i assume is being added as an application)" → §4.
  - Q6 "modulefiles at top-level i think. the spack env root variable should be allowed to be empty as i expect we'll use full paths in most cases" → §3.1: top-level `modulefiles/`; `ASSAY_SPACK_ENV_ROOT` optional with the `~= ""` guard, no fallback roots; `modulepath` carries the env's `modules/Core` as a full path; `module_env` keeps the `<name>_ver` pins.
  - Q7 "again, for the cece test suite, we'll need esmf and debug on. for the other suite configs, it can be turned off, but NOT when running unit tests" → the validator in §4 (`tests.enabled` ⇒ flags present), audit §2.1 settled.
  - Q8 "register catchem as a full application. will tests be considered a suite?" → §4: no ABC split; `Application.stages` + the shared `NoDriverSuite` refusal and two empty models in `base.py`; §1.4 answers the suite question (no: a stage with its own ledger and benchmark row).
  - Q9 "let's go with your proposal" → `docker_prefix` + ctest (§1.2).
  - Q10 "well, we do want to record benchmarks for the whole test run. if there is a regression in performance, we can always check out and monitor individual tests." → §1.4: `TestBenchmarkRow` per test run, junit kept per run, `record-benchmarks --tests-root`.
  - Q11 "c7 is in scope for the feature. ursa, gaea-c6, and gaea-c7. we'll need them tracked as todos, but i'll be running and uploading logs. c7 will likely be first as it already has a spack-stack 2.2 build there" → §6 TODO checklists per platform, `ufs_gaeac7.oneapi.lua` and both Gaea templates in scope (chunk 7), C7 first.
  - Q12 "yes, mark the pins as todo" → TODO(pins) in §3.1 / §3.3 / §6.
  - Round 2 also re-read `jobs.py`, `benchmarks.py`, `cli/sessions.py`, `cli/run_report.py`, `cli/commands/{watch,report,cancel,record_benchmarks}.py` to design the ledger reuse (§1.3, §1.4); audit §3 records those facts. §8 lists seven questions for round 3, including one new idea (`--toolchain all`).
- **2026-11-13, round 3** — the user's answers to §8, verbatim, and what changed:
  - Q3 "good catch. yes, let's record the build type" → `BenchmarkRow.build_type` (from `<checkout>/build/CMakeCache.txt`, null without one) and `TestBenchmarkRow.build_type`; the one-time header migration of the committed per-configuration `benchmarks.csv` files joins chunk 3 (§1.4).
  - Q5 "leave sessions as opt-in" → the kokkos templates' documented commands name `--stage source --stage build --stage test`; the driver sessions run on an explicit `--stage data --stage harness --session …`; no new config key (§5).
  - Q7 "spack-stack 2.2 is the official and all deps should be in there" → the C7 toolchain mirrors Ursa's gates and keeps the smoke test; §6's C7 TODO is the install path, the stack module names and the pins (§5, §6).
  - Q4 of the round-2 reply ("what does --toolchain do again?") was answered in conversation: it selects one entry of the `toolchains:` map (§3.3). Still open after round 3: §8 Q1 (`--toolchain all`), Q2 (who writes the test ledger), Q4 (one or per-application committed test benchmarks file), Q6 (slowest tests in the report line).
- **2026-11-13, round 4** — "i see. yes, i like --toolchain all in that case. we'll want to typically test gcc and intel all in one go" → §8 Q1 resolved: `--toolchain` takes names or `all` like `--application`; toolchain-major execution with everything rendered first; a failed toolchain does not stop the next; one effective config recording `selected_toolchains`; `watch` / `report` cover every recorded toolchain by default (§3.3, chunk 5, the template's documented commands). Still open: §8 Q2, Q4, Q6.
- **2026-11-16, round 5** — the user's answers, verbatim (their numbering "2 / 3 / 6" maps to §8 Q2 / Q4 / Q6, the three that were open):
  - Q2 "you'll need to expand on this a little more for me. what are the implications?" → §8 Q2 now lays out both options with their consequences (by-hand runs, the crash window, the already-queued guard, the script's readability, the extra subcommand) and corrects a round-2 misstatement: stage scripts already call the harness (`uv run --no-sync ufs-chem-assay fetch`, `uv run --no-sync pytest`). The recommendation moved from "`run` writes the ledger" to "the script calls `ufs-chem-assay submit-tests`"; §1.3 is neutral until the user decides.
  - Q4 "definitely per applications" → `src/tests/config/<application>/tests-benchmarks.csv` (§1.4).
  - Q6 "just the failures please" → `report` lists failed test names only (§1.3).
  - Still open: §8 Q2.
- **2026-11-16, round 6** — "okay, option two. update the refinement." → §8 Q2 resolved: the test stage script ends in `uv run --no-sync ufs-chem-assay submit-tests …`, which guards against an already-queued job (`--resubmit` cancels it), evaluates a finished-but-unevaluated predecessor before replacing its ledger, submits with `sbatch --parsable` and writes the ledger atomically, all in one process (§1.3). Consequences written through: the job script is rendered at render time beside the stage script (`scripts/04-test-<application>[-<toolchain>].sbatch`) so `--dry-run` shows it; per-job files carry the job id (`tests-%j.out`, `tests-${SLURM_JOB_ID}.xml`) and the tests root's `benchmarks.csv` appends, so a resubmission overwrites nothing; chunk 3 gains the subcommand and `test_submit_tests.py`. No design question remains open; the unknowns are §6's machine-fact TODOs.
- **2026-11-16, implementation of chunk 1** (working tree only; the user commits). What landed: `Platform.GAEA_C6` / `GAEA_C7`; `platforms.ModuleInit` (`purge` / `reset`), `MODULE_INIT` and `module_init()`; a new `src/lmod.py` whose `module_lines` is the one module block for the build stage and the driver jobs; `modulepath`, `module_env`, `toolchain_tools`, `configure_gates` on `ApplicationRunSection` (both templates list them); `RunConfigView.module_init`; `cli/stages.py` `module_block(config, checkout, section)`, `harness_paths`, `toolchain_probe`, `gate_lines`; `driver-job.sbatch.j2` renders `module_lines`; top-level `modulefiles/` with the optional `ASSAY_SPACK_ENV_ROOT` and no fallback roots; README and `design/design.md` updated. Verified: 945 harness tests at 100 % coverage, every suite under `--dry-run`, `simple-maccity-suite.yaml` against the real driver in docker (21 passed), `pre-commit run --all-files`. Differences from the plan as written, each a detail the design left open:
  1. **How the driver jobs learn the module environment**: two new application settings, `<PREFIX>MODULEPATH` (`os.pathsep`-separated) and `<PREFIX>MODULE_ENV` (`NAME=VALUE` pairs, as `ASSAY_JOB_ENV`), exported by the data / harness / resume stages only when set; relative `modulepath` entries are exported absolute (under the harness checkout). The platform's purge / reset comes from `ASSAY_PLATFORM`.
  2. **`MODULE_INIT` is typed**: `dict[Platform, ModuleInit]` with a `StrEnum`, not `dict[Platform, str]`.
  3. **The harness venv's shell follows the platform too**: `clean_python_env` renders `module reset` on Gaea (it was a hard-coded `module purge`).
  4. **The modulefiles were copied, not moved**: `test-kokkos/modulefiles/` is untouched (the reference scripts still read it) until chunk 6 retires the directory. The headers were rewritten to describe the optional environment root; the bodies differ from the reference only in that block.
  5. **The ESMF gate moved to chunk 2**: chunk 1 renders the run config's `configure_gates` after CECE's two existing gates; the adapter-owned `CECE dependency ESMF: system` gate arrives with the Debug / ESMF validator.
  6. **`module_env` validation**: names must be shell identifiers and YAML numbers become strings (a pin such as `kokkos_ver: 4.5`); the value must be a mapping.
  7. **Not recorded yet**: `run.yaml` and the benchmark rows still record `modulefile` only, not `modulepath` / `module_env`. Worth adding with the toolchain name in chunk 5, when the manifest gains the toolchain anyway.
  8. **Gaea stays explicit**: no `_HOSTNAME_PATTERNS` or `ACCOUNTING` entry (§6 TODOs), so a Gaea run names its platform and records the harness's own clock.
- **2026-11-16, two follow-ups to chunk 1** (working tree only). "can you fix the readme? i think the hostname pattern is just gaeac6 and gaeac7" → (a) the README's and the Ursa runbook's bare `pytest src/tests/test_driver_combos.py` commands gained `--application=cece` (a usage error since monetio was registered), the `--application` option text names both adapters, and `test_user_docs.py` now requires every documented driver command to name an application or a suite; (b) `_HOSTNAME_PATTERNS` gained `gaeac6.*` and `gaeac7.*` (fullmatch: the cluster name followed by anything), supersedes item 8's "no `_HOSTNAME_PATTERNS` entry". The patterns are provisional — §6 keeps the confirm-on-first-login TODO — and `ACCOUNTING` is still without Gaea entries.
- **2026-11-16, hostname patterns corrected** (working tree only). The assistant recalled that C6 login nodes are `gaea61`–`gaea68`; the user: "you are right! probably want a wildcard" → `_HOSTNAME_PATTERNS` is now `gaea6\d+(\..*)?` for C6 and `gaea7\d+(\..*)?` for C7 (the node digits are the wildcard; an optional domain follows, as for Ursa's `ufe\d+`), replacing `gaeac6.*` / `gaeac7.*`, which no Gaea hostname matches. C6 is confirmed; C7 is by analogy and stays on §6's first-login TODO.
- **2026-11-16, implementation of chunk 2** (working tree only; the user commits). What landed: `BuildNode` and `TestsSection` (`enabled` only — the launch keys arrive with the stage in chunk 3) in `applications/base.py`; `build_node`, `build_minutes`, `tests` on `ApplicationRunSection` (both templates list them); `SlurmSection.build_launcher`, `RunConfig.build_launcher` and the load-time refusal of `compute` outside the slurm runtime or without `slurm:`; CECE's build stage in three shapes (login: one configure; compute: the two-`srun` probe with `-DCECE_BUILD_TESTING=OFF`; compute with tests: the probe then the login-node finish with `-DCECE_BUILD_TESTING=ON`), its `CECE dependency ESMF: system` gate under `-DENABLE_ESMF=ON`, and the Debug / ESMF validator; README and `design/design.md`. Differences from the plan as written:
  1. **`build_launcher` takes the section, not the application** (`build_launcher(section, cpus=...)`): the adapters' `cli.py` modules never name their adapter (so the registry stays out of them), and the section already carries `build_node` and `build_minutes`. There is no separate `RunConfigView.build_node`: the adapter reads `section.build_node` through the launcher being empty or not.
  2. **`srun` inherits the login shell's modules**: the module block and the `which` probe run once, in the stage's shell, ahead of both `srun` steps (as the reference script does), not inside them.
  3. **The login build adds no test flag**: under `build_node: login` the configure is `cmake_args` as written (CECE's own default has the test stack on); only the compute path appends `-DCECE_BUILD_TESTING=OFF` / `=ON`, where the cache would otherwise keep the probe's `OFF`.
  4. **monetio rejects `build_node: compute`** at load (its build is `uv pip install` from the network) rather than ignoring the key.
  5. **The Debug / ESMF rule is runtime-blind**: it is a validator of the section, so it also applies under docker, where `cmake_args` is otherwise unused (the container build script owns the configure). Chunk 3 settles what the docker test path builds against; the rule may need to move to the run config then.
  6. **The ESMF gate was checked against CECE `develop`**: its top-level `CMakeLists.txt` prints `CECE dependency <name>: <provider>` for every dependency, so the gate does not depend on the kokkos branch.
  7. **Not exercised on a machine**: the `srun` path is rendered and golden-tested only; its first real run is the Ursa / Gaea first-run list.
- **2026-11-16, implementation of chunk 3** (working tree only; the user commits). What landed: `Stage.TEST` in slot 04 (`cli/stages.SCRIPT_INDEX`); the full `TestsSection`; `Application.test_plan` → `TestPlan` (CECE: ctest); the shared stage body for slurm / native / docker; `templates/tests-job.sbatch.j2`; `apptests.py` (`parse_junit`, `submit`, `record_local`, `evaluate`, `cancel`, `cancel_overdue`); `TestLedger` / `TestJob` / `TestProvenance` / `TestCounts` in `jobs.py`; the `submit-tests` and `evaluate-tests` subcommands; `watch` (one squeue for sessions and test jobs, in-process evaluation, `--only`), `report` (`summarize_tests`, `tests-failed` / `tests-job-failed`, `--only`), `cancel --tests-root`, `record-benchmarks --tests-root`; `TestBenchmarkRow`; `build_type` on `BenchmarkRow` and `run.yaml` with the migration of the two committed CSVs; templates, README, runbook, `design/design.md`. Verified: 1052 harness tests at 100 % coverage, every suite under `--dry-run`, `simple-maccity-suite.yaml` against the real driver in docker, `pre-commit` (tracked and new files), and one real end-to-end run of the docker test path against a scratch CTest project (ctest 3.28's own junit report parsed, the failing status propagated, the ledger / row / report as designed). CECE's real suite was **not** run: the local checkout's test stack is stale, and no Slurm machine was touched. Differences from the plan as written:
  1. **`submit-tests` / `evaluate-tests` take `--root-dir` and `--application`**, not the job's parameters: they load the effective config `run` wrote, as `watch` does, so the script path, the time limit (`tests.minutes`), the queue allowance (`slurm.queue_wait_s`) and the provenance cannot drift from what was rendered. `--resubmit` and `--exit-status` are the only other flags.
  2. **The test ledger is `tests.yaml`, not `jobs.yaml`**: it is a different model in a different kind of root, and a run in place (native, docker) has no job at all; `cancel --output-root` on a tests root (or the reverse) then fails cleanly instead of half-validating. It also carries a `TestProvenance` block and a run ULID, so an evaluation hours later describes the build the tests ran against and the benchmark rows have a key.
  3. **A disabled application gets no `04-test` script**: `run` logs `tests disabled for <application>` and renders nothing, instead of a script whose only line is that note — every existing run would otherwise gain a no-op script and log per application.
  4. **The adapter's part is a typed hook, `Application.test_plan`**, not `stage_lines(Stage.TEST)`: the same command has to be placed in a job script, behind a launcher, or inside an image, which a list of shell lines cannot express. `test_plan` returning None (the default) means "no test suite"; monetio's section also rejects `tests.enabled` at load.
  5. **The test job holds its exit status**: `status=0; <command> || status=$?; toc; report-mem; exit "$status"` — under plain `set -e` a failing suite (the common case worth timing) would lose its `toc` line and its memory report.
  6. **The Debug / ESMF rule moved to `runtime_problems(runtime)`** (chunk 2's item 5): checked by `RunConfig` at load, skipped under docker, and joined by "`all` among targets" in every runtime.
  7. **Without accounting the junit report decides**: where `sacct` has no row (unconfirmed on Gaea), a job is judged 0 only for a report with no failure, with a warning — rather than refusing to judge, as the driver jobs do.
  8. **`watch` / `report` gained `--only sessions|tests`**: after a run of the source, build and test stages alone no session was submitted, and the default would wait on (or report) them. `watch --only tests` is the form the kokkos templates will document.
  9. **A test job queued past its deadline is cancelled by `watch`** (submission + `tests.minutes` + `slurm.queue_wait_s`), then evaluated as Slurm recorded it — the driver jobs' rule.
  10. **Old benchmark files still read**: `read_benchmarks_csv` accepts a header that lacks only `build_type` (old output roots, published baselines' rows) with the cell null; appending to such a file is refused. The migration kept each committed line's own terminator (the files mix LF and CRLF).
  11. **First-run checks for a machine**: that the module environment's ctest is 3.21 or newer (`--output-junit`), that `report-mem` prints inside the test job, and that CECE's MPI tests launch under the configured launcher — the toolchain's `mpi_launcher` is chunk 5.
- **2026-11-16, implementation of chunk 4** (working tree only; the user commits). What landed: `Application.stages` and `NoDriverSuite` in `applications/base.py`; `applications/nodriver.py` (`NoDriverApplication`, `NoDriverConfig`, `NoDriverSuiteConfig`); `applications/catchem/` and `applications/kokkos_smoke/` (with `cmake/CMakeLists.txt` and `smoke.cpp` copied unchanged from `test-kokkos/smoke/`); the registry's two entries, `driver_application` and `driver_application_names`; stage gating in `run`, `render_stage` and `configured_sessions`; `cli/stages.cmake_stage` / `OfflineBuild` / `ctest_plan`, with CECE's build moved onto them (its rendered scripts are unchanged — the existing golden tests are the proof); `TestPlan.passed_marker` through the test ledger to `apptests.evaluate`; README and `design/design.md`. Verified: 1086 harness tests at 100 % coverage, every suite under `--dry-run`, `simple-maccity-suite.yaml` against the real driver in docker, `pre-commit` on tracked and new files. Neither new application was built for real: there is no Kokkos environment on the development machine. Differences from the plan as written:
  1. **The refusal lives in a shared base class**, `NoDriverApplication` in `applications/nodriver.py`, rather than as a helper each adapter's seven sweep methods call: the adapters then contain nothing about sweeps at all. The placeholder models are there too, not in `base.py`, because `SuiteConfig` cannot be imported by `base.py` at runtime.
  2. **`driver_application` is the one gate** for everything driver-shaped (suite files, pytest's `--application`, `fetch`), in the registry; the bare-session hint lists only the applications a session can run.
  3. **`run` logs what it skipped per application** (`catchem takes no part in the data, harness, resume stage(s)`), and the harness-wide data lines (the cartopy cache) go to the first application that has a data stage rather than to the first application.
  4. **CATChem builds in `<clone>/build`**, as CECE does, not the reference script's `<root>/build-catchem`: the benchmark rows read the build type from there, and the test stage's guard and `ctest --test-dir build` assume it.
  5. **CATChem's login-node finish is the `cmake_args` as written** (the cache keeps the probe's `MUSICA=OFF` otherwise), rendered with `tests.enabled`; with no `-DCATCHEM_BUILD_MUSICA=ON` among the arguments there is nothing to swap, and the compute build is the whole build.
  6. **`kokkos-smoke`'s `modulepath` defaults to the harness's `modulefiles/`** (it has no checkout with modulefiles of its own) and its `git_url` / `ref` default to empty; its settings report no checkout commit, so a test run records `application_commit: null` without a warning — the harness commit is its version.
  7. **Both new sections refuse the docker runtime** at load (`runtime_problems`): there is no container build for either.
  8. **A pass marker also guards runs in place**: `evaluate` records exit 1 when the command exited 0 but the marker never appeared.
  9. **Chunk 3 follow-up**: three lint findings in files committed with chunk 3 (`test_apptests.py`, `test_tests_watch.py`, `test_tests_commands.py`, `apptests.py` — an unused fixture argument, naive datetimes in a fixture, a non-raw regex, a `try/except/pass`) were fixed here; the chunk 3 check of new files had passed vacuously (a zsh word-splitting mistake in the command that ran pre-commit on them).
- **2026-11-16, implementation of chunk 5** (working tree only; the user commits). What landed: `ToolchainSection` and `MpiLauncher` in `applications/base.py`, with `ApplicationRunSection.with_toolchain` / `toolchain_updates` / `toolchain_owned`; `toolchains:` and `selected_toolchains` on `RunConfig`, `for_toolchain`, the toolchain segment in `checkout_dir`, `tests_root` and `session_output_root`, `mpi_configure_args`; `cli/selectors.toolchain_configs` and `one_toolchain_config`; `cli/stages.stage_name` in every script name and the launcher arguments in `cmake_stage`; `--toolchain` on `run` (the toolchain-major loop), `watch`, `report`, `submit-tests` and `evaluate-tests`; the toolchain in the session summary, its label and its log lookup; templates (`toolchains: {}`), README, `design/design.md`. Verified: 1117 harness tests at 100 % coverage, every suite under `--dry-run`, `simple-maccity-suite.yaml` against the real driver in docker, a CLI dry run of both toolchains, `pre-commit` on tracked and new files. No toolchain was built for real. Differences from the plan as written:
  1. **A toolchain is a derived config, not a second code path**: `RunConfig.for_toolchain` returns the config as that toolchain builds it, and everything downstream (stages, sessions, tests, the commands) works on it unchanged. The toolchain is a private attribute, so it can never be read from or written to a file; the effective config is always the config as loaded plus `selected_toolchains`.
  2. **"Sets it itself" means "differs from the section's default"**, not "appears in the file": the effective config lists every key, so a reload would otherwise trip the refusal. `kokkos-smoke`'s default `modulepath` is therefore not a claim on the toolchain's key.
  3. **`clone_dir` is refused under a map**, alongside the four module keys: one explicit checkout would be shared by every toolchain's build.
  4. **A section's rules are judged per toolchain** (`toolchains.gnu: applications.cece: tests.enabled requires …`), as the design's "effective cmake_args" implied; without a map they are judged on the section alone, as before.
  5. **The launcher arguments are not part of `cmake_args`**: `mpi_configure_args` returns ready shell words that `cmake_stage` appends unquoted (`"$(command -v srun)"` must be expanded where the configure runs), so they are in the rendered configure and not in the recorded `cmake_args` of a test run.
  6. **Selection default**: without `--toolchain`, `watch` / `report` / `submit-tests` / `evaluate-tests` use the selection the last `run` recorded; `run` itself needs the flag whenever the map has several entries and nothing is recorded in the file it loads.
  7. **`submit-tests` and `evaluate-tests` work on exactly one toolchain** and say so when the run selected several; the rendered stage always passes `--toolchain`.
  8. **`run.yaml` and the driver `BenchmarkRow` do not gain a toolchain column** (chunk 1's log floated it): the `modulefile` and `build_type` columns already tell two toolchains' rows apart, and a third committed-CSV migration was not worth it. The test ledger and `TestBenchmarkRow` do carry the toolchain.
  9. **Two helpers were renamed** (`job_script_for_tests`, `provenance_for_tests`): under their first names pytest collected them as tests in any test module that imported them.
  10. **A test helper stopped mutating its inputs**: `run_config_file` now copies override values, since a dotted override descending into a shared dict had been editing module-level fixtures.
