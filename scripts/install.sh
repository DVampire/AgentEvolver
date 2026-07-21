#!/usr/bin/env bash
set -euo pipefail

# One-shot AgentEvolver environment installer.
#
# Sets up everything needed to run the MetaAgent:
#   1. a Python env (conda or uv) on Python 3.11+
#   2. the agentevolver package + dependencies from pyproject.toml
#   3. Node.js / npm  -- required by the trace web UI and frontend/
#   4. optional extras (browser / chem / sandbox / benchmark)
#   5. an .env template, if one does not exist yet
#   6. a verification pass
#
# Re-running is safe: existing environments are reused, not recreated.
#
# Usage:
#   bash scripts/install.sh                      # conda env "agentos", core + dev
#   bash scripts/install.sh -n myenv -p 3.12
#   bash scripts/install.sh --extras browser     # + playwright & chromium
#   bash scripts/install.sh --extras all
#   bash scripts/install.sh --uv                 # use uv instead of conda
#   bash scripts/install.sh --no-node            # skip Node.js
#
# Vault is NOT installed here -- it is optional. The framework falls back to
# reading secrets from .env when Vault is unreachable. For central secret
# management see scripts/install_vault.sh and scripts/INSTALL.md.

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

ENV_NAME="agentos"
PY_VERSION="3.12"
EXTRAS="dev"
USE_UV=0
WITH_NODE=1

usage() {
  sed -n '4,27p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
  exit 0
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -n|--name)    ENV_NAME="$2"; shift 2 ;;
    -p|--python)  PY_VERSION="$2"; shift 2 ;;
    -e|--extras)  EXTRAS="$2"; shift 2 ;;
    --uv)         USE_UV=1; shift ;;
    --no-node)    WITH_NODE=0; shift ;;
    -h|--help)    usage ;;
    *) echo "Unknown option: $1 (try --help)" >&2; exit 1 ;;
  esac
done

# "dev" is always included so pytest is available for the verification pass.
case ",${EXTRAS}," in
  *,dev,*) ;;
  ,,)      EXTRAS="dev" ;;
  *)       EXTRAS="${EXTRAS},dev" ;;
esac

WANT_BROWSER=0
case ",${EXTRAS}," in
  *,browser,*|*,all,*) WANT_BROWSER=1 ;;
esac

STEPS=6
step() { echo; echo "=== [$1/${STEPS}] $2 ==="; }

echo "AgentEvolver installer"
echo "  repo    : ${REPO_ROOT}"
echo "  env     : ${ENV_NAME} (python ${PY_VERSION})"
echo "  extras  : ${EXTRAS}"
echo "  backend : $([[ ${USE_UV} -eq 1 ]] && echo uv || echo conda)"

cd "${REPO_ROOT}"

# ---------------------------------------------------------------------------
step 1 "Creating the Python environment"
# ---------------------------------------------------------------------------
if [[ ${USE_UV} -eq 1 ]]; then
  if ! command -v uv >/dev/null 2>&1; then
    echo "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="${HOME}/.local/bin:${PATH}"
  fi
  uv venv --python "${PY_VERSION}" 2>/dev/null || true
  ENV_PREFIX="${REPO_ROOT}/.venv"
else
  if ! command -v conda >/dev/null 2>&1; then
    echo "conda not found on PATH." >&2
    echo "Install Miniconda (https://docs.conda.io/en/latest/miniconda.html)," >&2
    echo "or re-run with --uv to use uv instead." >&2
    exit 1
  fi
  if conda env list | awk '{print $1}' | grep -qx "${ENV_NAME}"; then
    echo "conda env '${ENV_NAME}' already exists -- reusing it."
  else
    conda create -n "${ENV_NAME}" "python=${PY_VERSION}" -y
  fi
  ENV_PREFIX="$(conda env list | awk -v n="${ENV_NAME}" '$1==n {print $NF}')"
fi

PY="${ENV_PREFIX}/bin/python"
[[ -x "${PY}" ]] || { echo "Python not found at ${PY}" >&2; exit 1; }
echo "Python: $("${PY}" --version) at ${PY}"

# ---------------------------------------------------------------------------
step 2 "Installing agentevolver and its dependencies"
# ---------------------------------------------------------------------------
# Editable install: the repo stays the source of truth and `import agentevolver`
# works from anywhere. Dependencies come from pyproject.toml.
"${PY}" -m pip install --upgrade pip -q
"${PY}" -m pip install -e ".[${EXTRAS}]"

