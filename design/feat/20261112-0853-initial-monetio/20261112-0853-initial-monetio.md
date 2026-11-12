# Feature: initial `monetio` integration — the harness's second `Application` adapter, generalized CLI entry-point and UGRID point-dataset analysis/comparison, and the `aeronet` configuration and suite with S3-staged inputs and baseline roundtrip

Refined 2026-11-12 from the requirements in the appendix and an audit of `ufs-chem-assay` (`src/applications/`, `src/runner.py`, `src/analysis.py`, `src/comparison.py`, `src/staging.py`, `src/cli/`) and the local `monetio` v1 checkout at `/Users/bkoziol/sandbox/git-benkozi/monetio` (`feature/v1-benkozi` at `973f15c`, branched from `bbakernoaa/feature/v1` at `a848333`).

# session

- **Session ID**: `993a1c63-18a3-4a57-b724-b7bbde8656a7`
- **Resume Command**: `agy --resume 993a1c63-18a3-4a57-b724-b7bbde8656a7`

---

## Goal

Bring `monetio` into `ufs-chem-assay` as the second registered `Application` adapter (`application: monetio`), using AERONET (`aeronet`) as its first carried configuration and suite (`src/tests/config/monetio/aeronet/`), running natively on the local machine (`Runtime.NATIVE`) with zero changes required to the `monetio` repository, achieving the full harness roundtrip — S3 input staging (`ufs-chem-assay fetch`), per-combo YAML configuration generation and validation, CLI execution producing NetCDF outputs, structural and attribute assertions, descriptive statistics (`descriptive_stats.csv`), baseline publishing (`publish-baselines`), numerical/categorical baseline comparison (`stats-comparison.csv`), benchmark recording (`benchmarks.csv`), and run reporting (`ufs-chem-assay report`) — and retiring the transitional `CECE_*` legacy fallback on harness-wide `Settings` now that the second adapter has landed, structured into five self-contained commit chunks for a single PR (and without adding a `monetio` integration job to `ufs-chem-assay` CI).

---

## Pre-design audit

1. **State of the local `monetio` checkout (`/Users/bkoziol/sandbox/git-benkozi/monetio`)**:
   - Branch `feature/v1-benkozi` (`973f15c`, `git@github.com:benkozi/monetio.git`, tracking `bbakernoaa/feature/v1` at `a848333`) is the modernized `monetio` v1 architecture (`version = "1.0.0rc0"`, `requires-python = ">=3.11"`).
   - Every reader inherits from `BaseReader` / `PointReader` (`monetio/readers/base.py`) and returns a CF-1.8 / UGRID-1.0 `xarray.Dataset` via `monetio.load(source, files=..., **kwargs)`.
   - `pyproject.toml` declares a Click CLI entry point `monetio = "monetio.cli:cli"` (`monetio/cli.py`), which already provides `monetio load <source> -f <files> -o <output.nc> [-v <var> ...] [-k <key>=<val> ...]`:
     - Tested directly against both AERONET offline fixtures in `monetio/tests/data/`:
       - `monetio load aeronet -f .../aeronet-AOD15-example.txt -o aod15.nc -k product=AOD15 -k freq=1D` exits `0` in ~0.3 s and writes a 113 KB CF-1.8 / UGRID-1.0 NetCDF4 file.
       - `monetio load aeronet -f .../aeronet-inv-ALM15-SIZ-example.txt -o alm15_siz.nc -k product=ALM15 -k inv_type=SIZ` exits `0` in ~0.2 s and writes a 70 KB CF-1.8 / UGRID-1.0 NetCDF4 file.
     - Consequently, **no changes to `monetio` are required** for the initial integration.

2. **Does `monetio` have its own dedicated container? Container vs. native execution locally**:
   - `monetio` does **not** have a dedicated production or test container image:
     - The repository only has a 9-line `Dockerfile-dev` (`FROM continuumio/miniconda3`, installs `tmux vim less`, runs `conda env create --file environment-dev.yml` creating a conda environment `monetio-dev` that is not activated by an `ENTRYPOINT`, has no container build/test script, and is unused by `.github/workflows/ci.yml`, which runs `pytest` directly on the runner host).
   - `monetio` is a pure-Python package whose core and optional dependencies (`dask`, `netCDF4`, `pandas`, `xarray`, `requests`, `h5netcdf`, `h5py`, `pyproj`, `pyarrow`, `fsspec`, `click`, `scipy`) all publish prebuilt macOS (`arm64`/`x86_64`) and Linux wheels.
   - **Decision**: Run `monetio` **directly on the machine (`Runtime.NATIVE`)** via a dedicated `uv`-managed virtual environment inside the `monetio` checkout (`<MONETIO_ROOT_DIR>/.venv`, with `driver_path = ".venv/bin/monetio"`).
     - The `monetio` adapter's `build` stage (`02-build-monetio.sh`) creates/updates `<MONETIO_ROOT_DIR>/.venv` via `uv venv --allow-existing <clone>/.venv && uv pip install --python <clone>/.venv/bin/python -e "<clone>"`.
     - Running natively avoids a Docker daemon dependency for Python readers, builds in seconds from the shared `uv` cache, and uses the exact same virtual-environment layout on a laptop (`config/local.yaml`) and on Ursa (`config/ursa.yaml`).

