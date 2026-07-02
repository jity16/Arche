ARCHE 工程交付问答：Planner-MCP、Reflection 跳转、Workflow 入口与路径部署说明
=============================================================================

1. 当前结论摘要
---------------

当前代码已经包含：

-   controller 驱动的多智能体闭环 workflow：Retrieval → Hypothesis → Planner →
    Execution → Reflection。

-   `PlannerAgent`、`ExecutionAgent`、`ReflectionAgent` 等 agent 原型。

-   `src/chemistry_multiagent/tools/toolpool.json` 驱动的工具注册与执行路由。

-   Gaussian `replay` / `local_shell` / `slurm` 执行模式。

-   长时间 Gaussian job 的 `waiting_for_gaussian_jobs` 暂停状态和
    `--resume-state` 恢复入口。

MCP server 还没有作为正式边界实现。当前没有正式 MCP server/client、MCP tool
schema、MCP resource schema 或 HTTP backend API。工程侧需要围绕现有 Planner /
Expert model / Tool / Gaussian job / Workflow controller 边界来封装第一版
MCP/backend 服务。

路径硬化已经在 demo/test 脚本层完成：相关脚本现在优先使用
`ARCHE_PROJECT_ROOT`，否则从脚本位置向上查找 `src/chemistry_multiagent`
推断项目根目录。因此项目不再要求必须放在 `/home/lidong/ChemistryAgent` 或本地
`ARCHE` 路径下；放到新的服务器目录时，只要设置 `ARCHE_PROJECT_ROOT` 和
`PYTHONPATH`，项目内部路径应能正确解析。

需要特别说明：高级真实计算仍依赖外部环境配置，包括 DeepSeek/API key、ARCHE-Chem
模型、Gaussian、Slurm、RDKit/OpenBabel/ASE/cclib
等。路径硬化不等于这些依赖已经自动安装。

2. 问题一：Planner agent 如何接入 MCP server，尤其是如何接入专家模型？
----------------------------------------------------------------------

### A. 当前代码事实

当前 `PlannerAgent` 不调用正式 MCP server，也没有 MCP
client（这部分应该之前让方圆做了，需要的话可以让方圆按照下面内容将mcp
server结合进去）。

它现在使用的是本地 Python 原型集成方式：

-   读取本地 `toolpool.json` 获取工具名称、工具路径和描述。

-   通过 `src/chemistry_multiagent/utils/llm_api.py` 调用
    DeepSeek/OpenAI-compatible 风格接口。

-   对 Gaussian 相关步骤，通过
    `src/chemistry_multiagent/utils/arche_chem_client.py` 调用 ARCHE-Chem
    专家模型。

-   如果 ARCHE-Chem 本地/指定后端不可用，Planner 的专家复核路径会 fallback 到
    DeepSeek；如果通用 LLM 也不可用，则抛错或保留原 workflow。

关键代码位置如下：

