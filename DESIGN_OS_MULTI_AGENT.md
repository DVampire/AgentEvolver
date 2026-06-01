# AgentOS 设计文档：基于操作系统思想的 Multi-Agent 架构演进

> 作者：设计讨论文档  
> 日期：2026-06-01  
> 基于代码版本：AgentEvolver / AgentOS 当前 main 分支

---

## 一、现状分析：当前架构与 OS 概念的对应关系

在阅读完整项目代码后，可以发现 AgentEvolver 已经在很多地方自然地收敛到了操作系统的经典设计模式。以下是当前代码与 OS 概念的对应关系：

| OS 概念 | 当前实现 | 代码位置 |
|---|---|---|
| **进程 (Process)** | `Agent` 实例 | `src/agent/types.py` |
| **进程调度器** | `RuntimeManager` (spawn/stop/invoke) | `src/runtime/server.py` |
| **进程控制块 (PCB)** | `AgentRef` (name, status, inbox, pump_task) | `src/runtime/types.py` |
| **消息队列 / IPC** | `AgentRef._inbox` (asyncio.Queue) | `src/runtime/types.py` |
| **init 进程 / 内核** | `MetaAgent` (orchestrator) | `src/agent/actor/meta_agent.py` |
| **作业调度器 (Job Scheduler)** | `TaskManager` (两级优先队列) | `src/task/server.py` |
| **系统调用接口** | `ToolManager` (bash, file, git...) | `src/tool/server.py` |
| **共享库 (Shared Library)** | `SkillManager` (SOP workflows) | `src/skill/server.py` |
| **文件系统** | `MemoryManager` (per-session 记忆) | `src/memory/` |
| **内核钩子 / 中断** | `HookManager` (lifecycle hooks) | `src/hook/server.py` |
| **访问控制 (ACL)** | `PermissionManager` (read_only / workspace_write / danger) | `src/permission/server.py` |
| **包管理器** | `VersionManager` (semver, restore) | `src/version/server.py` |
| **动态链接器** | `dynamic_manager` (动态类加载) | `src/dynamic/` |
| **进程间信号** | `SubtaskDoneMessage / EscalationMessage` | `src/agent/actor/meta_agent.py` |

### 1.1 当前执行模型

```
用户请求
  │
  ▼
MetaAgent.on_start()          ← 类比：init 进程接收 systemd 服务启动
  │
  ├─ LLM 生成 PlanRound        ← 类比：OS 将程序分解为进程/线程组
  │
  └─ _dispatch():
       ├─ asyncio.create_task(run_subtask A)  ← 类比：fork()
       ├─ asyncio.create_task(run_subtask B)  ← 类比：fork()
       └─ asyncio.create_task(run_subtask C)  ← 类比：fork()
              │
              ▼
         runtime_manager.invoke(sub_agent)
              │
              ├─ spawn(): 创建 AgentRef + pump task   ← 类比：进程表条目
              ├─ ask(TaskMessage)                     ← 类比：execve()
              └─ stop(): 销毁 pump                   ← 类比：waitpid()
```

### 1.2 Hook 系统：类比内核中断/驱动

当前 `HookManager` 拦截以下生命周期事件，与 OS 中断/钩子对应：

```
HookEvent.PRE_MESSAGES   → 类比：读系统调用前的 seccomp 过滤
HookEvent.PRE_ACTION     → 类比：write 系统调用前的 LSM hook
HookEvent.POST_ACTION    → 类比：系统调用返回时的 audit 日志
HookEvent.ON_START       → 类比：进程创建时的 exec hook
HookEvent.ON_STOP        → 类比：进程退出时的 atexit / signal handler
HookEvent.ON_ESCALATE    → 类比：进程向父进程发送 SIGCHLD / 请求特权提升
```

---

## 二、现有架构的核心局限性

> **关于进程层次的设计决策**：当前 MetaAgent → SubAgent 是单层扁平结构，SubAgent 不能再嵌套 MetaAgent。这是有意为之——MetaAgent 通过**多轮规划（Round）+ 轮内并行**来处理复杂任务，上一轮的结果驱动下一轮的计划，逻辑上等价于多层级但更可控、更易 trace、失败传播路径更清晰。递归进程树会让计划变得不透明并使 Memory 聚合复杂化，不引入。

