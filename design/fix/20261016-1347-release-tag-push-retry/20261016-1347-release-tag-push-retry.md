# Fix: the develop release pushed the `0.1.0-rc.6` bump commit but GitHub rejected the tag push ("missing necessary objects") — repair the tag, retry tag pushes, and stop the report from calling a failed push "no release"

Written 2026-10-16 from https://github.com/benkozi/ufs-chem-assay/actions/runs/38936439352/job/117094483229 (tracked as https://github.com/benkozi/ufs-chem-assay/issues/28): the first push to develop after PR #26 merged (`18d0a61`) ran `release-psr`, which computed `0.1.0-rc.6`, committed the bump (`e7f5067`: `pyproject.toml`, `uv.lock`, `CHANGELOG.md`), pushed the branch — and then had its tag push rejected: `! [remote rejected] v0.1.0-rc.6 -> v0.1.0-rc.6 (missing necessary objects)`. develop now carries the version-bump commit without its tag.

# session

- **Session ID**: `0d287e13-6a80-4b72-9d8b-39a30b7b4ca0` (Claude Code, desktop app)
- **Resume Command**: `claude --resume 0d287e13-6a80-4b72-9d8b-39a30b7b4ca0`

## Goal

1. **Repair**: `v0.1.0-rc.6` exists on the remote as an annotated tag on `e7f5067`, as python-semantic-release (PSR) would have made it, before the next push to develop (otherwise PSR computes `0.1.0-rc.6` again and stacks a second bump commit).
2. **Prevent**: when PSR's branch push succeeded and only the tag push failed, the job pushes the tag itself with a short backoff, and the release counts as published; when the branch push failed, nothing is retried and the job fails as today.
3. **Report truthfully**: a production run that computed a version but did not publish says so in the job summary; today it prints "Will Release? false … No release will be triggered based on conventional commit rules", which is the wording for "nothing to release".

## Pre-design audit (fresh facts, 2026-10-16)

1. **The job** (`ci.yaml` → `semantic-release.yml` `release-psr`, production mode): `release-verify` passed (the head-commit probe on a push is `github.sha`, a real commit); PSR `-v version --commit --tag --push --no-vcs-release` logged `The next version is: 0.1.0-rc.6`, ran the build command (`uv lock --upgrade-package`), `Verified upstream branch 'origin/develop' has not changed` at 17:05:25 UTC, then at 17:05:27 `git push <remote> tag v0.1.0-rc.6` → `[remote rejected] … (missing necessary objects)`, `##[error] Failed to push tag (v0.1.0-rc.6) to remote`. The step failed; "Evaluate and report release" ran with `VERSION=0.1.0-rc.6 TAG=v0.1.0-rc.6 RELEASED=false` and printed the "no release will be triggered" summary.
2. **The remote now**: `origin/develop` = `e7f5067` "0.1.0-rc.6" by `semantic-release <semantic-release>` (the branch push succeeded; its own ci run 38936996497 passed, `release-psr` skipped on the bump-commit marker). Tags: `v0.1.0-rc.5` is the newest; no `v0.1.0-rc.6`. PSR's tags are annotated (`git cat-file tag v0.1.0-rc.5`: tagger `semantic-release <semantic-release>`, message `v0.1.0-rc.5`).
3. **PSR's push order** (`cli/commands/version.py`, v10): `verify_upstream_unchanged` → `git_push_branch` → `git_push_tag` for the version tag (then partial tags, not for prereleases). The two pushes are consecutive, about two seconds apart in the log; there is no retry, no delay, and no option for either.
4. **The rejection is GitHub's and recent.** "missing necessary objects" is the server saying the pushed ref points at an object it does not have — but the commit *was* on the server (the branch push had just succeeded, and develop carries it). Two issues filed 2026-10-13 describe the same thing: i2mint/wads#122 ("no retry when the tag push is rejected right after the bump push ('missing necessary objects')" — an Actions job whose bump push succeeded and whose tag push, seconds later, was rejected) and gitbutlerapp/grit#1020 (annotated tags only; lightweight tags and tag-with-commit pushes fine). A plausible mechanism is replication lag between the receive of the commit and the validation of a tag object that references it; whatever the mechanism, it is a transient, recoverable by pushing the tag again.
5. **No incident on githubstatus.com** today (the last resolved one is 2026-10-15 18:56 UTC). No ruleset covers tags (`base` targets branches; tag pushes are not subject to it), so the App token can push the tag — it pushed the commit a moment earlier with the same credential.
6. **What the job has after PSR fails**: PSR runs in its container on `/github/workspace`, which is the runner's checkout; the tag object and `refs/tags/v0.1.0-rc.6` exist in that repository afterwards (PSR tags before pushing). The checkout step persisted the App token credential (`persist-credentials: true`), so `git push origin refs/tags/<tag>` from a later `run:` step uses it. PSR writes its outputs (`version`, `tag`, `released=false`) before exiting non-zero — the report step's environment proves it.
7. **The report**: `report_release.py` decides the wording from `released` alone (`describe_actions`: "No release will be triggered based on conventional commit rules" when false). In production mode PSR sets `version` only when a release is due, so `version` set + `released` false can only mean "computed, not published".
8. **Local state**: this checkout is on `develop` at `e7f5067` (= `origin/develop`), clean; develop is protected (the `base` ruleset requires a pull request), so the workflow change goes in on a branch and PR like any other.