3. **What `monetio`'s AERONET reader produces in NetCDF (and where `ufs-chem-assay` currently assumes gridded CECE output)**:
   - Inspecting the `xarray.Dataset` written by `monetio load aeronet` for the two offline AERONET files:
     - `aeronet-AOD15-example.txt` (617,589 bytes, SHA-256 `71db20e4b91390cfd0d21af2dd0a490ba341dbb229ae9e6524a3ef1eb7a6031c`): Version 3 Direct Sun Level 1.5 AOD at `Cart_Site` (`2000-06-01` to `2000-06-13`, 562 native timestamps; 13 daily steps when resampled with `freq=1D`).
     - `aeronet-inv-ALM15-SIZ-example.txt` (13,596 bytes, SHA-256 `d943c96f6fdb7e83b5717a7a61008e17b5650d9b483265043a027ad9d933a633`): Version 3 Almucantar Level 1.5 Size Distribution Inversion (`ALM15` / `SIZ`) at `Cart_Site` (`2002-06-01` to `2003-05-10`, 36 timestamps; 12 monthly steps with `freq=30D`).
   - **Dimensions and coordinates (UGRID 2D point layout)**:
     - Dimensions: `("time", "node")` (with `expand2d=True`, the default).
     - Coordinates: `time(time)`, `node(node)`, `latitude(node)` (`units: "degrees_north"`, `standard_name: "latitude"`), `longitude(node)` (`units: "degrees_east"`, `standard_name: "longitude"`), `elevation(node)` (`units: "m"`, `standard_name: "height_above_mean_sea_level"`), `siteid(node)` (string `<U9`).
     - Data variables: numeric float64/int64 observation fields on `("time", "node")` with UGRID attributes `{"mesh": "mesh", "location": "node"}` (e.g. `aod_500nm`, `aod_440nm`, `aod_675nm`, `aod_870nm`, `precipitable_water(cm)`, `440-870_angstrom_exponent`, `solar_zenith_angle(degrees)`, `optical_air_mass`, or size bins `0.050000`..`15.000000`, `inflection_radius_of_size_distribution(um)`), a scalar `int32` dummy topology variable `mesh()` (`cf_role: "mesh_topology"`), and when `freq` is not resampling, string metadata variables (`data_quality_level`, `aeronet_site_name`, `last_date_processed`, or `level`, `retrieval_measurement_scan_type`).
     - Global attributes: `Conventions: "CF-1.8 UGRID-1.0"`, `info: "<header>"`, and **`history: "<YYYY-MM-DD HH:MM:SS>: ..."`** stamped with `datetime.now()` on every load.
   - **Four places in `ufs-chem-assay` that need clean application-generalization**:
     1. **Driver CLI invocation (`src/runner.py` & `src/applications/base.py`)**:
        - Today `runner.driver_command(app_settings, driver_yaml)` is hardcoded to `[app_settings.driver_path, str(driver_yaml)]`. That works only for binaries that take a single positional YAML file (`./build/cece_standalone_driver <combo.yaml>`).
        - Meanwhile, `conftest.py`, `test_driver_combos.py`, `comparison.py`, `benchmarks.py`, and `baselines/publish.py` all rely on `<combo_dir>/<combo_id>.yaml` being written via `app.write_config(config, combo_dir, combo.combo_id)` and loaded via `app.load_config`.
        - **Generalization**: Keep `<combo_dir>/<combo_id>.yaml` as the canonical serialized `DriverConfig` (`MonetioConfig`) for every combo, and add a `driver_arguments(self, driver_yaml: Path, *, runtime_yaml: Path | None = None) -> list[str]` method on `Application` (defaulting to `[str(runtime_yaml or driver_yaml)]`). `CeceApplication` inherits the default unchanged; `MonetioApplication` reads `MonetioConfig` from `driver_yaml` and returns `["load", config.source, "-f", ..., "-o", config.output, "-v", ..., "-k", ...]`.
     2. **Volatile global attributes (`MonetioApplication.volatile_global_attributes`)**:
        - `monetio`'s `BaseReader.log_history()` stamps wall-clock `YYYY-MM-DD HH:MM:SS` lines into the global `history` attribute on every read. Setting `volatile_global_attributes = frozenset({"history"})` on `MonetioApplication` excludes `history` from `comparison.py:_compare_global_attributes` automatically.
     3. **Non-numeric variables in baseline comparisons (`src/comparison.py:_compare_variable`)**:
        - Today `_compare_variable` casts every NetCDF variable (`ds.variables`, which includes coordinates like `siteid` and string columns like `data_quality_level` or `retrieval_measurement_scan_type`) to float via `(real.astype(float) - base.astype(float)).ravel()`. On string variables (`dtype.kind in {"U", "S", "O"}`), `.astype(float)` raises `ValueError: could not convert string to float`.
        - **Generalization**: In `_compare_variable`, after checking shape and dtype, if `not np.issubdtype(real.dtype, np.number)` (e.g. string/object arrays), compare values with exact equality (`np.array_equal(real, base)`), recording `max_abs_diff=0.0, max_rel_diff=0.0, rmse=0.0` on match or `status="failed"` with `max_abs_diff=math.inf` on mismatch.
     4. **Descriptive statistics on UGRID point datasets (`src/analysis.py:spatial_variables`)**:
        - Today `spatial_variables(ds)` selects only data variables where `{"lat", "lon"} <= set(var.dims)`. For UGRID point datasets (`dims = ("time", "node")` or `("node",)`), `spatial_variables(ds)` returns `[]`, producing an empty `descriptive_stats.csv` and failing `test_descriptive_stats` (`assert set(frame["file"]) == {nc_file.name for nc_file in nc_files}`).
        - **Generalization**: Update `spatial_variables(ds)` in `src/analysis.py` to select numeric data variables (`np.issubdtype(var.dtype, np.number)`) whose dimensions include either gridded horizontal axes (`{"lat", "lon"} <= dims`) or the UGRID point/mesh axis (`"node" in dims`). (The scalar `mesh` variable has `dims == ()` and string variables are non-numeric, so both are naturally excluded.)

