"""What a correct CECE run of a generated config writes: file count and
names from the driver's time settings and output pattern, and the
configured output variables with their standard dimensions."""

from __future__ import annotations

from datetime import datetime, timedelta

from applications.cece.config import CeceConfig
from assertions import render_filename_pattern
from logs import get_logger

logger = get_logger("cece.assertions")

# The driver's standard output layout: every output variable is 4-D on
# exactly these named dimensions.
STANDARD_DIMENSIONS = ("time", "lev", "lat", "lon")


def expected_output_count(config: CeceConfig) -> int:
    """Expected NetCDF output file count from the generated combo config:
    one file per output.frequency_steps timesteps; 0 when output is disabled
    or absent."""
    if config.output is None or not config.output.enabled:
        return 0
    start = datetime.fromisoformat(config.driver.start_time)
    end = datetime.fromisoformat(config.driver.end_time)
    n_steps = int((end - start).total_seconds()) // config.driver.timestep_seconds
    logger.info(
        "deriving expected_nc_file_count: timestep_seconds=%s n_steps=%s frequency_steps=%s",
        config.driver.timestep_seconds,
        n_steps,
        config.output.frequency_steps,
    )
    return n_steps // config.output.frequency_steps


def expected_output_filenames(config: CeceConfig) -> set[str]:
    """Expected NetCDF filenames: filename_pattern rendered at each write
    time, the first at start_time + frequency_steps * timestep_seconds (the
    end of the first output interval, not t=0)."""
    if config.output is None or not config.output.enabled:
        return set()
    count = expected_output_count(config)
    start = datetime.fromisoformat(config.driver.start_time)
    interval = timedelta(
        seconds=config.output.frequency_steps * config.driver.timestep_seconds
    )
    return {
        render_filename_pattern(config.output.filename_pattern, start + k * interval)
        for k in range(1, count + 1)
    }


def output_variable_names(config: CeceConfig) -> list[str]:
    """Configured output variable names (a fields entry is a plain string or
    an OutputField map); none when output is disabled or absent."""
    if config.output is None or not config.output.enabled:
        return []
    return [
        field if isinstance(field, str) else field.name
        for field in config.output.fields
    ]
