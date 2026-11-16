"""The harness-owned UFS modulefiles (modulefiles/): copies of the
ufs-weather-model platform files that load a spack-stack environment plus
the packages under test. They carry no environment root of their own — a run
config names the environment (applications.<name>.modulepath, or the
optional ASSAY_SPACK_ENV_ROOT in module_env)."""

from pathlib import Path

import pytest

_MODULEFILES = Path(__file__).resolve().parents[3] / "modulefiles"
_PLATFORM_FILES = (
    "ufs_gaeac6.oneapi.lua",
    "ufs_ursa.gnu.lua",
    "ufs_ursa.oneapi.lua",
)


def test_the_harness_ships_the_ufs_modulefiles() -> None:
    assert sorted(p.name for p in _MODULEFILES.glob("*.lua")) == [
        "ufs_common.lua",
        *_PLATFORM_FILES,
    ]


@pytest.mark.parametrize("name", ["ufs_common.lua", *_PLATFORM_FILES])
def test_no_modulefile_carries_an_environment_root(name: str) -> None:
    text = (_MODULEFILES / name).read_text()
    for needle in (
        "/scratch",
        "/gpfs",
        "/ncrc",
        "/contrib",
        "Richard.Grubin",
        "rickgrubin",
        "TEST_KOKKOS_ENV_ROOT",
        "build-test-kokkos",
    ):
        assert needle not in text, f"{name} contains {needle!r}"


@pytest.mark.parametrize("name", _PLATFORM_FILES)
def test_the_environment_root_is_optional(name: str) -> None:
    # Set: the file prepends <root>/modules/Core. Unset or empty: the caller
    # has already `module use`d the environment (the normal case — full
    # paths in the run config's modulepath); never a fallback path.
    text = (_MODULEFILES / name).read_text()
    assert 'local env_root = os.getenv("ASSAY_SPACK_ENV_ROOT")\n' in text
    assert (
        'if env_root and env_root ~= "" then\n'
        '  prepend_path("MODULEPATH", pathJoin(env_root, "modules/Core"))\n'
        "end\n"
    ) in text
    assert text.count("prepend_path(") == 1


@pytest.mark.parametrize("name", _PLATFORM_FILES)
def test_platform_files_load_the_common_set_and_the_packages_under_test(
    name: str,
) -> None:
    text = (_MODULEFILES / name).read_text()
    assert 'load("ufs_common")' in text
    assert 'load("kokkos")' in text
    assert 'load("kokkos-kernels")' in text
    assert 'try_load("arborx")' in text  # tolerant: not every environment has it
    # The pins stay overridable from the run config's module_env.
    for pin in ("kokkos_ver", "kokkos_kernels_ver", "arborx_ver", "cmake_ver"):
        assert f'os.getenv("{pin}")' in text, pin