> **关于 Memory 的设计决策**：每个 Agent 按 task_id 维护自己的记忆，执行隔离和历史追踪已经足够。"分层存储"是过度设计。如果未来需要跨 task 的项目级知识积累，可作为独立功能扩展，不是现有架构的缺陷。

> **关于进化闭环的设计决策**：MetaAgent 本身是 LLM 驱动的决策者，可以在任意 round 里调用 evaluator/optimizer/generator，进化时机由 MetaAgent 自主判断，不需要额外的自动触发机制。

> **关于分布式执行的设计决策**：Agent 的瓶颈是 LLM API 调用（I/O 密集型），asyncio 并发模型已经足够；MetaAgent 轮内并行本质上是多个 `asyncio.create_task`，真正的计算量在 LLM provider 侧。除非引入本地多机 GPU 推理，否则单进程 asyncio 不是瓶颈。

---

## 三、OS 启发的设计改进方案

### 方案 A：Constraint 模块（运行时约束）

**灵感来源**：Linux CGroups 资源限制

**核心思想**：将"运行限制"从 Agent 内部的硬编码参数（`max_steps`）提取为独立的可组合约束对象。Agent 持有一个 `constraints: List[Constraint]` 列表，空列表即无限制；每个 `Constraint` 子类封装一种维度的检查逻辑。

#### 模块结构

```
src/constraint/
├── types.py               # Constraint 基类、ConstraintContext、ConstraintResult
├── default/
│   ├── step_constraint.py    # 步骤数限制
│   ├── token_constraint.py   # LLM token 用量限制
│   └── wall_time_constraint.py  # 挂钟时间限制
├── server.py              # ConstraintManagerServer + 全局 constraint_manager 单例
└── __init__.py
```

#### 核心类型

```python
# src/constraint/types.py

class ConstraintContext(BaseModel):
    """每次 check 时传入的当前运行状态快照。"""
    task_id: str
    agent_name: str
    step_number: int = 0
    tokens_used: int = 0
    elapsed_sec: float = 0.0

class ConstraintResult(BaseModel):
    violated: bool = False
    constraint_name: str = ""
    reason: str = ""

class Constraint(BaseModel):
    name: str
    enabled: bool = True

    async def check(self, ctx: ConstraintContext) -> ConstraintResult:
        """子类实现：返回是否违反约束。"""
        return ConstraintResult(violated=False)
```

#### 三种内置约束

```python
# StepConstraint：替代各 Agent 内部硬编码的 max_steps
class StepConstraint(Constraint):
    name: str = "step_constraint"
    max_steps: int = 30

    async def check(self, ctx: ConstraintContext) -> ConstraintResult:
        if ctx.step_number >= self.max_steps:
            return ConstraintResult(
                violated=True, constraint_name=self.name,
                reason=f"步骤上限：{ctx.step_number}/{self.max_steps}",
            )
        return ConstraintResult(violated=False)

# TokenConstraint：累计 LLM token 用量
class TokenConstraint(Constraint):
    name: str = "token_constraint"
    max_tokens: int = 10_000_000

    async def check(self, ctx: ConstraintContext) -> ConstraintResult:
        if ctx.tokens_used >= self.max_tokens:
            return ConstraintResult(
                violated=True, constraint_name=self.name,
                reason=f"Token 上限：{ctx.tokens_used}/{self.max_tokens}",
            )
        return ConstraintResult(violated=False)

# WallTimeConstraint：从第一步开始计时
class WallTimeConstraint(Constraint):
    name: str = "wall_time_constraint"
    max_seconds: float = 300.0

    async def check(self, ctx: ConstraintContext) -> ConstraintResult:
        if ctx.elapsed_sec >= self.max_seconds:
            return ConstraintResult(
                violated=True, constraint_name=self.name,
                reason=f"时间上限：{ctx.elapsed_sec:.0f}s/{self.max_seconds:.0f}s",
            )
        return ConstraintResult(violated=False)
```

#### ConstraintManager（按 task_id 维护状态）

每个 Agent 的 `__call__` 都会生成一个 `task_id`，并将其透传进 `_think_and_act`。`constraint_manager` 以 `task_id` 为 key 维护每次任务的运行状态：

