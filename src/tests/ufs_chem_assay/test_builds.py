"""Build variants: `applications.<name>.builds:`, named overlays on one
application section, each its own build directory, tests root and scripts
from the one checkout — the sweep over build configurations that CATChem's
compute-node and MUSICA builds need. Without `builds:` nothing changes."""

import logging
import re
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError
from pytest_mock import MockerFixture

from applications.base import BuildNode, Stage
from applications.registry import get_application
from cli.main import main
from cli.run_config import RunConfig
from cli.selectors import (
    build_configs,
    check_builds,
    one_build_config,
    toolchain_configs,
)
from cli.sessions import configured_tests, job_script_for_tests, provenance_for_tests
from cli.stages import render_stage
from logs import LOGGER_NAME
from tests.ufs_chem_assay.run_configs import (
    TEMPLATES_DIR,
    load_run_config,
    run_config_file,
)

_CATCHEM = get_application("catchem")
_CECE = get_application("cece")
_CLI_LOGGER = f"{LOGGER_NAME}.cli"
_ROOT = Path("/scratch/ursa-run")
_DEBUG = "-DCMAKE_BUILD_TYPE=Debug"
_KOKKOS = "-DCATCHEM_ENABLE_KOKKOS=ON"
_NUOPC = "-DCATCHEM_BUILD_NUOPC=ON"
_PRECISE = "-DCMAKE_CXX_FLAGS=-fp-model=precise"  # the oneapi toolchain's
_BUILDS: dict[str, object] = {
    "compute": {
        "cmake_args": [_DEBUG, _KOKKOS, "-DCATCHEM_BUILD_MUSICA=OFF", _NUOPC],
        "build_node": "compute",
    },
    "musica": {
        "cmake_args": [_DEBUG, _KOKKOS, "-DCATCHEM_BUILD_MUSICA=ON", _NUOPC],
        "build_node": "login",
        "configure_gates": ["MUSICA found"],
        "tests": {"enabled": True, "ntasks": 1, "cpus": 4, "minutes": 9},
    },
}


def _kokkos_file(tmp_path: Path, **overrides: object) -> Path:
    """config/ursa-kokkos.yaml with CATChem's two variants (and overrides)."""
    return run_config_file(
        tmp_path,
        "ursa-kokkos.yaml",
        overrides={"applications.catchem.builds": _BUILDS, **overrides},
        root_dir=_ROOT,
    )


def _kokkos(tmp_path: Path, **overrides: object) -> RunConfig:
    return load_run_config(_kokkos_file(tmp_path, **overrides))


def _oneapi(config: RunConfig) -> RunConfig:
    (oneapi,) = toolchain_configs(config, ["oneapi"])
    return oneapi


# ── the overlay ──────────────────────────────────────────────────────────


def test_a_variant_overlays_the_section_then_the_toolchain_overlays_it(
    tmp_path: Path,
) -> None:
    oneapi = _oneapi(_kokkos(tmp_path))
    compute, musica = build_configs(oneapi, _CATCHEM, None)
    plain = compute.applications["catchem"]
    assert plain.build_name == "compute"
    assert plain.build_dirname == "build-compute"
    assert plain.builds == {}  # a variant has no variants of its own
    # cmake_args replaced whole by the variant's, the toolchain's appended.
    assert plain.build_flags() == [
        _DEBUG,
        _KOKKOS,
        "-DCATCHEM_BUILD_MUSICA=OFF",
        _NUOPC,
        _PRECISE,
    ]
    assert plain.build_node is BuildNode.COMPUTE
    # Keys the variant leaves alone keep the section's values.
    assert plain.tests.ntasks == 1
    assert plain.build_minutes == 10
    m = musica.applications["catchem"]
    assert m.build_name == "musica"
    assert m.build_node is BuildNode.LOGIN
    assert m.configure_gates == ["MUSICA found"]  # unioned with the section's (none)
    # tests replaced whole, then the toolchain's tests_env merged over it.
    assert (m.tests.ntasks, m.tests.cpus, m.tests.minutes) == (1, 4, 9)
    assert m.tests.env == {"I_MPI_HYDRA_BOOTSTRAP": "fork"}
    # The other applications, and the checkout, are the variant's too.
    assert compute.applications["cece"] == oneapi.applications["cece"]
    assert compute.checkout_dir(_CATCHEM) == oneapi.checkout_dir(_CATCHEM)