4. **Per-session `runtime` in `RunConfig` (`src/cli/run_config/sections.py`)**:
   - Currently `SessionOverlay` forbids setting `harness.runtime` inside `sessions.<name>.harness` (`_SESSION_FORBIDDEN_HARNESS_KEYS = ("runtime", "suite_config_search_path")`), because previously all sessions in `config/local.yaml` ran CECE under Docker.
   - Allowing `runtime` on `SessionOverlay` (`sessions.<name>.harness.runtime`) lets `config/local.yaml` keep `runtime: docker` as the default for CECE while a `monetio` session (`sessions.aeronet.harness.runtime: native`) runs natively on the host without requiring a command-line `--override harness:runtime=native`.

---

## Design

### 1. S3 and local dataset staging locations

All private or harness-mirrored suite input datasets in the `s3://ufs-chem` bucket live under a dedicated top-level `inputs/` prefix partitioned by `<application>/<configuration>/` (sibling to `s3://ufs-chem/baselines/` and `s3://ufs-chem/assay-output/`):

| Dataset | Source in `monetio` repo | S3 URI (`url`) | Local staged `dst` (under `<MONETIO_ROOT_DIR>/data/`) | Size | SHA-256 |
|---|---|---|---|---|---|
| AERONET V3 Direct Sun Level 1.5 AOD (`Cart_Site`, June 2000) | `tests/data/aeronet-AOD15-example.txt` | `s3://ufs-chem/inputs/monetio/aeronet/aeronet-aod15-cart-site-200006.txt` | `aeronet/aeronet-aod15-cart-site-200006.txt` | 617,589 B | `71db20e4b91390cfd0d21af2dd0a490ba341dbb229ae9e6524a3ef1eb7a6031c` |
| AERONET V3 Almucantar Level 1.5 Size Distribution Inversion (`Cart_Site`, 2002–2003) | `tests/data/aeronet-inv-ALM15-SIZ-example.txt` | `s3://ufs-chem/inputs/monetio/aeronet/aeronet-alm15-siz-cart-site-200206-200305.txt` | `aeronet/aeronet-alm15-siz-cart-site-200206-200305.txt` | 13,596 B | `d943c96f6fdb7e83b5717a7a61008e17b5650d9b483265043a027ad9d933a633` |

- **Why rename away from `*-example.txt` when staging to S3 and `data/aeronet/`?**
  - `test_user_docs.py::test_user_docs_never_say_example` enforces that `"example"` never appears in user-facing docs or `config/*.yaml`, and self-describing dataset filenames (`<reader>-<product>-<site>-<dates>.txt`) match the harness convention that suites test named configurations rather than "examples".
- **Why `<MONETIO_ROOT_DIR>/data/aeronet/` locally?**
  - `MonetioApplication.data_dirname = "data"` resolves `app.data_dir(checkout)` to `<MONETIO_ROOT_DIR>/data`, keeping staged suite inputs isolated from `monetio`'s git-tracked `tests/data/` tree while matching CECE's `<CECE_ROOT_DIR>/data/<configuration>/` layout.
- **One-time S3 upload command** (run during implementation Verification with AWS credentials for `s3://ufs-chem`):
  ```sh
  aws s3 cp /Users/bkoziol/sandbox/git-benkozi/monetio/tests/data/aeronet-AOD15-example.txt \
    s3://ufs-chem/inputs/monetio/aeronet/aeronet-aod15-cart-site-200006.txt
  aws s3 cp /Users/bkoziol/sandbox/git-benkozi/monetio/tests/data/aeronet-inv-ALM15-SIZ-example.txt \
    s3://ufs-chem/inputs/monetio/aeronet/aeronet-alm15-siz-cart-site-200206-200305.txt
  ```
  After upload, `uv run ufs-chem-assay fetch --suite-config=aeronet-suite.yaml` downloads both files into `<MONETIO_ROOT_DIR>/data/aeronet/` and verifies their SHA-256 digests.

---

### 2. Generalizing `Application` and the harness pipeline

#### 2.1 `Application.driver_arguments` and `src/runner.py`

In `src/applications/base.py`, add `driver_arguments` to `Application`:

```python
def driver_arguments(
    self, driver_yaml: Path, *, runtime_yaml: Path | None = None
) -> list[str]:
    """CLI arguments following `driver_path` to execute `driver_yaml`.

    `driver_yaml` is always the host path on disk (readable when the adapter
    derives CLI flags from the serialized config); `runtime_yaml` is the path
    as seen inside the execution runtime (e.g. `/work/artifacts/...` under
    Docker) when different from `driver_yaml`.
    """
    return [str(runtime_yaml if runtime_yaml is not None else driver_yaml)]
```

In `src/runner.py`:
- Update `driver_command`, `build_command`, `render_job_script`, and `run_driver` to accept an optional `app: Application | None = None` (or delegate to `app.driver_arguments(...)` when `app` is provided, falling back to `[str(runtime_yaml or driver_yaml)]` when `app is None` so existing unit-test callers of `driver_command(app_settings, driver_yaml)` remain backwards-compatible).
- In `src/tests/test_driver_combos.py:_ensure_driver_executed`, pass `app=app` to `run_driver(...)`.

#### 2.2 UGRID point variables in `src/analysis.py`

Update `spatial_variables(ds: xr.Dataset) -> list[str]` in `src/analysis.py`:

