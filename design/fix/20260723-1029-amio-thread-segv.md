# Fix: ex7 passes with `amio_worker_threads: 2`

## Goal

Make `examples/config/cece_config_ex7.yaml` run green with
`amio_worker_threads: 2` — fixing the underlying AMIO threading crash
rather than pinning around it. This operationalizes the investigation
designed in `design/spike/20260720-1654-investigate-amio-threads-failure.md`
(background, hypotheses, and prior evidence live there), with one major
scope change from the spike: **fixing code is now in scope** — the
requirements name both suspects, "the installed libraries or the cece
amio code itself".

## Background (from the spike + session evidence)

- Failure signatures observed so far (they *morph across builds* — a
  classic race): pre-merge driver, threads=2 → SIGSEGV during stream
  ingest; post-merge driver, a minimal 1-stream config completed its work
  then hit `double free or corruption` → SIGABRT and **hung** in the
  abort path; ex2 shows an intermittent ingest segfault at *default*
  thread count.
- **H1 (environment)**: the driver links `libnetcdf_mpi` → the *openmpi*
  HDF5 flavor, which has `H5_HAVE_PARALLEL` but **no thread safety**;
  Ubuntu's *serial* HDF5 (installed in the same image) **is**
  thread-safe (`H5_HAVE_THREADSAFE 1`). Concurrent AMIO reads through a
  non-thread-safe HDF5 is the textbook cause.
- **H2 (AMIO code)**: a race in the vendored AMIO itself
  (`extern/helm/libs/amio/`, with `staging/staging_pool.cpp`,
  `workers/worker_pool.cpp`, `workers/exception_bridge.cpp`,
  `prefetch/`, and a unit-test suite ready for a regression test).
- ESMF is irrelevant and must never build: `BUILD_ESMF=OFF` (Dockerfile
  default) and `ENABLE_ESMF=OFF` (CMake default); the standalone driver
  links zero ESMF libraries.

## Pre-design audit (fresh facts)

1. **The linkage mechanism behind H1 is CMake package discovery, not an
   explicit choice**: `find_package(netCDF CONFIG)` with a plain
   `netcdf` fallback — the driver links whatever the installed `-dev`
   package's CMake config points at, and the `Dockerfile` installs
   `libnetcdf-mpi-dev`. The H1 fix is therefore a **package/discovery
   swap** (serial `libnetcdf-dev` → thread-safe serial HDF5), landing in
   the `Dockerfile` and possibly a CMake hint — no HDF5 source build.
2. **Container rebuilds**: `setup.sh` wraps `docker buildx build` and
   accepts the ESMF arg; rebuilds must use
   `--builder cloud-bkrlps-cece-builder` (per requirements; verify
   setup.sh passes the flag through or add the builder via buildx
   config), always with `BUILD_ESMF=OFF`.
3. **Current state**: the full examples gate is 7/7 green with ex7
   pinned to `amio_worker_threads: 1` and a KNOWN DRIVER BUG comment;
   ex7's data (HTAP sectors + local CAMS copies) is cached in `data/`.
   gdb is not in the image.
4. **Debug tooling**: a debug-symbol build is a scratch in-container
   build (`-DCMAKE_BUILD_TYPE=RelWithDebInfo`, out-of-repo build dir);
   gdb runs need `--cap-add=SYS_PTRACE --security-opt
   seccomp=unconfined` on the container.

## Design — phased, red first

**Red state (TDD)**: flip ex7 to `amio_worker_threads: 2` (and drop the
KNOWN DRIVER BUG comment) *first*. The examples gate goes red on ex7;
green is the fix's acceptance signal. Because the bug is a race, "green"
is defined statistically: **10/10 consecutive ex7 passes**, not one
lucky run.

