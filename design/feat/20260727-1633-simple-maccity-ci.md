# Feature: CI job running `simple-maccity-suite.yaml` end-to-end

## Goal

Add an integration CI job that runs the real thing: clone CECE's
`feature/helm` branch, build the CECE container image (cached), compile
CECE inside it (build directory cached), download the one dataset the
maccity suite needs, and run `simple-maccity-suite.yaml` through the
combo-test-runner without `--dry-run` — uploading the full output root
(run.yaml, combos.csv, test-report.csv, stats, plots, NetCDF, cece.log)
as a workflow artifact. Alongside, one small runner feature: **run.yaml
records the CECE commit SHA** the session ran against, so every stored
artifact set identifies its driver exactly.

The job runs on pull requests targeting `develop`. **Merge sequencing
(per the notes)**: while this feature's PR is open the job runs
*blocking* so failures are loud and debuggable; immediately before
merging to `develop` it is flipped to allowed-to-fail
(`continue-on-error: true` at the job level) so a long or flaky
end-to-end run never blocks unrelated PRs. That flip is a deliberate
final commit on this branch, not a follow-up.

## Pre-design audit (fresh facts, 2026-07-27)

Verified on runner `feat/ci-simple-maccity` and the CECE checkout.

1. **CI plumbing already exists to copy from**
   (`design/feat/20260724-1449-basic-ci.md`, implemented):
   `.github/workflows/ci.yaml` builds the runner's own `Dockerfile`
   via buildx with `type=gha` caching and runs pre-commit + harness
   tests inside it; `on:` covers all PRs plus pushes to
   `develop`/`main`. The new job wants a **separate workflow file**:
   its trigger is narrower (`pull_request: branches: [develop]`), and
   the flip-to-allowed-to-fail shouldn't touch the hermetic jobs.
2. **The runner needs host docker, not its own image, for this job**:
   `driver_run` shells out to `docker run cece/cece-dev …` — running
   the runner *inside* a container would demand docker-in-docker.
   Simplest: run the runner on the host VM (`astral-sh/setup-uv` +
   `uv sync --frozen`; python + uv are the only host needs) and give
   the host daemon the CECE image. The runner's `Settings.docker_image`
   default is exactly `cece/cece-dev`, matching CECE's own tooling.
3. **CECE checkout shape**: root `Dockerfile`; nested submodules
   `extern/helm` → `extern/helm/libs/amio` (checkout must be
   `submodules: recursive`); the upstream branch is
   `ufs-community/CECE@feature/helm` (a fork variant
   `feature/helm-benkozi` also exists — the workflow parameterizes
   repository + ref in env at the top so retargeting is one line).
   `scripts/build-and-test-container.py --no-test` compiles the driver
   in the container (image default matches `setup.sh` =
   `cece/cece-dev`); `--no-build`/`--test-filter`/`--mount`/`--image`
   exist for control.
4. **The maccity suite needs exactly one external file**:
   `src/tests/config/cece/simple-maccity.yaml` reads
   `/work/data/MACCity_4x5.nc`, which is `Example.EX3`'s single
   dataset in CECE's own download machinery
   (`examples/common.py`: geos-chem bucket
   `HEMCO/MACCITY/v2014-07/MACCity_4x5.nc`) — so
   `python3 examples/download-example-data.py --example ex3 --dst-dir
   data` is the identified, minimal fetch, and the entrypoint is
   stdlib-only (no environment needed beyond python3).
5. **Baselines cannot run in CI**: `simple-maccity-suite.yaml` carries
   three `baseline_comparisons` ULIDs that live only in the local
   baseline store (`CECE_BASELINE_ROOT_DIR`, not downloadable from
   anywhere yet) — and a *configured-but-missing* baseline is a test
   **failure**, not a skip. The runner already has the switch:
   `CECE_ENABLE_BASELINE_COMPARISONS=false` skips every
   `test_baseline_comparison` regardless of suite config. CI sets it;
   **re-enabling baselines in CI once the baseline store has a public
   home is an explicit TODO** (see Future work).
6. **Expected CI test count**: 21 items — 18 pass, 3 baseline skips
   (the suite's three comparisons under the env switch). Everything
   else (assertions, dimensions check, stats, plots) runs for real;
   cartopy's Natural Earth fetch works on the host runner (offline it
   degrades to data-only maps with a warning, not a failure).
