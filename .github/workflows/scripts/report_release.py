#!/usr/bin/env python3
"""Turn python-semantic-release step outputs into a "Semantic Release Plan".

Runs last in semantic-release.yml, always: exports the consolidated outputs
(is_prerelease, current/new version, published flag, tag) to GITHUB_OUTPUT
and appends a Markdown plan to GITHUB_STEP_SUMMARY. On previews the plan
shows the merge-commit and squash-merge projections side by side with the
projected diffs (pyproject.toml, uv.lock, CHANGELOG.md).

Stdlib only: this runs under the runner's python3 before any `uv sync`.
"""

import argparse
import logging
import os
import re
import subprocess
import sys

logger = logging.getLogger(__name__)


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def parse_args() -> argparse.Namespace:
    """Parse command line arguments (every option defaults from the env)."""
    parser = argparse.ArgumentParser(
        description="Evaluate release outputs and generate the summary report."
    )
    parser.add_argument(
        "--target",
        default=_env("TARGET") or _env("GITHUB_BASE_REF") or _env("GITHUB_REF_NAME"),
        help="Target branch.",
    )
    parser.add_argument(
        "--version",
        default=_env("VERSION"),
        help="Version produced by the production run.",
    )
    parser.add_argument(
        "--tag", default=_env("TAG"), help="Tag produced by the production run."
    )
    parser.add_argument(
        "--released", nargs="?", const="true", default=_env("RELEASED", "false")
    )
    parser.add_argument(
        "--version-merge", default=_env("VERSION_MERGE"), help="Merge-preview version."
    )
    parser.add_argument(
        "--tag-merge", default=_env("TAG_MERGE"), help="Merge-preview tag."
    )
    parser.add_argument(
        "--released-merge", nargs="?", const="true", default=_env("RELEASED_MERGE")
    )
    parser.add_argument(
        "--version-squash",
        default=_env("VERSION_SQUASH"),
        help="Squash-preview version.",
    )
    parser.add_argument(
        "--tag-squash", default=_env("TAG_SQUASH"), help="Squash-preview tag."
    )
    parser.add_argument(
        "--released-squash", nargs="?", const="true", default=_env("RELEASED_SQUASH")
    )
    parser.add_argument(
        "--diff-merge-file",
        default=_env("DIFF_MERGE_FILE"),
        help="Projected diff, merge strategy.",
    )
    parser.add_argument(
        "--diff-squash-file",
        default=_env("DIFF_SQUASH_FILE"),
        help="Projected diff, squash strategy.",
    )
    parser.add_argument(
        "--dry-run", nargs="?", const="true", default=_env("DRY_RUN", "false")
    )
    parser.add_argument(
        "--current-version",
        default=_env("CURRENT_VERSION"),
        help="Override the detected version.",
    )
    return parser.parse_args()


def _truthy(value: str) -> bool:
    return str(value).lower() in ("true", "1", "yes")


def is_prerelease(version: str, target: str) -> bool:
    """A prerelease carries a `-` suffix or targets develop."""
    return "-" in version or target == "develop"


def write_github_output(outputs: dict[str, str]) -> None:
    """Append key=value pairs to the GITHUB_OUTPUT file."""
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
        f.writelines(f"{k}={v}\n" for k, v in outputs.items())


def get_git_diff() -> str:
    """Working-tree diff against HEAD (what a preview pass changed)."""
    try:
        return subprocess.check_output(
            ["git", "diff", "HEAD"], text=True, stderr=subprocess.PIPE
        ).strip()
    except subprocess.CalledProcessError:
        return ""


