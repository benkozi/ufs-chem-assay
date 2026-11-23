# ops/env.sh: the shared environment of ops/assay-run.sh and ops/assay-transfer.sh.
# Edit the values here. Each is a default: an exported variable wins, so
#   export ASSAY_ROOT=/path/to/a/fresh/root
# points both scripts at a new root, and ASSAY_STAGES="build test" ops/assay-run.sh
# changes one value for one run. Variables only (no set -e, no module calls, no
# output), so an interactive shell can source it too.

: "${ASSAY_HARNESS:=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}" # this checkout
: "${ASSAY_ROOT:=/gpfs/f7/epic/proj-shared/Benjamin.Koziol/sandbox/work-ufs-chem-assay}"
: "${ASSAY_CONFIG:=config/gaea-c7.yaml}" # relative to ASSAY_HARNESS
: "${ASSAY_APPLICATIONS:=all}"           # one --application each
: "${ASSAY_STAGES:=source build test}"   # one --stage each
: "${ASSAY_TRANSFER_REPO:=$HOME/l/c7-epic-proj-shared/git/benkozi-work/transfer}"

# uv's cache and Python live beside the roots, not in one: a fresh root per test
# would download them again every run, and removing a root would remove the uv
# that runs the harness.
export UV_CACHE_DIR="${UV_CACHE_DIR:-/gpfs/f7/epic/proj-shared/Benjamin.Koziol/sandbox/uv-cache}"
export UV_PYTHON_INSTALL_DIR="${UV_PYTHON_INSTALL_DIR:-/gpfs/f7/epic/proj-shared/Benjamin.Koziol/sandbox/uv-python}"
case ":$PATH:" in
  *":$HOME/.local/bin:"*) ;;
  *) export PATH="$HOME/.local/bin:$PATH" ;; # uv
esac