**Phase 0 — characterize on the current build** (cheap, sharpens the
choice between H1/H2): minimal 1-stream scratch config + ex7, threads
{2, 4, 8}, ≥5 reruns each under explicit timeouts, outcomes classified
pass / segfault / abort / hang. Capture a symbolized backtrace: install
gdb ad hoc in an ephemeral container (or bake it into the rebuild in
Phase 1 — it is part of the eventual image either way), debug-symbol
scratch build, `gdb -batch -ex run -ex 'thread apply all bt'`. Frames in
`H5*`/`nc_*` on two AMIO threads ⇒ H1; frames in AMIO's own
pool/queue/shutdown code with HDF5 uninvolved ⇒ H2.

**Phase 1 — environment fix (H1 path)**: switch the driver's netCDF to
the serial, thread-safe stack — `Dockerfile` swaps
`libnetcdf-mpi-dev` → `libnetcdf-dev` (plus gdb, per the spike's change
inventory), CMake hint if discovery needs steering; rebuild the image
via `setup.sh` with `--builder cloud-bkrlps-cece-builder` and
`BUILD_ESMF=OFF`; rebuild the driver; rerun the Phase 0 matrix. All
green ⇒ done via environment. (Contingency noted in the spike: if the
driver genuinely uses parallel-netCDF calls, the swap breaks the build —
then H2/serialization is the only path.)

**Phase 2 — AMIO code fix (H2 path, if the crash survives Phase 1)**:
guided by the backtraces, fix the race in the vendored AMIO
(`staging_pool` buffer lifecycle, `worker_pool` shutdown ordering, or a
serialization mutex around backend calls if HDF5 must stay
non-thread-safe anywhere). A regression test lands in
`extern/helm/libs/amio/tests/unit/` (the suite runs in ctest), red
before the fix where feasible for a race (loop-until-flake harness with
a bounded iteration count).

**Config/doc flips on green**: ex7 keeps `amio_worker_threads: 2`; the
KNOWN DRIVER BUG comment goes; the examples README's two known-issue
notes (threads >= 2, and the ex2 flake **if** the same fix resolves it —
verified by 10 ex2 reruns) are updated or removed; the spike doc gets an
"absorbed by this fix" banner, and its planned CECE issue drafts become
moot where the fix lands in-repo (anything upstream-worthy — e.g. the
error-clarity findings — stays noted in the spike for separate filing).

## Verification

- Red first: ex7@2 fails before any fix (recorded signature).
- Green: ex7@2 **10/10**; minimal-repro matrix {2,4,8} clean (≥5 each);
  ex2 rerun ×10 (flake status honestly reported either way); full
  `--run-examples` gate 7/7; CECE ctest green (AMIO unit tests +
  regression test if Phase 2); runner harness/dry-run/integration and
  pre-commit green (runner is expected untouched — the gate picks up
  the config flip automatically).
- Every phase's findings land in this doc's implementation notes as
  they happen (standing rule), including the H1-vs-H2 verdict and
  backtraces.

## Constraints

- **Never build ESMF** (`BUILD_ESMF=OFF`, `ENABLE_ESMF=OFF` throughout).
- Container rebuilds only via `setup.sh` with
  `--builder cloud-bkrlps-cece-builder`.
- Scratch configs stay outside the discovery glob and are removed;
  scratch build dirs live out-of-repo.
- Never commit — the user commits.

## Acceptance criteria

- `examples/config/cece_config_ex7.yaml` has `amio_worker_threads: 2`
  and no known-bug comment; the gate is 7/7 with ex7@2 stable (10/10).
- The root cause is stated with evidence (backtrace + which hypothesis
  held), and the fix is minimal for that cause (package/CMake swap for
  H1; targeted AMIO change + regression test for H2).
- No ESMF was built; image rebuilds used the cloud builder; both
  working trees left uncommitted.

## Implementation notes

**Outcome: fixed — ex7 runs `amio_worker_threads: 2`, clean 10/10; gate
7/7; ex2's default-thread flake is gone (10/10).**

- **Red**: ex7@2 on the pre-fix driver: 3/3 SIGSEGV, crash point moving
  between runs (8th vs 4th stream) — race confirmed.
