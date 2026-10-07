"""One `aws s3 sync` per call, configured by a pydantic model.

Stand-alone on purpose: the harness logger is the only harness import (one
line to replace with `logging.getLogger(__name__)` when this module is
reused elsewhere). Sync is the primitive because it is reentrant — an
interrupted transfer resumes by re-running the same configuration, and
unchanged files are skipped. `sync()` is exactly one subprocess call: no
pre-flight aws calls, no retries, no follow-up commands; deleting a prefix
is a sync too (an empty source directory plus `delete=True`).

Credentials are the AWS CLI's business, not this module's: the child
inherits the environment unchanged and the CLI resolves its own chain
(`~/.aws/credentials` and `~/.aws/config` profiles, environment variables,
SSO, instance roles). The one hook is `profile`, a pass-through of
`--profile`. Missing credentials surface as the CLI's own nonzero exit.

The AWS CLI (v2) is an external tool, like docker or git: located on PATH,
never installed by this module. See the README for the install per machine.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path
from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from logs import get_logger

logger = get_logger("s3_sync")

# Bucket naming rules (lowercase, digits, dots, hyphens, 3-63 chars, no edge
# dot/hyphen) plus an optional key prefix.
S3_URI_PATTERN = r"^s3://[a-z0-9][a-z0-9.-]{1,61}[a-z0-9](/.*)?$"
S3Uri = Annotated[str, StringConstraints(pattern=S3_URI_PATTERN)]

_S3_SCHEME = "s3://"
_S3_ARN_PREFIX = "arn:aws:s3:::"
_INSTALL_HINT = (
    "install AWS CLI v2 and put it on PATH (README: Prerequisites / S3 data sync)"
)


def s3_uri_from_arn(arn: str) -> str:
    """`arn:aws:s3:::bucket[/key]` -> `s3://bucket[/key]`: only S3 bucket and
    object ARNs have this shape."""
    if not arn.startswith(_S3_ARN_PREFIX) or len(arn) == len(_S3_ARN_PREFIX):
        raise ValueError(f"not an S3 bucket ARN: {arn!r}")
    return _S3_SCHEME + arn[len(_S3_ARN_PREFIX) :]


def _is_s3(side: str | Path) -> bool:
    return isinstance(side, str)


def _key_prefix(uri: str) -> str:
    """The part after the bucket, without a trailing slash: '' for a root
    (`s3://bucket` and `s3://bucket/` alike)."""
    _, _, rest = uri[len(_S3_SCHEME) :].partition("/")
    return rest.rstrip("/")


class S3SyncConfig(BaseModel):
    """One sync: where from, where to, and how. Frozen, unknown keys rejected.
    A string side is an S3 URI when it matches the pattern, otherwise a local
    path; local paths are resolved to absolute at validation so argv and
    logs never depend on the caller's cwd."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: S3Uri | Path = Field(
        union_mode="left_to_right",
        description="s3://bucket/prefix or an existing local directory",
    )
    destination: S3Uri | Path = Field(
        union_mode="left_to_right",
        description="s3://bucket/prefix or a local directory (created by aws)",
    )
    dry_run: bool = Field(
        False, description="--dryrun: list the operations, move nothing"
    )
    delete: bool = Field(
        False,
        description=(
            "--delete: remove destination files absent from the source; "
            "refused when an S3 side is a bucket root"
        ),
    )
    only_show_errors: bool = Field(
        False,
        description=(
            "--only-show-errors: no per-file lines (large transfers); refused "
            "with dry_run, whose lines are the point"
        ),
    )
    exclude: list[str] = Field(
        default_factory=list, description="--exclude patterns, applied first"
    )
    include: list[str] = Field(
        default_factory=list,
        description="--include patterns, applied after exclude (last match wins)",
    )
    profile: str | None = Field(
        None,
        min_length=1,
        description=(
            "--profile NAME: the AWS CLI named profile to use; None leaves the "
            "choice to the CLI (AWS_PROFILE, else default)"
        ),
    )
    timeout_s: int | None = Field(
        None,
        gt=0,
        description=(
            "subprocess timeout; the child is killed — re-run the same config "
            "to resume. None waits indefinitely"
        ),
    )
    aws_executable: str = Field(
        "aws", description="The AWS CLI to run; resolved on PATH by sync()"
    )

    @field_validator("source", "destination", mode="after")
    @classmethod
    def _absolute_local(cls, side: str | Path) -> str | Path:
        return side.resolve() if isinstance(side, Path) else side

    @model_validator(mode="after")
    def _check_sides(self) -> S3SyncConfig:
        s3_sides = [s for s in (self.source, self.destination) if _is_s3(s)]
        if not s3_sides:
            raise ValueError("at least one of source/destination must be an s3:// URI")
        if len(s3_sides) == 2:
            raise ValueError("S3-to-S3 sync is not supported")
        if isinstance(self.source, Path) and not self.source.is_dir():
            raise ValueError(
                f"a local source must be an existing directory: {self.source}"
            )
        if self.delete:
            for uri in s3_sides:
                assert isinstance(uri, str)
                if not _key_prefix(uri):
                    raise ValueError(
                        f"delete=True is refused on a bucket root ({uri}); "
                        "give a key prefix"
                    )
        if self.only_show_errors and self.dry_run:
            raise ValueError(
                "only_show_errors suppresses exactly the lines a dry_run exists "
                "to show; choose one"
            )
        return self

    @property
    def argv(self) -> list[str]:
        """[aws, s3, sync, <source>, <destination>, --no-progress, --dryrun?,
        --delete?, --only-show-errors?, --profile NAME?, --exclude P...,
        --include P...]: flags after the positionals, exclude before include
        (aws evaluates filters in order). argv[0] is aws_executable as given;
        sync() swaps in the path shutil.which resolved."""
        argv = [
            self.aws_executable,
            "s3",
            "sync",
            str(self.source),
            str(self.destination),
            "--no-progress",
        ]
        if self.dry_run:
            argv.append("--dryrun")
        if self.delete:
            argv.append("--delete")
        if self.only_show_errors:
            argv.append("--only-show-errors")
        if self.profile is not None:
            argv += ["--profile", self.profile]
        for pattern in self.exclude:
            argv += ["--exclude", pattern]
        for pattern in self.include:
            argv += ["--include", pattern]
        return argv


