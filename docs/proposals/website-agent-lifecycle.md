# WebsiteBuilderAgent 职责与调用边界

`WebsiteBuilderAgent` 只保留角色声明。上一次把 `completion_blocker` 提升为基类接口是不正确的：部署验收可复用，不代表它属于每个 Agent 的退出生命周期。现在已经删除该接口、循环中的隐式完成否决和重复否决计数，基类也不再直接调用 deployment manager 或 extension manager。

## 正确的调用链

```text
启动脚本：声明任务、附件、订阅者与可选部署要求
   ↓
Agent.prepare_task：公共输入投影、runtime 订阅者启动
   ↓
Agent 思考和工具调用
   ├─ deploy_tool → deployment manager：发布操作、发布要求与验收状态
   ├─ job__output → runtime 报告；读取事实通知 deployment manager
   └─ adoption_tool → extension manager：注册、版本、证据、采用与回滚
   ↓
Agent 根据工具结果决定继续处理，或返回完成/部分结果
   ↓
done_tool / 最终回答：结束本次执行，不暗中访问业务 manager
```

## 逐项归属

| 原方法 | 处理 |
|---|---|
| `feedback_progress` | 发布和反馈事实由 deployment manager 维护，`deploy_tool status` 可查询；JobEnvironment 展示当前轮次并记录报告读取事实。删除多余的 plan 哈希副本，计划走统一 plan 机制。 |
| `_evolution_entry` | 删除从工具参数抄录决策的 Actor 方法和 `act` 覆写。采用决策由 adoption tool 调用 extension manager 验证并持久化。 |
| `iteration_budget` | 删除网站专用的 36/30 步提醒及计数配置；保留公共资源预算与已有停滞检查。 |
| `completion_blocker` | 从 Actor、Agent 基类及执行循环删除。发布完整性变成 deployment manager 的状态查询，通过 `deploy_tool(action="status")` 返回，不再否决 Agent 退出。 |
| `prepare_task` | 基类仅处理通用任务输入和 runtime 订阅声明。角色话术与模型由 example 声明；部署 policy 在调用 deploy tool 时交给 manager 解释和绑定，基类不解释。 |

## 部署状态不等于 Agent 退出条件

`deploy_tool(action="status")` 返回 `configured`、`ready`、`reason`、发布次数、订阅者绑定和具体反馈轮次。

- 发布操作继续校验自身的前置条件，如预览源码版本和先前反馈读取情况。
- 状态检查保留发布次数、源码 revision、事件分发、独立验收和完整报告读取要求。
- 查询成功与验收通过是两件事：正常查询可以返回 `success=true, ready=false`。
- 未声明部署要求时返回 `configured=false, ready=null`，不把“没有要求”包装为通过验收。
- Agent 在交付前查询状态，继续处理未满足要求，或如实报告阻塞；`done_tool` 只结束执行，不能证明产品已经验收通过。

移除了扩展基线快照和“所有变化组件必须关闭才能退出”的全局检查。扩展的注册、评估、采用、回滚仍然通过 adoption tool 和 extension manager 执行：`keep` 仍要求实际归档版本、当前激活版本和有效 passing evaluation；记录 rollback 意向不会自动执行回滚。失败和缺失证据在工具调用时返回。

## 上下文与状态

通用准备阶段保留已过滤的 `task_manifest` 与共享的 `task_state`；它们不包含私有订阅 brief，也不把部署业务放进基类。Deployment manager 在工具首次使用时建立状态，后续调用不会重置已有发布历史。ToolContext 转换共享该状态；子 Agent 通过 runtime 的继承白名单获得独立上下文，不继承父任务的发布和验收状态。

## 验证边界

使用离线测试、模拟工具结果和假部署服务，验证普通 Agent 的文字结束与 done_tool 结束不再访问业务 manager；业务错误仍返回模型；部署状态跨多次工具上下文转换保留；反馈轮次、验收和采用校验继续通过各自工具/manager 测试。

没有启动真实 Agent、demo 或模型调用。历史 c7556070 中的上下文压缩失败、验收执行异常与产品 FAIL 混用，以及失败验收阻挡修复预览的问题，仍需独立修复。删除基类的完成否决只解决其中退出路径的越层耦合，不代表完整发布恢复协议已经修好。
