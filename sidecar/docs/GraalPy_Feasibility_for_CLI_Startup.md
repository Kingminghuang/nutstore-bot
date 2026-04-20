## Plan: GraalPy Feasibility for CLI Startup

先不要直接把 PyInstaller 替换成 GraalPy。当前打包 CLI 的主路径已经是 onedir，而不是 onefile；这意味着项目已经避开了 PyInstaller 最重的一类每次解包开销。现有代码显示，感知启动时间不仅来自打包器，还来自 Rust launcher 的 runtime 初始化、Python 进程启动、ACP app 创建、数据库连接与 schema 初始化，以及 provider catalog 等应用层初始化。推荐路线是先做带分层计时的基线测量，再优先处理应用初始化热点；只有当数据证明“打包器/解释器启动”占比足够大，并且关键依赖在 GraalPy 下可运行时，才进入 GraalPy 可行性 spike。

**Steps**
1. Phase 1 - 建立现状基线。分别定义并测量两个目标场景：独立打包 CLI 命令启动，以及桌面端通过 ACP stdio 拉起 sidecar 的首连时间。度量至少区分冷启动与热启动，并把端到端时间拆成 launcher 初始化、payload 进程拉起、ACP initialize 完成、首个业务命令可用几个阶段。
2. Phase 1 - 在现有链路上补最小侵入式计时点。优先复用已有启动链路而不是另写基准程序，在 Rust launcher 与 ACP 连接点记录 runtime 初始化和子进程启动耗时，在 Python 入口和 ACP app 创建点记录 import/建库/服务装配耗时。这个步骤阻塞后续判断，因为没有分层数据就无法判断 GraalPy 的真实收益上限。
3. Phase 2 - 先评估和验证应用层热点，而不是先换打包器。重点检查 provider catalog、litellm 相关导入、数据库/schema 初始化、模板与搜索工具拷贝是否发生在每次启动的关键路径上。若这些环节占比显著，优先设计延迟初始化或一次性初始化策略，因为这类优化无论保留 PyInstaller 还是未来切到 GraalPy 都能直接受益。
4. Phase 2 - 明确当前打包方案的边界条件。现有 packaged CLI 依赖 Rust launcher 按 onedir 布局在 dist/binaries 下查找 payload，并依赖 dist/runtime 下的 templates 与 search-tools 目录。任何 GraalPy 方案都必须满足相同的文件系统契约，或者同步修改 launcher 解析与运行时初始化逻辑；这应被视为迁移范围的一部分，而不是打包脚本层面的单点替换。
5. Phase 3 - 做 GraalPy 可行性 spike，但严格限定为验证而不是迁移。目标是先回答三个问题：GraalPy 能否在当前依赖集下跑通 sidecar CLI 主入口；能否产出与现有 launcher 兼容的可分发形态；实际冷/热启动改善是否超过维护复杂度。这个 spike 应先跑最小命令集，例如 --help、providers list、--acp 初始化握手，再扩展到打包 CLI E2E。
6. Phase 3 - 在兼容性上先做依赖分级。把依赖分成必须立即跑通的启动关键依赖和可延后验证的运行期依赖。第一层至少包括 typer、fastapi、anyio、uvicorn、websockets、agent-client-protocol、smolagents、litellm，以及项目自有 ACP/runtime 代码。官方 GraalPy 文档显示支持单二进制分发，也有较大的包兼容矩阵，但并非所有依赖都明确稳定；例如 anyio、anthropic 等包在官方兼容页并未体现为强阳性信号，因此不能把 GraalPy 当作 PyInstaller 的零风险替换。
7. Phase 4 - 设定决策门槛。若基线显示 PyInstaller/解释器启动只占总启动时间的小头，或者 GraalPy spike 只能带来有限收益但显著增加构建复杂度、包兼容风险和 CI 维护成本，则保留 PyInstaller 并把精力投入应用初始化优化。只有在数据同时满足“启动收益明显”“依赖兼容可控”“产物布局可接入现有 launcher”三个条件时，才进入正式迁移设计。
8. Phase 4 - 若进入正式迁移，再单独设计构建与验证矩阵。需要覆盖 Linux/macOS/Windows 目标平台、现有 dist 目录契约、launcher 对 payload 查找的兼容、runtime 资源准备、以及现有 packaged CLI smoke/E2E 测试。这个阶段不应与 feasibility spike 混在一起，以免在兼容性未证实前提前改动主构建链路。

