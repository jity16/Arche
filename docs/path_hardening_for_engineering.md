# ARCHE 路径硬化与演示环境说明

## 1. 修改背景

当前第一版工程目标是支持专家试用和现场演示，而不是一次性完成生产级产品化。代码需要在两个环境中运行：

- 服务器部署目录：`/home/lidong/ChemistryAgent`
- 本地重命名后的项目目录：`/Users/lidong/Documents/the way to AI expert /计算化学智能体/代码/chemagent_openning/ARCHE`

历史测试脚本中存在类似 `/home/lidong/ChemistryAgent` 的项目根目录硬编码。这个路径本身不是错误路径，它是服务器真实部署路径；问题在于脚本如果把它写死，就无法在本地重命名后的 `ARCHE` 目录中直接运行。

本次路径硬化的目标是让测试和演示脚本动态解析项目根目录：优先读取 `ARCHE_PROJECT_ROOT`，没有设置时从脚本所在位置向上查找项目标记。这个改动是 demo-readiness 改进，不是生产架构重构。

## 2. 重要路径说明

`/home/lidong/ChemistryAgent` 是服务器部署路径，不应被理解为“错误路径”。本地路径是重命名后的 `ARCHE` 项目目录。工程同事后续新增脚本时，不应硬编码服务器路径或本地路径，而应使用 `ARCHE_PROJECT_ROOT` 或项目根目录推断。

服务器：

```bash
cd /home/lidong/ChemistryAgent
export ARCHE_PROJECT_ROOT=/home/lidong/ChemistryAgent
```

本地：

```bash
cd "/Users/lidong/Documents/the way to AI expert /计算化学智能体/代码/chemagent_openning/ARCHE"
export ARCHE_PROJECT_ROOT="$(pwd)"
```

当 `ARCHE_PROJECT_ROOT` 未设置时，已硬化的 Python 脚本会从 `__file__` 所在目录逐级向上查找 `src/chemistry_multiagent`，并将找到的目录作为项目根目录。

## 3. 本次修改了哪些文件

根据当前仓库状态，本次路径硬化相关文件包括：

| 文件 | 当前作用 |
| --- | --- |
| `tests/scripts/path_utils.py` | 新增共享路径工具，集中实现项目根目录解析、常用目录常量、`src` 注入、Gaussian 演示 gate。 |
| `tests/scripts/test_env_probe.py` | 环境探测脚本改为读取项目根目录和关键环境变量，便于服务器/本地演示前检查。 |
| `tests/scripts/run_controller_integration_test.py` | 使用 `PROJECT_ROOT`、`TEST_INPUTS_DIR`、`TEST_TEMP_DIR`、`TOOLPOOL_PATH`，避免写死项目目录。 |
| `tests/scripts/run_real_tool_smoke_tests.py` | 使用共享路径工具解析输入、输出、toolpool 和 `src`。 |
| `tests/scripts/probe_real_tools_environment.py` | 使用项目根目录派生工具与测试路径。 |
| `tests/scripts/run_scientific_closed_loop_tests.py` | 使用共享路径工具定位项目内测试资源。 |
| `tests/scripts/run_state_entry_tests.py` | 使用共享路径工具定位脚本运行所需路径。 |
| `tests/scripts/run_ver_loop_tests.py` | 使用共享路径工具定位验证循环相关路径。 |
| `tests/scripts/test_real_tool_only_workflow.py` | real-tool-only 演示脚本改为项目根目录相对路径。 |
| `tests/scripts/test_gaussian_only_local_shell.py` | Gaussian local_shell 演示脚本改为项目根目录相对路径，并增加 Gaussian gate。 |
| `tests/scripts/test_hybrid_workflow.py` | hybrid real-tool + Gaussian 演示脚本改为项目根目录相对路径，并增加 Gaussian gate。 |
| `tests/scripts/test_paperscraper.py` | 使用共享路径工具注入 `src`。 |
| `tests/scripts/test_controller.sh` | Shell 入口现在从 `ARCHE_PROJECT_ROOT` 或脚本位置推断项目根目录，并导出 `PYTHONPATH`。 |
| `tests/test_controller_resume.py` | pytest 文件增加项目根目录解析，支持 `ARCHE_PROJECT_ROOT` 和本地推断。 |
| `README.md` | 补充服务器路径、本地 `ARCHE` 路径、`ARCHE_PROJECT_ROOT` 和第一版演示目标说明。 |
| `docs/quickstart.md` | 补充服务器/本地启动方式、`ARCHE_PROJECT_ROOT` 使用方式和演示定位。 |
| `docs/engineering_handoff_answers.md` | 补充工程交付视角下的路径策略说明，明确 `/home/lidong/ChemistryAgent` 是服务器路径而不是错误路径。 |

