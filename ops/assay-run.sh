#!/usr/bin/env bash
# ops/assay-run.sh: run the harness's stages under one root, then watch what
# they submitted.
#
#   ops/assay-run.sh [<more run arguments>]   e.g. --toolchain oneapi, --build musica
#
# The values come from ops/env.sh (an exported variable wins). Everything is
# shown and written to <root>/logs/assay-run-<YYYYmmdd-HHMMSS>.log. The exit
# status is run's when it failed, else watch's.
set -euo pipefail
# shellcheck source=ops/env.sh
source "$(dirname "${BASH_SOURCE[0]}")/env.sh"

_module_reset() {
  # The stage scripts reset their own modules; this one is for uv and git in
  # this shell, and records what the shell carried in.
  set +u # module init scripts read unset variables
  if [ -n "${MODULESHOME:-}" ] && [ -f "$MODULESHOME/init/bash" ]; then
    # shellcheck source=/dev/null
    source "$MODULESHOME/init/bash"
  fi
  if command -v module > /dev/null 2>&1; then
    module reset
  else
    echo "assay-run: no module command; no module reset"
  fi
  set -u
  echo "LD_LIBRARY_PATH=[${LD_LIBRARY_PATH:-}]"
  echo "PYTHONPATH=[${PYTHONPATH:-}]"
}

_main() {
  if [ -z "$ASSAY_STAGES" ] || [ -z "$ASSAY_APPLICATIONS" ]; then
    echo "assay-run: ASSAY_STAGES and ASSAY_APPLICATIONS must name at least one each" >&2
    return 2
  fi
  local stage_args=() app_args=() stage app
  for stage in $ASSAY_STAGES; do stage_args+=(--stage "$stage"); done
  for app in $ASSAY_APPLICATIONS; do app_args+=("$app"); done

  echo "assay-run: $(date -u +%Y-%m-%dT%H:%M:%SZ) on $(hostname)"
  echo "ASSAY_ROOT=$ASSAY_ROOT"
  echo "ASSAY_HARNESS=$ASSAY_HARNESS"
  echo "ASSAY_CONFIG=$ASSAY_CONFIG ASSAY_APPLICATIONS=$ASSAY_APPLICATIONS ASSAY_STAGES=$ASSAY_STAGES"
  _module_reset

  cd "$ASSAY_HARNESS"
  git pull
  echo "harness at $(git rev-parse --short HEAD 2> /dev/null || echo unknown)"

  local run_status=0 watch_status=0 watch_text
  uv run ufs-chem-assay run --config-file="$ASSAY_CONFIG" \
    --application "${app_args[@]}" --root-dir "$ASSAY_ROOT" \
    "${stage_args[@]}" "$@" || run_status=$?

  # watch whatever run returned: a failed build still leaves the other builds'
  # test jobs submitted, and watch evaluates and reports them.
  case " $ASSAY_STAGES " in
    *" test "* | *" harness "* | *" resume "*)
      if [ -f "$ASSAY_ROOT/scripts/run-config.yaml" ]; then
        uv run ufs-chem-assay watch --root-dir "$ASSAY_ROOT" || watch_status=$?
        watch_text="watch exit $watch_status"
      else
        echo "assay-run: nothing to watch: run wrote no $ASSAY_ROOT/scripts/run-config.yaml"
        watch_text="watch skipped"
      fi
      ;;
    *) watch_text="watch skipped; no test, harness or resume stage" ;;
  esac

  echo "assay-run: run exit $run_status, $watch_text; transcript $transcript"
  if [ "$run_status" -ne 0 ]; then return "$run_status"; fi
  return "$watch_status"
}

# The transcript's directory first: a fresh root has no logs/ yet.
mkdir -p "$ASSAY_ROOT/logs"
transcript="$ASSAY_ROOT/logs/assay-run-$(date +%Y%m%d-%H%M%S).log"
set +e
_main "$@" 2>&1 | tee -a "$transcript"
status=${PIPESTATUS[0]}
exit "$status"
