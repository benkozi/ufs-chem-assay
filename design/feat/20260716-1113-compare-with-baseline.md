# Feature: baseline comparison of combination NetCDF output

## Goal

Optionally compare each combination's NetCDF output against a **baseline** —
the payoff the stats/ids groundwork has been building toward. Pairing is at
the **combination** level (not every combination has a baseline), baselines
are identified by **ULID**, and the comparison models `nccmp`: bit-for-bit
by default, tolerance-based on request, with structural checks (files,
formats, dimensions, variables, attributes) always exact. Every comparison
produces a YAML results artifact from a pydantic model.

## Design

### Suite config: the `baseline_comparisons` list of combo selectors

> Revised: the first cut mapped full combination names to ULIDs. Full names
> grow with sweep dimensions and every name changes whenever a dimension is
> added — the map was churn-prone scaffolding. It is replaced by **combo
> selectors**: regex-style matches against the sweep combination *elements*
> (targets and field values), explicit yet insulated from name/id churn.

**Selectors mirror the `sweep` structure** — the same
structure-over-flatness lesson as the sweep-attachment redesign, and for
the same reason: generality. A selector walks the same paths a sweep does,
with regexes at the leaves:

```yaml
baseline_comparisons:           # a list; each entry pairs one combination
  - sweep_selector:
      cece_data:
        streams:
          - name: MACC.*        # regex vs the stream target's name
            mapalgo: bilinear   # regex vs that stream's swept value
    ulid: 01K0AAAAAAAAAAAAAAAAAAAAAA
  - sweep_selector:
      cece_data:
        streams:
          - name: MACC.*
            mapalgo: consd
    ulid: 01K0BBBBBBBBBBBBBBBBBBBBBB
    atol: 0.001                 # optional per-comparison override (default 0.0)
```

(As with the original sweep sketch, `name` and its sibling fields form one
block per stream — `name` selects the stream, siblings constrain its swept
values.)

```python
class StreamSweepSelector(StrictModel):     # mirrors StreamSweep
    name: str                               # regex, fullmatch vs the stream target
    taxmode: str | None                     # regex, fullmatch vs the swept value ...
    tintalgo: str | None
    mapalgo: str | None

class CeceDataSweepSelector(StrictModel):   # mirrors CeceDataSweep
    streams: list[StreamSweepSelector]

class SpeciesEntrySweepSelector(StrictModel):  # mirrors SpeciesEntrySweep
    operation: str | None
    category: str | None
    vdist_method: str | None

class SweepSelector(StrictModel):           # mirrors Sweep
    cece_data: CeceDataSweepSelector | None
    species: dict[str, list[SpeciesEntrySweepSelector]] | None  # key: regex vs species name; index -> entry

class BaselineComparison(StrictModel):      # one list entry; every field described
    sweep_selector: SweepSelector           # required
    ulid: str                               # the baseline's ULID
    atol: float = Field(0.0, ge=0, description="0 = bit-for-bit; > 0 = absolute tolerance for this comparison")

class SuiteConfig(StrictModel):
    ...
    baseline_comparisons: list[BaselineComparison] = Field(default_factory=list, description="Per-combination baseline comparisons; empty/absent disables")
```

**Matching semantics** (a structural walk, fullmatch at the leaves):

- Every regex uses **`re.fullmatch`** — anchored both ends, no accidental
  substring matches (`MACC.*` matches `MACCITY`; `consd` is effectively
  exact). Invalid regexes fail at suite load (compiled in a validator).
