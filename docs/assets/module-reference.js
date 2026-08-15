/* Source-aligned detail for the 56 top-level modules. The card already states the
 * short purpose; each entry adds runtime placement, boundary and public surface. */
(function () {
  const D = {
    agent:['Agent · ProceduralAgent · AgentConfig · AgentContext · AgentManagerServer','一次任务经 agent_manager 进入 Agent；它构造模型实际看到的消息、调用按角色选择的模型、分发 action、执行 hooks，最后返回统一 Response。它负责“思考—行动”生命周期，但不负责厂商 SDK、持久任务调度或沙箱隔离。','A task enters through agent_manager. Agent builds effective messages, calls a role-selected model, dispatches actions, applies hooks, and returns a normalized Response. Provider SDKs, durable scheduling, and isolation stay in neighboring modules.'],
    runtime:['RuntimeManager · AgentRef · AgentStatus · TaskMessage · StopMessage · delegate · delegate_background','管理存活 Agent 的引用、邮箱与事件泵：协议消息投递到 AgentRef，Runtime 唤醒并推进目标 Agent，同时记录生命周期状态。委派也在这里：一个 agent 跑另一个，是一个 ref 加一个 job，而不是另一种 agent。它调度 live actor，不管理持久化用户任务。','Owns live agent references, mailboxes, and the event pump. Delegation lives here too: one agent running another is a ref plus a job, not a separate kind of agent. Durable user-task records belong to task.'],
    session:['SessionContext · BaseContext · ensure_session_sandbox · bind_session_roots','定义一次调用共享的身份与项目状态。创建 Session 时分配项目/沙箱目录、绑定 workspace 和 log root、暂存上传文件并写 manifest。Session 可包含 Conversation 和 Task，但不等于其中任何一个。','Defines invocation identity and project state. Creation allocates the project tree, binds workspace/log roots, stages uploads, and writes a manifest; conversations and tasks live inside it.'],
    message:['Message · HumanMessage · SystemMessage · AssistantMessage · ToolMessage · ToolCall','统一文本、图片、音频、视频、PDF 与原生函数调用的数据结构。Agent 构造 Message，模型适配器翻译成厂商请求，Trajectory 保存实际发送形态；本模块只定义数据，不调用模型或落盘。','Provider-neutral data for text, media, and native function calls. Agents build it, adapters translate it, and trajectories serialize it; this module performs no I/O.'],
    response:['Response · ResponseType','统一 Agent、Tool、Skill、Connector、Environment 的返回信封：success、message、data、files。能力若返回任意对象而非 Response，调用方可能得不到可用 observation。','One result envelope across callable capability families: success, message, data, and files. Returning arbitrary values breaks the observation contract.'],
    queue:['AsyncQueue','提供事件驱动组件复用的异步队列原语，让生产者与消费者解耦并可预测关闭。任务优先级、Agent 邮箱或 Trace 语义仍由各自领域模块定义。','A reusable asynchronous queue primitive with predictable shutdown. Domain meaning—task priority, agent mailbox, or trace semantics—stays with its owner.'],
    protocol:['ProtocolManager · EscalationMessage · MonitorProgressMessage · ControlMessage · QueryMessage','定义经 Runtime 投递的 Agent 间类型化通信，包括委派、升级、进度、控制、查询和发布订阅。它定义消息含义，但不执行接收方或决定任务策略。','Typed agent-to-agent communication over Runtime: delegation, escalation, progress, control, query, and pub/sub. It defines meaning, not execution policy.'],
    model:['ModelManagerServer · ModelConfig · ModelContext · ApiKeyPool · TokenUsage','负责 provider 注册、按角色选模、API key 池、流式增量及统一 token/cost。调用方请求 main、judge 等角色；可观测的应用层负责重试，避免 SDK 隐式重试让 Trace 和 Trajectory 失真。','Owns provider registration, role-based selection, key pooling, streaming, and normalized usage. Retries remain above SDKs so trace and trajectory see every attempt.'],
    prompt:['PromptManagerServer · Prompt · PromptConfig · PromptContext · expand_modules','加载、版本化并渲染 HTML 原生 Prompt 与可复用模块。配置选择名称，渲染结合上下文展开，再由 Agent 组装请求；Prompt 是可审阅资产，不是隐藏的 Python 控制流。','Loads, versions, and renders HTML-native prompts. Rendering expands reusable modules against context; execution decisions remain in Agent or Workflow.'],
    memory:['MemoryManagerServer · Memory · MemoryConfig · ChatEvent · Insight · Summary','把历史加工成长期 Summary/Insight，并检索到后续调用中。它是可复用记忆；Trace 是不可变事实，Trajectory 是训练投影，三者不要混用。','Turns history into durable summaries and insights. Memory is reusable context; trace is immutable evidence and trajectory is a training projection.'],
    trace:['TraceManager · TraceEvent · TraceEventType · derive_messages · surface_events','追加写入带 provenance/confidence 的结构化生命周期事件，并分发给日志、UI 和派生视图。Trace 回答“实际发生了什么”，内容比训练导向的 Trajectory 更完整。','Appends structured lifecycle evidence and fans it out to logs, UI, and derived views. It answers what happened and is intentionally fuller than trajectory.'],
    trajectory:['TrajectoryManagerServer · Trajectory · TrajectoryStep · RLFormat · VerlFormat','TrajectoryHook 记录实际消息、原生 tool call、observation 与 token/cost；结束时写 JSONL，Judge 可随后把 reward 回填到每一步。当前支持加载、SFT 与 VERL 文本级 RL 导出；分词、训练器、checkpoint 与新权重回灌仍在 roadmap。','Captures effective messages, native calls, observations, usage, and late reward. It persists/loads JSONL and exports SFT or VERL-style RL records; training and weight feedback are roadmap work.'],
    tool:['ToolManagerServer · Tool · ToolConfig · ToolContext · clip_output','定义原子 Python 能力，并从签名生成原生 function-calling schema。初始化发现已导出 Tool、按 name 注册并路由调用；多步方法属于 Skill，有状态动作空间属于 Environment。','Atomic Python-backed capabilities with schemas derived from signatures. Multi-step methods belong to Skill; stateful action spaces belong to Environment.'],
    skill:['SkillManagerServer · Skill · SkillConfig · SkillContext','发现以 SKILL.md 为入口的文件系统工作方法，并按需加载 scripts、references、resources 与 examples。Skill 教 Agent“怎么做”，自身通常不是一个原生函数。','Discovers filesystem-backed procedures rooted at SKILL.md and progressively loads supporting resources. A skill teaches how to work; it is not usually a native function.'],
    environment:['EnvironmentManagerServer · Environment · EnvironmentConfig · ActionConfig · ActionResult · ExecutionWorld','定义有状态执行世界，并把每个 Action 暴露成模型能力。后续动作可依赖早先状态，因此创建、状态报告和清理都属于契约；这与通常无状态的 Tool 不同。','Defines stateful worlds whose actions become model capabilities. Later actions may depend on earlier state, so creation, reporting, and cleanup are contract parts.'],
    connector:['ConnectorManagerServer · ConnectorConfig · ConnectorContext','把外部能力提供方（主要是 MCP Server）投影成本地 Action：建立会话、发现动作、转换输入 Schema。它负责协议接入，但不拥有外部服务数据、可用性和授权。','Projects external providers—primarily MCP servers—into local actions by managing sessions, discovery, and schemas. External data and authorization remain external.'],
    plugins:['PluginManagerServer · Plugin · PluginTool · PluginConfig','按服务封装 OpenAI、向量库、搜索、Composio 等多工具集成，并可作为 Canvas 数据源节点。Plugin 是服务级分组，Connector 是远程协议边界，Tool 是原子调用。','Groups multi-tool service integrations and exposes them to Canvas. Plugin is the service grouping; Connector is the remote boundary; Tool is the atomic call.'],
    command:['CommandManagerServer · Command · SkillCommand · CommandContext','统一建模 /help、/registry 等用户控制命令。CLI/TUI 把原始文本交给 Gateway 的 command.execute，再由本模块解析并返回统一结果；普通任务不走这条控制路径。','Models user control commands routed through Gateway command.execute. Natural-language work enters through task/session APIs instead.'],
    knowledge:['KnowledgeManager · KnowledgeContext · RagBackend','对具名知识库提供 BM25、TF-IDF 等可插拔检索：文档入库，查询排序后返回 grounding 上下文。它不决定结果插入 Prompt 的位置，也不代替长期 Memory。','Retrieval over named corpora with pluggable ranking backends. Callers decide prompt placement; durable learned context remains Memory’s concern.'],
    code:['CodeRuntimeServer · GuardedDispatch · CodeRunResult · CodeFailure','在独立解释器执行模型生成的程序，并通过 GuardedDispatch 把能力调用代理回宿主，使一批工具工作只占一个模型回合。子进程不能直接 import 存活框架对象。','Runs model-written orchestration in a child interpreter and brokers calls through GuardedDispatch. Its only authority is the guarded bridge.'],
    sandbox:['SandboxManagerServer · ProjectSandbox · NetworkPolicy · EgressRelay · Ledger','跨 Backend 提供隔离执行、受管进程、staged validation 与 egress policy。资源先写入 Ledger，异常退出后也可回收；具体隔离强度取决于所选 Backend 与策略。','Provides isolated execution, staged validation, egress policy, and a cleanup ledger across backends. Isolation strength depends on the selected backend and policy.'],
    terminal:['TerminalServer · Terminal · TerminalStatus · TerminalBusy · WaitReason','让 PTY Shell 跨工具调用存活，保留目录、环境变量、SSH 或 REPL 状态。create 返回 terminal id，后续 send/read/wait 操作同一进程；它不是一次性命令或 Job 队列。','Keeps a PTY shell alive across calls. It preserves interactive process state and differs from one-shot commands and background jobs.'],
    process:['ProcessManager · Processor · ProcessContext','执行无副作用的数据管线变换：Processor 接收 input 与 params，返回统一 {message,data,files}。文件、网络或环境变更应留在外部，以保持相同输入可复现。','Side-effect-free pipeline transforms returning the canonical message/data/files envelope. Mutation stays outside processors for reproducibility.'],
    kernel:['KernelManagerServer · KernelResult · KernelOutput · Execution · KernelStatus','每个 Project 一个 Jupyter Server，Agent、Science REPL 与 JupyterLab 共用同一 Kernel 和变量历史。它面向有状态科学计算；Code Mode 则隔离模型生成程序。','One Jupyter server per project and one shared kernel across agent, REPL, and notebooks. Code mode uses a separate isolation model.'],
    lsp:['LspServer · LanguageServer · StdioLspProvider · SourceFile · Position · Range','通过 stdio Language Server 查询定义、引用、Hover 类型和 Symbols，并统一不同 Provider 的位置结果。它提供语义事实，但不修改代码，也不能替代测试。','Queries language servers for semantic code facts and normalizes locations. It neither edits files nor replaces tests.'],
    docker:['DockerManagerServer','提供 Sandbox 使用的 Docker Backend 接口，把上层生命周期和执行请求翻译成 Docker 资源操作。跨 Backend 策略、项目校验与统一语义仍属于 Sandbox。','The Docker backend adapter used by Sandbox. Cross-backend policy and staged validation remain in Sandbox.'],
    e2b:['E2BManagerServer','提供 E2B 远程 Sandbox 接入点，返回供执行与清理使用的远程环境 Handle。它是可选 Backend；核心安装不意味着必须配置 E2B。','Optional E2B remote-sandbox adapter returning handles for execution and cleanup.'],
    ide:['IdeManagerServer · IdeInstance','为每个 Gateway Session 启动 OpenVSCode Server Container，直接编辑 Agent 同一 Workspace。它是面向人的界面，刻意不暴露为 Agent 能力。','Runs one browser IDE container per Gateway session against the agent’s workspace. It is intentionally human-facing, not an agent capability.'],
    science:['ScienceManagerServer · Notebook · ComputeStatus','在共享 Jupyter Kernel 上组合 Agent 对话、REPL 与 JupyterLab，三处观察同一真实计算状态。它同样是人类工作台，而非可调用 Agent 能力。','Combines conversation, REPL, and JupyterLab over one real kernel state. It is a human workstation, not an agent capability.'],
    workflow:['WorkflowManagerServer · WorkflowCompiler · WorkflowRuntime · WorkflowDefinition · WorkflowRun','把可审阅 HTML 编译为动态多智能体程序，处理 Step、分支、重试、嵌套 Invocation 与 Evaluation。Workflow 是编排基础设施，不是 Agent 子类，也不必是固定 DAG。','Compiles reviewable HTML into dynamic multi-agent programs with branching, retries, nested invocations, and evaluation. It is not an Agent subtype or necessarily a fixed DAG.'],
    canvas:['CanvasManagerServer · CanvasCompiler · FlowGraph · GraphNode · GraphEdge · Catalog','以 JSON FlowGraph 为事实源的可视编辑器：按 Catalog Port 校验节点与边，编译后在共享 Workflow Runtime 上临时执行。它没有另造第二套执行引擎。','A visual JSON graph editor compiled onto the shared Workflow runtime. It does not introduce a second execution engine.'],
    task:['TaskManager · TaskRecord · TaskStatus · TaskPriority · GoalStore · GoalAuthority','建模持久工作与目标的优先级、状态、类别和 Authority。输入先规范化为 Task，Worker 领取 TaskRecord、执行 Handler 并落盘结果；Session 提供上下文，Runtime 推进 Agent。','Models durable work and goals. Workers claim records and persist results; Session supplies context and Runtime advances live agents.'],
    job:['JobManagerServer · Job · JobStatus · ScheduleError · resolve_due','在后台运行长任务并保存到期提醒。启动立即返回 Job ID，Agent 稍后 inspect/wait/collect，把模型回合留给决策；被委派的子 agent 也登记成 kind=agent 的 Job，所以「什么在跑」只有一个答案。','Runs long work and due reminders in the background. It returns an id immediately. A delegated child is registered here too, as a job of kind agent, so "what is running" has one answer.'],
    plan:['PlanManagerServer · PlanState · action_is_allowed · declaration_of','在人工批准前强制只读与推理。门禁依据能力声明的 mutates:false 或 Permission Mode，而不是工具名字；未声明语义的能力默认拒绝。','Enforces read/reason mode until approval. The gate trusts declared mutation and permission semantics, not capability names.'],
    constraint:['ConstraintManagerServer · Constraint · ConstraintConfig · ConstraintStatus','执行 Step、Token、Wall-time 预算，并把 NORMAL/TIGHT/CRITICAL 剩余额度写给模型，使 Agent 能在硬停止前调整。它限制执行，不决定任务优先级。','Enforces step, token, and wall-time budgets and renders remaining pressure to the model before a hard stop.'],
    extension:['ExtensionManagerServer · Manifest · ManifestComponent · Journal · replay_smoke','管理生成扩展的 Manifest、暂存激活、Promotion Journal 与回放 Smoke Gate。候选在包外注册、评测、晋升或整体回滚；它进化组件，不修改手写 Core，也不训练权重。','Owns staged, versioned extension promotion and rollback with replay smoke gates. It evolves components, not the handwritten core or model weights.'],
    dynamic:['DynamicModuleManager','校验并以受控名称加载生成的 Python Source，再从签名和 Argument Model 推导注册信息与 Function Schema。版本、Smoke Check 与 Promotion 由 Extension 负责。','Loads generated Python under controlled names and derives callable metadata. Extension supplies versioning, smoke checks, and promotion.'],
    version:['VersionManagerServer · VersionInfo · VersionStatus · ComponentVersionHistory','记录可进化实体的 Identity、前序版本、状态与完整谱系；晋升和回滚更新历史但不覆盖旧证据。实际注册事务由 Extension 执行。','Tracks immutable lineage and status for evolvable entities. Extension performs the actual registration transaction.'],
    scope:['Scope · ScopeEntry','把跨多个 Manager 的注册归到一个所有权单元。每次成功注册都记录逆操作，关闭时逆序 Unregister，并报告失败后仍残留的项目。','Groups registrations across managers and removes them in reverse order, reporting anything left installed after failure.'],
    capability:['CapabilitySchemaProvider · CapabilitySchema · SchemaSource · SchemaFormat','统一 Callable Manager 的 get_schema(name, action, format) 自省协议，让调用方无需知道来源是 Tool、Connector 还是 Environment。它标准化 Schema，不负责调用和权限。','A structural get_schema protocol shared by callable managers. It standardizes introspection, not invocation or permission.'],
    benchmark:['BenchmarkManager · Benchmark · BenchmarkConfig · JudgeResult · Stats','加载版本化 Benchmark、执行数据集任务并聚合 Judge 结果；评分还可回填到对应 Trajectory。它衡量系统，但不训练模型，数据解析由 Data 负责。','Runs versioned task sets, aggregates judged results, and can backfill trajectory reward. It measures the system; it does not train it.'],
    data:['DataManager · AIME24Dataset · AIME25Dataset · GPQADataset · GSM8kDataset · HLEDataset · LeetCodeDataset · DeepWebDataset · ProgramBenchDataset','提供 Benchmark 的数据集 Adapter：优先读本地，按需下载快照，把源记录统一成 Task。Judge、Agent 执行和分数聚合不在本模块。','Dataset adapters normalize local or snapshot-downloaded records into benchmark tasks. Execution and judging remain in Benchmark.'],
    gateway:['AgentGateway · GatewaySession · GatewayCommand · GatewayResponse · GatewayEvent · create_websocket_app','是 CLI、TUI、Web UI 的统一版本化后端边界，并统一启停所有 Manager。客户端通过进程内、stdio 或 WebSocket 发送 Command，Gateway 校验 Session、分发并流式返回 Event。','The single versioned backend for CLI, TUI, and Web UI, owning manager lifecycle and command/event transport.'],
    config:['Config · process_general · process_tools · process_environments · process_memory · process_agent · validate_assembly','加载 Python Config 并在启动前校验组装结果。Base 定义路径、模型角色和策略，场景配置继承或覆盖能力名称；Config 只做选择，不亲自注册和执行。','Loads Python configuration and validates the assembled system. It selects components; owning managers register and execute them.'],
    paths:['PathManagerServer · P','用一张表统一声明所有写入路径，只保留生成态 output/ 与共享 extension/ 两个根。Session 绑定后解析到 owner/project/session，防止各模块私造目录。','A single table for every writable path under output and extension roots, resolved into the bound session tree.'],
    port:['PortManager · is_free · os_free_port','注册约定服务端口、分配无冲突 Host Port，并把发现信息写入 output/.runtime。它协调数字，不负责传输认证、健康检查或防火墙。','Allocates and records service ports for discovery. Authentication, health, and firewall policy stay elsewhere.'],
    logger:['Logger · LogLevel · set_session_id · get_session_id','提供带级别、颜色和 Session ID 的关联日志；异步任务无需逐层传 ID 也能写出可归属记录。诊断输出属于 Logger，结构化生命周期事实属于 Trace。','Correlated diagnostic logging across async work. Structured lifecycle truth belongs to Trace.'],
    permission:['PermissionManagerServer · PermissionMode · Operation · PermissionRequest · ValidationResult · PermissionRule','根据 Mode、组合规则、文件大小和二进制保护评估命令/文件意图，在执行前返回允许、拒绝或需批准。它是策略决策层；真正隔离由 Sandbox 提供。','Evaluates command and filesystem intent against policy before execution. It is a decision layer; Sandbox supplies containment.'],
    hook:['HookManagerServer · Hook · HookConfig · HookContext · HookEvent · HookDecision','为 Trace、Trajectory、Compaction、注册和 Promotion 提供有序生命周期拦截点，并用消息契约保护共享 Payload。横切逻辑放 Hook，领域核心仍留在所属模块。','Ordered lifecycle interception for cross-cutting behavior, with shared-payload contract checks. Domain logic stays with its owner.'],
    utils:['make_id · FileLock · assemble_workspace_path · parse_tool_args · TodoStep','收纳路径、锁、并发、命名、字符串、URL、截图、Token 和参数解析等低依赖 Helper。带领域状态或生命周期的功能不应塞进 Utils。','Dependency-light mechanical helpers. Anything with domain state or lifecycle should remain in its owning module.'],
    spill:['SpillManagerServer · SpillStore · SpillRef · SpillSource','超大 Observation 进入模型上下文前先完整写盘，只返回 Locator 与读取提示；Agent 可再取相关片段。它不丢数据，但调用方需要全文时必须跟随引用。','Stores oversized results and returns a locator plus retrieval hint, preserving data while bounding model context.'],
    attachment:['AttachmentManagerServer · ImageAttachment · AttachmentError','模型读图后固定图片字节与 Media Type，并在后续请求重新附加，避免视觉上下文随单步消失。它用于上下文连续性，不是通用文件存储或图片编辑。','Pins image bytes and re-attaches them on later model requests. It provides context continuity, not general storage or editing.'],
    deploy:['DeploymentManagerServer · Deployer · DeployRequest · DeploymentSpec · SiteRecord · SiteStatus','从受控 Source 部署 Web Artifact，探测健康并记录 URL、资源和生命周期。它管理部署产物，不负责编写应用，也不接受任意来源。','Deploys controlled web artifacts and records URL, health, resources, and lifecycle. It does not author applications.'],
    conversation:['ConversationManagerServer · Conversation · UserQuestion · UserAnswer · PendingQuestion','管理 Project 内的对话身份、Transcript 与需要人类输入的 Suspend/Resume Question。Project 包含 Conversation，Conversation 包含 Turn 与 Task，但它不等于单次任务。','Owns dialogue identity, transcripts, and suspend/resume questions inside a project. It is the middle identity layer, not one task run.'],
    visual:['Dependency-free HTML artifact renderers','保存 HTML 原生 Artifact 的纯浏览器 CSS/JS Renderer。Runtime Parser 和 Agent 不执行这些文件，因此呈现层不能偷偷成为运行语义来源。','Browser-only renderers for HTML-native artifacts. Presentation must not become a hidden source of runtime semantics.']
  };

  const cards = [...document.querySelectorAll('.mod')];
  const main = document.querySelector('main');
  if (!cards.length || !main) return;
  const tools = document.createElement('div');
  tools.className = 'module-tools';
  tools.innerHTML = '<input class="module-search" type="search" autocomplete="off"><span class="module-count"></span>';
  main.prepend(tools);
  const input = tools.querySelector('input');
  const count = tools.querySelector('.module-count');
  const isZh = () => document.documentElement.lang.startsWith('zh');

  function render(card) {
    const name = card.querySelector('.mod-h b')?.textContent.trim();
    const item = D[name];
    if (!item) return;
    card.classList.add('has-detail'); card.tabIndex = 0; card.setAttribute('role','button');
    let box = card.querySelector('.module-detail');
    if (!box) { box = document.createElement('div'); box.className = 'module-detail'; card.append(box); }
    const labels = isZh() ? ['运行位置与边界','主要公共接口','源码'] : ['Runtime placement & boundary','Main public surface','Source'];
    box.innerHTML = `<div class="wide"><b>${labels[0]}</b><p>${isZh()?item[1]:item[2]}</p></div><div><b>${labels[1]}</b><p><code>${item[0]}</code></p></div><div><b>${labels[2]}</b><a class="module-link" href="https://github.com/DVampire/AgentEvolver/tree/main/agentevolver/${name}" target="_blank" rel="noopener">agentevolver/${name}/ →</a></div>`;
  }
  cards.forEach(card => {
    render(card);
    const toggle = event => {
      if (event.target.closest('a')) return;
      if (event.type === 'keydown' && !['Enter',' '].includes(event.key)) return;
      if (event.type === 'keydown') event.preventDefault();
      card.classList.toggle('open'); card.setAttribute('aria-expanded', String(card.classList.contains('open')));
    };
    card.addEventListener('click', toggle); card.addEventListener('keydown', toggle);
  });
  function filter() {
    const q = input.value.trim().toLowerCase(); let visible = 0;
    cards.forEach(card => {
      const name = card.querySelector('.mod-h b')?.textContent.trim(); const item = D[name] || [];
      const haystack = [name, card.querySelector('.mod > p')?.textContent, ...item].join(' ').toLowerCase();
      card.hidden = !!q && !haystack.includes(q); if (!card.hidden) visible++;
    });
    main.querySelectorAll('section').forEach(section => { if (section.querySelector('.mod')) section.classList.toggle('family-empty', !section.querySelector('.mod:not([hidden])')); });
    count.textContent = isZh() ? `${visible} / ${cards.length} 个模块` : `${visible} / ${cards.length} modules`;
  }
  function localize() { cards.forEach(render); input.placeholder = isZh()?'按模块、职责或 API 筛选…':'Filter by module, responsibility, or API…'; input.setAttribute('aria-label', input.placeholder); filter(); }
  input.addEventListener('input', filter);
  document.querySelectorAll('.lang button').forEach(button => button.addEventListener('click', localize));
  localize();
})();
