# 因子与策略挖掘

实现由两个专用 Agent、训练环境、数据适配器和独立 Benchmark 组成。
Agent 复用项目标准执行循环；运行编排在
[`examples/run_factor_mining.py`](../../../../examples/run_factor_mining.py)，
配置在 [`configs/factor_mining.py`](../../../../configs/factor_mining.py)。

## 模块边界

| 模块 | 职责 |
| --- | --- |
| `agentevolver/data/factor_mining.py` | CSV / Parquet / DataManager 数据导入、面板对齐、时间切分、指纹 |
| `expressions.py` | 严格 AST 白名单、因果算子、结构去重标识 |
| `research.py` | 因子指标、阈值协议、策略回测、诊断与报告 |
| `environment.py` | 仅训练集的研究操作、因子库查询和验证请求 |
| `agentevolver/benchmark/default/factor_mining.py` | 独立验证、入库、验证配额、冻结提交、一次最终测试 |
| `FactorMiningAgent` / `StrategyMiningAgent` | 提出与筛选假设、组合策略、反馈研究缺口 |
| `agentevolver/sandbox/filesystem.py` | 为研究进程构造文件系统白名单；没有宿主机全盘挂载 |

Benchmark 对外只通过 BenchmarkManager 的 `configure/reset/prepare/submit/eval/stats/cleanup`
调用。验证使用单独的 manager 实例及账本，最终统计不混入研究过程的验证尝试。

## 数据准备

行情必须包含 `timestamp, symbol, open, high, low, close, volume`。时间使用带时区
或可解析为 UTC 的 ISO 日期，不接受单位不明的数字时间戳。价格必须为正，成交量
非负；重复行、非法 OHLC 范围、缺失资产会被拒绝。缺失时间点补为 NaN，不前向填充。
资产名支持字母、数字、点、横线和下划线，例如 `BTCUSDT`。

```bash
python -m examples.run_factor_mining prepare \
  --source /data/ohlcv.parquet --frequency 1h \
  --symbols BTCUSDT ETHUSDT SOLUSDT \
  --dataset /data/factor_market
```

也可用 `--hf-repo owner/ohlcv --hf-split train` 经 DataManager 导入 HF 数据集。
默认按时间 60% / 20% / 20% 划分，在相邻区间之间跳过 5 根 bar；比例和 gap 可配置。
每个 split 单独存为按 symbol 分区的 Parquet，数据集不能覆盖已有目录。

连续交易市场使用 `--frequency 1h` 等固定间隔。股票等按交易日历运行的数据可用
`--frequency observed`：以数据源的时间戳并集为交易日历，避免把周末和节假日误当成
缺失行情；各资产仍对齐，个别资产缺失仍为 NaN。这一模式依赖数据源的交易日历完整性，
不能自行辨别“所有资产都漏了一天”和“市场休市”。收益周期、gap 和窗口均按 bar 计。

## 无模型检查

以下两条命令**不会初始化模型或运行 Agent**。合成数据刻意含可预测信号，只验证系统
链路，不能作为发现真实市场 alpha 的证据。

```bash
python -m examples.run_factor_mining prepare --synthetic --dataset /tmp/factor_market
python -m examples.run_factor_mining check --dataset /tmp/factor_market --out /tmp/factor_check
```

`check` 用固定候选经过真实数据适配、环境、验证桥和 BenchmarkManager，输出最终报告。
它与 Agent 实验的输出不能混用；重新运行请用新目录，或 `--resume` 读取已有结果。

## 启动研究

**只有显式 `run` 子命令会调用模型。** 当前实现验证不需要执行这一步。
运行环境须安装 Linux bubblewrap；隔离不可用时直接失败，不降级为全盘可读。
模型名称、凭据沿用项目 model 模块。

```bash
python -m examples.run_factor_mining run \
  --dataset /data/factor_market --out /data/factor_study \
  --cfg-options model_name=llm_hub/claude-opus-5
```

只挖因子时添加 `--goal factors`。修改轮次和阈值使用配置或 `--cfg-options`：

```bash
python -m examples.run_factor_mining run \
  --dataset /data/factor_market --out /data/factor_study_v2 \
  --cfg-options max_rounds=4 research_protocol.periods_per_year=252 \
    research_protocol.cost_bps=8 research_protocol.validation_budget=10
```

年化周期必须与行情频率、市场交易日历一致：默认 8766 是 365.25×24 的连续小时市场；
日频股票可按研究约定设为 252。阈值、数据指纹在创建 study 时冻结，后续不能放宽后
覆盖原实验。这里是研究回测，无下单、交易所账户接入或自动实盘执行。

