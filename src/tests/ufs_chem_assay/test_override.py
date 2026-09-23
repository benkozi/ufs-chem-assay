"""cli/override.py: KEY:PATH=VALUE entries merged into a raw dict, values as
YAML scalars."""

import pytest

from cli.override import apply_overrides, parse_value


def test_nested_paths_create_mappings() -> None:
    base: dict[str, object] = {"harness": {"suite_config": "a"}}
    apply_overrides(
        ["harness:suite_config=b", "applications:cece:ref=develop", "slurm:qos=batch"],
        base,
    )
    assert base == {
        "harness": {"suite_config": "b"},
        "applications": {"cece": {"ref": "develop"}},
        "slurm": {"qos": "batch"},
    }


def test_last_override_wins() -> None:
    base: dict[str, object] = {}
    apply_overrides(["a=1", "a=2"], base)
    assert base == {"a": 2}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("8", 8),
        ("true", True),
        ("false", False),
        ("null", None),
        ("~", None),
        ("[-x, -k, base]", ["-x", "-k", "base"]),
        ("/scratch/a:b=c", "/scratch/a:b=c"),
        ("yes", "yes"),  # YAML 1.2: not a boolean
        ("on", "on"),
        ("1e3", "1e3"),
        ("develop", "develop"),
    ],
)
def test_values_parse_as_yaml_scalars(text: str, expected: object) -> None:
    assert parse_value(text) == expected


def test_value_keeps_everything_after_the_first_equals() -> None:
    base: dict[str, object] = {}
    apply_overrides(["harness:env:X=a=b"], base)
    assert base == {"harness": {"env": {"X": "a=b"}}}


def test_missing_equals_is_an_error() -> None:
    with pytest.raises(ValueError, match="must be KEY"):
        apply_overrides(["harness:suite_config"], {})
    with pytest.raises(ValueError, match="must be KEY"):
        apply_overrides(["=value"], {})


def test_empty_segment_is_an_error() -> None:
    with pytest.raises(ValueError, match="empty key segment"):
        apply_overrides(["harness::x=1"], {})


def test_descending_through_a_scalar_is_an_error() -> None:
    base: dict[str, object] = {"harness": {"suite_config": "a"}}
    with pytest.raises(ValueError, match="'harness:suite_config' is not a mapping"):
        apply_overrides(["harness:suite_config:x=1"], base)
    assert base == {"harness": {"suite_config": "a"}}  # untouched


def test_null_clears_a_key() -> None:
    base: dict[str, object] = {"applications": {"cece": {"clone_dir": "/x"}}}
    apply_overrides(["applications:cece:clone_dir=null"], base)
    assert base == {"applications": {"cece": {"clone_dir": None}}}