# ---------------------------------------------------------------------------
step 3 "Installing Node.js / npm"
# ---------------------------------------------------------------------------
# Needed by the trace web UI (agentevolver/trace/ui) and the Vite frontend.
# Without it those UIs are skipped -- agent runs still work.
if [[ ${WITH_NODE} -eq 0 ]]; then
  echo "Skipped (--no-node)."
elif [[ -x "${ENV_PREFIX}/bin/npm" ]]; then
  # npm's shebang is "#!/usr/bin/env node", so the env's bin must be on PATH.
  echo "npm already present: $(PATH="${ENV_PREFIX}/bin:${PATH}" npm --version)"
elif command -v npm >/dev/null 2>&1; then
  echo "Using system npm: $(npm --version)"
elif [[ ${USE_UV} -eq 0 ]]; then
  conda install -n "${ENV_NAME}" -c conda-forge nodejs -y
  echo "Installed: node $("${ENV_PREFIX}/bin/node" --version)"
else
  echo "npm not found. With --uv, install Node.js yourself (e.g. via nvm)."
fi

# ---------------------------------------------------------------------------
step 4 "Installing browser automation (optional)"
# ---------------------------------------------------------------------------
if [[ ${WANT_BROWSER} -eq 1 ]]; then
  # install-deps needs root; without it Chromium still runs on most systems.
  bash "${REPO_ROOT}/scripts/install_playwright.sh" "${PY}" || {
    echo "Playwright setup did not finish cleanly -- browser skills may not work."
  }
else
  echo "Skipped (add --extras browser to enable)."
fi

# ---------------------------------------------------------------------------
step 5 "Checking .env"
# ---------------------------------------------------------------------------
# Secrets are read from Vault when it is configured and reachable, and from
# .env otherwise (see agentevolver/utils/hvac_utils.py).
if [[ -f "${REPO_ROOT}/.env" ]]; then
  echo ".env already exists -- left untouched."
else
  cat > "${REPO_ROOT}/.env" <<'ENVEOF'
# Provider credentials. Fill in the ones you use; unset providers simply
# register no usable models.
#
# The base URL may include a trailing /v1 or omit it -- both work.

ANTHROPIC_API_BASE=''
ANTHROPIC_API_KEY=''

OPENROUTER_API_BASE=''
OPENROUTER_API_KEY=''

GOOGLE_API_BASE='https://generativelanguage.googleapis.com'
GOOGLE_API_KEY=''

OPENAI_API_BASE=''
OPENAI_API_KEY=''

# Optional: central secret management via Vault (scripts/install_vault.sh).
# When these are unset or Vault is unreachable, the values above are used.
# VAULT_ADDR='http://127.0.0.1:8200'
# VAULT_TOKEN=''
# UNSEAL_TOKEN=''
# SECRET_ENGINE_PATH='cubbyhole/env'
ENVEOF
  echo "Created a template .env -- fill in your API keys before running an agent."
fi

# ---------------------------------------------------------------------------
step 6 "Verifying"
# ---------------------------------------------------------------------------
FAILED=0
check() {
  if eval "$2" >/dev/null 2>&1; then
    echo "  ok    $1"
  else
    echo "  FAIL  $1"
    FAILED=1
  fi
}

check "import agentevolver"  "'${PY}' -c 'import agentevolver'"
check "agentevolver CLI"     "'${ENV_PREFIX}/bin/agentevolver' --help"
[[ ${WITH_NODE} -eq 1 ]] && check "npm" "PATH='${ENV_PREFIX}/bin:${PATH}' npm --version"
[[ ${WANT_BROWSER} -eq 1 ]] && check "playwright" "'${PY}' -c 'import playwright'"

echo
echo "  test suite:"
PATH="${ENV_PREFIX}/bin:${PATH}" "${PY}" -m pytest -q 2>&1 | tail -3 | sed 's/^/    /'

echo
if [[ ${FAILED} -eq 0 ]]; then
  echo "Done. Activate the environment with:"
  if [[ ${USE_UV} -eq 1 ]]; then
    echo "    source .venv/bin/activate"
  else
    echo "    conda activate ${ENV_NAME}"
  fi
  echo
  echo "Then run an agent:"
  echo "    python examples/run_meta_agent.py --task \"...\""
else
  echo "Some checks failed -- see the FAIL lines above." >&2
  exit 1
fi
