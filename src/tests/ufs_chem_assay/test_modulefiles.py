"""The harness-owned UFS modulefiles (modulefiles/): copies of the
ufs-weather-model platform files that load a spack-stack environment plus
the packages under test. They carry no environment root of their own — a run
config names the environment (applications.<name>.modulepath, or the
optional ASSAY_SPACK_ENV_ROOT in module_env)."""

import re
from pathlib import Path

import pytest

_MODULEFILES = Path(__file__).resolve().parents[3] / "modulefiles"
_GAEA_C7 = "ufs_gaeac7.oneapi.lua"
_PLATFORM_FILES = (
    "ufs_gaeac6.oneapi.lua",
    _GAEA_C7,
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
@pytest.mark.parametrize("package", ["kokkos", "kokkos-kernels", "arborx"])
def test_platform_files_load_the_common_set_and_pin_the_packages_under_test(
    name: str, package: str
) -> None:
    text = (_MODULEFILES / name).read_text()
    assert 'load("ufs_common")' in text
    assert 'try_load("arborx")' in text  # tolerant: not every environment has it
    # The pins stay overridable from the run config's module_env.
    for pin in ("kokkos_ver", "kokkos_kernels_ver", "arborx_ver", "cmake_ver"):
        assert f'os.getenv("{pin}")' in text, pin
    assert f'load(pathJoin("{package}", {package.replace("-", "_")}_ver))' in text


def _loads(text: str, call: str) -> bool:
    """Whether `text` has `call` as a statement of its own (so `load(...)`
    is not found inside `try_load(...)`)."""
    return re.search(rf"^\s*{re.escape(call)}$", text, re.MULTILINE) is not None


@pytest.mark.parametrize("name", _PLATFORM_FILES)
def test_the_kokkos_packages_are_required(name: str) -> None:
    # Every platform's environment carries Kokkos and kokkos-kernels (Gaea
    # C7's since 2026-11-20): a missing one fails the load, not the build.
    text = (_MODULEFILES / name).read_text()
    for package in ("kokkos", "kokkos-kernels"):
        assert _loads(text, f'load("{package}")'), package
        assert not _loads(text, f'try_load("{package}")'), package


def test_the_gaea_c7_file_is_the_weather_models_recipe() -> None:
    # ufs-weather-model's own C7 file: the site's default cray-mpich and
    # oneAPI make way for the versions the environment was built with, the
    # compilers are Intel's own (C7 has no Cray wrappers), and the versions
    # stay overridable from the run config's module_env.
    text = (_MODULEFILES / _GAEA_C7).read_text()
    assert _loads(text, 'unload("cray-mpich/9.2.0")')
    assert _loads(text, 'unload("oneapi")')
    assert _loads(text, 'load(pathJoin("oneapi", oneapi_ver))')
    assert 'oneapi_ver=os.getenv("oneapi_ver") or "2025.3.1"' in text
    assert 'stack_intel_ver=os.getenv("stack_intel_ver") or "2025.3.1"' in text
    assert 'stack_cray_mpich_ver=os.getenv("stack_cray_mpich_ver") or "9.2.0"' in text
    ordered = [
        text.index(needle)
        for needle in (
            'unload("cray-mpich/9.2.0")',
            'load(pathJoin("oneapi", oneapi_ver))',
            'load(pathJoin("stack-intel-oneapi-compilers", stack_intel_ver))',
            'load(pathJoin("stack-cray-mpich", stack_cray_mpich_ver))',
            'load("ufs_common")',
        )
    ]
    assert ordered == sorted(ordered)
    for variable, compiler in (("CC", "icx"), ("CXX", "icpx"), ("FC", "ifx")):
        assert f'setenv("{variable}","{compiler}")' in text
    assert 'setenv("CMAKE_Platform","gaeac7.oneapi")' in text
    assert '"ftn"' not in text


def test_the_gaea_c7_file_loads_the_environments_yaml_cpp() -> None:
    # CECE prefers an installed yaml-cpp over its submodule; the environment
    # has one (yaml-cpp/0.8.0), and loading its module is what lets
    # find_package see it. Pinnable like every other load.
    text = (_MODULEFILES / _GAEA_C7).read_text()
    assert 'yaml_cpp_ver=os.getenv("yaml_cpp_ver")' in text
    assert _loads(text, 'load(pathJoin("yaml-cpp", yaml_cpp_ver))')
    assert _loads(text, 'load("yaml-cpp")')
    assert text.index('load("yaml-cpp")') > text.index(
        'load(pathJoin("stack-cray-mpich"'
    )
