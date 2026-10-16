"""The import rule that keeps `cli.*` <- `applications.cece.*` one-directional:
`cli.run_config` never imports the application registry (the registry imports
the adapters, which import the `cli` modules). Loading a run config and
resolving an application's checkout with an injected adapter must not pull
the registry or any adapter into the interpreter. Checked in a subprocess, so
nothing another test imported can mask it."""

import json
import subprocess
import sys
from pathlib import Path

from tests.ufs_chem_assay.run_configs import run_config_file

_SRC = Path(__file__).resolve().parents[2]

_PROBE = """
import json, sys
from pathlib import Path
from cli.run_config import RunConfig
from tests.ufs_chem_assay.stubs import stub_application

stub = stub_application()
config = RunConfig.from_yaml(Path(sys.argv[1]), resolve_application=lambda _name: stub)
assert config.checkout_dir(stub) == config.root_dir / "STUB", config.checkout_dir(stub)
loaded = sorted(
    name
    for name in sys.modules
    if name == "applications.registry" or name.startswith("applications.cece")
)
print(json.dumps(loaded))
"""


def test_run_config_never_loads_the_registry(tmp_path: Path) -> None:
    path = run_config_file(
        tmp_path,
        template="local.yaml",
        overrides={"applications": {"stub": {"git_url": "u", "ref": "main"}}},
    )
    result = subprocess.run(
        [sys.executable, "-c", _PROBE, str(path)],
        check=True,
        capture_output=True,
        text=True,
        cwd=_SRC,
        env={"PYTHONPATH": str(_SRC), "PATH": ""},
    )
    assert json.loads(result.stdout) == []
