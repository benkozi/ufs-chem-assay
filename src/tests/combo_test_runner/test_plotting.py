from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import xarray as xr
from PIL import GifImagePlugin, Image
from pydantic import ValidationError

from models.suite_config import Plotting, SuiteConfig
from plotting import (
    VariableScale,
    derive_scales,
    render_all_plots,
    render_combo_plots,
    render_spatial_plot,
)


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


@pytest.fixture()
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


def _stats_frame(rows: list[tuple[str, float, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"variable": variable, "min": vmin, "max": vmax}
            for variable, vmin, vmax in rows
        ]
    )


def test_derive_scales_exact_suite_wide_min_max() -> None:
    stats = _stats_frame(
        [("co", 0.2, 0.9), ("co", 0.1, 0.5), ("co", 0.3, 2.9), ("nox", -1.0, 1.0)]
    )
    scales = derive_scales(stats)
    assert scales["co"] == VariableScale(variable="co", vmin=0.1, vmax=2.9)
    assert scales["nox"] == VariableScale(variable="nox", vmin=-1.0, vmax=1.0)


def test_derive_scales_guards_degenerate_range() -> None:
    scales = derive_scales(_stats_frame([("co", 0.0, 0.0)]))
    assert scales["co"].vmin < scales["co"].vmax  # all-zero field still renders


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


def test_render_combo_plots_pngs_and_gif(
    combo_dirs: tuple[Path, np.ndarray, np.ndarray],
) -> None:
    root, first, _ = combo_dirs
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
    render_all_plots(root, stats, gif_enabled=True)
    assert (root / "3f9a1c2b7d4e8a01" / "plots-overview" / "co.gif").is_file()
    assert (root / "9004a4e23c1dd90a" / "plots-overview" / "co.gif").is_file()


def test_plotting_defaults_and_unknown_key() -> None:
    assert Plotting() == Plotting(enabled=True, gif_enabled=True)
    with pytest.raises(ValidationError):
        Plotting(gif_speed=1)  # type: ignore[call-arg]


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
        SuiteConfig.from_yaml(suite_file)


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
    suite = SuiteConfig.from_yaml(suite_file)
    assert suite.plotting.enabled is False


def _write_pair(realization: Path, baseline: Path, offset: float, hour: int) -> None:
    rng = np.random.default_rng(3)
    base_values = rng.random((1, 4, 5))
    name = f"cece_201001{hour:02d}_010000.nc"
    _write_nc(realization / name, base_values + offset, hour=1)
    _write_nc(baseline / name, base_values, hour=1)


def test_derive_bias_scales_symmetric_suite_wide() -> None:
    from plotting import derive_bias_scales

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


def test_derive_bias_scales_guards_zero_bias() -> None:
    from plotting import derive_bias_scales

    scales = derive_bias_scales(pd.DataFrame([{"variable": "co", "max_abs_diff": 0.0}]))
    assert scales["co"].vmin < 0 < scales["co"].vmax  # flat bias still renders


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
    assert len(pngs) == 2 and all(p.stat().st_size > 0 for p in pngs)
    gif = plots / "co.gif"
    assert gif.is_file()
    with Image.open(gif) as image:
        assert isinstance(image, GifImagePlugin.GifImageFile)
        assert image.n_frames == 2  # gif always accompanies the bias plots