class S3SyncResult(BaseModel):
    """What one sync() did: the command as executed and what aws said. A dry
    run's output is the plan — one `(dryrun) upload:`/`download:`/`delete:`
    line per operation the real run would perform."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    argv: list[str] = Field(description="The command that ran, as executed")
    returncode: int = Field(description="aws exit status (0 on success)")
    output: str = Field(description="Combined stdout+stderr of the call")
    dry_run: bool = Field(description="Whether the call was a --dryrun")


def sync(config: S3SyncConfig) -> S3SyncResult:
    """Run one `aws s3 sync`, the child inheriting this process's environment
    (credentials and region are resolved by the CLI itself). A nonzero exit —
    including the CLI's own "Unable to locate credentials" — fails the call
    with the output attached. The argv carries no secrets; nothing else is
    logged.
    """
    executable = shutil.which(config.aws_executable)
    if executable is None:
        raise FileNotFoundError(
            f"{config.aws_executable!r} not found on PATH: {_INSTALL_HINT}"
        )
    argv = [executable, *config.argv[1:]]
    logger.info(
        "%saws s3 sync: %s", "DRY RUN " if config.dry_run else "", " ".join(argv)
    )
    completed = subprocess.run(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=config.timeout_s,
        check=False,
    )
    output = (completed.stdout or b"").decode("utf-8", errors="replace")
    if completed.returncode != 0:
        logger.error("aws s3 sync failed (exit %s):\n%s", completed.returncode, output)
        raise subprocess.CalledProcessError(completed.returncode, argv, output=output)
    # A dry run's lines are its whole point: INFO; a real run's are DEBUG.
    logger.log(
        logging.INFO if config.dry_run else logging.DEBUG,
        "aws s3 sync %s:\n%s",
        "plan" if config.dry_run else "output",
        output,
    )
    return S3SyncResult(
        argv=argv,
        returncode=completed.returncode,
        output=output,
        dry_run=config.dry_run,
    )
