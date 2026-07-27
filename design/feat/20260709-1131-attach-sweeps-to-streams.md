# Feature: sweeps attach to streams (structure-mirroring sweep config)

## Goal

Today's `sweep` is a flat list of enum dimensions, each hardwired to an
implicit injection point (`streams[0]`, the first species entry). Real
configs have **multiple streams and species entries**, so a sweep must say
*what it attaches to*. The sweep config is restructured to **mirror the
driver-config structure**: a swept `mapalgo` lives inside a stream block
that names the stream it applies to, and the same pattern covers species
entries (the other current injection point) and future configuration
groups.

## Design

### Suite yaml shape

```yaml
sweep:
  cece_data:
    streams:
      - name: MACCITY                              # selector: which stream
        mapalgo: [bilinear, consd, passthrough]    # swept dimensions
  species:            # optional; mirrors species.<name>[<entry>]
    co:
      - operation: [add, replace]                  # first entry of co
```

(The raw note's sketch showed `name` and `mapalgo` as two separate list
items; this design normalizes that to one block per stream — `name` is the
selector, sibling keys are the swept dimensions.)

### Models (suite_config.py, all `StrictModel`)

```python
class StreamSweep(StrictModel):
    name: str                                   # must match a base-config stream
    taxmode:  list[Taxmode]  | None = None      # min_length=1 each
    tintalgo: list[Tintalgo] | None = None
    mapalgo:  list[Mapalgo]  | None = None

class CeceDataSweep(StrictModel):
    streams: list[StreamSweep]                  # min_length=1, unique names

class SpeciesEntrySweep(StrictModel):
    operation:    list[Operation]   | None = None
    category:     list[Category]    | None = None
    vdist_method: list[VdistMethod] | None = None

class Sweep(StrictModel):
    cece_data: CeceDataSweep | None = None
    species: dict[str, list[SpeciesEntrySweep]] | None = None
```

The flat `Sweep` fields are **removed** — a breaking suite-schema change,
rejected loudly by `StrictModel` if an old-format suite is loaded. Species
sweeps use **list position as the entry selector** (sweep list index i →
`species.<name>[i]`), since entries have no names; an empty `{}` block
skips an entry.

### Selector validation against the base config

At session start (before any container runs), the sweep's selectors are
validated against the loaded base config: every `StreamSweep.name` must
match exactly one stream, sweep stream names must be unique, every swept
species key must exist, and a species sweep list must not be longer than
that species' entry list. Violations raise a clear error naming the
selector. `enumerate_combos` gains the base config as an argument
(`enumerate_combos(sweep, base_config)`) to do this.

### Combination machinery (combos.py rework)

`DIMENSIONS`' fixed six-entry table is replaced by dimensions **derived from
the sweep**: one dimension per (target, field, values), where target is a
named stream or a species entry. Apply functions locate the target in the
config by name/index instead of `streams[0]` / first-entry. Vdist companion
fields still accompany a swept `vdist_method`, on the targeted entry.

### Normalization: declaration order never matters

Combo ids are content hashes, so the canonical string must not depend on
how the suite yaml happens to be ordered. The sweep is **normalized before
enumeration**:

- **Targets are sorted**, not taken in declaration order: species groups
  first (species names lexicographic, entry index ascending), then stream
  groups (stream names lexicographic). Fields within a target keep the
  fixed canonical order (`op, cat, vd` / `tax, tint, map`).
- **Value lists are sorted** (by enum value). This cannot change any
  combo's id — a combination's name contains only its own values — but it
  makes enumeration order, test execution order, and `combos.csv` row order
  declaration-independent too.
- Net effect: two semantically identical sweeps, however reordered in yaml,
  produce byte-identical combo ids, names, and `combos.csv` contents.

**Duplicates are validation errors, not dedupes**: a repeated value in a
sweep list (`mapalgo: [consd, consd]`), like a repeated stream name or
species entry collision, would enumerate two combinations with the same id
and directory — rejected loudly at load/validation time instead.

### Combination naming: target-qualified segments (pytest ids only)

Name segments gain the attachment target:

```
<target>.<tag>-<value>       e.g.  MACCITY.map-consd
                                   co.op-add   (co-1.op-add for entry 1)
```

Multi-dimension combos join segments with **`__`** into the canonical
combination string, e.g. `MACCITY.map-consd__co.op-add`. A double underscore
is shell-safe and `-k`-safe (no quoting needed) and visually distinct from
the `.` and `-` used within segments; the theoretical ambiguity of a target
name containing `__` is cosmetic only, since nothing parses the canonical
string back — `combos.csv` carries the structure in columns. The string is
**deterministic** (canonical order) and serves as the pytest parameter id —
human-readable, `-k`-selectable (substring filters like `-k map-consd` keep
working) — but it is not used for directories or filenames (below).

