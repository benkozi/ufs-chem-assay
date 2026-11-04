"""The suite repoint: one `ulid:` line rewritten in place, every other byte
kept (the suite files are hand-commented, which a YAML dump would lose),
verified by reloading the file."""

from __future__ import annotations

import re
from pathlib import Path

import yaml


def rewrite_suite_ulid(path: Path, old: str, new: str) -> None:
    """Exactly one `ulid: <old>` line becomes `ulid: <new>`. Zero matches
    (the entry was edited since the run) and two or more (two entries pin
    the same ULID) are refused; after writing, the file is reloaded and
    must equal the previous suite with that one ULID changed, or the
    original text is restored."""
    from applications.registry import load_suite

    original = path.read_text()
    # The value may be quoted (the all-zero placeholder must be, or YAML
    # reads it as the integer 0); the new ULID keeps the line's quoting.
    pattern = re.compile(
        rf"^(?P<head>[ \t]*(?:-[ \t]+)?ulid:[ \t]*)(?P<quote>['\"]?){re.escape(old)}(?P=quote)(?P<tail>[ \t]*(?:#.*)?)$",
        re.MULTILINE,
    )
    matches = list(pattern.finditer(original))
    if not matches:
        raise ValueError(f"{path}: no `ulid:` line pins {old}")
    if len(matches) > 1:
        raise ValueError(
            f"{path}: {len(matches)} `ulid:` lines pin {old}; repoint by hand"
        )
    expected = load_suite(path).model_dump()
    entries = expected["baseline_comparisons"]
    (index,) = [i for i, entry in enumerate(entries) if entry["ulid"] == old]
    entries[index]["ulid"] = new
    match = matches[0]
    path.write_text(
        original[: match.start()]
        + match["head"]
        + match["quote"]
        + new
        + match["quote"]
        + match["tail"]
        + original[match.end() :]
    )
    try:
        after = load_suite(path).model_dump()
    except (
        ValueError,
        OSError,
        yaml.YAMLError,
    ) as exc:  # ValidationError is a ValueError
        path.write_text(original)
        raise ValueError(
            f"{path}: reload after the repoint failed ({exc}); restored"
        ) from exc
    if after != expected:
        path.write_text(original)
        raise ValueError(f"{path}: reload differs beyond the one ULID; restored")
