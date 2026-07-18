# 前言

本项目所有 API Key 主要依赖密钥管理软件 Vault 进行统一管理，而非完全写在 `.env` 中，这是防止 key 泄漏的必要措施。因此，本文档先介绍 API Key 管理软件（Vault）的安装与配置，随后介绍 Python 环境的安装，最后提供一些其他配置供参考。

> 🌐 English version: [INSTALL.md](INSTALL.md)

# 一、安装API Key管理软件

## Step1: 

```bash
1. 如果已经安装了，直接启动服务
vault server -config=/mnt/agent-framework/<yourt user path>/myapp/vault/config/vault.hcl > /mnt/agent-framework/<yourt user path>/myapp/vault/vault.log 2>&1 &

2. 如果还未安装，使用安装脚本
cd scripts
chmod +x install_vault.sh 
./install_vault.sh /mnt/agent-framework/<yourt user path>/myapp # 会默认本地启动服务http://127.0.0.1:8200, vscodel连接服务器会默认做端口映射，所以直接点击vscode弹射出窗口进入http://127.0.0.1:8200链接就可以进入到前端
```

## Step2: 设置平台登录验证秘钥个数为1
**Key shares**设置为**1**, **Key threshold**设置为**1**，最后点击**Initialize**
![alt text](../docs/assets/step2.png)

## Step3: 
可以看到有两个key一个是**Initial root token**，一个是登录用验证**unseal token key1**，一定要记录下来!!!也可以保存到本地（点击**Download Keys**下载json文件到本地）

建议把**Initial root token**放到项目根目录下的.env里
```bash
VAULT_ADDR='http://127.0.0.1:8200'
VAULT_TOKEN="<initial root token>"
UNSEAL_TOKEN='<unseal token key1>'
SECRET_ENGINE_PATH='cubbyhole/env'
```

![alt text](../docs/assets/step3.png)

然后点击**Continue to Unseal**

## Step4: 
输入key是**unseal token key1**
![alt text](../docs/assets/step4.png)

## Step5: 
输入的key是**Initial root token**
![alt text](../docs/assets/step5.png)

## Step6:
可以看到登录成功了，有一个秘钥本是**cubbyhole/**，点击**View**
![alt text](../docs/assets/step6.png)

## Step7:
点击**Create secret**, path设置为**env**，这样和.env里的**SECRET_ENGINE_PATH='cubbyhole/env'**对应
![alt text](../docs/assets/step7.png)

## Step8:
点击**填入key:value即可**，最后点击**Save**，这样就配置完毕。也可以选择直接粘贴正确格式的json串如下
![alt text](../docs/assets/step8.png)

需要填入的key应该包括：
```bash
{
  "AWS_CLAUDE_API_BASE": "公司内aws-claude路径base url（必填）",
  "AWS_CLAUDE_API_KEY": "公司内aws-claude路径api key（必填）",
  "FIRECRAWL_API_BASE": "官网firecrawl的base url，例https://api.firecrawl.dev/v2（必填）",
  "FIRECRAWL_API_KEY": "官网firecrawl的api key（必填）",
  "INT_OPENROUTER_API_BASE": "公司内openrouter路径base url（必填）",
  "INT_OPENROUTER_API_KEY": "公司内openroute路径api key（必填）",
  "JINA_BASE_URL": "公司内jina base url（必填）",
  "JINA_API_KEY": "公司内jina api key（必填）",
  "SERPER_BASE_URL": "公司内serper base url（必填）",
  "SERPER_API_KEY": "公司内serper api key （必填）",
  "OPENROUTER_API_BASE": "官网openrouter的base url，例https://openrouter.ai/api/v1（选填）",
  "OPENROUTER_API_KEY": "官网openrouter的api key（选填）"
}
```


## Step9: 最后验证是否配置成功
```
export VAULT_ADDR='http://127.0.0.1:8200'
export VAULT_TOKEN='你的Initial root token'
vault kv get -field=OPENROUTER_API_KEY cubbyhole/env

输出内容是你的OPENROUTER_API_KEY内容:
abcabc...
```

# 二、安装python环境

以下两种方式**任选其一**(conda 或 uv)。推荐 Python 3.12(最低 3.11)。
所有依赖都声明在 `pyproject.toml` 里(不再有 `requirements.txt`)。

## Step1 — 方式 A：conda + pip
```bash
conda create -n agent python=3.12
conda activate agent
pip install -e .              # 核心依赖 + agentevolver 包（并注册 `agentevolver` 命令行）

# 可选 extras（浏览器自动化 / 化学 / 沙箱）：
pip install -e ".[browser]"   # 或 ".[chem]" ".[sandbox]" ".[all]"

# playwright / browser-use 需要一次性下载浏览器：
playwright install
browser-use install
```

## Step1 — 方式 B：uv（更快、可复现）
[uv](https://docs.astral.sh/uv/) 是 pip/venv 的高速替代；`uv sync` 会依据 `pyproject.toml`
+ 已提交的 `uv.lock` 安装,环境可复现。
```bash
# 安装 uv（一次即可）
curl -LsSf https://astral.sh/uv/install.sh | sh

# 创建 .venv 并安装核心依赖 + 本包（使用 uv.lock）
uv sync
source .venv/bin/activate            # Windows: .venv\Scripts\activate

# 可选 extras：
uv sync --extra browser              # 或 --extra chem / sandbox / all

# playwright / browser-use 需要一次性下载浏览器：
playwright install
browser-use install
```

> 说明：`pip install -e .` / `uv pip install -e .` 会把本仓库装成可导入的 `agentevolver` 包，
> 其他项目就能 `import agentevolver`，同时得到 `agentevolver` 命令。运行产生的数据落在当前目录
> （或 `$AGENTEVOLVER_HOME`），绝不会写进已安装的包里。

## Step2:

保证`.env`内容如下：
```bash
VAULT_ADDR='http://127.0.0.1:8200'
VAULT_TOKEN="<initial root token>"
UNSEAL_TOKEN='<unseal token key1>'
SECRET_ENGINE_PATH='cubbyhole/env'
```

# 三、其他

```bash
1. 测试模型调用
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