```python
# src/constraint/server.py

class ConstraintManagerServer:
    # task_id → _SessionState
    _sessions: Dict[str, _SessionState]

    def start_session(task_id, constraints, agent_name): ...  # 第一步自动调用
    def record_tokens(task_id, n): ...                        # model_manager 返回后调用
    async def check(task_id, step_number) -> Optional[ConstraintResult]: ...
    def end_session(task_id): ...                             # done=True 时自动清理

constraint_manager = ConstraintManagerServer()
```

#### Agent 集成（只改基类）

`Agent.__init__` 增加 `constraints: List[Constraint] = []` 参数。
所有约束检查和 token 记录统一集中在基类的 `_think_and_act` 中，**不需要修改任何子类**：

```python
# src/agent/types.py — _think_and_act 新增逻辑（伪代码）

async def _think_and_act(self, messages, task_id, step_number, ctx, **kwargs):
    # 1. 首次调用时自动注册 session
    if self.constraints and not constraint_manager.has_session(task_id):
        constraint_manager.start_session(task_id, self.constraints, self.name)

    # 2. 步骤开始前检查约束
    if self.constraints:
        violation = await constraint_manager.check(task_id, step_number)
        if violation and violation.violated:
            return {"done": True, "result": violation.reason, ...}

    # 3. 调用 LLM（现有逻辑）
    llm_response = await model_manager(...)
    # 4. 记录本步 token 消耗（利用 LLMResponse.usage）
    if self.constraints and llm_response.usage:
        constraint_manager.record_tokens(task_id, llm_response.usage.total)
    think_output = llm_response.extra.parsed_model

    # ... 现有工具调用逻辑 ...

    # 5. 任务完成时自动清理 session
    if done and self.constraints:
        constraint_manager.end_session(task_id)

    return {"done": done, ...}
```

#### 使用示例

```python
# 配置文件中为某个 Agent 挂载约束
from src.constraint.default import StepConstraint, TokenConstraint, WallTimeConstraint

code_agent = CodeAgent(
    base_dir=...,
    constraints=[
        StepConstraint(max_steps=20),
        TokenConstraint(max_tokens=30_000),
        WallTimeConstraint(max_seconds=180),
    ],
)
```

---

### 方案 B：虚拟上下文窗口（Virtual Context Window）

**灵感来源**：虚拟内存 + 分页系统

**核心思想**：将 LLM 的上下文窗口视为"物理内存"，引入"虚拟上下文"抽象，让 Agent 感知自己有"无限"历史，而框架负责将其映射/压缩到有限的物理上下文中。

#### 当前现状

当前 `Agent._get_messages()` 直接将所有上下文塞入 prompt，没有管理"上下文溢出"。`FileSystemMemory` 提供部分解决，但不系统。

#### 设计：上下文页面管理器（Context Page Manager）

```
虚拟上下文空间（无限）：
┌─────────────────────────────────────────────────────────────┐
│ [系统提示] [长期记忆页] [工作记忆页] [当前步骤上下文页]...  │
└─────────────────────────────────────────────────────────────┘
                    ↓ 页面调度（按重要性/时间）
物理上下文窗口（有限，如 200K tokens）：
┌──────────────────────────────────────────┐
│ [系统提示][热页面 x3][当前步骤][工具输出] │
└──────────────────────────────────────────┘
```

**页面类型**：
- **固定页（Pinned）**：系统提示、PROJECT.md — 永不换出
- **热页（Hot）**：最近 N 步的 memory — 常驻
- **冷页（Cold）**：早期历史 — 压缩后存入 FileSystemMemory，按需调入
- **工具输出页**：单次工具调用结果 — 用完即弃

**实现**：在现有 `_get_messages()` 中引入 `ContextPageManager`：

```python
# 在 Agent._get_messages() 末尾，调用 PRE_MESSAGES hook 之前
page_manager = ContextPageManager(budget_tokens=config.max_tokens * 0.9)
messages = await page_manager.fit(messages, session_id=ctx.id)
```

`ContextPageManager.fit()` 负责：
1. 计算各页 token 数
2. 按优先级驱逐冷页（先压缩到摘要，再存入 memory）
3. 返回可以放入 LLM 的 message 列表

