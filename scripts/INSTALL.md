# 一、安装API Key管理软件

## Step1: 

```bash
如果已经安装了，直接启动服务
vault server -config=/mnt/agent-framework/<yourt user path>/myapp/vault/config/vault.hcl > /mnt/agent-framework/<yourt user path>/myapp/vault/vault.log 2>&1 &

cd scripts
chmod +x install_vault.sh 
./install_vault.sh /mnt/agent-framework/<yourt user path>/myapp # 会默认本地启动服务http://127.0.0.1:8200, vscodel连接服务器会默认做端口映射，所以直接点击vscode弹射出窗口进入http://127.0.0.1:8200链接就可以进入到前端
```

## Step2: 设置平台登录验证秘钥个数为1
**Key shares**设置为**1**, **Key threshold**设置为**1**，最后点击**Initialize**
![alt text](../docs/step2.png)

## Step3: 
可以看到有两个key一个是**Initial root token**，一个是登录用验证**key1**，一定要记录下来!!!也可以保存到本地（点击**Download Keys**下载json文件到本地）

建议把**Initial root token**放到项目根目录下的.env里
```bash
VAULT_ADDR='http://127.0.0.1:8200'
VAULT_TOKEN=<Initial root token>
SECRET_ENGINE_PATH='cubbyhole/env'
```

![alt text](../docs/step3.png)

然后点击**Continue to Unseal**

## Step4: 
输入key是**key1**
![alt text](../docs/step4.png)

## Step5: 
输入的key是**Initial root token**
![alt text](../docs/step5.png)

## Step6:
可以看到登录成功了，有一个秘钥本是**cubbyhole/**，点击**View**
![alt text](../docs/step6.png)

## Step7:
点击**Create secret**, path设置为**env**，这样和.env里的**SECRET_ENGINE_PATH='cubbyhole/env'**对应
![alt text](../docs/step7.png)

## Step8:
点击**填入key:value即可**，最后点击**Save**，这样就配置完毕
![alt text](../docs/step8.png)

需要填入的key应该包括：
```bash
OPENROUTER_API_BASE=...
OPENROUTER_API_KEY=...
OPENAI_API_BASE=...
OPENAI_API_KEY=...
NEWAPI_API_BASE=...
NEWAPI_API_KEY=...
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

```
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
        "baseURL": "${OPENROUTER_API_BASE}",
        "apiKey": "${OPENROUTER_API_KEY}"
      },
      "models": {
        "anthropic/claude-opus-4.6": {}
      }
    },

    "newapi": {
      "options": {
        "baseURL": "${NEWAPI_API_BASE}",
        "apiKey": "${NEWAPI_API_KEY}"
      },
      "models": {
        "claude-opus-4-6": {}
      }
    },

    "openai": {
      "options": {
        "baseURL": "${OPENAI_API_BASE}",
        "apiKey": "${OPENAI_API_KEY}"
      },
      "models": {
        "gpt-5.4": {},
        "gpt-5.4-pro": {}
      }
    }
  },
  "model": "newapi/claude-opus-4-6"
}
```

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
 
> build · anthropic/claude-opus-4.6

Hello! How can I help you today? If you need assistance with a software engineering task, feel free to describe what you're working on.
```

# 三、安装python环境

## Step1: 
```bash
conda create -n agentos python=3.11
conda activate agentos
pip install -r requirements.txt
```

## Step2:

保证`.env`内容如下：
```bash
VAULT_ADDR='http://127.0.0.1:8200'
VAULT_TOKEN=abcabcabc
SECRET_ENGINE_PATH='cubbyhole/env'
```

## Step3: 测试验证
```bash
# download hle
cd datasets
git clone https://huggingface.co/datasets/cais/hle
cd ..

# run example
python examples/run_hle.py
```

## 参数说明

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `--config` | str | `configs/bus.py` | 配置文件路径 |
| `--model-name` | str | `openrouter/gemini-3.1-pro-preview` | 使用的模型名称（直接推理模式） |
| `--use-bus` | flag | `False` | 启用 AgentBus 完整 agent 流水线，默认为直接 LLM 推理 |
| `--max-concurrency` | int | `4` | 最大并发任务数 |
| `--max-rounds` | int | `10` | Bus 模式下每个任务的最大规划轮次 |
| `--start` | int | `None` | HLE 数据集子集的起始索引（含） |
| `--end` | int | `None` | HLE 数据集子集的结束索引（不含） |
| `--cfg-options` | list | - | 以 `key=value` 格式覆盖配置项 |


# 四、SkillsBench

SkillsBench 是一个多轮交互式 benchmark，包含 87 个任务，运行在 Docker 容器中。Agent 发送 shell 命令，接收 stdout/stderr 反馈，最终由 test.sh 评估（reward 0.0-1.0）。

## 前置条件

1. 安装 Docker 并确保当前用户有 Docker 权限
2. 启动 SkillsBench sandbox server：

```bash
git submodule update --init -- src/benchmark/skillsbench-sandbox/skillsbench

pip install gymnasium pyyaml
```


## 启动 Sandbox Server

```bash
python src/benchmark/skillsbench-sandbox/start_sandbox_server.py --config src/benchmark/skillsbench-sandbox/sandbox_config.yaml
```

Health check:

```bash
curl -s http://127.0.0.1:8080/health
```

## 运行

```bash
# 直接 LLM 推理（多轮 shell 交互）
python examples/run_skillsbench.py --model-name openrouter/gemini-3.1-pro-preview

# Bus 模式（AgentOS 完整 agent 流水线）
python examples/run_skillsbench.py --use-bus
```

## SkillsBench 参数说明

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `--config` | str | `configs/bus.py` | 配置文件路径 |
| `--model-name` | str | `openrouter/gemini-3.1-pro-preview` | 使用的模型名称（直接推理模式） |
| `--use-bus` | flag | `False` | 启用 AgentBus 完整 agent 流水线 |
| `--server-url` | str | `http://127.0.0.1:8080` | Sandbox server 地址 |
| `--dataset` | str | `tasks` | 数据集名称（tasks, tasks-no-skills 等） |
| `--task-id` | str | `None` | 运行单个任务（如 xlsx-recover-data） |
| `--max-concurrency` | int | `4` | 最大并发任务数 |
| `--max-steps` | int | `None` | 覆盖每个任务的最大步数（默认按难度推断） |
| `--max-rounds` | int | `20` | Bus 模式下每个任务的最大规划轮次 |
| `--step-timeout` | int | `None` | 覆盖每步执行超时（秒） |
| `--start` | int | `None` | 任务子集的起始索引（含） |
| `--end` | int | `None` | 任务子集的结束索引（不含） |
| `--resume` | flag | `False` | 从最新结果文件恢复 |
| `--filter` | str | `None` | 配合 --resume 使用：wrong 重跑失败，null 重跑无结果 |
| `--disable-skill-injection` | flag | `False` | 禁用 skill 元数据注入到 agent prompt |
| `--cfg-options` | list | - | 以 `key=value` 格式覆盖配置项 |

# 五、其他

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
  "max_tokens": 2048,
}'
```

```bash
1. 安装gcloud
curl https://sdk.cloud.google.com | bash
gcloud init
gcloud auth application-default login
gcloud config set project YOUR_PROJECT_ID # https://console.cloud.google.com/ get your project id
gcloud auth application-default set-quota-project YOUR_PROJECT_ID
```

```bash
1. 安装playwright

pip install playwright
playwright install

2. browser use
pip install browser-use
browser-use install

```