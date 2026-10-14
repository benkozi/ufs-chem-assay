"""Baselines: publishing a run's compared combinations to the store and
syncing pinned ones back.

A baseline is the whole combination output directory (NetCDF, generated
config, captured output, driver log, stats, plots, GIFs), published as
`<store>/<ulid>/` with a `baseline.yaml` manifest. Its name is the
combination's runtime ULID — minted fresh every run, never derived from
content or configuration — so the store is append-only by construction and
enforced by a probe. The store is an `s3://bucket/prefix` (through the S3
wrapper) or a local directory (plain copies), and the suite's
`baseline_comparisons` entries are the contract: each selects a combination
and pins the ULID the next session compares against.

Generic: nothing here knows an application. One module per job —
`models` (the manifest, the records, the conventions), `plan` (the gate,
the plan, the CLI's reader of an output root), `publish` (the upload half:
manifest, probe, sync, cache, suite repoint), `stage` (the download half),
`suite` (the textual repoint), `store` (the two kinds of store).
"""

from baselines.models import (
    CONVENTIONS,
    Action,
    BaselineConventions,
    BaselineFile,
    BaselineManifest,
    BaselineParameter,
    BaselineState,
    PublishRecord,
    StagedBaseline,
    SuiteInput,
)
from baselines.plan import (
    default_selector,
    load_run,
    plan,
    publishable,
    read_parameters,
    read_run,
    read_test_report,
)
from baselines.publish import publish, publish_session
from baselines.stage import ensure_baselines
from baselines.suite import rewrite_suite_ulid

__all__ = [
    "CONVENTIONS",
    "Action",
    "BaselineConventions",
    "BaselineFile",
    "BaselineManifest",
    "BaselineParameter",
    "BaselineState",
    "PublishRecord",
    "StagedBaseline",
    "SuiteInput",
    "default_selector",
    "ensure_baselines",
    "load_run",
    "plan",
    "publish",
    "publish_session",
    "publishable",
    "read_parameters",
    "read_run",
    "read_test_report",
    "rewrite_suite_ulid",
]
