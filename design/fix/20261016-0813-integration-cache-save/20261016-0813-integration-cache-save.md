# Fix: the integration job's CECE build cache never saves — a root-owned pip cache under `build/python` defeats the runner's `tar`, so every run recompiles the driver

Written 2026-10-16 from the user's question on https://github.com/benkozi/ufs-chem-assay/actions/runs/38911968499/job/117009788118?pr=26 ("doesn't seem like we should rebuild cece unless the commit hash has changed"). The workflow already intends exactly that — `cece/build` is cached under a key carrying the CECE SHA and the build step runs only on a miss — but the **save half has failed on every run since 2026-10-08**, silently: `actions/cache/save` reports a failed `tar` as a warning and the step stays green. The job spends 3 min 20 s of its ~5 minutes rebuilding a driver it should restore in seconds.

# session

- **Session ID**: `0d287e13-6a80-4b72-9d8b-39a30b7b4ca0` (Claude Code, desktop app)
- **Resume Command**: `claude --resume 0d287e13-6a80-4b72-9d8b-39a30b7b4ca0`

## Goal

1. The save succeeds: after a run that built the driver, `gh cache list` shows `cece-build-<CECE SHA>` and the save step's log ends with `Cache saved with key: …`.
2. The next run with the same CECE SHA logs `Cache hit for: cece-build-<sha>`, skips "Build CECE in the container", and finishes in roughly a minute and a half instead of five.
3. A save that fails again is **visible**: a step goes red (without aborting the suite run that follows — the job is `continue-on-error` already, and a cache problem must not hide the integration result).
4. The workflow header and `design.md` describe the mechanism truthfully: the prefix-fallback restore does not give an incremental build (mtimes), its value is skipping configure and the `FetchContent` clones.

Out of scope: a compiler cache (ccache would make a CECE-SHA change cheap too, but `cece/cece-dev` ships no ccache — an upstream change); pinning `CECE_REF` to a SHA; shrinking the archive by excluding the pip cache (see Decision 2 for why not now).

## Pre-design audit (fresh facts, 2026-10-16)