另外，已检查 `src/chemistry_multiagent/tools/toolpool.json`。当前 checked-in toolpool 使用的是类似 `../tools/gen_gaussiancode.py`、`../tools/sdf2gjf.py` 的相对引用，没有发现需要替换的项目根目录绝对路径。

## 4. 核心实现方式

路径解析集中放在 `tests/scripts/path_utils.py`。核心策略是：

1. 如果设置了 `ARCHE_PROJECT_ROOT`，直接使用该路径。
2. 如果没有设置，则从当前脚本 `__file__` 所在位置开始向上查找。
3. 找到包含 `src/chemistry_multiagent` 的目录后，将其视为项目根目录。
4. 如果无法推断，抛出错误并提示设置 `ARCHE_PROJECT_ROOT`。

概念模式如下：

```python
from pathlib import Path
import os
import sys

def find_project_root() -> Path:
    env_root = os.environ.get("ARCHE_PROJECT_ROOT")
    if env_root:
        return Path(env_root).expanduser().resolve()

    current = Path(__file__).resolve()
    for parent in [current.parent, *current.parents]:
        if (parent / "src" / "chemistry_multiagent").exists():
            return parent

    raise RuntimeError("Cannot infer ARCHE project root. Set ARCHE_PROJECT_ROOT.")

PROJECT_ROOT = find_project_root()
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
```

常用路径现在应从 `PROJECT_ROOT` 派生，例如：

- `PROJECT_ROOT / "src"`
- `PROJECT_ROOT / "tests" / "inputs"`
- `PROJECT_ROOT / "tests" / "temp"`
- `PROJECT_ROOT / "tests" / "reports"`
- `PROJECT_ROOT / "tests" / "outputs"`
- `PROJECT_ROOT / "src" / "chemistry_multiagent" / "tools" / "toolpool.json"`

需要特别注意：当前仓库内的 toolpool 文件是：

`src/chemistry_multiagent/tools/toolpool.json`

不要改成 `src/chemistry_multiagent/toolpool/toolpool.json`，除非将来真的新增了该文件并迁移调用逻辑。

## 5. 如何在服务器运行

基础环境：

```bash
cd /home/lidong/ChemistryAgent
export ARCHE_PROJECT_ROOT=/home/lidong/ChemistryAgent
export PYTHONPATH="$ARCHE_PROJECT_ROOT/src:${PYTHONPATH:-}"
```

运行环境探测：

```bash
python tests/scripts/test_env_probe.py
```

运行 replay/mock controller 演示：

```bash
PYTHONPATH=src python -m chemistry_multiagent.controllers.chemistry_multiagent_controller \
  --mock \
  --question "Propose a plausible TS validation workflow" \
  --work-dir "$(pwd)" \
  --toolpool "src/chemistry_multiagent/tools/toolpool.json"
```

运行 Gaussian local_shell 演示：

```bash
ARCHE_RUN_GAUSSIAN_TESTS=1 GAUSSIAN_COMMAND=g16 \
python tests/scripts/test_gaussian_only_local_shell.py
```

Gaussian 演示要求服务器上已安装 Gaussian，并且 `GAUSSIAN_COMMAND` 指向可执行命令。对于需要 module 或额外环境初始化的服务器，可配合 `GAUSSIAN_MODULE_LOAD` 或 `GAUSSIAN_ENV_HOOK`。

## 6. 如何在本地运行

本地基础环境：

```bash
cd "/Users/lidong/Documents/the way to AI expert /计算化学智能体/代码/chemagent_openning/ARCHE"
export ARCHE_PROJECT_ROOT="$(pwd)"
export PYTHONPATH="$ARCHE_PROJECT_ROOT/src:${PYTHONPATH:-}"
python tests/scripts/test_env_probe.py
```

本地环境通常可能没有 Gaussian、Slurm 或 HPC 相关工具。默认建议优先运行 mock/replay 演示和 real-tool-only 演示；只有在本地确认 Gaussian 已安装并可执行时，再显式打开 Gaussian gate。

