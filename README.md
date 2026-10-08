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
checkout, data directory — lives in an application adapter; CECE is the
shipped one (`application: cece`, the default in every suite and run
config), and a session runs one application. Configurations the harness
carries live under `src/tests/config/<application>/<configuration>/`, each
beside the suites that sweep it. Design rationale lives in
[design/design.md](design/design.md).

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
                              #   installs the `ufs-chem-assay` command (editable)
uv run pre-commit install     # ruff check/format + mypy + vulture on every commit, plus
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
use the `ASSAY_` prefix (the older `CECE_` spellings still work as
fallbacks); an application's own settings keep its prefix (`CECE_ROOT_DIR`,
`CECE_MODULEFILE`, ...). There is no command-line flag for the checkout
root: the variable, `.env`, or the run config (below) supplies it.

## Running

```sh
uv run pytest                      # everything: integration + runner harness tests
uv run pytest --application=cece   # only this application's suites (the default suite when
                                   #   no --suite-config is given); a session runs one application
uv run pytest -vs                  # show each driver's output as it runs
uv run pytest -x                   # fail fast: stop at the first failure
uv run pytest -k map-consd         # run a subset by combo name
uv run pytest --combo-clean-root   # delete an existing output root first

uv run pytest src/tests/ufs_chem_assay                  # harness only: fast, no docker
uv run pytest src/tests/ufs_chem_assay/applications/cece  # the CECE adapter's tests only
uv run pytest src/tests/test_driver_combos.py  # integration only (real docker)

uv run mypy                        # type checking (all of src/; zero errors expected)
uv run pre-commit run --all-files  # everything the commit hook runs: ruff check/format,
                                   #   mypy, vulture (dead code), yamlfix/yamllint, whitespace

# everything except driver execution (no docker needed); all combo tests skip
uv run pytest src/tests/test_driver_combos.py --dry-run

# stage the inputs the selected suites declare (inputs:) into <CECE_ROOT_DIR>/data —
# a present file with the declared sha256 is skipped without touching the network;
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
an optional per-entry `atol`, and a `plot` switch for bias plots; structure
and attributes exact, data bit-for-bit or within `atol`; RMSE and
difference statistics recorded per file x variable in
`<combo_id>-stats-comparison.csv`, concatenated to `stats-comparison.csv`
at the root; bias maps + GIF render at session end into `plots-baselines/`
on a suite-wide symmetric color scale; unselected combinations skip) — with the
driver running once per combination. If the driver run fails, its
`test_driver_execution` fails and that combination's assertion tests are
skipped with a `driver run failed: ...` reason.

Options:

- `--application=NAME` — run only suites of this application (registry
  name; `cece` today), overriding `ASSAY_APPLICATION`. Without it the
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
  a download replaces anything), the per-combination timeout
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
- Inputs are never downloaded by the session. When driver execution is
  coming (no `--dry-run`) and a selected suite's declared input is absent
  from the checkout's `data/`, collection fails with a usage error naming
  the missing files and the `ufs-chem-assay fetch --suite-config=…`
  command that stages them. `run.yaml` records every declared input as
  found at session start (path, source, declared `sha256`, size or null).

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
`baseline.yaml` manifest beside the files (application, the application
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
call. With `ASSAY_BASELINE_ROOT_DIR` set, each published directory is also
copied to `<root>/<ulid>/`, so the very next local run compares against it.
A re-run of the same output root resumes: a directory whose `baseline.yaml`
names the ULID already in the store is not uploaded again, only the suite
is repointed. `pytest --publish-baselines` (the run config's
`harness.publish_baselines: true`) does the same at session end; dry-run
the output root with the subcommand afterwards if you want to see the plan.
Credentials are the AWS CLI's (publishing needs `s3:ListBucket` on the
bucket and `s3:PutObject` under `baselines/`); nothing is read from `.env`.

## Configuring a run: `ufs-chem-assay run`

A whole run — the application's source and build, its input data, and
the pytest session — is described by **one YAML run config**, and every
setting a run reads has a key in it: the `harness:` section mirrors every
harness-wide setting (`ASSAY_*`) plus the pytest options, and each
application's section under `applications:` mirrors its own settings
(`CECE_*`) plus how to obtain and build it. The shipped templates
(`config/local.yaml` for a laptop, `config/ursa.yaml` for Ursa) list every
key and run as-is from a checkout laid out like the runbook:

```yaml
platform: ursa
applications:            # one section per application in the run
  cece:
    git_url: git@github.com:ufs-community/CECE.git
    ref: develop
    clone_dir:           # null: <root_dir>/CECE (CECE_ROOT_DIR)
    modulefile: cece_ursa.intelllvm
    ...