这利用了现有的 `memory_manager` 和 `hook_manager`（`token_count` hook），不需要新增大量基础设施。

---

### 方案 C：增强型 Agent IPC（进程间通信）

**灵感来源**：Unix IPC（管道、共享内存、信号、套接字）

**当前现状**：Agent 间通信只有两种：
1. MetaAgent → SubAgent：通过 `TaskMessage`（单向任务分发）
2. SubAgent → MetaAgent：通过 `SubtaskDoneMessage / EscalationMessage`（完成/求助）

缺少：
- Peer-to-peer Agent 通信（Agent A 直接调用 Agent B）
- 广播（一个事件通知所有 Agent）
- 共享状态（多 Agent 读写同一状态）

#### 设计：四种 IPC 原语

**1. Agent 管道（Pipe）**：A 的输出直接作为 B 的输入，无需 MetaAgent 中转

```python
# MetaAgent 规划时可以声明管道关系
class PipelineRound(BaseModel):
    stages: List[TaskSpec]  # 顺序执行，前一个的 result 作为下一个的 task
```

**2. Agent 信号（Signal）**：单向通知，不需要回复

```python
class SignalMessage(BaseMessage):
    signal_type: str  # e.g., "RELOAD_TOOLS", "CANCEL_SUBTASKS"
    data: Dict[str, Any] = {}

# runtime_manager.broadcast(signal) → 发给所有 running refs
```

**3. 共享工作空间（Shared Memory）**：通过现有的文件系统实现

```python
class SharedWorkspace(BaseModel):
    session_id: str
    path: str  # work_dir 下的共享目录
    
# 现有 ctx.work_dir 已经是共享的（MetaAgent 将其传递给所有子 Agent）
# 可以在此基础上增加文件锁协议，避免并发写冲突
```

**4. Escalation 升级为双向 RPC**：当前 `EscalationMessage` 已经有 `reply_future`，这实际上已经是 RPC。可以泛化为通用的 AgentRPC：

```python
class AgentRPCRequest(BaseMessage):
    method: str
    args: Dict[str, Any]
    # reply_future 继承自 BaseMessage，已有

# 任意 Agent 可向父 Agent（通过 parent_ref）发起 RPC
```

---

### 方案 D：能力基础安全模型（Capability-Based Security）

**灵感来源**：Capsicum / seL4 能力安全模型

**当前现状**：`PermissionManager` 是基于 Agent 名称的粗粒度控制（`read_only / workspace_write / danger_full_access`），是 ACL 模式，不是能力模式。

问题：
- 权限与 Agent 名称绑定，动态生成的 Agent 权限难以管理
- 没有权限传播规则（父 Agent 给子 Agent 授权的机制不明确）
- 工具调用没有参数级别的权限检查（只检查操作类型，不检查操作目标）

#### 设计：基于 Capability Token 的权限系统

```python
class Capability(BaseModel):
    token: str = Field(default_factory=make_id)  # 不可伪造的令牌
    operations: List[str]   # 允许的操作集合，e.g., ["bash:read", "file:write"]
    paths: List[str]        # 允许访问的路径模式，e.g., ["/tmp/*", "src/tool/extended/*"]
    expires_at: Optional[datetime] = None
    delegatable: bool = False  # 是否可以转授给子 Agent

class AgentContext(BaseContext):
    capabilities: List[Capability] = []  # 当前 Agent 持有的能力集合
```

**传播规则**：MetaAgent 在 `_run_subtask()` 中，只将必要的能力子集传递给子 Agent：

```python
# MetaAgent 为子 Agent 裁剪能力
child_capabilities = [
    cap for cap in parent_ctx.capabilities
    if cap.delegatable and task_spec.name in cap.allowed_agents
]
child_ctx = AgentContext(capabilities=child_capabilities, ...)
```

**工具端验证**：`ToolContextManager` 在执行前检查 `ctx.capabilities` 是否包含所需权限：

```python
# src/tool/context.py 中
def _check_capability(self, tool_name: str, op: str, target: str, ctx: ToolContext):
    for cap in ctx.capabilities:
        if op in cap.operations and any(fnmatch(target, p) for p in cap.paths):
            return True
    raise PermissionError(f"No capability for {op} on {target}")
```

---

### 方案 E：持续进化循环（Continuous Evolution Loop）