## Design

### 0. Decisions

1. **Repair by hand, once, now** — an annotated tag `v0.1.0-rc.6` on `e7f5067` with PSR's tagger identity and message, pushed by the user (project rule: the user pushes). It must land before the next push to develop. Commands in §3.
2. **Retry only the tag, only when the commit is already on the remote.** A step after PSR, run when PSR failed and reported a tag: resolve the local tag to its commit, compare with `git ls-remote origin refs/heads/<branch>`; equal → push the tag with up to five attempts and growing sleeps (5 s … 25 s); not equal → the branch push is what failed, do nothing, fail. This keeps the recovery inside the one failure class that is known to be transient and never pushes a tag ahead of its commit.
3. **PSR's step becomes `continue-on-error: true`** so the retry can run and the job's result is decided afterwards by a final "Release outcome" step: green when PSR succeeded or the retry pushed the tag; red (with the reason) otherwise. The report step's `RELEASED` becomes "PSR released, or the retry succeeded".
4. **The report distinguishes "nothing to release" from "failed to publish"**: in production mode, `version` set and not released → `- Release of \`<version>\` was computed but NOT published: the commit/tag push failed — see the job log; the version bump may already be on the branch.` and the table row `Will Release?` keeps `false`. Dry runs are unchanged. One new test; the existing tests keep passing.
5. **No change to PSR configuration or version**; no change to `verify-secrets.yml` or `ci.yaml`.

### 1. `semantic-release.yml` — `release-psr`

```yaml
      - name: Python Semantic Release (production)
        if: env.PREVIEW != 'true'
        id: release-prod
        # The job's result is decided by "Release outcome" below: a failed
        # tag push right after a successful branch push is retried first.
        continue-on-error: true
        uses: python-semantic-release/python-semantic-release@v10
        with: ...unchanged...
      # GitHub can reject a tag pushed seconds after the commit it points at
      # ("missing necessary objects", 2026-10-16: v0.1.0-rc.6 — the bump
      # commit landed, the tag did not). PSR pushes branch then tag with no
      # retry. Push the tag again, only when the bump commit is already on
      # the remote; a failed branch push is left alone and fails below.
      - name: Retry the tag push
        id: retry-tag
        if: env.PREVIEW != 'true' && steps.release-prod.outcome == 'failure' && steps.release-prod.outputs.tag != ''
        env:
          TAG: ${{ steps.release-prod.outputs.tag }}
          BRANCH: ${{ inputs.target_branch || github.ref_name }}
        run: |
          local=$(git rev-parse "refs/tags/$TAG^{commit}")
          remote=$(git ls-remote origin "refs/heads/$BRANCH" | cut -f1)
          if [ "$local" != "$remote" ]; then
            echo "::error::$TAG points at $local but origin/$BRANCH is at $remote: the branch push failed, not retrying"
            exit 1
          fi
          for attempt in 1 2 3 4 5; do
            sleep $((attempt * 5))
            if git push origin "refs/tags/$TAG"; then
              echo "pushed $TAG on attempt $attempt"
              exit 0
            fi
          done
          echo "::error::could not push $TAG after 5 attempts"
          exit 1
      - name: Evaluate and report release
        ...
          RELEASED: ${{ steps.release-prod.outputs.released == 'true' || steps.retry-tag.outcome == 'success' }}
        ...
      - name: Release outcome
        if: env.PREVIEW != 'true' && steps.release-prod.outcome == 'failure' && steps.retry-tag.outcome != 'success'
        run: |
          echo "::error::python-semantic-release failed and the tag push could not be recovered; see the steps above"
          exit 1
```

(`sleep` before the first attempt as well: the rejection is a timing problem, and the five seconds cost nothing against a release.)

### 2. `report_release.py`