### Combination ids: content hash directories + mapping CSV

Qualified names grow linearly with sweep dimensions and will exceed
filesystem name limits on realistic sweeps, so **storage stops carrying
semantics**:

- Each combination's id is a **deterministic content hash** of its canonical
  combination string:

  ```python
  combo_id = hashlib.sha256(name.encode()).hexdigest()[:16]
  ```

  16 hex chars (64 bits) — stdlib, fixed-length, filesystem-safe, and
  collision-safe far beyond any realistic combination count. Being
  content-derived, the id is **stable across runs**: the same combination
  hashes to the same id every time, making `combo_id` the natural cross-run
  join key for the future baseline work. (Base64 was considered for
  reversibility, but it is an encoding, not a hash — output grows with the
  name, recreating the length problem. The hash is not reversible;
  `combos.csv` below is the dereference map.)

- Directories and artifact filenames use the id exclusively:

  ```
  <output-root>/
    run.yaml
    combos.csv                       # the dereferencing map (below)
    descriptive_stats.csv
    3f9a1c2b7d4e8a01/                # one directory per combination
      3f9a1c2b7d4e8a01.yaml          # generated driver config
      3f9a1c2b7d4e8a01.out           # captured driver stdout+stderr
      3f9a1c2b7d4e8a01-stats.csv     # per-NetCDF statistics
      *.nc
  ```

- **`combos.csv`** at the output root maps ids back to what was tested —
  written at session start alongside `run.yaml`, before any container runs.
  Long format, one row per swept dimension per combination:

  | run_id | combo_id | name | target | field | value |
  |--------|----------|------|--------|-------|-------|
  | 01KX…  | 3f9a1c2b7d4e8a01 | MACCITY.map-consd | MACCITY | mapalgo | consd |

  Filtering on `combo_id` dereferences a directory; the `name` column holds
  the full canonical string linking to pytest ids and reports.

- **Stats rows gain `combo_id`**; the existing `combo` column keeps the
  human-readable name. Both are stable across runs — join on whichever is
  convenient.

## Ripples (standing process rules)

- **Harness tests**: `test_combos` reworked around the nested sweep
  (attachment to a *named* stream — including a second stream in a
  fabricated config to prove non-first attachment works; canonical ordering;
  qualified names; combo ids deterministic (same sweep enumerates to
  identical ids twice) and 16 lowercase hex chars; **normalization** — a
  reordered but semantically identical sweep (shuffled stream blocks,
  shuffled value lists) yields identical ids, names, and enumeration order;
  validation failures for unknown stream name / species key / oversized
  entry list / duplicate values in a sweep list);
  `test_suite_config` gains nested-sweep parsing plus rejection of the old
  flat format; a `combos.csv` writer test (row per dimension, dereference by
  `combo_id`); `test_analysis` gains the `combo_id` column; pipeline
  expected ids and ULID-named artifact paths update.
- **`design.md`**: suite-configuration example, the combination-space
  "injected at" table (becomes attachment-based), naming section, directory
  layout (ULID directories, `combos.csv`).
- **`README.md`**: suite yaml description; results layout (ULID directories,
  `combos.csv` as the dereferencing map); stats CSV column list gains
  `combo_id`; the `-k map-consd` example still holds (substring match) —
  note ids are now target-qualified.
- Suite file updated to the nested shape; pydantic models, never
  dataclasses.

## Non-goals

- No sweeping of non-enum fields (scales, paths) — enum dimensions only,
  same as today.
- No cross-target constraints (e.g. "sweep these two streams in lockstep");
  the space is still a full cartesian product.
- No new enum dimensions; same six, relocated.

## Acceptance criteria

- The nested initial suite loads; pytest ids are `MACCITY.map-*`; each
  generated yaml carries the swept value on the **MACCITY stream located by
  name**.
- Combo directories and artifact filenames are 16-hex-char content-hash
  ids; two runs of the same suite produce identical combo ids;
  `<output-root>/combos.csv` exists before any container runs and
  dereferences every directory to its swept dimensions and canonical name;
  stats rows carry both `combo_id` and the readable `combo` name.
- A sweep naming an unknown stream, an unknown species, or more entry
  blocks than the base config has entries fails at session start with an
  error naming the offending selector; duplicate values in a sweep list are
  rejected; an old flat-format suite fails validation.
- Reordering stream blocks or value lists in the suite yaml changes no
  combo id, name, or `combos.csv` content.
- Harness passes without docker (including a fabricated two-stream config
  proving attachment targets the named, non-first stream); integration
  keeps its expected shape (filename tests red per the known driver bug).
