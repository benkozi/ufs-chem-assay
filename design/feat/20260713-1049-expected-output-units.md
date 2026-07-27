# Feature: per-species output assertions — units first

## Goal

Assertions about **specific species** in the NetCDF output, configured under
`assertions.species.<name>` — a home that will accumulate more per-species
checks over time. The first is **output units**: verify the `units`
attribute of the species' variable in every NetCDF a combination produces.

**This test is expected to fail against the current driver**: a known bug
emits malformed units for `co` (observed as `mol mol-1`-style text that does
not match the correct value). Catching that is the assertion's purpose —
same red-by-design pattern as the (since fixed) filename-stamp bug.

## Design

### Suite config

```yaml
assertions:
  expected_nc_file_count: null
  validate_filenames: true
  species:
    co:
      units: kg m-2 s-1     # exact expected string
      # units: null         -> assert the variable has NO units attribute
      # units: "__ignore__" -> don't check (the default)
```

```python
IGNORE_VALUE = "__ignore__"   # suite_config.py; the general string sentinel

class SpeciesAssertions(StrictModel):
    units: str | None = IGNORE_VALUE

class Assertions(StrictModel):
    ...
    species: dict[str, SpeciesAssertions] | None = None
```

Three-way semantics for `units` (and the template for future string-valued
assertion fields):

| Value          | Meaning                                          |
|----------------|--------------------------------------------------|
| `"__ignore__"` | don't check — the **default** when omitted       |
| `null`         | assert the variable has **no** `units` attribute |
| any string     | assert exact equality                            |

**`__ignore__` is a general convention**, not units-specific: string-valued
assertion fields use it as their "don't check" sentinel (a plain `null`
can't serve, since it already means "assert absent"). This goes into
`design.md` as a standing rule for all future assertions.

### Assertion (`assertions.py`)

```python
def assert_species_units(combo_dir: Path, species: str, expected: str | None) -> None
```

- Evaluated against **every** `*.nc` in the combo directory: for each file,
  the species' variable must exist (a missing variable fails the assertion
  with a clear message — units cannot be verified on nothing), and its
  `units` attribute must match:
  - expected `None` → the attribute must be absent (`attrs.get("units") is
    None`; an empty string counts as present and fails, shown by repr).
  - expected string → exact string equality.
- INFO logging in the established style:
  `testing species 'co' units expected='kg m-2 s-1', found='mol mol-1' (cece_20100101_010000.nc)`.
- The failure message names the file(s), expected, and found values.

### Test structure

A new unwrapped test, parameterized by **combo × configured species**
(`pytest_generate_tests` already knows the suite at collection):

- `test_species_units[<combo-name>-co]` — skips when the driver run failed
  (standard reason), skips with `units check ignored by suite config` when
  the species' value is the sentinel, otherwise asserts.
- Species absent from `assertions.species` generate no tests at all.

### The known driver bug

The checked-in suite sets the **correct expected units for `co`** (value to
confirm at implementation — the driver currently emits something
`mol mol-1`-shaped that the user has identified as wrong). The default run
is therefore expected to go red on the three `test_species_units` combos
until the driver is fixed, at which point they flip green with no suite
edit — the same standing-record pattern the filename tests just vindicated.

## Ripples (standing process rules)

- **Harness tests**: `SpeciesAssertions` defaults (omitted → sentinel) and
  unknown-key rejection; `assert_species_units` against fabricated NetCDFs —
  exact match passes, mismatch fails with file/expected/found in the
  message, `None` vs absent attribute passes, `None` vs present (including
  empty-string) fails, missing variable fails; suite parsing of the nested
  `assertions.species` block.
- **`design.md`**: assertions example gains the species block; a new
  standing rule documents `__ignore__` as the string-assertion sentinel.
- **`README.md`**: test list gains `test_species_units`; the known-bug note
  (removed when the stamp bug was fixed) returns for units; suite yaml
  description mentions per-species assertions.
- Pydantic models, never dataclasses.

## Non-goals

- No other per-species checks yet (value ranges, fill values, dtypes — the
  `SpeciesAssertions` model is their future home).
- No pattern/regex matching on units — exact string or absent only.
- No unit *conversion* or equivalence (e.g. `kg/m2/s` vs `kg m-2 s-1` are
  simply different strings).

## Acceptance criteria

- Harness passes without docker, covering the full three-way semantics.
- Integration with the checked-in suite: `test_species_units` **fails** for
  all three combos, the message showing expected vs the driver's malformed
  found units — the accepted red state until the driver fix.
- A suite with `units: "__ignore__"` (or no `species` block) runs green with
  the units tests skipped (or absent).
- All other tests keep their current outcomes.
