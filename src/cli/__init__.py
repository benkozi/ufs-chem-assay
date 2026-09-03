"""The `ufs-chem-assay` entrypoint: assembles a run — CECE source, native
or container build, data staging, CECE unit tests, the harness session —
from one YAML run config, directly or as a Slurm batch job. It renders shell
scripts and executes them; pytest stays the test entry point and no test
logic lives here."""
