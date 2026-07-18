# HLE评测指南

本文档说明如何在 **HLE (Humanity's Last Exam, 2500 题)** 上进行端到端评测。涵盖：环境与密钥准备、首次运行、过程中如何看进度与结果、断点续跑、以及对“空答案/未完成”题目的二次重跑。

> 入口脚本：examples/run_hle.py 默认配置：configs/hle.py
> 

> github链接：https://github.com/DVampire/AgentEvolver
> 

> 🌐 English version: [intro_hle.md](intro_hle.md)

---

## **快速简介版（TL;DR）**

假设你已经按[INSTALL_zh.md](INSTALL_zh.md) 配置好了 Vault、opencode、Python 环境：

```bash
# 0) 小规模验证（5 题，约几分钟）
python examples/run_hle.py --start 0 --end 5 --max-concurrency 4

# 1) 全量跑 2500 题
python examples/run_hle.py --max-concurrency 16
#    过程中：终端会打 Progress / ✅ / ❌；
#    实时结果写在 workdir/hle/results/hle/benchmark_hle_<timestamp>.json
#    日志在 workdir/hle/hle.log

# 2) 中途中断了，续跑
python examples/run_hle.py --resume        # 自动用最新结果文件

# 3) 全部跑完后，把 predicted_answer 为空 / planner 未完成的题再跑一遍
python examples/run_hle.py --resume <results.json> --filter null

# 4) 只重新出统计 + 可视化页（不重跑）
python examples/run_hle.py --stats
#    生成 workdir/hle/results/hle/viewer.html，浏览器打开抽查
```

---

## **完整评测流程（三步）**

#### 后一步都在前一步产出的 results 文件上接着做

#### **1. 正常全量跑**

```jsx
python examples/run_hle.py --max-concurrency 16
```

跑完 2500 题，拿到基线结果 `benchmark_hle_<ts1>.json`。中断了用 `--resume` 续跑，不单算一步

### **2. `--filter null` 空题补跑**

```jsx
python examples/run_hle.py --resume benchmark_hle_<ts1>.json --filter null --max-concurrency 16
```

只挑 `predicted_answer` 为空 / planner 未完成的题再跑一遍——这些题可能因为 API Key 不稳定超时或其他不稳定因素导致未能完成完整答题过程，避免被错误判成错题

### **3. `--eval-only` LLM 重判**

```jsx
python examples/run_hle.py --eval-only --resume benchmark_hle_<ts2>.json --eval-model-name openrouter/o3-mini
```

不重跑答案，只让 LLM judge 把分重判一遍。答案补齐后再统一判分，准确率最准

---

## **评测范围与产物**

- **题量**：HLE test split 共 2500 题（含 multiple choice 与 exact match 两类，部分含图片）。
- **每题工作流**：默认开启 `-use-bus`，即走 AgentEvolver 完整 Agent 流水线（planner + deep_researcher + deep_analyzer + opencode + sop）。
- **产物**：每次运行会在 `workdir/hle/results/hle/` 下生成：
    - `benchmark_hle_<timestamp>.json` —— 每题逐条记录 + summary（实时写入，可随时查看进度）
    - `benchmark_hle_<timestamp>_tagged.json` / `hle_tagged.json` —— 评测结束后生成的标注版（供 viewer 读取）
    - `benchmark_hle_<timestamp>_viewer.html` / `viewer.html` —— 可直接用浏览器打开的可视化结果页

---

## **环境准备**

详细配置步骤请参考 [INSTALL_zh.md](INSTALL_zh.md)，请严格按照要求进行配置。

---

## **跑全量 2500 题**

### **启动命令**

```bash
python examples/run_hle.py \
    --config configs/hle.py \
    --max-concurrency 16 \
    --start 0 \
    --end 1500
```

常用可选参数：

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `--max-concurrency` | 16 | 并发任务数。机器内存大 / 配额充足时可上调到 32 |
| `--max-rounds` | 20 | bus 模式下每题 planner 最大轮次（一般不用改） |
| `--start` / `--end` | None | 仅评测 HLE 数据集 `[start, end)` 区间，用于分片或冒烟 |
| `--tags TAG [TAG ...]` | None | 只跑命中给定 tag 的题目（tag 定义见 `datasets/hle/data/tags.json`） |
| `--eval-model-name` | `openrouter/o3-mini` | 用于二次判分的 LLM evaluator |
| `--no-use-bus` | / | 退回到直接 LLM 推理（不走 agent 流水线），仅做对照用 |

### **评测期间如何看进度**

运行后会立刻打印一行：

```
| Results will be saved to: workdir/hle/results/hle/benchmark_hle_2026-05-18_10-30-12.json
```

**这个文件就是评测的唯一权威产物**，记下它的路径。框架在每完成一题后会原子地把整份 JSON（包括 summary）覆写一次，所以可以随时 `cat` / 用编辑器看：

```bash
# 实时看 summary（已完成数 / 正确数 / 准确率）
jq '.summary' workdir/hle/results/hle/benchmark_hle_<timestamp>.json
```

终端里日志的关键字：

- `| Progress: 137/2500 (5.5%)` —— 总进度
- `| ✅ Correct [<task_id>] | score=1.0 | time=42.3s` —— 单题判对
- `| ❌ Wrong [<task_id>] | score=0.0 | time=58.1s` —— 单题判错
- `| ❌ [<id>] LLM eval: wrong — retrying` —— 答错后框架内置的“LLM 二次判分 + 自动重试”逻辑触发

完整文本日志在配置里的 `log_path = hle.log`（即 `workdir/hle/hle.log`），可以 `tail -f` 跟。

### **评测结束后看结果**

`run_hle.py` 在所有题跑完后会自动打印一段汇总，例如：

```
==================================================
Results file: workdir/hle/results/hle/benchmark_hle_2026-05-18_10-30-12.json
==================================================
Overall: 612/2500 = 24.48%
==================================================
Per-tag accuracy (sorted by task count):
  math                            123/420  = 29.29%
  biology                          89/310  = 28.71%
  ...
```

如果想脱机重新出统计 / viewer，不重跑：

```bash
# 默认使用 workdir/hle/results/hle/ 下时间戳最新的那份
python examples/run_hle.py --stats

# 或指定文件
python examples/run_hle.py --stats --resume workdir/hle/results/hle/benchmark_hle_<timestamp>.json
```

- `-stats` 会重新计算并打印准确率，同时生成与 results 同目录下的 `viewer.html` + `hle_tagged.json`。**用浏览器直接打开 `viewer.html` 就可以按 tag/类别交互式浏览每道题的题面、模型答案、ground truth**，是给评测人员核查最方便的方式。

JSON 字段速查（`results` 数组里的每条记录）：

| 字段 | 含义 |
| --- | --- |
| `task_id` | HLE 题目 ID |
| `question` | 题面（已去掉 multipleChoice 的尾部提示） |
| `answer` / `ground_truth` | 正确答案 |
| `predicted_answer` | 模型最终答案 |
| `correct` | 是否判对（bool） |
| `category` / `raw_subject` / `tags` | 学科/标签 |
| `processing_time` | 单题耗时（秒） |
| `llm_eval_reasoning` | （若有）LLM 二次判分的理由 |

---

## **断点续跑（运行被中断后）**

如果训练机宕机、网络掉线、或手动 Ctrl-C 中断了评测，**不要新起一次评测**，应使用 `--resume` 在原 results 文件上继续：

```bash
# 自动使用 workdir/hle/results/hle/ 下最新的那份 results
python examples/run_hle.py --resume

# 或显式指定
python examples/run_hle.py --resume workdir/hle/results/hle/benchmark_hle_<timestamp>.json
```

行为：

- 框架会从 results 文件读出已完成 `task_id` 集合，**跳过这些题**，只跑剩余未完成的；
- 续跑会写到一个 **新** 的 `benchmark_hle_<新时间戳>.json`（已完成题以 preload 形式整体迁移过来，所以这份新文件最终就是“完整 2500 题”的成品，老文件可保留也可删）；
- 命令行其他参数（`-max-concurrency` 等）仍然生效。

---

## **全量跑完后，对“空答案 / planner 未完成”做二次重跑**

跑 2500 题难免有少量题目因为 bus timeout、planner 卡死、API 偶发失败等原因留空 `predicted_answer`。框架提供一个专门的过滤模式 `--filter null` 来只挑这些“空题”再跑一遍：

```bash
python examples/run_hle.py \
    --resume workdir/hle/results/hle/benchmark_hle_<timestamp>.json \
    --filter null \
    --max-concurrency 16
```

- `-filter null` 的判定规则（在 examples/run_hle.py:677-690）：满足以下任一即触发重跑，否则跳过保留原结果——
- `predicted_answer` 为空字符串
- `predicted_answer` 中包含 `"planner did not finish"`
- `predicted_answer` 中包含 `"planner returned no decision"`
- `predicted_answer` 中包含 `"unable to determine"`

其他 `--filter` 取值（按需）：

| 值 | 含义 |
| --- | --- |
| `null` | 仅重跑空 / planner 未完成的题（**推荐用于补跑**） |
| `wrong` | 重跑所有 `correct=False` 的题（即所有错题，慎用，相当于又跑一大半） |
| `none` | （默认）只跑 results 里完全没出现过的 task_id |

> ⚠️ `--filter` 必须配合 `--resume` 使用，单独传会被忽略并打印 warning。
> 

### **仅用 LLM 重新打分（不重新生成答案）**

如果只是怀疑判分严格度有问题（exact-match 把 `1/2` 和 `0.5` 判成不一样等），可以不重新生成答案、只让 LLM 把所有判错的题再判一遍：

```bash
python examples/run_hle.py \
    --eval-only \
    --resume workdir/hle/results/hle/benchmark_hle_<timestamp>.json \
    --eval-model-name openrouter/o3-mini
```

这会**原地**更新 resume 文件的 `correct` 字段并补写 `llm_eval_reasoning`，最后重新打印 stats。

---

## **常见问题**

| 现象 | 排查 |
| --- | --- |
| 启动报 `vault.exceptions.InvalidRequest` 或读不到 key | 检查 `.env` 的 `VAULT_TOKEN` / `VAULT_ADDR`、`vault status` 是否 unsealed |
| 启动报找不到某个模型 / 401 | 多半是 `INT_OPENROUTER_API_KEY` 没填（INSTALL_zh.md 没列这条，最容易漏） |
| 日志里 `the opencode CLI tool was not found` | `opencode` 没装在 PATH 上，回 INSTALL_zh.md 第二节安装 |
| opencode 子进程报 newapi 401 / 找不到模型 | `NEWAPI_API_KEY` 没填，或 opencode.json 里的 `model` 改成了不可用的 |
| deep_researcher 报 Firecrawl 网络 / 401 | `FIRECRAWL_API_KEY` 没填，或额度用完 |
| `--resume: no previous results found` | `workdir/hle/results/hle/` 目录不存在或为空，确认上一次确实跑过 |
| 大量题目 `predicted_answer` 为空 | 一般是 bus timeout (5400s) 或 planner 早退，先 `--filter null` 补跑；仍空则查 `hle.log` 里对应 task_id 的 traceback |
| `Eval timeout` 频繁出现 | judge 模型（`--eval-model-name`）网络问题，重试或换模型；不影响最终原始答案 |

---

## **关键文件位置一览**

| 路径 | 说明 |
| --- | --- |
| examples/run_hle.py | 评测入口 |
| configs/hle.py | HLE 默认配置（模型、agent 等） |
| agentevolver/benchmark/hle.py | HLE 数据集加载与判分逻辑 |
| `datasets/hle/` | HLE 原始数据（git clone 后得到） |
| `workdir/hle/results/hle/benchmark_hle_*.json` | 结果文件（评测产物） |
| `workdir/hle/hle.log` | 完整文本日志 |
| `workdir/hle/results/hle/viewer.html` | 可视化页面（结果产生后自动生成） |
