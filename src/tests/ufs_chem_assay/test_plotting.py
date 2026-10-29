import logging
from pathlib import Path

import matplotlib
import matplotlib.axes
import matplotlib.colors
import numpy as np
import pandas as pd
import pytest
import xarray as xr
from cartopy.mpl.geoaxes import GeoAxes
from matplotlib.colors import LogNorm, SymLogNorm
from PIL import GifImagePlugin, Image
from pydantic import ValidationError
from pytest_mock import MockerFixture

from applications.registry import load_suite
from models.suite_config import BiasColorScale, ColorScale, Plotting
from plotting import (
    BIAS_PLOTS_DIRNAME,
    PLOTS_DIRNAME,
    ZERO_COLOR,
    VariableScale,
    derive_bias_scales,
    derive_scales,
    make_norm,
    render_all_bias_plots,
    render_all_plots,
    render_bias_plot,
    render_combo_bias_plots,
    render_combo_plots,
    render_spatial_plot,
)

_LINEAR = Plotting(scale=ColorScale.linear)


def _write_nc(path: Path, values: np.ndarray, hour: int) -> None:
    dataset = xr.Dataset(
        {"co": (("time", "lat", "lon"), values)},
        coords={
            "time": pd.date_range(f"2010-01-01T{hour:02d}:00:00", periods=1, freq="h"),
            "lat": np.linspace(-90.0, 90.0, values.shape[1]),
            "lon": np.linspace(-180.0, 180.0, values.shape[2]),
        },
    )
    dataset["co"].attrs["units"] = "kg m-2 s-1"
    dataset.to_netcdf(path, engine="netcdf4")


@pytest.fixture
def combo_dirs(tmp_path: Path) -> tuple[Path, np.ndarray, np.ndarray]:
    """Two combo directories with driver-like NetCDFs of known ranges."""
    rng = np.random.default_rng(7)
    first = rng.random((1, 4, 5))
    second = rng.random((1, 4, 5)) + 2.0  # disjoint range: forces suite-wide scale
    dir_a = tmp_path / "3f9a1c2b7d4e8a01"
    dir_b = tmp_path / "9004a4e23c1dd90a"
    dir_a.mkdir()
    dir_b.mkdir()
    _write_nc(dir_a / "cece_20100101_010000.nc", first, hour=1)
    _write_nc(dir_a / "cece_20100101_020000.nc", first * 0.5, hour=2)
    _write_nc(dir_b / "cece_20100101_010000.nc", second, hour=1)
    return tmp_path, first, second


def _stats_frame(
    rows: list[tuple[str, float, float]], min_positive: list[float | None] | None = None
) -> pd.DataFrame:
    frame = pd.DataFrame(
        [
            {"variable": variable, "min": vmin, "max": vmax}
            for variable, vmin, vmax in rows
        ]
    )
    if min_positive is not None:
        frame["min_positive"] = min_positive
    return frame


def test_derive_scales_linear_exact_suite_wide_min_max() -> None:
    stats = _stats_frame(
        [("co", 0.2, 0.9), ("co", 0.1, 0.5), ("co", 0.3, 2.9), ("nox", -1.0, 1.0)]
    )
    scales = derive_scales(stats, _LINEAR)
    assert scales["co"] == VariableScale(variable="co", vmin=0.1, vmax=2.9)
    assert scales["nox"] == VariableScale(variable="nox", vmin=-1.0, vmax=1.0)
    assert make_norm(scales["co"]) is None


def test_derive_scales_linear_guards_degenerate_range() -> None:
    scales = derive_scales(_stats_frame([("co", 0.0, 0.0)]), _LINEAR)
    assert scales["co"].vmin < scales["co"].vmax  # all-zero field still renders


def test_derive_scales_log_by_default_with_the_decade_floor() -> None:
    # The maccity shape: zeros, a long tail, an exact maximum. Five decades
    # below the maximum is the floor; the maximum stays exact.
    stats = _stats_frame(
        [("co", 0.0, 5.0e-9), ("co", 0.0, 2.0e-9)], min_positive=[7.6e-19, 8.0e-19]
    )
    scales = derive_scales(stats)
    scale = scales["co"]
    assert scale.scale is ColorScale.log
    assert scale.vmax == 5.0e-9
    assert scale.floor == pytest.approx(5.0e-14)
    assert scale.vmin == scale.floor
    norm = make_norm(scale)
    assert isinstance(norm, LogNorm)
    assert (norm.vmin, norm.vmax) == (scale.floor, 5.0e-9)