7. **Per-combo timeout risk, noted not preempted**: the suite's
   `timeout_s: 10` caps each driver run (`CECE_RUN_TIMEOUT_S` can only
   lower it, never raise). Local runs take ~2–3 s per combo; if GH
   runners flake near 10 s the contingency is bumping the suite's
   `timeout_s` — a suite-file change, deliberately not made
   preemptively.
8. **run.yaml today**: `RunManifest(run_id, suites)` — no record of
   *which CECE* produced the artifacts; `settings.root_dir` is the
   checkout, so the SHA is one `git rev-parse HEAD` away at
   sessionstart.

Second architecture pass (verified 2026-07-27):

9. **Cache scoping demands a `push: develop` trigger.** GitHub cache
   isolation lets a PR run *restore* caches scoped to its base branch,
   but a workflow that only ever runs on `pull_request` never *creates*
   develop-scoped caches — every fresh PR would rebuild the image and
   CECE from scratch, with its caches invisible to other PRs. The
   workflow therefore also runs on `push: branches: [develop]`
   (post-merge warms the shared caches — the same reason ci.yaml has
   its push trigger), which additionally stores post-merge artifact
   sets for free.
10. **Image and script facts confirmed**: the local `cece/cece-dev`
    image is 1.28 GB uncompressed — comfortably inside the 10 GB
    per-repo gha cache, but image layers + the CECE build cache will
    consume a few GB (eviction shows up as slow runs, not failures).
    The Dockerfile's `ARG BUILD_ESMF=OFF` defaults ESMF off, so a
    plain `build-push-action` honors never-build-ESMF with no
    build-args. `build-and-test-container.py` **does not build the
    image** (pure `docker run` phases — the pre-built loaded image is
    exactly what it wants), and its configure step self-skips when
    `build/CMakeCache.txt` exists, so a restored `cece/build` cache
    goes straight to the incremental `cmake --build -j`. CMake cache
    paths stay valid because configuration happens at the stable
    container path (`/work`) and the workspace path is fixed.
    Container-written files (`build/`, driver NetCDF, `cece.log`) are
    root-owned on the host but world-readable — `actions/cache` and
    `upload-artifact` tar them fine.
11. **Interpreter pin**: the runner's own Dockerfile pins
    `python3.14` (uv base image); the host job mirrors it via
    `setup-uv` with `python-version: "3.14"` so CI runs the same
    interpreter as the hermetic jobs.
12. **Upstream `feature/helm` may lack the local AMIO fixes**
    (describe-variable lock, dimension names — currently on local
    branches). The maccity suite does not depend on them: default
    `amio_worker_threads` (1) means FIFO write order (no dimension
    race) and the suite passed 18/18 long before the lock existed.
    CI deliberately tests upstream as-is; if upstream later regresses,
    this job is the alarm, which is its purpose.

Third pass — diagnosis of the first real CI runs (2026-07-27):

13. **Consistent failures at ~78%, compiling gmock** (`_deps/
    googletest-build/.../gmock-all.cc.o`), presenting as a timeout.
    Three compounding causes, not a mysterious flake:
    - `build-and-test-container.py` runs `cmake --build build -j` —
      **bare `-j` is unlimited parallelism**, a memory-thrash/OOM
      recipe on a 4-vCPU/16 GB GitHub runner with heavy template TUs
      (Kokkos, ArborX) compiling alongside googletest.
    - The build phase compiles the default **"all" target** — every
      C++ test executable plus googletest/rapidcheck — none of which
      this job needs. `gmock` is not in `cece_standalone_driver`'s
      dependency graph at all.
    - **The build cache never saves**: `actions/cache` persists in a
      post-job hook that a `timeout-minutes` kill doesn't reliably
      reach, so every run restarts cold and dies at the same spot —
      the "consistent failure" is a cold-build loop, not one bug.
14. **The fix requires modifying CECE**, so CI retargets to the fork
    branch under our control: `benkozi/CECE @ fix/all-examples-pass`
    (the working checkout of this whole effort). Retargeting back
    upstream later is the one-line env edit the workflow was designed
    for. A Dockerfile `BUILD_TEST_DEPS` arg was considered and
    rejected — test deps enter at CMake time inside the mounted
    checkout, not at image build, so the image has no knob to hold.

