# Feature: respond to the machine review of PR 35 — two robustness fixes in the output download and the input search, a refreshed suite header, a docstring, and the replies to post

Refined 2026-11-04 from the requirement below: respond to the review comments on https://github.com/benkozi/ufs-chem-assay/pull/35 ("feat: global anthropogenic CECE use case integration", `feature/global-anthro-use-case` → `develop`, 19 commits, 55 files). The one review is machine-generated (`ufs-chem-pr-review-mcp` @ `0e1088e`, submitted 2026-11-04 as COMMENTED on commit `c80954c`), prepared by the spike `design/spike/20261104-0836-global-anthro-pr-35-review/`; it carries four inline comments and no human comments. Each is assessed below against the code as it stands, accepted or not, and answered with the change, the test, and the reply text the user posts.

# session

- **Session ID**: `c30fb022-0fff-43a5-9908-1cfbe2cb2d88`
- **Resume Command**: `agy --resume c30fb022-0fff-43a5-9908-1cfbe2cb2d88`

## Goal

1. **Every comment answered on the PR by the user**, each reply saying what was done (or why not) and naming the commit — the replies are drafted here, never posted by the harness or the assistant.
2. **The two P1 findings fixed with tests**: `download_output` cleans up only its own staging directory, and `_find_source` deduplicates matches by their resolved path.
3. **The suite header made true** (P2, and three more stale passages the review did not reach): the gcafs-anthro suite now pins a published baseline, carries real digests, links its inputs from `sources:`, and runs through `config/ursa-gcafs-anthro.yaml`.
4. **The `download_output` docstring says how S3 downloads land** (P3).
5. Nothing else changes: no behaviour the review did not question, no new surface.

## Pre-design audit (fresh facts, 2026-11-04)

