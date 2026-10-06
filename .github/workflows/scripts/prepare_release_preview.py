#!/usr/bin/env python3
"""Shape the checkout so python-semantic-release can preview a pull request.

On a pull_request run actions/checkout leaves HEAD detached on GitHub's
synthetic merge commit (HEAD^2 = the PR head). PSR decides what to release
from the *branch name* and the commits since the last tag, so:

- `--strategy merge` puts a branch named after the target (develop/main) on
  that merge commit: "what releases if this PR merges with a merge commit",
  and, with no PR at all (manual dispatch), "what would release right now".
- `--strategy squash` resets to origin/<target>, squash-merges the PR head,
  and commits it with the PR title as subject: exactly the commit a squash
  merge produces, which is what this repository does.

The original merge ref and PR head are tagged once (pr-merge-ref,
pr-source-ref) so both strategies can run from one checkout.

Stdlib only: this runs under the runner's python3 before any `uv sync`.
"""

import argparse
import logging
import os
import subprocess
import sys

logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Prepare branch state for a semantic-release preview."
    )
    parser.add_argument(
        "--strategy",
        choices=["merge", "squash"],
        default="merge",
        help="Merge strategy to preview.",
    )
    parser.add_argument(
        "--target",
        default=os.environ.get("TARGET")
        or os.environ.get("GITHUB_BASE_REF")
        or os.environ.get("GITHUB_REF_NAME", ""),
        help="Target branch (develop, main).",
    )
    parser.add_argument(
        "--pr-title",
        default=os.environ.get("PR_TITLE", ""),
        help="PR title: subject of the synthetic squash commit.",
    )
    return parser.parse_args()


def run_git(args: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run a git command."""
    return subprocess.run(["git", *args], capture_output=True, text=True, check=check)


def prepare_preview(target: str, pr_title: str, strategy: str = "merge") -> None:
    """Prepare the checkout for a merge-commit or squash-merge preview."""
    if not target:
        raise ValueError("Target branch must be specified for release preview.")

    run_git(["config", "user.name", "github-actions[bot]"])
    run_git(["config", "user.email", "github-actions[bot]@users.noreply.github.com"])

    # Drop whatever the previous preview pass wrote (version bump, changelog).
    run_git(["checkout", "-f"])
    run_git(["clean", "-fd"])

    if (
        run_git(
            ["rev-parse", "-q", "--verify", "refs/tags/pr-merge-ref"], check=False
        ).returncode
        != 0
    ):
        run_git(["tag", "-f", "pr-merge-ref", "HEAD"])
        source_sha = (
            run_git(
                ["rev-parse", "-q", "--verify", "HEAD^2"], check=False
            ).stdout.strip()
            or run_git(["rev-parse", "HEAD"]).stdout.strip()
        )
        run_git(["tag", "-f", "pr-source-ref", source_sha])

    logger.info("Preparing release preview (strategy=%s, target=%s)", strategy, target)

    if strategy == "merge":
        run_git(["checkout", "-B", target, "pr-merge-ref"])
        return

    run_git(["checkout", "-B", target, f"origin/{target}"])
    if not pr_title.strip():
        logger.info("No PR title provided; checked out target branch: %s", target)
        return
    if run_git(["merge", "--squash", "pr-source-ref"], check=False).returncode != 0:
        logger.warning("Squash merge encountered conflicts; aborting merge.")
        run_git(["merge", "--abort"], check=False)
    logger.info("Applying synthetic squash commit from PR title: %s", pr_title)
    run_git(["commit", "--allow-empty", "-m", pr_title])


def main() -> int:
    """Entry point."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    args = parse_args()
    try:
        prepare_preview(
            args.target.strip(), args.pr_title.strip(), args.strategy.strip()
        )
    except subprocess.CalledProcessError as exc:
        logger.error(
            "Git command failed (exit %d): %s\nStderr: %s",
            exc.returncode,
            exc.cmd,
            exc.stderr,
        )
        return exc.returncode
    except ValueError as exc:
        logger.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
