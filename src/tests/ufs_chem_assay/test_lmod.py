"""The Lmod block: one rendering for the stage scripts (build) and the job
scripts (driver), so a job loads exactly the environment the build did."""

from pathlib import Path

from lmod import LMOD_INIT, module_lines
from platforms import ModuleInit


def test_module_lines_are_init_exports_use_load_list_in_that_order() -> None:
    assert module_lines(
        init=ModuleInit.PURGE,
        env={"kokkos_ver": "4.5.01", "ASSAY_SPACK_ENV_ROOT": "/envs/stack oneapi"},
        paths=[Path("/harness/modulefiles"), Path("/envs/stack/modules/Core")],
        modulefile="ufs_ursa.oneapi",
    ) == [
        LMOD_INIT,
        "module purge",
        "export kokkos_ver=4.5.01",
        "export ASSAY_SPACK_ENV_ROOT='/envs/stack oneapi'",  # shell-quoted
        "module use /harness/modulefiles",
        "module use /envs/stack/modules/Core",
        "module load ufs_ursa.oneapi",
        "module list",
    ]


def test_module_lines_reset_where_purge_would_strip_the_machine() -> None:
    # Gaea: `module purge` removes Cray's craype/PrgEnv machinery.
    lines = module_lines(
        init=ModuleInit.RESET,
        env={},
        paths=[Path("/clone/modulefiles")],
        modulefile="ufs_gaeac6.oneapi",
    )
    assert lines == [
        LMOD_INIT,
        "module reset",
        "module use /clone/modulefiles",
        "module load ufs_gaeac6.oneapi",
        "module list",
    ]


def test_lmod_init_is_guarded_for_shells_without_lmod() -> None:
    assert LMOD_INIT.startswith('if [ -n "${MODULESHOME:-}" ]')