def extract_version_from_toml(text: str) -> str:
    """The `version = "..."` value of a pyproject text, without a leading v."""
    match = re.search(r'^\s*version\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
    return match.group(1).strip().lstrip("v") if match else ""


def _describe_tag(ref: str) -> str:
    return (
        subprocess.check_output(
            ["git", "describe", "--tags", "--abbrev=0", ref],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        .strip()
        .lstrip("v")
    )


def _pyproject_version_at(ref: str) -> str:
    content = subprocess.check_output(
        ["git", "show", f"{ref}:pyproject.toml"], text=True, stderr=subprocess.DEVNULL
    )
    return extract_version_from_toml(content)


def determine_current_version(target: str, released: bool, dry_run: bool) -> str:
    """The version before this evaluation: nearest tag, else pyproject."""
    if not dry_run and released:
        refs = ["HEAD~1", "HEAD~2"]
    else:
        refs = ([f"origin/{target}", target] if target else []) + ["HEAD~1", "HEAD"]
    for ref in refs:
        for probe in (_describe_tag, _pyproject_version_at):
            try:
                if ver := probe(ref):
                    return ver
            except (subprocess.CalledProcessError, FileNotFoundError):
                continue
    try:
        with open("pyproject.toml", encoding="utf-8") as f:
            if ver := extract_version_from_toml(f.read()):
                return ver
    except OSError:
        pass
    return "None"


def read_diff_file(path: str) -> str:
    """Contents of a captured diff file, or empty."""
    if path and os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                return f.read().strip()
        except OSError:
            pass
    return ""


def describe_actions(
    strategy_label: str, released: bool, version: str, tag: str, prerelease: bool
) -> list[str]:
    """What a release on this repository does (nothing is published)."""
    header = (
        f"#### Actions if merged via {strategy_label}:"
        if strategy_label
        else "#### Actions on merge:"
    )
    lines = [header]
    if not released:
        lines.append(
            "- No release will be triggered based on conventional commit rules."
        )
        return lines
    kind = "prerelease" if prerelease else "release"
    lines.append(f"- Git tag `{tag}` will be created ({kind} `{version}`).")
    lines.append(
        "- `CHANGELOG.md` will be updated and `pyproject.toml` / `uv.lock` bumped in a commit by python-semantic-release."
    )
    lines.append("- No GitHub Release object; nothing is published.")
    return lines


def _diff_block(title: str, diff: str) -> list[str]:
    return [
        f"\n#### Projected repository diff ({title})"
        if title
        else "\n#### Projected repository diff",
        "<details open>",
        "<summary>Click to collapse projected file changes</summary>\n",
        "```diff",
        diff,
        "```",
        "</details>",
    ]


def generate_report(
    target: str,
    current_version: str,
    dry_run: bool,
    version: str = "",
    tag: str = "",
    released: bool = False,
    version_merge: str = "",
    tag_merge: str = "",
    released_merge: bool = False,
    version_squash: str = "",
    tag_squash: str = "",
    released_squash: bool = False,
    diff_merge: str = "",
    diff_squash: str = "",
) -> str:
    """Build the Markdown plan."""
    is_dual = dry_run and bool(version_squash or tag_squash or released_squash)
    lines = ["### Semantic Release Plan"]
    if dry_run:
        lines.append(
            "**Mode**: Preview (dry run) - no tags or releases created in this run.\n"
        )
    else:
        lines.append("**Mode**: Production execution\n")

    if is_dual:
        pre_merge = is_prerelease(version_merge, target)
        pre_squash = is_prerelease(version_squash, target)
        rows = [
            ("Target Branch", target, target),
            ("Current Version", current_version or "None", current_version or "None"),
            ("Next Version", version_merge or "None", version_squash or "None"),
            ("Git Tag", tag_merge or "None", tag_squash or "None"),
            (
                "Will Release?",
                str(released_merge).lower(),
                str(released_squash).lower(),
            ),
            ("Is Prerelease?", str(pre_merge).lower(), str(pre_squash).lower()),
        ]
        lines.extend(["| Parameter | Merge Commit | Squash Merge |", "|---|---|---|"])
        lines.extend(f"| {p} | `{vm}` | `{vs}` |" for p, vm, vs in rows)
        lines.append("")
        lines.extend(
            describe_actions(
                "Merge Commit", released_merge, version_merge, tag_merge, pre_merge
            )
        )
        lines.append("")
        lines.extend(
            describe_actions(
                "Squash Merge", released_squash, version_squash, tag_squash, pre_squash
            )
        )
        if diff_merge:
            lines.extend(_diff_block("Merge Commit", diff_merge))
        if diff_squash:
            lines.extend(_diff_block("Squash Merge", diff_squash))
        return "\n".join(lines) + "\n"

    eff_version = version or version_merge
    eff_tag = tag or tag_merge
    eff_released = released or released_merge
    prerelease = is_prerelease(eff_version, target)
    single_rows = [
        ("Target Branch", target),
        ("Current Version", current_version or "None"),
        ("Next Version", eff_version or "None"),
        ("Git Tag", eff_tag or "None"),
        ("Will Release?", str(eff_released).lower()),
        ("Is Prerelease?", str(prerelease).lower()),
    ]
    lines.extend(["| Parameter | Value |", "|---|---|"])
    lines.extend(f"| {p} | `{v}` |" for p, v in single_rows)
    lines.append("")
    lines.extend(describe_actions("", eff_released, eff_version, eff_tag, prerelease))
    diff_text = diff_merge or get_git_diff()
    if dry_run and diff_text:
        lines.extend(_diff_block("", diff_text))
    return "\n".join(lines) + "\n"


def main() -> int:
    """Entry point."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    args = parse_args()

    target = args.target.strip()
    version = args.version.strip()
    tag = args.tag.strip()
    released = _truthy(args.released)
    dry_run = _truthy(args.dry_run)
    version_merge = args.version_merge.strip()
    tag_merge = args.tag_merge.strip()
    released_merge = _truthy(args.released_merge)
    version_squash = args.version_squash.strip()
    tag_squash = args.tag_squash.strip()
    released_squash = _truthy(args.released_squash)
    diff_merge = read_diff_file(args.diff_merge_file)
    diff_squash = read_diff_file(args.diff_squash_file)

    current_version = args.current_version.strip() or determine_current_version(
        target, released or released_merge, dry_run
    )

    primary_version = (
        version if not dry_run else (version_merge or version_squash or version)
    )
    primary_released = (
        released if not dry_run else (released_merge or released_squash or released)
    )
    primary_tag = tag if not dry_run else (tag_merge or tag_squash or tag)
    prerelease = is_prerelease(primary_version, target)

    write_github_output(
        {
            "is_prerelease": "true" if prerelease else "false",
            "target_branch": target,
            "current_version": current_version,
            "new_release_version": primary_version,
            "new_release_published": "true" if primary_released else "false",
            "tag": primary_tag,
        }
    )

    report = generate_report(
        target=target,
        current_version=current_version,
        dry_run=dry_run,
        version=version,
        tag=tag,
        released=released,
        version_merge=version_merge,
        tag_merge=tag_merge,
        released_merge=released_merge,
        version_squash=version_squash,
        tag_squash=tag_squash,
        released_squash=released_squash,
        diff_merge=diff_merge,
        diff_squash=diff_squash,
    )
    print(report)
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
        f.write(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
