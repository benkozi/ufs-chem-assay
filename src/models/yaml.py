"""YAML loading that follows YAML 1.2 boolean rules (true/false only).

PyYAML implements YAML 1.1, where yes/no/on/off are booleans too — which
collides with species names like "no". Every YAML the harness reads for the
driver, and every command-line override value it parses, goes through this
loader so the two agree.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

_BOOL_TAG = "tag:yaml.org,2002:bool"
_BOOL_RE = re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$")


class StrictBoolLoader(yaml.SafeLoader):
    """SafeLoader with the YAML 1.1 bool resolver replaced by a 1.2 one."""


# A fresh copy of the parent's resolver map with the bool tag removed, then
# the YAML-1.2-compatible resolver (true/false only) added back.
StrictBoolLoader.yaml_implicit_resolvers = {
    ch: [(tag, regexp) for tag, regexp in resolvers if tag != _BOOL_TAG]
    for ch, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
StrictBoolLoader.add_implicit_resolver(_BOOL_TAG, _BOOL_RE, list("tTfF"))


def load_yaml(text: str) -> object:
    """Parse YAML text with 1.2 booleans; scalars come back as Python
    scalars (int, float, bool, None, str), documents as dicts/lists."""
    return yaml.load(text, Loader=StrictBoolLoader)


def load_yaml_file(path: Path) -> object:
    with open(path) as f:
        return yaml.load(f, Loader=StrictBoolLoader)
