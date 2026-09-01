# Spike: rename the repository + plan the CATChem generalization

## Goal

Documentation-only (spike rules): (1) pick a new repository name, (2)
scope the rename, (3) scope what incorporating **CATChem** beside CECE
requires — which pieces of the harness are already generic, which are
CECE-coupled, and which design pattern carries the generalization —
so the follow-on feature docs can be written against a settled shape.
The premise from the notes: CATChem brings its own configuration,
container, and driver, while the core test running, pre-processing,
and post-processing stay shared; more applications may follow, so the
mechanism must be generic, not a two-way switch.

## Pre-spike audit (fresh facts, 2026-09-01)

### The runner's naming is already app-neutral except the repo itself

`pyproject.toml` `name = "combo-test-runner"`, the logger namespace is
`combo_test_runner`, the harness test package is
`src/tests/combo_test_runner/`, CI tags its image
`combo-test-runner:ci`, and the design docs say "combo-test-runner"
throughout. The **only** `cece-`-prefixed identity is the GitHub
repository (`benkozi/cece-combo-test-runner`) and the local clone
directory. The rename (deliverable 2) is therefore cheap and almost
entirely repo-level; the *coupling* to CECE (deliverable 3) is a
separate, much larger surface.

### What CATChem actually is (from the local checkout)

- **Configurable ATmospheric Chemistry**: a NUOPC-compliant chemistry/
  aerosol component in the UFS-Chem family (same family as CECE — see
  the ufs-chem reference), Fortran core, ESMF I/O, **CF-compliant
  NetCDF input/output** (good news for the generic post-processing).
- **Three driver flavors** under `drivers/`: `standalone` (a column
  driver, `standalone_column_driver.F90`, with a YAML config
  template), `nuopc`, and `ccpp`. The standalone column driver is the
  closest analogue to `cece_standalone_driver`.
- **YAML configuration, completely different schema from CECE**:
  top-level `simulation:` (dates, species file, column mode,
  location), `grid:` (levels, pressure/height), `timesteps:`, and
  `process:` (run phases with ordered process lists — emissions,
  chemistry, transport…). Config families ship under `configs/v0` and
  `configs/v1` (`analytical`, `chapman`, `ts1`, `full_configuration`),
  each as YAML + JSON.
- **Its own containers**: `docker/Dockerfile` (ARG
  `CATCHEM_ENABLE_KOKKOS`) on the `noaaepic/ufschem-spack-base-…`
  base, plus published `noaaepic/catchem-ubuntu-gcc-13` images. Mount
  convention **`/opt/project`** — not CECE's `/work`.
- Python tooling (pyproject, `util/` scripts, pre-commit) exists in
  the repo; examples are Fortran API demos, not the shipped-config
  kind CECE has.

### Where the harness is generic today vs CECE-coupled

Generic already (survives multi-app unchanged or nearly so):

- `combos.py` enumeration core: `Dimension` (target/field/tag +
  **apply callables** — already behavior-as-data), cartesian product,
  canonical names, runtime ULIDs.
- Session machinery: `SuiteContext` list, multi-suite parametrization,
  per-suite fixtures via callspec, flat ULID output root, run.yaml,
  test-report, dry-run.
- `resolution.py` selection; `report.py`; `logs.py`; `analysis.py`
  stats and `comparison.py` (operate on NetCDF; fine given CF
  conventions); `plotting.py` *mostly* (assumes lat/lon grids — a
  CATChem **column** run has no map to draw; needs a per-app
  capability flag, not a rewrite).

CECE-coupled (the generalization surface):

- `models/cece_config.py` — the whole driver-config model.
- The **sweep and selector models** in `suite_config.py`
  (`StreamSweep`/`SpeciesEntrySweep`/`CeceDataSweep` + mirrors) —
  they *mirror the CECE config structure* by design.
