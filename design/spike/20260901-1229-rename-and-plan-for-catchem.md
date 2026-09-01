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

- we need to come up with a better name for the software repository
- i want to expand the test harness to incorporate catchem in addition to cece
- catchem will have its own configuration, container, etc. however, the core test running structure, pre-processing, and post-processing can be generic between the two software packages
- it's also possible that other applications will become part of the harness, so a generic system to handle other configurations will be required
- the purpose of this spike is:
  1. identify a new repository name
  2. scope out what will be required to rename it
  3. scope out what would be required to incorporate catchem in terms of abstractions and generalizations. which design pattern will we use?

# references

- ufs-chem: https://csl.noaa.gov/groups/csl4/modeldata/ufs-chem/
- catchem: /Users/bkoziol/sandbox/git-benkozi/CATChem