- **Phase 0 verdict — initially read as "serialization is
  insufficient", later CORRECTED (see the lock-only experiment below)**:
  the symbolized gdb backtrace (RelWithDebInfo scratch build, ad-hoc
  gdb) showed the crashing worker **holding** `g_nc_driver_mutex`
  inside `read() → nc_get_vara → H5Dread → H5CX_get_data_transform`
  while the other worker waited on the mutex. That was misread as
  proof that even serialized cross-thread use corrupts non-thread-safe
  HDF5 — in fact the corruption was **seeded earlier in the run by the
  unlocked `describe_variable`** (caller-thread `nc_inq_*` during every
  read resolution, racing concurrent worker reads) and merely
  manifested later under the lock. Memory corruption is delayed-action;
  a crash-time backtrace shows where it fired, not where it began.
- **Discovery mechanism surprise**: `netCDF_DIR` resolved to
  `cmake/netCDF_mpi` — Debian ships two CONFIG packages both answering
  to `find_package(netCDF)`, and unhinted discovery picked the mpi one
  (whose `netCDF::netcdf` points at `libnetcdf_mpi` → openmpi
  non-threadsafe HDF5). AMIO *additionally* force-links `netcdf_mpi`
  for `nc_*_par` — which the standalone path never executes
  (`use_parallel_` requires comm size > 1; the facade forces
  `MPI_COMM_SELF`).
- **The fix (no image rebuild needed — deviation from the design's
  Phase 1)**: serial netCDF + thread-safe serial HDF5 were already in
  the image, so the whole fix is build-level + guards:
  - AMIO `CMakeLists.txt`: new `AMIO_NETCDF_SERIAL` option (default OFF,
    upstream-neutral) — skips the `netcdf_mpi` find (clearing stale
    cache), hints `find_package(netCDF)` at the serial CONFIG dir, and
    defines `AMIO_HAS_PARALLEL_NETCDF` only when the parallel lib links.
  - `netcdf_driver.{hpp,cpp}`: `netcdf_par.h` include and all four
    `nc_*_par` call sites compile-guarded; multi-rank writes without
    parallel support fail with a **named cause**; multi-rank reads warn
    and degrade to per-rank serial opens; `verify_parallel_support()`
    returns early for serial builds (its R7.1 gate had rejected the
    serial link outright — surfaced as a cryptic
    `AMIO_ERR_BACKEND_FAILURE`, another error-clarity data point);
    `describe_variable()` now takes the driver mutex like every other
    entry point (it issued unlocked `nc_inq_*` on the shared handle).
  - CECE `CMakeLists.txt`: `AMIO_NETCDF_SERIAL=ON` (cache, with
    rationale comment) before the AMIO subdirectory.
  - Both the scratch and canonical `build/` trees verified via `ldd`:
    `libnetcdf.so.19 → libhdf5_serial.so.103`.
- **Verification**: clean ex7@2 **10/10**; threads {4, 8} 5/5 each; ex2
  default-threads **10/10** (one fix killed both bugs); full
  `--run-examples` gate **7/7**; `simple-maccity-suite.yaml` integration
  **18/18** on the relinked driver. (One verification hiccup recorded
  honestly: an early gate run collided with the still-running matrix —
  both writing the checkout's `cece_output/` — producing 3 contaminated
  aborts; the clean rerun isolated it.)
- **Deviations from the design**: no container/image rebuild and no
  Dockerfile change were needed (the cloud-builder requirement never
  activated). An AMIO-internal threaded regression test was *initially*
  skipped (the fix looked configuration-level and ex7@2 in the examples
  gate already exercised it) — **superseded**: after the revert to the
  one-line lock, `unit.netcdf_describe_thread_safety` was added and
  verified fail-before/pass-after (see conversational updates).
- Spike doc banner updated (absorbed by this fix); its error-clarity
  findings remain live for upstream filing, now including the R7.1
  serial-rejection case.