| 职责                      | 当前代码位置                                                                                      | 当前行为                                                                                                                         |
|---------------------------|---------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------|
| Planner agent 类          | `src/chemistry_multiagent/agents/planner_agent.py PlannerAgent`                                   | 根据 hypothesis/strategy 生成 workflow protocol。                                                                                |
| Planner 初始化            | `PlannerAgent.__init__`                                                                           | 接收 `toolpool_path`、`general_model_name`、`expert_model_name`、`expert_model_path`、`expert_backend`、`enable_expert_review`。 |
| toolpool 查找             | `PlannerAgent._find_toolpool_path`                                                                | 如果未显式传 `toolpool_path`，会查找 legacy 默认路径；工程运行建议显式传 `src/chemistry_multiagent/tools/toolpool.json`。        |
| toolpool 加载             | `PlannerAgent._load_tools`                                                                        | 读取 JSON 工具列表；失败时使用 `_create_default_tools`。                                                                         |
| workflow step schema 兼容 | `PlannerAgent.normalize_step_schema`、`PlannerAgent.export_execution_compatible_workflow`         | 同时维护增强 `steps` 与执行层兼容的旧字段 `Steps`。                                                                              |
| workflow 生成             | `PlannerAgent.generate_experiment_protocol`、`PlannerAgent.generate_workflows_for_top_strategies` | 调用通用 LLM 生成和优化候选 workflow。                                                                                           |
| workflow 修订             | `PlannerAgent.revise_workflow_from_reflection`                                                    | Reflection 要求 `revise_workflow` 时由 controller 调用。                                                                         |
| 通用 LLM 调用             | `PlannerAgent._call_llm` → `llm_api.call_deepseek_api`                                            | 调用 DeepSeek API，`llm_api.py` 内部使用 OpenAI client 和 `DEEPSEEK_API_KEY`。                                                   |
| Gaussian step 检测        | `PlannerAgent._is_gaussian_related_step`、`PlannerAgent.extract_gaussian_related_steps`           | 根据 tool/description/input/output 中的 Gaussian、freq、opt、TS、IRC 等关键词识别需复核步骤。                                    |
| Gaussian 复核请求构造     | `PlannerAgent.build_gaussian_review_request`、`PlannerAgent.build_arche_chem_review_prompt`       | 把 step、workflow、科学问题、charge/multiplicity/solvent/elements 等上下文组织成专家模型请求。                                   |
| ARCHE-Chem 专家复核调用   | `PlannerAgent._call_arche_chem_with_audit`、`PlannerAgent.review_gaussian_steps_with_arche_chem`  | 优先调用共享 `call_arche_chem`，失败后 fallback 到 DeepSeek，并记录 audit。                                                      |
| 专家复核写回 workflow     | `PlannerAgent.apply_expert_review_to_workflow`、`PlannerAgent._run_gaussian_expert_review`        | 写入 `gaussian_review`、`expert_review_audit`、`gaussian_review_summary`，再导出执行层兼容 schema。                              |
| 专家模型 client           | `src/chemistry_multiagent/utils/arche_chem_client.py ArcheChemClient`、`call_arche_chem`          | 支持 `local_hf`、`vllm`、`openai_compatible` 三类后端。                                                                          |
| 通用 LLM client           | `src/chemistry_multiagent/utils/llm_api.py call_deepseek_api`                                     | 通过 `DEEPSEEK_API_KEY` 和 DeepSeek base URL 调用 chat completions，带简单重试。                                                 |
| 当前工具注册源            | `src/chemistry_multiagent/tools/toolpool.json`                                                    | 只包含 `tool_name`、`tool_path`、`description` 等轻量字段，尚无正式输入/输出/error/artifact schema。                             |

补充：`ExecutionAgent` 也有专家模型分析路径，例如
`ExecutionAgent.analyze_gaussian_error_with_arche_chem` 和内部
`_call_expert_with_fallback`，用于 Gaussian 错误/结果分析。Planner-Gaussian
keyword review 的主路径仍在 `PlannerAgent`。

### B. MCP 工程接入建议

工程化后建议把当前原型映射成以下 MCP/backend 边界。

1.  Planner 作为 MCP client 或 backend service client

    Planner 不应在生产中直接理解所有本地脚本路径。它应查询 MCP Tool Registry
    或后端 tool service，获取可用工具、schema、约束、artifact
    规则、超时和资源要求。

2.  `toolpool.json` 作为 MCP tool definitions 的种子

    当前 `src/chemistry_multiagent/tools/toolpool.json`
    可以作为第一版工具注册源，但需要扩展为正式 schema：

    -   input schema

    -   output schema

    -   error schema

    -   artifact schema

    -   version

    -   timeout/resource requirements

    -   side effects/sandbox policy

    -   tool name alias 规则，例如 `smiles2sdf` 与 `smiles_to_sdf` 的兼容关系

3.  专家模型封装为独立 MCP tool 或 model-service endpoint

    候选接口：

    -   `arche_chem.review_gaussian_keywords`

    -   `arche_chem.diagnose_gaussian_error`

    -   `arche_chem.analyze_gaussian_result`

    -   `arche_chem.recommend_calculation_protocol`

    这些接口内部可以继续复用
    `arche_chem_client.py`，也可以改为调用生产模型服务。第一版建议保留 audit
    字段，记录 requested backend、used backend、fallback reason 和模型名称。

4.  推荐 Planner-MCP 调用流

    -   Planner 接收 scientific question / hypothesis。

    -   Planner 查询 MCP tool registry。

    -   Planner 生成 workflow draft。

    -   Planner 检测 Gaussian-related steps。

    -   Planner 将 Gaussian steps 发送给专家模型 MCP tool。

    -   专家模型返回 approved/revised/rejected、recommended
        route、warnings、solvent/method/basis/element checks、validation
        requirements。

    -   Planner 把专家复核 metadata 写回 workflow。

    -   Planner 输出 ExecutionAgent/backend service 可执行的 protocol。

