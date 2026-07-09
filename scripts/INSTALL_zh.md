# 前言

本项目所有 API Key 主要依赖密钥管理软件 Vault 进行统一管理，而非完全写在 `.env` 中，这是防止 key 泄漏的必要措施。因此，本文档先介绍 API Key 管理软件（Vault）的安装与配置，再介绍 opencode 的安装，随后介绍 Python 环境的安装，最后提供一些其他配置供参考。

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

# 二、安装opencode

如果你已经安装过opencode了，只需要保证opencode.json文件内容正确即可

vim /mnt/agent-framework/<yourt user path>/myapp/opencode/opencode.json

```json
{
  "$schema": "https://opencode.ai/config.json",
  "permission": {
    "bash": "allow",
    "edit": "allow",
    "write": "allow"
  },
  "provider": {
    "anthropic": {
      "options": {
        "baseURL": "公司内aws-claude路径base url（必填）",
        "apiKey": "公司内aws-claude路径api key（必填）"
      },
      "models": {
        "claude-opus-4-6": {
          "options": {
            "thinking": { "type": "adaptive" },
            "output_config": { "effort": "high" }
          }
        },
        "claude-opus-4-8": {
          "options": {
            "thinking": { "type": "adaptive" },
            "output_config": { "effort": "high" }
          }
        }
      }
    }
  },
  "model": "anthropic/claude-opus-4-8"
}
```

说明：
- provider 名必须写 `anthropic`（不能写 `aws-claude`），这样 opencode 才会使用 `@ai-sdk/anthropic` 包，自动过滤消息中的空 text block，避免 Bedrock 400 报错；
- `thinking.type = "adaptive"` + `output_config.effort = "high"` 是 AWS Bedrock Claude Opus 4.x 的 reasoning 格式（直连 Anthropic API 用 `"type": "enabled"` + `budgetTokens`，两者不同）；
- 如果想使用 openrouter 路径可以配置如下：
  ```json
  {
    "$schema": "https://opencode.ai/config.json",
    "permission": {
      "bash": "allow",
      "edit": "allow",
      "write": "allow"
    },
    "provider": {
      "openrouter": {
        "options": {
          "baseURL": "官网或公司内openrouter的base url",
          "apiKey": "官网或公司内openrouter的api key"
        },
        "models": {
          "anthropic/claude-opus-4.6": {},
          "anthropic/claude-opus-4.7": {}
        }
      },
      "anthropic": {
        "options": {
          "baseURL": "公司内aws-claude路径base url（必填）",
          "apiKey": "公司内aws-claude路径api key（必填）"
        },
        "models": {
          "claude-opus-4-6": {
            "options": {
              "thinking": { "type": "adaptive" },
              "output_config": { "effort": "high" }
            }
          },
          "claude-opus-4-8": {
            "options": {
              "thinking": { "type": "adaptive" },
              "output_config": { "effort": "high" }
            }
          }
        }
      }
    },
    "model": "openrouter/anthropic/claude-opus-4.6"
  }
  ```

> 安全提示：opencode.json 里的 apiKey 是明文写在文件里的，请确保该文件权限不暴露给非可信用户（`chmod 600 opencode.json` 推荐）。

## Step1: 

```bash
cd scripts
chmod +x install_opencode.sh 
./install_opencode.sh /mnt/agent-framework/<yourt user path>/myapp
```

## Step2: 最后验证是否配置成功
```
opencode run 'hello world'

如果出现类似信息表示成功：
 
> build · claude-opus-4-6

Hello! How can I help you today? If you need assistance with a software engineering task, feel free to describe what you're working on.
```

# 三、安装python环境

## Step1: 
```bash
conda create -n agent python=3.12
conda activate agent
pip install -r requirements.txt

# 安装playwright
pip install playwright
playwright install

# 安装browser use
pip install browser-use
browser-use install
```

## Step2:

保证`.env`内容如下：
```bash
VAULT_ADDR='http://127.0.0.1:8200'
VAULT_TOKEN="<initial root token>"
UNSEAL_TOKEN='<unseal token key1>'
SECRET_ENGINE_PATH='cubbyhole/env'
```

# 四、其他

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