harness:
  suite_config: simple-maccity-suite.yaml
  output_root: ufs-chem-assay-output
  ...
slurm: ...
```

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
uv run ufs-chem-assay run --config-file=config/ursa.yaml --dry-run   # render only
uv run ufs-chem-assay run --config-file=config/ursa.yaml             # clone, build,
                                                                     #   data, harness
uv run ufs-chem-assay run --config-file=config/ursa.yaml --stage harness \
  --override harness:suite_config=exhaustive-maccity-run-only-suite.yaml harness:pytest_args='[-x]' \
             applications:cece:ref=develop slurm:qos=batch
```

Every invocation, dry runs included, writes the effective configuration
— the file with every override merged in, as validated — to
`<root_dir>/scripts/run-config.yaml` beside the rendered scripts; it
re-runs the same configuration with no override list, and it is the way
to check that an override landed.

Stages (`--stage`, repeatable): `source` (clone or fast-forward the
checkout), `build` (modules + cmake, or the application's container build
script locally), `data` (`ufs-chem-assay fetch` for the inputs the
configured `suite_config` selects, under the same exports as the harness
stage, plus the cartopy cache), `harness` (the pytest session). Running CECE's own tests is a
separate task (issue #9). Each renders to
`<root_dir>/scripts/<NN>-<stage>-<application>.sh` and runs with bash
where the CLI runs; logs land in `<root_dir>/logs/`. A run config naming
several applications is a comprehensive run: the stages render per
application in stage-major order (every `source`, then every `build`,
...) and the harness stage runs one pytest session per application, each
under `<output_root>/<application>`; `--application NAME` (repeatable)
narrows such a run. Run the harness stage under `tmux` — it lives as
long as the suite. The CLI never deletes anything except the harness
output root (`clean_root`), and never mutates an existing checkout
without `update_source`. `baselines.store` (`ASSAY_BASELINE_STORE`),
`harness.publish_baselines`, and `harness.no_suite_update` carry the
[publishing](#publishing-baselines) options into the harness stage.

## Running on RDHPC (Ursa)

RDHPCS machines have no docker, so the target driver is built natively
against the application's own modulefiles (`<checkout>/modulefiles/cece_ursa.*.lua`
for CECE), and the harness runs on a **login node** submitting **one
Slurm job per driver call** — the **slurm runtime**, selected by `ASSAY_RUNTIME=slurm` (the
default once `ASSAY_PLATFORM` is anything but `local`; the platform is
detected from the hostname and overridable). Each job is
a rendered script, `<combo_id>.sbatch`, kept beside the combo's `.yaml`
and `.out` so a failed job is reproducible by hand: `#SBATCH` directives
from `ASSAY_SBATCH_ARGS` (account, QOS, partition, cpus) and the suite's
`timeout_s` rounded up to whole minutes, the `CECE_MODULEFILE` load,
the `ASSAY_JOB_ENV` exports, and the driver behind `srun --ntasks=1`
(MPI inside a batch job needs Slurm's PMI endpoint). Two environments stay apart on
purpose: the harness venv never sees the modulefile (spack-stack's
`PYTHONPATH` would shadow its numpy), the driver always does. Analysis
runs in the pytest process, so cap `ASSAY_DASK_NWORKERS` on a login node.
`ASSAY_RUNTIME=native` runs the driver as a direct host process with
`ASSAY_LAUNCHER` as an optional prefix — for a docker-less machine whose
shell already suits both the harness and the driver; not Ursa, where the
two environments conflict.

The run config above assembles all of that; the same steps by hand are
in [docs/ursa-runbook.md](docs/ursa-runbook.md).

## CI and releases

Commit messages (and PR titles) must follow
[Conventional Commits](https://www.conventionalcommits.org/) — `feat: …`,
`fix: …`, etc. Locally the `commit-msg` pre-commit hook rejects a
non-conforming message at commit time; in CI the PR title is checked with
the same hook (squash-merge subjects come from PR titles, and
semantic-release parses them to compute versions).

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
  run.yaml                       # run manifest: session ULID; the application and
                                 #   its checkout's HEAD SHA (application_commit; null
                                 #   only when no checkout is configured — a configured
                                 #   root that is not a git checkout fails the session
                                 #   at start); the harness's own harness_version and
                                 #   harness_commit (HEAD, `-dirty` when edited; null
                                 #   when not run from a git checkout); platform,
                                 #   runtime, modulefile; every resolved suite in
                                 #   selection order (one-element list when single);
                                 #   and every declared input as found at session
                                 #   start (suite, url, path, sha256, bytes or null)
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
same lat/lon rule as the statistics. All plots of a variable share
one **exact suite-wide min/max color scale** derived from the descriptive
statistics — so plotting requires `compute_descriptive_stats`. First-time
boundary rendering downloads Natural Earth coastline/border data; offline,
plots degrade to data-only maps with a warning.

Stats CSV columns: `run_id`, `application`, `suite` (the suite's unique
`name` from its yaml, e.g. `simple-maccity`), identity (`combo_id`, `combo`, `file`,
`variable`), the file's
timestamp from its NetCDF time coordinate as `time` (ISO-8601) plus part
columns `year`/`month`/`day`/`hour`/`minute`/`second` for easy time
summaries (null if the file has no time coordinate), and the nan-aware
statistics (`count`, `sum`, `mean`, `std`, `min`, `max`, `median`).

## Environment variables

Every variable can also be set (lowercase works) in a gitignored `.env`
file at the repo root, read when pytest runs from there; real environment
variables override `.env`. AWS credentials are not harness settings at
all — the AWS CLI reads its own configuration (see
[S3 data sync](#s3-data-sync)). Two namespaces: **harness-wide** settings under
`ASSAY_*` (the pre-adapter `CECE_*` spellings are accepted as fallbacks
and go away when the second application adapter lands; `ASSAY_X` wins over
`CECE_X` when both are set), and each **application's** settings under its
own prefix — `CECE_*` for the CECE adapter.

Harness-wide:

| Env var                         | Meaning                                        | Default                          |
|---------------------------------|------------------------------------------------|----------------------------------|
| `ASSAY_APPLICATION`             | the application the session runs (`--application` overrides) | unset → inferred from the selected suites |
| `ASSAY_PLATFORM`                | machine the harness runs on (`local`, `ursa`)  | detected from the hostname, else `local` |
| `ASSAY_RUNTIME`                 | how the driver is spawned (`docker`, `native`, `slurm`) | `docker` on `local`, `slurm` elsewhere |
| `ASSAY_LAUNCHER`                | command prefix for native driver runs (e.g. `srun --ntasks=1`) | empty (run directly) |
| `ASSAY_SBATCH_ARGS`             | slurm runtime: sbatch options per driver job (`-A … -q … -p … -N 1 -n 1 -c …`) | empty |
| `ASSAY_SLURM_QUEUE_WAIT_S`      | slurm runtime: seconds a driver job may wait in the queue before the harness cancels it (the run config's `slurm.queue_wait_s`) | `3600` |
| `ASSAY_JOB_ENV`                 | slurm runtime: `NAME=VALUE` pairs exported inside each driver job | empty |
| `ASSAY_RUN_TIMEOUT_S`           | caps the suite `timeout_s` when smaller        | `300`                            |
| `ASSAY_LOG_LEVEL`               | runner log level (`DEBUG`, `INFO`, ...)        | `INFO`                           |
| `ASSAY_DASK_NWORKERS`           | dask workers for the stats cluster (int > 0)   | unset → all available cores      |
| `ASSAY_BASELINE_ROOT_DIR`       | baselines live at `<root>/<ulid>/`             | unset → current working directory |
| `ASSAY_ENABLE_BASELINE_COMPARISONS` | global switch; `false` skips comparison tests | `true`                          |
| `ASSAY_BASELINE_STORE`          | the baselines' S3 prefix (`s3://bucket/prefix`, never a bucket root); `publish-baselines` writes `<store>/<ulid>/` there | `s3://ufs-chem/baselines` |
| `ASSAY_CONFIG_SEARCH_PATH`      | prepended to relative `config_path` values     | unset                            |
| `ASSAY_SUITE_CONFIG_SEARCH_PATH` | colon-separated dirs searched recursively for `--suite-config` selection | unset → built-in suite dir only |

The CECE adapter:

| Env var                         | Meaning                                        | Default                          |
|---------------------------------|------------------------------------------------|----------------------------------|
| `CECE_ROOT_DIR`                 | host CECE checkout root, mounted at `/work` under docker | unset — required to run the driver |
| `CECE_DOCKER_IMAGE`             | container image (docker runtime)               | `cece/cece-dev`                  |
| `CECE_DRIVER_PATH`              | driver path, relative to the checkout          | `./build/cece_standalone_driver` |
| `CECE_MODULEFILE`               | modulefile each driver job loads before the driver (slurm runtime; recorded in `run.yaml`) | unset |

There is no AWS table: the AWS CLI's own `~/.aws/credentials`,
`~/.aws/config`, and `AWS_*` variables apply unchanged to
[S3 data sync](#s3-data-sync), and the live test's bucket is fixed at
`arn:aws:s3:::ufs-chem`, not a variable.
