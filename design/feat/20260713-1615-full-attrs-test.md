# Feature: per-species full-attribute assertions (exact/subset)

## Goal

Generalize the per-species units check
(`design/feat/20260713-1049-expected-output-units.md`, since implemented and
green) to the **complete attribute dictionary** of the species' variable in
every NetCDF a combination produces. The suite declares the expected
attributes per species and whether the match is **exact** (the attribute
dictionaries are equal) or **subset** (the expectation is contained in what
the file carries). The units-only schema is superseded and removed.

## Design

### Suite config

```yaml
assertions:
  species:
    co:
      attributes:
        exact: true            # default; false = subset match
        expected:
          units: kg m-2 s-1
          long_name: carbon_monoxide_emission_flux
          coordinates: time lev lat lon
```

```python
class AttributesAssertion(StrictModel):
    exact: bool = True
    expected: dict[str, str | None] = {}

class SpeciesAssertions(StrictModel):
    attributes: AttributesAssertion | None = None   # None -> no attribute test
```

The `units: str | None = IGNORE_VALUE` field is **removed** (breaking suite
schema change, rejected loudly by `StrictModel` — same pattern as the sweep
restructure).

### Matching semantics

Per-value semantics inside `expected` follow the standing three-way string
convention (design.md):

| Expected value | Meaning                                                |
|----------------|--------------------------------------------------------|
| a string       | attribute must exist with exactly this value           |
| `null`         | attribute must be **absent**                           |
| `"__ignore__"` | no value constraint (in exact mode: may exist, any value) |

Mode semantics:

- **`exact: true` (default)** — the dictionaries match exactly: every
  string-valued expected key must be present with that value, every found
  attribute must be accounted for in `expected` (as a string or
  `__ignore__` entry), and `null`-valued keys must be absent. An attribute
  in the file that `expected` does not mention is a failure.
- **`exact: false` (subset)** — only the `expected` entries are checked
  (strings must match, `null`s must be absent); attributes beyond the
  expectation are allowed.

Checked against **every** `*.nc` in the combo directory; a missing variable
fails (attributes cannot be verified on nothing). The failure message names
the file(s) and the per-key differences (missing / wrong value / unexpected).

### Raw attributes, not xarray-decoded

The assertion reads attributes **undecoded**
(`xr.open_dataset(..., decode_cf=False, decode_coords=False)`): xarray's CF
decoding strips structural attributes like `coordinates` (and `_FillValue`)
out of `.attrs` into `.encoding`, which would make them silently
unassertable. Raw attributes are what the driver actually wrote — the honest
target for an evaluation tool. Consequence: exact mode must account for
*everything* on disk (that is its point); use `__ignore__` entries or subset
mode when structural attributes are not of interest.

### Test structure

`test_species_units` is renamed **`test_species_attributes`** (same
parameterization: combo × configured species; same skip ladder — failed
run, then `attributes: null` → skipped as unconfigured). The assertion
function `assert_species_units` becomes:

```python
def assert_species_attributes(combo_dir: Path, species: str,
                              expected: dict[str, str | None], exact: bool) -> None
```

with INFO logging of expected vs found dictionaries per file.

### Checked-in suite

`simple-maccity-suite.yaml` declares the full expected dictionary in exact
mode — units, long_name, and the default coordinates — locking the complete
attribute surface of the fixed driver:

```yaml
species:
  co:
    attributes:
      expected:
        units: kg m-2 s-1
        long_name: carbon_monoxide_emission_flux
        coordinates: time lev lat lon
```

Expected outcome: all `test_species_attributes` tests green against the
fixed driver (if the driver writes attributes beyond these three, exact mode
will surface them — a finding, handled by extending the expectation or
deliberately ignoring).

## Ripples (standing process rules)

- **Harness tests**: rework the units tests into the attribute matrix —
  exact match passes; exact fails on extra found attribute / missing key /
  wrong value; subset passes with extras and fails on wrong value; `null`
  asserts absence in both modes; `__ignore__` permits any value in exact
  mode; missing variable fails; model defaults (`exact` true, absent
  `attributes` block) and unknown-key rejection; old `units:` schema
  rejected.
- **`design.md`**: assertions example updated to the `attributes` block; the
  `__ignore__` standing rule already covers the per-value semantics.
- **`README.md`**: test id rename and the exact/subset description.
- Pydantic models, never dataclasses.

## Non-goals

- No regex/pattern matching on attribute values — exact strings, `null`, or
  the sentinel.
- No global (non-variable) attribute assertions — per-species variables
  only, for now.
- No numeric-typed attribute comparison (values compare as strings as read).

## Acceptance criteria

- Harness passes without docker, covering the full matrix above.
- Integration (checked-in suite, exact mode): `test_species_attributes`
  passes for all three combos against the fixed driver; any undeclared
  on-disk attribute surfaces as a failure naming the key.
- A suite using the old `units:` key fails validation; a suite with
  `attributes` omitted runs with the species test skipped/absent.
- All other tests keep their current outcomes.
