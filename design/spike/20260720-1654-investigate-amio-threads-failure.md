# Spike: investigate AMIO reliability — worker-threads crash + error-reporting clarity

> **Absorbed** (2026-07-23) by
> `design/fix/20260723-1029-amio-thread-segv.md`, which executed the
> investigation and landed the fix. Final verdict (corrected by the
> lock-only experiment): the crash was AMIO's own mutex discipline
> having one hole — an **unlocked `describe_variable`** issuing
> `nc_inq_*` from the caller thread concurrent with worker reads;
> locking it fixes ex7@2 (10/10) even against the non-thread-safe
> openmpi HDF5. An `AMIO_NETCDF_SERIAL` option (serial, thread-safe
> HDF5) was prototyped as defense-in-depth and then reverted by
> direction — the shipped fix is the lock alone; the serial-linkage
> pattern is documented in the fix doc should it ever be wanted. The **error-clarity findings remain live here** for upstream
> filing (misattributed backpressure, uncaught exceptions → abort, and
> `verify_parallel_support`'s R7.1 gate rejecting serial builds that
> never need parallel I/O).

## Goal

Characterize **AMIO's reliability failures** — originally the
worker-threads crash from `design/fix/20260720-1500-fix-cece-examples.md`,
refined by the 2026-07-21 findings below into **two live defect leads
plus one confirmed diagnosability defect**:

1. the threads-triggered memory corruption (ingest segfault on the old
   binary → shutdown double-free + hang on the rebuilt one);
2. an intermittent ingest segfault at *default* thread count (ex2
   flake);
3. **error-handling clarity (confirmed)**: AMIO/driver errors
   misattribute root causes — a dimensionally mis-ordered input file
   surfaced as `AMIO_ERR_STAGING_BACKPRESSURE` with no reference to the
   data layout, and driver failures escalate as uncaught exceptions →
   `abort()` + MPI backtrace (see findings 3–4).

— well enough to (a) file high-quality CECE issues covering all three,
(b) **evaluate the remediation** for the threads crash: a derived image
with gdb and a **thread-safe HDF5** (serial core-I/O libraries are not
acceptable) must allow `amio_worker_threads >= 2`, and (c) produce a
tracked change inventory for the eventual implementation (image +
example updates enabling multiple AMIO threads). Deliverable is a report
in `design/artifacts/` containing the evidence, the remediation verdict,
the issue drafts, and the change inventory.