def test_derive_scales_floor_never_below_the_smallest_positive_value() -> None:
    stats = _stats_frame([("co", 0.0, 1.0)], min_positive=[0.01])
    assert derive_scales(stats, Plotting(decades=5))["co"].floor == 0.01
    assert derive_scales(stats, Plotting(decades=None))["co"].floor == 0.01
    assert derive_scales(stats, Plotting(decades=1))["co"].floor == 0.1


def test_derive_scales_explicit_floor_and_its_refusal(
    caplog: pytest.LogCaptureFixture,
) -> None:
    stats = _stats_frame([("co", 0.0, 1.0)], min_positive=[1e-9])
    assert derive_scales(stats, Plotting(floor=1e-3))["co"].floor == 1e-3
    with caplog.at_level(logging.WARNING):
        scale = derive_scales(stats, Plotting(floor=2.0))["co"]
    assert scale.floor == pytest.approx(1e-5)  # the decade rule instead
    assert any(
        "not below the suite-wide maximum" in r.getMessage() for r in caplog.records
    )


def test_derive_scales_without_a_positive_floor_falls_back_to_linear(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # An all-zero field: nothing to take the log of.
    with caplog.at_level(logging.WARNING):
        scales = derive_scales(_stats_frame([("co", 0.0, 0.0)], min_positive=[None]))
    assert scales["co"].scale is ColorScale.linear
    assert scales["co"].floor is None
    assert scales["co"].vmin < scales["co"].vmax
    assert any("linear scale instead" in r.getMessage() for r in caplog.records)
    # decades: null with no positive value: the same fallback.
    scales = derive_scales(_stats_frame([("co", 0.0, 1.0)]), Plotting(decades=None))
    assert scales["co"].scale is ColorScale.linear


def test_derive_scales_tolerates_stats_without_min_positive() -> None:
    # A stats CSV from before the column: the decade rule alone.
    scales = derive_scales(_stats_frame([("co", 0.0, 1.0)]))
    assert scales["co"].floor == pytest.approx(1e-5)


def test_derive_scales_symlog_keeps_zero_on_the_colorbar() -> None:
    stats = _stats_frame([("co", 0.0, 1.0)], min_positive=[1e-3])
    scale = derive_scales(stats, Plotting(scale=ColorScale.symlog, decades=2))["co"]
    assert scale.scale is ColorScale.symlog
    assert (scale.vmin, scale.vmax, scale.floor) == (0.0, 1.0, 1e-2)
    norm = make_norm(scale)
    assert isinstance(norm, SymLogNorm)
    assert (norm.vmin, norm.vmax) == (0.0, 1.0)


def test_render_spatial_plot_writes_png(
    tmp_path: Path, combo_dirs: tuple[Path, np.ndarray, np.ndarray]
) -> None:
    root, first, _ = combo_dirs
    out_path = tmp_path / "co__test.png"
    scale = VariableScale(
        variable="co", vmin=float(first.min()), vmax=float(first.max())
    )
    render_spatial_plot(
        root / "3f9a1c2b7d4e8a01" / "cece_20100101_010000.nc", "co", scale, out_path
    )
    assert out_path.is_file()
    assert out_path.stat().st_size > 0


def test_render_spatial_plot_log_masks_zeros_and_marks_the_title(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    values = np.array([[[0.0, 1e-12, 1e-10, 1e-9, 0.0]] * 4])
    _write_nc(tmp_path / "f.nc", values, hour=1)
    mesh = mocker.spy(GeoAxes, "pcolormesh")
    title = mocker.spy(matplotlib.axes.Axes, "set_title")
    face = mocker.spy(matplotlib.axes.Axes, "set_facecolor")
    scale = VariableScale(
        variable="co", vmin=1e-14, vmax=1e-9, scale=ColorScale.log, floor=1e-14
    )
    render_spatial_plot(tmp_path / "f.nc", "co", scale, tmp_path / "co.png")
    kwargs = mesh.call_args.kwargs
    assert isinstance(kwargs["norm"], LogNorm)
    assert "vmin" not in kwargs  # a norm and vmin/vmax never both
    data = mesh.call_args.args[3]
    assert np.ma.is_masked(data)
    assert int(np.ma.count_masked(data)) == 8  # the zeros, transparent …
    assert face.call_args.args[1] == ZERO_COLOR  # … over a grey axes face
    assert title.call_args.args[1].endswith("[log]")


def test_render_spatial_plot_linear_passes_vmin_vmax_without_a_norm(
    tmp_path: Path,
    combo_dirs: tuple[Path, np.ndarray, np.ndarray],
    mocker: MockerFixture,
) -> None:
    root, first, _ = combo_dirs
    mesh = mocker.spy(GeoAxes, "pcolormesh")
    scale = VariableScale(variable="co", vmin=0.0, vmax=float(first.max()))
    render_spatial_plot(
        root / "3f9a1c2b7d4e8a01" / "cece_20100101_010000.nc",
        "co",
        scale,
        tmp_path / "co.png",
    )
    kwargs = mesh.call_args.kwargs
    assert "norm" not in kwargs
    assert (kwargs["vmin"], kwargs["vmax"], kwargs["cmap"]) == (
        0.0,
        float(first.max()),
        "viridis",
    )


def test_render_combo_plots_pngs_and_gif(
    combo_dirs: tuple[Path, np.ndarray, np.ndarray],
) -> None:
    root, _, _ = combo_dirs
    combo_dir = root / "3f9a1c2b7d4e8a01"
    scales = {"co": VariableScale(variable="co", vmin=0.0, vmax=1.0)}

    render_combo_plots(combo_dir, scales, gif_enabled=True)

    plots = combo_dir / "plots-overview"
    assert (plots / "co__cece_20100101_010000.png").is_file()
    assert (plots / "co__cece_20100101_020000.png").is_file()
    gif = plots / "co.gif"
    assert gif.is_file()
    with Image.open(gif) as image:
        assert isinstance(image, GifImagePlugin.GifImageFile)
        assert image.n_frames == 2  # one frame per NetCDF, in timestamp order


def test_render_combo_plots_gif_disabled(
    combo_dirs: tuple[Path, np.ndarray, np.ndarray],
) -> None:
    root, *_ = combo_dirs
    combo_dir = root / "9004a4e23c1dd90a"
    render_combo_plots(
        combo_dir,
        {"co": VariableScale(variable="co", vmin=0.0, vmax=3.0)},
        gif_enabled=False,
    )
    plots = combo_dir / "plots-overview"
    assert (plots / "co__cece_20100101_010000.png").is_file()
    assert not (plots / "co.gif").exists()


def test_render_all_plots_covers_every_combo_dir(
    combo_dirs: tuple[Path, np.ndarray, np.ndarray],
) -> None:
    root, first, second = combo_dirs
    stats = _stats_frame(
        [
            ("co", float(first.min() * 0.5), float(first.max())),
            ("co", float(second.min()), float(second.max())),
        ]
    )
    # render_all_plots derives combo directories from the frame (a per-suite
    # slice renders only that suite's combos on a flat multi-suite root).
    stats["combo_id"] = ["3f9a1c2b7d4e8a01", "9004a4e23c1dd90a"]
    render_all_plots(root, stats, Plotting(gif_enabled=True))
    assert (root / "3f9a1c2b7d4e8a01" / "plots-overview" / "co.gif").is_file()
    assert (root / "9004a4e23c1dd90a" / "plots-overview" / "co.gif").is_file()


def test_plotting_defaults_and_unknown_key() -> None:
    assert Plotting() == Plotting(
        enabled=True,
        gif_enabled=True,
        scale=ColorScale.log,
        decades=5,
        floor=None,
        bias_scale=BiasColorScale.linear,
        bias_decades=5,
    )
    with pytest.raises(ValidationError):
        Plotting(gif_speed=1)  # type: ignore[call-arg]
    for bad in (
        {"scale": "percentile"},
        {"decades": 0},
        {"floor": -1.0},
        {"bias_scale": "log"},
    ):
        with pytest.raises(ValidationError):
            Plotting.model_validate(bad)
    assert (
        Plotting(scale=ColorScale.linear, floor=3.0).floor == 3.0
    )  # accepted, ignored


def test_plotting_requires_descriptive_stats(
    tmp_path: Path, cece_config_path: Path
) -> None:
    suite_file = tmp_path / "plots-no-stats-suite.yaml"
    suite_file.write_text(
        f"name: plots-no-stats\nconfig_path: {cece_config_path}\ntimeout_s: 5\n"
        "analysis:\n  compute_descriptive_stats: false\n"
        "plotting:\n  enabled: true\n"
        "sweep:\n  cece_data:\n    streams:\n      - name: MACCITY\n        mapalgo: [consd]\n"
    )
    with pytest.raises(ValidationError, match="compute_descriptive_stats"):
        load_suite(suite_file)


def test_stats_disabled_with_plotting_disabled_is_valid(
    tmp_path: Path, cece_config_path: Path
) -> None:
    suite_file = tmp_path / "no-stats-no-plots-suite.yaml"
    suite_file.write_text(
        f"name: no-stats-no-plots\nconfig_path: {cece_config_path}\ntimeout_s: 5\n"
        "analysis:\n  compute_descriptive_stats: false\n"
        "plotting:\n  enabled: false\n"
        "sweep:\n  cece_data:\n    streams:\n      - name: MACCITY\n        mapalgo: [consd]\n"
    )
    suite = load_suite(suite_file)
    assert suite.plotting.enabled is False


def _write_pair(realization: Path, baseline: Path, offset: float, hour: int) -> None:
    rng = np.random.default_rng(3)
    base_values = rng.random((1, 4, 5))
    name = f"cece_201001{hour:02d}_010000.nc"
    _write_nc(realization / name, base_values + offset, hour=1)
    _write_nc(baseline / name, base_values, hour=1)


def test_derive_bias_scales_symmetric_suite_wide() -> None:
    stats = pd.DataFrame(
        [
            {"variable": "co", "max_abs_diff": 0.25},
            {"variable": "co", "max_abs_diff": 0.75},
            {"variable": "nox", "max_abs_diff": 0.1},
        ]
    )
    scales = derive_bias_scales(stats)
    assert (scales["co"].vmin, scales["co"].vmax) == (-0.75, 0.75)
    assert (scales["nox"].vmin, scales["nox"].vmax) == (-0.1, 0.1)
    assert scales["co"].scale is BiasColorScale.linear
    assert make_norm(scales["co"]) is None
    # symlog on request: linthresh by the bias_decades rule, symmetric.
    symlog = derive_bias_scales(
        stats, Plotting(bias_scale=BiasColorScale.symlog, bias_decades=3)
    )
    assert symlog["co"].floor == pytest.approx(0.75e-3)
    norm = make_norm(symlog["co"])
    assert isinstance(norm, SymLogNorm)
    assert (norm.vmin, norm.vmax) == (-0.75, 0.75)


def test_derive_bias_scales_guards_zero_bias() -> None:
    scales = derive_bias_scales(pd.DataFrame([{"variable": "co", "max_abs_diff": 0.0}]))
    assert scales["co"].vmin < 0 < scales["co"].vmax  # flat bias still renders


def test_render_bias_plot_symlog_uses_a_norm(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    values = np.array([[[0.0, 1e-12, 1e-10, 1e-9, 0.0]] * 4])
    _write_nc(tmp_path / "real.nc", values * 2, hour=1)
    _write_nc(tmp_path / "base.nc", values, hour=1)
    mesh = mocker.spy(GeoAxes, "pcolormesh")
    scale = VariableScale(
        variable="co", vmin=-1e-9, vmax=1e-9, scale=BiasColorScale.symlog, floor=1e-14
    )
    render_bias_plot(
        tmp_path / "real.nc", tmp_path / "base.nc", "co", scale, tmp_path / "b.png"
    )
    assert isinstance(mesh.call_args.kwargs["norm"], SymLogNorm)
    assert "vmin" not in mesh.call_args.kwargs


def test_render_combo_bias_plots_pngs_and_gif(tmp_path: Path) -> None:
    from plotting import VariableScale, render_combo_bias_plots

    realization = tmp_path / "combo"
    baseline = tmp_path / "baseline"
    realization.mkdir()
    baseline.mkdir()
    _write_pair(realization, baseline, offset=0.1, hour=1)
    _write_pair(realization, baseline, offset=0.2, hour=2)

    scales = {"co": VariableScale(variable="co", vmin=-0.3, vmax=0.3)}
    render_combo_bias_plots(realization, baseline, scales)

    plots = realization / "plots-baselines"
    pngs = sorted(plots.glob("co__*.png"))
    assert len(pngs) == 2
    assert all(p.stat().st_size > 0 for p in pngs)
    gif = plots / "co.gif"
    assert gif.is_file()
    with Image.open(gif) as image:
        assert isinstance(image, GifImagePlugin.GifImageFile)
        assert image.n_frames == 2  # gif always accompanies the bias plots


def test_render_combo_plots_skips_bounds_variables(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """CF cell bounds written as data variables (lon_bnds/lat_bnds, no latxlon
    dims) are not fields: no plot attempt, no error logged."""
    combo_dir = tmp_path / "3f9a1c2b7d4e8a01"
    combo_dir.mkdir()
    path = combo_dir / "cece_20100101_010000.nc"
    _write_nc(path, np.random.default_rng(3).random((1, 4, 5)), hour=1)
    with xr.open_dataset(path) as ds:
        dataset = ds.load()
    lon = dataset["lon"].values
    lat = dataset["lat"].values
    dataset["lon_bnds"] = (
        ("lon", "lon_bnds_dim1"),
        np.stack([lon - 1, lon + 1], axis=1),
    )
    dataset["lat_bnds"] = (
        ("lat", "lon_bnds_dim1"),
        np.stack([lat - 1, lat + 1], axis=1),
    )
    dataset.to_netcdf(path, engine="netcdf4")
    scales = {
        "co": VariableScale(variable="co", vmin=0.0, vmax=1.0),
        "lon_bnds": VariableScale(variable="lon_bnds", vmin=-181.0, vmax=181.0),
        "lat_bnds": VariableScale(variable="lat_bnds", vmin=-91.0, vmax=91.0),
    }

    with caplog.at_level(logging.WARNING, logger="ufs-chem-assay.plotting"):
        render_combo_plots(combo_dir, scales, gif_enabled=False)

    plots = combo_dir / "plots-overview"
    assert sorted(p.name for p in plots.iterdir()) == ["co__cece_20100101_010000.png"]
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


# ── the fallbacks: offline boundaries, missing scales, render failures ────────


def _raise(*_: object, **__: object) -> None:
    raise RuntimeError("boom")


def test_spatial_plot_survives_missing_natural_earth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # Offline: cartopy cannot fetch coastlines/borders; the map still renders.
    from cartopy.mpl.geoaxes import GeoAxes

    monkeypatch.setattr(GeoAxes, "coastlines", _raise)
    monkeypatch.setattr(GeoAxes, "add_feature", _raise)
    values = np.random.default_rng(1).random((1, 4, 5))
    _write_nc(tmp_path / "a.nc", values, hour=1)
    with caplog.at_level(logging.WARNING):
        render_spatial_plot(
            tmp_path / "a.nc",
            "co",
            VariableScale(variable="co", vmin=0.0, vmax=1.0),
            tmp_path / "a.png",
        )
    assert (tmp_path / "a.png").is_file()
    messages = [r.getMessage() for r in caplog.records]
    assert any("coastlines" in m for m in messages)
    assert any("BORDERS" in m for m in messages)


def test_spatial_plot_takes_the_first_level(tmp_path: Path) -> None:
    dataset = xr.Dataset(
        {"co": (("time", "lev", "lat", "lon"), np.ones((1, 2, 4, 5)))},
        coords={
            "time": pd.date_range("2010-01-01T01:00:00", periods=1, freq="h"),
            "lev": [1.0, 2.0],
            "lat": np.linspace(-90.0, 90.0, 4),
            "lon": np.linspace(-180.0, 180.0, 5),
        },
    )
    dataset.to_netcdf(tmp_path / "lev.nc", engine="netcdf4")
    render_spatial_plot(
        tmp_path / "lev.nc",
        "co",
        VariableScale(variable="co", vmin=0.0, vmax=1.0),
        tmp_path / "lev.png",
    )
    assert (tmp_path / "lev.png").is_file()


def test_bias_plot_survives_missing_natural_earth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from cartopy.mpl.geoaxes import GeoAxes

    monkeypatch.setattr(GeoAxes, "coastlines", _raise)
    monkeypatch.setattr(GeoAxes, "add_feature", _raise)
    realization, baseline = tmp_path / "r", tmp_path / "b"
    realization.mkdir()
    baseline.mkdir()
    _write_pair(realization, baseline, 0.5, hour=1)
    with caplog.at_level(logging.WARNING):
        render_bias_plot(
            realization / "cece_20100101_010000.nc",
            baseline / "cece_20100101_010000.nc",
            "co",
            VariableScale(variable="co", vmin=-1.0, vmax=1.0),
            tmp_path / "bias.png",
        )
    assert (tmp_path / "bias.png").is_file()
    assert sum("could not draw" in r.getMessage() for r in caplog.records) == 2


def test_combo_plots_skip_without_files_or_scale(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    render_combo_plots(empty, {}, gif_enabled=True)
    assert not (empty / PLOTS_DIRNAME).exists()
    combo = tmp_path / "combo"
    combo.mkdir()
    _write_nc(combo / "cece_20100101_010000.nc", np.ones((1, 4, 5)), hour=1)
    with caplog.at_level(logging.WARNING):
        render_combo_plots(combo, {}, gif_enabled=True)  # no scale for co
    assert any("no scale for variable co" in r.getMessage() for r in caplog.records)
    assert not list((combo / PLOTS_DIRNAME).glob("*.png"))


def test_combo_plots_log_render_and_gif_failures(
    tmp_path: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    combo = tmp_path / "combo"
    combo.mkdir()
    _write_nc(combo / "cece_20100101_010000.nc", np.ones((1, 4, 5)), hour=1)
    scales = {"co": VariableScale(variable="co", vmin=0.0, vmax=1.0)}
    mocker.patch("plotting.render_spatial_plot", side_effect=RuntimeError("png"))
    with caplog.at_level(logging.ERROR):
        render_combo_plots(combo, scales, gif_enabled=True)
    assert any("failed to render" in r.getMessage() for r in caplog.records)
    caplog.clear()
    mocker.patch("plotting.render_spatial_plot")  # succeeds, writes nothing
    mocker.patch("plotting.render_gif", side_effect=RuntimeError("gif"))
    with caplog.at_level(logging.ERROR):
        render_combo_plots(combo, scales, gif_enabled=True)
    assert any("failed to render gif" in r.getMessage() for r in caplog.records)


def test_render_all_plots_isolates_a_failing_combo(
    tmp_path: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    (tmp_path / "01JA").mkdir()
    mocker.patch("plotting.render_combo_plots", side_effect=RuntimeError("combo"))
    stats = _stats_frame([("co", 0.0, 1.0)]).assign(combo_id="01JA")
    with caplog.at_level(logging.ERROR):
        render_all_plots(tmp_path, stats, Plotting(gif_enabled=False))
    assert any("failed to render plots for" in r.getMessage() for r in caplog.records)


def test_bias_plots_skip_fallbacks_and_log_failures(
    tmp_path: Path, mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    realization, baseline = tmp_path / "r", tmp_path / "b"
    realization.mkdir()
    baseline.mkdir()
    render_combo_bias_plots(realization, baseline, {})  # no common files
    assert not (realization / BIAS_PLOTS_DIRNAME).exists()
    _write_pair(realization, baseline, 0.5, hour=1)
    with caplog.at_level(logging.WARNING):
        render_combo_bias_plots(realization, baseline, {})  # no bias scale
    assert any("no bias scale" in r.getMessage() for r in caplog.records)
    scales = {"co": VariableScale(variable="co", vmin=-1.0, vmax=1.0)}
    mocker.patch("plotting.render_bias_plot", side_effect=RuntimeError("png"))
    with caplog.at_level(logging.ERROR):
        render_combo_bias_plots(realization, baseline, scales)
    assert any("failed to render" in r.getMessage() for r in caplog.records)
    caplog.clear()
    mocker.patch("plotting.render_bias_plot")
    mocker.patch("plotting.render_gif", side_effect=RuntimeError("gif"))
    with caplog.at_level(logging.ERROR):
        render_combo_bias_plots(realization, baseline, scales)
    assert any("failed to render bias gif" in r.getMessage() for r in caplog.records)
    caplog.clear()
    mocker.patch("plotting.render_combo_bias_plots", side_effect=RuntimeError("x"))
    frame = pd.DataFrame([{"variable": "co", "max_abs_diff": 0.5, "combo_id": "01JA"}])
    with caplog.at_level(logging.ERROR):
        render_all_bias_plots([(realization, baseline)], frame)
    assert any("failed to render bias plots" in r.getMessage() for r in caplog.records)