### C. Important caveat

MCP 是待新增工程边界，不是当前已实现能力。当前代码提供了
Planner、toolpool、专家模型 client、Gaussian job backend
的原型逻辑和集成点，但没有正式 MCP server 实现。

3. 问题二：Reflection agent 的节点跳转逻辑
------------------------------------------

### A. 当前代码行为

`ReflectionAgent`
负责生成反思决策，但它自己不执行节点跳转。真正的跳转/状态机解释在
`ChemistryMultiAgentController` 中完成。

当前 `ReflectionAgent` 的主要输出 decision 包括：

-   `accept`

-   `revise_workflow`

-   `revise_hypothesis`

-   `stop`

`waiting_for_gaussian_jobs` 不是 `ReflectionAgent` 的 decision。它是
Execution/Controller 在发现 Gaussian job 仍处于
`prepared`、`submitted`、`queued`、`running` 等未完成状态时产生的 workflow
状态。Gaussian 结果未完成时，controller 会暂停并写等待快照，不进入 Reflection。

### B. 代码位置

| 逻辑                                    | 当前代码位置                                                                                                                 | 当前行为                                                                                                                                      |
|-----------------------------------------|------------------------------------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------|
| Reflection agent 类                     | `src/chemistry_multiagent/agents/reflection_agent.py ReflectionAgent`                                                        | 规则驱动反思，输出决策和修订建议。                                                                                                            |
| evidence summary                        | `ReflectionAgent.summarize_evidence`                                                                                         | 汇总执行结果、科学证据、Gaussian/专家信号。                                                                                                   |
| problem identification                  | `ReflectionAgent.identify_problems`                                                                                          | 生成 `identified_problems`。                                                                                                                  |
| decision generation                     | `ReflectionAgent.make_decision`                                                                                              | 根据失败、专家信号、TS 验证、证据支持度和轮次等规则生成 decision。                                                                            |
| reflect entrypoint                      | `ReflectionAgent.reflect`                                                                                                    | 调用 summarize/identify/make_decision，返回结构化 reflection result。                                                                         |
| controller reflection phase             | `src/chemistry_multiagent/controllers/chemistry_multiagent_controller.py ChemistryMultiAgentController.run_reflection_phase` | 准备 evidence，调用 `self.reflection_agent.reflect(...)`，保存 `reflection_result_round_*.json`。                                             |
| controller bounded loop                 | `ChemistryMultiAgentController.run_bounded_closed_loop_workflow`                                                             | fresh run 的主闭环；解释 reflection decision 并跳转。                                                                                         |
| controller handling `accept`            | `run_bounded_closed_loop_workflow`、`resume_from_waiting_state`                                                              | 设置 `reflection_accept`，停止 loop，进入 final conclusion。                                                                                  |
| controller handling `revise_workflow`   | `run_bounded_closed_loop_workflow`、`resume_from_waiting_state`                                                              | 调 `PlannerAgent.revise_workflow_from_reflection`，更新 `optimized_protocols`，下一轮执行。                                                   |
| controller handling `revise_hypothesis` | `run_bounded_closed_loop_workflow`、`resume_from_waiting_state`                                                              | 可先调 `RetrievalAgent.retrieve_followup_evidence`，再调 `HypothesisAgent.revise_hypotheses_from_reflection`，下一轮重新 planning/execution。 |
| controller handling `stop`              | `run_bounded_closed_loop_workflow`、`resume_from_waiting_state`                                                              | 设置 `reflection_stop`，停止 loop。                                                                                                           |
| pending Gaussian 检测                   | `ChemistryMultiAgentController._extract_pending_gaussian_job_summary`、`_has_pending_gaussian_jobs`                          | 从 execution result 中识别 pending Gaussian job。                                                                                             |
| waiting snapshot                        | `ChemistryMultiAgentController._write_waiting_state_snapshot`                                                                | 写 `waiting_gaussian_jobs_state.json`，设置 `status=waiting_for_gaussian_jobs` 和 `can_resume=true`。                                         |
| resume entrypoint                       | `ChemistryMultiAgentController.resume_from_waiting_state`                                                                    | 加载等待快照，恢复 execution/poll，完成后再进入 reflection。                                                                                  |
| resume snapshot 校验                    | `ChemistryMultiAgentController._load_waiting_resume_state`                                                                   | 校验 `status`、`can_resume`、`workflow_state`、`resume_state`、`pending_gaussian_jobs`、`gaussian_execution_mode`。                           |