- In `combos.py`: `_SPECIES_FIELDS`/`_STREAM_FIELDS`, the vdist
  companions, `build_config` (loads `CeceConfig`, redirects
  output/log_file), and `_effective_parameter_rows` (enumerates
  CECE's sweepable fields for combos.csv).
- `assertions.py` derived expectations (file count/filenames from
  CECE `driver:`/`output:` fields; `STANDARD_DIMENSIONS`).
- `runner.py` `build_command` (image, `/work` mount, `driver_path`,
  OMPI env) and `resolution.py`'s `CONTAINER_WORK` constant.
- `settings.py` (the `CECE_` env prefix carries both app-specific
  values *and* harness-wide ones like log level and dask workers),
  `Settings.get_cece_commit_sha`, the `${CECE_ROOT_DIR}` config_path
  token, conftest's root-dir guards, the examples machinery, the
  checked-in suites, and the integration CI workflow.
- `RunManifest.cece_commit` — provenance is currently single-app.

## Deliverable 1 — the name

Scope correction (user direction): the harness will eventually
incorporate **benchmarking**, which sits outside "testing" — the name
should say "UFS-Chem testing, verification, and benchmarking harness".
That retires the first-cut recommendation (`combo-test-runner`
undersells two of the three activities). Revised shortlist, best
first:

- **`assay`** (repo `ufs-chem-assay`) — chemistry's own word for
  exactly this activity: the qualitative and *quantitative* analysis
  of a substance. Testing (does it pass), verification (is it right),
  and benchmarking (how much, how fast) are literally what an assay
  is; the dictionary definition is the requirements sentence.
- **`VERB`** (repo `ufs-chem-verb`) — backronym: Verification,
  Evaluation, Regression, and Benchmarking. Says the trio outright,
  fits the community's backronym tradition (CECE, CATChem, HEMCO),
  four letters. Slightly forced next to assay's natural fit.
- **`crucible`** (repo `ufs-chem-crucible`) — literal chemistry
  vessel + figurative severe trial; minor mindshare collision with
  Atlassian's defunct code-review tool.
- `mettle` ("prove your mettle") and the boring-safe
  `ufs-chem-harness` round out the list.

Structural guidance either way: family scoping lives in the **repo
prefix** (`ufs-chem-…`) while the bare word is the tool/package
identity — if an application outside UFS-Chem ever joins (this
spike's own caveat), the tool name survives and only the repo
description overpromises.

**Decision (2026-09-01, user-selected): the new name is
`ufs-chem-assay`, tool identity `assay`.** Chosen because an *assay*
is chemistry's own term for the qualitative and quantitative analysis
of a substance — which maps one-to-one onto the harness's mission:
testing (does it pass), verification (is it right), and benchmarking
(how much, how fast). The name is chemistry-native rather than
borrowed testing jargon, short and CLI/package-friendly, and the
`ufs-chem-` repo prefix carries the family scoping while the bare
`assay` identity survives any future application beyond UFS-Chem.

## Deliverable 2 — rename scope

Revised with the benchmarking-driven name change: unlike the retired
`combo-test-runner` pick (which matched the internal identity for
free), any new-word name makes the internal rename a real — still
small — code change:

0. **Internal identity sweep**: `pyproject.toml` `name`, the
   `combo_test_runner` logger namespace (`logs.py`), the
   `src/tests/combo_test_runner/` package directory, the CI image tag
   `combo-test-runner:ci`, and doc mentions all move to the chosen
   word. Mechanical, verified by the harness suite and hooks.

Then the repo-level steps as before:

1. **GitHub rename** `cece-combo-test-runner` → `ufs-chem-assay`
   (GitHub redirects old URLs and git remotes, so nothing breaks
   immediately; update the `origin` remote locally anyway).
2. **Local clone directory** rename to match (side effect worth
   knowing: the Claude memory/scratch directories key on the absolute
   repo path and will start fresh at the new path).
3. **Docs**: README title/intro already say "combo-test-runner";
   sweep for any `cece-combo-test-runner` literals in docs and design
   files (currently only incidental mentions).
4. **Nothing else**: pyproject, loggers, CI, workflows, and image
   tags are already on the target name. No `.env`, no settings, no
   Python changes.

## Deliverable 3 — incorporating CATChem

### Design pattern: Strategy — an `Application` adapter behind a registry

One adapter object per supported application, selected per suite;
everything the session machinery needs from "the app" goes through the
adapter's surface, and pydantic **discriminated unions** carry the
app-specific config/sweep schemas inside the suite file:

```yaml
# a CATChem suite
name: catchem-chapman
application: catchem            # NEW discriminator; default "cece"
config_path: ${CATCHEM_ROOT_DIR}/configs/v1/chapman/config.yaml
timeout_s: 120
sweep: ...                      # schema chosen by `application`
```

The adapter surface (a protocol/ABC; each method exists today as
CECE-specific code listed in the audit):

- `name` — registry key and the `application:` discriminator value.
- `settings` — per-app namespace: `root_dir`, `docker_image`,
  `driver_path`/invocation, **container mount point** (`/work` vs
  `/opt/project`), commit-SHA provenance (generalizing
  `get_cece_commit_sha`).
- `config model` — load/validate/serialize the driver config
  (`CeceConfig` ↔ a new `CatchemConfig`; strict pydantic, same
  posture).
- `sweep + selector models` — the app-shaped sweep schema and its
  baseline-selector mirror (the discriminated-union payload).
- `dimensions(sweep, base_config)` — produce the generic
  `(Dimension, values)` list; the enumeration core stays shared.
- `build_config(combo, output_dir, config_path)` — apply values and
  redirect output/log into the combo directory.
- `effective_parameters(combo, config)` — the combos.csv rows.
- `derived assertions` — expected file count/filenames/standard
  dimensions (CATChem's CF column output will define its own).
- `capabilities` — e.g. `spatial_plots: bool` (a column model draws
  no maps), examples support, baseline support.

Registry: an in-repo `dict[str, Application]` — two entries. Entry
points/plugin discovery is a *later* hardening if third-party apps
ever materialize; it changes nothing about the adapter surface.

Alternatives considered and rejected:

- **Separate repo per application** — duplicates the ~80% that is
  already generic (session machinery, stats, plots, comparison,
  reporting, CI patterns); the whole point of the notes is sharing it.
- **Template-method inheritance** (an abstract session base class per
  app) — same information, worse fit: the variability is in *data
  schemas* (configs, sweeps) more than in *steps*, which is exactly
  what discriminated unions + a small adapter express naturally.
- **If/else on an app enum inside the existing modules** — the
  two-way switch the notes preempt; the third app would pay for it.

### What stays byte-identical for CECE

`application:` defaults to `cece`, so every checked-in suite file,
test id, artifact, and CI job is unchanged through the refactor phase
— the same "nothing exceptional" bar as the multi-suite arc. The
`CECE_*` environment variables keep working verbatim.

### Open questions (answers shape the feature docs, not this spike)

1. **Which CATChem driver is the target** — the standalone column
   driver (closest analogue, column-mode output) or the NUOPC driver?
2. **Which config family/version** (`configs/v1/chapman` etc.) is the
   first suite's base config?
3. **Container strategy** — pull the published `noaaepic/catchem-…`
   image or build `docker/Dockerfile` locally/CI (the CECE CI pattern
   would transfer either way)?
4. **What are CATChem's sweep dimensions of interest** — process
   toggles / run-phase composition / scheme choices? (Determines the
   sweep model; CECE's dimensions were enum-valued fields, CATChem's
   may be list-composition, which the `Dimension.apply` design
   handles but the sweep schema must express.)
5. **Harness-wide env prefix** — today log level, dask workers, and
   suite search paths live under `CECE_*`. Migrate harness-wide vars
   to a neutral prefix (with `CECE_*` fallback for compat) or leave
   them?
6. **run.yaml provenance** — one commit field per app adapter
   (`cece_commit` → per-application map) so multi-app sessions record
   every checkout?

### Phased plan (each phase its own feature doc + arc)

- **Phase A — rename** (deliverable 2; an afternoon).
- **Phase B — adapter extraction, CECE-only**: introduce the
  `Application` protocol + registry + `application:` discriminator
  and move the CECE-specific code behind it. Pure refactor, zero
  behavior change, verified by the existing 217-test harness plus
  byte-stable dry-run artifacts.
- **Phase C — CATChem MVP**: `CATCHEM_*` settings, container/driver
  command, a `CatchemConfig` covering the chosen config family, one
  **sweep-less smoke suite** (the examples-as-suites pattern: base
  config as the single `base` combo), execution + stats through the
  shared pipeline; spatial plots gated off via the capability flag if
  the column driver is chosen.
- **Phase D — depth**: CATChem sweep dimensions + selectors, derived
  assertions, baselines, a CATChem CI job (clone/build/cache pattern
  transfers from the simple-maccity job).

## Constraints

- Spike: **no code changes**; this document is the deliverable.
- Never commit — the user commits.
- The follow-on feature docs inherit the standing rules (TDD,
  pydantic + Field descriptions, README/design.md updates).

## Findings summary

1. **Name — decided: `ufs-chem-assay`** (tool identity `assay`) — the
   chemistry term whose meaning is exactly testing + verification +
   quantitative benchmarking; `VERB` and `crucible` were the
   runners-up.
2. **Rename**: repo rename plus a small internal identity sweep
   (pyproject name, logger namespace, harness test package, CI image
   tag) — mechanical, harness-verified.
3. **Pattern**: Strategy — a per-application adapter behind a
   registry, `application:` as a pydantic discriminator selecting
   app-shaped config/sweep schemas; enumeration, session machinery,
   stats/plots/comparison, and reporting stay shared. CECE remains
   byte-identical through the extraction; CATChem lands as the second
   adapter with a sweep-less smoke suite first. Six open questions
   listed for the user before the feature docs are written.

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

- we need to come up with a better name for the software repository
- i want to expand the test harness to incorporate catchem in addition to cece
- catchem will have its own configuration, container, etc. however, the core test running structure, pre-processing, and post-processing can be generic between the two software packages
- it's also possible that other applications will become part of the harness, so a generic system to handle other configurations will be required
- the purpose of this spike is:
  1. identify a new repository name
  2. scope out what will be required to rename it
  3. scope out what would be required to incorporate catchem in terms of abstractions and generalizations. which design pattern will we use?

## references

- ufs-chem: https://csl.noaa.gov/groups/csl4/modeldata/ufs-chem/
- catchem: /Users/bkoziol/sandbox/git-benkozi/CATChem

## conversational updates

- 2026-09-01: **benchmarking widens the naming brief** (user) — the
  harness will incorporate benchmarking, commonly considered outside
  "testing", so the name must say "UFS-Chem testing, verification,
  and benchmarking harness". The first-cut `combo-test-runner`
  recommendation is retired; revised shortlist is `assay`
  (recommended), `VERB` (backronym: Verification, Evaluation,
  Regression, Benchmarking), `crucible`, `mettle`,
  `ufs-chem-harness`. The rename scope grows a small internal
  identity sweep (pyproject/logger/test-package/CI-tag), since the
  new word no longer matches the existing internal naming for free.
- 2026-09-01: **name decided: `ufs-chem-assay`** (user) — chosen for
  the one-to-one fit between the chemistry meaning of *assay*
  (qualitative + quantitative analysis) and the harness's testing /
  verification / benchmarking mission; tool and package identity is
  the bare `assay`, with the `ufs-chem-` prefix scoping the repo.