**Lock-only experiment (2026-07-23, post-fix)** — answering "were the
CMake/ifdef changes necessary, or would the lock suffice?": rebuilding
with `AMIO_NETCDF_SERIAL=OFF` (restoring the `netcdf_mpi` /
non-thread-safe openmpi HDF5 linkage, keeping only the unconditional
`describe_variable` lock) runs ex7@2 **10/10 green**. Conclusion: **the
one-line lock is the sufficient minimal fix** for the observed crashes;
the serial-netCDF machinery is *defense-in-depth*, not a requirement —
its remaining justification is vendor support (the HDF Group does not
guarantee non-thread-safe HDF5 under any multithreaded use, even fully
serialized) and it costs CECE nothing since the standalone path never
uses parallel I/O. Upstream implication: the AMIO PR's essential change
is the `describe_variable` lock (completing the mutex discipline their
own recent commit introduced); `AMIO_NETCDF_SERIAL` can be offered as an
optional hardening knob.

**Final state (per user direction — see conversational updates)**: the
serial-netCDF option and all compile guards were **reverted**; the
shipped change is the `describe_variable` `lock_guard` with an
explanatory comment, in `extern/helm/libs/amio` only. `netcdf_mpi`
linkage restored (verified via `ldd` after a cache-cleared reconfigure —
note the `netCDF_DIR` cache entry must be cleared when flipping
configurations, or CECE core and AMIO can end up linking *different*
netCDF flavors whose duplicate `nc_*` symbols collide). Re-verified on
the canonical build: ex7@2 **10/10**, ex2 **5/5**, full
`--run-examples` gate **7/7**, `simple-maccity-suite.yaml` integration
**18/18**. Final CECE diff: `examples/config/cece_config_ex7.yaml`
(threads: 2, bug comment removed), `examples/README.md` (historical
note), and the one-line-plus-comment lock in
`extern/helm/libs/amio/src/drivers/netcdf/netcdf_driver.cpp`.

---

# appendix: original notes

## always do

- include updating design.md as part of the implementation
- update the harness tests (`src/tests/ufs_chem_assay`) in addition to any changes to test_driver_combos.py
- update README.md with any necessary documentation changes in case of an api adjustment
- use pydantic models as opposed to dataclasses
  - all pydantic fields should include a description like `... = Field(description="<description content here>", ...`
- do *not* add driver bugs to known bugs in `README.md` unless explicitly told to do so
- use a test-driven development, red-green-refactor approach for all fixes and features (when possible)
- maintain original design sections when refining design docs - create an appendix
  - summarize conversational updates in the appendix following original refinement target
- when using python `typing`, avoid `Any` as much as possible
- **never, ever, ever** commit code - the user always commits

### testing

- not necessary for design documents in the `spike` folder - code *should not* change for spikes
- *all* suites should pass `--dry-run`
- run `simple-maccity-suite.yaml` without `--dry-run` for integration testing with the driver
- only run examples when requested to do so
- no need to run tests for spikes/documentation-only tasks
- pre-commit hooks pass

## requirements

- examples/config/cece_config_ex7.yaml should pass with amio_worker_threads: 2
- reference design/spike/20260720-1654-investigate-amio-threads-failure.md for some additional background info
- it is okay to rebuild the dev container from setup.sh (never build ESMF)
- could consider building the CECE stack with debug symbols and running through gdb
- for any container rebuilds, use the `--builder cloud-bkrlps-cece-builder` - should be the default but just to make sure
- issue could be in the installed libraries or the cece amio code itself

## conversational updates

