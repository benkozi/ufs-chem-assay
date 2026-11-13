# always do

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

# session

- **Session ID**: `<session-id>`
- **Resume Command**: `agy --resume <session-id>`

## testing

- not necessary for design documents in the `spike` folder - code *should not* change for spikes
- *all* suites should pass `--dry-run`
- run `simple-maccity-suite.yaml` without `--dry-run` for integration testing with the driver
- only run examples when requested to do so
- no need to run tests for spikes/documentation-only tasks
- pre-commit hooks pass

# requirements

- refine from https://github.com/benkozi/ufs-chem-assay/issues/16
- also wrap in https://github.com/benkozi/ufs-chem-assay/issues/9
  - we want to also run CATChem tests obviously
- scripts are here: design/feat/20261113-1348-build-test-kokkos/test-kokkos
- how do we wrap this into the harness?
- how can we leverage the current platform configurations?
- goal will be not to duplicate infrastructure if possible.
  - formalizing compilation will be a thing - login v compute node
  - initially we'll support ursa but plan to also test on gaea c6 and c7 which will have different modulefile locations
- no need to include upload-logs.sh at this point
- let's make the refinement iterative for sure, so ask questions and pose new ideas. i think a lot of code in the scripts is duplicative for what we're doing in the assay harness