**Relevant files**
- /home/hqm/nutstore-bot/sidecar/scripts/build_packaged_cli.sh — 当前 packaged CLI 主构建入口；确认 PyInstaller 使用 onedir、产物复制到 dist/binaries、以及 runtime/templates 与 runtime/search-tools 的 staging 逻辑。
- /home/hqm/nutstore-bot/sidecar/scripts/build_pyinstaller_sidecar.sh — 当前 onefile 参考脚本；可用于对比 PyInstaller 形态差异，但不是 packaged CLI 主路径。
- /home/hqm/nutstore-bot/src-tauri/src/bin/nsbot.rs — Rust launcher 入口；关键在 resolve_payload_path 和对 onedir payload 布局的假设。
- /home/hqm/nutstore-bot/src-tauri/src/runtime/launcher.rs — runtime 初始化逻辑；确认 templates/search tools 拷贝和环境变量注入是否进入启动关键路径。
- /home/hqm/nutstore-bot/src-tauri/src/main.rs — 桌面端 ACP stdio 连接点；适合放置 handshake 前后的阶段计时。
- /home/hqm/nutstore-bot/sidecar/src/nsbot_sidecar/cli.py — Python CLI 入口；适合区分命令解析、模板准备、ACP 模式入口和普通 CLI 命令入口的启动成本。
- /home/hqm/nutstore-bot/sidecar/src/nsbot_sidecar/api/acp_stdio.py — ACP stdio 主入口；确认 create_acp_app 与 initialize 前后的时间边界。
- /home/hqm/nutstore-bot/sidecar/src/nsbot_sidecar/api/acp_app.py — ACP app 创建点；这里串起数据库、repositories、ProviderService、SessionService 和 WorkspaceSidecarIndexer。
- /home/hqm/nutstore-bot/sidecar/src/nsbot_sidecar/application/provider_service.py — catalog_payload 触发 list_providers 的入口，适合排查 provider catalog 是否在启动或首个请求时产生显著成本。
- /home/hqm/nutstore-bot/sidecar/src/nsbot_sidecar/providers/provider_catalog.py — provider catalog 与 litellm 相关导入点；若要优化冷启动，这是优先怀疑对象之一。
- /home/hqm/nutstore-bot/sidecar/pyproject.toml — GraalPy feasibility 所需的关键依赖清单，用于兼容性分级与 spike 验证范围。
- /home/hqm/nutstore-bot/sidecar/tests/e2e_packaged_cli.sh — 现有 packaged CLI 端到端验证入口；若做 GraalPy spike，至少要有同等级 smoke 验证。
- /home/hqm/nutstore-bot/sidecar/tests/e2e_agent_cli.sh — CLI agent 路径验证入口；可用于判断迁移是否影响真实运行路径。

**Verification**
1. 在现有 PyInstaller 方案下，对 dist/nsbot 的最小命令做冷/热启动基线，例如 --help、providers list，以及桌面端 ACP initialize 完成时间，并保留分层计时日志。
2. 运行现有 sidecar 测试中的最小相关集合，至少覆盖 /home/hqm/nutstore-bot/sidecar/tests/test_acp_stdio.py、/home/hqm/nutstore-bot/sidecar/tests/test_runtime_engine.py、/home/hqm/nutstore-bot/sidecar/tests/test_worker.py、/home/hqm/nutstore-bot/sidecar/tests/test_tools.py，确保任何启动优化没有破坏 runtime/ACP 契约。
3. 若进入 GraalPy spike，先验证在 GraalPy 解释器下直接运行 /home/hqm/nutstore-bot/sidecar/src/nsbot_sidecar/cli.py 的最小命令，再验证打包分发形态，最后才验证与 Rust launcher 的集成。
4. 对 GraalPy spike 记录三类结果：启动时间变化、产物布局差异、依赖兼容异常。没有这三类结果，不做迁移决策。
5. 最终输出必须是一个明确结论：保留 PyInstaller 并优化初始化，或继续 GraalPy 迁移；不能停留在“可能更快”的模糊状态。

**Decisions**
- 已确认评估范围同时覆盖“独立打包 CLI 命令启动”和“桌面端首次连接 sidecar”两个场景。
- 已确认优化目标是整体权衡，而不是只盯冷启动毫秒数；因此兼容性、构建复杂度、跨平台分发和维护成本与速度同等重要。
- 当前建议是不把 GraalPy 视为 PyInstaller 的直接替代品，而是视为需要单独证明收益与兼容性的候选方案。
- 当前计划包含 feasibility spike 与决策门槛，不包含正式迁移实施。