def test_paths_follow_the_variant(tmp_path: Path) -> None:
    oneapi = _oneapi(_kokkos(tmp_path))
    compute, musica = build_configs(oneapi, _CATCHEM, None)
    assert (
        compute.tests_root(_CATCHEM)
        == _ROOT / "oneapi" / "tests" / "catchem" / "compute"
    )
    assert (
        musica.tests_root(_CATCHEM) == _ROOT / "oneapi" / "tests" / "catchem" / "musica"
    )
    # An application without variants: one unnamed build, today's paths.
    (cece,) = build_configs(oneapi, _CECE, None)
    assert cece is oneapi
    assert cece.applications["cece"].build_name is None
    assert cece.applications["cece"].build_dirname == "build"
    assert cece.tests_root(_CECE) == _ROOT / "oneapi" / "tests" / "cece"


def test_build_selection_narrows_and_refuses_unknown_names(tmp_path: Path) -> None:
    oneapi = _oneapi(_kokkos(tmp_path))
    (musica,) = build_configs(oneapi, _CATCHEM, ["musica"])
    assert musica.applications["catchem"].build_name == "musica"
    assert len(build_configs(oneapi, _CATCHEM, ["all"])) == 2
    assert len(build_configs(oneapi, _CATCHEM, ["compute musica"])) == 2
    # A selection names variants; an application without any keeps its one build.
    assert build_configs(oneapi, _CECE, ["musica"]) == [oneapi]
    # A name no variant of this application has selects nothing of it.
    assert build_configs(oneapi, _CATCHEM, ["release"]) == []


def test_the_recorded_selection_is_the_default(tmp_path: Path) -> None:
    oneapi = _oneapi(_kokkos(tmp_path, selected_builds=["compute"]))
    (compute,) = build_configs(oneapi, _CATCHEM, None)
    assert compute.applications["catchem"].build_name == "compute"


# ── refusals at load ─────────────────────────────────────────────────────


@pytest.mark.parametrize("name", ["all", "Compute", "-x", "a_b"])
def test_variant_names_name_a_directory_and_a_script(tmp_path: Path, name: str) -> None:
    with pytest.raises(ValidationError, match="is not a build name"):
        _kokkos(tmp_path, **{"applications.catchem.builds": {name: {}}})


def test_a_variant_cannot_change_the_checkout(tmp_path: Path) -> None:
    for key, value in (("git_url", "x"), ("ref", "main"), ("modulefile", "m")):
        builds = {"compute": {key: value}}
        with pytest.raises(ValidationError, match=key):
            _kokkos(tmp_path, **{"applications.catchem.builds": builds})


def test_a_variant_key_the_section_lacks_fails_the_load(tmp_path: Path) -> None:
    # monetio is a uv install: it has no configure arguments to overlay.
    path = run_config_file(
        tmp_path,
        "local.yaml",
        overrides={"applications.monetio.builds": {"x": {"cmake_args": ["-DA=1"]}}},
        root_dir=tmp_path / "runs",
    )
    with pytest.raises(ValidationError, match=r"applications\.monetio\.builds\.x"):
        load_run_config(path)


def test_each_variant_is_judged_as_it_builds(tmp_path: Path) -> None:
    # A variant's rule failure names the variant (and the toolchain).
    builds = {"compute": {"cmake_args": [_KOKKOS], "tests": {"enabled": True}}}
    with pytest.raises(
        ValidationError,
        match=r"toolchains\.oneapi: applications\.catchem\.builds\.compute: "
        r"tests\.enabled requires -DCMAKE_BUILD_TYPE=Debug",
    ):
        _kokkos(tmp_path, **{"applications.catchem.builds": builds})


def test_a_compute_variant_needs_slurm(tmp_path: Path) -> None:
    builds = {"compute": {"build_node": "compute"}}
    with pytest.raises(
        ValidationError,
        match=r"applications\.catchem\.builds\.compute: build_node: compute needs "
        "the slurm runtime",
    ):
        _kokkos(
            tmp_path,
            **{
                "applications.catchem.builds": builds,
                "applications.cece.build_node": "login",
                "applications.kokkos-smoke.build_node": "login",
                "harness.runtime": "native",
                "harness.submit_only": False,
            },
        )