## 研究与评测协议

1. 因子 Agent 读取训练数据描述、算子和现有因子库，批量生成并测试不同假设。
2. 验证先查结构重复，再查逐资产数值相关；因子必须在每个资产、每个收益周期上满足
   样本数、覆盖率和有方向的 RankIC，并满足 train → valid 保留率，才进入版本化因子库。
3. 策略 Agent 只引用已入库的因子名。先做训练回测，再验证最佳方案；失败时保存
   `coverage_gap / weak_signal / generalization_failure` 诊断。
4. 新一轮因子引用 `triggered_by_gap`，仍经过相同标准。`run.json` 保留每轮因子、诊断
   和策略质量，因子报告保留来源 id，策略报告保留 `used_factors`。
5. 验证达标、验证额度耗尽、连续两轮无明显改进或达到轮次上限时结束研究。
   未达标不会伪造成功。评分依据固定阈值，不使用 LLM judge。
6. BenchmarkManager 冻结提交、关闭验证桥，再运行最终 test。相同提交重读缓存结果，
   不重新看 test；不同提交被拒绝。最终测试中途失败会保留“已消耗”标记，不开放再次调参。

验证额度默认整个 study 共 8 次，每次至多 8 个因子或 1 个策略。配额在评测前持久化，
重启和请求重放不能重置。环境只拿到指标和检查结果，不拿到 valid/test 原始行情或序列。
默认 Agent 只允许研究环境和完成工具，无 Bash、任意文件读取、连接器或进化工具。
研究进程只挂载训练工作目录、自己的日志和只读运行代码，私有数据与评测账本留在宿主机。

## 回测口径与边界

- `close[t]` 产生信号，`open[t+1]` 建仓，以之后的 open-to-open 收益计损益，避免同根成交。
  因子未来收益标签也采用相同成交口径，并在各 split 内单独构建。
- 支持排序、阈值、线性仓位及 long-only；总绝对目标仓位不超过 1。成本包含手续费、
  滑点、按价格漂移调整后的换手，以及末尾平仓。成本按扣费前目标权重近似计算。
- stateful 模式提供有限的止损/冷却策略：收盘观察止损，下一根开盘退出。不是任意 Python
  策略执行器，不模拟盘中止损触价。训练收益曲线和最终测试曲线可导出 Parquet。
- 数据缺失时不伪造持仓收益，持仓需要的价格缺失使检查失败。初始资本计入回撤峰值。
- 指标包含逐资产 IC / RankIC / 分块 RankICIR、截面 RankIC、覆盖率，以及策略的年化收益、
  Sharpe、Sortino、最大回撤、Calmar、换手和成本。无定义的比率输出 null，不冒充达标。
- 不包含交易所资金费率、融资借券、市场冲击、容量约束或公司行动自动调整；输入数据需由
  研究者明确这些口径。采用固定持出集和有限验证，未实现组合式交叉验证或多重检验校正。

时间移位与缺失值语义参考 [pandas shift](https://pandas.pydata.org/docs/reference/api/pandas.DataFrame.shift.html)
和 [pandas pct_change](https://pandas.pydata.org/docs/reference/api/pandas.DataFrame.pct_change.html)；
时间顺序和 gap 的设计可对照 [TimeSeriesSplit](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html)。

## 输出与恢复

```text
study/
  run.json                       # 轮次、停止原因、状态与最终统计
  result.json                    # 最终 Task/EvaluationResult
  benchmark/
    evaluations.json             # 最终评分账本
    study/
      contract.json              # 冻结数据指纹、阈值与引擎版本
      state.json                 # 权威配额、因子、策略和诊断
      factor_library.json        # 入库因子及 train/valid 验证证据
      FACTOR-LIBRARY.md           # 可读因子库契约
      reports/                   # 每次验证的 JSON + Markdown
      final_report.json/.md      # 最终测试证据
      test_equity.parquet        # 策略最终测试曲线
    tasks/.../workspace/
      train/                     # Agent 可见的唯一行情 split
      study.json                 # 公开任务协议
      bridge/                    # 验证请求和摘要响应
      reports/                   # 训练诊断、训练曲线
  runtime/                       # 仅 run 模式创建：每轮 Agent 日志、轨迹和响应
```

用相同命令加 `--resume` 恢复；已完成实验只返回已有结果，已冻结但尚未评分的提交直接
进入评测。中断在轮次内部时可能重新执行该轮，但已使用的验证额度与已入库因子不会丢失。
SIGTERM/SIGINT 会终止当前隔离进程并关闭验证桥，保留所有研究证据。
