# Feature: print CECE driver output after each run

## Goal

Make the driver's output visible in the terminal when running the suite, so
the user can confirm the driver is actually executing — not just producing an
exit code. With `pytest -vs`, each test prints the full captured driver
output immediately after its driver call completes.

## Current behavior

`run_driver()` captures combined stdout/stderr via
`subprocess.check_output(..., stderr=STDOUT)` and writes it to the combo's
`.out` file. Nothing is ever printed, so a passing run shows only pytest's
dots — the driver could be a no-op and the terminal would look identical.

## Design

After each driver call, the runner **prints the captured output to stdout**,
in both the success and failure paths (on failure: write `.out`, print, then
re-raise — same ordering guarantee as the existing `.out` handling). The
capture is decoded as UTF-8 with `errors="replace"` for printing; the `.out`
file keeps the raw bytes.

Each print is framed with a header/footer naming the combo, so interleaved
output from consecutive tests is attributable:

```
----- cece driver output [map-consd] -----
INFO: ...
INFO: CECE Finalize completed successfully
----- end driver output [map-consd] -----
```

### Interaction with pytest capture modes

Printing to stdout deliberately delegates visibility to pytest's own capture
model — no custom flags:

- `pytest -vs` (or `-s` / `--capture=no`): output appears in the terminal
  after each driver call, per the requirement.
- plain `pytest` (capture on): output stays hidden for passing tests and is
  shown automatically in the **"Captured stdout call"** section of each
  failure report — a strict improvement for debugging failed combos.

## Implementation notes

- Single change site: `run_driver()` in `src/runner.py`,
  which already owns both the success and failure paths. The test body stays
  a bare `run_driver(...)` call.
- The combo name for the header is derived from `out_path.stem` (already the
  combo name) rather than adding a parameter.

## Non-goals

- **No live streaming.** Output is printed after the call completes, per the
  requirement ("printed after each cece driver call"). Streaming while the
  driver runs would require replacing `check_output` with a `Popen` loop and
  complicate the `.out`-on-failure guarantee; not worth it until runs are
  long enough that post-hoc output is insufficient.
- No verbosity/quiet flags on the runner itself; pytest's `-s` / capture
  settings are the only switch.

## Acceptance criteria

- `uv run pytest -vs` shows each combo's framed driver output immediately
  after that combo's driver call, for passing and failing runs alike.
- `uv run pytest` (default capture) stays quiet on success; a failing combo's
  report includes the driver output under "Captured stdout call".
- `.out` files are written exactly as before, in all cases.

## Documentation

- README "Running" section: note that `-vs` shows driver output live and
  that failures always include it in the report.