def test_selected_builds_must_be_variants(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match=r"selected_builds: \['release'\]"):
        _kokkos(tmp_path, selected_builds=["release"])


# ── no variants: nothing changes ─────────────────────────────────────────


@pytest.mark.parametrize(
    ("template", "flags"),
    [
        ("ursa.yaml", ["--application=cece"]),
        ("local.yaml", ["--application=all"]),
        ("ursa-kokkos.yaml", ["--application=all", "--toolchain=all"]),
        ("gaea-c7.yaml", ["--application=cece"]),
    ],
)
def test_an_empty_builds_map_renders_exactly_as_none(
    tmp_path: Path, mocker: MockerFixture, template: str, flags: list[str]
) -> None:
    mocker.patch("cli.commands.run.run_bash")
    raw = yaml.safe_load((TEMPLATES_DIR / template).read_text())
    # An empty map where the template has none; a template's own variants
    # stay (they are not what this compares).
    explicit: dict[str, object] = {
        f"applications.{name}.builds": {}
        for name, section in raw["applications"].items()
        if not section.get("builds")
    }
    none: dict[str, object] = {}
    rendered: list[dict[str, str]] = []
    for label, overrides in (("none", none), ("empty", explicit)):
        root = tmp_path / label
        root.mkdir()
        path = run_config_file(root, template, overrides=overrides)
        assert (
            main(
                [
                    "run",
                    f"--config-file={path}",
                    f"--root-dir={root}",
                    *flags,
                    "--dry-run",
                ]
            )
            == 0
        )
        rendered.append(
            {
                p.name: p.read_text().replace(str(root), "<root>")
                for p in sorted((root / "scripts").iterdir())
                if p.name != "run-config.yaml"
            }
        )
    assert rendered[0] == rendered[1]


# ── rendering ────────────────────────────────────────────────────────────