最近执行层已有一个重要行为：`ExecutionAgent.execute_workflow(...)` 会在 Gaussian
job 返回 `prepared`、`submitted`、`queued`、`running` 时停止后续步骤，记录
“Workflow paused”，并跳出当前 workflow 步骤循环。这样下游结果分析步骤不会在
Gaussian 结果未完成时提前执行。

### C. Transition table

| Decision / state            | Produced by                                 | Controller behavior                                                                                                                                            | Next node                                    |
|-----------------------------|---------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------|----------------------------------------------|
| `accept`                    | `ReflectionAgent.make_decision` / `reflect` | 记录 `reflection_accept`，结束 bounded loop，合成最终结论。                                                                                                    | final conclusion                             |
| `revise_workflow`           | `ReflectionAgent.make_decision` / `reflect` | 调用 `PlannerAgent.revise_workflow_from_reflection`，更新 planning result；fresh run 使用 `pending_planning_override`，resume run 标记 `needs_planning=True`。 | Planner revision / Execution                 |
| `revise_hypothesis`         | `ReflectionAgent.make_decision` / `reflect` | 可执行 follow-up retrieval；调用 `HypothesisAgent.revise_hypotheses_from_reflection`；更新 ranked/top strategies；下一轮重新 planning/execution。              | Retrieval / Hypothesis / Planner / Execution |
| `stop`                      | `ReflectionAgent.make_decision` / `reflect` | 记录 `reflection_stop`，停止 loop，进入最终结论或错误收尾。                                                                                                    | final/error conclusion                       |
| `waiting_for_gaussian_jobs` | Execution/Controller，不是 ReflectionAgent  | 暂停 workflow；写 `outputs/multiagent/waiting_gaussian_jobs_state.json`；不运行 Reflection；等待稍后 `--resume-state`。                                        | resume/poll later                            |

4. 问题三：整个 Workflow 的入口在哪里？
---------------------------------------

### A. CLI entrypoint

当前 CLI module 入口是：

~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~ bash
python -m chemistry_multiagent.controllers.chemistry_multiagent_controller
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

推荐在仓库根目录设置 `PYTHONPATH=src` 或使用绝对 `PYTHONPATH`。

mock 示例：

~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~ bash
PYTHONPATH=src python -m chemistry_multiagent.controllers.chemistry_multiagent_controller \
  --mock \
  --question "Propose a plausible TS validation workflow" \
  --work-dir "$(pwd)" \
  --toolpool "src/chemistry_multiagent/tools/toolpool.json"
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

replay 示例：

~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~ bash
PYTHONPATH=src python -m chemistry_multiagent.controllers.chemistry_multiagent_controller \
  --question "Evaluate a candidate TS workflow for nucleophilic addition" \
  --work-dir "$(pwd)" \
  --toolpool "src/chemistry_multiagent/tools/toolpool.json" \
  --gaussian-execution-mode replay \
  --pdf-dir "tests/inputs/papers" \
  --index-dir "tests/temp/index"
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

resume 示例：

~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~ bash
PYTHONPATH=src python -m chemistry_multiagent.controllers.chemistry_multiagent_controller \
  --resume-state "outputs/multiagent/waiting_gaussian_jobs_state.json" \
  --work-dir "$(pwd)" \
  --toolpool "src/chemistry_multiagent/tools/toolpool.json"
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

CLI 代码位置：

-   `src/chemistry_multiagent/controllers/chemistry_multiagent_controller.py
    main`

-   `if __name__ == "__main__": main()`

当前 CLI 约束：

-   `--resume-state` 不能和 `--mock` 同时使用。

-   未提供 `--resume-state` 时必须提供 `--question`。

-   `--gaussian-execution-mode` 可选值是 `replay`、`local_shell`、`slurm`。