- A combination matches a selector iff **every constrained element is
  satisfied**, following the sweep's own structure:
  - each `streams` selector block requires a swept stream dimension group
    whose target name fullmatches `name` **and** whose specified fields'
    swept values fullmatch — the block scopes its fields to that one
    stream, exactly as a `StreamSweep` block scopes its lists;
  - each `species` selector entry requires a swept species whose *name*
    fullmatches the dict key, with list position i constraining entry i
    (mirroring the sweep's positional entry selection);
  - multiple blocks/entries are ANDed.
- Unspecified structure is unconstrained — that is the insulation: adding
  a sweep dimension (or a whole new target) never invalidates a selector's
  text. The structural mirror also means any future sweep extension (new
  groups, new fields) extends the selector language mechanically, keeping
  the feature general.

**Resolution, validated at session start** (before any container runs):

- Each selector must match **exactly one** enumerated combination. Zero
  matches (stale/typo selector) is an error; more than one is an error
  naming the matched combinations — which is exactly what happens when a
  new sweep dimension multiplies a previously-unique selector, forcing the
  author to refine it (e.g. add `taxmode: cycle`). Ambiguity is surfaced,
  never guessed away.
- A combination matched by **more than one selector** is an error (one
  baseline per combination).
- Combinations matched by no selector skip the comparison test (the
  "optional" in the requirement); an empty or absent `baseline_comparisons`
  list disables it for the suite.
- `atol` is **per comparison entry** (default `0.0` = bit-for-bit,
  pydantic-validated `ge=0`), so each pairing states its own tolerance.
  **Absolute** tolerance, deliberately: no tolerance scaling by the
  baseline's magnitude.

### Setting and baseline layout

| Setting                       | Env var                             | Default      |
|-------------------------------|-------------------------------------|--------------|
| `baseline_root_dir`           | `CECE_BASELINE_ROOT_DIR`            | `None` → cwd |
| `enable_baseline_comparisons` | `CECE_ENABLE_BASELINE_COMPARISONS`  | `true`       |

`enable_baseline_comparisons` is the **global kill switch**: when `false`,
every `test_baseline_comparison` skips with an explicit reason regardless of
the suite's `baseline_comparisons` list — an environment-level control (e.g.
a machine without the baseline store) that never requires editing suites.

A baseline lives at `<baseline_root_dir>/<ulid>/` and contains exactly the
`*.nc` files of the combination run it was captured from (flat, same
filenames the driver produced). A **configured baseline that cannot be
found is a test failure, not a skip** — a declared expectation that cannot
be evaluated must be loud. Future work replaces local lookup with online
retrieval plus a manifest linking ULIDs to combination names and metadata;
nothing in this design depends on the directory carrying metadata today.

### Comparison engine (`src/comparison.py`, modeled on nccmp)

Per combination, given the combo dir and the baseline dir:

1. **File sets**: `*.nc` names and counts must match exactly (missing and
   unexpected files reported by name).
2. Per matched file pair, all checked and reported:
   - **NetCDF format** exact (e.g. `NETCDF4` vs classic — via the
     underlying file `data_model`).
   - **Dimensions**: names and sizes exact.
   - **Variables**: names and counts exact.
   - **Global attributes**: exact (raw, undecoded — same
     `decode_cf=False` rationale as the species-attributes assertion).
   - **Per-variable attributes**: exact, raw.
   - **Data**: `atol == 0` → bit-for-bit (dtypes equal; values identical
     with NaNs required in identical positions); `atol > 0` →
     `|realization - baseline| <= atol` elementwise — absolute tolerance,
     no scaling by the baseline's magnitude (NaN positions still
     identical). Always-exact attributes are per the requirement —
     tolerance applies to data only.
3. **Parallel xarray**: datasets open with `chunks="auto"`; per-variable
   comparison reductions (equality / max-abs-diff) are gathered into a
   single `dask.compute` executed on the existing session `dask_client` —
   the same batching pattern as the stats step.
4. **Logging** goes through the runner's logging system
   (`logs.get_logger("comparison")`, level via `CECE_LOG_LEVEL`), in the
   established style: an INFO line when a combination's comparison starts
   (combo, baseline ULID, atol), one per file pair with its outcome, and a
   summary line (`comparison passed`/`FAILED` with the failing checks);
   mismatch details additionally at ERROR so they stand out at any level.

### Results: pydantic model → YAML artifact

```python
class VariableComparison(StrictModel):   # frozen; every field described
    name, dtype_match, data_match, attributes_match, max_abs_diff, detail

class FileComparison(StrictModel):
    file, format_match, dimensions_match, variables_match,
    global_attributes_match, variables: list[VariableComparison], passed

class BaselineComparisonResult(StrictModel):
    run_id, combo, combo_id, baseline_ulid, atol,   # baseline_ulid stays
    file_names_match, files: list[FileComparison], passed  # qualified: run_id
                                                    # (also a ULID) coexists
```

