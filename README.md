# ufs-chem-assay

The UFS-Chem testing, verification, and benchmarking harness.

The harness tests **specific configurations of an application**: a suite
file names a base driver configuration, the inputs it reads, and how it is
swept, asserted, and analysed. Combinations of enum-valued driver options
(declared in a suite file, e.g.
`src/tests/config/cece/maccity/simple-maccity-suite.yaml`) are rendered to
YAML configs and each runs in its own Docker container, followed by
per-combo assertions on the output (exit code, file counts/names,
attributes) and a statistics/plotting analysis step. Everything that knows
the application under test — its driver config, sweep schema, image,
checkout, data directory — lives in an application adapter; CECE (`application: cece`,
the default `SuiteConfig` application) and MONETIO (`application: monetio`)
are the shipped adapters, and a session runs one application. Configurations the
harness carries live under `src/tests/config/<application>/<configuration>/`, each
beside the suites that sweep it: `cece/maccity/` (a 4x5 CO configuration
over one public input; the default suite and the CI integration run),
`cece/gcafs-anthro/` (the global anthropogenic use case — 0.25°, 25 hourly
steps, 32 streams over seven placed inputs; Ursa only, see
[docs/gcafs-anthro-runbook.md](docs/gcafs-anthro-runbook.md)), and
`monetio/aeronet/` (a native Python CLI configuration reading AERONET
direct-sun AOD and inversion datasets into CF-1.8 / UGRID-1.0 `(time, node)`
NetCDF files). Design rationale lives in [design/design.md](design/design.md).

## Prerequisites