```python
def spatial_variables(ds: xr.Dataset) -> list[str]:
    """Variables to summarize: every numeric data variable on `(lat, lon)` or UGRID `node`."""
    fields: list[str] = []
    for name, var in ds.data_vars.items():
        if not np.issubdtype(var.dtype, np.number):
            continue
        dims = set(var.dims)
        if {"lat", "lon"} <= dims or "node" in dims:
            fields.append(str(name))
    return fields
```

- Gridded CECE datasets (`(time, lev, lat, lon)` or `(time, lat, lon)`) behave identically to today.
- UGRID `monetio` point datasets (`(time, node)` or `(node,)`) summarize every numeric observation variable per time slice (and whole-file when timeless), while skipping the scalar `mesh` dummy variable (`dims == ()`) and any non-numeric string columns (`data_quality_level`, `aeronet_site_name`, `last_date_processed`, `level`, `retrieval_measurement_scan_type`).

#### 2.3 Non-numeric variables in `src/comparison.py`

In `src/comparison.py:_compare_variable`, handle non-numeric arrays before the float subtraction path:

```python
if not np.issubdtype(real.dtype, np.number):
    equal = bool(np.array_equal(real, base))
    diff = 0.0 if equal else math.inf
    return VariableComparison(
        file=filename,
        variable=var_name,
        status="identical" if equal else "failed",
        dtype_match=True,
        shape_match=True,
        attributes_match=attrs_match,
        n_nan_realization=0,
        n_nan_baseline=0,
        nan_locations_match=True,
        max_abs_diff=diff,
        max_rel_diff=diff,
        rmse=diff,
    )
```

#### 2.4 Per-session `runtime` override in `src/cli/run_config/`

- In `src/cli/run_config/sections.py`, remove `"runtime"` from `_SESSION_FORBIDDEN_HARNESS_KEYS` (leaving only `("suite_config_search_path",)`), so a session in `sessions:` can set `harness.runtime: native` or `harness.runtime: docker`.
- Add `ResolvedSession.effective_runtime(default: Runtime) -> Runtime` (returning `self.harness.runtime or default`), and use `session.effective_runtime(config.runtime)` in `src/cli/stages.py` when checking whether a session uses Slurm/Docker/Native and when rendering `ASSAY_RUNTIME` into `05-harness-*.sh`.
- Preserve the validation rule that `submit_only: true` requires the effective runtime of that session to be `Runtime.SLURM`.

---

### 3. The `applications/monetio` adapter package

Create `src/applications/monetio/` mirroring `src/applications/cece/`:

```text
src/applications/monetio/
├── __init__.py
├── application.py   # MonetioApplication & MONETIO_APPLICATION singleton
├── cli.py           # MonetioRunSection & source/build Stage lines
├── combos.py        # monetio parameter-sweep mutators & combo generator
├── config.py        # MonetioConfig(DriverConfig) Pydantic model
├── settings.py      # MonetioSettings(AppSettings) with MONETIO_ prefix
└── suite.py         # MonetioSuiteConfig(SuiteConfig)
```

#### 3.1 `MonetioConfig` (`src/applications/monetio/config.py`)

Every generated combo configuration is a strict Pydantic `DriverConfig` model serialized to `<combo_dir>/<combo_id>.yaml`:

```python
ScalarKwarg = str | int | float | bool | None

class MonetioConfig(DriverConfig):
    """Typed schema for a monetio CLI `load` invocation."""

    model_config = ConfigDict(extra="forbid")

    source: str = Field(
        min_length=1,
        description="Registered monetio reader name passed to `monetio load <source>` (e.g. 'aeronet')",
    )
    files: list[str] = Field(
        min_length=1,
        description="Input file path(s) passed as `-f <path>` to `monetio load`",
    )
    output: str = Field(
        min_length=1,
        description="Output NetCDF path passed as `-o <path>` to `monetio load`",
    )
    variables: list[str] = Field(
        default_factory=list,
        description="Optional variable subset passed as `-v <var>` to `monetio load`",
    )
    kwargs: dict[str, ScalarKwarg] = Field(
        default_factory=dict,
        description="Reader keyword arguments passed as `-k key=value` to `monetio load`",
    )
```

- **Path rewriting in `apply_combo`**:
  - Input paths in `base_config.files`: any relative path (or path starting with `${MONETIO_ROOT_DIR}/data/`) is resolved under `data_dir` (`<data_dir>/<rel_path>`).
  - Output path in `config.output`: rewritten to `str(combo_dir / f"{combo.combo_id}.nc")`.