In `generate_report`'s production (non-dry-run) path: if `eff_version and not eff_released`, replace the `describe_actions` lines with the header and the "computed but NOT published" line of Decision 4. Test (`TestGenerateReport.test_single_production_failed_push`): `dry_run=False, version="0.2.0", tag="v0.2.0", released=False` → the report contains `NOT published`, `v0.2.0`, `| Will Release? | \`false\` |`, and not `No release will be triggered`. Red before the code change (the current output has the "No release" line), green after.

### 3. The repair (user, from this checkout at `e7f5067`)

```bash
GIT_COMMITTER_NAME=semantic-release GIT_COMMITTER_EMAIL=semantic-release git tag -a v0.1.0-rc.6 e7f50672e7b55125d391d411b846a891d22806bb -m v0.1.0-rc.6
```

```bash
git push origin refs/tags/v0.1.0-rc.6
```

Then `git ls-remote --tags origin | grep rc.6` shows the tag and its `^{}` peel to `e7f5067`. The committer identity only makes the tag look like PSR's others; any identity works for PSR's next computation, which reads the tag name.

### 4. `design.md` — CI and releases

One sentence on the release bullet: the tag push is retried when the commit is already on the remote (2026-10-16, `v0.1.0-rc.6`), the job's result comes from a final outcome step, and the report distinguishes a failed publish from nothing-to-release; pointer to this document.

## Implementation plan

1. Test first (§2's test), run → red. Then `report_release.py` → green.
2. `semantic-release.yml` (§1).
3. `design.md` (§4).
4. `uv run pytest src/tests/ufs_chem_assay/test_release_scripts.py`, then the whole harness suite with coverage; `uv run pre-commit run --all-files`; actionlint in CI's container.
5. The user: the tag repair (§3) now; the workflow change on a branch and PR to develop (the ruleset requires one).

## Verification

- Repair: `git ls-remote --tags origin` lists `v0.1.0-rc.6` → `e7f5067`; the next push to develop that warrants a release computes `0.1.0-rc.7`, not `rc.6` again (`release-preview` on the PR shows the projected version).
- Prevention: cannot be forced locally. The first real exercise is the next transient; what to look for in the log: PSR's `Failed to push tag`, then `pushed v… on attempt N` and a green "Release outcome". The guard path (branch push failed) is covered by reading: the step exits 1 with the two SHAs.
- Report: the new test; `test_single_production_table` and `test_describe_actions_no_release` unchanged and green.
- Linters green.

## Risks and notes

- `continue-on-error` on PSR means a PSR failure of any other kind now reaches "Release outcome", which fails the job with the same visibility as before; nothing becomes quieter.
- The retry pushes with the persisted App credential; the token is revoked by the action's post step after the job, so it is valid during the retry.
- Five attempts × at most 25 s ≈ 75 s worst case before giving up.
- The tag retry runs `git` on the runner against objects PSR's root container created under `.git/objects`; they are world-readable and the repository directory itself is runner-owned, so no `safe.directory` issue (the integration workflow's problem was the opposite direction: runner reading root-only `0600` files).

## Open questions

None blocking.

## Future work

- If the rejection recurs often, report it to GitHub with the two run logs; the retry hides the cost but not the cause.

## Implementation notes (2026-10-16)

**Outcome: implemented as designed on branch `ci/add-retry` (the user commits and opens the PR). The tag repair (§3) was done by the user the same day: `refs/tags/v0.1.0-rc.6` → `e7f5067` on the remote (a lightweight tag, which PSR reads the same way), and `semantic-release --noop version` on develop reports "No release will be made, 0.1.0-rc.6 has already been released!".**

- TDD: `test_single_production_failed_push` added first → red (the current report printed "No release will be triggered"); `report_release.py` changed → green; the 22 release-script tests pass.
- `semantic-release.yml`: PSR production step `continue-on-error: true`; "Retry the tag push" (guarded on the bump commit being at `origin/<branch>`; five attempts, 5–25 s sleeps); `RELEASED` for the report is PSR's `released` **or** a successful retry; "Release outcome" fails the job when neither held.
- `design/design.md`: the release bullet under CI and releases.
- Verification: `uv run pytest src/tests/ufs_chem_assay --cov` → all tests pass at 100 % coverage; `uv run pre-commit run --all-files` → all hooks pass (the first pass let a hook normalise a file; the second is clean); actionlint in CI's container → clean. The retry path itself can only be exercised by the next rejection on CI (Verification above says what its log will show).

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

User request (2026-10-16, verbatim): "we have a develop failure to address: https://github.com/benkozi/ufs-chem-assay/actions/runs/38936439352/job/117094483229"

## conversational updates

- 2026-10-16 (creation): diagnosed in the session before writing; no refinement round requested.
