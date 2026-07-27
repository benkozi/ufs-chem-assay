# Feature: unique suite name, carried into stats output

## Goal

Real usage runs **multiple suites**, and their artifacts (especially
accumulated stats CSVs) must say which suite produced them. Each suite
declares a unique name; the maccity suite is named **`simple-maccity`**.
The name joins `run_id` in every descriptive-stats row.

## Design

### `name` on the suite config

`SuiteConfig` gains a required `name: str` as its first field:

```yaml
# simple-maccity-suite.yaml
name: simple-maccity
config_path: ../cece/simple-maccity.yaml
...
```

- **Format-validated**: `pattern=^[a-z0-9][a-z0-9-]*$` (lowercase slug) so
  the name is safe in filenames, CSV cells, and future grouping keys.
  A missing or malformed name fails at load time (`StrictModel` already
  rejects a misspelled key).
- **Uniqueness is a convention, not a runtime check**: one session loads one
  suite, so collisions only matter across accumulated artifacts. The
  documented convention ties the name to the filename — a suite named `X`
  lives in `X-suite.yaml` — but the field is authoritative; the filename is
  not parsed.
- `RunManifest` already embeds the full resolved `SuiteConfig`, so the name
  lands in `run.yaml` with no further change.
- The session-start log line gains it:
  `starting run <ulid> (suite=simple-maccity, path=...)`.

### Stats output: a `RunContext` bundle

`VariableStats` gains `suite: str` right after `run_id` (column order:
`run_id, suite, combo, file, ...`).

Rather than growing `compute_file_stats(nc_path, combo, run_id, suite, ...)`
one identity string at a time, the run-level identity moves into a small
frozen pydantic model, passed as one argument:

```python
class RunContext(BaseModel):          # analysis.py; frozen
    run_id: str
    suite: str

def compute_file_stats(nc_path: Path, combo: str, run: RunContext) -> list[VariableStats]
```

A session-scoped `run_context` fixture builds it from the existing `run_id`
and the loaded suite (subsuming the bare `run_id` fixture, which it
replaces). Future identity columns (e.g. a baseline id) extend `RunContext`
without touching call sites.

## Ripples (standing process rules)

- **Harness tests**: suite-loading tests assert `name == "simple-maccity"`;
  a missing `name` and a malformed name (`Simple Maccity!`) both fail
  validation; `test_analysis` row helpers and `compute_file_stats` calls
  switch to `RunContext`; the concatenation test asserts the `suite` column
  survives both CSV layers. Inline suite yamls written by existing tests
  gain a `name:` line.
- **`design.md`**: suite-configuration example gains `name`; the stats
  blurb/resolved decisions mention the suite column.
- **`README.md`**: suite yaml description and the stats CSV column list gain
  the name/`suite` column.
- Pydantic models, never dataclasses.

## Non-goals

- No multi-suite sessions (one `--suite-config` per run, unchanged); the
  name exists so artifacts from *separate* runs can be distinguished.
- No registry/uniqueness enforcement across suite files.

## Acceptance criteria

- The checked-in suite loads with `name: simple-maccity`; a suite without a
  `name`, or with a non-slug name, fails at load with a pydantic error.
- Integration: every stats row in both CSV layers carries
  `suite=simple-maccity` alongside `run_id`; `run.yaml` shows the name via
  the embedded suite config; the session-start log line includes it.
- Harness passes without docker; integration keeps its expected shape
  (filename tests red per the known driver bug).