- A local checkout of the application under test (this harness lives in
  its own repository; the application checkout is external). For CECE,
  the shipped adapter:
  - Docker and the `cece/cece-dev` image available locally (build it via
    `./setup.sh` in the checkout) — or, on a machine without docker such
    as Ursa, the target driver built natively against the checkout's
    modulefiles (see [Running on RDHPC](#running-on-rdhpc-ursa));
  - the target driver built at `./build/cece_standalone_driver` (relative
    to the checkout root);
  - the inputs of the suites you run, staged into the checkout's `data/`
    with `uv run ufs-chem-assay fetch` (below) — a session with a declared
    input missing fails at collection and names that command.
- [uv](https://docs.astral.sh/uv/) installed.
- The [AWS CLI v2](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html)
  on `PATH` for anything that touches S3: `ufs-chem-assay fetch` (public
  buckets are read anonymously with `--no-sign-request` — no AWS account
  needed; credentials only for an input declared `public: false`) and the
  `data_integration` tests (see [S3 data sync](#s3-data-sync)). A laptop
  installs it with its package manager, the Ursa runbook installs it
  user-locally, and the toolchain image (`Dockerfile`) carries it.

## Setup

```sh
uv sync                       # includes dev tools (mypy, pre-commit, stubs) and
                              #   installs the `ufs-chem-assay` command (editable);
                              #   Python 3.14, the .python-version pin
uv run pre-commit install     # ruff check/format, mypy, vulture, codespell, the lock check
                              #   on every commit, plus
                              #   the conventional-commit message gate (the
                              #   commit-msg hook type installs automatically)

# per-machine configuration lives in a .env file at the repo root
# (gitignored; read when running pytest from the repo root):
cat > .env <<'EOF'
cece_root_dir=/path/to/CECE                    # the CECE adapter's checkout
assay_baseline_root_dir=/path/to/cece-baselines  # a harness-wide setting
EOF
```

Real environment variables override `.env` values. Harness-wide settings
use the `ASSAY_` prefix; an application's own settings keep its prefix (`CECE_ROOT_DIR`,
`CECE_MODULEFILE`, `MONETIO_ROOT_DIR`, ...). There is no command-line flag for the checkout
root: the variable, `.env`, or the run config (below) supplies it.

## Running

```sh
uv run pytest                      # everything: integration + runner harness tests
uv run pytest --application=cece   # only this application's suites (the default suite when
                                   #   no --suite-config is given); a session runs one application
ASSAY_RUNTIME=native uv run pytest --application=monetio  # runs monetio's default aeronet-suite.yaml natively
uv run pytest -vs                  # show each driver's output as it runs
uv run pytest -x                   # fail fast: stop at the first failure
uv run pytest -k map-consd         # run a subset by combo name
uv run pytest --combo-clean-root   # delete an existing output root first

uv run pytest src/tests/ufs_chem_assay                  # harness only: fast, no docker
uv run pytest src/tests/ufs_chem_assay --cov            # ... with coverage: CI fails under 100%
uv run pytest src/tests/ufs_chem_assay/applications/cece  # the CECE adapter's tests only
uv run pytest src/tests/test_driver_combos.py --application=cece  # integration only (real docker)

uv run mypy                        # type checking (all of src/; zero errors expected)
uv run pre-commit run --all-files  # everything the commit hook runs: ruff check/format,
                                   #   mypy (strict), vulture (dead code), codespell,
                                   #   uv lock --check, yamlfix/yamllint, whitespace,
                                   #   merge markers, private keys, large files

# everything except driver execution (no docker needed); all combo tests skip.
# --application picks the adapter and runs its default suite (several adapters are
# registered, so a command naming neither an application nor a suite is a usage error)
uv run pytest src/tests/test_driver_combos.py --dry-run --application=cece

# stage the inputs the selected suites declare (inputs:) into <CECE_ROOT_DIR>/data —
# a present file with the declared sha256 is skipped without touching the network —
# and the baselines they pin into ASSAY_BASELINE_ROOT_DIR (unless comparisons are off);
# --dry-run lists what is present and what would be fetched, and moves nothing
uv run ufs-chem-assay fetch --suite-config=simple-maccity-suite.yaml --dry-run
uv run ufs-chem-assay fetch --suite-config=simple-maccity-suite.yaml

# the exhaustive run-only suite: every enum value on every driver-meaningful
# dimension, the inert category label pinned to "undefined" (240 combinations,
# on demand only — dry-run it first; the real run takes ~25-30 min).
# Suites are selected by name (see --suite-config below):
uv run pytest src/tests/test_driver_combos.py --dry-run \
  --suite-config=exhaustive-maccity-run-only-suite.yaml
```

To rebuild the driver and run the CECE C++ tests in the container (this
suite is separate — run it with `uv run pytest` as above), from any
directory:

```sh
$CECE_ROOT_DIR/scripts/build-and-test-container.py            # build + C++ tests
$CECE_ROOT_DIR/scripts/build-and-test-container.py --clean    # wipe build dirs first
$CECE_ROOT_DIR/scripts/build-and-test-container.py --test-filter Configured  # gtest subset
# --no-build / --no-test skip a phase; --mount and --image override defaults
```

Driver output is printed after every driver call: with `-vs` (or `-s`) it
appears in the terminal as the suite runs; without `-s`, passing tests stay
quiet and failing tests include the output in their report under
"Captured stdout call".

Each combination produces one test per assertion/analysis step —
`test_driver_execution` (driver exits 0), `test_nc_file_count` (expected
NetCDF output count; `validate_file_count: false` skips it),
`test_nc_filenames` (filenames match
`filename_pattern` at the expected write times), `test_nc_variable_dimensions`
(every configured output variable carries the standard
`(time, lev, lat, lon)` dimensions in every NetCDF — a synthetic dimension
like `nox_dim2` where `lat` belongs means the writer failed to associate
the coordinate; `validate_dimensions: false` skips it), `test_species_attributes`
(the species variable's full attribute dictionary, one test per combo ×
configured species; `exact: true` — the default — requires the dictionaries
to match exactly, `exact: false` checks the expectation as a subset; per
value, `null` asserts absence and `"__ignore__"` allows any value),
`test_descriptive_stats` (per-NetCDF statistics via distributed dask for
every spatial field — a data variable carrying both `lat` and `lon`; CF
cell bounds and other auxiliary tables are skipped — written to
`<combo_id>-stats.csv`; all combos concatenated into
`descriptive_stats.csv` at the output root when the session ends), and
`test_baseline_comparison` (nccmp-style comparison against a per-combination
baseline: each `baseline_comparisons` entry carries a `sweep_selector` —
mirroring the sweep structure with regexes at the leaves — that must select
exactly one combination, a baseline `ulid` under `ASSAY_BASELINE_ROOT_DIR`,
optional per-entry tolerances `atol` and `rtol`, and a `plot` switch for
bias plots; the combination's generated driver configuration must equal the one the
baseline directory carries (run paths aside) — a changed configuration
needs a new baseline even when the output did not change, and a baseline
published without its configuration cannot pass; structure and attributes
exact — except the values of the global attributes the application stamps
per run (for CECE `date_created`, `date_modified`,
`date_metadata_modified`, `history`), which must be present but are not
compared — data bit-for-bit, or within `atol + rtol × |baseline|` (numpy
style; `rtol: 1.0e-15` admits a few float64 ULPs at any magnitude, which is
what the checked-in `simple-maccity` entries pin: its baselines come from
Ursa's Intel build and other builds differ from it by one ULP); RMSE and
difference statistics recorded per file x variable in
`<combo_id>-stats-comparison.csv`, concatenated to `stats-comparison.csv`
at the root; bias maps + GIF render at session end into `plots-baselines/`
on a suite-wide symmetric color scale; unselected combinations skip) — with the
driver running once per combination. If the driver run fails, its
`test_driver_execution` fails and that combination's assertion tests are
skipped with a `driver run failed: ...` reason.

Options:

- `--application=NAME` — run only suites of this application (registry
  name: `cece`, `monetio` — the applications with a driver the harness
  sweeps), overriding `ASSAY_APPLICATION`. Without it the
  application is inferred from the selected suites, which must all agree
  (a mixed selection is a usage error naming the flag); with it, suites
  of other applications are dropped from the selection with a log line.
  A bare `--application=cece` with no `--suite-config` runs that
  adapter's default suite (`simple-maccity-suite.yaml`).
- `--suite-config=SELECTOR` — selects the suites to run. The selector
  is a regex fullmatched against each discovered suite's file name or its
  search-root-relative path; candidates are every `*-suite.yaml` found
  recursively under the `ASSAY_SUITE_CONFIG_SEARCH_PATH` directories plus
  the built-in `src/tests/config/` tree (always searched last). Only files
  ending in `-suite.yaml` are suites — a configuration's own YAML files sit
  beside the suites that sweep them (`src/tests/config/cece/maccity/` holds
  `maccity.yaml` and its three suites) and are never candidates. A literal
  filename is its own selector (`--suite-config=exhaustive-maccity-run-only-suite.yaml`);
  an existing file path is used verbatim. **Every match runs**: one match
  is the classic single-suite session; several matches run as one
  multi-suite session (e.g. `--suite-config='exhaustive-maccity-.*-suite.yaml'`
  runs both exhaustive suites, `--suite-config='cece/maccity/.*'` every
  suite of the maccity configuration) over a single flat output root, each
  combo under its own suite's timeout, assertions, and plotting switches,
  with test ids suite-qualified (`exhaustive-maccity-run-only/…`) so `-k`
  selects per suite. There is no
  guard on broad selectors — a regex matching everything runs everything.
  Zero matches fail immediately with a listing; duplicate suite *names*
  among the matches fail at session start. Default: the application's
  default suite (`simple-maccity-suite.yaml` for cece) — a bare `uv run
  pytest` always runs exactly that one suite, however many suites exist.
  Use the `--suite-config=...` form (with `=`), not a space.

  The suite YAML defines the suite's unique `name` (lowercase slug; suite
  `X` lives in `X-suite.yaml` — the rule discovery relies on), its
  `application` (optional, default `cece`; selects the sweep schema and
  the driver config model), the base driver configuration (`config_path`:
  a file, or a directory for an application whose configuration spans
  several files; relative to the suite file, so a suite beside its
  configuration names it with a bare file name), the inputs it reads
  (`inputs:` — a list of `{url, public, dst, sha256}` entries: an
  `s3://bucket/key` object, whether the bucket allows anonymous reads,
  the destination relative to the checkout's `data/`, and the expected
  digest; `ufs-chem-assay fetch` stages them, verifying the digest before
  a download replaces anything. `url: null` declares an input with no
  remote source yet, verified by its required `sha256` and never
  downloaded: absent, `fetch` links it into place from the entry's
  `sources:` path for the session's platform (`{ursa: /abs/path}` —
  where the file is known to live; a missing declared file is a named
  failure) or, without one, from the one file of that name found under
  `ASSAY_INPUT_SEARCH_PATH` (the run config's `data.input_search_path`:
  this machine's directories; none or several matches fail, naming the
  path to place it at), and
  `fetch --record-digests` writes the on-disk `sha256` of every such input
  whose digest differs into the suite file — review the diff and commit,
  as with baseline ULIDs), the per-combination timeout
  (`timeout_s`), and the sweep — which, for CECE,
  mirrors the driver-config structure, attaching swept
  values to named streams (or positional species entries). A sweep value
  may be a regex string instead of a list — `mapalgo: ".*"` expands
  (fullmatch, against the enum's values) to every value at load time,
  including values added after the suite was written; `run.yaml` records
  the expanded list. The checked-in `exhaustive-maccity-run-only-suite.yaml`
  sweeps `".*"` on every dimension.

  `sweep:` is optional: absent (or attaching no dimensions) the suite runs
  its base config as the single combination named `base`. A `config_path`
  starting with the literal `${CECE_ROOT_DIR}` token — the application's
  own root variable — anchors on the CECE checkout, the portable way for a
  suite to reference a configuration that lives in the checkout rather
  than here; using such a suite without a configured root fails
  immediately with the standard root-dir message.
- `--dry-run` — everything except driver execution: the suite loads, combos
  enumerate, `run.yaml`/`combos.csv`/every generated driver config/
  `test-report.csv` are all written, and every combo test skips. No docker
  required — use it to validate a suite (notably the exhaustive one) before
  paying for containers. With the default output root it also needs no
  `CECE_ROOT_DIR`.
- `--combo-output-root=PATH` — root artifact directory; relative paths
  resolve against the application checkout (`CECE_ROOT_DIR`, mounted at
  `/work` under docker), so results persist there. Under docker an
  absolute path must lie under the checkout mount; natively any absolute
  host path works. Default: a pytest-managed temporary directory (nothing
  is written to the checkout). The checkout root is required to execute
  the driver and to resolve an explicit output root; a missing or
  nonexistent path fails immediately, before any test runs.
- `--combo-clean-root` — with an explicit `--combo-output-root`, remove an
  existing output root before running. Without it, an existing root is an
  error — prior results are never mixed with a new run. Only a previous
  harness root (one with `run.yaml` at its top) is ever removed; any other
  existing directory is refused, since an absolute root under the native
  or slurm runtime can point anywhere.
- `--publish-baselines` — at session end, publish every compared
  combination (one per `baseline_comparisons` entry) whose driver run
  passed, and no test but the old comparison failed, as a new baseline
  under `ASSAY_BASELINE_STORE`, and repoint the suite files at the new
  ULIDs (see [Publishing baselines](#publishing-baselines)). Needs the AWS
  CLI and credentials it can find. `--no-suite-update` publishes without
  touching the suite files; it is an error without `--publish-baselines`.
- `--baseline-atol=<float>` / `--baseline-rtol=<float>` — data tolerances
  applied to every baseline comparison of the session in place of the
  entries' own `atol` / `rtol` (0 = bit-for-bit for that part), over
  `ASSAY_BASELINE_ATOL` / `ASSAY_BASELINE_RTOL`; the override is logged
  once at session start and never edits a suite file.
- Inputs are never downloaded by the session. When driver execution is
  coming (no `--dry-run`) and a selected suite's declared input is absent
  from the checkout's `data/`, collection fails with a usage error naming
  the missing files and the `ufs-chem-assay fetch --suite-config=…`
  command that stages them. `run.yaml` records every declared input as
  found at session start (path, source, declared `sha256`, size or null).
- Baselines, unlike inputs, **are** synced by the session: at session
  start every ULID the selected suites pin that is absent from
  `ASSAY_BASELINE_ROOT_DIR` is fetched from `ASSAY_BASELINE_STORE` into
  `<root>/<ulid>/` (one reentrant `aws s3 sync` per baseline, or a copy
  from a directory store); present ones are used as is with no network.
  A missing baseline that cannot be synced (no network, not in the
  store, no baseline root configured) fails that comparison test with
  the reason — the session itself runs. `ufs-chem-assay fetch` stages the
  same baselines too, the pre-warm for a session that will run without
  network. `--dry-run` classifies without syncing. `run.yaml` records
  every pinned baseline's state (`local`, `synced`, `would-sync`,
  `failed`).

## S3 data sync

`src/s3_sync.py` is a stand-alone wrapper around `aws s3 sync`: one
`S3SyncConfig` in (source, destination, `dry_run`, `delete`, filters,
`no_sign_request`, `profile`, `timeout_s`), exactly one `aws s3 sync`
subprocess call, one `S3SyncResult` out. Sync is the primitive because it
is reentrant — an interrupted transfer resumes by re-running the same
config, unchanged files are skipped. `dry_run=True` passes `--dryrun`
straight through: the CLI's `(dryrun) upload:`/`download:`/`delete:` lines
*are* the plan, returned in the result and logged at INFO.
`no_sign_request=True` passes `--no-sign-request`: anonymous reads of a
publicly readable bucket, no credentials involved (refused together with
`profile`). `ufs-chem-assay fetch` is its first caller: one object is
fetched by syncing its parent prefix with `exclude=['*']` and
`include=[<file>]` into a staging directory, verified, then moved into
place.

Credentials are the **AWS CLI's business, not the harness's**: the
wrapper passes nothing and knows nothing, and the CLI resolves its own
chain — `~/.aws/credentials` and `~/.aws/config` profiles (`aws configure`,
`aws sso login`), environment variables, instance roles. Configure a region
there too. `profile=` on the config is a pass-through of `--profile`;
otherwise `AWS_PROFILE` or the default profile applies. Nothing AWS-related
is read from `.env`, and no `AWS_*` setting exists in the harness. Missing
credentials surface as the CLI's own `Unable to locate credentials`, raised
as a `CalledProcessError` with the output attached.

The mock tests (`src/tests/ufs_chem_assay/test_s3_sync.py`) run with the
harness suite and need no network and no `aws`. Two live tests are marked
`data_integration`: `test_public_bucket_single_object` fetches the maccity
file anonymously from the public `geos-chem` bucket and checks its digest
(network, no credentials); `test_private_bucket_round_trip` authenticates
to the private test bucket **`arn:aws:s3:::ufs-chem`** (fixed in the test),
uploads a small tree under `ufs-chem-assay-tests/<ULID>/`, downloads it
back, checks the bytes, proves the second upload is a no-op, and empties
exactly that prefix — previewing each transfer with a dry run first. It is
deselected by default and fails (never skips) when the CLI finds no
credentials or `aws` is not on `PATH`:

```sh
# with the CLI configured (aws configure / aws sso login) for the account that owns the bucket;
# a named profile is the CLI's own AWS_PROFILE (the test sets none)
AWS_PROFILE=<profile> uv run pytest -m data_integration src/tests/ufs_chem_assay/test_s3_sync.py -v

# the same inside the toolchain image: mount the CLI's configuration read-only,
# and mask the repo .env (its CECE_ROOT_DIR is a host path the container cannot see)
docker buildx build --load -t ufs-chem-assay:dev .
: > /tmp/empty.env
docker run --rm -v "$PWD":/repo -v /tmp/empty.env:/repo/.env:ro -w /repo \
  -v "$HOME/.aws":/root/.aws:ro -e AWS_PROFILE=<profile> ufs-chem-assay:dev \
  sh -c 'git config --global --add safe.directory /repo && uv sync --frozen \
         && uv run pytest -m data_integration src/tests/ufs_chem_assay/test_s3_sync.py -v'
```

The credentials need `s3:ListBucket` on the bucket and `s3:GetObject`,
`s3:PutObject`, `s3:DeleteObject` under `ufs-chem-assay-tests/`. On Ursa run
it from a login node (compute nodes have no network) after the runbook's
AWS CLI step.

## Publishing baselines

A baseline is one combination's whole output directory — the NetCDF, the
generated config, the captured `.out`, the driver log, the stats CSVs, the
plots and GIFs — published as `s3://ufs-chem/baselines/<ulid>/` with a
`baseline.yaml` manifest beside the files. The generated config in it is
what later comparisons check the configuration against, so the published
directory is the whole claim: this configuration produced this output (application, the application
commit the run was built from, the run and combination, the effective
parameters, every file with its digest, when and by whom it was published,
and the ULID it supersedes). The ULID is the combination's runtime ULID,
minted fresh every run, so the store is append-only: a prefix that already
holds objects is refused, never overwritten, and nothing is ever deleted.

The suite's `baseline_comparisons` entries say what gets published: each
entry selects one combination and pins the ULID the next session compares
against. After a run, publish it and repoint the suite in one command —
dry-run first:

```sh
# the plan: which combinations, why any are skipped, the files each upload would
# carry (the AWS CLI's own "(dryrun) upload:" lines), the manifest; nothing written
AWS_PROFILE=<profile> uv run ufs-chem-assay publish-baselines \
  --output-root="$CECE_ROOT_DIR/ufs-chem-assay-output" --dry-run
AWS_PROFILE=<profile> uv run ufs-chem-assay publish-baselines \
  --output-root="$CECE_ROOT_DIR/ufs-chem-assay-output"
git diff src/tests/config   # the ulid: lines now name the new baselines; commit them
```

Only sound output becomes a baseline: a combination is published when its
`test_driver_execution` passed and no other test of it failed — the
comparison against the *old* baseline excepted, since republishing is
exactly what follows an intentional driver change. Skipped tests (stats
off, a `--dry-run` session) are allowed. Combinations the gate holds back
are reported and left pinned to their previous ULID. `--no-suite-update`
publishes without editing the suite files; `--suite-config` and
`--application` select the suites as for pytest (default: the suites
`run.yaml` records); `--store` overrides `ASSAY_BASELINE_STORE` for one
call. **The store may be a local directory instead of an S3 prefix**
(`--store=/path/to/baselines`, or the same in `ASSAY_BASELINE_STORE` /
`baselines.store`): a Dropbox folder or a scratch area as the store of
record, no AWS involved — the probe is an existence check and the upload a
directory copy; everything else (the manifest, the gate, append-only, the
suite repoint, the resume) is the same. With `ASSAY_BASELINE_ROOT_DIR` set,
each published directory is also copied to `<root>/<ulid>/`, so the very
next local run compares against it; when the store *is* that directory,
one copy serves both. A ULID published to a local store is only
reachable where that directory is, so the checked-in suites keep
pointing at the bucket.
A re-run of the same output root resumes: a directory whose `baseline.yaml`
names the ULID already in the store is not uploaded again, only the suite
is repointed. `pytest --publish-baselines` (the run config's
`harness.publish_baselines: true`) does the same at session end; dry-run
the output root with the subcommand afterwards if you want to see the plan.
Credentials are the AWS CLI's (publishing needs `s3:ListBucket` on the
bucket and `s3:PutObject` under `baselines/`); nothing is read from `.env`.
**Network is checked up front** whenever S3 is involved: one short TCP
connect to the bucket's endpoint (`AWS_ENDPOINT_URL` when set) before any
work — `pytest --publish-baselines` refuses to start on a node without
network (an HPC compute node) rather than run the whole suite and fail at
the end, `publish-baselines` and `fetch` fail in seconds instead of
waiting out the AWS CLI's retries. The run itself is never lost: publish
its output root afterwards from a login or data-transfer node.

## Configuring a run: `ufs-chem-assay run`

A whole run — the application's source and build, its input data, and
the pytest session — is described by **one YAML run config**, and every
setting a run reads has a key in it: the `harness:` section mirrors every
harness-wide setting (`ASSAY_*`) plus the pytest options, and each
application's section under `applications:` mirrors its own settings
(`CECE_*`) plus how to obtain and build it. The shipped templates
(`config/local.yaml` for a laptop, `config/ursa.yaml` for Ursa — whose
`sessions:` map carries `simple-maccity` and the gcafs-anthro use case)
list every key and run as-is from a checkout laid out like the runbook.
A third, `config/ursa-kokkos.yaml`, builds and tests CECE, CATChem and
the Kokkos smoke project against a spack-stack environment with two
toolchains (below; its runbook is
[docs/kokkos-runbook.md](docs/kokkos-runbook.md)). A fourth,
`config/gaea-c7.yaml`, builds and tests CECE on Gaea C7 the same way,
with one toolchain (also below):

```yaml
platform: ursa
applications:            # one section per application in the run
  cece:
    git_url: git@github.com:ufs-community/CECE.git
    ref: develop
    clone_dir:           # null: <root_dir>/CECE (CECE_ROOT_DIR)
    modulefile: cece_ursa.intelllvm
    modulepath: []       # dirs to `module use` first; empty: <clone>/modulefiles
    module_env: {}       # exported before the load (what the modulefile reads)
    toolchain_tools: []  # `which` these after the load; empty: the adapter's probe
    configure_gates: []  # extra grep -E patterns the configure log must match
    build_node: login    # or compute: configure and build behind srun
    tests: {enabled: false}  # build the application's own test stack
    ...
harness:
  suite_config: simple-maccity-suite.yaml
  output_root: ufs-chem-assay-output
  submit_only: true    # slurm: submit every job, write jobs.yaml, stop; `watch` evaluates
  ...
slurm: ...
sessions:              # one pytest session per entry, under <output_root>/<session>
  simple-maccity: {}   # the sections above as written
  gcafs-anthro:        # an overlay, one level deep: its suite, timeout cap, job geometry
    harness: {suite_config: gcafs-anthro-suite.yaml, run_timeout_s: 28800, env: {...}}
    slurm: {cpus: 1, srun_args: --cpu-bind=cores, extra_args: --mem=0}
```

**The module environment is four keys of an application section.**
`modulefile` names the module to load and switches the whole block on
(null: no modules at all). `modulepath` lists the directories to
`module use` first, in order: an absolute entry is used as written (a
spack-stack environment's `modules/Core`), a relative one is under the
harness checkout, and an empty list means the application's own
`<clone>/modulefiles`. `module_env` is exported before the load — what
the modulefile reads, such as a `<name>_ver` version pin. The build
stage and every driver job render the same block from them
(`CECE_MODULEFILE`, `CECE_MODULEPATH`, `CECE_MODULE_ENV` carry it to the
pytest session), so a job loads exactly what the build loaded. How the
block starts is a fact of the platform, not a key: `module purge` on
Ursa, `module reset` on Gaea (`gaea-c6`, `gaea-c7`; detected from the
login-node hostnames `gaea6N` / `gaea7N`), where a purge
strips the Cray programming environment. Two more keys shape the build
stage only: `toolchain_tools` (the commands `which` locates after the
load, so the log names the compilers the configure will see) and
`configure_gates` (extended regular expressions the configure log must
match before anything is built, after the adapter's own gates — the
claims of the environment, such as `Using system Kokkos`).

The build and test stages, and the test job, start by leaving the
harness's own Python environment: they take its `bin/` off `PATH` and
unset `VIRTUAL_ENV` and `CONDA_PREFIX`, which `uv run` and a conda shell
would otherwise pass on. A CMake project can read those variables (HELM's
AXIS library, inside CECE, puts either one first on `CMAKE_PREFIX_PATH`),
so without this the harness venv would be searched before the module
environment for every package. The source, data and harness stages keep
the venv: they run the harness itself.

The harness ships UFS platform modulefiles of its own under
`modulefiles/` (`modulefiles/ufs_ursa.oneapi.lua`, `ufs_ursa.gnu.lua`,
`ufs_gaeac6.oneapi.lua`, `ufs_gaeac7.oneapi.lua`, and the `ufs_common.lua`
they load): copies of
the ufs-weather-model files plus the Kokkos packages, for building an
application against a spack-stack environment the way the UFS does.
They carry no environment of their own — name it in the run config:

```yaml
    modulefile: ufs_ursa.oneapi
    modulepath:
      - modulefiles                              # this checkout's modulefiles/
      - /path/to/spack-stack/envs/<env>/modules/Core
    module_env: {kokkos_ver: 4.5.01}             # optional pins
```

(`ASSAY_SPACK_ENV_ROOT: /path/to/spack-stack/envs/<env>` in `module_env`
is the one-variable alternative to the second path: the modulefiles turn
it into the same `modules/Core` entry.)

**Where the build runs is a key too.** `build_node: login` (the default)
configures and builds where the CLI runs, with network access.
`build_node: compute` — slurm runtime only, and it needs the `slurm:`
section — puts the configure and the build each behind its own `srun`
(the section's account, QOS, partition and `extra_args`, one task with
`build_jobs` cores, `build_minutes` minutes), in the module environment
the login shell loaded: an offline compile probe, the way a workflow that
compiles on compute nodes must build. The adapter turns off what would
reach the network at configure time and says so in the log; CECE
configures with `-DCECE_BUILD_TESTING=OFF`, because its test stack
fetches GTest and RapidCheck. `tests.enabled: true` asks the build stage
for the application's own test stack: on the login node that is the one
configure as `cmake_args` and `targets` say; after a compute-node probe
it is a second, login-node configure of the same build directory with
the test stack back on, the gates again, and a build that reuses the
compute-built objects. For CECE, `tests.enabled` requires
`-DCMAKE_BUILD_TYPE=Debug` and `-DENABLE_ESMF=ON` in `cmake_args` (a
validation error names the missing flag) — its unit tests are built
against a Debug build with ESMF; any other build keeps the tests off.
And whenever `-DENABLE_ESMF=ON` is among the `cmake_args`, the configure
log must show `CECE dependency ESMF: system`: the environment's ESMF,
never a fetched one. The build stage's first line names the node
(`build node: login`, or `compute (srun ...)`, `then login (tests)`).

**Command-line arguments are overrides on the file.** `--override`
(`-o`) takes `key:path=value` entries, repeatable and several per flag;
values are YAML scalars (`8`, `true`, `null`, `[-x, -k, base]`, or a
plain string), and pydantic validates the merged result exactly as it
validates the file, so a typo in a key path is the usual unknown-key
error. Precedence, highest first: `--override` entries in command-line
order, the named flags `--platform` and `--root-dir`, the file, then the
derived defaults (hostname detection; the harness checkout's parent as
the root).

```sh
uv run ufs-chem-assay run --config-file=config/ursa.yaml --application cece --dry-run   # render only
uv run ufs-chem-assay run --config-file=config/ursa.yaml --application cece             # clone, build,
                                                                                        #   data, harness
uv run ufs-chem-assay run --config-file=config/ursa.yaml --application cece --stage harness \
  --override harness:suite_config=exhaustive-maccity-run-only-suite.yaml harness:pytest_args='[-x]' \
             applications:cece:ref=develop slurm:qos=batch
uv run ufs-chem-assay run --config-file=config/ursa.yaml --application cece --session gcafs-anthro  # one session
uv run ufs-chem-assay run --config-file=config/local.yaml --application all --dry-run   # all configured apps
uv run ufs-chem-assay run --config-file=config/local.yaml --application "monetio cece" --dry-run
uv run ufs-chem-assay watch                                           # evaluate as jobs finish
```

Every invocation, dry runs included, writes the effective configuration
— the file with every override merged in, as validated — to
`<root_dir>/scripts/run-config.yaml` beside the rendered scripts; it
re-runs the same configuration with no override list, and it is the way
to check that an override landed.

Stages (`--stage`, repeatable): `source` (clone or fast-forward the
checkout), `build` (modules + cmake, or the application's container build
script locally), `test` (the application's own test suite against that
build, for applications with `tests.enabled`; below), `data`
(`ufs-chem-assay fetch` for the inputs each session's `suite_config`
selects, under that session's exports, plus the plots' map data, downloaded
into `data.cartopy_dir` — `<root_dir>/cartopy` unless named), `harness`
(the pytest session), and `resume` (the second half of a submit-only
session, below; rendered by default, executed only when named). Each
renders to `<root_dir>/scripts/<NN>-<stage>-<application>.sh` —
`<NN>-<stage>-<application>-<session>.sh` for the harness and resume
stages of a named session — and runs with bash where the CLI runs; logs
land in `<root_dir>/logs/`. A run config naming several applications is a
comprehensive run: the stages render per application in stage-major order
(every `source`, then every `build`, ...) and the harness stage runs one
pytest session per application, each under `<output_root>/<application>`;
`--application NAME` (repeatable) narrows such a run.

**The application's own tests.** `tests.enabled: true` on an application
section gives it a `test` stage: its own test suite — `ctest` for CECE,
all 577 of its tests — run against the build the build stage left. The
stage only runs tests: a guard fails it when the test stack is not there,
and nothing in it compiles. Where they run follows the runtime. Under
**slurm** they are one job, rendered beside the stage script as
`04-test-<application>.sbatch` so a dry run shows all of it: the
`slurm:` section's account, QOS, partition and `extra_args`, one node
with `tests.ntasks` tasks of `tests.cpus` cores for `tests.minutes`, the
application's module block, the `tests.env` exports, then `ctest`
**directly on the node, not behind `srun`** — ctest is the launcher, and
each MPI test starts its own ranks — writing a junit report, bracketed
by `tic`/`toc` and followed by `report-mem` like a driver job. The stage
script submits it with `ufs-chem-assay submit-tests` and returns at once:
nothing waits on the queue, and the data and harness stages go on while
the tests run. `submit-tests` records the job in the tests root,
`<root_dir>/tests/<application>/tests.yaml` (the test ledger: the job,
who submitted it, what build it ran against, and later the verdict),
refuses a second submission while one is queued (`--resubmit` cancels
and submits again), and evaluates a finished run nobody evaluated before
replacing it. The job's files carry its id (`tests-<job id>.out`,
`tests-<job id>.xml`), so a resubmission overwrites nothing. Under
**native** and **docker** the stage runs the command in place — behind
`harness.launcher` natively, in the application's image under docker —
tees it to `tests.out`, then calls `ufs-chem-assay evaluate-tests` and
exits with the tests' status. `tests.filter` and `tests.parallel` are
`ctest -R` and `ctest -j`. For CECE the tests need a Debug build with
ESMF and `all` among the targets (above); `config/ursa.yaml` ships with
them off.

`ufs-chem-assay watch` covers the test job with the sessions' jobs in
one `squeue` per tick, and evaluates it in-process when it has left the
queue (there is no resume script): Slurm's accounting, the junit counts,
one line — `tests cece: evaluated (exit 0), 577 passed / 0 failed / 0
skipped` — and its verdict in `watch`'s exit status. `watch --only tests`
(or `--only sessions`) watches one kind; after a run of the source,
build and test stages alone it is the form to use, since no session was
submitted. `evaluate-tests` is the same evaluation by hand. `report`
adds one line per test run and, for a run that failed, one issue:
`tests-failed` with the failed tests' names (up to `--tail`) and the tail
of the output, or `tests-job-failed` when the job died without a failed
test to name (a time limit, no junit report). `cancel --tests-root
<root_dir>/tests/<application>` scancels a queued test job.

**Stage outcomes.** `run` records what every stage script it executed or
skipped came to in `<root_dir>/scripts/stages.yaml`, beside the effective
config: the script, its stage, application, toolchain and build variant,
`passed` / `failed` / `skipped`, the exit status, start and end times,
its log under `<root_dir>/logs/` and, for a skipped script, the failure
that skipped it. Each script's entry is replaced by its next run, so a
run of one variant leaves the others' entries alone; a dry run records
nothing. `report` and `watch` read it:

- a line per selected source, build and data stage (`build
  catchem/oneapi/musica  failed (exit 2)  2026-11-20 16:43 UTC
  02-build-catchem-oneapi-musica.sh`), with a `stage-failed` issue (the
  log's last `--tail` lines and its path) or a `stage-skipped` one (the
  failure that skipped it). `--only` does not hide these lines: sessions
  and test runs both stand on the builds;
- a test run with no record names the stage that kept it from running
  (`no test run: 02-build-catchem-oneapi-musica.sh failed (exit 2)`);
- a test run older than its variant's latest build, or than a later test
  stage that failed, gets a `tests-stale` issue: the result shown is not
  the current build's.

A failed or skipped stage and a stale result make the report's exit 1.

**Applications that are built and tested, never swept.** Two registered
applications have no driver, so no suite and no pytest session: a run
gives them the stages they take part in and skips the rest, saying so
(`catchem takes no part in the data, harness, resume stage(s)`), and
anything driver-shaped that names one — a suite file, `pytest
--application`, `fetch` — is refused (`catchem has no driver suite: it
takes part in the source, build and test stages only`). Neither builds
under the docker runtime.

- `catchem` (source, build, test): CATChem cloned with its submodules
  (a guard requires MUSICA's), configured with `cmake_args` — by default
  `-DCMAKE_BUILD_TYPE=Debug -DCATCHEM_ENABLE_KOKKOS=ON
  -DCATCHEM_BUILD_MUSICA=ON -DCATCHEM_BUILD_NUOPC=ON` — and built
  (`targets`, default `all`). Kokkos is always on (a build without
  `-DCATCHEM_ENABLE_KOKKOS=ON` is refused), and the configure log must
  show `Using system Kokkos`: the module environment's, never a fetched
  one. MUSICA fetches its dependencies at configure time, so a build
  with `-DCATCHEM_BUILD_MUSICA=ON` and `build_node: compute` is refused;
  with MUSICA off nothing is fetched, and a compute-node build is the
  whole build, its tests included. A compute-node build and a MUSICA
  build are two build variants (below). With
  `-DCATCHEM_USE_SYSTEM_YAML_CPP=ON` the build also checks, after each
  configure, that CATChem took an installed yaml-cpp: its fallback line
  (`yaml-cpp not found on system, using internal version`) must be
  absent and `yaml-cpp_DIR` in `CMakeCache.txt` found, and the log
  prints it. Its tests are `ctest`, one single-rank job; `tests.enabled`
  requires the Debug build and `all` among the targets.
- `kokkos-smoke` (build, test): a small CMake project shipped with the
  harness that requires the module environment's Kokkos and
  kokkos-kernels with no fallback, builds one executable (a
  `parallel_for` and a BLAS `axpby`), and runs it — the direct check that
  an environment provides the two packages. Its section needs no
  `git_url` or `ref`; `modulepath` defaults to the harness's own
  `modulefiles/`; the build is under `<root_dir>/kokkos-smoke/build`. The
  configure log must show both packages found. The test is the
  executable itself: it writes no junit report, so the run counts as one
  test, passed when its output holds `SMOKE TEST PASSED`.

```yaml
applications:
  cece: {...}
  catchem:
    git_url: git@github.com:ufs-community/CATChem.git
    ref: develop
    modulefile: ufs_ursa.oneapi
    modulepath: [modulefiles, /path/to/spack-stack/envs/<env>/modules/Core]
    tests: {enabled: true}
  kokkos-smoke:
    modulefile: ufs_ursa.oneapi
    modulepath: [modulefiles, /path/to/spack-stack/envs/<env>/modules/Core]
    tests: {enabled: true}
```

`--application all` on such a config clones, builds and tests all three
and runs the data and harness stages for `cece` alone.

**Toolchains.** `toolchains:` is a map of toolchain name → a compiler
suite: everything that must change together when the compiler does, so
no application can pair one suite's modulefile with another's flags.

```yaml
toolchains:
  oneapi:
    modulefile: ufs_ursa.oneapi
    modulepath: [modulefiles, /path/to/envs/<oneapi env>/modules/Core]
    module_env: {}                    # exported before the load: version pins
    toolchain_tools: [mpiicx, mpiicpx, mpiifx, cmake, ctest]
    cmake_args: [-DCMAKE_CXX_FLAGS=-fp-model=precise]
    mpi_launcher: mpiexec             # mpiexec (FindMPI's default) | srun
    tests_env: {I_MPI_HYDRA_BOOTSTRAP: fork}
    configure_gates: []               # added to every application's: see below
  gnu:
    modulefile: ufs_ursa.gnu
    modulepath: [modulefiles, /path/to/envs/<gcc env>/modules/Core]
    toolchain_tools: [mpicc, mpic++, mpifort, cmake, ctest]
    mpi_launcher: srun
    tests_env: {OMPI_MCA_plm: isolated}
```

A toolchain is overlaid on every application section: its module
environment (`modulefile`, `modulepath`, `module_env`,
`toolchain_tools`) replaces the section's whole — and under a
`toolchains:` map a section may not set those keys itself, nor
`clone_dir`; its `cmake_args` are appended to the application's own; its
`configure_gates` are added to them, so a toolchain gate must be a line
every application's configure prints (a gate only one application can
pass, such as CECE's ArborX line, goes on that application's section);
its `tests_env` is merged over `tests.env`. `mpi_launcher: srun` adds `-DMPIEXEC_EXECUTABLE` (the path
of `srun`), `-DMPIEXEC_NUMPROC_FLAG=-n` and, when `mpiexec_preflags` is
set, `-DMPIEXEC_PREFLAGS` to every CMake configure, so each MPI test is
its own Slurm step; `mpiexec` leaves CMake's default. A section's rules
are checked as each toolchain builds it (a flag CECE's tests require
may be the toolchain's).

`--toolchain NAME` (several, or `all`) selects what a `run` builds; it
is required when the config names more than one. Each toolchain is its
own build: its checkouts, build directories and tests roots are under
`<root_dir>/<toolchain>/`, its scripts are
`<NN>-<stage>-<application>-<toolchain>[-<session>].sh`, and its session
output roots gain a `/<toolchain>` segment, so two toolchains never
share anything but the harness checkout, `scripts/`, `logs/` and the uv
cache. With several selected, the run is toolchain-major — every script
of every toolchain is rendered first, then the whole stage list runs for
one toolchain, then for the next. A failed stage stops only what
depends on it: a failed source stage skips the rest of that
application, any other failed stage skips the later stages of the same
application and build variant (a failed build its tests), and every
other application, variant and toolchain runs on, each skipped script
logged with the failure that skipped it. The run exits with the first
failure's status after one line per toolchain (`oneapi: failed at
02-build-cece-oneapi.sh (exit 2)`, `gnu: ok`):

```sh
uv run ufs-chem-assay run --config-file=<config> --application all --toolchain all \
    --stage source --stage build --stage test
uv run ufs-chem-assay watch --only tests      # every toolchain the run selected
uv run ufs-chem-assay report --only tests --toolchain gnu
```

The effective config stays one file — the config as loaded, plus
`selected_toolchains` (and `selected_applications`, `selected_builds`) — and `watch` and `report` cover the toolchains
the last `run` selected (`--toolchain` narrows them), labelling each
session and test run with its toolchain (`gnu/simple-maccity`, `tests
cece/gnu`). Without a `toolchains:` map nothing changes: one unnamed
build, the module keys on the application section, today's paths.
`config/ursa-kokkos.yaml` is the shipped use of all of this: the
`oneapi` and `gnu` toolchains on Ursa, the three applications built as
the offline compile probe and tested, the driver sessions left to be
asked for.

`config/gaea-c7.yaml` is the same idea on Gaea C7, for what has been
built and tested there: one toolchain (`oneapi`: Intel's `icx` / `icpx` /
`ifx` with cray-mpich, `mpi_launcher: srun`), CECE and CATChem, against the
spack-stack 2.2.0 environment through `modulefiles/ufs_gaeac7.oneapi.lua`.
Three things in it are particular to the machine. `module_env` pins
`esmf_ver` and `mapl_ver`, because the environment's default ESMF is a
later beta than the one its MAPL is built against. `slurm.extra_args` is
`--clusters=c7`, which reaches the build's `srun` steps as well as the
test job. And CECE takes everything from the environment: Kokkos,
kokkos-kernels and ArborX, and yaml-cpp (the modulefile loads `yaml-cpp`,
a local addition), each held by a gate (`CECE dependency Kokkos: system`,
`CECE dependency yaml-cpp: system`, the ArborX line). So the build is the
offline compile probe, as on Ursa: `build_node: compute`, with
`--override applications:cece:build_node=login` to build on the login
node alone. CATChem is two build variants of one checkout (`compute`,
MUSICA off, built whole on a compute node; `musica`, on the login node),
both with `-DCATCHEM_USE_SYSTEM_YAML_CPP=ON`, so they take the
environment's yaml-cpp and the build checks that they did. The Kokkos
smoke project is not in the template. CECE's test job exports
`PIP_NO_INDEX=1`, which stops the pip fixtures of CECE's Python tests
from retrying an index a compute node cannot reach; CATChem's tests reach
no network.

```bash
ufs-chem-assay run --config-file=config/gaea-c7.yaml --application cece \
  --stage source --stage build --stage test
ufs-chem-assay watch --only tests
```

**Build variants.** `applications.<name>.builds:` is a map of variant
name → an overlay on that application's section: several builds of one
checkout with different options, each with its own build directory,
tests root and scripts. A variant may set `cmake_args`, `build_jobs`,
`targets` and `tests` (each replacing the section's whole),
`configure_gates` (added to the section's), `build_node` and
`build_minutes`; the checkout keys and, under `toolchains:`, the module
keys stay the section's and the toolchain's. The variant is overlaid on
the section as written, then the toolchain on top, so a toolchain's
configure arguments still come last.

```yaml
applications:
  catchem:
    builds:
      compute:   # MUSICA off: nothing to fetch, so the whole build runs on a compute node
        cmake_args: [-DCMAKE_BUILD_TYPE=Debug, -DCATCHEM_ENABLE_KOKKOS=ON, -DCATCHEM_BUILD_MUSICA=OFF]
        build_node: compute
      musica:    # MUSICA fetches at configure time: the login node
        cmake_args: [-DCMAKE_BUILD_TYPE=Debug, -DCATCHEM_ENABLE_KOKKOS=ON, -DCATCHEM_BUILD_MUSICA=ON]
        build_node: login
```

| | No `builds:` | Variant `<v>` |
|---|---|---|
| Build directory | `<checkout>/build` | `<checkout>/build-<v>` |
| Tests root | `<root>[/<toolchain>]/tests/<app>` | `<root>[/<toolchain>]/tests/<app>/<v>` |
| Build and test scripts | `02-build-<app>[-<toolchain>].sh` | `02-build-<app>[-<toolchain>]-<v>.sh`, the same for `04-test-…` |
| Labels | `tests <app>[/<toolchain>]` | `tests <app>[/<toolchain>]/<v>` |

The source stage runs once per application (one checkout serves every
variant); the build and test stages run once per variant, in the map's
order. `--build NAME [NAME ...]` (or `all`) on `run`, `watch` and
`report` selects variants, every one by default; an application
without `builds:` keeps its one build either way, and a name no selected
application has is an error. `run` records the selection
(`selected_builds`, empty for every variant) for `watch` and `report`;
`submit-tests` and `evaluate-tests` take `--build` the way they take
`--toolchain`. An application with variants takes no data or harness
stage yet: a driver session runs against one build, and a run config
cannot name which. Without `builds:` nothing about a run changes.

**Sessions.** `sessions:` is a map of session name → an overlay on the
file's `harness:`, `slurm:` and `baselines:` sections, one level deep: an
overlaid key replaces the base value whole (a session that restates `env`
restates all of it), and `runtime` is config-wide. Each entry is one
pytest session with its own output root, `<output_root>/<session>`
(`<output_root>/<application>/<session>` in a comprehensive run), its own
`run.yaml`, run ULID, `benchmarks.csv` and upload, so every per-root
command works per session unchanged; `--session NAME` (repeatable)
narrows a run to some of them, and `--override
sessions:<name>:<section>:<key>=<value>` reaches one entry. Without
`sessions:` the config is one nameless session with the bare paths and
script names (`config/local.yaml`). `config/ursa.yaml` ships
`simple-maccity` (the base sections as written) and `gcafs-anthro` (its
suite, timeout cap and job geometry) enabled, and `exhaustive-maccity` as
a commented entry; a different set of sessions for a different
purpose is another run config over the same base.

**Submit, then watch.** With `harness.submit_only: true` (the Ursa
template; slurm runtime only) the harness stage's pytest session submits
every combination's job at once, writes `<output root>/jobs.yaml` (the
job ledger: each job's id, cluster, script, `.out` and deadline, who
submitted, and later when a session evaluated them with what status) and returns
within minutes; `run` renders a sixth script per session,
`06-resume-<application>-<session>.sh` — `pytest --resume` over the same
root — and executes it only under `--stage resume`. `ufs-chem-assay
watch` is the loop that finishes the work: it reads the effective config
the last `run` wrote (`--root-dir` as for `run`; no config file, no
overrides, so nothing can drift), polls `squeue` once per tick for every
session's jobs (every `slurm.poll_s` seconds, or `--interval`), reports
each session (`queued`, `running`, `done`; `evaluated (exit N)`), runs a
session's resume script once its jobs have all left the queue — one
session at a time, so the committed files are written serially — and
exits when every selected session is evaluated, with the sessions'
pytest statuses as its own (`--once` reports and exits; `--session` and
`--application` narrow it, and by default it covers the applications the
last `run` selected, recorded as `selected_applications` in the effective
config). A session or test run that is not submitted does not keep it
waiting: when the stage record says why (its source, build, data or
harness stage failed or was skipped) `watch` settles it at the first
check, as `not submitted (02-build-cece.sh failed (exit 2))`; with
nothing recorded it gives up after `--not-submitted-ticks` checks
(default 10, about five minutes at a 30 s poll), as `not submitted
(still not submitted after 10 checks)`. Either way it ends with the
report and exit status 1, naming what was never submitted. It is safe to kill and restart: a session
already evaluated is skipped by its ledger, a resume that died is tried
again by the next `watch`. `tmux` is a convenience for watching, not a
requirement. `ufs-chem-assay cancel --output-root=<session root>`
scancels a session's jobs still in the queue, and `clean_root` refuses to
remove a root whose ledger lists queued jobs, naming that command.

**Multi-cluster Slurm.** On a machine whose jobs go to a named cluster
(Gaea: `slurm.extra_args: --clusters=c7`), `sbatch --parsable` answers
`<job id>;<cluster>`. The harness records that cluster beside the job id
— in `jobs.yaml` for the driver jobs, in `tests.yaml` for a test job —
and names it (`-M <cluster>`) on every later `squeue`, `sacct` and
`scancel` about that job: the wait inside pytest, `watch`, `cancel`,
`evaluate-tests`, the `clean_root` guard. `extra_args` is the only
setting; no `SLURM_CLUSTERS` is needed in the shell, and a ledger whose
jobs are on two clusters is asked once per cluster. A job submitted
without a cluster name is asked about without one, as before.

**Then report.** `watch` ends with the run report, and
`ufs-chem-assay report` prints the same thing any time later, from the
files alone (no `squeue`, no `sacct`): one line per session of the last `run`'s effective
config — its state (`evaluated (exit N)`, `not evaluated`, `submission
incomplete`, `not submitted`), run ULID, test counts, combinations clean,
how the comparisons went (`passed` / `failed` / `unpublished` / `off`),
where it was uploaded — then one block per issue: `driver-failed`,
`driver-timeout`, `accounting-missing`, `test-failed`,
`comparison-failed` for a combination (job id, Slurm state, exit code,
the failed tests, the last `--tail` lines of its `.out`, and the paths to
open: `.out`, `cece.log`, the combination directory, the resume log), and
`not-submitted`, `submission-incomplete`, `not-evaluated`,
`pipeline-error` (benchmarks, publish, upload) for a session. Pytest's
`FAILED … - message` lines and the pipeline's `ERROR` lines are read from
the newest `06-resume-*` (or `05-harness-*`) log under `<root_dir>/logs/`
to decorate what the files already say; a skipped comparison is never an
issue, but it is counted. `--session`/`--application` narrow it,
`--output-root PATH` reports one root instead (a downloaded one works;
no logs), `--write PATH` saves the report as YAML (the `RunReport`
model), `--quiet` keeps the session lines only. Exit 0 only when every
selected session is evaluated with exit 0 and nothing is wrong, so a
script can end with `report` and mean it; the report writes nothing into
a root. The
CLI never deletes anything except the harness output root
(`clean_root`), and never mutates an existing checkout without
`update_source`. `baselines.store` (`ASSAY_BASELINE_STORE`),
`harness.publish_baselines`, and `harness.no_suite_update` carry the
[publishing](#publishing-baselines) options into the harness stage;
`baselines.atol` / `baselines.rtol` (`ASSAY_BASELINE_ATOL` /
`ASSAY_BASELINE_RTOL`, null = the entries' own) the tolerance overrides.

## Running on RDHPC (Ursa)

RDHPCS machines have no docker, so the target driver is built natively
against the application's own modulefiles (`<checkout>/modulefiles/cece_ursa.*.lua`
for CECE), and the harness runs on a **login node** submitting **one
Slurm job per driver call — all of a session's at once**, then waiting
for each by polling `squeue` and judging it by `sacct` — the **slurm
runtime**, selected by `ASSAY_RUNTIME=slurm` (the default once
`ASSAY_PLATFORM` is anything but `local`; the platform is detected from
the hostname and overridable). The jobs are recorded in `<output
root>/jobs.yaml` as they are submitted; `pytest --submit-only` ends the
session there and `pytest --resume --combo-output-root=<root>` reopens
the root later (adopting its run and combination ids, waiting for what is
still queued) and runs every test and the session-end pipeline — the two
halves `harness.submit_only` and `ufs-chem-assay watch` drive (above).
Each job is
a rendered script, `<combo_id>.sbatch`, kept beside the combo's `.yaml`
and `.out` so a failed job is reproducible by hand: `#SBATCH` directives
from `ASSAY_SBATCH_ARGS` (account, QOS, partition, `-N 1 -n <ntasks> -c
<cpus>`, then `slurm.extra_args` verbatim) and the suite's
`timeout_s` rounded up to whole minutes, the module block (the
platform's `module purge` or `module reset`, the `CECE_MODULE_ENV`
exports, a `module use` per `CECE_MODULEPATH` entry — the checkout's
own `modulefiles` by default — and the `CECE_MODULEFILE` load),
the `ASSAY_JOB_ENV` exports, and the driver behind `srun` with
`ASSAY_SRUN_ARGS` (`--ntasks=<ntasks>` plus `slurm.srun_args`, e.g.
`--cpu-bind=cores`; MPI inside a batch job needs Slurm's PMI endpoint),
bracketed by `tic`/`toc` timestamps and followed by RDHPCS's `report-mem`
when present, so the job's `.out` records its own wall time and memory.
The task count is a run-config choice, not a configuration one: the driver
splits latitude bands across the ranks it is given, and `run.yaml` (and a
published `baseline.yaml`) records the launch — `sbatch_args`,
`srun_args`, `job_env` — under `launch`. Two environments stay apart on
purpose: the harness venv never sees the modulefile (spack-stack's
`PYTHONPATH` would shadow its numpy), the driver always does. Analysis
runs in the pytest process, so cap `ASSAY_DASK_NWORKERS` on a login node.
`ASSAY_RUNTIME=native` runs the driver as a direct host process with
`ASSAY_LAUNCHER` as an optional prefix — for a docker-less machine whose
shell already suits both the harness and the driver; not Ursa, where the
two environments conflict.

The run config above assembles all of that; the same steps by hand are
in [docs/ursa-runbook.md](docs/ursa-runbook.md). The gcafs-anthro
configuration is the `gcafs-anthro` session of `config/ursa.yaml` (an
overlay with the suite, a raised timeout cap, and one task on one bound
core with the single-thread exports and `--mem=0`; its inputs' Ursa
locations are in the suite's `sources:`) — `--session gcafs-anthro` runs
it alone — and has its own runbook:
[docs/gcafs-anthro-runbook.md](docs/gcafs-anthro-runbook.md).

## CI and releases

Commit messages (and PR titles) must follow
[Conventional Commits](https://www.conventionalcommits.org/) — `feat: …`,
`fix: …`, etc. Locally the `commit-msg` pre-commit hook rejects a
non-conforming message at commit time; in CI the PR title is checked with
the same hook (squash-merge subjects come from PR titles, and
semantic-release parses them to compute versions).

Every pull request also runs `.github/workflows/dependency-review.yaml`,
which fails the PR when a dependency change (the lock, an action) brings
in a known vulnerability of moderate severity or above — it needs the
repository's **Dependency graph** enabled (Settings → Code security), and
reports that plainly until it is; Dependabot
(`.github/dependabot.yml`) opens weekly grouped PRs for the actions and
the uv lock (third-party actions are pinned by commit hash with a
`# vX.Y.Z` comment that Dependabot bumps along with the hash; GitHub-owned
actions stay on major tags). The `tests` job measures the harness
suite's coverage of `src/` (tests excluded) and **fails under 100%**
(`[tool.coverage]` in `pyproject.toml`; every line is exercised or is one
of the listed unreachable patterns), reports it in the job summary, and
uploads the HTML report as an artifact; an `actionlint` job lints the
workflows.

Every pull request (and every push to `develop`/`main`) also runs
`.github/workflows/codeql.yaml`: CodeQL code scanning of the Python and
the workflow files, with findings under the repository's Security tab and
PR annotations; it runs weekly as well so new queries reach an unchanged
branch.

Every pull request (and every push to `develop`/`main`, as post-merge
validation) runs `.github/workflows/ci.yaml`: the toolchain container is
built from `Dockerfile` (cached via the GitHub Actions buildx cache;
never pushed off-runner), then pre-commit and the harness tests run
inside it with no `ASSAY_*`/`CECE_*` environment at all. Reproduce
exactly what CI runs locally:

```sh
docker buildx build --load -t ufs-chem-assay:dev .
docker run --rm -v "$PWD":/repo -w /repo ufs-chem-assay:dev \
  sh -c 'git config --global --add safe.directory /repo \
         && uv sync --frozen && uv run pre-commit run --all-files'
docker run --rm -v "$PWD":/repo -w /repo ufs-chem-assay:dev \
  sh -c 'git config --global --add safe.directory /repo \
         && uv sync --frozen && uv run pytest src/tests/ufs_chem_assay'
```

Pull requests targeting `develop` (and pushes to `develop`, which exist
to warm the shared caches) additionally run
`.github/workflows/integration.yaml`: the CECE repository and ref named
in the workflow's `env` block are cloned (nested submodules), the
container image built through the buildx cache and loaded as
`cece/cece-dev`, the driver compiled in the container only when no
`build/` tree is cached for that CECE commit (an exact cache hit skips the
build step outright — the driver is in the restored tree), the suite's
inputs staged with `ufs-chem-assay fetch`, and `simple-maccity-suite.yaml`
runs for real.
Baseline-comparison tests skip in CI
(`ASSAY_ENABLE_BASELINE_COMPARISONS=false`: the store is the private
`s3://ufs-chem/baselines` prefix and the job has no AWS credentials —
re-enabling is a standing TODO). The CECE ref is upstream `develop`. The full
output root uploads as a workflow artifact on success and failure
alike; `run.yaml` records the exact CECE commit (`application_commit`).
Mirror it locally:

```sh
ASSAY_ENABLE_BASELINE_COMPARISONS=false uv run pytest \
  src/tests/test_driver_combos.py --suite-config=simple-maccity-suite.yaml
```

Releases are **automatic**. Every push to `develop` or `main` (that is,
every merged PR) runs the `release-psr` job of `ci.yaml` after `pre-commit`
and `tests` are green: it calls the reusable
`.github/workflows/semantic-release.yml`, where python-semantic-release v10
(configured in `[tool.semantic_release]` in `pyproject.toml`) computes the
next version from the conventional-commit subjects since the last tag —
`develop` produces `X.Y.Z-rc.N` release candidates, `main` full releases —
and pushes a version-bump commit (`pyproject.toml` and `uv.lock`), a git
tag, and the `CHANGELOG.md` update back to the branch. Nothing is
published: no GitHub Release object, no PyPI, no container registry. A red
branch does not release.

Every pull request (and a manual dispatch of `ci.yaml` against any branch)
runs `release-preview` first: a dry run that evaluates both a merge commit
and a squash merge of the PR and writes a "Semantic Release Plan" — current
version, next version, tag, will-release, is-prerelease, and the projected
diff of `pyproject.toml`/`uv.lock`/`CHANGELOG.md` — to the job summary.
Nothing is committed, tagged, or pushed by a preview; fork PRs run it with
the read-only token.

The bump commit's message is `<version>` plus the marker line
`Automatically generated by python-semantic-release` — deliberately **not**
`[ci skip]`. The push does start CI (it comes from an App token, see
below); the `release-psr` job, the `integration` job, and the pushed-commit
grammar check skip themselves on that marker, while `pre-commit` and
`tests` still validate the bump commit.

**Credentials.** The `develop` ruleset requires pull requests, so the
workflow's own token cannot push the bump commit. Releases push with a
GitHub App: repository secrets `SEMVER_APP_ID` (the App's client id) and
`SEMVER_APP_PRIVATE_KEY` (PEM contents, unquoted); the App needs
`Contents: Read and write` on this repository and must be an
always-bypass actor on every ruleset that requires pull requests. The
`release-verify` job gates each real release on exactly that, and
`.github/workflows/verify-secrets.yml` re-checks it daily at 06:00 UTC
(dispatch it by hand after changing the App, secrets, or rulesets): secrets
present and well-formed, the App token can create and delete a ref here,
and the ruleset bypass is in place (a warning otherwise — the one thing
that makes a release fail at its final push). Previews fall back to the
plain token when the secrets are absent. Forks never release and never
run the daily check: the three workflows guard on
`github.repository == 'benkozi/ufs-chem-assay'` (three strings to edit on
a repository move, next to `CECE_REPOSITORY` in `integration.yaml`).

semantic-release is not a project dependency (the action runs it); preview
the next version locally with an ephemeral run:

```sh
uv tool run --from 'python-semantic-release>=10,<11' semantic-release --noop version
```

## Results

By default results land in a pytest temp directory (printed paths in test
failures point there; pytest keeps the last few runs under e.g.
`/tmp/pytest-of-<user>/`). With `--combo-output-root=combo_runs` they land in
`combo_runs/` at the application checkout root (`CECE_ROOT_DIR`). Either way, one
directory per combination:

```
<output-root>/
  upload.yaml                  # present once the root was uploaded (upload-output)
  jobs.yaml                    # slurm: the job ledger — every combination's job as
                               #   submitted (id, script, .out, deadline), who submitted,
                               #   and when a session evaluated them with what status
  run.yaml                       # run manifest: session ULID; the application and
                                 #   its checkout's HEAD SHA (application_commit; null
                                 #   only when no checkout is configured — a configured
                                 #   root that is not a git checkout fails the session
                                 #   at start); the harness's own harness_version and
                                 #   harness_commit (HEAD, `-dirty` when edited; null
                                 #   when not run from a git checkout); platform,
                                 #   runtime, modulefile; every resolved suite in
                                 #   selection order (one-element list when single);
                                 #   every declared input as found at session start
                                 #   (suite, url, path, sha256, bytes or null); and
                                 #   every pinned baseline as found or synced (suite,
                                 #   ulid, source, path, state, detail)
  combos.csv                     # effective-parameter table: one row per sweepable
                                 #   dimension per combo (columns: run_id, combo_id,
                                 #   application, suite, name, target, field, value, swept)
  test-report.csv                # per combo-test outcome: pytest_name, application,
                                 #   suite, combo_id, combo, result (passed/failed/skipped)
  descriptive_stats.csv          # all combos' statistics, concatenated (suite-stamped)
  stats-comparison.csv           # all combos' comparison rows, concatenated
  01K0Z8FJX2.../                 # one directory per combination (runtime ULID);
    <combo_id>.yaml              #   generated driver config
    <combo_id>.out               #   captured driver stdout+stderr
    cece.log                     #   the driver's own tee'd run log (log_file always
                                 #     points here, whatever the base config says)
    <combo_id>-stats.csv         #   per-NetCDF descriptive statistics
    <combo_id>-stats-comparison.csv  # comparison rows (when configured)
    plots-overview/              #   spatial plot per NetCDF + per-variable GIF
    plots-baselines/             #   bias maps + GIF (compared combos only)
    baseline.yaml                #   present once the combination was published as a
                                 #     baseline (ufs-chem-assay publish-baselines)
    *.nc                         #   driver NetCDF output
```

Test ids stay human-readable (`MACCITY.map-consd`, target-qualified;
`<suite>/<combo>` in multi-suite sessions); directories are runtime ULIDs —
minted per combo per run, time-ordered (directories list in creation order),
never derived from content. The output root stays flat however many suites
run. Every table carries the `application` column immediately before
`suite`, so rows stay self-describing when CSVs from several output roots
(a comprehensive run's per-application sessions) are concatenated. Cross-run
joins use the recorded parameters, not ids: `combos.csv` is
the **effective-parameter table** — for every combo, one row per sweepable
dimension (for CECE: per-stream `taxmode`/`tintalgo`/`mapalgo`,
per-species-entry `operation`/`category`/`vdist_method`) with the value from
the combo's generated config, `swept` marking actual sweep dimensions — so
pinned parameters and sweep-less `base` combos join exactly like swept ones
(join stats to parameters on `run_id` + `combo_id`, or across runs on
`application` + `suite` + `combo` name).

Every run gets a runtime-generated ULID (`run_id`) — logged at session
start, written to `run.yaml`, and stamped into every stats row so CSVs from
different runs stay distinguishable. It is never set via configuration;
unknown keys in suite or driver config files are rejected at load time.

Spatial plots render at session end (suite `plotting.enabled`, default on;
`gif_enabled` controls the per-variable GIF), one per spatial field — the
same lat/lon rule as the statistics. All plots of a variable share one
**suite-wide color scale** derived from the descriptive statistics — so
plotting requires `compute_descriptive_stats` — whose maximum is always the
exact suite-wide maximum (an outlier that saturates a map is a finding).
The scale is **logarithmic by default** (`plotting.scale: log`): emission
and concentration fields are non-negative and span many decades, and a
linear map shows a blank globe with a few bright cells. Under `log` the
floor is `max / 10**plotting.decades` (default 5), never below the
suite-wide smallest positive value (the `min_positive` statistic), or
`plotting.floor` in data units when set; zeros (and negatives) have no
logarithm and are drawn light grey with `(grey: 0)` in the colorbar label,
so "no emission" and "small emission" read differently. `plotting.scale:
symlog` keeps zeros on the colorbar inside a linear core of that floor;
`linear` is the plain exact min/max. The title carries `[log]`/`[symlog]`.
Bias maps are symmetric at the exact max |difference|, linear by default;
`plotting.bias_scale: symlog` (with `bias_decades`) shows ULP-level
structure beside gross differences. Boundary rendering needs Natural
Earth coastline/border data. Under `ufs-chem-assay run` it lives in
`data.cartopy_dir` (`<root_dir>/cartopy` by default, exported to the
session as `ASSAY_CARTOPY_DATA_DIR`), and the data stage downloads it
there (`data.warm_cartopy`); a bare `pytest` run leaves cartopy its own
directory (`$XDG_DATA_HOME/cartopy`, else `~/.local/share/cartopy`) and
downloads on first use. Offline, or when the directory cannot be written,
plots degrade to data-only maps with a warning.

Stats CSV columns: `run_id`, `application`, `suite` (the suite's unique
`name` from its yaml, e.g. `simple-maccity`), identity (`combo_id`, `combo`, `file`,
`variable`), the file's
timestamp from its NetCDF time coordinate as `time` (ISO-8601) plus part
columns `year`/`month`/`day`/`hour`/`minute`/`second` for easy time
summaries (null if the file has no time coordinate), and the nan-aware
statistics (`count`, `sum`, `mean`, `std`, `min`, `min_positive` — the
smallest value above zero, empty when none —, `max`, `median`).

## Output upload

A finished run's **whole output root** — `run.yaml`, the CSVs, every
combination directory with its NetCDF, `.out`, logs, plots, job script and
`baseline.yaml` — can be copied to an output store under its run ULID:

```sh
AWS_PROFILE=<profile> uv run ufs-chem-assay upload-output --output-root=<root> [--dry-run]
AWS_PROFILE=<profile> uv run ufs-chem-assay download-output --ulid=<run_id> --dest=<dir> [--dry-run]
```

The store is `ASSAY_OUTPUT_STORE` (the run config's `outputs.store`), by
default **`s3://ufs-chem/assay-output`** — the copy lives at
`<store>/<run_id>/`, beside the `baselines/` prefix in the same bucket;
`--store` overrides it per call, and a local directory works as a store too
(no AWS needed). Uploading is one resumable `aws s3 sync`: re-running it
transfers only what changed (a `baseline.yaml` written by a later publish,
say) and never deletes anything, and nothing is refused — a run is the same
run every time. `upload.yaml`, written into the root first and uploaded
with it, records the run ULID, store, destination, when, by whom, and the
file count and bytes. The download lands at `<dest>/<run_id>/` (`--dest` is
required), refuses a ULID the store does not hold, and never deletes local
files. `--dry-run` on either passes the wrapper's `--dryrun` through (a file
count for a directory store); an S3 store is probed first so a node without
network fails in seconds.

`pytest --upload-output` (the run config's `harness.upload_output`, **false
in every template**) does the upload as the last step of the session, after
the plots, so the copy is complete; with an S3 store the session refuses the
flag at start when the bucket is unreachable, and a dry-run session uploads
nothing. On Ursa that means the slurm runtime from a login node; a session
inside an allocation uploads afterwards with the command above.

## Benchmarks

Every session writes **`<output root>/benchmarks.csv`**: one row per
executed combination (dry runs write the header alone), and the same row
again as `<combo_id>/benchmarks.csv` inside the combination directory, so
a published baseline carries its own benchmark. A row is a `BenchmarkRow`
(`src/benchmarks.py`; the model's fields are the columns): identity
(`run_id`, `combo_id`, `suite`, `combo`, `config_sha256` — equal across
runs of the same generated configuration), provenance
(`application_commit`, `build_type` — the `CMAKE_BUILD_TYPE` of the
checkout's build directory, read from its CMake cache at session start
and recorded in `run.yaml`, so a Debug-build row is never read beside a
Release one by mistake; empty without a cache and in rows older than the
column — `harness_version`, `harness_commit`, `platform`,
`runtime`, `modulefile`, the launch: `sbatch_args`, `srun_args`,
`job_env`), timings (`duration_s`: docker/native, the harness's clock
around the driver call; slurm, submission to end as `sacct` reports it,
queue included; `wall_s`: the job's own `tic`→`toc`
lines, empty when the job was killed; `queue_s`, `elapsed_s`,
`max_rss_kb`, `slurm_job_id`, `slurm_state`: Slurm accounting via `sacct`,
on platforms that have it — Ursa and Gaea C7 today — and empty elsewhere), outcome
(`driver_result`: passed/failed/timeout, `exit_code`, `nc_files`,
`output_bytes`), and the baseline links (`baseline_ulid`,
`baseline_state`, `comparison_result`). Whether a combination later became
a baseline is the suite's `ulid:` line and the store's manifest, not a
column: a row never changes once written.

The committed copy lives beside each configuration's suites,
`src/tests/config/<application>/<configuration>/benchmarks.csv`, and grows
by rows: `pytest --record-benchmarks` (the run config's
`harness.record_benchmarks`, **true in the Ursa templates**, false in
`local.yaml`) appends the run's rows at session end, and
`ufs-chem-assay record-benchmarks --output-root=<root>` does the same for a
finished run (`--dry-run` lists what it would append). A row already
present — same `run_id` and `combo_id` — is skipped; existing lines are
never touched, re-sorted, or rewritten, and a header that is not the
model's columns is refused. The append holds an exclusive `flock` on the
file for its read-modify-write (the suite-file rewrites do the same), so
two sessions ending at once cannot interleave — `watch` evaluates them
one after another anyway (on Lustre, locking across nodes needs the
mount's `flock` option). Review the diff and commit it, as with baseline
ULIDs and input digests.

An application's own test runs are benchmarked as whole runs: every
evaluated run appends one row to `<root_dir>/tests/<application>/benchmarks.csv`
(a `TestBenchmarkRow`: the run's id, `application`, `toolchain`,
`build` (the build variant; empty for an application without `builds:`),
`platform`, `runtime`, `modulefile`, `build_type`, `cmake_args`,
`application_commit`, the harness's identity, the Slurm job and its
accounting, `exit_code`, `wall_s`, the junit counts — `tests_total`,
`tests_passed`, `tests_failed`, `tests_skipped` — and `ctest_s`, the
tests' own durations summed).
`ufs-chem-assay record-benchmarks --tests-root=<root_dir>/tests/<application>`
appends those rows to the committed per-application file,
`src/tests/config/<application>/tests-benchmarks.csv`, skipping runs
already there. A tests root's file from before the `build` column still
reads (the cell empty); the next evaluation there rewrites it with the
current header, its rows kept, and says so. A regression shows in the whole-run numbers; the run's
junit report in the tests root has the individual tests.

## Environment variables

Every variable can also be set (lowercase works) in a gitignored `.env`
file at the repo root, read when pytest runs from there; real environment
variables override `.env`. AWS credentials are not harness settings at
all — the AWS CLI reads its own configuration (see
[S3 data sync](#s3-data-sync)). Two namespaces: **harness-wide** settings under
`ASSAY_*`, and each **application's** settings under its
own prefix — `CECE_*` for the CECE adapter and `MONETIO_*` for the MONETIO adapter
(`CATCHEM_*` and `KOKKOS_SMOKE_*` exist for the two build-and-test
applications — `CATCHEM_ROOT_DIR`, `CATCHEM_MODULEFILE`, ... — but
nothing reads them outside `ufs-chem-assay run`, which takes those
values from the run config).

Harness-wide:

| Env var                         | Meaning                                        | Default                          |
|---------------------------------|------------------------------------------------|----------------------------------|
| `ASSAY_APPLICATION`             | the application the session runs (`--application` overrides) | unset → inferred from the selected suites |
| `ASSAY_PLATFORM`                | machine the harness runs on (`local`, `ursa`, `gaea-c6`, `gaea-c7`) | detected from the hostname, else `local` |
| `ASSAY_RUNTIME`                 | how the driver is spawned (`docker`, `native`, `slurm`) | `docker` on `local`, `slurm` elsewhere |
| `ASSAY_LAUNCHER`                | command prefix for native driver runs (e.g. `srun --ntasks=1`) | empty (run directly) |
| `ASSAY_SBATCH_ARGS`             | slurm runtime: sbatch options per driver job (`-A … -q … -p … -N 1 -n 1 -c …`) | empty |
| `ASSAY_SRUN_ARGS`               | slurm runtime: options of the `srun` that launches the driver inside each job (the run config renders `--ntasks=<slurm.ntasks> <slurm.srun_args>`) | `--ntasks=1` |
| `ASSAY_SLURM_QUEUE_WAIT_S`      | slurm runtime: seconds a driver job may wait in the queue before the harness cancels it (the run config's `slurm.queue_wait_s`) | `3600` |
| `ASSAY_SLURM_POLL_S`            | slurm runtime: seconds between `squeue` polls while a driver job is queued or running (the run config's `slurm.poll_s`) | `30` |
| `ASSAY_JOB_ENV`                 | slurm runtime: `NAME=VALUE` pairs exported inside each driver job | empty |
| `ASSAY_RUN_TIMEOUT_S`           | caps the suite `timeout_s` when smaller        | `300`                            |
| `ASSAY_LOG_LEVEL`               | runner log level (`DEBUG`, `INFO`, ...)        | `INFO`                           |
| `ASSAY_DASK_NWORKERS`           | dask workers for the stats cluster (int > 0)   | unset → all available cores      |
| `ASSAY_BASELINE_ROOT_DIR`       | baselines live at `<root>/<ulid>/`; absent ones are synced there from the store at session start (and by `fetch`); unset means read-only from the cwd, nothing is synced. An entry pinning the all-zero placeholder ULID is "not published yet": never synced, the comparison test skips, `publish-baselines` fills it in | unset → current working directory |
| `ASSAY_ENABLE_BASELINE_COMPARISONS` | global switch; `false` skips comparison tests | `true`                          |
| `ASSAY_BASELINE_STORE`          | where `publish-baselines` writes `<store>/<ulid>/` (append-only): an `s3://bucket/prefix` (never a bucket root) or a local directory | `s3://ufs-chem/baselines` |
| `ASSAY_OUTPUT_STORE`            | where `upload-output` / `--upload-output` copy a run's whole output root as `<store>/<run_id>/` (resumable): an `s3://bucket/prefix` or a local directory | `s3://ufs-chem/assay-output` |
| `ASSAY_BASELINE_ATOL`           | absolute data tolerance over every comparison's own `atol` (`--baseline-atol` wins) | unset → the entries' values |
| `ASSAY_BASELINE_RTOL`           | relative data tolerance (× \|baseline\|) over every comparison's own `rtol` (`--baseline-rtol` wins) | unset → the entries' values |
| `ASSAY_INPUT_SEARCH_PATH`       | colon-separated dirs `fetch` searches recursively, by basename, to link source-less (`url: null`) inputs into the data directory | unset → absent ones fail |
| `ASSAY_CARTOPY_DATA_DIR`        | where cartopy downloads the plots' Natural Earth coastlines and borders (the run config's `data.cartopy_dir`, `<root_dir>/cartopy` by default) | unset → cartopy's own (`$XDG_DATA_HOME/cartopy`, else `~/.local/share/cartopy`) |
| `ASSAY_CONFIG_SEARCH_PATH`      | prepended to relative `config_path` values     | unset                            |
| `ASSAY_SUITE_CONFIG_SEARCH_PATH` | colon-separated dirs searched recursively for `--suite-config` selection | unset → built-in suite dir only |

The CECE adapter:

| Env var                         | Meaning                                        | Default                          |
|---------------------------------|------------------------------------------------|----------------------------------|
| `CECE_ROOT_DIR`                 | host CECE checkout root, mounted at `/work` under docker | unset — required to run the driver |
| `CECE_DOCKER_IMAGE`             | container image (docker runtime)               | `cece/cece-dev`                  |
| `CECE_DRIVER_PATH`              | driver path, relative to the checkout          | `./build/cece_standalone_driver` |
| `CECE_MODULEFILE`               | modulefile each driver job loads before the driver (slurm runtime; recorded in `run.yaml`) | unset |
| `CECE_MODULEPATH`               | colon-separated dirs each driver job `module use`s before the load | unset → `<checkout>/modulefiles` |
| `CECE_MODULE_ENV`               | `NAME=VALUE` pairs exported in each driver job before the load (what the modulefile reads) | empty |

The MONETIO adapter:

| Env var                         | Meaning                                        | Default                          |
|---------------------------------|------------------------------------------------|----------------------------------|
| `MONETIO_ROOT_DIR`              | host `monetio` checkout root                   | unset — required to run the CLI  |
| `MONETIO_DOCKER_IMAGE`          | container image (docker runtime)               | `monetio/monetio-dev`            |
| `MONETIO_DRIVER_PATH`           | CLI executable path, relative to the checkout  | `.venv/bin/monetio`              |
| `MONETIO_MODULEFILE`            | modulefile each driver job loads before the CLI (slurm runtime; recorded in `run.yaml`) | unset |
| `MONETIO_MODULEPATH`            | colon-separated dirs each driver job `module use`s before the load | unset → `<checkout>/modulefiles` |
| `MONETIO_MODULE_ENV`            | `NAME=VALUE` pairs exported in each driver job before the load | empty |

There is no AWS table: the AWS CLI's own `~/.aws/credentials`,
`~/.aws/config`, and `AWS_*` variables apply unchanged to
[S3 data sync](#s3-data-sync), and the live test's bucket is fixed at
`arn:aws:s3:::ufs-chem`, not a variable.

## Super Intelligence (SI) Disclosure

Portions of this code were generated/augmented using agentic SI models and has been reviewed and validated consistent with NOAA's [SI Statement](https://www.noaa.gov/nao-216-128-artificial-intelligence-in-noaa).