**灵感来源**：操作系统自动更新 + CI/CD + 演化算法

**当前现状**：Evaluate → Generate/Optimize 是手动触发的，没有自动感知系统状态并触发进化的机制。

#### 设计：进化监控守护进程（Evolver Daemon）

类比操作系统中的 `cron`/`systemd timer`：后台持续监控系统健康状态，当工具/Agent 表现下降时自动触发进化任务。

```
┌─────────────────────────────────────────────────────┐
│                   EvolverDaemon                     │
│                                                     │
│  ┌─────────────┐  ┌─────────────┐  ┌────────────┐  │
│  │  Collector  │  │  Analyzer   │  │ Dispatcher │  │
│  │             │  │             │  │            │  │
│  │ 收集 hook   │→ │ 计算失败率  │→ │ 提交进化   │  │
│  │ 的 POST_AC  │  │ 延迟分布    │  │ 到 Task    │  │
│  │ TION 事件   │  │ 对比历史版本│  │ Manager    │  │
│  └─────────────┘  └─────────────┘  └────────────┘  │
└─────────────────────────────────────────────────────┘
```

**触发条件配置**：

```python
class EvolutionTrigger(BaseModel):
    entity_type: str        # "tool" / "agent" / "skill"
    entity_name: str
    metric: str             # "failure_rate" / "avg_latency" / "token_per_success"
    threshold: float        # 阈值
    window_n: int = 100     # 最近 N 次调用
    cooldown_sec: int = 3600  # 触发后冷却时间
```

**利用现有的 TaskManager**：进化任务提交为 `TaskCategory.EVOLVER`，已有的 per-entity throttle 防止重复进化：

```python
# EvolverDaemon 检测到 bash_tool 失败率 > 20%
await task_manager.submit(
    content="Optimize bash_tool: failure rate is 23% over last 100 calls",
    category=TaskCategory.EVOLVER,
    entity_key="tool/bash_tool",
    priority=TaskPriority.LOW,
)
```

**A/B 版本验证**：优化完成后，不立即全量切换，而是进行影子测试：

```python
class ShadowTestConfig(BaseModel):
    baseline_name: str   # 当前版本（e.g., bash_tool v1.2.3）
    candidate_name: str  # 优化版本（e.g., bash_tool v1.2.4）
    traffic_ratio: float = 0.1  # 10% 流量路由到候选版本
    min_calls: int = 50         # 最小采样量
    success_threshold: float = 0.95  # 候选版本通过门槛
```

---

### 方案 F：Agent 命名空间与多租户隔离（Namespace Isolation）

**灵感来源**：Linux Namespaces (pid, mount, net, ipc, user)

**核心思想**：将"全局单例管理器"改造为支持命名空间的版本，同一套框架可以同时运行多个隔离的 Agent 系统（不同用户、不同项目）。

#### 设计

```python
class AgentNamespace(BaseModel):
    id: str = Field(default_factory=make_id)
    name: str
    owner: str
    
    # 每个 namespace 有自己的管理器实例，而非全局单例
    tool_manager: ToolManagerServer
    agent_manager: AgentManagerServer
    skill_manager: SkillManagerServer
    memory_manager: MemoryManagerServer
    
    # 命名空间级别的资源配额
    quota: NamespaceQuota
```

**改造最小化影响**：不需要修改 Agent 代码，只需在 `AgentContext` 中增加 `namespace_id`，各 Manager 的 `__call__` 方法根据 `namespace_id` 路由到对应的子实例：

```python
# src/tool/server.py
async def __call__(self, name, input, ctx=None, **kwargs):
    ns_id = ctx.namespace_id if ctx else "default"
    ns_manager = self._namespace_managers.get(ns_id, self._default_manager)
    return await ns_manager(name, input, ctx=ctx, **kwargs)
```

---

## 四、优先级与实施路线图

根据对现有代码的影响范围和实现复杂度，建议按以下顺序推进：

### 第一阶段（低侵入性，高收益）