1. **Every recent run built the driver.** `gh run view <id> --json jobs` for the last five runs (develop pushes and PRs, 2026-10-15/16): "Restore the CECE build directory" success, "Build CECE in the container" success at **3 min 20 s**, "Save the CECE build directory" success. Yesterday's develop push (38824374994) totalled 5 min 07 s; today's PR #26 run (38911968499) the same shape.
2. **The exact key has been stable for two days.** `git ls-remote ufs-community/CECE develop` → `35f2bc41e067c291b8c6ba3bf6eefc0a84ea558c`, committed 2026-10-14 17:46 UTC (29 commits / 300 files after the previous pin `b28ba696…`, dated 2026-09-02 — the fork branch the harness used before CECE#154 merged on 2026-10-08). Every run since 2026-10-14 asked for `cece-build-35f2bc41…`.
3. **No such cache exists.** `gh cache list --repo benkozi/ufs-chem-assay` shows one build cache, `cece-build-b28ba69603c0964b1aedbad3994c86722bd58bb3` (180 MB, created 2026-10-06 19:57, `ref=refs/heads/develop`, last accessed today — as the fallback). Total repository cache use: ~3.4 GB of the 10 GB limit, so eviction is not the cause.
4. **The restore log**: `key: cece-build-35f2bc41…` → `Cache hit for restore-key: cece-build-b28ba696…` → `Cache restored from key: cece-build-b28ba696…`. `cache-hit` is `false` on a restore-keys fallback (exact match only), so the build runs — correct — and the save is reached.
5. **The save log (today and yesterday, identical)**: `tar --posix -cf cache.tzst … --files-from manifest.txt --use-compress-program zstdmt` then dozens of `tar: cece/build/python/cache/http-v2/…: Cannot open: Permission denied`, then `##[warning]Failed to save: "/usr/bin/tar" failed with error: The process '/usr/bin/tar' failed with exit code 2`. The step's conclusion is `success`.
6. **Why the files are unreadable.** The container builds as root (and the restore side deliberately `chown -R 0:0`s the restored tree so git's `safe.directory` check passes in `build/_deps`); CECE's CMake sets up a Python environment under `build/python/` — a `venv/`, a `tmp/`, and pip's HTTP download cache `cache/http-v2/`, which pip writes at mode `0600`. The runner's `tar` (user `runner`) can read everything root-owned at 0644/0755, and nothing at 0600. Locally (`../CECE/build`): `python/` is 293 MB of a 1.7 GB tree; the compressed archive in CI was 181 MB.
7. **Why it worked on 2026-10-06.** The pinned CECE branch then (`b28ba696…`, 2026-09-02) did not create `build/python/cache`; the 29 commits since (the `develop` the harness moved to on 2026-10-08) do. The save last succeeded on the last run before the move.
8. **`actions/cache/save` cannot fail the step** on a tar error by design (its README: "the cache will always be saved if generated, or a warning will be generated"). There is no `fail-on-error` input. The `gh` CLI is on `ubuntu-latest`; listing caches needs `actions: read` on the token; the job currently has only the workflow-level `contents: read`.
9. **Exclusions cannot prune a subdirectory of a cached directory.** `@actions/cache`'s `resolvePaths` globs the `path` patterns with `implicitDescendants: false` and hands the *matches* to `tar` (`--files-from manifest.txt`): `cece/build` resolves to the one directory entry and `tar` recurses into it; a `!cece/build/python/cache` line removes nothing from that manifest. Excluding would need `cece/build/*` + `!cece/build/python` + `cece/build/python/*` + `!cece/build/python/cache` + `!cece/build/python/tmp` — workable, but the mechanism is non-obvious and the archive saving is small.
10. **The fallback restore does not yield an incremental build.** The header comment and `design.md` say so themselves ("recompiled every CECE object because the fresh checkout's mtimes postdate the restored objects"), and the 3 min 20 s on every fallback run confirms it; cold builds on 2026-10-06 took 2–3.5 minutes. What the fallback does buy is the configure step and the `FetchContent` clones under `build/_deps` (`CMakeCache.txt` present → configure skipped).

## Design

### 0. Decisions

1. **Re-own the tree to the runner before the save** — one step, the mirror image of the existing "Re-own the restored build tree for the container": `sudo chown -R "$(id -u):$(id -g)" cece/build`, gated exactly as the save is (`always() && cache-hit != 'true' && build.outcome == 'success'`). This is the fix. It is robust against any root-only file the CECE build may add in future, not just today's pip cache; the restore side hands the tree back to root on the next run, so the round trip stays consistent.
2. **No path exclusions now.** Fact 9: excluding the pip cache needs a five-pattern dance for a modest saving, and excluding `build/python` wholesale would drop the venv the build may rely on when configure is skipped (untested here). Recorded under Future work with the mechanism, so whoever picks it up does not repeat the "`!subdir` does nothing" discovery.
3. **Make a failed save red, without aborting the suite run.** A "Verify the build cache was saved" step after the save, same gate, `continue-on-error: true` at the *step* level: `gh cache list --key cece-build-<sha> --json key` filtered to the exact key; zero matches → `::error::` + exit 1. The step shows red in the run, the job carries on to stage inputs and run the suite. Needs `permissions: { contents: read, actions: read }` on the job (a job-level block replaces the workflow-level one, so both are listed).
4. **Truthful comments.** The header's "a new SHA seeds an incremental build from the `cece-build-` prefix fallback" becomes "a new SHA restores the prefix fallback, which skips configure and the FetchContent clones but recompiles the objects (mtimes)"; a paragraph records this failure mode and the date. `design.md`'s integration-job bullet gets the same correction and the 2026-10-16 fix.
5. **Branch.** The user asked for this in the current session, which sits on PR #26's branch (`dependabot/uv/python-6d21107c6c`) with the ruff-0.17 fix uncommitted. The change is left uncommitted; whether it ships with PR #26 or on its own branch is the user's call at commit time (the two changes touch disjoint files).

### 1. `integration.yaml`

Job-level permissions:

```yaml
    permissions:
      contents: read
      actions: read  # the save-verification step lists the repository's caches
```

Steps, between "Build CECE in the container" and "Save the CECE build directory", and after the save:

```yaml
      # The container built as root; the save's tar runs as the runner user
      # and cannot read root-only files -- CECE's build leaves pip's HTTP
      # cache under build/python/cache at mode 0600. actions/cache/save turns
      # a tar failure into a warning and a green step: from 2026-10-08 (the
      # move to CECE develop, whose build creates that cache) to 2026-10-16
      # no save succeeded and every run rebuilt the driver (3m20s). Hand the
      # tree back to the runner before saving; the restore side gives it to
      # root again (above).
      - name: Re-own the built tree for the cache save
        if: always() && steps.restore-build.outputs.cache-hit != 'true' && steps.build.outcome == 'success'
        run: sudo chown -R "$(id -u):$(id -g)" cece/build
      - name: Save the CECE build directory
        ...unchanged...
      # A failed save is only a warning in actions/cache/save: assert the key
      # exists. Red but non-fatal (step-level continue-on-error): a cache
      # problem must not hide the suite result below.
      - name: Verify the build cache was saved
        if: always() && steps.restore-build.outputs.cache-hit != 'true' && steps.build.outcome == 'success'
        continue-on-error: true
        env:
          GH_TOKEN: ${{ github.token }}
          KEY: cece-build-${{ steps.cece.outputs.sha }}
        run: |
          caches=$(gh cache list --repo "$GITHUB_REPOSITORY" --key "$KEY" --json key)
          found=$(printf '%s' "$caches" | jq "map(select(.key == \"$KEY\")) | length")
          if [ "$found" -eq 0 ]; then
            echo "::error::cache $KEY was not saved -- see the warning in the save step"
            exit 1
          fi
```

### 2. `design.md` — CI and releases, the integration-job bullet

Replace "the save step is gated on the build succeeding so a partial tree never lands under the exact key" with that plus: since 2026-10-16 the built tree is re-owned to the runner before the save (the container builds as root and CECE's build leaves pip's 0600 HTTP cache under `build/python`; the runner's `tar` could not read it and `actions/cache/save` only warned — no cache was saved from 2026-10-08 to 2026-10-16 and every run rebuilt), and a verification step lists the key after saving and goes red (non-fatal) when it is absent. Correct the fallback claim as in Decision 4. Point at this document.

## Implementation plan

1. `integration.yaml`: job permissions, the re-own step, the verify step, header comment.
2. `design.md` bullet.
3. `uv run pre-commit run --all-files` (yamlfix and yamllint cover the workflow; `actionlint` is not installed locally — CI's `actionlint` job lints it on the PR).
4. Leave uncommitted (Decision 5).

## Verification

Locally only the linters can run. The proof is on the next CI run of the branch the change lands on:

1. "Save the CECE build directory" logs `Cache saved with key: cece-build-<sha>` and no `Failed to save` warning; "Verify the build cache was saved" is green; `gh cache list --repo benkozi/ufs-chem-assay --key cece-build-` lists the new key (under the run's ref: `refs/pull/<n>/merge` for a PR run, `refs/heads/develop` after merge).
2. Re-running that job (same CECE SHA): "Restore the CECE build directory" logs `Cache hit for: cece-build-<sha>`, "Build CECE in the container" is skipped, the two new steps are skipped, the suite runs, job total ≈ 1.5 min.
3. The `actionlint` CI job stays green.

Caveat on scope: a PR run's cache is visible only to that PR and its re-runs; other PRs hit develop's cache, which exists once a develop push has run with the fix (the `push` trigger exists for that). After this lands, the first run per CECE SHA per scope still builds — by design.

## Risks and notes

- `chown -R` over a 1.7 GB tree is seconds on the runner (the restore-side chown already does it).
- The verify step depends on the cache REST API listing a just-saved entry immediately; it does in practice (the save action itself reads back nothing). If it ever lags, the step is red but non-fatal, and the next run's restore log is the tie-breaker.
- `gh cache list --key` is a prefix match, hence the exact-key `jq` filter.
- The job-level `permissions` block must repeat `contents: read`, or checkout loses it.

## Open questions

None blocking.

## Future work

- **Exclude the pip cache and `tmp/` from the archive** with the pattern set in audit fact 9 — after confirming that the restored `build/python/venv` alone lets the skipped-configure build run. Small archive saving; the chown makes it unnecessary for correctness.
- **ccache in `cece/cece-dev`** (upstream) would make a CECE SHA change cost only the changed objects; CMake honours `CMAKE_CXX_COMPILER_LAUNCHER` from the environment at configure time.

## Implementation notes (2026-10-16)

**Outcome: implemented as designed; linters green; the proof is the next CI run. Left uncommitted on `dependabot/uv/python-6d21107c6c` beside the uncommitted ruff-0.17 fix (disjoint files) — the user decides whether it ships with PR #26 or on its own branch.**

- `integration.yaml`: header paragraphs (fallback corrected; the failure mode and dates), job-level `permissions` (`contents: read`, `actions: read`), "Re-own the built tree for the cache save" before the save, "Verify the build cache was saved" after it (step-level `continue-on-error: true`). `design/design.md`: the integration-job bullet as in §2.
- `uv run pre-commit run --all-files`: every hook passes. One adjustment from §1 as first written: yamlfix folds a backslash-continued line inside a `run: |` block into one line, and yamllint then rejects it at 150 > 120 columns — the `gh cache list` call and the `jq` filter are two statements now (the design snippet above is the final form). actionlint (the `rhysd/actionlint` container CI uses, same two ignores): clean.
- The `jq` filter was tested locally against a two-entry list (prefix match `cece-build-abcdef` beside the exact key): 1 with the exact key present, 0 without.
- Not verifiable locally: the save itself and the hit on the following run (Verification 1–2). What to look for on the first CI run after this lands is written there.

**CI verification (2026-10-16, commit `e63d642` on PR #26, run 38914844029):** "Re-own the built tree for the cache save" and "Verify the build cache was saved" both green; the save step logs `Cache saved with key: cece-build-35f2bc41e067c291b8c6ba3bf6eefc0a84ea558c` with no `Failed to save` warning; `gh cache list` shows that key (385 MB, `ref=refs/pull/26/merge` — the tree now carries `build/python`, hence larger than the 180 MB of 2026-10-06). This run still built the driver (first run after the fix; 4 min 40 s total). Verification 2 — the exact hit with the build skipped — is the next run on this PR (a re-run of the job, or the next push); the develop-scoped cache appears with the first develop push after merge, which builds once more. The same push's `ci` run: `pre-commit` and `tests` green; `release-preview / release-verify` failed with a 403 (`Resource not accessible by integration`) creating its test tag ref — the identical step passed on this PR 20 minutes earlier with the same App and actor, GitHub reported no incident, and nothing in that workflow changed; unrelated to this fix, re-run to confirm transient.

**Verification 2 met (2026-10-16, run on commit `699aae3`, an empty re-trigger push):** "Restore the CECE build directory" logged `Cache hit for: cece-build-35f2bc41e067c291b8c6ba3bf6eefc0a84ea558c` (386 MB); "Build CECE in the container", the re-own, the save, and the verify steps were all skipped; the suite ran; the job took 1 min 19 s (15:31:06 → 15:32:25 UTC) against 4 min 40 s for the run that built. The release-verify 403 turned out not to be transient; it has its own document, `design/fix/20261016-0936-release-verify-403/`.

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

- **Session ID**: `0d287e13-6a80-4b72-9d8b-39a30b7b4ca0` (Claude Code, desktop app)
- **Resume Command**: `claude --resume 0d287e13-6a80-4b72-9d8b-39a30b7b4ca0`

### testing

- not necessary for design documents in the `spike` folder - code *should not* change for spikes
- *all* suites should pass `--dry-run`
- run `simple-maccity-suite.yaml` without `--dry-run` for integration testing with the driver
- only run examples when requested to do so
- no need to run tests for spikes/documentation-only tasks
- pre-commit hooks pass

## requirements

User request (2026-10-16, verbatim): "can we speed up this pipeline somehow? doesn't seem like we should rebuild cece unless the commit hash has changed: https://github.com/benkozi/ufs-chem-assay/actions/runs/38911968499/job/117009788118?pr=26" — then, after the diagnosis: "no, let's implement here. create a fix design doc" and "and implement :)".

## conversational updates

- 2026-10-16 (creation): the diagnosis was done in the session before the document (the user asked a question first); the document records it and the fix. No refinement round was requested — the user asked for the doc and the implementation together.
