#!/usr/bin/env python3
"""Validate the GitHub App credentials python-semantic-release pushes with.

Two subcommands, run by semantic-release.yml (gate) and verify-secrets.yml
(daily): `check-secrets` checks presence and shape of the two repository
secrets; `check-permissions` proves the minted App token can see this
repository and create/delete a ref on it, and warns when a ruleset that
requires pull requests does not list the App as an always-bypass actor.

Stdlib only: this runs under the runner's python3 before any `uv sync`.
"""

import argparse
import json
import logging
import os
import subprocess
import sys

logger = logging.getLogger(__name__)


def write_github_output(outputs: dict[str, str]) -> None:
    """Append key=value pairs to the GITHUB_OUTPUT file."""
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
        f.writelines(f"{k}={v}\n" for k, v in outputs.items())


def check_secrets(app_id: str, private_key: str, allow_missing: bool) -> int:
    """Validate presence and structure of the Semantic Release App secrets."""
    missing = [
        name
        for name, val in (
            ("SEMVER_APP_ID", app_id),
            ("SEMVER_APP_PRIVATE_KEY", private_key),
        )
        if not val.strip()
    ]
    if missing:
        missing_str = " ".join(missing)
        if allow_missing:
            logger.info("Semantic Release secrets not provided (%s).", missing_str)
            logger.info("Skipping credential verification for preview run.")
            write_github_output({"skip_verification": "true"})
            return 0
        print(
            f"::error::Missing required Semantic Release repository secrets: {missing_str}"
        )
        return 1

    key = private_key.strip()
    checks = (
        (
            key.endswith(".pem") or key.startswith(("/", "~")),
            "SEMVER_APP_PRIVATE_KEY is a file path. Paste file contents instead.",
        ),
        (
            "-----BEGIN" not in key or "-----END" not in key,
            "SEMVER_APP_PRIVATE_KEY missing BEGIN/END RSA PRIVATE KEY markers.",
        ),
        (
            (key.startswith('"') and key.endswith('"'))
            or (key.startswith("'") and key.endswith("'")),
            "SEMVER_APP_PRIVATE_KEY wrapped in quotes. Remove quotes in Secrets.",
        ),
    )
    for failed, err in checks:
        if failed:
            print(f"::error::{err}")
            return 1

    write_github_output({"skip_verification": "false"})
    logger.info(
        "All required Semantic Release secrets are present and formatted properly."
    )
    return 0


def run_gh_api(
    endpoint: str, method: str = "GET", fields: dict[str, str] | None = None
) -> tuple[int, str]:
    """Run `gh api` and return (exit code, stdout or stderr)."""
    cmd = ["gh", "api", endpoint, "-X", method]
    for k, v in (fields or {}).items():
        cmd.extend(["-f", f"{k}={v}"])
    res = subprocess.run(cmd, capture_output=True, text=True, check=False)
    return res.returncode, res.stdout or res.stderr


def _warn_on_missing_bypass(repo: str, branches: list[str], actor_desc: str) -> None:
    """Warn for every pull-request ruleset that does not let the App bypass."""
    code, out = run_gh_api(f"repos/{repo}/rulesets")
    if code != 0:
        return
    try:
        rulesets = json.loads(out)
    except json.JSONDecodeError:
        return
    for rs in rulesets:
        rs_id = rs.get("id")
        if not rs_id:
            continue
        d_code, d_out = run_gh_api(f"repos/{repo}/rulesets/{rs_id}")
        if d_code != 0:
            continue
        try:
            detail = json.loads(d_out)
        except json.JSONDecodeError:
            continue
        if not any(r.get("type") == "pull_request" for r in detail.get("rules", [])):
            continue
        rs_name = detail.get("name", str(rs_id))
        has_bypass = detail.get("current_user_can_bypass") == "always" or any(
            a.get("actor_type") == "Integration" and a.get("bypass_mode") == "always"
            for a in detail.get("bypass_actors", [])
        )
        for branch in branches:
            if has_bypass:
                logger.info(
                    "Ruleset '%s' includes App (%s) in bypass list for %s.",
                    rs_name,
                    actor_desc,
                    branch,
                )
            else:
                print(
                    f"::warning::Ruleset '{rs_name}' requires PRs on {branch}; App ({actor_desc}) not in bypass list."
                )


