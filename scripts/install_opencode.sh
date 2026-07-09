#!/usr/bin/env bash
set -euo pipefail

# Install opencode and generate opencode.json with secrets from Vault.
# Usage: bash install_opencode.sh [INSTALL_PREFIX]
# Default INSTALL_PREFIX: /usr/local

INSTALL_PREFIX="${1:-/usr/local}"
OPENCODE_DIR="${INSTALL_PREFIX}/opencode"
OPENCODE_CONFIG="${OPENCODE_DIR}/opencode.json"
OPENCODE_BIN_DIR="${INSTALL_PREFIX}/opencode/bin"

echo "Install prefix: ${INSTALL_PREFIX}"
mkdir -p "${OPENCODE_DIR}" "${OPENCODE_BIN_DIR}"

# ---------------------------------------------------------------------------
# Step 1: Install opencode binary
# ---------------------------------------------------------------------------
echo "[1/3] Installing opencode..."
curl -fsSL https://opencode.ai/install | bash

# The official installer always puts the binary in ~/.opencode/bin.
# Move it to the requested INSTALL_PREFIX.
mkdir -p "${OPENCODE_BIN_DIR}"
mv "${HOME}/.opencode/bin/opencode" "${OPENCODE_BIN_DIR}/opencode"

# Export so opencode is usable in subsequent steps
export PATH="${OPENCODE_BIN_DIR}:${PATH}"
export OPENCODE_CONFIG_PATH="${OPENCODE_CONFIG}"

# Remove existing opencode entries before re-adding
sed -i '/# opencode/d' "${HOME}/.bashrc"
sed -i '/export OPENCODE_CONFIG=/d' "${HOME}/.bashrc"
sed -i "\|export PATH=${OPENCODE_BIN_DIR}|d" "${HOME}/.bashrc"

{
  echo ''
  echo '# opencode'
  echo "export OPENCODE_CONFIG=${OPENCODE_CONFIG}"
  echo "export PATH=${OPENCODE_BIN_DIR}:\$PATH"
} >> "${HOME}/.bashrc"

# ---------------------------------------------------------------------------
# Step 2: Fetch secrets from Vault
# ---------------------------------------------------------------------------
echo "[2/3] Fetching secrets from Vault..."

: "${VAULT_ADDR:?VAULT_ADDR is not set}"
: "${VAULT_TOKEN:?VAULT_TOKEN is not set}"
: "${SECRET_ENGINE_PATH:=${VAULT_SECRET_PATH:-cubbyhole/env}}"

vault_get() {
  vault kv get -field="$1" "${SECRET_ENGINE_PATH}"
}

INT_OPENROUTER_API_BASE="$(vault_get INT_OPENROUTER_API_BASE)"
INT_OPENROUTER_API_KEY="$(vault_get INT_OPENROUTER_API_KEY)"
AWS_CLAUDE_API_BASE="$(vault_get AWS_CLAUDE_API_BASE)"
AWS_CLAUDE_API_KEY="$(vault_get AWS_CLAUDE_API_KEY)"

# ---------------------------------------------------------------------------
# Step 3: Write opencode.json
# ---------------------------------------------------------------------------
echo "[3/3] Writing ${OPENCODE_CONFIG}..."

cat > "${OPENCODE_CONFIG}" <<EOF
{
  "\$schema": "https://opencode.ai/config.json",
  "permission": {
    "bash": "allow",
    "edit": "allow",
    "write": "allow"
  },
  "provider": {
    "openrouter": {
      "options": {
        "baseURL": "${INT_OPENROUTER_API_BASE}",
        "apiKey": "${INT_OPENROUTER_API_KEY}"
      },
      "models": {
        "anthropic/claude-opus-4.6": {}
      }
    },

    "anthropic": {
      "options": {
        "baseURL": "${AWS_CLAUDE_API_BASE}",
        "apiKey": "${AWS_CLAUDE_API_KEY}"
      },
      "models": {
        "claude-opus-4-6": {
          "options": {
            "thinking": {
              "type": "adaptive"
            },
            "output_config": {
              "effort": "high"
            }
          }
        },
        "claude-opus-4-8": {
          "options": {
            "thinking": {
              "type": "adaptive"
            },
            "output_config": {
              "effort": "high"
            }
          }
        }
      }
    }
  },
  "model": "anthropic/claude-opus-4-8"
}
EOF

echo
echo "Done."
echo "Config: ${OPENCODE_CONFIG}"
echo "Binary: ${OPENCODE_BIN_DIR}/opencode"
echo
echo "Tip:"
echo "  opencode run 'hello world'"
