"""`python -m cli` — the console-script-free way to run the entrypoint."""

from cli.main import main

raise SystemExit(main())