def check_permissions(
    repo: str, sha: str, run_id: str, branches: list[str], app_id: str, app_slug: str
) -> int:
    """Verify repository access, ref mutation permission, and ruleset bypass."""
    logger.info("Verifying GitHub App permissions for %s...", repo)

    code, out = run_gh_api("/installation/repositories")
    if code != 0:
        print(f"::error::Failed to query installation repositories: {out.strip()}")
        return 1
    try:
        repo_names = [
            r.get("full_name") for r in json.loads(out).get("repositories", [])
        ]
    except json.JSONDecodeError:
        print(
            f"::error::Invalid JSON returned from installation repositories API: {out.strip()}"
        )
        return 1
    if repo not in repo_names:
        print(f"::error::GitHub App is not installed on repository {repo}.")
        return 1
    logger.info("GitHub App repository access confirmed for %s.", repo)

    test_ref = f"tags/ci-perm-check-{run_id}"
    logger.info("Testing Git ref creation (%s)...", test_ref)
    code, out = run_gh_api(
        f"repos/{repo}/git/refs",
        method="POST",
        fields={"ref": f"refs/{test_ref}", "sha": sha},
    )
    if code != 0:
        print(
            f"::error::Failed to create test Git ref on {repo}. Verify App has 'Contents: Read and write'.\n{out}"
        )
        return 1
    logger.info("Git ref creation succeeded. Cleaning up test ref...")
    run_gh_api(f"repos/{repo}/git/refs/{test_ref}", method="DELETE")

    _warn_on_missing_bypass(repo, branches, app_slug or f"ID {app_id}")
    logger.info("Semantic release credential verification completed successfully.")
    return 0


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Verify Semantic Release secrets and repository permissions."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    secrets_parser = subparsers.add_parser(
        "check-secrets", help="Check presence and formatting of secrets."
    )
    secrets_parser.add_argument(
        "--app-id",
        default=os.environ.get("SEMVER_APP_ID", ""),
        help="GitHub App client id.",
    )
    secrets_parser.add_argument(
        "--private-key",
        default=os.environ.get("SEMVER_APP_PRIVATE_KEY", ""),
        help="GitHub App private key (PEM).",
    )
    secrets_parser.add_argument(
        "--allow-missing",
        action="store_true",
        help="Skip gracefully if secrets are missing (preview runs).",
    )

    perm_parser = subparsers.add_parser(
        "check-permissions", help="Verify repository permissions and ruleset bypass."
    )
    perm_parser.add_argument(
        "--repo",
        default=os.environ.get("REPO") or os.environ.get("GITHUB_REPOSITORY", ""),
        help="owner/repo.",
    )
    perm_parser.add_argument(
        "--sha",
        default=os.environ.get("SHA") or os.environ.get("GITHUB_SHA", ""),
        help="SHA for the probe ref.",
    )
    perm_parser.add_argument(
        "--run-id",
        default=os.environ.get("RUN_ID") or os.environ.get("GITHUB_RUN_ID", ""),
        help="Unique run id.",
    )
    perm_parser.add_argument(
        "--branches", nargs="+", default=["develop", "main"], help="Branches to audit."
    )
    perm_parser.add_argument(
        "--app-id",
        default=os.environ.get("APP_ID") or os.environ.get("SEMVER_APP_ID", ""),
        help="GitHub App id.",
    )
    perm_parser.add_argument(
        "--app-slug", default=os.environ.get("APP_SLUG", ""), help="GitHub App slug."
    )
    return parser.parse_args()


def main() -> int:
    """Entry point."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    args = parse_args()
    if args.command == "check-secrets":
        return check_secrets(args.app_id, args.private_key, args.allow_missing)
    if args.command == "check-permissions":
        branches = [
            item for b in args.branches for item in b.replace(",", " ").split() if item
        ]
        return check_permissions(
            args.repo, args.sha, args.run_id, branches, args.app_id, args.app_slug
        )
    return 1


if __name__ == "__main__":
    sys.exit(main())