- 2026-07-23: **regression unit test added** —
  `tests/unit/test_netcdf_describe_thread_safety.cpp` in the AMIO
  submodule, registered as ctest `unit.netcdf_describe_thread_safety`.
  One thread hammers full driver reads while two threads hammer
  `describe_variable()` on the same open driver (real netCDF file, no
  eckit dependency — it uses `conf::Config::from_string`, so it builds
  where `unit.read_netcdf4` cannot). **Fail-before/pass-after verified
  empirically**: with the lock temporarily removed, 3/3 segfaults
  (exit 139); with the lock, 3/3 pass (~0.2 s) and ctest discovers it.
  Environment caveat documented in the test: provoking the crash
  requires a non-thread-safe HDF5 (the dev-container default); on a
  thread-safe HDF5 the pre-fix code may pass — the lock is still
  required, the crash is just the strongest observable. Enabling the
  suite surfaced three pre-existing build gaps, fixed alongside:
  `tests/unit/CMakeLists.txt` used `${CMAKE_SOURCE_DIR}` paths that
  break in superbuilds (now an explicit `AMIO_ROOT`); the test stubs
  the zarr/grib2 registrar symbols and defines `g_amio_parent_comm`
  (normally from `amio_core`, not compiled into driver-level tests);
  and superbuild configuration needs
  `-DCMAKE_DISABLE_FIND_PACKAGE_rapidcheck=ON` (+Catch2) because the
  PBT deps' configs clash with superbuild-defined targets.
- 2026-07-23: **AMIO PR-checklist suite verification** (`ctest -L unit`,
  `-L pbt`, header isolation), run both in the CECE superbuild and in a
  standalone AMIO build (ephemeral container: apt Catch2 3.4.0,
  Kokkos/yaml-cpp installed from the superbuild's `_deps`, HELM
  conf/halo/logs built+installed):
  - **header_isolation: 3/3 pass** (C99 isolation, C++ inclusion,
    symbol mangling); `amio_spack_no_rust_cargo_go` **skips** wherever
    `spack` is not on PATH (by design — CI hard-gates it with
    `-DAMIO_REQUIRE_SPACK_CHECK=ON`).
  - **unit: every test that builds passes — 17/24** (incl. the new
    `unit.netcdf_describe_thread_safety`). The same 7 targets are "Not
    Run" in *both* contexts, i.e. **broken upstream on `develop`**,
    unrelated to this fix: since `ceb4aad` `backend_factory.cpp`
    force-calls `amio_register_{netcdf,zarr,grib2}_driver()`, but
    `test_write_snapshot` + the `test_read_*` family compile it with no
    driver sources/stubs (undefined references), and
    `test_backend_factory` lacks the `HELM::CONF` include path (only
    target missing from the `_amio_worker_test_targets` fixup list).
  - **pbt: cannot build in the superbuild at all** — CECE FetchContents
    RapidCheck (defines `rapidcheck`/`rapidcheck_gtest` in-tree), so
    `/usr/local`'s RapidCheck package export aborts ("some but not all
    targets defined"), and the cece-dev image has no Catch2 (AMIO's own
    dev container ships `/opt/catch2` + `/opt/rapidcheck`). Standalone:
    **every PBT test that builds passes (10 ran, 0 failed)**; 26 of 36
    registered are "Not Run" from two upstream target-config gaps —
    targets compiling `amio_api.cpp` miss the MPI C++-bindings library
    (undefined `MPI::Win::Free` etc.; need `MPI::MPI_CXX` or
    `OMPI_SKIP_MPICXX`), and grib2 PBT targets don't link `HELM::CONF`
    (undefined `conf::Config::*`).
  - Net for the PR checklist: the one-line lock cannot affect these
    build gaps (they predate it and live in test target wiring); all
    suites that build are green, and the new regression test is
    fail-before/pass-after verified.
- 2026-07-23: **reverted to the one-line fix.** After the lock-only
  experiment proved the `describe_variable` lock sufficient (10/10), the
  user directed dropping the `AMIO_NETCDF_SERIAL` CMake option, its
  compile guards, and CECE's serial default — the shipped fix is the
  lock (plus its explanatory comment) alone, restoring the stock
  `netcdf_mpi` linkage and keeping parallel I/O intact. The serial
  option remains documented here and in the spike as an available
  defense-in-depth pattern should it ever be wanted.