| 方案 | 关键改动 | 预期收益 |
|---|---|---|
| **方案 A：资源配额** | 扩展 `token_count` hook + `AgentQuota` 模型 | 防止 Agent 失控，降低 LLM 成本 |
| **方案 B：上下文页面管理器** | 在 `_get_messages()` 后插入 `ContextPageManager` | 解决长任务上下文溢出 |
| **方案 E：进化触发器** | 在 `hook_manager.POST_ACTION` 中收集失败统计，后台 daemon 触发 | 自动持续改进 |

### 第二阶段（中等侵入性）

| 方案 | 关键改动 | 预期收益 |
|---|---|---|
| **方案 C：IPC 增强** | 增加 `PipelineRound` + `SignalMessage` + 泛化 EscalationMessage | 支持更复杂的多 Agent 协作 |
| **方案 E：A/B 版本测试** | 在 `tool_manager.__call__()` 中增加流量分配逻辑 | 安全地进化而不影响生产 |

### 第三阶段（高侵入性，架构级改造）

| 方案 | 关键改动 | 预期收益 |
|---|---|---|
| **方案 D：能力安全** | 重构 `PermissionManager` + `AgentContext` | 细粒度、可传播的权限控制 |
| **方案 F：命名空间** | 将全局单例改为 namespace-aware | 真正的多租户支持 |
| 分布式执行 | 引入 Ray/Celery 替代 asyncio.create_task | 横向扩展到多机 |

---

## 五、核心设计原则

参考 OS 设计的几个经典原则，建议在 AgentEvolver 的后续演进中坚守：

### 5.1 策略与机制分离（Policy vs Mechanism）

**好的例子（当前已做到）**：`HookManager` 只提供拦截机制，具体策略由各 Hook 实现类决定。

**待改进**：`MetaAgent` 的调度策略（按轮次、并发数等）目前硬编码在 `_dispatch()` 中，应该抽出为可配置的调度策略类。

### 5.2 最小权限原则（Principle of Least Privilege）

当前子 Agent 通过 `AgentContext.permission_mode` 设置权限，但这是全局的。应该精细到：**每个子任务只获得完成该任务所需的最小权限**。

### 5.3 Fail-Fast + 自动恢复

当前失败行为：子 Agent 失败 → MetaAgent 收到 `SubtaskFailedMessage` → 重新规划。这已经是合理的设计。可以在 `SubTaskRecord` 上增加轻量的 `max_retries` / `retry_count` 字段，让 MetaAgent 在重新规划前先原地重试 N 次，而不必每次都走完整的 LLM think 周期，降低重试开销。

### 5.4 可观测性（Observability）

当前 `TraceManager` 已有基础。建议对齐 OS 的三大可观测性支柱：
- **Logs**：已有（`logger`）
- **Traces**：已有（`trace_manager`）
- **Metrics**：缺失 — 需要增加 Agent 调用次数、成功率、延迟分布等指标

---

## 六、补充：AgentOS 模式（Bus 模式）的角色

从 `AgentOS` 的配置来看（`mode: bus`，`configs/bus.py`），AgentOS 已经在向"总线架构"演进，这本质上是 **消息总线 (Message Bus)** 模式，对应 OS 中的 **系统总线**。

建议在 AgentEvolver 中吸收这个思路：

```
当前：MetaAgent → (直接 invoke) → SubAgent
目标：MetaAgent → AgentBus → SubAgent
                    ↑
                  [路由规则 / 负载均衡 / 重试策略 / 审计日志]
```

`AgentBus` 成为所有 Agent 通信的统一入口，解耦 MetaAgent 对具体 Agent 实现的依赖，同时提供统一的可观测性切面。

---

## 七、总结

AgentEvolver 已经自然地实现了操作系统中许多核心概念的 Agent 版本。单层 MetaAgent → SubAgent 的扁平结构是有意的设计选择，通过多轮规划保持可控性。在此基础上，最关键的几个演进方向是：

1. **从协作式调度 → 资源配额**（防止失控，方案 A）
2. **从固定上下文 → 虚拟上下文窗口**（分页管理，方案 B）
3. **从手动进化 → 自动进化闭环**（进化 daemon，方案 E）
4. **从粗粒度 ACL → 细粒度能力模型**（capability，方案 D）
5. **从全局单例 → 命名空间隔离**（多租户，方案 F）

建议按阶段逐步推进，每个方案都尽量利用现有的基础设施（HookManager、TaskManager、RuntimeManager）而不是重写。
