"""The CATChem adapter: cloned, built and tested by the CLI; never swept."""

from __future__ import annotations

from typing import ClassVar

from applications.base import ApplicationRunSection, RunConfigView, Stage, TestPlan
from applications.catchem import cli
from applications.catchem.settings import CatchemSettings
from applications.nodriver import NoDriverApplication


class CatchemApplication(NoDriverApplication):
    name = "catchem"
    env_prefix = CatchemSettings.env_prefix
    checkout_dirname = "CATChem"
    settings_model = CatchemSettings
    run_section_model = cli.CatchemRunSection
    stages: ClassVar[frozenset[Stage]] = frozenset(
        {Stage.SOURCE, Stage.BUILD, Stage.TEST}
    )

    def stage_lines(
        self, stage: Stage, config: RunConfigView, section: ApplicationRunSection
    ) -> list[str]:
        return cli.stage_lines(stage, config, section, config.checkout_dir(self))

    def test_plan(self, section: ApplicationRunSection, junit: str) -> TestPlan:
        return cli.test_plan(section, junit)