注意：controller 默认 toolpool 路径仍是
`work_dir/toolpool/toolpool.json`，而仓库内 checked-in toolpool 是
`src/chemistry_multiagent/tools/toolpool.json`。工程演示和脚本中应显式传
`--toolpool "src/chemistry_multiagent/tools/toolpool.json"` 或用 `TOOLPOOL_PATH`
派生。

### B. Python API entrypoint

主要 Python API 入口：

| API                                                                   | 当前定位                                                                                                                      |
|-----------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------|
| `ChemistryMultiAgentController`                                       | controller 主类，负责初始化 agents、输出目录、workflow state。                                                                |
| `ChemistryMultiAgentController.run_bounded_closed_loop_workflow(...)` | 当前真实主编排方法：Retrieval → Hypothesis → Planner → Execution → pending Gaussian check → Reflection → revise/accept/stop。 |
| `ChemistryMultiAgentController.run_complete_workflow(...)`            | legacy-compatible wrapper；内部调用 bounded closed-loop，再转换为旧格式结果。CLI 非 mock/非 resume 路径当前调用它。           |
| `ChemistryMultiAgentController.run_mock_workflow(...)`                | mock 快速路径，不依赖真实 API key。                                                                                           |
| `ChemistryMultiAgentController.resume_from_waiting_state(...)`        | 从 `waiting_for_gaussian_jobs` snapshot 恢复执行。                                                                            |

### C. Workflow modes

| 模式                             | 当前入口/配置                                   | 当前行为                                                                                                    |
|----------------------------------|-------------------------------------------------|-------------------------------------------------------------------------------------------------------------|
| mock mode                        | CLI `--mock` / `run_mock_workflow`              | 生成最小 mock result，最适合基础连通性和前端演示。                                                          |
| replay mode                      | `--gaussian-execution-mode replay`，也是默认值  | Gaussian 相关工具返回模拟归一化结果，不提交真实 Gaussian job。                                              |
| local_shell mode                 | `--gaussian-execution-mode local_shell`         | 对 `.gjf` 生成 shell script，用本地 `GAUSSIAN_COMMAND` 启动 Gaussian，并通过 state/pid/exit-code/log 轮询。 |
| slurm mode                       | `--gaussian-execution-mode slurm`               | 生成 sbatch script，调用 `sbatch` 提交，用 `squeue`/`sacct` 轮询。                                          |
| real-tool-only demo              | `tests/scripts/test_real_tool_only_workflow.py` | 直接使用 `ExecutionAgent` 真实执行部分顶层工具，Gaussian 使用 replay；依赖 RDKit/ASE/OpenBabel 等环境。     |
| hybrid real-tool + Gaussian demo | `tests/scripts/test_hybrid_workflow.py`         | 前处理工具真实执行，Gaussian 使用 `local_shell`；通过环境 gate 显式开启。                                   |

相关执行代码：

-   `src/chemistry_multiagent/agents/execution_agent.py ExecutionAgent.__init__`

-   `ExecutionAgent.execute_tool`

-   `ExecutionAgent.execute_workflow`

-   `ExecutionAgent._execute_gaussian_job_backend`

-   `ExecutionAgent.execute_gaussian_related_tool`

### D. Long-running Gaussian behavior

当前 pending statuses：

-   `prepared`

-   `submitted`

-   `queued`

-   `running`

行为如下：

1.  `ExecutionAgent._execute_gaussian_job_backend` 准备
    `.gjf`、script、log、chk、exit-code、job state。

2.  `local_shell` 模式生成 shell script 并后台提交；`slurm` 模式生成 sbatch
    script 并提交。

3.  job state 存在 `.gaussian_job_state_*.json` 一类文件中，并被
    `_recover_gaussian_job` 后续读取/轮询。

4.  `ExecutionAgent.execute_workflow` 如果发现当前 Gaussian job 仍
    pending，会停止后续步骤，避免 downstream result-analysis 过早运行。

5.  `ChemistryMultiAgentController._extract_pending_gaussian_job_summary` 从
    execution result 提取 pending jobs。

6.  Controller 写出 `outputs/multiagent/waiting_gaussian_jobs_state.json`，设置
    `status=waiting_for_gaussian_jobs`、`can_resume=true`。

7.  用户或后端稍后用 `--resume-state` 恢复；若 job 仍
    pending，则再次写等待快照；若完成，则继续 Reflection。