## 7. Gaussian demo gate 说明

Gaussian 相关演示脚本现在通过环境变量显式 gate，避免在没有 Gaussian 的机器上误触发真实计算。

关键环境变量：

- `ARCHE_RUN_GAUSSIAN_TESTS=1`：明确允许运行真实 Gaussian 演示。
- `GAUSSIAN_COMMAND=g16`：指定 Gaussian 执行命令，默认约定为 `g16`。
- `GAUSSIAN_MODULE_LOAD`：可选，用于需要 module load 的服务器环境。
- `GAUSSIAN_ENV_HOOK`：可选，用于需要 source 环境脚本的服务器环境。

这样做的原因是：

- 避免在未安装 Gaussian 的本地或 CI 环境中误运行真实任务。
- 让现场演示是否运行真实 Gaussian 变成显式选择。
- 支持服务器和本地环境之间的差异。

## 8. 哪些路径没有改，为什么

以下路径或路径类别没有在本次 demo-readiness 路径硬化中直接修改：

- 文档中的服务器路径示例：`/home/lidong/ChemistryAgent` 是真实服务器部署路径，应保留为服务器示例。
- Gaussian 安装路径或命令路径：这属于外部科学计算环境配置，不是仓库内部路径。
- `GAUSS_SCRDIR`：这是 Gaussian scratch 目录，应由运行环境配置。
- Slurm/system 路径：属于服务器调度系统配置。
- 本地模型 checkpoint 路径，例如 legacy 默认的 `/home/lidong/model`：属于模型服务/模型文件部署配置。
- vendored third-party 工具内部路径：不应在本次脚本路径硬化中盲改。
- 外部 spectrum/tool 数据路径：可能指向用户数据或外部工具资源，不属于项目根目录硬编码。
- controller/agent 中的 core legacy fallback 路径：本次范围是测试/演示脚本和文档，不做运行时代码重构。

这些路径代表环境配置或外部工具依赖。后续工程化时，应将它们逐步收敛到配置文件、环境变量或部署参数中，但本次没有为了本地可运行而把服务器路径替换成本地绝对路径。

## 9. 对工程同事的使用建议

第一版演示建议显式设置 `ARCHE_PROJECT_ROOT`，这样服务器和本地行为最稳定。后续新增测试或演示脚本时，不要再引入新的 `/home/...` 或 `/Users/...` 项目根目录硬编码。

新增 Python 脚本应复用 `tests/scripts/path_utils.py`，并使用 `PROJECT_ROOT / ...` 定位仓库内部文件。新增 shell 脚本应优先读取 `ARCHE_PROJECT_ROOT`，否则从脚本所在目录推断项目根目录。

仓库内部路径应使用：

- `PROJECT_ROOT / "src"`
- `PROJECT_ROOT / "tests" / "inputs"`
- `PROJECT_ROOT / "tests" / "temp"`
- `PROJECT_ROOT / "tests" / "reports"`
- `PROJECT_ROOT / "src" / "chemistry_multiagent" / "tools" / "toolpool.json"`

外部系统、模型、Gaussian、Slurm、scratch 和用户数据路径应通过环境变量或配置传入。`tests/scripts/` 当前应视为内部演示/验证 harness，不是稳定公开 API；后续工程化时，关键脚本应逐步转换为带 fixtures 的 pytest 测试。

## 10. 当前仍需工程化补齐的内容

本次路径硬化解决的是 demo/test 脚本跨服务器和本地目录运行的问题，并不表示系统已经生产就绪。仍需补齐的工程化内容包括：

- formal workflow/run/step/artifact/job schemas
- MCP tool schemas
- frontend-visible state model
- dependency and deployment matrix
- model backend health/status API
- production job service abstraction
- retry/cancel/idempotency policy

这些内容应在后续 backend/frontend/MCP 工程实现前冻结或至少形成第一版规范。

## 11. 一句话结论

当前项目已经可以在服务器部署路径 `/home/lidong/ChemistryAgent` 和本地重命名后的 `ARCHE` 路径下，通过 `ARCHE_PROJECT_ROOT` 或根目录自动推断运行测试/演示脚本；工程同事后续应继续避免固定绝对项目路径。这个改动支持第一版专家试用和现场演示，但生产级 schema、服务边界和部署配置仍需继续工程化。