Read from the harness checkout at `24c9688` (branch `feature/global-anthro-use-case`, the PR's head plus the overview spike commits).

1. **The review.** One `COMMENTED` review by the repository owner's account, body "Machine-generated comments by ufs-chem-pr-review-mcp @ 0e1088e…", four inline comments (ids 4469950312, 4469950349, 4469950360, 4469950374), no replies, no other reviews or conversation comments. The spike's `review_payload.json` is the same four comments as posted. CI on the PR is green (11 checks, the `simple-maccity` integration run included; the spike's section 3 notes the gcafs-anthro run itself is Ursa-only by design).
2. **P1 — `src/outputs.py:181`, staging cleanup.** `download_output`'s S3 branch stages into `dest / ".staging" / run_id`, moves files into `dest / run_id`, and in `finally` runs `shutil.rmtree(dest / _STAGING_DIRNAME, ignore_errors=True)` — the **whole** `.staging/`, not this run's subdirectory. Two downloads into the same `dest` for different ULIDs at the same time (two terminals, or a future batch download) would have the first to finish delete the second's staging area mid-transfer. The suggested fix removes `staging` only and `rmdir`s the parent best-effort. **Valid; accepted** (the scenario is uncommon today — the CLI downloads one run per call — but the fix is two lines and the current code is wrong in principle). The `upload_output` side has no staging and is unaffected; `baselines/stage.py`'s staging root is removed once per `ensure_baselines` call after all its ULIDs, a different shape, and not part of this PR's new code.
3. **P1 — `src/staging.py:194`, false ambiguity.** `_find_source` concatenates `rglob(name)` hits from every search-path directory. Overlapping entries (`/scratch/x/INPUT` and `/scratch/x/INPUT/CEDS`), a directory listed twice, or a symlinked tree yield the same physical file more than once, and `_link_source_less` refuses "ambiguous" with identical paths. The runbook's own advice ("the directory trees the files live under") makes a nested pair plausible. The suggested fix keys matches by `p.resolve()` and keeps the first spelling. **Valid; accepted.** Two genuinely different files of the same name stay ambiguous, as they must.
4. **P2 — the gcafs-anthro suite's comments.** Beyond the flagged block (lines 71–75: "The all-zero ULID means not published yet … keeping the quotes"), which is stale since the user published and the suite pins `01M5JV8MBW0XE9TPE0BDCGSFJZ` (unquoted — the repoint keeps a line's quoting, so the line was edited by hand, which is fine), three other header passages are stale too: the top comment ("run it through config/ursa.yaml with the override list in docs/gcafs-anthro-runbook.md" — the override list became `config/ursa-gcafs-anthro.yaml` on 2026-10-22), the inputs comment ("No remote source yet … the files are copied there from the existing staging area" — they are linked from `sources:`), and "The all-zero digests are placeholders: replace them with sha256sum …" — the digests are real since the user's `fetch --record-digests`. **Accepted and widened**: the whole header is rewritten to describe the file as it is. The review's suggested two-liner loses the "rtol as for maccity" rationale; the replacement keeps it.
5. **P3 — the `download_output` docstring** says "an existing target is synced into, never removed" and omits that S3 downloads land through `.staging/<run_id>` with per-file `os.replace`, while a directory store copies with `dirs_exist_ok`. **Accepted**: one sentence.
6. **Tests that pin the touched behaviour today**: `test_outputs.py` (`test_download_syncs_into_dest_under_the_ulid` asserts `.staging` is gone afterwards; `test_download_replaces_a_stale_staging_directory`; `test_download_into_an_existing_target_resumes`), `test_staging.py` (`test_ambiguous_link_target_is_refused` with two distinct files; `test_source_less_absent_input_is_linked_from_the_search_path`), `test_gcafs_anthro.py` (the suite loads and enumerates one combination — comments are free). Coverage is gated at 100 %.
7. **The conventions**: the user posts to GitHub (the harness and the assistant never do); replies name the commit; conventional-commit messages; the design doc's implementation notes record what landed.

## Design

### 0. Decisions

1. **Accept all four.** Two are real defects in new code (cheap, correct fixes), one is a stale comment block that is worse than the review says, one is a docstring. Nothing to push back on; the replies say "done, see <commit>" with one line of specifics each.
2. **Fix the P1s exactly as suggested, plus the test each asks for.** The reviewer's patches are right; the tests make the properties explicit: a concurrent download's staging directory survives another's cleanup, and overlapping search directories link instead of refusing.
3. **Rewrite the suite header as a whole**, not only lines 71–75, because the file is the user-facing reference for the suite schema and three other passages describe states the suite has left behind. Keep every rationale that still holds (why `timeout_s` is 30 minutes, why `sources:` exists, the search-path fallback, the `rtol`).
4. **One commit per comment** (`fix(outputs): …`, `fix(staging): …`, `docs(suite): …`, `docs(outputs): …`), so each reply names its own commit and the diff per comment is small. The user commits.
5. **No new review round is requested by the harness.** After the four replies the user re-requests the machine review or merges; either is theirs.

### 1. `download_output` cleans up only its own staging — `src/outputs.py`

```python
        finally:
            shutil.rmtree(staging, ignore_errors=True)
            with contextlib.suppress(OSError):  # empty once the last concurrent download is done
                (dest / _STAGING_DIRNAME).rmdir()
```

Docstring (P3) becomes: "`<store>/<run_id>/` into `<dest>/<run_id>/`. S3: one sync into `<dest>/.staging/<run_id>/`, moved into place file by file (`os.replace`) only when `run.yaml` arrived — an absent prefix syncs nothing, a `ValueError` naming the ULID and the store — so an existing target is refreshed in place, never removed, and files it holds that the store lacks stay. A directory store copies with `dirs_exist_ok`. Only this run's staging directory is cleaned up (concurrent downloads into one `dest` do not disturb each other). A dry run plans and writes nothing. Returns the target path."

Tests (`test_outputs.py`): `test_download_cleans_up_only_its_own_staging` — before the download, another run's staging directory `dest/.staging/<other ULID>/partial.nc` exists; after the download it still exists, this run's staging directory is gone, and `.staging/` itself remains (not empty); then with the other directory removed, a second download leaves no `.staging/` at all (the `rmdir` branch). The existing `assert not (dest / ".staging").exists()` keeps holding for the single-download case.

### 2. `_find_source` deduplicates by resolved path — `src/staging.py`

```python
def _find_source(name: str, search_path: Sequence[Path]) -> list[Path]:
    """Every distinct file called `name` under the search-path directories
    (recursive, sorted, one entry per physical file however many listed
    directories reach it): the one link target, or the ambiguity to report."""
    found: dict[Path, Path] = {}
    for directory in search_path:
        if directory.is_dir():
            for path in sorted(directory.rglob(name)):
                if path.is_file():
                    found.setdefault(path.resolve(), path)
    return list(found.values())
```

The link target is `source.resolve()` already, so which spelling wins is cosmetic (the first listed directory's). Tests (`test_staging.py`): `test_overlapping_search_directories_are_not_ambiguous` — the file under `tmp/trees/deep/`, the search path `[tmp/trees, tmp/trees/deep, tmp/trees]` (a parent, its child, and a repeat) and a symlinked directory `tmp/alias -> tmp/trees` added as a fourth entry: the input links, `detail` names one path; `test_ambiguous_link_target_is_refused` unchanged (two distinct files stay ambiguous).

### 3. The suite header, rewritten — `src/tests/config/cece/gcafs-anthro/gcafs-anthro-suite.yaml`

Top:
```yaml
# The global anthropogenic use case (issue 33): one configuration, no sweep —
# the base config is the single combination. Ursa only (the inputs live on
# Ursa; a run takes hours): run it with config/ursa-gcafs-anthro.yaml, the
# Ursa template with this suite, the QOS, the timeout cap and the author's
# single-task single-thread geometry baked in (docs/gcafs-anthro-runbook.md).
```
Inputs:
```yaml
# Inputs the base config reads, expected at <CECE_ROOT_DIR>/data/gcafs-anthro/.
# No remote source (url null): on Ursa `ufs-chem-assay fetch` links each file
# from its `sources.ursa` location (the author's staging area) and verifies the
# digest recorded here (`fetch --record-digests`, 2026-10-22); elsewhere,
# ASSAY_INPUT_SEARCH_PATH is searched by basename. Nothing is ever downloaded
# for these; filling in a url (after a one-time upload) would make one fetchable.
```
Baseline:
```yaml
# One entry: the empty selector matches the single combination of a sweep-less
# suite. The ULID is the baseline published from Ursa on 2026-10-22
# (publish-baselines repoints this line on every publish); rtol as for
# maccity: a few float64 ULPs at any magnitude.
```
The `timeout_s`, `plotting`, and `assertions` comments stand (they are current). `test_user_docs` does not read this file; `test_gcafs_anthro.py` loads it (comments are free). yamllint's line limit applies outside the `inputs:` block.

### 4. The replies (the user posts them; one per comment, on the comment's thread)

- **4469950312 (outputs staging)**: "Agreed — the `finally` removed the shared `.staging/` rather than this run's directory. Fixed as suggested in <commit>: only `dest/.staging/<run_id>` is removed and the parent is `rmdir`ed best-effort; `test_download_cleans_up_only_its_own_staging` keeps another run's staging directory alive across the download and checks the parent disappears once empty."
- **4469950349 (search dedupe)**: "Agreed — overlapping or repeated search directories (a parent and its child, a symlinked tree) produced the same file twice and a false 'ambiguous'. Fixed as suggested in <commit>: matches are keyed by `resolve()`, first spelling kept; `test_overlapping_search_directories_are_not_ambiguous` covers a parent/child pair, a repeat, and a symlinked alias, and two distinct files of one name are still refused."
- **4469950360 (suite comment)**: "Agreed, and the header was stale in three more places (the override list that became `config/ursa-gcafs-anthro.yaml`, the placeholder digests that are real now, the copy-the-files instruction that became `sources:` links). The whole header is rewritten in <commit> to describe the file as it is; the `rtol` rationale is kept."
- **4469950374 (docstring)**: "Done in <commit>: the docstring now says S3 downloads stage under `.staging/<run_id>` and replace files individually, a directory store copies with `dirs_exist_ok`, and an existing target is refreshed, never removed."

### 5. Documentation

README and `design.md` are unchanged by the two fixes (no surface changed); `design.md`'s output-upload paragraph gains the half-sentence "only its own staging directory is cleaned up" at implementation. This document's Implementation notes record the commits.

## Implementation plan (TDD, red → green → refactor; one commit per comment, by the user)

1. `test_download_cleans_up_only_its_own_staging` red → the `finally` change and the docstring (P1 + P3 together in `src/outputs.py`; two commits if the user prefers one per comment — the docstring is its own one-line change).
2. `test_overlapping_search_directories_are_not_ambiguous` red → `_find_source` keyed by resolved path.
3. The suite header rewrite; `uv run pytest src/tests/ufs_chem_assay/applications/cece/test_gcafs_anthro.py` and yamllint.
4. The `design.md` half-sentence; these Implementation notes.
5. The user posts the four replies with the commit SHAs and pushes.

## Verification

```bash
uv run pytest src/tests/ufs_chem_assay/test_outputs.py src/tests/ufs_chem_assay/test_staging.py -q
uv run pytest src/tests/ufs_chem_assay -q --cov          # 100 %
uv run pre-commit run --all-files
uv run pytest src/tests/test_driver_combos.py --dry-run --suite-config='.*-suite.yaml'
```

The local directory-store round trip (`upload-output` then `download-output --store <dir>`) still produces identical trees; the docker `simple-maccity` run is unaffected (nothing in its path changed) and is not re-run for this change unless the user wants it.

## Risks and notes

- **`rmdir` of a shared `.staging/`** races with a concurrent download that is about to create its own subdirectory: `mkdir(parents=True, exist_ok=True)` on the other side recreates the parent, so the race is harmless; the `rmdir` is best-effort by design.
- **Deduplication by `resolve()`** follows symlinks; two distinct files that are hard links of each other resolve to different paths and would still be "ambiguous" — correct, since the harness cannot know they are one inode without `stat`, and that case is contrived.
- **The suite header** is prose; the only check is that the file still loads and lints.

## Open questions

None: the four comments are unambiguous and all accepted.

## Future work

- A batch `download-output` (several ULIDs) would make the concurrent-staging property load-bearing; the fix here already supports it.
- The machine review's spike (`design/spike/20261104-0836-…`) could be re-run after the four commits to confirm it finds nothing new; that is the user's call.

## Second round: the human review (2026-11-04)

Six inline comments by the repository owner on the same PR (read 2026-11-04, after the four machine comments were answered in `09cac10`): ids 4470706315, 4470719056, 4470842648, 4470853195, 4470892524, 4470903746. They are one theme — module-level constants scattered across modules, some duplicated, when the repository's own pattern (`BaselineConventions`/`CONVENTIONS` in `src/baselines/models.py`) is a frozen pydantic model with described fields — plus one CLI nit. All six are accepted.

### Audit (2026-11-04, checkout at `09cac10`)

- `src/cli/main.py:31` — "benchmark_filename should be part of settings": `BENCHMARK_FILENAME` is a bare string in `benchmarks.py`, imported by `cli/main.py`, `tests/conftest.py`, and three test modules. `EFFECTIVE_CONFIG_NAME = "run-config.yaml"` sits beside it in `cli/main.py`; `"run.yaml"` is spelled as a literal three times in `cli/main.py`, twice in `tests/conftest.py`, once in `baselines/plan.py`, and once as `RUN_MANIFEST` in `outputs.py`; `"combos.csv"`, `"test-report.csv"`, `"descriptive_stats.csv"`, `"stats-comparison.csv"` are literals in `tests/conftest.py` and `baselines/plan.py`.
- `src/cli/main.py:757` — "can args.command be compared to a unique strenum?": `main` dispatches with five `if args.command == "<literal>"` lines and a fall-through to `_run`; the subcommand names are typed a second time in `_parser`'s `add_parser` calls. The repository already uses `@unique class X(StrEnum)` for `Platform`, `Runtime`, and the suite enums.
- `src/benchmarks.py:53` — `_TIC`, `_TOC`, `_CLOCK_FORMAT`, `_SACCT_FIELDS`, `_SACCT_RETRY_DELAY_S`, `_RSS_UNITS` are six module globals. `_CLOCK_FORMAT` duplicates `runner.CLOCK_FORMAT` (the writer and the parser of the same `tic:`/`toc:` lines; the sbatch template spells the same format for `date -u`, which cannot share a Python constant).
- `src/outputs.py:33` — `UPLOAD_RECORD`, `RUN_MANIFEST`, `_STAGING_DIRNAME`; the last is the third spelling of `.staging` (`staging.STAGING_DIRNAME`, `BaselineConventions.staging_dirname`).
- `src/settings.py:37` — `ENV_PREFIX`, `LEGACY_ENV_PREFIX`, `_ENV_FILE`, `DEFAULT_BASELINE_STORE`, `DEFAULT_OUTPUT_STORE`; the prefixes are imported by `cli/main.py`, `cli/stages.py`, and two test conftests, the defaults by `cli/run_config.py`.
- `src/staging.py:43` — `STAGING_DIRNAME`, `_CHUNK` (duplicates `BaselineConventions.digest_chunk`), `ENV_INPUT_SEARCH_PATH` (the prefix spelled out by hand: `"ASSAY_INPUT_SEARCH_PATH"`, while `cli/stages.py` builds the same name from `ENV_PREFIX`).
- **Left where they are, deliberately** (single-owner, single-use, not conventions of the harness as a whole): `cli/override.KEY_SEPARATOR`, `combos.NAME_SEPARATOR`, `cli/stages._DRIVER_COMBOS/_SHEBANG/SETTINGS_NOT_MIRRORED/LMOD_INIT`, `report._RESULT_PRECEDENCE/_REPORT_COLUMNS`, `analysis._STATS_PER_VARIABLE`, `comparison._COMPARISON_COLUMNS`, `s3_sync.S3_URI_PATTERN/_S3_SCHEME/_S3_ARN_PREFIX/_INSTALL_HINT`, `selection.BUILTIN_CONFIG_DIR`, `plotting.PLOTS_DIRNAME/BIAS_PLOTS_DIRNAME/_FRAME_DURATION_MS/_FOOTER/ZERO_COLOR`, `models/suite_config.IGNORE_VALUE/S3_OBJECT_URI_PATTERN/SHA256_PATTERN`, `identity.HARNESS_NAME/HARNESS_ROOT`, `logs.LOGGER_NAME/_FORMAT`, `cli/main._INDEX`, and the adapter's own `applications/cece/settings.ENV_PREFIX = "CECE_"` (the application's prefix; that it equals the harness's legacy prefix is history, not a shared convention). The baseline-only names (`manifest`, `driver_test`, `comparison_test`, `unpublished_ulid`) stay on `BaselineConventions`.

### 6. One harness-wide conventions model — `src/models/conventions.py` (new)

`class Conventions(StrictModel)`, frozen, every field `Field(default, description=...)`, one module instance `CONVENTIONS`. The names every module agrees on, grouped by what they are:

- **Environment**: `env_prefix = "ASSAY_"`, `legacy_env_prefix = "CECE_"`, `env_file = ".env"`; a method `env_var(name) -> str` (`CONVENTIONS.env_var("input_search_path") == "ASSAY_INPUT_SEARCH_PATH"`) so no module spells a variable name by hand again.
- **Default stores**: `default_baseline_store = "s3://ufs-chem/baselines"`, `default_output_store = "s3://ufs-chem/assay-output"` (`Settings` field defaults and the run config's `baselines.store`/`outputs.store` defaults read these).
- **A run's files** (written by the pytest session, read by publish/record/upload): `run_manifest = "run.yaml"`, `test_report = "test-report.csv"`, `combos_csv = "combos.csv"`, `descriptive_stats = "descriptive_stats.csv"`, `stats_comparison = "stats-comparison.csv"`, `benchmarks = "benchmarks.csv"`, `upload_record = "upload.yaml"`, `effective_config = "run-config.yaml"`.
- **Transfers**: `staging_dirname = ".staging"` (inputs, baselines, output downloads — one spelling), `digest_chunk = 1 MiB` (the SHA-256 read size for inputs and baselines).
- **Clock**: `clock_format = "%Y-%m-%dT%H:%M:%SZ"` (the `tic:`/`toc:` lines `runner` writes and `benchmarks` parses).

`BaselineConventions` loses `staging_dirname` and `digest_chunk` (they were the duplicates) and its instance is renamed `BASELINE_CONVENTIONS`, so the two models are never confused at an import site. `settings.py` imports `CONVENTIONS` for its `SettingsConfigDict` (`env_prefix`, `env_file`), the legacy sources, and the two store defaults, and re-exports it so `from settings import CONVENTIONS` works — the answer to "part of settings": the file name is not an environment-derived, overridable value (nothing on `Settings` is fixed), so it is a convention beside `run.yaml` and `.staging`, reachable from `settings`.

### 7. The benchmarking knobs — `src/benchmarks.py`

`class BenchmarkConventions(StrictModel)`, frozen, instance `BENCHMARK_CONVENTIONS`: `tic_pattern`, `toc_pattern` (the regex sources; compiled at the use site — `re` caches), `sacct_fields` (the `-o` list, a tuple), `sacct_retry_delay_s`, `rss_units` (the `MaxRSS` suffix multipliers). The clock format comes from the shared model; `runner.CLOCK_FORMAT` goes.

### 8. The output-upload names — `src/outputs.py`

The three module constants go; the module reads `CONVENTIONS.upload_record`, `CONVENTIONS.run_manifest`, `CONVENTIONS.staging_dirname`.

### 9. The input-staging names — `src/staging.py`

`STAGING_DIRNAME`, `_CHUNK`, `ENV_INPUT_SEARCH_PATH` go; the module reads `CONVENTIONS.staging_dirname`, `CONVENTIONS.digest_chunk`, `CONVENTIONS.env_var("input_search_path")`.

### 10. The CLI dispatch — `src/cli/main.py`

`@unique class Command(StrEnum)`: `RUN = "run"`, `FETCH = "fetch"`, `PUBLISH_BASELINES = "publish-baselines"`, `RECORD_BENCHMARKS = "record-benchmarks"`, `UPLOAD_OUTPUT = "upload-output"`, `DOWNLOAD_OUTPUT = "download-output"`. `_parser` registers each subparser by its member, and `main` resolves `Command(args.command)` and dispatches through one `dict[Command, Callable[[Namespace], int]]` — no string comparisons, and a member without a handler is a test failure (`test_every_command_has_a_handler`). `EFFECTIVE_CONFIG_NAME` becomes `CONVENTIONS.effective_config`; the `run.yaml` and `benchmarks.csv` literals become the model's fields.

### 11. Tests

- New `test_conventions.py`: every field described; the model frozen and `extra="forbid"`; `env_var` builds `ASSAY_*` names; the values pinned (`run.yaml`, `.staging`, the prefixes, the stores) so a change is a deliberate one.
- `test_benchmarks.py`: `BENCHMARK_CONVENTIONS` fields described; the `BENCHMARK_FILENAME` assertion moves to the shared model; `sacct` tests keep their field list through the model.
- `test_cli.py`: `Command` is unique, its values are exactly the parser's subcommands, and every member dispatches.
- The modules that imported the moved names (`tests/conftest.py`, `tests/ufs_chem_assay/conftest.py`, `test_settings.py`, `test_staging.py`, `test_outputs.py`, `test_cli.py`, `test_benchmarks.py`, `test_baselines.py`) import from the models instead. Literal file names in tests stay literal where they pin the on-disk name on purpose.

### 12. Documentation and replies

`design.md` gets a short "Conventions" paragraph (the model, where it lives, what belongs on it and what does not). README is unchanged (no user-facing surface moves). The six replies are drafted in the Implementation notes and appended to `replies_payload.json`.

## Implementation notes (2026-11-04)

Implemented as designed, red → green, one change per comment; the user commits (one commit per comment is the suggested shape) and fills the SHAs into `design/spike/20261104-0836-global-anthro-pr-35-review/replies_payload.json` before posting.

- **P1 staging cleanup** (`src/outputs.py`): the `finally` removes `dest/.staging/<run_id>` only and `rmdir`s the parent best-effort; `test_download_cleans_up_only_its_own_staging` keeps another run's `partial.nc` alive through a download, sees the parent stay while it is not empty, and sees it go once empty.
- **P3 docstring** (`src/outputs.py`, same function): the staging-and-replace mechanism, the directory-store copy, "refreshed, never removed", the per-run cleanup.
- **P1 search dedupe** (`src/staging.py`): `_find_source` keys matches by `resolve()`, first spelling kept; `test_overlapping_search_directories_are_not_ambiguous` searches a parent, its child, the parent again, and a symlinked alias, and links; `test_ambiguous_link_target_is_refused` (two distinct files) unchanged.
- **P2 suite header** (`src/tests/config/cece/gcafs-anthro/gcafs-anthro-suite.yaml`): the top, inputs, and baseline comments rewritten as in section 3 — the run config, the `sources:` links and the recorded digests, the published baseline (2026-10-22 by its ULID) — the `rtol` rationale kept; the `timeout_s`, `plotting`, and `assertions` comments untouched.
- `design.md`: the output-upload paragraph notes the per-run cleanup.

**Gates**: harness suite passed at 100 % coverage, hooks green, every suite dry-runs. The reply payload has the four bodies with `<commit>` placeholders.

## Implementation notes, second round (2026-11-04)

Implemented as designed in sections 6–12, red → green; the user commits (one commit for the consolidation is the natural shape — the six comments are one change — and fills its SHA into the six new entries of `replies_payload.json` before posting).

- **`src/models/conventions.py`** (new): `Conventions`/`CONVENTIONS` — the prefixes and `.env` name, the default stores, the eight run-file names, `.staging`, the digest chunk, the clock format, and `env_var(name, legacy=False)`. Every field described; frozen; `extra="forbid"`.
- **`src/baselines/models.py`**: `BaselineConventions` keeps `manifest`, `driver_test`, `comparison_test`, `unpublished_ulid`; the instance is `BASELINE_CONVENTIONS` (`baselines/__init__.py`, `plan.py`, `publish.py`, `stage.py` follow); `stage.py` and `store.py` read `.staging` and the digest chunk from the shared model; `plan.py` reads `run.yaml`, `test-report.csv`, `combos.csv` through it.
- **`src/settings.py`**: the five module constants are gone; `SettingsConfigDict`, the legacy sources, the store defaults, and the `JOB_ENV` error read `CONVENTIONS`; `__all__` re-exports `CONVENTIONS` and `Conventions`. `cli/run_config.py` takes the store defaults from the model; `cli/stages.py` spells every export with `CONVENTIONS.env_var(...)`.
- **`src/cli/main.py`**: `Command` (`@unique` `StrEnum`), the subparsers registered by member, `HANDLERS` (one entry per member), `main` → `HANDLERS[Command(args.command)](args)`; `EFFECTIVE_CONFIG_NAME` and the `run.yaml`/`benchmarks.csv` literals → the model; the log-level lookup → `env_var("log_level")` and its legacy twin.
- **`src/benchmarks.py`**: `BenchmarkConventions`/`BENCHMARK_CONVENTIONS` (`tic_pattern`, `toc_pattern`, `sacct_fields`, `sacct_retry_delay_s`, `rss_units`); the clock format from the shared model. **`src/runner.py`**: `CLOCK_FORMAT` gone, the clock line uses `CONVENTIONS.clock_format`. **`src/outputs.py`**, **`src/staging.py`**: their constants gone, the model read instead. **`src/tests/conftest.py`**: `benchmarks.csv`, `run.yaml`, `test-report.csv`, `descriptive_stats.csv`, `stats-comparison.csv`, `combos.csv` written through the model.
- **Tests**: `test_conventions.py` (descriptions, frozen/strict, the pinned values, `env_var`, the baseline model's narrowed fields, the `settings` re-export); `test_cli.py` (`test_commands_are_the_parsers_subcommands`, `test_every_command_has_a_handler`); `test_benchmarks.py` (the benchmark model's descriptions and two values); the importers of the moved names repointed (`tests/ufs_chem_assay/conftest.py`, `test_settings.py`, `test_staging.py`, `test_outputs.py`, `test_cli.py`, `test_baselines.py`). Literal file names in tests that pin an on-disk name stay literal.
- **`design.md`**: a "Conventions" subsection under Settings (what belongs on the model and what does not; the `Command` enum) and the layout tree (`models/conventions.py`, `cli/`).
- **Unchanged on purpose**: the constants listed under "Left where they are" in the audit; the sbatch template's `date -u +%Y-%m-%dT%H:%M:%SZ` (shell, the same format by convention — `test_slurm_runtime` would catch a drift through `parse_tic_toc`); README (no user-facing surface moved).

**Gates**: harness suite 788 passed at 100 % coverage; all hooks green (ruff, mypy strict, vulture, codespell, yamllint, yamlfix); every suite dry-runs (2907 combinations skipped). One test, `test_analysis.py::test_compute_file_stats_min_positive_is_null_for_an_all_zero_field`, fails in this session's sandbox with `PermissionError` from netCDF4 writing its temporary `zeros.nc`; it fails identically on an export of the committed tree (`09cac10`), touches nothing this change touched, and passed in the earlier sessions of this branch — an environment matter to re-check outside the sandbox, not a regression. The docker `simple-maccity` run (no `--dry-run`, default output root): 21 passed; the output root carries `run.yaml`, `combos.csv`, `test-report.csv`, `descriptive_stats.csv`, `stats-comparison.csv`, `benchmarks.csv` (3 rows, one per combination, each also in its combo directory) and the `.out` files' `tic:`/`toc:` lines parse — every renamed name round-trips through the real session.

# appendix: original notes

## always do

- include updating design.md as part of the implementation
- update the harness tests (`src/tests/ufs_chem_assay`) in addition to any changes to test_driver_combos.py
- update README.md with any necessary documentation changes in case of an api adjustment
- use pydantic models as opposed to dataclasses
  - all pydantic fields should include a description like `... = Field(description="<description content here>", ...`
- do *not* add driver bugs to known bugs in `README.md` unless explicitly told to do so
- use a test-driven development, red-green-refactor approach for all fixes and features (when possible)
- maintain original design sections when refining design docs - create an appendix
  - summarize conversational updates in the appendix following original refinement target
  - refinements never create files. they only edit the target document that is being refined.
  - `design.md` may be updated during the _implementation_ phase only.
- when using python `typing`, avoid `Any` as much as possible
- **never, ever, ever** commit code or use git actions that perform updates - the user always updates
- always prefer the harness's logging implementation over raw python print statements
- no need for wrapping when writing to the `./design` folder. wrapping will be handled by the user's ide
- persist the agy session id and resume command in any design/refined files for resumability

## session

- **Session ID**: `c30fb022-0fff-43a5-9908-1cfbe2cb2d88`
- **Resume Command**: `agy --resume c30fb022-0fff-43a5-9908-1cfbe2cb2d88`

### testing

- not necessary for design documents in the `spike` folder - code *should not* change for spikes
- *all* suites should pass `--dry-run`
- run `simple-maccity-suite.yaml` without `--dry-run` for integration testing with the driver
- only run examples when requested to do so
- no need to run tests for spikes/documentation-only tasks
- pre-commit hooks pass

## requirements

- respond to review comments on https://github.com/benkozi/ufs-chem-assay/pull/35

The review as read on 2026-11-04 (one machine-generated review, four inline comments):

- `src/outputs.py:181` **[P1]** `download_output`'s `finally` removes the whole `.staging/` directory, not this run's; concurrent downloads into one `dest` would disturb each other. Suggested: remove `staging` only, `rmdir` the parent best-effort.
- `src/staging.py:194` **[P1]** `_find_source` reports false ambiguity when overlapping, repeated, or symlinked search directories reach the same file. Suggested: deduplicate by `resolve()`.
- `src/tests/config/cece/gcafs-anthro/gcafs-anthro-suite.yaml:71` **[P2]** the "all-zero ULID means not published yet" comment is stale: the suite pins a published baseline.
- `src/outputs.py:138` **[P3]** the `download_output` docstring omits the staging-and-replace mechanism of S3 downloads.

## conversational updates

- 2026-11-04 (refinement, no user input beyond the requirement): the four comments were checked against the code; all four are valid and accepted. Beyond them, the audit found three more stale passages in the suite header (the override list, the placeholder digests, the copy instruction), so the header is rewritten whole. The reply texts are drafted in section 4 for the user to post with the commit SHAs; one commit per comment is the suggested shape. No files other than this document were created or changed.
- 2026-11-04 (user: how would we auto-respond to comments? create a payload like the review one; then: implement): replies have no draft state in GitHub's REST API (one `POST …/comments/{id}/replies` per thread), so the human step is reviewing the payload before posting; `replies_payload.json` sits beside the review payload with the four bodies, the comment ids, and `<commit>` placeholders. The four changes are implemented (Implementation notes); the user commits, fills the SHAs, posts the replies.
- 2026-11-04 (user: read the six new human comments on PR 35 and respond/implement): all six accepted — the scattered module constants (`benchmarks.py`, `outputs.py`, `settings.py`, `staging.py`) consolidate into one frozen `Conventions` model in `src/models/conventions.py` (plus a `BenchmarkConventions` for the `sacct`/`tic`/`toc` knobs), which also removes the repeats the last comment pointed at (`.staging` ×3, the digest chunk ×2, the clock format ×2, `run.yaml` ×7, the hand-spelled `ASSAY_INPUT_SEARCH_PATH`); `BaselineConventions` keeps only its baseline-specific names as `BASELINE_CONVENTIONS`; the CLI dispatches on a `@unique` `Command` `StrEnum`. Sections 6–12 above; replies in the Implementation notes.