**Precondition — PR #59 merged: SATISFIED (verified 2026-07-21).**
[ufs-community/CECE#59](https://github.com/ufs-community/CECE/pull/59)
landed (`8cb8b60`), followed by #64 (HELM docs/config), and
`feature/helm` is merged into the working branch (`e8bb533`). The
checkout's `Dockerfile` carries `ARG BUILD_ESMF=OFF` (verified) — image
rebuilds are cheap and ESMF-free by construction, and the spike never
turns the flag on. The driver binary was rebuilt on the merged codebase
(2026-07-21 16:11); re-verified: it still links `libnetcdf_mpi` (the
non-thread-safe openmpi HDF5 path — H1's premise holds) and zero ESMF
libraries. Remaining start item: rebuild `cece/cece-dev` from the merged
Dockerfile if the image predates it, so measurements run on matching
image + binary.

**Repo-change rules**: no changes to CECE src, examples, scripts, or the
checked-in `Dockerfile` (explicitly: *not yet* — the PR-59 Dockerfile is
used **as-is** with its `BUILD_ESMF=OFF` default; the spike's additions
live in the report as a draft diff). None in the runner. The evaluation
image is built as a **derived image** from a scratch Dockerfile in the
session scratchpad (`FROM` the PR-59-built base, tagged
`cece/cece-dev-amio-spike`) so the checked-in file stays untouched.
Scratch configs (`cece_scratch_*.yaml`, invisible to the
`--run-examples` glob) run directly via `docker run` and are deleted
afterwards. The checkout is already on post-merge history; the pending
uncommitted examples work in the CECE tree is the user's to land and
stays untouched.

## What is already known

From the examples fix (2026-07-20, **pre-merge driver**):

- On ex7 (7 streams), `amio_worker_threads: 2` → SIGSEGV "Address not
  mapped" **during initial stream ingest**; `1` and absent both pass.
  Config bisection proved the threads knob is the trigger, not the
  cadence machinery.
- The crash is in the driver's own binary frames (abort backtrace,
  address-only).

Findings update (2026-07-21, **post-merge rebuilt driver** — the failure
modes shifted and multiplied; each observed once or twice, so the spike
treats them as leads to characterize, not settled facts):

1. **threads=2 now corrupts at shutdown and hangs.** A minimal 1-stream
   scratch config (ex3 shape + `amio_worker_threads: 2`) *completed all
   work and wrote its output*, then hit
   `double free or corruption (!prev)` → SIGABRT during
   "Cleaning up…" — and the process **hung** in the abort path instead
   of exiting (killed after ~10 min). Same knob, different phase:
   ingest-segfault (old binary) vs shutdown heap corruption + hang (new
   binary) — a classic race signature morphing across builds.
2. **Intermittent ingest segfault at default thread count.** ex2
   (mask-scoped replace; no `amio_worker_threads` set) exits 139 during
   stream ingest on some runs and 0 on others, with no error text. The
   race is therefore not gated on the threads knob alone.
3. **RESOLVED (2026-07-21 eve): the staging backpressure was
   misattributed — root cause was a mis-ordered input file.** The
   `AMIO_ERR_STAGING_BACKPRESSURE` (rc=7, "staging pool exhausted") on
   `amio_read('FH_weekday_tro', t=0)` disappeared entirely when the
   user's dimensionally mis-ordered `_utc` hourly file was replaced with
   a correctly-ordered `(time, latitude, longitude)` copy — the full
   gate went **7/7 green**, including a 1.17 GB monthly file whose
   ~311 MB variables read cleanly through the same hardcoded 8×256 MB
   pool. Both staging hypotheses (H4a capacity, H4b pool leak) are
   **rejected** for this case; the earlier discriminator experiments are
   no longer needed. What survives is the facade's hardcoded manifest
   (still a configurability gap worth a sentence in the issue) and,
   mainly, finding 4.
4. **Error reporting misattributes — now confirmed, not suspected.** A
   data-layout problem (wrong dimension ordering) surfaced as a
   resource-exhaustion timeout with zero reference to the input's
   shape — burning an investigation cycle on staging-pool theories. The
   issue drafts gain a concrete diagnosability item: AMIO/driver should
   validate variable dimension ordering at open/read and name the
   offending variable and layout, rather than starving the pool and
   timing out. The broader error-surface critique stands (uncaught
   exceptions → abort + MPI backtrace; symptom-accurate but
   cause-silent codes).

Consequence for the experiment plan: **every driver invocation runs
under an explicit timeout** (hangs are now a known failure mode; a
wedged container must be killed and counted as "hang", a distinct
outcome from pass/segfault/abort), and the isolation matrix gains a
default-threads ex2-shaped flake column (≥10 reruns) alongside the
threads sweep.

## Pre-spike environment probe (already run — the leading hypothesis)

Ubuntu 24.04 ships **two** HDF5 flavors in the image, verified via
`H5pubconf.h`:

- **serial** (`libhdf5-103-1t64`): `H5_HAVE_THREADSAFE 1` — apt's serial
  build **is thread-safe** ("serial" means non-MPI, not non-threadsafe).
- **openmpi** (`libhdf5-openmpi-103-1t64`): `H5_HAVE_PARALLEL 1`,
  **no threadsafe** — upstream treats parallel and threadsafe as
  mutually exclusive builds.

**The driver links the wrong flavor for threading**: `ldd` shows
`libnetcdf_mpi.so.19 → libhdf5_openmpi_hl.so.100 / libhdf5_openmpi.so.103`
(the `Dockerfile` installs `libnetcdf-mpi-dev`). With PR 59's
`BUILD_ESMF=OFF` there is no ESMF consumer of netCDF in the image at
all, removing any competing linkage constraint on the serial switch. No
gdb in the image; OpenMPI reports `MPI_THREAD_MULTIPLE: yes`.

**Hypothesis H1 (primary, sharpened): AMIO worker threads issue
concurrent netCDF/HDF5 calls through `libnetcdf_mpi` into the
non-thread-safe *openmpi* HDF5.** The dependencies are not missing or
mis-installed — the thread-safe library is already in the image from
apt; the driver simply links the parallel flavor. Docker is incidental —
the same linkage would fail anywhere.

Fallback hypotheses if H1 evidence doesn't hold: H2 — race in AMIO's own
queue/buffer management (crash would persist even with reads serialized
by a single stream); H3 — thread-count–dependent resource exhaustion
(stack size, file handles; would scale with thread count, not
concurrency).

## Experiment plan (all scratch configs + ephemeral containers)

1. **Minimal reproducer search.** Start from a 1-stream config (MACCity,
   the smallest known-good data) with `amio_worker_threads: 2`; if it
   doesn't crash, grow along one dimension at a time (streams 1→2→5,
   distinct files vs the same file twice, small vs large files, with and
   without scale-factor streams) until it does. Product: the smallest
   crashing YAML, inlined in the report.
