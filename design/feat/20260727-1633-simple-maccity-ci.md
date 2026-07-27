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

## Design

### Phase 1 — runner: `cece_commit` in run.yaml (red-green)

Red: unit tests — a fabricated git repo as `root_dir` yields its HEAD
SHA in the written run.yaml; a non-repo (or unset) `root_dir` yields
null and never errors; the manifest round-trip test carries the field.

Green:

- `RunManifest.cece_commit: str | None = Field(None, description=…)` —
  the CECE checkout's HEAD commit SHA, null when no checkout is
  configured or it is not a git repository.
- A small helper (`git -C <root_dir> rev-parse HEAD` via subprocess,
  swallowing every failure to `None` — recording must never break a
  run) called once at sessionstart; `combo_roots` writes it into the
  manifest. README (Results/run.yaml description) and
  `design/design.md` updated.

### Phase 2 — the workflow (`.github/workflows/integration.yaml`)

New file, `name: integration`, triggered on
`pull_request: branches: [develop]` **and** `push: branches: [develop]`
(the push trigger exists to create develop-scoped caches PRs can
restore — audit item 9 — and stores post-merge artifact sets),
concurrency-cancelled per ref under its own group
(`integration-${{ github.ref }}`, distinct from ci.yaml's), single job
`simple-maccity` (`runs-on: ubuntu-latest`, an explicit
`timeout-minutes` around 60 so a wedged driver can't burn a 6-hour
default). Env at the top: `CECE_REPOSITORY` (`ufs-community/CECE`) and
`CECE_REF` (`feature/helm`) so the target is one edit. Steps:

1. **Checkouts**: the runner repo, and CECE via `actions/checkout`
   with `repository`/`ref` from env, `path: cece`,
   `submodules: recursive`.
2. **CECE image, cached**: buildx + `docker/build-push-action` on
   `cece/` with `cache-from/to: type=gha` (the ci.yaml pattern),
   `load: true`, `tags: cece/cece-dev` — the host daemon ends up with
   the image the runner and CECE tooling both expect; nothing is
   pushed off-runner.
3. **CECE build, cached**: `actions/cache` on `cece/build` keyed
   `cece-build-<CECE SHA>` with a prefix restore-key (stale-tree
   incremental rebuilds are cmake's normal case), then
   `cece/scripts/build-and-test-container.py --no-test` — CECE's own
   entrypoint compiles the driver in the already-present image
   (verified: the script never builds an image, and its configure
   step self-skips when the restored `build/CMakeCache.txt` exists,
   so a warm cache goes straight to the incremental build; ESMF stays
   off via the Dockerfile's `BUILD_ESMF=OFF` default).
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

1. run.yaml contains `cece_commit` (the checkout's HEAD SHA; null
   without a git checkout), unit-tested both ways.
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

(To be filled during implementation.)

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
