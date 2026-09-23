"""Command-line overrides onto a loaded run config: `key:nested:key=value`
entries merged into the raw dict before validation, so pydantic remains the
only gate. Values are YAML scalars (the harness's YAML-1.2 loader): `8` is
an int, `true`/`false` booleans, `null` clears a key, `[a, b]` a list, and
anything else the string as typed — a path with a colon survives."""

from __future__ import annotations

from collections.abc import Iterable

from models.yaml import load_yaml

KEY_SEPARATOR = ":"


def parse_value(text: str) -> object:
    """The override's value as the YAML scalar (or flow collection) it spells."""
    return load_yaml(text)


def apply_overrides(overrides: Iterable[str], base: dict[str, object]) -> None:
    """Apply each `key:path=value` entry to `base` in place, in order (last
    wins). Intermediate mappings are created when absent; descending through
    an existing non-mapping value is an error (the path cannot exist), as is
    an entry without `=`."""
    for override in overrides:
        key_path, sep, value = override.partition("=")
        if not sep or not key_path:
            raise ValueError(
                f"override {override!r} must be KEY[:NESTED...]=VALUE (an '=' with a key before it)"
            )
        keys = key_path.split(KEY_SEPARATOR)
        if any(not key for key in keys):
            raise ValueError(f"override {override!r} has an empty key segment")
        current = base
        for depth, key in enumerate(keys[:-1]):
            existing = current.get(key)
            if existing is None:
                child: dict[str, object] = {}
                current[key] = child
                current = child
            elif isinstance(existing, dict):
                current = existing
            else:
                parent = KEY_SEPARATOR.join(keys[: depth + 1])
                raise ValueError(
                    f"override {override!r}: {parent!r} is not a mapping "
                    f"({type(existing).__name__}), so nothing can nest under it"
                )
        current[keys[-1]] = parse_value(value)
