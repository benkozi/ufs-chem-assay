"""The import rules that keep the layers one-directional, each checked in a
subprocess so nothing another test imported can mask it:

- `applications.base` imports no `cli` module (the stage enum and the run
  config view an adapter reads are declared in base, and the `cli` modules
  import base);
- `cli.run_config` never imports the application registry (the registry
  imports the adapters, which import the `cli` modules): loading a run config
  and resolving a checkout with an injected adapter pulls in neither.
"""

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


def test_base_imports_nothing_from_cli() -> None:
    probe = (
        "import json, sys\n"
        "import applications.base\n"
        "print(json.dumps(sorted(m for m in sys.modules "
        "if m == 'cli' or m.startswith(('cli.', 'applications.registry', 'applications.cece')))))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        check=True,
        capture_output=True,
        text=True,
        cwd=_SRC,
        env={"PYTHONPATH": str(_SRC), "PATH": ""},
    )
    assert json.loads(result.stdout) == []