**Further Considerations**
1. 推荐把“打包器层收益占总启动时间至少达到可观比例”作为 GraalPy spike 立项条件，否则优先做应用初始化优化。
2. 若未来要继续 GraalPy，建议优先做 Linux 开发环境 spike，因为当前用户环境是 Linux，且 sidecar README 已记录 Linux 打包链路本身存在 search-tools 准备限制，正好可以一起暴露构建侧真实复杂度。
3. 若基线显示 packaged CLI 与桌面 ACP 首连的瓶颈不同，后续实施应拆成两个优化 workstream，而不是强行用一种打包替换覆盖两个问题。

## Plan: GraalPy Feasibility for CLI Startup

我的判断先放前面：现阶段不建议直接把 PyInstaller 替换成 GraalPy。当前 packaged CLI 主链路已经是 onedir，不是 onefile，所以最典型的 PyInstaller 每次解包损耗本来就已经绕开了；而现有启动路径里，真正影响感知时延的不只是打包器，还包括 Rust launcher 的 runtime 初始化、Python 进程启动、ACP app 创建、数据库连接与 schema 初始化，以及 sidecar 模块导入。相关链路我核对过 build_packaged_cli.sh、nsbot.rs、launcher.rs、acp_stdio.py、acp_app.py 和 provider_catalog.py。

**Steps**
1. 先建立两条基线：独立打包 CLI 命令启动，以及桌面端通过 ACP stdio 拉起 sidecar 的首连时间。你已经确认这两种场景都要评估，且目标是整体权衡，不只看冷启动毫秒数。
2. 在现有链路上补最小侵入式计时点，拆出 launcher 初始化、payload 拉起、ACP initialize 完成、首个业务命令可用这几个阶段。没有这层数据，就无法判断 GraalPy 的收益上限。
3. 先看应用初始化热点，再谈换打包器。重点核查 launcher.rs 的 templates 和 search-tools 准备、acp_app.py 的建库和服务装配、以及 provider_catalog.py 顶层导入是否把 litellm 成本提前到了启动阶段。
4. 明确迁移约束：现有 launcher 依赖 nsbot.rs 约定的 onedir payload 布局和 build_packaged_cli.sh 产出的 runtime 目录。GraalPy 方案如果不能保持这个契约，就不是“替换打包器”，而是连 launcher 一起改。
5. 只在前面数据表明“打包器/解释器启动”占比明显时，才做 GraalPy spike。这个 spike 先验证三件事：能否跑通 cli.py 的最小命令、能否支持 ACP 初始化路径、能否产出与现有 launcher 兼容的分发形态。
6. 给出明确决策门槛：如果 GraalPy 只带来有限收益，却显著增加跨平台构建、依赖兼容和 CI 维护成本，就保留 PyInstaller，把优化重点放回应用初始化。

**Relevant files**
- build_packaged_cli.sh 负责当前 packaged CLI 主构建，已确认使用 onedir
- build_pyinstaller_sidecar.sh 是 onefile 参考脚本，不是主分发路径
- nsbot.rs 定义 launcher 如何查找 payload
- launcher.rs 负责 runtime 初始化和环境变量注入
- main.rs 包含桌面端 ACP stdio 拉起与 initialize 握手
- cli.py 是 Python CLI 入口
- acp_stdio.py 是 ACP stdio 主入口
- acp_app.py 串起数据库、仓储和服务初始化
- provider_service.py 和 provider_catalog.py 是 provider catalog 成本的关键入口
- pyproject.toml 定义 GraalPy spike 需要验证的依赖集合
- e2e_packaged_cli.sh 和 e2e_agent_cli.sh 是后续可行性验证入口

**Verification**
1. 先对现有 PyInstaller 方案测 dist/nsbot 的最小命令和桌面 ACP initialize 完成时间，分别记录冷启动和热启动。
2. 跑最小相关测试集，至少覆盖 test_acp_stdio.py、test_runtime_engine.py、test_worker.py 和 test_tools.py。
3. 如果进入 GraalPy spike，先用 GraalPy 解释器直接跑 cli.py 的最小命令，再验证打包和 launcher 集成，不要一上来就改主构建链路。
4. 最终输出必须是二选一：继续沿 PyInstaller 优化初始化，或进入 GraalPy 正式迁移设计。

补充判断：官方 GraalPy 文档确实强调了单二进制分发和较大的包兼容矩阵，但它并不是现有依赖栈的零风险替换。对这个仓库来说，GraalPy 更像“需要单独证明收益和兼容性”的候选方案，而不是一个可以预设更快的直接替代物。计划已经写入 session plan，后续如果你要，我可以继续把这份计划收缩成一个更短的执行清单，或者展开成一份明确的 benchmark 设计。