- **Expected outputs**:
  - `expected_output_count(config)` returns `1`.
  - `expected_output_filenames(config)` returns `[Path(config.output).name]`.
  - `output_variable_names(config)` returns `list(config.variables)` (when `variables` is empty, `monetio` loads all reader columns; `test_nc_variable_dimensions` checks any listed `variables` plus the suite's `assertions.species` have dimensions that are a subset of `standard_dimensions`).
  - `standard_dimensions = ("time", "node")`.
  - `volatile_global_attributes = frozenset({"history"})`.

#### 3.2 `MonetioSettings` (`src/applications/monetio/settings.py`)

```python
class MonetioSettings(AppSettings):
    """Settings for the monetio application under test (`MONETIO_*` env vars)."""

    model_config = SettingsConfigDict(
        env_prefix="MONETIO_",
        env_file=CONVENTIONS.env_file,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    root_dir: Path = Field(
        default=Path("../monetio"),
        description="Host path to the monetio repository root",
    )
    docker_image: str = Field(
        default="monetio/monetio-dev",
        description="Docker image containing the monetio environment (docker runtime)",
    )
    driver_path: str = Field(
        default=".venv/bin/monetio",
        description="Path to the monetio CLI executable relative to the checkout root",
    )
    modulefile: str | None = Field(
        default=None,
        description="Optional modulefile to load before running monetio on HPC",
    )

    @property
    def executable_path(self) -> Path:
        return self.root_dir / self.driver_path
```

#### 3.3 `MonetioRunSection` and `source` / `build` stages (`src/applications/monetio/cli.py`)

- `MonetioRunSection(ApplicationRunSection)` declares:
  - `git_url: str = "git@github.com:noaa-oar-arl/monetio.git"`
  - `ref: str = "feature/v1"`
  - `clone_dir: Path | None = None` (defaults to `<root_dir>/monetio` via `checkout_dirname = "monetio"`)
  - `update_source: bool = False`
  - `modulefile: str | None = None`
  - `docker_image: str | None = None`
  - `driver_path: str | None = None`
  - `extras: list[str] = Field(default_factory=list, description="Optional pyproject extras to install into the checkout venv")`
- **Stage `01-source-monetio.sh`**:
  - Clones `git_url` at `ref` into `checkout_dir` when absent; when `update_source: true`, runs `git -C <clone> fetch origin && git -C <clone> checkout <ref> && git -C <clone> pull --ff-only origin <ref>`; logs `git -C <clone> log -1 --oneline`.
- **Stage `02-build-monetio.sh`**:
  - Under `Runtime.NATIVE` or `Runtime.SLURM`: creates/updates `<clone>/.venv` using `uv`:
    ```sh
    uv venv --allow-existing <clone>/.venv
    uv pip install --python <clone>/.venv/bin/python -e <clone>
    <clone>/.venv/bin/monetio --version
    ```
    (with `UV_CACHE_DIR` exported from `config.uv_cache_dir`).

#### 3.4 Parameter sweep & mutators (`src/applications/monetio/combos.py` & `suite.py`)

`MonetioSuiteConfig(SuiteConfig)` pins `application: Literal["monetio"] = "monetio"` and supports sweeping reader configurations across axes in `parameter_sweep`:
- `preset` (or `case`): named reader presets defined in the suite's `presets:` map (each preset can override `files`, `variables`, and `kwargs` on `MonetioConfig`), allowing a single suite to test multiple AERONET products/files (e.g. `aod15` direct-sun AOD and `alm15_siz` size-distribution inversion) across shared sweep axes.
- `freq`: list of resampling frequencies (e.g. `["1D"]` or `["1D", "6h"]`), setting `config.kwargs["freq"]`.
- `expand2d`: list of booleans (`[True]`), setting `config.kwargs["expand2d"]`.
- Combo IDs are deterministic slug tokens built from the active sweep axes (e.g. `case-aod15_freq-1D` and `case-alm15-siz_freq-30D`), matching CECE's readable `combo_id` convention.

---

### 4. The first `monetio` configuration and suite (`aeronet`)

Carried under `src/tests/config/monetio/aeronet/`:

1. **Base configuration (`src/tests/config/monetio/aeronet/aeronet_base.yaml`)**:
   ```yaml
   source: aeronet
   files:
     - aeronet/aeronet-aod15-cart-site-200006.txt
   output: aeronet.nc
   variables: []
   kwargs:
     product: AOD15
     freq: 1D
     expand2d: true
   ```
2. **Suite configuration (`src/tests/config/monetio/aeronet/aeronet-suite.yaml`)**:
   - Declares `application: monetio`, `name: aeronet`, `config_path: aeronet_base.yaml`, `timeout_s: 60`.
   - Declares `inputs:` for both S3-staged AERONET datasets (`s3://ufs-chem/inputs/monetio/aeronet/aeronet-aod15-cart-site-200006.txt` and `s3://ufs-chem/inputs/monetio/aeronet/aeronet-alm15-siz-cart-site-200206-200305.txt`) with `public: false` and their verified SHA-256 digests.
   - Sweeps two combos covering both AERONET direct-sun AOD and inversion reader pathways in `monetio`:
     1. `case-aod15_freq-1D`: loads `aeronet-aod15-cart-site-200006.txt` with `product: AOD15, freq: 1D` (13 daily time steps × 1 site node).
     2. `case-alm15-siz_freq-30D`: loads `aeronet-alm15-siz-cart-site-200206-200305.txt` with `product: ALM15, inv_type: SIZ, freq: 30D` (12 resampled time steps × 1 site node).
   - Declares `assertions:` with `expected_nc_count: 1`, `check_variable_dimensions: true`, and coordinate/variable attribute assertions (`latitude` `units: degrees_north`, `longitude` `units: degrees_east`, `aod_500nm` `location: node` for AOD or shared coordinate/UGRID attributes).
   - Sets `analysis: { compute_descriptive_stats: true }` and `plotting: { enabled: false }` (since `plotting.py` renders 2D `(lat, lon)` `pcolormesh` maps via Cartopy, whereas AERONET is a single-site UGRID `(time, node)` point dataset).

---

### 5. Run-config template integration (`config/local.yaml`)

- Keep `config/local.yaml`'s default `applications.cece` and `harness.suite_config: simple-maccity-suite.yaml` unchanged so existing default invocations (`ufs-chem-assay run --config-file=config/local.yaml`) and CI tests behave identically, or document/include a commented `monetio:` application block and `aeronet:` session overlay in `config/local.yaml` (or an active `monetio:` entry if desired — see Open Questions).
- Specifically, in `config/local.yaml` (and `test_run_config.py` / `test_user_docs.py`):
  - Adding `monetio` under `applications:` in `config/local.yaml` (`git_url: git@github.com:benkozi/monetio.git`, `ref: feature/v1-benkozi`, `clone_dir: null`, `update_source: false`, `modulefile: null`, `docker_image: null`, `driver_path: null`, `extras: []`) does **not** run `monetio` stages unless a `monetio` suite is selected, because `stages.py:active_applications(config)` filters `config.applications` to only the applications referenced by the selected suites!
  - Thus `ufs-chem-assay run --config-file=config/local.yaml` continues to run only `cece` (`simple-maccity-suite.yaml`), while:
    ```sh
    ufs-chem-assay run --config-file=config/local.yaml \
      --override harness:suite_config=aeronet-suite.yaml \
      --override harness:runtime=native
    ```
    runs the full `monetio` pipeline (`01-source-monetio.sh`, `02-build-monetio.sh`, `03-harness-env-monetio.sh`, `04-data-monetio.sh`, `05-harness-monetio.sh`) against `/Users/bkoziol/sandbox/git-benkozi/monetio`!

---

## Implementation plan (TDD, red → green → refactor)

The work is divided into **5 self-contained commit chunks** that fit cleanly in a single PR on `ufs-chem-assay` (with zero commits needed in `monetio`):

- [x] **Chunk 1**: Generalize `Application.driver_arguments`, `analysis.py` (UGRID `node`), `comparison.py` (non-numeric variables), and per-session `runtime`
- [x] **Chunk 2**: Implement the `applications/monetio` adapter package and register `monetio`
- [ ] **Chunk 3**: Add the `aeronet` configuration and suite (`src/tests/config/monetio/aeronet/`) and `config/local.yaml` entry
- [ ] **Chunk 4**: Stage AERONET inputs to `s3://ufs-chem`, run live local roundtrip (fetch, execute, publish baselines, compare, benchmark, report), and update `README.md` & `design/design.md`
- [ ] **Chunk 5**: Retire the transitional `CECE_*` fallback on harness-wide `Settings` (`ASSAY_*` only)

### Chunk 1: Generalize `Application.driver_arguments`, `analysis.py` (UGRID `node`), `comparison.py` (non-numeric variables), and per-session `runtime`
1. **Red**:
   - In `src/tests/ufs_chem_assay/test_runner.py`, add tests for `Application.driver_arguments` custom flag expansion in both `Runtime.NATIVE` and `Runtime.DOCKER`.
   - In `src/tests/ufs_chem_assay/test_analysis.py`, add tests for `spatial_variables` and `compute_file_stats` on a CF-1.8 / UGRID-1.0 `(time, node)` dataset containing numeric observation variables, a scalar `mesh` variable, and string variables (`siteid`, `data_quality_level`).
   - In `src/tests/ufs_chem_assay/test_comparison.py`, add tests for `compare_combo` when the NetCDF files contain string coordinates/variables (`<U9`) — both matching (`identical`) and differing (`failed`).
   - In `src/tests/ufs_chem_assay/test_run_config.py` and `test_stages.py`, add tests for `sessions.<name>.harness.runtime` overriding the top-level `runtime`.
2. **Green**:
   - Implement `Application.driver_arguments` in `src/applications/base.py` and wire `src/runner.py` and `src/tests/test_driver_combos.py`.
   - Update `spatial_variables` in `src/analysis.py`.
   - Update `_compare_variable` in `src/comparison.py` for non-numeric dtypes.
   - Allow `runtime` in `SessionOverlay` (`src/cli/run_config/sections.py`, `config.py`, `src/cli/stages.py`).
3. **Refactor & verify**:
   - Run `uv run pytest src/tests/ufs_chem_assay --cov` (100% coverage).

### Chunk 2: Implement the `applications/monetio` adapter package and register `monetio`
1. **Red**:
   - Create `src/tests/ufs_chem_assay/applications/test_monetio.py` testing:
     - `MonetioConfig` validation, YAML roundtrip, and `apply_combo` path rewriting (`files` under `data_dir`, `output` under `combo_dir`).
     - `MonetioSettings` defaults (`env_prefix == "MONETIO_"`, `driver_path == ".venv/bin/monetio"`, `executable_path`).
     - `MonetioSuiteConfig` validation, presets + parameter-sweep combo generation, and deterministic `combo_id` formatting.
     - `MonetioApplication.driver_arguments` translating a serialized `MonetioConfig` YAML into `["load", source, "-f", ..., "-o", ..., "-v", ..., "-k", ...]`.
     - `MonetioApplication.stage_lines` for `Stage.SOURCE` and `Stage.BUILD` (creating `<clone>/.venv` and installing `-e <clone>` via `uv`).
   - Update `src/tests/ufs_chem_assay/applications/test_registry.py` and `test_import_graph.py` to include `"monetio"` alongside `"cece"`.
2. **Green**:
   - Implement `src/applications/monetio/{__init__,config,settings,suite,combos,cli,application}.py`.
   - Register `MONETIO_APPLICATION` in `src/applications/registry.py`.
3. **Refactor & verify**:
   - Run `uv run pytest src/tests/ufs_chem_assay --cov` (100% coverage).

### Chunk 3: Add the `aeronet` configuration and suite (`src/tests/config/monetio/aeronet/`) and `config/local.yaml` entry
1. **Red**:
   - Add suite-loading and `--dry-run` tests in `src/tests/ufs_chem_assay/applications/test_monetio.py` and `test_suite_config.py` verifying that `aeronet-suite.yaml` loads via `load_suite`, resolves `aeronet_base.yaml`, generates the expected combos (`case-aod15_freq-1D` and `case-alm15-siz_freq-30D`), and passes `pytest src/tests/test_driver_combos.py --suite-config=aeronet-suite.yaml --dry-run`.
   - Update `test_run_config.py` and `test_user_docs.py` for `applications.monetio` in `config/local.yaml`.
2. **Green**:
   - Create `src/tests/config/monetio/aeronet/aeronet_base.yaml` and `src/tests/config/monetio/aeronet/aeronet-suite.yaml`.
   - Add `applications.monetio` to `config/local.yaml` (inactive by default until a `monetio` suite or session is selected).
3. **Refactor & verify**:
   - Run `uv run pytest src/tests/ufs_chem_assay --cov` and `uv run pytest src/tests/test_driver_combos.py --suite-config=aeronet-suite.yaml --dry-run`.

### Chunk 4: Stage AERONET inputs to `s3://ufs-chem`, run live local roundtrip (fetch, execute, publish baselines, compare, benchmark, report), and update `README.md` & `design/design.md`
1. **Live S3 & local roundtrip execution**:
   - Upload the two AERONET datasets from `/Users/bkoziol/sandbox/git-benkozi/monetio/tests/data/` to `s3://ufs-chem/inputs/monetio/aeronet/`.
   - Run `ufs-chem-assay run --config-file=config/local.yaml --override harness:suite_config=aeronet-suite.yaml --override harness:runtime=native` to build `/Users/bkoziol/sandbox/git-benkozi/monetio/.venv`, stage inputs via `fetch` into `/Users/bkoziol/sandbox/git-benkozi/monetio/data/aeronet/`, execute both combos, compute `descriptive_stats.csv`, and verify all assertions pass.
   - Publish initial baselines to `s3://ufs-chem/baselines/` (`--publish-baselines`), recording the baseline ULID in `aeronet-suite.yaml`, then re-run with `baselines:enabled=true` and `--record-benchmarks` to verify `test_baseline_comparison`, `stats-comparison.csv`, `benchmarks.csv`, and `ufs-chem-assay report`.
2. **Documentation**:
   - Update `README.md` and `design/design.md` to document the `monetio` adapter (`MONETIO_*` env vars, `MonetioConfig`, UGRID point dataset support, and the `aeronet` suite).
   - Run full `pre-commit run --all-files` and `uv run pytest src/tests/ufs_chem_assay --cov`.

### Chunk 5: Retire the transitional `CECE_*` fallback on harness-wide `Settings` (`ASSAY_*` only)
1. **Red**:
   - Update `src/tests/ufs_chem_assay/test_conventions.py`, `test_settings.py`, `test_platforms.py`, and `conftest.py` to assert that `Conventions` has no `legacy_env_prefix` or `env_var(..., legacy=True)` parameter and that `Settings` reads only `ASSAY_*` environment variables (while `CeceSettings` continues to read `CECE_*`).
2. **Green**:
   - Remove `legacy_env_prefix` and the `legacy` keyword argument on `Conventions.env_var` in `src/models/conventions.py`.
   - Remove `settings_customise_sources` (the `legacy_env_prefix` fallback sources) from `Settings` in `src/settings.py`.
   - Remove the `CONVENTIONS.env_var("log_level", legacy=True)` fallback from `src/cli/main.py`.
   - Update `README.md` and `design/design.md` to remove mentions of the retired `CECE_*` fallback on harness-wide settings.
3. **Refactor & verify**:
   - Run `uv run pytest src/tests/ufs_chem_assay --cov` (100% coverage) and `uv run pre-commit run --all-files`.

---

## Verification

- `uv run pytest src/tests/ufs_chem_assay --cov` passes with 100% production coverage.
- Every carried suite (`simple-maccity-suite.yaml`, `exhaustive-maccity-asserted-suite.yaml`, `exhaustive-maccity-run-only-suite.yaml`, `gcafs-anthro-suite.yaml`, `aeronet-suite.yaml`) passes `uv run pytest src/tests/test_driver_combos.py --suite-config=<suite> --dry-run`.
- Live local `monetio` run against `/Users/bkoziol/sandbox/git-benkozi/monetio` (`Runtime.NATIVE`) passes all stages (`fetch`, `test_driver_combos.py`, `publish-baselines`, baseline comparison, `record-benchmarks`, `report`).
- Live local `simple-maccity-suite.yaml` integration run without `--dry-run` continues to pass.
- `uv run pre-commit run --all-files` passes (ruff, mypy, vulture, actionlint, shellcheck, etc.).

---

## Risks and notes

- **Wall-clock timestamps in `monetio` global `history` attribute**: `monetio`'s `BaseReader.log_history()` prepends `datetime.now().strftime("%Y-%m-%d %H:%M:%S")` on every read. Declaring `volatile_global_attributes = frozenset({"history"})` on `MonetioApplication` prevents false-positive global-attribute failures during baseline comparisons while still checking `Conventions` and `info`.
- **String coordinates and variables in AERONET NetCDF files**: When reopened with `xr.open_dataset(..., decode_times=False)`, `siteid` (and un-resampled string columns like `data_quality_level`) have Unicode string dtypes (`<U9`, `<U5`). Handling `not np.issubdtype(real.dtype, np.number)` in `comparison.py:_compare_variable` and `analysis.py:spatial_variables` fixes this cleanly for all point and gridded readers alike.
- **No CI workflow changes**: Per requirements, `.github/workflows/ci.yaml` and `.github/workflows/integration.yaml` are not modified to run a live `monetio` integration job; `src/tests/ufs_chem_assay/` unit tests test the `monetio` adapter hermetic to the repo.

---

## Open questions (resolved)

1. **S3 prefix for `monetio` AERONET input datasets**:
   - *Resolved*: Use `s3://ufs-chem/inputs/monetio/aeronet/` with filenames `aeronet-aod15-cart-site-200006.txt` and `aeronet-alm15-siz-cart-site-200206-200305.txt`, staged locally to `<MONETIO_ROOT_DIR>/data/aeronet/`.
2. **Representation of `monetio` in `config/local.yaml`**:
   - *Resolved*: Include `monetio` under `applications:` in `config/local.yaml` (`git_url: git@github.com:benkozi/monetio.git`, `ref: feature/v1-benkozi`, `update_source: false`), remaining inactive by default until a `monetio` suite or session is selected.
3. **Retiring the legacy `CECE_*` fallback on harness-wide `Settings` (`src/settings.py`)**:
   - *Resolved*: Retire the transitional `CECE_*` env-var fallback on harness-wide `Settings` (`ASSAY_*`) as a 5th commit chunk in this PR (`CeceSettings` retains its own `CECE_*` prefix).

---

## Future work

- Add offline suites for additional `monetio` v1 readers (e.g., `airnow`, `ish`, `icap_mme`, `cems`) using the same `MonetioConfig` / `presets` mechanism.
- Add optional time-series / station-scatter plotting in `src/plotting.py` for UGRID `(time, node)` point datasets when `plotting.enabled: true`.
- Add a GitHub Actions integration job for `monetio` (`aeronet-suite.yaml` under `Runtime.NATIVE`) once `s3://ufs-chem` OIDC credentials or public input mirrors are configured in CI.

---

# appendix: original notes

## always do

- include updating design.md as part of the implementation
- update the harness tests (`src/tests/ufs_chem_assay`) in addition to any changes to test_driver_combos.py
- update README.md with any necessary documentation changes in case of an api adjustment
- use pydantic models as opposed to dataclasses
  - all pydantic fields should include a description like `... = Field(description="<description content here>", ...`
- do *not* add driver bugs to known bugs in `README.md` unless explicitly told to do so
- use a test-driven development, red-green-refactor approach for all fixes and features (when possible)
- maintain original design sections when refining design docs - create an appendix
  - summarize conversational updates in the appendix following original refinement target
  - refinements never create files. they only edit the target document that is being refined.
  - `design.md` may be updated during the _implementation_ phase only.
- when using python `typing`, avoid `Any` as much as possible
- **never, ever, ever** commit code or use git actions that perform updates - the user always updates
- always prefer the harness's logging implementation over raw python print statements
- no need for wrapping when writing to the `./design` folder. wrapping will be handled by the user's ide
- persist the agy session id and resume command in any design/refined files for resumability

## testing

- not necessary for design documents in the `spike` folder - code *should not* change for spikes
- *all* suites should pass `--dry-run`
- run `simple-maccity-suite.yaml` without `--dry-run` for integration testing with the driver
- only run examples when requested to do so
- no need to run tests for spikes/documentation-only tasks
- pre-commit hooks pass

## requirements

- we want to bring in an initial monetio integration
- use the aeronet test as the first configuration
  - we'll want to stage the downloaded aeronet data in the s3 bucket
  - what will be the appropriate location for the datasets?
- location of monetio branch locally is ~/sandbox/git-benkozi/monetio
- the test harness needs to run locally. i'll let you initially decide if it's better to run in a container or directly on the machine
  - does monetio have its own dedicated container?
- this is an opportunity to look into how we are generalizing for applications as the monetio test entry point is different from the yaml configuration driver for cece
- the goal is have a similar roundtrip for baselines, etc.
- right now, do not consider or add a monetio integration test to the ufs-chem-assay ci
- changes to monetio are okay if absolutely necessary
- we'll want to break the work into reasonable chunks that i can commit separately. separate prs are okay if needed, but i'm hoping to keep this scoped enough to avoid that

## conversational updates

- Audited `/Users/bkoziol/sandbox/git-benkozi/monetio` (`feature/v1-benkozi` at `973f15c`): confirmed `monetio` has no dedicated test/production container (only a minimal 9-line conda `Dockerfile-dev` unused by CI), is pure Python, and already ships a Click CLI (`monetio load <source> -f <files> -o <output.nc> -v ... -k ...`) that reads offline AERONET files and writes CF-1.8 / UGRID-1.0 NetCDF files out of the box with zero modifications to `monetio`.
- Selected `Runtime.NATIVE` locally via `<MONETIO_ROOT_DIR>/.venv/bin/monetio` managed by `uv` in the adapter's `build` stage.
- Confirmed open questions with the user:
  1. S3 staging prefix: `s3://ufs-chem/inputs/monetio/aeronet/` (staged locally to `<MONETIO_ROOT_DIR>/data/aeronet/`).
  2. `config/local.yaml`: include `monetio` under `applications:` (inactive by default until a `monetio` suite or session is selected).
  3. Retire the transitional `CECE_*` env-var fallback on harness-wide `Settings` (`ASSAY_*` only) as a 5th commit chunk in this PR.
- Identified the four harness generalizations needed for `monetio` and UGRID point datasets (`Application.driver_arguments`, `analysis.py:spatial_variables` for `node` dimensions, `comparison.py:_compare_variable` for non-numeric string variables, and per-session `runtime` in `RunConfig`), and structured the implementation plan into 5 independently committable TDD chunks within a single PR.