2. **Thread sweep** on the minimal reproducer and on one known-good
   example shape: `amio_worker_threads` ∈ {1, 2, 3, 4, 8}. Record
   pass/crash, and whether the crash point (which stream) moves —
   nondeterministic movement across identical reruns (≥3 each) is strong
   H1/H2 race evidence; deterministic scaling with thread count favors
   H3.
3. **Concurrency vs thread-count discrimination**: rerun the crashing
   case with `OMP_NUM_THREADS=1` and with the same total data but a
   single stream, to separate "many threads exist" from "threads read
   concurrently".
4. **Symbolized backtrace.** gdb comes from the derived image (below);
   capture `gdb -batch -ex run -ex bt` plus `thread apply all bt` at the
   crash. Fall back to `addr2line` on the recorded offsets if needed.
   Frames landing in `H5*`/`nc_*` calls from two AMIO threads would
   confirm H1 directly.
5. **Environment audit for the report**: `ldd` of the driver (which
   netCDF/HDF5 actually link, resolved with `/work` mounted), exact
   package provenance (`dpkg -l | grep -E 'hdf5|netcdf'`), HDF5
   threadsafe flag (`H5_HAVE_THREADSAFE` via `h5cc -showconfig`) —
   answering the requirement's "are dependencies missing / installed
   correctly?" question precisely.
6. **Remediation evaluation in the derived image**
   (`cece/cece-dev-amio-spike`, scratch Dockerfile `FROM cece/cece-dev`;
   ESMF untouched):
   - install **gdb** (apt).
   - **No HDF5 source build should be needed**: the thread-safe HDF5 is
     already installed (apt serial flavor). The remediation is a
     **relink**: rebuild the driver in the derived image against serial
     netCDF (`libnetcdf-dev` → thread-safe HDF5) instead of
     `libnetcdf_mpi`, into a scratch out-of-repo build dir. **ESMF-free
     by construction**: the standalone driver links no ESMF library
     (verified: 0 of its 60 `ldd` entries) and CMake's `ENABLE_ESMF`
     defaults to OFF — the spike build keeps it OFF and never invokes
     any ESMF step; if any step turns out to demand ESMF, that step is
     abandoned and recorded as blocked rather than performed. How the
     driver's CMake selects netcdf_mpi vs netcdf is itself a finding for
     the change inventory.
   - **Contingency**: if the driver genuinely requires parallel-netCDF
     features (nc_*_par), threadsafe + parallel is an upstream
     either/or — then no image fix exists and AMIO-side call
     serialization (src fix) becomes the issue's recommendation.
   - **Verdict runs**: the minimal reproducer and the full experiment
     matrix repeated in the derived image at threads {2, 4, 8}. Segfault
     gone ⇒ H1 confirmed and the relink remediation validated; still
     present ⇒ H1 rejected, evidence goes to the issue draft and the
     src-side hypothesis (H2) leads.

## Deliverables

- **`design/artifacts/20260720-1654-amio-threads-report.md`**:
  environment audit, isolation matrix (config × thread count × outcome,
  with rerun counts, base **and** derived image), minimal reproducer
  YAML, backtrace, hypothesis + remediation verdicts, and
  **ready-to-paste CECE issue drafts** — likely two: (1) the AMIO race
  (threads-triggered corruption + the default-threads flake, if the
  evidence ties them together), and (2) **error-handling clarity**:
  validate input dimension ordering at open/read and name the offending
  variable/layout instead of surfacing `AMIO_ERR_STAGING_BACKPRESSURE`
  (the confirmed misattribution, with the mis-ordered-file repro);
  replace uncaught-exception `abort()`s with clean, named-cause errors;
  and a sentence on the facade's hardcoded AMIO manifest (staging pool /
  timeouts not configurable). Each draft with title, environment, repro
  steps with the scratch YAML, expected/actual, evidence, suspected
  cause, and the validated fix direction.
- **Change inventory for the eventual implementation** (in the report;
  nothing applied yet): the draft `Dockerfile` diff (gdb; the
  thread-safe-I/O linkage change — expected to be switching the driver's
  netCDF from `libnetcdf-mpi-dev` to serial `libnetcdf-dev`, exactly as
  validated in the derived image, including any driver CMake flag it
  requires), the example-config changes to enable multiple AMIO threads
  (e.g. ex7's `amio_worker_threads` unpinned from 1, and which examples
  should gain the knob) — sized so a follow-up feat doc can implement it
  directly.
