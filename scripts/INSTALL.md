# Preface

All API keys in this project are managed centrally via the secret manager **Vault**, rather than being written entirely into `.env` — this is a necessary measure to prevent key leakage. Accordingly, this document first covers installing and configuring the secret manager (Vault), then setting up the Python environment, and finally provides some additional configuration for reference.

> 🌐 中文版请见 [INSTALL_zh.md](INSTALL_zh.md)

# 1. Install the API Key Manager (Vault)

## Step 1:

```bash
1. If already installed, just start the service
vault server -config=/mnt/agent-framework/<your user path>/myapp/vault/config/vault.hcl > /mnt/agent-framework/<your user path>/myapp/vault/vault.log 2>&1 &

2. If not installed yet, use the install script
cd scripts
chmod +x install_vault.sh
./install_vault.sh /mnt/agent-framework/<your user path>/myapp # starts the service locally at http://127.0.0.1:8200 by default. When VSCode connects to the server it forwards the port automatically, so just click the popup in VSCode to open http://127.0.0.1:8200 and reach the frontend
```

## Step 2: Set the number of unseal key shares to 1
Set **Key shares** to **1** and **Key threshold** to **1**, then click **Initialize**.
![alt text](../docs/assets/step2.png)

## Step 3:
You will see two keys — one **Initial root token** and one login/unseal verification key **unseal token key1**. Be sure to record them!!! You can also save them locally (click **Download Keys** to download the JSON file).

It is recommended to put the **Initial root token** into the `.env` at the project root:
```bash
VAULT_ADDR='http://127.0.0.1:8200'
VAULT_TOKEN="<initial root token>"
UNSEAL_TOKEN='<unseal token key1>'
SECRET_ENGINE_PATH='cubbyhole/env'
```

![alt text](../docs/assets/step3.png)

Then click **Continue to Unseal**.

## Step 4:
The key to enter is **unseal token key1**.
![alt text](../docs/assets/step4.png)

## Step 5:
The key to enter is the **Initial root token**.
![alt text](../docs/assets/step5.png)

## Step 6:
Login succeeds. You will see a secret engine named **cubbyhole/** — click **View**.
![alt text](../docs/assets/step6.png)

## Step 7:
Click **Create secret** and set path to **env**, so it matches **SECRET_ENGINE_PATH='cubbyhole/env'** in `.env`.
![alt text](../docs/assets/step7.png)

## Step 8:
Fill in **key: value** pairs, then click **Save** — configuration is done. You may also paste a correctly formatted JSON blob directly, as below.
![alt text](../docs/assets/step8.png)

The keys to fill in should include:
```bash
{
  "AWS_CLAUDE_API_BASE": "internal aws-claude base url (required)",
  "AWS_CLAUDE_API_KEY": "internal aws-claude api key (required)",
  "FIRECRAWL_API_BASE": "official Firecrawl base url, e.g. https://api.firecrawl.dev/v2 (required)",
  "FIRECRAWL_API_KEY": "official Firecrawl api key (required)",
  "INT_OPENROUTER_API_BASE": "internal openrouter base url (required)",
  "INT_OPENROUTER_API_KEY": "internal openrouter api key (required)",
  "JINA_BASE_URL": "internal jina base url (required)",
  "JINA_API_KEY": "internal jina api key (required)",
  "SERPER_BASE_URL": "internal serper base url (required)",
  "SERPER_API_KEY": "internal serper api key (required)",
  "OPENROUTER_API_BASE": "official openrouter base url, e.g. https://openrouter.ai/api/v1 (optional)",
  "OPENROUTER_API_KEY": "official openrouter api key (optional)"
}
```


## Step 9: Verify the configuration works
```
export VAULT_ADDR='http://127.0.0.1:8200'
export VAULT_TOKEN='your Initial root token'
vault kv get -field=OPENROUTER_API_KEY cubbyhole/env

The output is the content of your OPENROUTER_API_KEY:
abcabc...
```

# 2. Set up the Python environment

Pick **one** of the two options below (conda or uv). Python 3.12 is recommended (3.11+ required).
All dependencies are declared in `pyproject.toml` (there is no `requirements.txt`).

## Step 1 — Option A: conda + pip
```bash
conda create -n agent python=3.12
conda activate agent
pip install -e .              # core deps + the agentevolver package (adds the `agentevolver` CLI)

# optional extras (browser automation / chemistry / sandboxes):
pip install -e ".[browser]"   # or ".[chem]", ".[sandbox]", ".[all]"

# playwright + browser-use need a one-time browser download:
python -m playwright install chromium
```

## Step 1 — Option B: uv (faster, reproducible)
[uv](https://docs.astral.sh/uv/) is a fast pip/venv replacement; `uv sync` installs from
`pyproject.toml` + the committed `uv.lock` for a reproducible environment.
```bash
# install uv (once)
curl -LsSf https://astral.sh/uv/install.sh | sh

# create .venv and install core deps + the package (uses uv.lock)
uv sync
source .venv/bin/activate            # Windows: .venv\Scripts\activate

# optional extras:
uv sync --extra browser              # or --extra chem / sandbox / all

# playwright + browser-use need a one-time browser download:
python -m playwright install chromium
```

> Note: `pip install -e .` / `uv pip install -e .` installs this repo as the importable
> `agentevolver` package, so other projects can `import agentevolver` and you get the
> `agentevolver` console command. Run data goes to the current directory (or `$AGENTEVOLVER_HOME`),
> never into the installed package.

## Step 2:

Make sure `.env` contains the following:
```bash
VAULT_ADDR='http://127.0.0.1:8200'
VAULT_TOKEN="<initial root token>"
UNSEAL_TOKEN='<unseal token key1>'
SECRET_ENGINE_PATH='cubbyhole/env'
```

# 3. Misc

```bash
1. Test a model call
curl -X POST "https://xxx/v1/responses" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer xxx" \
  -d '{
    "model": "gpt-5.4-pro",
    "input": "hello",
    "max_output_tokens": 2048
  }'

curl -X POST "https://xxx/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer xxx" \
  -d '{
  "model": "openai/gpt-5.4",
  "messages": [
    {
      "role": "user",
      "content": "Hello"
    }
  ],
  "temperature": 0.7,
  "max_tokens": 2048
}'
```