Written to `<combo-dir>/<combo_id>-comparison.yaml` via the `to_yaml`
convention **whether the comparison passes or fails** (like `.out`), so a
failed comparison leaves its full diff record. The test then asserts
`result.passed`, with the failure message summarizing the offending
files/variables/checks.

### Test structure

A new unwrapped test on the shared fixture, standard skip ladder:

- `test_baseline_comparison[<combo>]` — skip ladder, in order: driver run
  failed; `baseline comparisons disabled by settings` when
  `enable_baseline_comparisons` is false (the global switch trumps suite
  config); `no baseline configured for this combination` when no
  `baseline_comparisons` entry selects the combo. Otherwise compares with
  the entry's `atol` and asserts. Missing baseline directory →
  **failure** (see above).

### Initial baseline generation (implementation step)

With the current fixed driver and checked-in config:

1. Run the suite; for each of the three combinations, copy its `*.nc` into
   `/Users/bkoziol/Library/CloudStorage/Dropbox/rlps/rsandbox/cece-baselines/<new ULID>/`
   (one freshly generated ULID per combination).
2. Wire those ULIDs into the checked-in suite's `baseline_comparisons`
   as one entry per combination (streams block: `name: MACCITY`,
   `mapalgo: bilinear|consd|passthrough`) and set
   `CECE_BASELINE_ROOT_DIR` to the Dropbox path when running locally.

**Portability caveat, accepted**: the checked-in suite then references
baselines that exist only where `CECE_BASELINE_ROOT_DIR` points at this
store; elsewhere the comparison tests fail loudly (missing baseline). The
future manifest/online-retrieval work resolves this properly.

## TDD plan (red first)

Harness tests against fabricated NetCDF pairs, written before
`comparison.py` exists:

- identical pair → passes bit-for-bit; a single perturbed value → fails at
  `atol=0`, passes at a covering `atol`, fails at a tighter one
- NaN-position mismatch fails even under tolerance
- changed variable attribute / global attribute → fails (attributes are
  always exact)
- dimension size change, variable added/removed, file added/removed/renamed,
  format mismatch (`to_netcdf(format="NETCDF4"/"NETCDF3_CLASSIC")`) → each
  fails naming the check
- results YAML written on both pass and fail and round-trips through the
  model
- selector resolution: single match resolves; zero matches rejected; a
  selector matching two combinations rejected (the added-dimension
  insulation scenario: a selector unique under a one-dimension sweep
  becomes ambiguous when a second dimension is added — the error names
  both matches); two selectors matching one combination rejected;
  structural scoping (with two swept streams, a block pins its fields to
  the name-matched stream only); species key regex + positional entries;
  `re.fullmatch` anchoring (no substring surprises); invalid regex
  rejected at load
- suite parsing: per-entry `atol` validation (`-0.5` rejected) and the
  per-entry override taking effect in the comparison

## Ripples (standing process rules)

- **`design.md`**: suite-configuration example gains the
  `baseline_comparisons` list; settings table gains `baseline_root_dir`; the artifacts layout
  gains `<combo_id>-comparison.yaml`; the "no baseline comparison yet"
  non-goal is removed.
- **`README.md`**: env var table (`CECE_BASELINE_ROOT_DIR`), test list
  gains `test_baseline_comparison`, results layout gains the comparison
  yaml.
- Pydantic models with described fields, never dataclasses.

## Non-goals

- No online baseline retrieval, no baseline manifest (ULID → combination
  metadata), no baseline *creation* tooling in the runner — capture is a
  manual/scripted step this iteration.
- No relative-tolerance (`rtol` / scaled) mode — absolute only
  (per-comparison `atol` is in scope and delivered).
- No statistics-CSV comparison — this feature compares the NetCDF files
  themselves.

## Acceptance criteria

- Harness passes without docker, covering the matrix above.
- Integration with the generated baselines and
  `CECE_BASELINE_ROOT_DIR` set: all `test_baseline_comparison` tests pass
  bit-for-bit against the freshly captured baselines; each combo dir
  contains its `<combo_id>-comparison.yaml` with `passed: true`.
- Removing a combination's `baselines` entry skips its comparison test with
  the explicit reason; pointing at a nonexistent ULID fails it.
- All other tests keep their outcomes.
