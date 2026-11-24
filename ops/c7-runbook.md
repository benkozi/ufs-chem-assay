# Gaea C7 runbook: the daily run

The harness checkout on C7 is
`/gpfs/f7/epic/proj-shared/Benjamin.Koziol/git/ufs-chem-assay`. You create
one file, your run config. Nothing else is new: no `.env` changes, no
`ops/env.sh`, nothing in `~/.bashrc`.

Every harness command goes through `ops/assay` (the bootstrap does too). It
keeps uv's cache and Pythons in the checkout's `.uv/` (gitignored), for the
assay only: other uv projects keep their own, and nothing is exported into
your shell.

## Once: get the new harness

The bootstrap pulls the harness itself, but the copy you run first is the
old one (it still looks for `ops/env.sh`). So pull by hand once, after the
change is pushed:

```bash
cd /gpfs/f7/epic/proj-shared/Benjamin.Koziol/git/ufs-chem-assay
git pull
```

## Once: move the venv onto the assay's own Python

The existing `.venv` was built with the Python in
`sandbox/uv-python`. Remove it once, so the first `ops/assay` call rebuilds it
against `.uv/python` (it downloads that Python and fills `.uv/cache` the one
time):

```bash
rm -rf /gpfs/f7/epic/proj-shared/Benjamin.Koziol/git/ufs-chem-assay/.venv
```

`sandbox/uv-cache` and `sandbox/uv-python` are no longer used by the assay;
remove them when nothing else needs them.

## Once: your run config

`config/c7.local.yaml` in the checkout (gitignored: it is yours). It extends
the shared `config/gaea-c7.yaml` and holds only your values:

```bash
cat > /gpfs/f7/epic/proj-shared/Benjamin.Koziol/git/ufs-chem-assay/config/c7.local.yaml <<'EOF'
# My daily C7 run: everything else is config/gaea-c7.yaml's.
extends: config/gaea-c7.yaml
root_dir: /gpfs/f7/epic/proj-shared/Benjamin.Koziol/sandbox/work-ufs-chem-assay
transfer_dir: ~/l/c7-epic-proj-shared/git/benkozi-work/transfer
selected_stages: [source, build, test]  # selected_applications omitted: every application (cece, catchem)
EOF
```

## Once: check the config before running it

Render everything and execute nothing (this first call also rebuilds the
venv):

```bash
cd /gpfs/f7/epic/proj-shared/Benjamin.Koziol/git/ufs-chem-assay
ops/assay run --config-file config/c7.local.yaml --dry-run
```

It should say `applications: cece, catchem; ... stages: source, build, test;
toolchains: oneapi` and write the scripts under `<root>/scripts/`. Then
`ls .uv` shows `cache` and `python`.

## Daily

Start from a fresh root if you want one (the path stays the same):

```bash
rm -rf /gpfs/f7/epic/proj-shared/Benjamin.Koziol/sandbox/work-ufs-chem-assay
```

Run (module reset, git pull, the stages, then watch until the tests are
evaluated):

```bash
cd /gpfs/f7/epic/proj-shared/Benjamin.Koziol/git/ufs-chem-assay
ops/assay-run.sh config/c7.local.yaml
```

The last line is `run: stages exit N, watch exit M`. The transcript is
`<root>/logs/assay-run-<YYYYmmdd-HHMMSS>.log`; each stage's own output is in
its stage log beside it.

Send it to the transfer repo (zips the logs, scripts, records, tests roots,
CMake caches and `report.txt` into one payload, `transfer/assay-<YYYYmmdd-HHMM>.zip`,
then pull, add, commit, push; only the zip is committed):

```bash
ops/assay-transfer.sh config/c7.local.yaml
```

It takes the one file, like `ops/assay-run.sh`. To see what it would zip
first, or to name the payload, call the harness command itself:
`ops/assay transfer --config-file config/c7.local.yaml --dry-run` (or
`--name assay-<something>`). An existing name is refused.

## On the laptop: benchmarks

Once the transfer has synced:

```bash
cd /Users/bkoziol/sandbox/git-benkozi/ufs-chem-assay
ops/assay record-benchmarks --transfer ~/Library/CloudStorage/Dropbox/rlps/rsandbox/benkozi-work/transfer/assay-<YYYYmmdd-HHMM>.zip --dry-run
```

It reads the zip in place (no need to unzip it; the unzipped `assay-<...>/` directory works too). To look at the run, `unzip assay-<YYYYmmdd-HHMM>.zip` gives `assay-<YYYYmmdd-HHMM>/`, with `report.txt` at its top. Drop `--dry-run` to append, then review `git diff -- 'src/tests/config/*/tests-benchmarks.csv'` and commit. (Plain `uv run ufs-chem-assay ...` works on the laptop too; `ops/assay` keeps its uv apart there as well.)

## Other runs

- **Another selection:** a second small file that extends yours, e.g.
  `config/c7-musica.local.yaml`:

  ```yaml
  extends: config/c7.local.yaml
  selected_applications: [catchem]
  selected_builds: [musica]
  selected_stages: [build, test]
  ```

  then `ops/assay-run.sh config/c7-musica.local.yaml`.
- **Rerun exactly what a root ran:**
  `ops/assay-run.sh <root>/scripts/run-config.yaml`.
- **A one-off flag** (no module reset or git pull):
  `ops/assay run --config-file config/c7.local.yaml --watch --stage test`.

## If something is off

| You see | It means |
|---|---|
| `usage: ops/assay-run.sh <run config yaml>` | The bootstrap takes exactly one argument, the file. |
| `no such file ... ops/env.sh` | You ran the old bootstrap: `git pull` once by hand (above). |
| `...: extends ..., which does not exist` | `extends:` is relative to the harness checkout: `config/gaea-c7.yaml`. |
| `transfer: ... sets no transfer_dir` | Add `transfer_dir:` to your file. |
| `transfer: ... already exists: pass another --name` | Two transfers in one minute, or a reused name. |
| uv downloads into `$HOME` | It was called as plain `uv run`, not `ops/assay`: on C7, always `ops/assay`. |
