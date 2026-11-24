# ops: the daily run, transfer and benchmarks

One run config file says everything about a run: its root, which
applications and stages it runs, and where its transfer goes. A personal
file `extends:` the shared template, so it holds only what is yours, and
`config/*.local.yaml` is gitignored for it.

| What | Where |
|---|---|
| `ops/assay <arguments>` | The harness (`ufs-chem-assay <arguments>`), with uv's cache and managed Pythons in this checkout's `.uv/` (gitignored) and nowhere else: set for that process only, never in your shell, so other uv projects keep their own. Use it for every harness command on a machine, from the checkout (relative paths and `.env` are read from the current directory). |
| `ops/assay-run.sh <run config>` | The bootstrap: `module reset` and `git pull`, which only a shell can do before the harness starts, then `ops/assay run --watch --config-file <run config>`. One argument, nothing else. |
| `ops/assay transfer --config-file <run config>` | Zips the root's logs, rendered scripts and records, tests roots, CMake caches and report into one payload, `<transfer_dir>/<name>.zip` (unzipping to `<name>/`), then `git pull`, `add`, `commit` and `push` in the transfer repo. Only the zip is committed. |
| `ops/assay record-benchmarks --transfer <payload>` | On the laptop: appends every test run a transfer holds (its `.zip`, read in place, or the directory it unzips to) to this checkout's committed `src/tests/config/<application>/tests-benchmarks.csv`. |

## Setting up (a new machine, or a new person)

1. Your own checkout of the harness (`git pull`, `.venv` and your run config
   live in it; a shared checkout changes under someone else's run).
2. Your run config, e.g. `config/c7.local.yaml`:

   ```yaml
   # config/c7.local.yaml (gitignored): the daily C7 run
   extends: config/gaea-c7.yaml   # relative to the checkout; chains are allowed
   root_dir: /gpfs/f7/epic/proj-shared/<you>/sandbox/work-ufs-chem-assay
   transfer_dir: ~/l/c7-epic-proj-shared/git/<your work repo>/transfer
   selected_stages: [source, build, test]  # selected_applications omitted: every application
   ```

3. Nothing for uv: `ops/assay` puts its cache and Pythons in the
   checkout's `.uv/` (on C7 the checkout is on GPFS, so `$HOME`'s small
   quota is untouched), and they serve the assay alone. Do not export
   `UV_CACHE_DIR` or `UV_PYTHON_INSTALL_DIR` in your profile for it: that
   would point every uv project at them (`ops/assay` sets its own over
   them anyway). The stage scripts call `uv run --no-sync` on the venv
   `ops/assay` built, so they download nothing; their `UV_CACHE_DIR` is
   the run config's `uv.cache_dir` (default `<root>/uv-cache`).

Step by step on Gaea C7, with the exact files: [c7-runbook.md](c7-runbook.md).

Roots must be distinct, and one root runs one `run` at a time: two runs in a
root overwrite each other's scripts and records.

## The daily loop

```bash
cd /gpfs/f7/epic/proj-shared/Benjamin.Koziol/git/ufs-chem-assay
ops/assay-run.sh config/c7.local.yaml
ops/assay transfer --config-file config/c7.local.yaml
```

Then, on the laptop, once the transfer is synced:

```bash
cd /Users/bkoziol/sandbox/git-benkozi/ufs-chem-assay
ops/assay record-benchmarks --transfer ~/Library/CloudStorage/Dropbox/rlps/rsandbox/benkozi-work/transfer/assay-<YYYYmmdd-HHMM>.zip --dry-run
ops/assay record-benchmarks --transfer ~/Library/CloudStorage/Dropbox/rlps/rsandbox/benkozi-work/transfer/assay-<YYYYmmdd-HHMM>.zip
```

Runs already recorded are skipped; review `git diff -- 'src/tests/config/*/tests-benchmarks.csv'` and commit.

## Changing what runs

The file is the run. A variation is another small file that extends yours:

```yaml
# config/c7-musica.local.yaml: rebuild and retest CATChem's musica build only
extends: config/c7.local.yaml
selected_applications: [catchem]
selected_builds: [musica]
selected_stages: [build, test]
```

A fresh root is one line: edit `root_dir`. For a one-off without the
bootstrap (no `module reset` or `git pull`), the harness's flags are
overrides on the file, each replacing only its own key:
`ops/assay run --config-file config/c7.local.yaml --build musica --watch`.

`<root>/scripts/run-config.yaml`, which `run` writes, is the run merged,
selected and complete: `ops/assay-run.sh <root>/scripts/run-config.yaml`
reruns exactly what that root ran, and every transfer carries it.

## What `run --watch` does

- `watch` runs whatever the stages returned: after a failed build the other
  builds' test jobs are still submitted, and `watch` evaluates and reports
  them.
- `watch` is skipped under `--dry-run` and when no selected stage leaves
  anything to wait for (no `test`, `harness` or `resume`).
- The last line says both: `run: stages exit N, watch exit M`. The exit
  status is the stages' when they failed, else watch's.

Every `run` writes its transcript, `<root>/logs/assay-run-<YYYYmmdd-HHMMSS>.log`:
the harness's lines (`[run]`, then `[watch]`), opening with the host, the
harness's version and commit, the config file and each one it extends, and
`LD_LIBRARY_PATH`/`PYTHONPATH`. The stage scripts' output is in their own
stage logs, each starting with a header naming the command and the run.

Design: `design/feat/20261113-1348-build-test-kokkos/20261123-1550-ops-as-python.md`.
