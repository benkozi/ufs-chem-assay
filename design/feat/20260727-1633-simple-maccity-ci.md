# always do

- include updating design.md as part of the implementation
- update combo-test-runner tests in addition to any changes to test_driver_combos.py
- update README.md with any necessary documentation changes in case of an api adjustment
- use pydantic models as opposed to dataclasses
  - all pydantic fields should include a description like `... = Field(description="<description content here>", ...`
- do *not* add driver bugs to known bugs in `README.md` unless explicitly told to do so
- use a test-driven development, red-green-refactor approach for all fixes and features (when possible)
- maintain original design sections when refining design docs - create an appendix
  - summarize conversational updates in the appendix following original refinement target
- when using python `typing`, avoid `Any` as much as possible
- **never, ever, ever** commit code - the user always commits

## testing

- not necessary for design documents in the `spike` folder - code *should not* change for spikes
- *all* suites should pass `--dry-run`
- run `simple-maccity-suite.yaml` without `--dry-run` for integration testing with the driver
- only run examples when requested to do so
- no need to run tests for spikes/documentation-only tasks
- pre-commit hooks pass

# requirements

- add a ci job that runs the simple-maccity-suite.yaml
- it should:
  - clone cece's feature/helm branch
  - build the cece container (cache it)
  - build cece in the container (cache the cece build)
  - run the simple-maccity-suite.yaml
    - external data for the example will need to be downloaded. identify which example data is needed and download it.
- ci job is allowed to fail
- ci job is run on pull requests to develop
- ci job should fail but before merge we will change to allow to fail before merge develop. note in design.
- store output cece artifacts from the combo-test-runner
- update combo-test-runner to store the current cece commit sha in the output run.yaml