这意味着生产后端不能把 Gaussian submit 当作同步短任务处理。它需要 run/job
状态持久化、轮询或事件流，并在 job 未完成时暂停 downstream analysis。

### E. Engineering backend recommendation

第一版 backend 可以先包住当前 controller，而不是重写科学逻辑。建议 API 形态：

-   `POST /workflow-runs`：创建 workflow run，传
    question、mode、toolpool、Gaussian/model 配置。

-   `GET /workflow-runs/{id}`：查询 run 状态、phase 状态、step 状态、reflection
    decision、pending jobs。

-   `POST /workflow-runs/{id}/resume`：恢复 waiting run，对应当前
    `resume_from_waiting_state(...)`。

-   `GET /workflow-runs/{id}/artifacts`：列出 workflow、step、Gaussian、report
    artifacts。

-   SSE/WebSocket event stream：向前端推送 run status、phase status、Gaussian
    job status、reflection decision、waiting/resume prompt。

这些是工程建议，不是当前已实现的 HTTP API。

5. 问题四：test 中脚本路径是否已经换成相对路径？放到其他服务器怎么运行？
------------------------------------------------------------------------

### A. Current modified state

相关 demo/test 脚本已经做了路径硬化，核心文件是：

-   `tests/scripts/path_utils.py`

当前解析策略：

1.  优先使用 `ARCHE_PROJECT_ROOT`。

2.  如果未设置，则从当前脚本 `__file__` 所在位置向上查找，直到发现
    `src/chemistry_multiagent`。

3.  常用仓库内部路径从 `PROJECT_ROOT` 派生。

`path_utils.py` 当前提供：

-   `PROJECT_ROOT`

-   `SRC_DIR`

-   `TESTS_DIR`

-   `TEST_INPUTS_DIR`

-   `TEST_TEMP_DIR`

-   `TEST_REPORTS_DIR`

-   `TEST_OUTPUTS_DIR`

-   `TOOLPOOL_PATH`

-   `add_src_to_path()`

-   `require_gaussian_demo_enabled(...)`

已使用路径硬化的脚本包括：

-   `tests/scripts/test_env_probe.py`

-   `tests/scripts/test_real_tool_only_workflow.py`

-   `tests/scripts/test_gaussian_only_local_shell.py`

-   `tests/scripts/test_hybrid_workflow.py`

-   `tests/scripts/run_controller_integration_test.py`

-   `tests/scripts/run_real_tool_smoke_tests.py`

-   `tests/scripts/probe_real_tools_environment.py`

-   `tests/scripts/run_scientific_closed_loop_tests.py`

-   `tests/scripts/run_state_entry_tests.py`

-   `tests/scripts/run_ver_loop_tests.py`

-   `tests/scripts/test_paperscraper.py`

`tests/scripts/test_controller.sh` 也会从 `ARCHE_PROJECT_ROOT`
或脚本相对位置推断项目根目录，并导出 `PYTHONPATH`。

`tests/test_controller_resume.py` 已支持相同的项目根目录解析逻辑。

当前 checked-in toolpool 文件是：

~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~ text
src/chemistry_multiagent/tools/toolpool.json
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

已检查该文件，内部工具路径是相对引用，例如
`../tools/gen_gaussiancode.py`、`../tools/sdf2gjf.py`、`../tools/smiles2sdf.py`，没有发现项目根目录绝对路径。

### B. 可以放到其他服务器路径运行吗？

对于项目内部路径解析，答案是：可以。它不应再要求项目必须位于
`/home/lidong/ChemistryAgent` 或本地 `/Users/.../ARCHE`。

例如部署到：

~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~ text
/data/projects/ARCHE
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

可以这样运行：

~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~ bash
cd /data/projects/ARCHE
export ARCHE_PROJECT_ROOT="$(pwd)"
export PYTHONPATH="$ARCHE_PROJECT_ROOT/src:${PYTHONPATH:-}"
python tests/scripts/test_env_probe.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

此时 mock/replay 和已经路径硬化的 demo/test 脚本应能正确解析项目内部路径。

### C. 仍需要配置哪些外部环境？

路径硬化不会安装外部依赖。换到其他服务器后，真实工具/Gaussian/hybrid
演示仍需要配置：

-   Python 环境和依赖。