- **Regression-testing recommendation** (design only, for a future
  feature doc): a checked-in minimal AMIO-threads example (e.g.
  `cece_config_ex8`-style, auto-discovered by `--run-examples` once the
  bug is fixed) is the lightest option; a runner-side
  `amio_worker_threads` sweep (would need the field added to
  `CeceConfig` + a suite dimension) is the heavier alternative — the
  report recommends one with rationale, sized to "all examples at small
  thread counts, max ~8".

## Constraints & standing rules

- No changes to any checked-in file — CECE src/examples/scripts, the
  `Dockerfile` (draft diff lives in the report only), or the runner. No
  commits (user commits). Scratch YAMLs live outside the discovery glob
  and are removed; the scratch Dockerfile lives in the session
  scratchpad; the derived image is tagged `cece/cece-dev-amio-spike` and
  left available for follow-up work (noted in the report; removable with
  `docker rmi`). The CECE working tree's pending changes stay untouched
  (`git status` clean-delta before vs after).
- **No ESMF, at all — assume it is not built.** The PR-59 Dockerfile
  skips ESMF by default (`BUILD_ESMF=OFF`), and the spike never sets it
  ON; the driver rebuild runs with CMake's default `ENABLE_ESMF=OFF`
  (the standalone driver verifiably links no ESMF library — 0 of 60
  `ldd` entries). Any step that would require ESMF is abandoned and
  recorded as blocked.
- Spike ⇒ no runner test runs required; driver executions here are the
  spike's subject (sanctioned by the requirements), using already-cached
  `data/` files — no new downloads expected.
- `design.md`/`README.md` untouched (no behavior changes to document);
  the spike doc + artifact report are the record.

## Acceptance criteria

- A minimal reproducer exists (or the report documents the smallest
  crashing configuration found and why smaller ones pass). The
  2026-07-21 1-stream shutdown-corruption case is the current best
  candidate.
- The isolation matrix covers thread counts {1, 2, 3, 4, 8} with ≥3
  reruns at the crash boundary, in the base image **and** the derived
  image — outcomes classified as pass / segfault / abort / **hang**
  (every run under an explicit timeout), plus a default-threads
  ex2-shaped flake column with ≥10 reruns.
- The H1 verdict is stated with direct evidence (backtrace frames or
  documented best-effort failure to obtain them), and the **remediation
  verdict** states whether thread-safe HDF5 + gdb in the derived image
  allows `amio_worker_threads` up to 8 — with the exact escalation level
  that was required (library swap / netcdf rebuild / driver rebuild).
- The change inventory is complete enough for a follow-up feat doc to
  implement the Dockerfile and example updates without re-investigation.
- The issue drafts are self-contained: a CECE maintainer can reproduce
  with only the report in hand — including the error-clarity draft's
  mis-ordered-file repro for the backpressure misattribution.
- CECE and runner working trees end the spike with no new modifications
  (`Dockerfile` explicitly included).
- No ESMF was built or required at any point (`ENABLE_ESMF=OFF`
  throughout; the rebuilt driver's `ldd` shows no ESMF, matching the
  shipped binary).

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
- when using python `typing`, avoid `Any` as much as possible
- **never, ever, ever** commit code - the user always commits

### testing

- *all* suites should pass `--dry-run`
- run `simple-maccity-suite.yaml` without `--dry-run` for integration testing with the driver
- only run examples when requested to do so
- no need to run tests for spikes/documentation-only tasks
- pre-commit hooks pass

## requirements

- investigate the amio threads failure in design/fix/20260720-1500-fix-cece-examples.md
- switch to 2 or more amio threads and generate a report in design/artifacts
- i want to create an issue in the cece repo to investigate the issue
- is it related to the docker execution environment? are dependencies missing? installed correctly?
- a basic multi-threaded amio example in the container is ideal as we'll want to test this regularly for all examples at small thread counts (max 8 probably)
- this is a spike, no code changes expected
- (follow-up) it's okay to do evaluation: install gdb and the correct hdf in the image to allow multiple amio threads — we don't want serial versions of our core io libraries
- (follow-up) rebuild the image as necessary, but do not build esmf
- (follow-up) track changes for an eventual implementation to update examples to allow multiple amio threads
- (follow-up) no changes to the dockerfile yet
- (follow-up) for this spike we DO NOT WANT TO BUILD ESMF. no esmf included. assume it is not built
- (follow-up) the build_esmf flag changes are in https://github.com/ufs-community/CECE/pull/59 — assume we're working with that codebase
- (follow-up) the PR will be merged before doing the spike
