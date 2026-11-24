"""The kokkos-smoke adapter: a project shipped with the harness, built and
run by the CLI against a module environment; no source, no driver suite."""

from __future__ import annotations

from applications.base import ApplicationRunSection, RunConfigView, Stage, TestPlan
from applications.kokkos_smoke import cli
from applications.kokkos_smoke.settings import KokkosSmokeSettings
from applications.nodriver import NoDriverApplication


class KokkosSmokeApplication(NoDriverApplication):
    name = "kokkos-smoke"
    env_prefix = KokkosSmokeSettings.env_prefix
    checkout_dirname = "kokkos-smoke"  # holds only build/: nothing is cloned
    settings_model = KokkosSmokeSettings
    run_section_model = cli.KokkosSmokeRunSection

    def stage_lines(
        self, stage: Stage, config: RunConfigView, section: ApplicationRunSection
    ) -> list[str]:
        return cli.stage_lines(stage, config, section, config.checkout_dir(self))

    def test_plan(self, section: ApplicationRunSection, junit: str) -> TestPlan:
        return cli.test_plan(section, junit)