-   DeepSeek/API key，如果要调用通用 LLM：

    -   `DEEPSEEK_API_KEY`

-   ARCHE-Chem 专家模型，如果使用本地/模型服务复核：

    -   `ARCHE_CHEM_MODEL_PATH`

    -   `ARCHE_CHEM_MODEL_NAME`

    -   `ARCHE_CHEM_BACKEND`

    -   `ARCHE_CHEM_BASE_URL` / `ARCHE_CHEM_API_KEY`，仅 `openai_compatible`
        后端需要。

-   Gaussian：

    -   `ARCHE_RUN_GAUSSIAN_TESTS=1`

    -   `GAUSSIAN_COMMAND=g16` 或服务器实际命令

    -   `GAUSSIAN_MODULE_LOAD`

    -   `GAUSSIAN_ENV_HOOK`

    -   `GAUSSIAN_JOB_ROOT`，如需指定 job state 根目录

-   chemistry tools/packages：

    -   RDKit

    -   OpenBabel

    -   ASE

    -   cclib

    -   相关工具脚本自身依赖

`src/chemistry_multiagent/tools/gen_gaussiancode.py` 仍保留 `/home/lidong/model`
作为 legacy model path fallback，但优先读取 `ARCHE_CHEM_MODEL_PATH` /
`GAUSSIAN_LOCAL_MODEL_PATH`。这属于外部模型部署配置，不是项目内部路径；新服务器应通过环境变量显式配置。

### D. Server / arbitrary server / local examples

服务器部署路径：

~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~ bash
cd /home/lidong/ChemistryAgent
export ARCHE_PROJECT_ROOT=/home/lidong/ChemistryAgent
export PYTHONPATH="$ARCHE_PROJECT_ROOT/src:${PYTHONPATH:-}"
python tests/scripts/test_env_probe.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

任意新服务器路径：

~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~ bash
cd /data/projects/ARCHE
export ARCHE_PROJECT_ROOT="$(pwd)"
export PYTHONPATH="$ARCHE_PROJECT_ROOT/src:${PYTHONPATH:-}"
python tests/scripts/test_env_probe.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

本地重命名目录：

~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~ bash
cd "/Users/lidong/Documents/the way to AI expert /计算化学智能体/代码/chemagent_openning/ARCHE"
export ARCHE_PROJECT_ROOT="$(pwd)"
export PYTHONPATH="$ARCHE_PROJECT_ROOT/src:${PYTHONPATH:-}"
python tests/scripts/test_env_probe.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Gaussian demo 显式开启示例：

~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~ bash
ARCHE_RUN_GAUSSIAN_TESTS=1 GAUSSIAN_COMMAND=g16 \
python tests/scripts/test_gaussian_only_local_shell.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

### E. 限制说明

-   mock/replay 最可移植，适合第一版前端/后端流程演示。

-   real-tool-only 依赖真实 chemistry Python packages 和工具脚本运行环境。

-   Gaussian demo 依赖 Gaussian 安装和命令可用。

-   hybrid demo 同时依赖 real tools 与 Gaussian。

-   `tests/scripts/` 仍然是内部 demo/validation harness，不是稳定公开 API。

-   运行时代码中的部分 legacy fallback
    路径没有在本次测试路径硬化中重构，工程服务化时应单独配置化。

6. 最终结论
-----------

当前代码适合作为第一版专家试用/现场演示工程输入。它已经有可读的 controller
主流程、agent 职责边界、toolpool 工具注册、Gaussian replay/local_shell/slurm
原型、waiting/resume 行为，以及路径硬化后的 demo/test 脚本。

项目可以放到新的服务器路径，例如 `/data/projects/ARCHE`，只要设置：

~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~ bash
export ARCHE_PROJECT_ROOT="$(pwd)"
export PYTHONPATH="$ARCHE_PROJECT_ROOT/src:${PYTHONPATH:-}"
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

mock/replay 路径应以较少依赖运行；real-tool、Gaussian、hybrid
演示仍需要显式配置外部依赖和环境变量。

MCP 当前没有实现。工程侧应把现有 Planner/Expert model/Tool/Gaussian/Workflow
controller 边界包装为 MCP tools 或 backend service
API（这部分方圆应该已经完成了），并先冻结 workflow、tool、artifact、job 和
frontend-visible state schema。