def test_run_renders_one_source_and_a_build_and_test_per_variant(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    mocker.patch("cli.commands.run.run_bash")
    path = _kokkos_file(tmp_path)
    root = tmp_path / "root"
    command = ["run", f"--config-file={path}", f"--root-dir={root}"]
    stages = ["--stage=source", "--stage=build", "--stage=test"]
    assert (
        main(
            [
                *command,
                "--application=catchem",
                "--toolchain=oneapi",
                *stages,
                "--dry-run",
            ]
        )
        == 0
    )
    scripts = root / "scripts"
    assert sorted(p.name for p in scripts.iterdir()) == [
        "01-source-catchem-oneapi.sh",  # one checkout serves every variant
        "02-build-catchem-oneapi-compute.sh",
        "02-build-catchem-oneapi-musica.sh",
        "04-test-catchem-oneapi-compute.sbatch",
        "04-test-catchem-oneapi-compute.sh",
        "04-test-catchem-oneapi-musica.sbatch",
        "04-test-catchem-oneapi-musica.sh",
        "run-config.yaml",
    ]
    clone = root / "oneapi" / "CATChem"
    compute = (scripts / "02-build-catchem-oneapi-compute.sh").read_text()
    assert ">>> stage: build (catchem, oneapi, compute)" in compute
    assert f"-B {clone}/build-compute " in compute
    assert "srun " in compute  # the compute variant's build node
    assert "-DCATCHEM_BUILD_MUSICA=OFF" in compute
    assert "login-node finish" not in compute  # MUSICA off: the whole build
    musica = (scripts / "02-build-catchem-oneapi-musica.sh").read_text()
    assert f"-B {clone}/build-musica " in musica
    assert "srun " not in musica
    assert "grep -Eq 'MUSICA found'" in musica
    test = (scripts / "04-test-catchem-oneapi-musica.sh").read_text()
    assert f"[ -f {clone}/build-musica/CTestTestfile.cmake ]" in test
    assert "--application catchem --toolchain oneapi --build musica" in test
    job = (scripts / "04-test-catchem-oneapi-musica.sbatch").read_text()
    assert "ctest --test-dir build-musica " in job
    assert f"--output={root}/oneapi/tests/catchem/musica/tests-%j.out" in job
    assert "#SBATCH -c 4" in job
    assert "#SBATCH --time=9" in job
    # The effective config records the selection for watch and report:
    # none named is every variant.
    effective = yaml.safe_load((scripts / "run-config.yaml").read_text())
    assert effective["selected_builds"] == []
    assert effective["applications"]["catchem"]["builds"].keys() == {
        "compute",
        "musica",
    }


def test_build_narrows_the_variants_run_renders(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    mocker.patch("cli.commands.run.run_bash")
    path = _kokkos_file(tmp_path)
    root = tmp_path / "root"
    assert (
        main(
            [
                "run",
                f"--config-file={path}",
                f"--root-dir={root}",
                "--application=all",
                "--toolchain=oneapi",
                "--build=musica",
                "--stage=build",
                "--dry-run",
            ]
        )
        == 0
    )
    names = sorted(p.name for p in (root / "scripts").glob("02-*.sh"))
    assert names == [
        "02-build-catchem-oneapi-musica.sh",
        "02-build-cece-oneapi.sh",  # no variants: its one build
        "02-build-kokkos-smoke-oneapi.sh",
    ]
    effective = yaml.safe_load((root / "scripts" / "run-config.yaml").read_text())
    assert effective["selected_builds"] == ["musica"]


def test_an_unknown_build_name_is_an_error(
    tmp_path: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    mocker.patch("cli.commands.run.run_bash")
    path = _kokkos_file(tmp_path)
    with caplog.at_level(logging.ERROR, logger=_CLI_LOGGER):
        code = main(
            [
                "run",
                f"--config-file={path}",
                f"--root-dir={tmp_path / 'root'}",
                "--application=catchem",
                "--toolchain=oneapi",
                "--build=release",
                "--stage=build",
                "--dry-run",
            ]
        )
    assert code == 1
    assert "--build ['release'] is no build of the selected applications" in caplog.text


def test_driver_stages_refuse_an_application_with_variants(
    tmp_path: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    # A driver session runs against one build; which one is not designed yet.
    mocker.patch("cli.commands.run.run_bash")
    path = run_config_file(
        tmp_path,
        "ursa.yaml",
        overrides={
            "applications.cece.builds": {
                "release": {"cmake_args": ["-DCMAKE_BUILD_TYPE=Release"]}
            }
        },
        root_dir=_ROOT,
    )
    with caplog.at_level(logging.ERROR, logger=_CLI_LOGGER):
        code = main(
            [
                "run",
                f"--config-file={path}",
                f"--root-dir={tmp_path / 'root'}",
                "--application=cece",
                "--stage=build",
                "--stage=data",
                "--dry-run",
            ]
        )
    assert code == 1
    assert (
        "applications.cece has builds: (release), and the data and harness stages "
        "run a driver against one build, which a run config cannot name yet"
    ) in caplog.text


# ── the commands that find a test run ────────────────────────────────────


def test_watch_and_report_see_one_test_run_per_variant(tmp_path: Path) -> None:
    oneapi = _oneapi(_kokkos(tmp_path))
    labels = [t.label for t in configured_tests(oneapi, [_CECE, _CATCHEM])]
    assert labels == [
        "tests cece/oneapi",
        "tests catchem/oneapi/compute",
        "tests catchem/oneapi/musica",
    ]
    roots = [t.root for t in configured_tests(oneapi, [_CATCHEM], ["musica"])]
    assert roots == [_ROOT / "oneapi" / "tests" / "catchem" / "musica"]


def test_the_job_script_and_the_provenance_follow_the_variant(tmp_path: Path) -> None:
    (root := tmp_path / "root").mkdir()
    oneapi = _oneapi(_kokkos(tmp_path, root_dir=str(root)))
    _, musica = build_configs(oneapi, _CATCHEM, None)
    assert job_script_for_tests(root / "scripts", _CATCHEM, "oneapi", "musica") == (
        root / "scripts" / "04-test-catchem-oneapi-musica.sbatch"
    )
    cache = musica.checkout_dir(_CATCHEM) / "build-musica" / "CMakeCache.txt"
    cache.parent.mkdir(parents=True)
    cache.write_text("CMAKE_BUILD_TYPE:STRING=Debug\n")
    assert provenance_for_tests(musica, _CATCHEM).build_type == "Debug"
    assert provenance_for_tests(oneapi, _CATCHEM).build_type is None  # build/ is absent


def test_submit_tests_finds_the_variants_job(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    mocker.patch("cli.commands.run.run_bash")
    path = _kokkos_file(tmp_path)
    root = tmp_path / "root"
    stages = ["--stage=build", "--stage=test"]
    command = [
        "run",
        f"--config-file={path}",
        f"--root-dir={root}",
        "--application=catchem",
        "--toolchain=oneapi",
    ]
    assert main([*command, *stages, "--dry-run"]) == 0
    clone = root / "oneapi" / "CATChem" / "build-musica"
    clone.mkdir(parents=True)
    (clone / "CTestTestfile.cmake").write_text("")
    submit = mocker.patch("apptests.submit")
    args = [
        "submit-tests",
        f"--root-dir={root}",
        "--application=catchem",
        "--toolchain=oneapi",
    ]
    assert main([*args, "--build=musica"]) == 0
    kwargs = submit.call_args.kwargs
    assert submit.call_args.args[0] == root / "oneapi" / "tests" / "catchem" / "musica"
    assert kwargs["script"] == root / "scripts" / "04-test-catchem-oneapi-musica.sbatch"
    assert kwargs["timeout_s"] == 9 * 60
    assert kwargs["build"] == "musica"  # recorded in the ledger and the row
    # Several variants and none named: the command works on one.
    assert main(args) == 1


def test_render_stage_names_the_variant(tmp_path: Path) -> None:
    oneapi = _oneapi(_kokkos(tmp_path))
    compute, _ = build_configs(oneapi, _CATCHEM, None)
    script = render_stage(Stage.BUILD, compute, _CATCHEM)
    assert script.name == "build-catchem-oneapi-compute"
    assert re.search(
        r'^echo ">>> stage: build \(catchem, oneapi, compute\)"$',
        script.text,
        re.MULTILINE,
    )


def test_the_build_flag_needs_a_name(tmp_path: Path) -> None:
    oneapi = _oneapi(_kokkos(tmp_path))
    with pytest.raises(ValueError, match="--build requires at least one build name"):
        check_builds(oneapi, [_CATCHEM], [" "])
    assert check_builds(oneapi, [_CATCHEM], ["all"]) == []
    assert check_builds(oneapi, [_CATCHEM], ["musica"]) == ["musica"]


def test_a_per_build_command_finds_its_one_variant(tmp_path: Path) -> None:
    oneapi = _oneapi(
        _kokkos(
            tmp_path,
            **{"applications.catchem.builds": {"only": {"build_node": "login"}}},
        )
    )
    # One variant: no name needed.
    only = one_build_config(oneapi, _CATCHEM, None)
    assert only.applications["catchem"].build_name == "only"
    # No variants: the config itself, and naming one is an error.
    assert one_build_config(oneapi, _CECE, None) is oneapi
    with pytest.raises(
        ValueError, match=r"--build 'x' is no build of cece \(it has no builds:\)"
    ):
        one_build_config(oneapi, _CECE, "x")


def test_evaluate_tests_records_the_variant_of_a_run_in_place(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    # native runtime: the test stage runs ctest in place, then evaluate-tests.
    mocker.patch("cli.commands.run.run_bash")
    login = {
        name: {**overlay, "build_node": "login"}  # type: ignore[dict-item]
        for name, overlay in _BUILDS.items()
    }
    path = _kokkos_file(
        tmp_path,
        **{
            "applications.catchem.builds": login,
            "applications.cece.build_node": "login",
            "applications.kokkos-smoke.build_node": "login",
            "harness.runtime": "native",
            "harness.submit_only": False,
        },
    )
    root = tmp_path / "root"
    run = ["run", f"--config-file={path}", f"--root-dir={root}"]
    assert (
        main(
            [
                *run,
                "--application=catchem",
                "--toolchain=oneapi",
                "--stage=test",
                "--dry-run",
            ]
        )
        == 0
    )
    record = mocker.patch("apptests.record_local")
    evaluate = mocker.patch("apptests.evaluate")
    evaluate.return_value.tests = None
    evaluate.return_value.exit_status = 0
    args = [
        "evaluate-tests",
        f"--root-dir={root}",
        "--application=catchem",
        "--toolchain=oneapi",
    ]
    assert main([*args, "--build=compute", "--exit-status=0"]) == 0
    assert record.call_args.args[0] == root / "oneapi" / "tests" / "catchem" / "compute"
    assert record.call_args.kwargs["build"] == "compute"