## Design

### Phase 1 — runner: `cece_commit` in run.yaml (red-green)

Red: unit tests — a fabricated git repo as `root_dir` yields its HEAD
SHA in the written run.yaml; a **configured root that is not a git
checkout (or has no commits) is a fatal usage error at sessionstart**;
only an *unconfigured* root records null; the manifest round-trip test
carries the field.

Green:

- `RunManifest.cece_commit: str | None = Field(None, description=…)` —
  the CECE checkout's HEAD commit SHA; null **only** when no checkout
  is configured.
- `settings.cece_commit_sha(root_dir)` (`git -C <root_dir> rev-parse
  HEAD`) **raises ValueError on any failure** — sessions always run
  against a checked-out CECE, so an unresolvable SHA is a
  misconfiguration, not a recordable state. Sessionstart converts it
  to the standard usage error (before any work; configured-root
  sessions therefore fail fast on non-checkout roots), stashes the
  value, and `combo_roots` writes it into the manifest. Harness tests
  that fabricate CECE-shaped roots git-init them. README
  (Results/run.yaml description) and `design/design.md` updated.

### Phase 2 — the workflow (`.github/workflows/integration.yaml`)

New file, `name: integration`, triggered on
`pull_request: branches: [develop]` **and** `push: branches: [develop]`
(the push trigger exists to create develop-scoped caches PRs can
restore — audit item 9 — and stores post-merge artifact sets),
concurrency-cancelled per ref under its own group
(`integration-${{ github.ref }}`, distinct from ci.yaml's), single job
`simple-maccity` (`runs-on: ubuntu-latest`, an explicit
`timeout-minutes` around 60 so a wedged driver can't burn a 6-hour
default). Env at the top: `CECE_REPOSITORY` (**`benkozi/CECE`**) and
`CECE_REF` (**`fix/all-examples-pass`**) — the fork branch carrying the
CECE-side script change (audit item 14); retargeting upstream later is
this one edit. Steps:

1. **Checkouts**: the runner repo, and CECE via `actions/checkout`
   with `repository`/`ref` from env, `path: cece`,
   `submodules: recursive`.
2. **CECE image, cached**: buildx + `docker/build-push-action` on
   `cece/` with `cache-from/to: type=gha` (the ci.yaml pattern),
   `load: true`, `tags: cece/cece-dev` — the host daemon ends up with
   the image the runner and CECE tooling both expect; nothing is
   pushed off-runner.
3. **CECE build, cached and bounded** (reworked per audit item 13):
   `actions/cache/restore` on `cece/build` keyed
   `cece-build-<CECE SHA>` with a prefix restore-key, then

   ```
   cece/scripts/build-and-test-container.py --no-test \
     --target cece_standalone_driver --jobs 4
   ```

   — the driver target only (googletest/gmock never compile; they are
   not in its dependency graph) at bounded parallelism, via CECE's own
   entrypoint (verified: no image build; configure self-skips on a
   restored `build/CMakeCache.txt`; ESMF off via `BUILD_ESMF=OFF`).
   The save half is an explicit **`actions/cache/save` step with
   `if: always()`** so a timed-out or failed build still persists its
   partial tree and the next run resumes incrementally — turning the
   cold-build failure loop into convergence. (Saving under an
   already-existing key is a non-fatal warning.)
4. **Data, cached**: `actions/cache` on `cece/data` keyed by the
   dataset identity (`maccity-data-v2014-07`), then
   `python3 cece/examples/download-example-data.py --example ex3
   --dst-dir cece/data` (a cache hit makes this a no-op — the
   entrypoint skips present files).
5. **Run the suite**: `astral-sh/setup-uv` pinned to
   `python-version: "3.14"` (mirroring the runner Dockerfile's
   interpreter — audit item 11), `uv sync --frozen`, then

   ```
   CECE_ROOT_DIR=$PWD/cece \
   CECE_ENABLE_BASELINE_COMPARISONS=false \
   uv run pytest src/tests/test_driver_combos.py \
     --suite-config=simple-maccity-suite.yaml \
     --combo-output-root=ctr-ci-output
   ```

   (audit item 5: baselines off — no public baseline store yet; the
   explicit output root resolves into the CECE checkout at
   `cece/ctr-ci-output`, a plain host path the upload step can reach.)
6. **Artifacts, always**: `actions/upload-artifact` (`if: always()`)
   of `cece/ctr-ci-output/` — run.yaml (now carrying `cece_commit`),
   combos.csv, test-report.csv, stats CSVs, plots/GIFs, driver NetCDF,
   `.out`, and `cece.log` per combo; named with the run attempt so
   retries don't collide.

**Allowed-to-fail flip**: developed and reviewed with the job blocking;
the branch's final commit before merge adds `continue-on-error: true`
on the job (recorded in this doc's notes when it happens).

### CECE-side change (`scripts/build-and-test-container.py`, fork branch)

Two flags, keeping the script's name and phase semantics (`--no-build`/
`--no-test` already subset phases; these subset *what the build phase
builds*):

- `--target NAME` (repeatable; default: the "all" target as today) —
  forwarded as `cmake --build build --target …`. Generic beats a
  bespoke no-test-build flag: CMake has no "everything except tests"
  target, so the honest mechanics are "build these targets".
- `--jobs N` (default: `os.cpu_count()`) — replaces the unbounded bare
  `-j`, fixing a latent footgun for every caller, not just CI.

No rename (the default invocation still builds and tests; the name
stays true) and no CMake `BUILD_TESTING` gating — configure-time
leanness is a possible later CECE refinement, unnecessary for this
job since `--target` already skips compiling the test stack.

### Out of scope (deliberately)

- Baseline comparisons in CI — **Future work: publish the baseline
  store (or regenerate baselines in CI) and drop
  `CECE_ENABLE_BASELINE_COMPARISONS=false` from the workflow; until
  then every CI run skips the suite's three baseline tests.**
- Running other suites (examples need larger data and have their own
  arcs); exhaustive suites in CI; scheduled/nightly triggers.
- Publishing images or artifacts anywhere but the workflow-run
  artifact store.
- Preemptive timeout changes (audit item 7's contingency).

## Verification

- Runner unit tests (new `cece_commit` tests + existing) via
  `uv run pytest src/tests/combo_test_runner`; all suites still pass
  `--dry-run`.
- `simple-maccity-suite.yaml` without `--dry-run` locally: 18 passed +
  3 baseline skips with `CECE_ENABLE_BASELINE_COMPARISONS=false`
  (mirroring CI), and run.yaml carries the checkout's HEAD SHA.
- Workflow YAML passes yamllint/pre-commit; actual workflow execution
  is **user-triggered** (open/update the PR) — the report-back loop on
  the real run follows, as with the hemco CI fix.
- Pre-commit hooks pass.

## Constraints

- pydantic models with `Field(description=…)`; avoid `Any`.
- Never commit — the user commits.
- No CECE-checkout modifications; never build ESMF; nothing pushed to
  external registries.
- README.md and `design/design.md` updated as part of implementation.

## Acceptance criteria

1. run.yaml contains `cece_commit` (the checkout's HEAD SHA; null only
   when no checkout is configured; a configured root that is not a git
   checkout fails the session at start), unit-tested all three ways.
2. `.github/workflows/integration.yaml` exists: PRs to `develop` plus
   pushes to `develop` (the cache-warming trigger); CECE
   `feature/helm` cloned with recursive submodules; image built
   through the gha buildx cache; `cece/build` and `cece/data` cached;
   the maccity suite runs with baselines disabled via
   `CECE_ENABLE_BASELINE_COMPARISONS=false`; the output root uploads
   on success *and* failure.
3. The baseline-re-enable TODO and the pre-merge allowed-to-fail flip
   are both recorded in this doc.
4. Local mirror run: 18 passed + 3 baseline skips; harness tests and
   `--dry-run` all green; hooks pass.

## Implementation notes

**Outcome: implemented 2026-07-27; local mirror green; the real
workflow run awaits the user's PR (report-back loop follows).**

- **Phase 1 (`cece_commit`)**: `settings.cece_commit_sha(root_dir)` —
  `git -C <root> rev-parse HEAD`, **raising ValueError on every
  failure mode** (not a repo, no commits, git missing/failing/timeout)
  per the fatal-error direction; sessionstart converts to a usage
  error for configured roots, records null only for an unconfigured
  root, and stashes the value (`_CECE_COMMIT`) for `combo_roots` to
  write. `RunManifest.cece_commit` (`Field(description=…)`, null only
  without a checkout). Red-green tests: helper SHA/raise (non-repo,
  commit-less repo); a subprocess dry-run against a git-inited root
  asserts run.yaml carries the exact HEAD SHA; a new guard test
  asserts a configured non-checkout root is a usage error naming the
  path. Ripple: every harness test that fabricates a CECE-shaped root
  and reaches sessionstart now git-inits it (examples-gating fake
  root, dry-run roots, root-dir-guard flag tests — the flag-wins test
  deliberately repo-ifies only the winning root).
- **Phase 2 (`.github/workflows/integration.yaml`)**: as designed —
  PR-to-develop + push-to-develop triggers (cache warming),
  `integration-${{ github.ref }}` concurrency, `timeout-minutes: 60`,
  env-parameterized `ufs-community/CECE` @ `feature/helm`, recursive
  submodule checkout into `cece/`, `build-push-action` with
  `type=gha,scope=cece-image` cache and `load: true` as
  `cece/cece-dev`, `actions/cache` on `cece/build`
  (`cece-build-<sha>` + prefix restore-key) feeding
  `build-and-test-container.py --no-test`, `actions/cache` on
  `cece/data` (`maccity-data-v2014-07`) feeding
  `download-example-data.py --example ex3`, `setup-uv@v6` pinned to
  python 3.14, the suite run with
  `CECE_ENABLE_BASELINE_COMPARISONS=false` and
  `--combo-output-root=ctr-ci-output`, and an `if: always()`
  `upload-artifact` of `cece/ctr-ci-output` named by run id +
  attempt. The header comment carries the baselines TODO and the
  merge-sequencing note (blocking now; `continue-on-error: true`
  added as the final pre-merge commit).
- **Local mirror verification**: with baselines off,
  `simple-maccity-suite.yaml` = **18 passed + 3 skipped** (exactly the
  CI expectation) and run.yaml's `cece_commit` matched
  `git rev-parse HEAD` of the checkout byte-for-byte. 214 unit tests
  green; maccity `--dry-run` green; all 7 pre-commit hooks pass
  (yamlfix accepted the workflow file unchanged).
- **Not verifiable locally**: the workflow execution itself
  (buildx/gha cache behavior, runner wall-clock vs the 10 s per-combo
  timeout, artifact upload) — user-triggered via the PR; findings from
  the first real runs land here.
- **CI-failure fix round (2026-07-28, after the first real runs)**:
  per the third-pass diagnosis (audit items 13–14),
  `build-and-test-container.py` gained `--target` (repeatable,
  append) and `--jobs` (default `os.cpu_count()`), with `build()`
  emitting `cmake --build build -j N [--target …]`; the workflow now
  targets `benkozi/CECE @ fix/all-examples-pass`, builds only
  `cece_standalone_driver` at `--jobs 4`, and splits the build cache
  into `actions/cache/restore` + `if: always()` `actions/cache/save`
  so partial cold builds persist and converge. Verified locally: the
  exact CI invocation builds the driver (incremental, no
  googletest/gmock compiled), `--help` documents both flags, runner
  hooks 7/7, and the baselines-off maccity mirror stays 18 + 3. The
  script change lives in the CECE fork checkout (user commits there).
  **Confirmed 2026-07-28: the real CI run passes with the fix** — the
  driver-only bounded build eliminated the ~78% gmock failure.
- **Retarget to `develop` attempted and reverted (2026-09-01)**: a
  move of `CECE_REF` to the fork's `develop` was requested, and the
  retarget audit found `develop` (through the PR #46
  TIDE-removal/HELM restructure) does **not** contain the
  `--target`/`--jobs` script change — the build step would die on an
  unknown argument until that change lands there. On that finding the
  user reverted the direction: **CI stays on
  `benkozi/CECE @ fix/all-examples-pass`**, whose remote head
  (`c480810`, 2026-07-28) is exactly the state the green CI run
  exercised and has not moved since. The workflow and the local CECE
  checkout were restored to their pre-retarget state; nothing
  changed on this branch except this record. Standing dependency for
  any future retarget (develop or upstream): the target ref must
  carry the `--target`/`--jobs` script flags first.
- **Pre-merge flip applied (2026-09-01, user direction)**: the
  `simple-maccity` job now carries `continue-on-error: true` — the
  designed final pre-merge step, closing the merge-sequencing thread.
  The workflow header notes the consequence: the workflow reports
  green even when the job fails, so the job's own status is the
  signal to watch. The branch is merge-ready.
- **Docs**: README CI section (integration workflow paragraph + local
  mirror command) and Results tree (`run.yaml` now lists
  `cece_commit`); `design/design.md` layout block updated likewise.

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

- add a ci job that runs the simple-maccity-suite.yaml
- it should:
  - clone cece's feature/helm branch
  - build the cece container (cache it)
  - build cece in the container (cache the cece build)
  - run the simple-maccity-suite.yaml
    - external data for the example will need to be downloaded. identify which example data is needed and download it.
- ci job is allowed to fail
- ci job is run on pull requests to develop
- ci job should fail but before merge we will change to allow to fail before merge develop. note in design.
- store output cece artifacts from the combo-test-runner
- update combo-test-runner to store the current cece commit sha in the output run.yaml

## conversational updates

- 2026-07-27: **baselines are off in CI** (user direction, after the
  audit caught that a configured-but-missing baseline *fails* rather
  than skips): the workflow sets
  `CECE_ENABLE_BASELINE_COMPARISONS=false` because the baseline store
  is not downloadable from anywhere yet. **Re-enabling baselines in CI
  once they have a public home is a standing TODO** (Out of
  scope/Future work).
- 2026-07-27: **an unresolvable CECE commit is fatal** (user
  direction, superseding the swallow-to-null first cut): sessions
  always run against a checked-out CECE, so a configured root whose
  SHA cannot be determined fails the session at start with a usage
  error; null is recorded only when no checkout is configured at all.
  Fabricated CECE roots in the harness tests are git-inited to match.
- 2026-07-27: **SHA ownership moved into `Settings`** (user
  direction) — the module-level `cece_commit_sha(root_dir)` became the
  method `Settings.get_cece_commit_sha()`, which also absorbs the
  unconfigured-root-returns-None branch; conftest and the tests import
  only `Settings` (one import fewer, one call site with no ternary).
- 2026-07-27: **CI-failure diagnosis and the CECE-side fix** (user
  direction after consistent ~78% gmock failures): root causes are
  unbounded `-j`, building the unneeded "all" target, and a build
  cache that never saves on timeout (audit item 13). Fix:
  `build-and-test-container.py` gains `--target` (repeatable) and
  `--jobs` (default `os.cpu_count()`); the workflow builds only
  `cece_standalone_driver` at `--jobs 4` and splits the build cache
  into restore + `if: always()` save so partial cold builds converge.
  CI retargets to `benkozi/CECE @ fix/all-examples-pass` — the fork
  branch we can modify; upstream retarget stays a one-line env edit.
  A Dockerfile `BUILD_TEST_DEPS` arg was considered and rejected
  (test deps enter at CMake time, not image build); a CMake
  `BUILD_TESTING` gate remains a possible later CECE refinement.
- 2026-07-27: **`RunManifest.cece_commit` is required-but-nullable**
  (user review) — fully non-nullable would kill the checkout-less
  `--dry-run` flow (a documented feature: suite validation with zero
  environment), so instead the field lost its default: every writer
  must state the SHA explicitly, deliberate `null` stays expressible
  for that one flow, and omission is a validation error no future code
  path can slip past (unit-tested).
- 2026-07-27: **second architecture pass** (user request) confirmed
  the host-runner/loaded-image/build-script choices against the real
  files (image 1.28 GB, `BUILD_ESMF=OFF` default, configure
  self-skip, root-owned outputs tar fine, python 3.14 pin) and caught
  one real flaw: a pull_request-only trigger never creates
  develop-scoped caches, so cross-PR caching silently would not
  exist — fixed by also running the workflow on pushes to `develop`.
  Also added: distinct concurrency group and an explicit job
  `timeout-minutes`, plus the note that CI tests upstream
  `feature/helm` as-is (the maccity suite does not depend on the
  locally-pending AMIO fixes).
