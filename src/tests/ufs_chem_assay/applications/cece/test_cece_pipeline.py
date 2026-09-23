"""The full maccity-suite flow — enumeration, mapping CSV, config generation,
driver invocation, assertions — with the process call mocked. No docker."""

from pathlib import Path, PurePosixPath

from pytest_mock import MockerFixture

from applications.cece.config import CeceConfig
from applications.cece.settings import CeceSettings
from applications.registry import get_application, load_suite
from assertions import assert_nc_file_count, assert_nc_filenames
from combos import enumerate_combos, write_combos_csv
from platforms import Platform
from runner import run_driver
from settings import Settings


def test_maccity_pipeline_runs_all_combos_mocked(
    mocker: MockerFixture,
    tmp_path: Path,
    suite_path: Path,
    maccity_expected_filenames: set[str],
) -> None:
    check_output = mocker.patch(
        "runner.subprocess.check_output",
        return_value=b"INFO: CECE Finalize completed successfully\n",
    )
    app = get_application("cece")
    settings = Settings(platform=Platform.LOCAL)
    app_settings = CeceSettings(root_dir=tmp_path)
    suite = load_suite(suite_path)
    base_config = CeceConfig.from_yaml(suite.config_path)
    combos = enumerate_combos(app.dimensions(suite.sweep, base_config))
    assert [combo.name for combo in combos] == [
        "MACCITY.map-bilinear",
        "MACCITY.map-consd",
        "MACCITY.map-passthrough",
    ]

    mapping = write_combos_csv(
        [
            (
                suite.name,
                combo,
                app.effective_parameters(
                    combo,
                    app.build_config(
                        combo, output_directory=".", config_path=suite.config_path
                    ),
                ),
            )
            for combo in combos
        ],
        run_id="01JZZZZZZZZZZZZZZZZZZZZZZZ",
        application="cece",
        csv_path=tmp_path / "combos.csv",
    )
    assert set(mapping["combo_id"]) == {combo.combo_id for combo in combos}
    assert set(mapping["suite"]) == {suite.name}

    container_root = PurePosixPath("/combo_runs")
    effective_timeout = min(suite.timeout_s, settings.run_timeout_s)

    for combo in combos:
        # Storage is by runtime ULID; combos.csv maps it back to the name.
        combo_dir = tmp_path / combo.combo_id
        combo_dir.mkdir()
        container_dir = container_root / combo.combo_id

        config = app.build_config(
            combo, output_directory=str(container_dir), config_path=suite.config_path
        )
        yaml_path = combo_dir / f"{combo.combo_id}.yaml"
        config.to_yaml(yaml_path)

        # The generated yaml round-trips through the model and carries the
        # combo's swept value (on the MACCITY stream) and output directory.
        reloaded = CeceConfig.from_yaml(yaml_path)
        assert reloaded.cece_data.streams[0].mapalgo.value == combo.name.removeprefix(
            "MACCITY.map-"
        )
        assert reloaded.output is not None
        assert reloaded.output.directory == str(container_dir)

        out_path = combo_dir / f"{combo.combo_id}.out"
        run_driver(
            settings,
            app,
            app_settings,
            driver_yaml=container_dir / f"{combo.combo_id}.yaml",
            out_path=out_path,
            timeout_s=effective_timeout,
            output_mount=(tmp_path, container_root),
        )
        assert out_path.read_bytes() == b"INFO: CECE Finalize completed successfully\n"

        # Fabricate the files a *correct* driver would produce (first write at
        # hour 1), then run the suite's assertions in derived mode.
        for name in maccity_expected_filenames:
            (combo_dir / name).touch()
        assert suite.assertions.expected_nc_file_count is None  # derived
        assert_nc_file_count(combo_dir, app.expected_output_count(config))
        assert suite.assertions.validate_filenames
        assert_nc_filenames(combo_dir, app.expected_output_filenames(config))

    assert check_output.call_count == 3
    for call in check_output.call_args_list:
        command = call.args[0]
        assert command[:3] == ["docker", "run", "--rm"]
        assert f"{tmp_path}:/combo_runs" in command
        assert call.kwargs["timeout"] == effective_timeout
