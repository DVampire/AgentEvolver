#!/usr/bin/env bash
set -euo pipefail

# Launch any examples/run_*.py. Agent setup, output paths, monitoring and experiment
# lifecycle remain in the selected example; this script only selects and execs it.
EXPERIMENT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
EXPERIMENT_PYTHON="${AGENTEVOLVER_PYTHON:-}"

usage() {
  cat <<'USAGE'
Usage: scripts/run.sh [--python PATH] EXAMPLE [example arguments...]
       scripts/run.sh --list

EXAMPLE accepts a short name, run_*.py, or its path under examples/.
Everything after EXAMPLE is passed unchanged to that example, including --help.
Relative experiment paths are resolved from the repository root.

Examples:
  scripts/run.sh website_evolution_demo --scenario-dir examples/tasks/website_evolution/orbital_simulator
  scripts/run.sh run_swebench_pro.py --help
  scripts/run.sh examples/run_factor_strategy_mining_demo.py --help
  scripts/run.sh --python /path/to/env/bin/python meta_agent --task 'Build a website'

Python: --python / AGENTEVOLVER_PYTHON, then the active virtualenv or non-base conda env,
repository .venv, the installed agentos conda env, then python/python3 on PATH.
The selected interpreter's bin directory is also added to PATH for child tools.
The script stays in the foreground; use your usual terminal, tmux or nohup to detach.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --python)
      if [[ $# -lt 2 || -z "$2" ]]; then
        echo 'Error: --python requires an executable path or name.' >&2
        exit 2
      fi
      EXPERIMENT_PYTHON="$2"
      shift 2
      ;;
    --list)
      for entry in "${EXPERIMENT_ROOT}"/examples/run_*.py; do
        [[ -f "${entry}" ]] || continue
        entry="${entry##*/run_}"
        echo "${entry%.py}"
      done
      exit 0
      ;;
    -h|--help) usage; exit 0 ;;
    --) shift; break ;;
    -*) echo "Unknown launcher option: $1" >&2; exit 2 ;;
    *) break ;;
  esac
done

if [[ $# -eq 0 ]]; then
  usage >&2
  exit 2
fi

EXPERIMENT_ENTRY="$1"
shift
case "${EXPERIMENT_ENTRY}" in
  /*) ;;
  */*) EXPERIMENT_ENTRY="${EXPERIMENT_ROOT}/${EXPERIMENT_ENTRY}" ;;
  *)
    EXPERIMENT_ENTRY="${EXPERIMENT_ENTRY%.py}"
    EXPERIMENT_ENTRY="${EXPERIMENT_ROOT}/examples/run_${EXPERIMENT_ENTRY#run_}.py"
    ;;
esac
if [[ ! -f "${EXPERIMENT_ENTRY}" ]]; then
  echo "Example not found: ${EXPERIMENT_ENTRY} (use --list)" >&2
  exit 2
fi
EXPERIMENT_ENTRY="$(realpath -- "${EXPERIMENT_ENTRY}")"
if [[ "${EXPERIMENT_ENTRY}" != "${EXPERIMENT_ROOT}"/examples/run_*.py ||
      "${EXPERIMENT_ENTRY##*/}" != run_*.py ]]; then
  echo 'Choose an examples/run_*.py entry point.' >&2
  exit 2
fi

if [[ -z "${EXPERIMENT_PYTHON}" ]]; then
  EXPERIMENT_CONDA="${CONDA_PREFIX:-/nonexistent}"
  # Shell initialization often activates base, which does not contain project deps.
  [[ "${CONDA_DEFAULT_ENV:-}" == base ]] && EXPERIMENT_CONDA=/nonexistent
  for candidate in \
    "${VIRTUAL_ENV:-/nonexistent}/bin/python" \
    "${EXPERIMENT_CONDA}/bin/python" \
    "${EXPERIMENT_ROOT}/.venv/bin/python" \
    "${HOME}/miniconda3/envs/agentos/bin/python" \
    "${HOME}/anaconda3/envs/agentos/bin/python" \
    "${HOME}/miniforge3/envs/agentos/bin/python"; do
    if [[ -x "${candidate}" ]]; then
      EXPERIMENT_PYTHON="${candidate}"
      break
    fi
  done
  if [[ -z "${EXPERIMENT_PYTHON}" ]]; then
    EXPERIMENT_PYTHON="$(command -v python || command -v python3 || true)"
  fi
fi
if [[ -z "${EXPERIMENT_PYTHON}" ]] || ! EXPERIMENT_PYTHON="$(command -v -- "${EXPERIMENT_PYTHON}")"; then
  echo 'Python not found. Activate the environment or pass --python.' >&2
  exit 2
fi
# Preserve the venv executable path rather than resolving its symlink to system Python.
EXPERIMENT_BIN="$(cd -- "$(dirname -- "${EXPERIMENT_PYTHON}")" && pwd)"
export PATH="${EXPERIMENT_BIN}:${PATH}"
export PYTHONPATH="${EXPERIMENT_ROOT}${PYTHONPATH:+:${PYTHONPATH}}"
cd -- "${EXPERIMENT_ROOT}"
exec "${EXPERIMENT_BIN}/$(basename -- "${EXPERIMENT_PYTHON}")" -u "${EXPERIMENT_ENTRY}" "$@"
