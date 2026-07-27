# Fix: runner mirror update for the merged `output.fields` schema

## Context

The driver (this branch, from `feature/config-attrs`) merged
`field_attributes` **into** `output.fields`: each entry is now either a
plain field-name string (shorthand: no configured attributes) or a map
pairing the required `name` with an optional flat `attributes` map — see
`docs/configuration.md` §`output` and the C++
`CeceOutputField{name, attributes}` model. The old parallel
`field_attributes` key is **treated as never having existed**: no
back-compat, no migration errors, and no tests for it (standing decision
from the merge work).

The runner has not caught up: `models/cece_config.py` still models
`fields: list[str]` plus a `field_attributes` map, and the checked-in
`src/tests/config/cece/simple-maccity.yaml` still carries the removed key —
which the driver now silently ignores, so the output presumably carries no
units/long_name and `test_species_attributes` (exact mode) is red. That
existing red is the natural TDD baseline; this fix turns it green.

## Fix design

### Pydantic mirror (`models/cece_config.py`)

```python
class OutputField(StrictModel):
    name: str = Field(description="Export field name to write")
    attributes: dict[str, str] | None = Field(
        None,
        description="NetCDF attributes (name -> value) for this field; fields without configured attributes get none",
    )

class Output(StrictModel):
    ...
    fields: list[str | OutputField] = Field(
        description="Fields to write; a plain string is shorthand for a field with no configured attributes"
    )
```

- `field_attributes` is **deleted** from the model — `StrictModel` rejects
  it like any unknown key, and per the standing rule there is no
  deprecation shim and no test asserting the old key's rejection.
- Attribute values stay `str`: typed values (`NcAttrType`) belong to the
  backed-out typed-attributes follow-up
  (`design/fix/20260713-1136-fix-mol-unit-output-issues.md`), not this fix.
- The `str | OutputField` union round-trips through `to_yaml`/`from_yaml`
  as the driver schema: string entries stay scalars, map entries dump to
  `{name, attributes}` (with `exclude_none` dropping absent `attributes`).
- Per the (new) standing rule, every new pydantic field carries a
  `description`.

### Checked-in test config

`simple-maccity.yaml`'s output block becomes the nested form:

```yaml
output:
  fields:
    - name: co
      attributes:
        units: kg m-2 s-1
        long_name: carbon_monoxide_emission_flux
```

(The suite's `test_species_attributes` expectation — units, long_name,
`coordinates: time lev lat lon` — is unchanged: the driver still applies
the default coordinates via `CeceOutputField::GetCoordinates`.)

### Harness updates (red first, per the TDD rule)

Test changes land **before** the model change and fail against the old
model, then the model/config updates turn them green:

- `test_combos`: the field-attributes round-trip test asserts the nested
  shape — the base config's `fields` entry for `co` is an `OutputField`
  with the expected attributes, and `build_config` carries it into
  generated combo yamls verbatim.
- New model test: a `fields` list mixing shorthand strings and `{name,
  attributes}` maps parses, with the shorthand entry carrying no
  attributes.
- No test references the old `field_attributes` key in any direction
  (never-existed rule).

### Acceptance criteria

- Harness passes without docker.
- Integration: **all combo tests pass** — in particular
  `test_species_attributes` is green again, proving the nested attributes
  reach the driver output (units/long_name configured, coordinates
  defaulted).
- `design.md`'s base-configuration blurb describes the nested
  `output.fields` (not `field_attributes`); README needs no schema change
  (it does not document the driver-config internals) — verify and leave.
