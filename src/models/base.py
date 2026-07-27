"""Shared base for all YAML-backed config models."""

from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    """Rejects unknown keys at any nesting level so typos and unsupported
    fields fail loudly at load time instead of being silently dropped.
    Deliberately not frozen — build_config() mutates CeceConfig instances by
    design; strictness and immutability are separate concerns."""

    model_config = ConfigDict(extra="forbid")
