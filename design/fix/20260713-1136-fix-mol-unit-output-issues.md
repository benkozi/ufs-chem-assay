# Fix: driver emits wrong units (`mol mol-1`) on output fields

## Bug, located

`src/driver/cece_standalone_writer.cpp` (lines ~265–273) generates the AMIO
output manifest and **hardcodes, for every output field**:

```
units: "mol mol-1"
long_name: "mole_fraction_of_<name>_in_air"
```

The output fields are stacked **emission fluxes** (the MACCity input
variable carries `kg/m2/s`), so both attributes are factually wrong — this
is exactly what `test_species_units` has been failing on
(`expected 'kg m-2 s-1', found 'mol mol-1'`, per
`design/feat/20260713-1049-expected-output-units.md`).

**Finding vs. the original note**: no `extern/helm/libs/conf` ↔
`extern/helm/libs/amio` changes are needed. AMIO already carries arbitrary
per-variable attributes from the manifest (parsed by helm's `conf`) through
to the NetCDF — the proof is that the wrong hardcoded values arrive intact
in the output. The fix is entirely CECE-side: the config parser and the
writer's manifest generation.

## Fix design

### Config-driven field attributes

The CECE yaml `output:` block gains an optional per-field attribute map:

```yaml
output:
  enabled: true
  directory: ...
  fields: [co]
  field_attributes:
    co:
      units: kg m-2 s-1
      long_name: carbon_monoxide_emission_flux
```

- `CeceOutputConfig` (include/cece/cece_config.hpp) gains
  `std::map<std::string, std::map<std::string, std::string>>
  field_attributes;` parsed in `src/core/cece_config_parser.cpp` from the
  `output.field_attributes` node (string storage is sufficient — AMIO
  types attributes by content; see typed values below).
- The writer's manifest loop emits the configured attributes for each field.
  **When a field has no configured attributes, it gets none** — absence over
  fabrication; a wrong default is precisely the bug being fixed, and the
  units assertion's `null` semantics can verify absence.
- **Exception: `coordinates`.** It is structural (CF auxiliary-coordinate
  listing), so every field gets `coordinates: "time lev lat lon"` by default
  — matching the written field shape `[time, lev, lat, lon]` — but a
  user-supplied `coordinates` key in the field's `field_attributes` block
  **overrides** the default (it is just another attribute; the writer skips
  the default when the user provided one).
- The hardcoded `units`/`long_name` lines are deleted.

### Typed attribute values: string, integer, double

*(Status: implemented, verified, then **backed out** — the AMIO/helm and
runner changes were judged too fundamental for this fix's scope. The
investigation findings below remain valid and are the basis for the
follow-up. The `missing_value` whitelist exclusion is tracked as a
**follow-up issue** together with the type-inference work.)*

Nothing restricts user-provided
attributes to strings: NetCDF attributes are typed, and a configured
`missing_value: -999` or `scale_factor: 1.5` must land as a numeric
attribute, not the text `"-999"`. The first cut stored and emitted strings
only. An earlier draft of this revision proposed a CECE-side
`std::variant` value type with yaml-tag type inference and typed manifest
emission — superseded by the investigation below, which found AMIO already
does the typing.

- **Investigation resolved** (AMIO source read + empirical driver run):
  1. **AMIO already emits typed attributes, for every attribute name**, by
     **sniffing the string content** (`parse_attr_value` →
     `AttrValue{text, number, is_numeric, is_integer}`): an integral
     literal becomes `NC_INT64`, a real becomes `NC_DOUBLE`, `_FillValue`
     is retyped to the variable's own type per CF, everything else is
     text. Verified end to end: a configured `scale_factor: 1.5` arrives
     in the NetCDF as a genuine double **with today's string-only CECE
     implementation** — the manifest's quoting is irrelevant, so the
     CECE-side `AttributeValue` variant and yaml-tag inference above are
     **unnecessary for typing** and can be dropped from this design.
  2. **Quoting cannot force text**: `AttrValue` keeps no quote
     information, so a string attribute whose content looks numeric (e.g.
     `version: "2.0"`) would be written as `NC_DOUBLE`. Honoring quoting
     would require helm `conf`/AMIO changes.
  3. **The real gap is a key whitelist**: helm `conf` cannot iterate
     sub-map keys, so AMIO reads only a hardcoded list of known CF/UGRID
     attribute names (`units`, `long_name`, `standard_name`, `_FillValue`,
     `coordinates`, `cell_methods`, `cf_role`, `mesh`, `location`,
     `topology_dimension`, `scale_factor`, `add_offset`, `valid_min`,
     `valid_max`, `valid_range` — `var_attributes.cpp`). Anything else —
     including, ironically, CF's `missing_value` — is **silently
     dropped** (verified empirically). The original note's conf↔amio
     intuition was right after all, for this reason: supporting arbitrary
     attribute names means either key iteration in `conf` or an explicit
     attribute-name listing in the manifest schema.

  **Revised scope for the typed-attributes work**: no CECE-side type
  plumbing; instead (a) document the sniffing semantics (numeric-looking
  values become typed numerics), (b) add the C++ type-asserting regression
  tests below against whitelisted keys, (c) add `missing_value` support
  (below), and (d) **a committed follow-up feature** (its own design note,
  not this fix) properly repairs type inference: quote-aware typing so a
  quoted scalar stays text, and lifting the key whitelist (`conf` key
  iteration or an explicit attribute-name listing in the manifest schema).

### `missing_value` support (backed out; follow-up issue)

*(Status: implemented red→green as designed — whitelist addition plus
variable-type retyping in both drivers, all 289 CECE tests green — then
**backed out** with the typed-attributes work. The design below stands as
the reference for the follow-up issue tracking the `missing_value`
exclusion.)*

The whitelist gap bites hardest on `missing_value` — a standard CF
attribute that today is **silently dropped**. Minimal, additive support:

- **Whitelist**: add `"missing_value"` to `known_var_keys` in
  `extern/helm/libs/amio/src/drivers/common/var_attributes.cpp`. This is
  the first change inside `extern/helm` for this workstream — deliberately
  a one-token addition, not the full key-iteration rework.
- **Typing**: CF expects `missing_value`, like `_FillValue`, to carry the
  **variable's own type**. AMIO's netCDF driver special-cases only
  `_FillValue` for that retyping; the special case is widened to cover
  `missing_value` as well (same `switch` in
  `netcdf_driver.cpp:nc_write_attributes`; the NCZarr fallback driver
  shares the pattern and is updated for consistency).
- **C++ test change (red → green, per the repo's TDD pattern)**: a new
  case in `tests/test_standalone_writer_attributes.cpp` configures
  `missing_value: -999` on `co` and asserts the attribute is **present**,
  carries the **variable's type** (`NC_DOUBLE` — fields are written as
  F64), and equals `-999.0`. Written and run before the AMIO change, it is
  RED (the attribute is dropped — today's verified behavior); the
  whitelist + retyping change turns it green.
- **C++ regression test additions**: configure one integer and one double
  attribute **using whitelisted keys** (e.g. `valid_min: -999`,
  `scale_factor: 1.5`); read back with `nc_inq_atttype` + the typed getters
  and assert **both the NetCDF type and the value** (the type assertion is
  the point — value equality alone would not catch stringification).
- **Runner-side ripple**: the pydantic mirror widens via a **named, reusable
  type alias** in `models/base.py` (next to `StrictModel`, the shared model
  infrastructure):

  ```python
  # models/base.py
  NcAttrType = str | int | float   # a NetCDF attribute value as configured/read
  ```

  `Output.field_attributes` becomes
  `dict[str, dict[str, NcAttrType]] | None` (today's `str`-only model
  rejects numeric yaml scalars outright). The alias is the single place the
  attribute value-type is defined — future users (e.g. typed
  `test_species_attributes` expectations, stats/plot attribute handling)
  reuse it rather than restating the union.
  Noted consequence for the separate attribute-assertion feature
  (`test_species_attributes`): found attributes are compared **stringified**,
  so numeric attributes compare via their string form (e.g. `-999`,
  `1.5`) — formatting-sensitive; typed assertion comparison (reusing
  `NcAttrType`) is future work there, not here.

### Units value for the checked-in config

The MACCity input variable says `kg/m2/s`; the checked-in
`simple-maccity.yaml` will configure the CF-canonical spelling
**`kg m-2 s-1`** (matching what the suite assertion already expects — exact
string philosophy: the config states it, the driver echoes it, the
assertion verifies the echo).

### Runner-side ripple (forced by StrictModel)

- `src/models/cece_config.py`: the `Output` model gains
  `field_attributes: dict[str, dict[str, str]] | None = None` — without it,
  the new key would be rejected at load.
- `src/tests/config/cece/simple-maccity.yaml` sets
  `field_attributes.co.units: kg m-2 s-1` (and an honest `long_name`).
- Expected outcome: the three `test_species_units` tests **flip green**, the
  same automatic flip the filename tests demonstrated. All other tests
  unchanged. The suite-file "known bug" comment for units is removed.
- Harness: model round-trip of `field_attributes` (StrictModel accepts the
  new key; generated combo yamls carry it through `build_config`).
- Driver-side tests: check `tests/` for any writer/manifest expectations
  pinned to the old hardcode and update alongside.

### C++ regression test in CECE (red before the fix, green after)

A new GTest executable, `tests/test_standalone_writer_attributes.cpp`
(registered in `CMakeLists.txt` + ctest like the existing
`test_driver_configuration`), exercising the real writer end to end:
construct `CeceStandaloneWriter`, `Initialize`, `WriteTimeStep` with a small
field, `Finalize`, then read the produced NetCDF back (netcdf-c API,
available in the image) and assert on the variable's attributes. Two tests,
sequenced deliberately:

1. **`DefaultConfigEmitsNoFabricatedAttributes`** — written and run
   **before** the fix, against the unchanged `CeceOutputConfig` (so it
   compiles pre-fix): a field with no configured attributes must have **no
   `units` attribute**. Pre-fix this is **RED** — the writer stamps
   `mol mol-1` on everything. The fix turns it green.
2. **`ConfiguredFieldAttributesReachTheOutput`** — lands *with* the fix (it
   needs the new `field_attributes` member to compile): configure
   `co -> {units: "kg m-2 s-1", long_name: ...}`, assert both arrive in the
   NetCDF verbatim. This is the permanent regression lock for the feature.

The demonstrated pre-fix failure of test 1 is part of the acceptance
evidence, per the TDD requirement.

### Build-and-test script: `scripts/build-and-test-container.py`

`setup.sh` stays untouched — its job is setting up a development
environment, nothing more. A new **Python** script (argparse +
`subprocess.check_call` throughout) lives in the existing `scripts/` directory:

```sh
./scripts/build-and-test-container.py               # build + test (the default)
./scripts/build-and-test-container.py --clean       # remove build/ and cmake-build-debug/ first
./scripts/build-and-test-container.py --no-build    # test only
./scripts/build-and-test-container.py --no-test     # build only
./scripts/build-and-test-container.py --mount /work # container-side mount point (default)
./scripts/build-and-test-container.py --image cece/cece-dev  # container image (default)
```

- **Host repo root is derived from the script's own location, never the
  cwd**: `Path(__file__).resolve().parent.parent` (the script lives in
  `scripts/`, directly under the repo root) — the same pattern the runner's
  `settings.py` uses. The script works identically invoked from anywhere;
  no assumption about executing from `scripts/` or the repo root, and no git
  dependency.
- `--mount` (default `/work`): the **container-side** path the host repo
  root is mounted at; all in-container paths (`<mount>/build`, ctest
  invocations) derive from it.
- `--image` (default `cece/cece-dev`): the container image — the
  default matches the image `setup.sh` builds/uses, so the script runs in
  the same environment as interactive development without configuration.
- `--clean` (off by default): removes the `build/` **and**
  `cmake-build-debug/` directories before anything else.
- `--no-build` / `--no-test`: independently disable a phase; both phases
  run by default.
- `--test-filter STRING`: run a single C++ test or subset via
  `ctest -R STRING` (regex/substring match against registered test names).
  Without a filter, **the entire registered CECE test suite runs** — all
  `gtest_discover_tests` cases plus other `add_test` entries.
- **Container lifecycle**: each containerized step is its own
  `docker run --rm` against `cece/cece-dev` with
  `-v <derived-host-root>:<mount> -w <mount>` and the standard env
  (mirroring `setup.sh`'s invocation, without modifying it) — spun up and
  removed per execution, no reuse.
- **Build phase** (in container): CMake configure into `<mount>/build` when
  needed (always after `--clean`), then build the default `all` target —
  the driver and every test executable.
- **Test phase**: the full CECE test suite runs via
  `ctest --test-dir <mount>/build --output-on-failure` **in the container**,
  where the toolchain and netcdf-c live (`-R` applies `--test-filter`).
  **The harness suite is deliberately out of the script's scope**
  — it is run separately on the host (`uv run pytest` in
  this repository), where it orchestrates its own per-combo
  containers. The script is the C++ build/test loop, nothing more.
- `check_call` semantics give the script its exit contract for free: the
  first failing step aborts with a nonzero exit — the one-command
  verification loop for driver fixes like this one (`README.md` gains it).

(Python per the requirement; stdlib-only — argparse/subprocess/shutil/
logging — so it runs with any `python3`, no uv environment needed. Script
output goes through python `logging`, deliberately minimal: `basicConfig`
with a timestamped format, everything at INFO for now.)

## Acceptance criteria

- **Pre-fix red demonstrated**: the new C++ test
  `DefaultConfigEmitsNoFabricatedAttributes` fails against the unfixed
  writer (finds `mol mol-1`), establishing the TDD baseline.
- Post-fix, `./scripts/build-and-test-container.py` is green (invoked from an
  arbitrary cwd, proving the `__file__`-derived root works): both C++
  writer-attribute tests pass in the container. **Separately**,
  `uv run pytest` at the repo root passes on the host — output
  NetCDFs carry `units: kg m-2 s-1` on `co` and `test_species_units` passes
  for all three combos.
- `--clean` removes and rebuilds from scratch successfully; `--no-build`
  and `--no-test` each skip exactly their phase; `setup.sh` is unchanged.
- **Typed attributes / `missing_value`: backed out — follow-up issue.**
  Both were implemented and verified (typed sniffing locked by
  type-asserting tests; `missing_value` red→green with 289/289 CECE tests
  green) before being reverted as out of scope for this fix. The follow-up
  issue covers: the `missing_value` whitelist exclusion, quote-aware type
  inference, lifting the key whitelist, and the runner's `NcAttrType`
  widening.
- A driver config without `field_attributes` produces output with **no**
  units/long_name attributes on data fields (verifiable with the assertion's
  `units: null` mode).
- Runner harness passes; `simple-maccity.yaml` round-trips through
  `CeceConfig` with the new key.
- `design.md` documents `field_attributes` in the base-config description;
  `README.md` documents `./setup.sh -t`.

## Non-goals

- No propagation of units from *input stream* attributes through the
  stacking engine (config-declared attributes only — the config is the
  statement of intent).
- No conf/amio (helm) changes.
- No unit conversion or validation of unit strings.
