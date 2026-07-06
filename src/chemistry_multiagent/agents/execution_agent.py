#!/usr/bin/env python3
"""
Execution Agent - 工作流执行智能体

功能:
1. 执行Planner agent生成的优化工作流
2. 解决工具输入输出格式匹配问题
3. 自动化执行计算化学工作流步骤
4. 监控执行状态,生成执行报告

输入: 优化的工作流步骤 + 工具定义
输出: 执行结果和报告
"""

import os
import sys
import json
import re
import shlex
import subprocess
import importlib
import inspect
import logging
import time
import datetime
import traceback
import uuid
import shutil
from enum import Enum
from typing import Dict, List, Any, Optional, Tuple, Union
from dataclasses import dataclass, asdict, field
from collections import defaultdict
import requests  # for potential HTTP calls to expert backend
from typing import Callable

# 添加项目根目录到Python路径
project_root = os.path.dirname(os.path.abspath(__file__))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

try:
    from chemistry_multiagent.utils.llm_api import call_deepseek_api
    LLM_API_AVAILABLE = True
except ImportError:
    LLM_API_AVAILABLE = False
    print("警告: utils.llm_api模块不可用")

try:
    from chemistry_multiagent.utils.pyscf_runner import dump_pyscf_log, pyscf_available, run_pyscf_job
except ImportError:
    try:
        from utils.pyscf_runner import dump_pyscf_log, pyscf_available, run_pyscf_job
    except ImportError:
        dump_pyscf_log = run_pyscf_job = None

        def pyscf_available() -> bool:  # type: ignore[redef]
            return False

try:
    from chemistry_multiagent.utils.arche_chem_client import call_arche_chem as shared_call_arche_chem
    ARCHE_CHEM_CALL_AVAILABLE = True
except ImportError:
    try:
        from utils.arche_chem_client import call_arche_chem as shared_call_arche_chem
        ARCHE_CHEM_CALL_AVAILABLE = True
    except ImportError:
        try:
            from arche_chem_client import call_arche_chem as shared_call_arche_chem
            ARCHE_CHEM_CALL_AVAILABLE = True
        except ImportError:
            shared_call_arche_chem = None
            ARCHE_CHEM_CALL_AVAILABLE = False

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
REPO_ROOT = os.path.abspath(os.path.join(project_root, "..", "..", ".."))

# ==================== 新增枚举和数据类 ====================

class StepStatus(str, Enum):
    """步骤状态枚举"""
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
    RETRYING = "retrying"


class ErrorCategory(str, Enum):
    """错误分类枚举"""
    TOOL_NOT_FOUND = "tool_not_found"
    INPUT_MISSING = "input_missing"
    FORMAT_MISMATCH = "format_mismatch"
    GAUSSIAN_NONCONVERGENCE = "gaussian_nonconvergence"
    GAUSSIAN_SCF_FAILURE = "gaussian_scf_failure"
    TS_INVALID = "ts_invalid"
    IRC_FAILURE = "irc_failure"
    PARSING_FAILURE = "parsing_failure"
    UNKNOWN_ERROR = "unknown_error"


class ValidationStatus(str, Enum):
    """验证状态枚举"""
    PASS = "pass"
    FAIL = "fail"
    WARNING = "warning"
    UNKNOWN = "unknown"


@dataclass
class ToolDefinition:
    """工具定义"""
    tool_name: str
    tool_path: str
    description: str
    input_format: Optional[str] = None
    output_format: Optional[str] = None
    is_pipeline: bool = False


@dataclass
class ExecutionStep:
    """执行步骤(增强版,向后兼容)"""
    # 原有字段(保持兼容)
    step_number: int
    description: str
    tool_name: str
    expected_input: str
    expected_output: str
    actual_input: Optional[str] = None
    actual_output: Optional[str] = None
    status: str = "pending"  # pending, running, success, failed
    error: Optional[str] = None
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    duration: Optional[float] = None

    # 新增结构化字段
    step_id: Optional[str] = None  # 唯一标识符,默认为 step_number 的字符串形式
    step_name: Optional[str] = None  # 步骤名称,可从 description 派生
    tool: Optional[ToolDefinition] = None  # 工具定义对象
    input_data: Optional[Dict[str, Any]] = None  # 结构化输入数据
    raw_output: Optional[Any] = None  # 原始输出(可能为字符串、字典等)
    output_files: List[str] = field(default_factory=list)  # 生成的输出文件路径
    parsed_results: Optional[Dict[str, Any]] = None  # 解析后的结果(如能量、频率等)
    validation: Optional[Dict[str, Any]] = None  # 验证结果 {status: ValidationStatus, details: str}
    error_info: Optional[Dict[str, Any]] = None  # 结构化错误信息 {category: ErrorCategory, message: str, repair_suggestion: str}
    # ARCHE-Chem expert fields
    scientific_context: Optional[Union[str, Dict[str, Any]]] = None
    route_section: Optional[str] = None
    job_type: Optional[str] = None  # redundant with parsed_results but kept for convenience
    preflight_check: Optional[Dict[str, Any]] = None
    gaussian_analysis: Optional[Dict[str, Any]] = None
    expert_error_analysis: Optional[Dict[str, Any]] = None
    working_directory: Optional[str] = None
    artifacts: List[Dict[str, Any]] = field(default_factory=list)

    def __post_init__(self):
        """初始化后处理"""
        if self.step_id is None:
            self.step_id = str(self.step_number)
        if self.step_name is None:
            self.step_name = self.description[:50]  # 截断
        # 将旧字段映射到新字段(如果可能)
        if self.actual_input is not None and self.input_data is None:
            self.input_data = {"raw_input": self.actual_input}
        if self.actual_output is not None and self.raw_output is None:
            self.raw_output = self.actual_output
        if self.error is not None and self.error_info is None:
            self.error_info = {
                "category": ErrorCategory.UNKNOWN_ERROR.value,
                "message": self.error,
                "repair_suggestion": "请检查工具配置和输入数据"
            }
        # 确保 status 是字符串(兼容枚举)
        if self.status not in [s.value for s in StepStatus]:
            # 尝试映射旧状态
            status_map = {"pending": StepStatus.PENDING, "running": StepStatus.RUNNING,
                         "success": StepStatus.SUCCESS, "failed": StepStatus.FAILED}
            if self.status in status_map:
                self.status = status_map[self.status].value


@dataclass
class ExecutionResult:
    """执行结果(增强版,向后兼容)"""
    # 原有字段(保持兼容)
    strategy_name: str
    total_steps: int
    successful_steps: int
    failed_steps: int
    success_rate: float
    total_duration: float
    steps: List[ExecutionStep]
    issues: List[str]
    final_output: Optional[Any] = None

    # 新增结构化摘要字段
    workflow_name: Optional[str] = None  # 工作流名称(同 strategy_name)
    overall_status: Optional[str] = None  # 总体状态(使用 StepStatus 枚举值)
    summary: Optional[Dict[str, Any]] = None  # 结构化摘要
    workflow_outcome: Optional[str] = None  # 科学工作流结果语义
    intermediate_artifacts: List[Dict[str, Any]] = field(default_factory=list)  # 工作流级中间产物
    metadata: Dict[str, Any] = field(default_factory=dict)  # 额外元数据
    # ARCHE-Chem expert fields
    expert_analysis_summary: Optional[Dict[str, Any]] = None
    expert_confidence: Optional[float] = None
    recommended_next_action: Optional[str] = None

    def __post_init__(self):
        """初始化后处理"""
        if self.workflow_name is None:
            self.workflow_name = self.strategy_name
        if self.overall_status is None:
            # 运行时执行状态:success / failed / partial_success
            if self.success_rate == 1.0:
                self.overall_status = StepStatus.SUCCESS.value
            elif self.failed_steps == self.total_steps:
                self.overall_status = StepStatus.FAILED.value
            elif self.successful_steps > 0:
                self.overall_status = "partial_success"
            else:
                self.overall_status = StepStatus.FAILED.value
        if self.summary is None:
            self.summary = {
                "workflow_name": self.workflow_name,
                "total_steps": self.total_steps,
                "successful_steps": self.successful_steps,
                "failed_steps": self.failed_steps,
                "success_rate": self.success_rate,
                "total_duration": self.total_duration,
                "issues_count": len(self.issues),
                "final_output_available": self.final_output is not None,
                "workflow_outcome": self.workflow_outcome or "unknown"
            }


class ExecutionAgent:
    """工作流执行智能体"""

    def __init__(self,
                 deepseek_api_key: Optional[str] = None,
                 toolpool_path: Optional[str] = None,
                 expert_model_name: str = "qwen2.5-7b-instruct",
                 expert_model_path: Optional[str] = None,
                 expert_backend: str = "local_hf",
                 enable_expert_analysis: bool = True,
                 gaussian_execution_mode: str = "api",
                 gaussian_command: Optional[str] = None,
                 gaussian_module_load: Optional[str] = None,
                 gaussian_environment_hook: Optional[str] = None,
                 gaussian_slurm_partition: Optional[str] = None,
                 gaussian_job_root: Optional[str] = None):
        """
        初始化执行智能体

        参数:
            deepseek_api_key: Deepseek API密钥
            toolpool_path: 工具定义文件路径
            expert_model_name: ARCHE-Chem专家模型名称 (默认 qwen2.5-7b-instruct)
            expert_model_path: 本地模型路径 (可选)
            expert_backend: 模型后端 (默认 local_hf)
            enable_expert_analysis: 是否启用专家分析 (默认 True)
            gaussian_execution_mode: Gaussian执行模式 api/local_shell/slurm (默认 api,已移除 replay 模拟)
            gaussian_command: Gaussian执行命令 (默认 g16)
            gaussian_module_load: 可选模块加载命令 (如 "module load gaussian")
            gaussian_environment_hook: 可选环境初始化命令
            gaussian_slurm_partition: 可选Slurm分区
            gaussian_job_root: Gaussian任务状态根目录
        """
        self.deepseek_api_key = deepseek_api_key or os.environ.get("DEEPSEEK_API_KEY", "")

        # 工具定义
        self.toolpool_path = toolpool_path or self._find_toolpool_path()
        self.tools = self._load_tools()

        # 文件格式转换映射
        self.format_converters = {
            ("smiles", "sdf"): "smiles_to_sdf",
            ("sdf", "xyz"): "sdf_to_xyz",
            ("xyz", "gjf"): "xyz_to_gjf",
            ("sdf", "gjf"): "sdf_to_gjf",
            ("gjf", "xyz"): "gjf_to_xyz",
            ("xyz", "sdf"): "xyz_to_sdf",
        }

        # 中间文件存储
        self.intermediate_files = {}
        # 执行状态
        self.execution_history = []

        # 新增配置属性
        self.max_step_retries = 1  # 最大重试次数(有限重试机制)
        self.current_step_retries = defaultdict(int)  # 步骤重试计数器
        self.step_timeout = 300.0  # 步骤超时时间(秒)
        # 跨步 .log 传递：解析步(output_parser/Get_gjf_from_log)常拿不到上一步真实产出的 Gaussian 日志
        # 路径（planner 的 expected_input 多为占位符），导致"缺少Gaussian日志文件"而失败。执行时按产出
        # 顺序累积本工作流的 .log/.out，解析步无候选时回退到最近一个存在的日志。每个工作流开始清空。
        self._recent_gaussian_logs: List[str] = []
        self._recent_gaussian_jsons: List[str] = []
        self.enable_validation = True  # 启用验证
        self.enable_error_recovery = True  # 启用错误恢复建议
        self.simulated_tools = []  # 已移除 mock/replay:不再有"模拟工具",全部真实执行或诚实抛错
        # ARCHE-Chem expert integration
        self.expert_model_name = expert_model_name
        self.expert_model_path = expert_model_path
        # 化学专家后端可经 env 覆盖：部署默认走 openai_compatible（ARCHE_CHEM_* 指向 interns2 API），
        # 无 GPU 也能审查高斯结果；不配则保持入参默认（local_hf）。
        self.expert_backend = os.environ.get("ARCHE_CHEM_BACKEND", expert_backend)
        self.enable_expert_analysis = enable_expert_analysis
        self.arche_chem_client = None
        if self.enable_expert_analysis:
            self.arche_chem_client = self._create_arche_chem_client()
        self.validation_critical_types = {"opt", "ts", "irc", "sp"}

        # 已移除 replay(mock)模式:env 优先,默认真实 api 后端;未知/已废弃模式一律回退到 api 真实执行。
        requested_mode = (os.environ.get("GAUSSIAN_EXECUTION_MODE", "").strip().lower() or (gaussian_execution_mode or "api").strip().lower())
        if requested_mode not in {"local_shell", "slurm", "api"}:
            logger.warning(f"未知/已废弃的 gaussian_execution_mode={requested_mode},回退到真实 api 后端")
            requested_mode = "api"
        self.gaussian_execution_mode = requested_mode
        # Gaussian 远程 API 后端（mode=api）：把 .gjf 内容 POST 给 Gaussian-as-a-Service，同步返回 log。
        # 两层鉴权（ingress Basic Auth + x-api-key），全部 env 注入、部署可覆盖。
        self.gaussian_api_base_url = (os.environ.get("GAUSSIAN_BASE_URL", "") or "").rstrip("/")
        self.gaussian_api_key = os.environ.get("GAUSSIAN_API_KEY", "")
        # ingress Basic Auth(AK/SK)与 interns2/化学专家 同一套网关凭证，复用 ARCHE_CHEM_INGRESS_*，
        # 部署无需为高斯单独再配一遍（GAUSSIAN_INGRESS_* 仅作可选覆盖）。
        self.gaussian_api_ingress_ak = os.environ.get("GAUSSIAN_INGRESS_AK") or os.environ.get("ARCHE_CHEM_INGRESS_AK", "")
        self.gaussian_api_ingress_sk = os.environ.get("GAUSSIAN_INGRESS_SK") or os.environ.get("ARCHE_CHEM_INGRESS_SK", "")
        self.gaussian_api_timeout = int(os.environ.get("GAUSSIAN_API_TIMEOUT", "1200"))
        self.gaussian_api_max_retries = max(0, int(os.environ.get("GAUSSIAN_API_MAX_RETRIES", "2")))
        self.gaussian_api_retry_backoff = max(0.0, float(os.environ.get("GAUSSIAN_API_RETRY_BACKOFF_SECONDS", "2")))
        self.local_pyscf_available = bool(pyscf_available())
        self.enable_local_pyscf_fallback = os.environ.get("ARCHE_ENABLE_PYSCF_FALLBACK", "1") != "0"
        self.gaussian_command = gaussian_command or os.environ.get("GAUSSIAN_COMMAND", "g16")
        self.gaussian_module_load = gaussian_module_load or os.environ.get("GAUSSIAN_MODULE_LOAD")
        self.gaussian_environment_hook = gaussian_environment_hook or os.environ.get("GAUSSIAN_ENV_HOOK")
        self.gaussian_slurm_partition = gaussian_slurm_partition or os.environ.get("GAUSSIAN_SLURM_PARTITION")
        self.gaussian_job_root = gaussian_job_root or os.environ.get("GAUSSIAN_JOB_ROOT") or os.path.join(project_root, "gaussian_jobs")
        os.makedirs(self.gaussian_job_root, exist_ok=True)

        self.tools_root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "tools"))
        self.real_top_level_tool_files = {
            "smiles2sdf.py", "sdf2gjf.py", "sdf_to_xyz.py", "xyz2gjf.py", "stereoisomer.py",
            "Get_gjf_from_log.py", "gen_conformation.py", "gen_gaussiancode.py",
            "output_parser.py", "process_spectrum.py", "plot_spectrum.py",
            "Ni_plot.py", "plot_tools.py", "reaction_thermochemistry.py"
        }
        self.real_tool_name_to_file = {
            "smiles_to_sdf": "smiles2sdf.py",
            "sdf_to_gjf": "sdf2gjf.py",
            "sdf_to_xyz": "sdf_to_xyz.py",
            "xyz_to_gjf": "xyz2gjf.py",
            "enumerate_stereoisomers": "stereoisomer.py",
            "stereoisomer": "stereoisomer.py",
            "get_gjf_from_log": "Get_gjf_from_log.py",
            "generate_conformations": "gen_conformation.py",
            "generate_visualize_and_save_conformers": "gen_conformation.py",
            "main": os.path.join("23_TSPipeline", "run.py"),
            "ts_pipeline": os.path.join("23_TSPipeline", "run.py"),
            "generate_gaussian_code": "gen_gaussiancode.py",
            "generate_gaussian_code_result": "gen_gaussiancode.py",
            "parse_gaussian_output": "output_parser.py",
            "run_spectrum_pipeline": "process_spectrum.py",
            "generate_spectrum_plot": "plot_spectrum.py",
            "plot_ni_spectrum": "Ni_plot.py",
            "draw_spectrum_from_file": "plot_tools.py",
            "draw_spectrum": "plot_tools.py",
            "compute_reaction_thermochemistry": "reaction_thermochemistry.py",
        }

        logger.info(f"Execution Agent 初始化完成,加载了 {len(self.tools)} 个工具")

    def _find_toolpool_path(self) -> str:
        """查找工具定义文件路径"""
        # 优先查找当前项目
        local_path = os.path.join(project_root, "toolpool", "toolpool.json")
        if os.path.exists(local_path):
            return local_path

        # 显式环境变量优先（替代历史上硬编码的开发机绝对路径 /Users/lidong/...）。
        env_path = os.environ.get("ARCHE_TOOLPOOL_PATH")
        if env_path and os.path.exists(env_path):
            return env_path

        # 仓库内置工具定义：src/chemistry_multiagent/tools/toolpool.json（部署容器里走这条）。
        bundled_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "toolpool.json"
        )
        if os.path.exists(bundled_path):
            return bundled_path

        # 创建默认工具定义
        logger.warning("未找到工具定义文件,使用默认工具")
        return os.path.join(project_root, "toolpool", "toolpool.json")

    def _load_tools(self) -> Dict[str, ToolDefinition]:
        """加载工具定义"""
        if not os.path.exists(self.toolpool_path):
            logger.warning(f"工具定义文件不存在: {self.toolpool_path}")
            return self._create_default_tools()

        try:
            with open(self.toolpool_path, "r", encoding="utf-8") as f:
                tool_data = json.load(f)
        except Exception as e:
            logger.error(f"加载工具定义失败: {e}")
            return self._create_default_tools()

        tools = {}
        for item in tool_data:
            tool_name = item.get("tool_name", "")
            if not tool_name:
                continue

            # 从描述中提取输入输出格式
            description = item.get("description", "")
            input_format, output_format = self._extract_formats_from_description(description)

            tool = ToolDefinition(
                tool_name=tool_name,
                tool_path=item.get("tool_path", ""),
                description=description,
                input_format=input_format,
                output_format=output_format,
                is_pipeline=item.get("pipline", False) or False
            )
            tools[tool_name] = tool

        return tools

    def _create_default_tools(self) -> Dict[str, ToolDefinition]:
        """创建默认工具定义"""
        default_tools = [
            {
                "tool_name": "generate_gaussian_code",
                "tool_path": "gaussian.tools.generate_gaussian_code",
                "description": "Generate Gaussian input file keywords and route section based on calculation requirements."
            },
            {
                "tool_name": "main",
                "tool_path": "../tools/23_TSPipeline/run.py",
                "description": "Generate initial transition state guess structures from SMILES inputs."
            },
            {
                "tool_name": "xyz_to_gjf",
                "tool_path": "gaussian.tools.xyz_to_gjf",
                "description": "Convert XYZ coordinates and Gaussian keywords to GJF input file."
            },
            {
                "tool_name": "smiles_to_sdf",
                "tool_path": "rdkit.tools.smiles_to_sdf",
                "description": "Convert SMILES string to SDF molecular structure file."
            },
            {
                "tool_name": "sdf_to_xyz",
                "tool_path": "../tools/sdf_to_xyz.py",
                "description": "Convert SDF molecular structure to XYZ coordinates."
            },
            {
                "tool_name": "compute_reaction_thermochemistry",
                "tool_path": "../tools/reaction_thermochemistry.py",
                "description": "Compute reaction enthalpy or related thermochemistry from parsed JSON files using reaction stoichiometry."
            }
        ]

        tools = {}
        for item in default_tools:
            tool_name = item["tool_name"]
            description = item["description"]
            input_format, output_format = self._extract_formats_from_description(description)

            tool = ToolDefinition(
                tool_name=tool_name,
                tool_path=item["tool_path"],
                description=description,
                input_format=input_format,
                output_format=output_format
            )
            tools[tool_name] = tool

        return tools

    def _extract_formats_from_description(self, description: str) -> Tuple[Optional[str], Optional[str]]:
        """从描述中提取输入输出格式"""
        input_format = None
        output_format = None

        # 常见格式关键词
        format_keywords = {
            "SMILES": "smiles",
            "XYZ": "xyz",
            "SDF": "sdf",
            "GJF": "gjf",
            "Gaussian input": "gjf",
            ".gjf": "gjf",
            ".xyz": "xyz",
            ".sdf": "sdf"
        }

        # 查找输入格式
        input_patterns = [
            r"input.*?([A-Z]+(?:[-_][A-Z]+)*) files?",
            r"takes.*?([A-Z]+(?:[-_][A-Z]+)*)",
            r"from.*?([A-Z]+(?:[-_][A-Z]+)*)",
            r"Input:.*?([A-Z]+(?:[-_][A-Z]+)*)"
        ]

        description_lower = description.lower()
        for pattern in input_patterns:
            match = re.search(pattern, description, re.IGNORECASE)
            if match:
                format_str = match.group(1).upper()
                for keyword, format_code in format_keywords.items():
                    if keyword.upper() in format_str:
                        input_format = format_code
                        break
                if input_format:
                    break

        # 查找输出格式
        output_patterns = [
            r"output.*?([A-Z]+(?:[-_][A-Z]+)*) files?",
            r"returns.*?([A-Z]+(?:[-_][A-Z]+)*)",
            r"produces.*?([A-Z]+(?:[-_][A-Z]+)*)",
            r"Output:.*?([A-Z]+(?:[-_][A-Z]+)*)",
            r"saves.*?as.*?([A-Z]+(?:[-_][A-Z]+)*)"
        ]

        for pattern in output_patterns:
            match = re.search(pattern, description, re.IGNORECASE)
            if match:
                format_str = match.group(1).upper()
                for keyword, format_code in format_keywords.items():
                    if keyword.upper() in format_str:
                        output_format = format_code
                        break
                if output_format:
                    break

        return input_format, output_format

    # ==================== ARCHE-Chem expert integration ====================

    def _create_arche_chem_client(self):
        """Create ARCHE-Chem expert client (optional)."""
        # 优先使用共享函数接口,此处仅尝试类式客户端作为回退
        try:
            from chemistry_multiagent.utils.arche_chem_client import ArcheChemClient
            logger.info("Using ARCHE-Chem client from chemistry_multiagent.utils.arche_chem_client")
            return ArcheChemClient(
                model_name=self.expert_model_name,
                model_path=self.expert_model_path,
                backend=self.expert_backend
            )
        except ImportError:
            pass

        try:
            from utils.arche_chem_client import ArcheChemClient
            logger.info("Using ARCHE-Chem client from utils.arche_chem_client")
            return ArcheChemClient(
                model_name=self.expert_model_name,
                model_path=self.expert_model_path,
                backend=self.expert_backend
            )
        except ImportError:
            pass

        try:
            from arche_chem_client import ArcheChemClient
            logger.info("Using ARCHE-Chem client from arche_chem_client.py")
            return ArcheChemClient(
                model_name=self.expert_model_name,
                model_path=self.expert_model_path,
                backend=self.expert_backend
            )
        except ImportError:
            logger.warning("arche_chem_client module not found. Expert analysis will fallback to rule-based logic.")
            return None

    def call_arche_chem(self, messages: List[Dict[str, str]], max_tokens: int = 512, temperature: float = 0.1):
        """Backward-compatible wrapper returning expert text (or None)."""
        text, _audit = self._call_expert_with_fallback(messages, max_tokens=max_tokens, temperature=temperature)
        return text

    def _call_expert_with_fallback(self,
                                   messages: List[Dict[str, str]],
                                   max_tokens: int = 1024,
                                   temperature: float = 0.2) -> Tuple[Optional[str], Dict[str, Any]]:
        """Backend priority: local ARCHE-Chem -> DeepSeek -> rule-based."""
        audit: Dict[str, Any] = {
            "expert_backend_requested": self.expert_backend,
            "expert_backend_used": None,
            "expert_fallback_triggered": False,
            "expert_fallback_reason": None,
            "expert_fallback_model": None,
            "expert_analysis_source": None,
        }

        if not self.enable_expert_analysis:
            audit["expert_backend_used"] = "rule_based"
            audit["expert_analysis_source"] = "rule_based"
            audit["expert_fallback_triggered"] = True
            audit["expert_fallback_reason"] = "expert_analysis_disabled"
            return None, audit

        local_errors: List[str] = []

        if ARCHE_CHEM_CALL_AVAILABLE and shared_call_arche_chem is not None:
            try:
                text = shared_call_arche_chem(
                    messages=messages,
                    model=self.expert_model_name,
                    model_path=self.expert_model_path,
                    backend=self.expert_backend,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
                audit["expert_backend_used"] = self.expert_backend
                audit["expert_analysis_source"] = "local_arche_chem"
                return text, audit
            except Exception as e:
                local_errors.append(f"shared_call_failed: {e}")

        if self.arche_chem_client is not None:
            try:
                text = self.arche_chem_client.generate(messages, max_tokens=max_tokens, temperature=temperature)
                audit["expert_backend_used"] = self.expert_backend
                audit["expert_analysis_source"] = "local_arche_chem"
                return text, audit
            except Exception as e:
                local_errors.append(f"client_generate_failed: {e}")
        else:
            local_errors.append("local_client_unavailable")

        deepseek_error = None
        audit["expert_fallback_triggered"] = True
        if LLM_API_AVAILABLE:
            fallback_model = os.environ.get("DEEPSEEK_MODEL", "interns2-preview-sft")
            try:
                text = call_deepseek_api(
                    messages,
                    model=fallback_model,
                    max_tokens=int(max_tokens),
                    temperature=float(temperature),
                )
                audit["expert_backend_used"] = "deepseek_api"
                audit["expert_fallback_model"] = fallback_model
                audit["expert_analysis_source"] = "deepseek_fallback"
                audit["expert_fallback_reason"] = "; ".join(local_errors) if local_errors else None
                return text, audit
            except Exception as e:
                deepseek_error = str(e)

        reasons = list(local_errors)
        if deepseek_error:
            reasons.append(f"deepseek_fallback_failed: {deepseek_error}")
        elif not LLM_API_AVAILABLE:
            reasons.append("deepseek_api_unavailable")

        audit["expert_backend_used"] = "rule_based"
        audit["expert_analysis_source"] = "rule_based"
        audit["expert_fallback_model"] = os.environ.get("DEEPSEEK_MODEL", "interns2-preview-sft") if LLM_API_AVAILABLE else None
        audit["expert_fallback_reason"] = "; ".join(reasons) if reasons else "unknown"
        logger.warning(f"Expert model unavailable, fallback to rule-based: {audit['expert_fallback_reason']}")
        return None, audit

    def _rule_based_gaussian_result_analysis(self,
                                             step: ExecutionStep,
                                             parsed_output: Dict[str, Any]) -> Dict[str, Any]:
        """Final fallback analysis when expert backends are unavailable."""
        job_type = parsed_output.get("job_type") or self._detect_step_job_type(step)
        converged = parsed_output.get("converged")
        n_imag = parsed_output.get("n_imag_freq")
        normal_termination = parsed_output.get("normal_termination")

        notes = []
        if normal_termination is False:
            notes.append("normal_termination_failed")
        if job_type in {"opt", "ts"} and converged is False:
            notes.append("geometry_not_converged")
        if job_type == "ts":
            if isinstance(n_imag, int) and n_imag != 1:
                notes.append("ts_imaginary_frequency_check_failed")

        recommended_next_action = "Continue workflow with standard validation checks"
        if notes:
            recommended_next_action = "Revise Gaussian setup or validate TS/frequency/IRC conditions before proceeding"

        return {
            "status": "rule_based_fallback",
            "analysis": f"Rule-based Gaussian analysis fallback. Checks: {', '.join(notes) if notes else 'basic checks passed'}",
            "analysis_json": {
                "job_type": job_type,
                "normal_termination": normal_termination,
                "converged": converged,
                "n_imag_freq": n_imag,
                "flags": notes,
            },
            "job_type": job_type,
            "expert_confidence": 0.35,
            "recommended_next_action": recommended_next_action,
        }

    def _rule_based_gaussian_error_analysis(self,
                                            step: ExecutionStep,
                                            error_text: str) -> Dict[str, Any]:
        """Final fallback error diagnosis when expert backends are unavailable."""
        category, repair = self.classify_execution_error(Exception(str(error_text)))
        return {
            "status": "rule_based_fallback",
            "analysis": f"Rule-based Gaussian error diagnosis fallback: {str(category.value)}",
            "analysis_json": {
                "error_category": str(category.value),
                "repair_suggestions": repair,
            },
            "error_category": str(category.value),
            "repair_suggestions": repair,
        }

    def is_gaussian_related_tool(self, tool: ToolDefinition) -> bool:
        """Check if tool is Gaussian-related."""
        if not tool:
            return False
        tool_path = tool.tool_path.lower()
        tool_name = tool.tool_name.lower()
        return "gaussian" in tool_path or "gaussian" in tool_name

    def _extract_json_object_safe(self, raw_text: Any) -> Dict[str, Any]:
        """从模型响应中提取JSON对象,失败返回空字典。"""
        if raw_text is None:
            return {}
        text = str(raw_text)
        cleaned = re.sub(r"```(?:json)?", "", text, flags=re.IGNORECASE).strip()
        match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if not match:
            return {}
        try:
            return json.loads(match.group(0))
        except Exception:
            return {}

    def _scientific_context_to_dict(self, scientific_context: Any) -> Dict[str, Any]:
        if isinstance(scientific_context, dict):
            return dict(scientific_context)
        if isinstance(scientific_context, str) and scientific_context.strip():
            return {"summary": scientific_context.strip()}
        return {}

    def _build_step_scientific_context(self, step: ExecutionStep) -> Dict[str, Any]:
        context = self._scientific_context_to_dict(step.scientific_context)
        if not context:
            context = {}
        context.setdefault("job_type", step.job_type or self._detect_step_job_type(step))
        context.setdefault("route_section", step.route_section)
        context.setdefault("scientific_question", context.get("scientific_question") or context.get("question"))
        context.setdefault("calculation_purpose", context.get("calculation_purpose") or step.description)
        context.setdefault("solvent", context.get("solvent"))
        context.setdefault("charge", context.get("charge"))
        context.setdefault("multiplicity", context.get("multiplicity"))
        context.setdefault("expected_elements", context.get("expected_elements") or context.get("elements") or [])
        context.setdefault("expected_validation_requirements", context.get("expected_validation_requirements") or context.get("validation_plan") or [])
        return context

    def preflight_check_gaussian_step(self, step: ExecutionStep) -> Dict[str, Any]:
        """Run expert-aware preflight check for Gaussian-related step."""
        context = self._build_step_scientific_context(step)
        route_section = context.get("route_section") or ""
        job_type = context.get("job_type") or "unknown"
        solvent = context.get("solvent")
        charge = context.get("charge")
        multiplicity = context.get("multiplicity")
        expected_elements = context.get("expected_elements") or []
        validation_requirements = context.get("expected_validation_requirements") or []

        checklist = {
            "job_type_available": bool(job_type and job_type != "unknown"),
            "route_section_available": bool(route_section),
            "solvent_specified": solvent is not None,
            "charge_multiplicity_specified": charge is not None and multiplicity is not None,
            "expected_elements_available": bool(expected_elements),
            "validation_requirements_available": bool(validation_requirements),
        }

        if not self.enable_expert_analysis:
            return {
                "status": "skipped",
                "reason": "expert_analysis_disabled",
                "checklist": checklist,
                "scientific_context": context,
                "route_section": route_section,
                "job_type": job_type,
            }

        prompt = (
            "请对以下Gaussian计算步骤进行预检检查并返回JSON。\n"
            f"步骤描述: {step.description}\n"
            f"科学上下文: {json.dumps(context, ensure_ascii=False)}\n"
            f"路线段: {route_section}\n"
            f"任务类型: {job_type}\n"
            f"溶剂: {solvent}\n"
            f"电荷: {charge}\n"
            f"自旋多重度: {multiplicity}\n"
            f"预期元素: {expected_elements}\n"
            f"验证要求: {validation_requirements}\n"
            "请判断该设置是否合理,指出风险并给出修订建议。"
        )

        messages = [
            {"role": "system", "content": "你是计算化学专家，擅长Gaussian软件工作流预检。"},
            {"role": "user", "content": prompt},
        ]

        analysis, audit = self._call_expert_with_fallback(messages, max_tokens=768, temperature=0.1)
        if analysis is None:
            return {
                "status": "rule_based_fallback",
                "reason": "expert_backend_unavailable",
                "checklist": checklist,
                "scientific_context": context,
                "route_section": route_section,
                "job_type": job_type,
                **audit,
            }

        parsed_analysis = self._extract_json_object_safe(analysis)
        return {
            "status": "completed",
            "analysis": analysis,
            "analysis_json": parsed_analysis,
            "checklist": checklist,
            "scientific_context": context,
            "route_section": route_section,
            "job_type": job_type,
            **audit,
        }

    def build_gaussian_analysis_prompt(self, step: ExecutionStep, raw_output, parsed_output, scientific_context) -> str:
        """Build prompt for expert analysis of Gaussian results."""
        ctx = self._scientific_context_to_dict(scientific_context)
        job_type = parsed_output.get("job_type", "unknown")
        energy = parsed_output.get("scf_energy")
        converged = parsed_output.get("converged")
        n_imag_freq = parsed_output.get("n_imag_freq")

        prompt = f"""请分析以下Gaussian计算结果并尽量返回JSON:
步骤描述: {step.description}
任务类型: {job_type}
科学上下文: {json.dumps(ctx, ensure_ascii=False)}
路线段: {step.route_section}
能量 (Hartree): {energy}
几何收敛: {converged}
虚频数量: {n_imag_freq}

原始输出预览:
{str(raw_output)[:1000] if raw_output else "无"}

解析结果摘要:
{json.dumps(parsed_output, indent=2, ensure_ascii=False)[:1500]}

请重点回答:
1. 此计算是否达到了预期科学目的?
2. 是否存在技术警告信号?
3. 若为TS任务,虚频行为是否合理(是否近似单一反应坐标)?
4. 是否建议IRC/后续单点或更高精度修正?
5. 建议下一步动作。
"""
        return prompt

    def analyze_gaussian_result_with_arche_chem(self, step: ExecutionStep, raw_output, parsed_output, scientific_context) -> Dict[str, Any]:
        """Invoke expert analysis for successful Gaussian results."""
        if not self.enable_expert_analysis:
            fallback = self._rule_based_gaussian_result_analysis(step, parsed_output)
            fallback.update({
                "reason": "expert_analysis_disabled",
                "expert_backend_requested": self.expert_backend,
                "expert_backend_used": "rule_based",
                "expert_fallback_triggered": True,
                "expert_fallback_reason": "expert_analysis_disabled",
                "expert_fallback_model": None,
                "expert_analysis_source": "rule_based",
            })
            return fallback

        prompt = self.build_gaussian_analysis_prompt(step, raw_output, parsed_output, scientific_context)
        messages = [{"role": "system", "content": "你是计算化学专家，擅长Gaussian输出分析。"},
                    {"role": "user", "content": prompt}]

        analysis, audit = self._call_expert_with_fallback(messages, max_tokens=1024, temperature=0.2)
        if analysis is None:
            fallback = self._rule_based_gaussian_result_analysis(step, parsed_output)
            fallback.update(audit)
            fallback["reason"] = "expert_backend_unavailable"
            return fallback
        parsed_analysis = self._extract_json_object_safe(analysis)
        recommended_next_action = parsed_analysis.get("recommended_next_action") if parsed_analysis else None
        return {
            "status": "completed",
            "analysis": analysis,
            "analysis_json": parsed_analysis,
            "job_type": parsed_output.get("job_type"),
            "expert_confidence": parsed_analysis.get("expert_confidence") if parsed_analysis else None,
            "recommended_next_action": recommended_next_action,
            **audit,
        }

    def build_gaussian_error_prompt(self, step: ExecutionStep, error_text, scientific_context) -> str:
        """Build prompt for expert diagnosis of Gaussian errors."""
        ctx = self._scientific_context_to_dict(scientific_context)
        prompt = f"""请诊断以下Gaussian计算错误:
步骤描述: {step.description}
错误信息:
{error_text[:2000]}

科学上下文: {json.dumps(ctx, ensure_ascii=False)}
任务类型: {step.job_type or self._detect_step_job_type(step)}
路线段: {step.route_section}

请帮助分类可能的根本原因:
- 路线段问题
- 错误的任务类型
- 结构猜测不佳
- SCF问题
- 收敛问题
- 基组/元素不匹配
- 溶剂/模型不匹配
- 输入格式或解析问题

请给出针对性的修复建议。"""
        return prompt

    def analyze_gaussian_error_with_arche_chem(self, step: ExecutionStep, error_text, scientific_context) -> Dict[str, Any]:
        """Invoke expert diagnosis for failed Gaussian runs."""
        if not self.enable_expert_analysis:
            fallback = self._rule_based_gaussian_error_analysis(step, str(error_text))
            fallback.update({
                "reason": "expert_analysis_disabled",
                "expert_backend_requested": self.expert_backend,
                "expert_backend_used": "rule_based",
                "expert_fallback_triggered": True,
                "expert_fallback_reason": "expert_analysis_disabled",
                "expert_fallback_model": None,
                "expert_analysis_source": "rule_based",
            })
            return fallback

        prompt = self.build_gaussian_error_prompt(step, error_text, scientific_context)
        messages = [{"role": "system", "content": "你是计算化学专家，擅长Gaussian错误诊断。"},
                    {"role": "user", "content": prompt}]

        analysis, audit = self._call_expert_with_fallback(messages, max_tokens=1024, temperature=0.2)
        if analysis is None:
            fallback = self._rule_based_gaussian_error_analysis(step, str(error_text))
            fallback.update(audit)
            fallback["reason"] = "expert_backend_unavailable"
            return fallback
        parsed_analysis = self._extract_json_object_safe(analysis)
        return {
            "status": "completed",
            "analysis": analysis,
            "analysis_json": parsed_analysis,
            "error_category": parsed_analysis.get("error_category") if parsed_analysis else "unknown",
            "repair_suggestions": parsed_analysis.get("repair_suggestions") if parsed_analysis else "",
            **audit,
        }

    # ==================== 工具匹配 ====================

    def find_tool(self, step_description: str) -> Optional[ToolDefinition]:
        """
        根据步骤描述查找合适的工具

        使用LLM进行语义匹配,回退到关键词匹配
        """
        if not self.tools:
            return None

        # 先尝试关键词匹配(更快)
        tool = self._fallback_tool_match(step_description)
        if tool:
            return tool

        # 如果关键词匹配失败,使用LLM语义匹配(需要API)
        if not LLM_API_AVAILABLE:
            return None

        # 构建工具列表供LLM选择
        tools_info = []
        for tool_name, tool in self.tools.items():
            tools_info.append(f"- {tool_name}: {tool.description[:100]}...")

        tools_text = "\n".join(tools_info)

        prompt = f"""
        根据以下步骤描述,从工具列表中选择最合适的工具:

        步骤描述: {step_description}

        可用工具:
        {tools_text}

        请返回工具名称(只需要名称,不要其他内容)。
        如果没有合适的工具,返回 "NO_MATCH"。
        """

        try:
            response = call_deepseek_api([
                {"role": "system", "content": "你是计算化学工具选择专家。"},
                {"role": "user", "content": prompt}
            ], model=os.environ.get("DEEPSEEK_MODEL", "interns2-preview-sft"), max_tokens=600)

            tool_name = response.strip().strip('"').strip("'")

            if tool_name == "NO_MATCH" or tool_name not in self.tools:
                return None

            return self.tools[tool_name]

        except Exception as e:
            logger.error(f"工具选择失败: {e}")
            return None

    def _fallback_tool_match(self, step_description: str) -> Optional[ToolDefinition]:
        """回退方法:基于关键词的工具匹配"""
        description_lower = step_description.lower()

        # 关键词映射
        keyword_mapping = {
            "gaussian": ["gaussian", "gjf", "input file", "route", "keywords"],
            "smiles": ["smiles", "sdf", "convert", "molecule"],
            "xyz": ["xyz", "geometry", "coordinate", "structure"],
            "optimize": ["optimize", "optimization", "relax", "geometry"],
            "transition state": ["transition state", "ts", "ts search", "saddle point"],
            "frequency": ["frequency", "vibrational", "ir", "vibration"],
            "conformer": ["conformer", "conformation", "ensemble", "rotamer"],
            "dock": ["dock", "docking", "alignment", "orient"],
            "spectrum": ["spectrum", "spectral", "ir spectrum", "raman"]
        }

        best_match = None
        best_score = 0

        for tool_name, tool in self.tools.items():
            score = 0

            # 检查工具描述中的关键词
            tool_desc_lower = tool.description.lower()
            for keyword_group in keyword_mapping.values():
                for keyword in keyword_group:
                    if keyword in description_lower and keyword in tool_desc_lower:
                        score += 2
                    elif keyword in description_lower or keyword in tool_desc_lower:
                        score += 1

            # 检查工具名称匹配
            tool_name_lower = tool_name.lower()
            if tool_name_lower in description_lower:
                score += 3

            if score > best_score:
                best_score = score
                best_match = tool

        return best_match if best_score > 0 else None

    # ==================== 格式转换 ====================

    def resolve_format_mismatch(self,
                               current_format: str,
                               required_format: str,
                               data: Any) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        解决格式不匹配问题

        返回:
            (success, converted_data, converter_tool)
        """
        if current_format == required_format:
            return True, data, None

        converter_key = (current_format, required_format)

        if converter_key in self.format_converters:
            converter_tool = self.format_converters[converter_key]

            # 检查是否有相应的工具
            if converter_tool in self.tools:
                return False, data, converter_tool
            else:
                logger.warning(f"格式转换工具 '{converter_tool}' 不存在")

        # 使用LLM生成转换代码(如果需要)
        if LLM_API_AVAILABLE:
            try:
                converted_data = self._generate_conversion_code(
                    current_format, required_format, data
                )
                return True, converted_data, "llm_generated"
            except Exception as e:
                logger.warning(f"LLM格式转换失败: {e}")

        return False, None, None

    def _generate_conversion_code(self,
                                 from_format: str,
                                 to_format: str,
                                 data: Any) -> str:
        """使用LLM生成格式转换代码"""
        prompt = f"""
        将以下{from_format.upper()}格式的数据转换为{to_format.upper()}格式:

        数据:
        {data}

        请只返回转换后的数据,不要解释。
        """

        response = call_deepseek_api([
            {"role": "system", "content": "你是计算化学数据格式转换专家。"},
            {"role": "user", "content": prompt}
        ], model=os.environ.get("DEEPSEEK_MODEL", "interns2-preview-sft"), max_tokens=1000)

        return response.strip()

    # ==================== 工具执行 ====================

    def execute_tool(self, tool: ToolDefinition, input_data: Any, step: Optional[ExecutionStep] = None) -> Any:
        """
        执行工具(增强版):
        1) 顶层标准化工具优先真实执行
        2) Gaussian相关工具走真实后端(api/local_shell/slurm)
        3) 其他工具真实执行或诚实抛错(已移除所有 mock/replay 回退)
        """
        try:
            tool_path = str(tool.tool_path or "")

            # 执行意图拦截:planner 常把"执行 Gaussian"步骤钳成 generate_gaussian_code(只生成代码、永不真跑),
            # 导致真实后端零调用、最终谎报成功。识别 execute 意图 → 走确定性真实链(分子→3D→gjf→真实 Gaussian API→解析)。
            if self._is_gaussian_execution_intent(step, tool):
                direct_job = self._execute_existing_gaussian_job(step, input_data, tool)
                if direct_job is not None:
                    logger.info(f"[确定性Gaussian] 执行步骤直接复用现成.gjf真跑: {str(getattr(step, 'description', ''))[:60]}")
                    return direct_job
                det = self._deterministic_gaussian_calc(step, input_data, tool)
                if det is not None:
                    logger.info(f"[确定性Gaussian] 执行步骤改走确定性真实链: {str(getattr(step, 'description', ''))[:60]}")
                    return det
                raise RuntimeError("Gaussian执行步骤无法定位可执行的 .gjf 或单一目标分子，拒绝回退到 generate_gaussian_code 伪成功")

            eligible, script_path, reason = self._is_real_tool_eligible(tool, step=step)
            if eligible and script_path:
                logger.info(f"[RealTool] 真实执行顶层工具: {tool.tool_name} ({script_path})")
                return self._execute_real_top_level_tool(tool, input_data, step=step, script_path=script_path)
            if tool_path.endswith(".py") or "tools" in tool_path:
                logger.info(f"[RealTool] 工具不在本批次真实执行白名单或不可解析: {tool.tool_name}, reason={reason}")

            if tool_path.startswith("gaussian.tools."):
                logger.info(f"[GaussianJob] 执行Gaussian任务后端: {tool.tool_name}")
                return self.execute_gaussian_related_tool(tool.tool_name, input_data, step=step, tool=tool)

            if tool_path.endswith(".py"):
                script_candidate = os.path.join(project_root, tool_path)
                if os.path.exists(script_candidate):
                    return self.execute_python_tool(tool_path, input_data)
                raise RuntimeError(f"脚本文件不存在,拒绝任何 mock 回退: {script_candidate}")

            # 已彻底移除 mock/replay 回退:工具无法真实执行(含 rdkit/openbabel/通用)一律诚实抛错,绝不返回伪造结果。
            raise RuntimeError(
                f"工具 '{tool.tool_name}' (path={tool_path}) 无法真实执行,已禁用所有 mock/replay: {reason or '不在真实执行白名单或不可解析'}"
            )

        except Exception as e:
            logger.error(f"工具执行失败: {e}")
            return {
                "success": False,
                "tool_name": tool.tool_name,
                "tool_path": tool.tool_path,
                "execution_mode": "real_tool",
                "execution_backend": "python_import",
                "raw_result": None,
                "output_artifacts": [],
                "message": "工具执行异常",
                "error": str(e),
            }

    # ==================== 归一化Gaussian结果Schema ====================

    def _create_normalized_gaussian_schema(self) -> Dict[str, Any]:
        """
        创建归一化的Gaussian解析结果schema

        返回:
            包含所有可能字段的归一化schema,未知字段为None
        """
        return {
            "job_type": "unknown",  # "opt" | "ts" | "freq" | "irc" | "sp" | "unknown"
            "normal_termination": None,  # True | False | None
            "converged": None,  # True | False | None (几何优化收敛)
            "scf_converged": None,  # True | False | None (SCF收敛)
            "scf_energy": None,  # float | None (Hartree)
            "free_energy": None,  # float | None (Hartree)
            "zero_point_energy": None,  # float | None (Hartree)
            "E_HOMO": None,  # float | None (HOMO轨道能,单位Hartree)
            "E_LUMO": None,  # float | None (LUMO轨道能,单位Hartree)
            "HOMO_LUMO_gap": None,  # float | None (HOMO-LUMO能隙=E_LUMO-E_HOMO,单位Hartree)
            "n_imag_freq": None,  # int | None (虚频数量)
            "imag_freqs": [],  # list (虚频列表,单位cm^-1)
            "frequencies": [],  # list (所有频率列表)
            "irc_verified": None,  # True | False | None (IRC验证通过)
            "irc_path": None,  # str | None (IRC路径文件)
            "notes": [],  # list (注释/警告)
            "raw_output_preview": None,  # str | None (原始输出预览)
            "metadata": {}  # dict (额外元数据)
        }


    def _normalize_error_category(self, category: Union[ErrorCategory, str, None]) -> str:
        """统一错误分类输出为字符串,避免枚举/字符串混用。"""
        if isinstance(category, ErrorCategory):
            return category.value
        if isinstance(category, str):
            return category
        return ErrorCategory.UNKNOWN_ERROR.value

    def _detect_step_job_type(self, step: ExecutionStep) -> str:
        """从步骤文本和工具名推断任务类型。"""
        text = " ".join([
            str(step.step_name or ""),
            str(step.description or ""),
            str(step.tool_name or ""),
            str(step.expected_output or ""),
        ]).lower()
        if "irc" in text:
            return "irc"
        if "transition state" in text or re.search(r"\bts\b", text):
            return "ts"
        if "single point" in text or "single-point" in text or re.search(r"\bsp\b", text):
            return "sp"
        if "frequency" in text or re.search(r"\bfreq\b", text):
            return "freq"
        if "optimization" in text or "optimize" in text or re.search(r"\bopt\b", text):
            return "opt"
        return "unknown"

    def _build_step_artifacts(self, step: ExecutionStep, conversion_steps: Optional[List[ExecutionStep]] = None) -> List[Dict[str, Any]]:
        """汇总步骤中间产物。"""
        artifacts: List[Dict[str, Any]] = []
        for output_file in step.output_files or []:
            artifacts.append({
                "type": "output_file",
                "step_id": step.step_id,
                "step_number": step.step_number,
                "tool_name": step.tool_name,
                "path": output_file,
            })

        if isinstance(step.parsed_results, dict):
            parsed = step.parsed_results
            artifacts.append({
                "type": "parsed_result",
                "step_id": step.step_id,
                "step_number": step.step_number,
                "tool_name": step.tool_name,
                "job_type": parsed.get("job_type", "unknown"),
                "summary": {
                    "normal_termination": parsed.get("normal_termination"),
                    "converged": parsed.get("converged"),
                    "scf_converged": parsed.get("scf_converged"),
                    "scf_energy": parsed.get("scf_energy"),
                    "free_energy": parsed.get("free_energy"),
                    "n_imag_freq": parsed.get("n_imag_freq"),
                    "irc_verified": parsed.get("irc_verified"),
                },
            })

        for conv in conversion_steps or []:
            if conv.status == StepStatus.SUCCESS.value:
                artifacts.append({
                    "type": "conversion",
                    "step_id": step.step_id,
                    "step_number": step.step_number,
                    "converter_tool": conv.tool_name,
                    "description": conv.description,
                    "output_preview": str(conv.raw_output)[:200] if conv.raw_output is not None else None,
                })
        return artifacts

    def _classify_workflow_outcome(self,
                                   total_steps: int,
                                   successful_steps: int,
                                   failed_steps: int,
                                   validation_overview: Dict[str, Any],
                                   has_final_output: bool) -> str:
        """给出工作流级语义化结论。"""
        if total_steps == 0:
            return "unknown"
        critical_total = validation_overview.get("critical_total", 0)
        critical_failed = validation_overview.get("critical_failed", 0)
        if failed_steps == total_steps:
            return "failed"
        if successful_steps == total_steps and critical_failed == 0 and has_final_output:
            return "supported"
        if successful_steps > 0:
            if critical_total > 0 and critical_failed == critical_total:
                return "failed"
            return "partially_supported"
        return "unknown"

    def _aggregate_expert_analysis(self, steps: List[ExecutionStep]) -> Dict[str, Any]:
        """Aggregate expert analysis across steps."""
        expert_steps = []
        for step in steps:
            if step.gaussian_analysis and step.gaussian_analysis.get("status") == "completed":
                expert_steps.append({"step_id": step.step_id, "analysis": step.gaussian_analysis})
            if step.expert_error_analysis and step.expert_error_analysis.get("status") == "completed":
                expert_steps.append({"step_id": step.step_id, "analysis": step.expert_error_analysis})
        
        if not expert_steps:
            return {"has_expert_analysis": False}
        
        # Compute overall confidence (average of expert_confidence if available)
        confidences = []
        for item in expert_steps:
            conf = item["analysis"].get("expert_confidence")
            if conf is not None:
                confidences.append(conf)
        overall_confidence = sum(confidences) / len(confidences) if confidences else None
        
        # Collect recommended next actions
        next_actions = []
        for item in expert_steps:
            action = item["analysis"].get("recommended_next_action")
            if action:
                next_actions.append(action)
        
        return {
            "has_expert_analysis": True,
            "expert_steps_count": len(expert_steps),
            "overall_confidence": overall_confidence,
            "recommended_next_actions": next_actions,
            "expert_steps": expert_steps
        }

    def _normalize_gaussian_result(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """
        将任意Gaussian解析结果归一化为标准schema
        """
        normalized = self._create_normalized_gaussian_schema()
        result = result or {}

        # 映射旧字段到新字段
        field_mapping = {
            "energy": "scf_energy",
            "frequencies": "frequencies",
            "convergence": "converged",
            "irc_path": "irc_path",
            "has_imaginary_frequencies": "n_imag_freq",
            "imaginary_frequencies_count": "n_imag_freq",
            "scf_converged": "scf_converged",
            "normal_termination": "normal_termination",
            "raw_output_preview": "raw_output_preview",
            "tool": "metadata.tool",
            "input": "metadata.input",
            "status": "metadata.status",
            "note": "notes",
            "converged": "converged",
            "job_type": "job_type",
            "scf_energy": "scf_energy",
            "free_energy": "free_energy",
            "zero_point_energy": "zero_point_energy",
            "E_HOMO": "E_HOMO",
            "E_LUMO": "E_LUMO",
            "HOMO_LUMO_gap": "HOMO_LUMO_gap",
            "n_imag_freq": "n_imag_freq",
            "imag_freqs": "imag_freqs",
            "irc_verified": "irc_verified",
            "metadata": "metadata",
        }

        for old_key, new_key in field_mapping.items():
            if old_key not in result or result[old_key] is None:
                continue
            if "." in new_key:
                parent, child = new_key.split(".", 1)
                if not isinstance(normalized.get(parent), dict):
                    normalized[parent] = {}
                normalized[parent][child] = result[old_key]
            elif old_key == "has_imaginary_frequencies":
                normalized["n_imag_freq"] = 1 if result[old_key] else 0
            elif old_key == "convergence":
                if isinstance(result[old_key], str):
                    normalized["converged"] = result[old_key].strip().lower() in {"achieved", "true", "yes"}
                else:
                    normalized["converged"] = bool(result[old_key])
            elif old_key == "note" and isinstance(result[old_key], str):
                normalized["notes"].append(result[old_key])
            elif old_key == "metadata" and isinstance(result[old_key], dict):
                normalized["metadata"].update(result[old_key])
            else:
                normalized[new_key] = result[old_key]

        # 统一列表字段
        for list_field in ["imag_freqs", "frequencies", "notes"]:
            val = normalized.get(list_field)
            if val is None:
                normalized[list_field] = []
            elif not isinstance(val, list):
                normalized[list_field] = [val]

        # 从 frequencies 回填虚频信息
        if normalized["frequencies"]:
            imag_freqs = [f for f in normalized["frequencies"] if isinstance(f, (int, float)) and f < 0]
            if not normalized["imag_freqs"]:
                normalized["imag_freqs"] = imag_freqs
            if normalized["n_imag_freq"] is None:
                normalized["n_imag_freq"] = len(imag_freqs)

        # 推断 job_type(缺失时)
        job_type = str(normalized.get("job_type") or "unknown").lower()
        if job_type == "unknown":
            tool_meta = str(normalized.get("metadata", {}).get("tool", "")).lower()
            if "irc" in tool_meta:
                job_type = "irc"
            elif "transition" in tool_meta or re.search(r"\bts\b", tool_meta):
                job_type = "ts"
            elif "single" in tool_meta or re.search(r"\bsp\b", tool_meta):
                job_type = "sp"
            elif normalized.get("frequencies"):
                job_type = "freq"
            elif normalized.get("scf_energy") is not None and normalized.get("converged") is True:
                job_type = "opt"
            elif normalized.get("scf_energy") is not None:
                job_type = "sp"
        normalized["job_type"] = job_type if job_type in {"opt", "ts", "freq", "irc", "sp"} else "unknown"

        if normalized["scf_converged"] is None and normalized["scf_energy"] is not None:
            normalized["scf_converged"] = True
        if normalized["normal_termination"] is None and normalized.get("metadata", {}).get("status") == "completed":
            normalized["normal_termination"] = True
        if normalized["converged"] is None and normalized["job_type"] in {"opt", "ts"}:
            normalized["converged"] = normalized["normal_termination"]

        return normalized

    def execute_tool_step(self, step: ExecutionStep, input_data: Any, max_retries: int = None) -> ExecutionStep:
        """
        执行单个工具步骤(单次尝试;重试由 execute_workflow 调度)
        """
        if max_retries is None:
            max_retries = self.max_step_retries

        step.start_time = time.time()
        step.status = StepStatus.RUNNING.value

        retry_count = self.current_step_retries.get(step.step_id, 0)
        if retry_count > max_retries:
            step.status = StepStatus.FAILED.value
            step.error = f"超过最大重试次数 ({max_retries})"
            step.error_info = {
                "category": ErrorCategory.UNKNOWN_ERROR.value,
                "message": step.error,
                "repair_suggestion": "检查工具配置或输入数据"
            }
            step.end_time = time.time()
            step.duration = step.end_time - step.start_time
            return step

        try:
            tool = step.tool
            manual_tool = str(step.tool_name or "").strip().lower()
            if manual_tool in {"", "none", "none (manual input)", "manual input", "manual analysis", "none (manual analysis)"} or (
                "manual" in manual_tool
            ):
                step.raw_output = {
                    "success": True,
                    "execution_mode": "manual_input",
                    "provided_output": step.expected_output,
                    "message": "manual/no-tool step accepted as provided context",
                }
                step.actual_output = step.raw_output
                step.status = StepStatus.SUCCESS.value
                return step
            if tool is None:
                if step.tool_name in self.tools:
                    tool = self.tools[step.tool_name]
                    step.tool = tool
                else:
                    tool = self.find_tool(step.description)
                    if tool:
                        step.tool = tool
                        step.tool_name = tool.tool_name
                    else:
                        raise ValueError(f"未找到工具: {step.tool_name}")

            # ARCHE-Chem expert preflight check for Gaussian-related steps
            if self.is_gaussian_related_tool(tool):
                step.preflight_check = self.preflight_check_gaussian_step(step)
                logger.info(f"[ARCHE-Chem] 预检检查完成: {step.preflight_check.get('status')}")

            # 已移除 mock/replay:不存在"模拟"步骤——真实执行或诚实抛错。
            is_simulated = False

            raw_output = self.execute_tool(tool, input_data, step=step)
            step.raw_output = raw_output
            step.actual_output = str(raw_output)[:500] + "..." if raw_output and len(str(raw_output)) > 500 else raw_output

            if tool and tool.tool_name == "generate_gaussian_code":
                route_candidate = None
                if isinstance(raw_output, dict) and raw_output.get("execution_mode") == "real_tool":
                    inner = raw_output.get("raw_result")
                    if isinstance(inner, str):
                        route_candidate = inner
                    elif isinstance(inner, dict):
                        route_candidate = inner.get("gaussian_code") or inner.get("raw_gaussian_code")
                elif isinstance(raw_output, str):
                    route_candidate = raw_output
                normalized_route = self._normalize_route_section(route_candidate)
                if normalized_route and normalized_route.startswith("#"):
                    step.route_section = normalized_route
                    self._latest_gaussian_route_section = normalized_route

            if isinstance(raw_output, dict) and raw_output.get("execution_mode") == "real_tool":
                if raw_output.get("success") is False:
                    raise RuntimeError(raw_output.get("error") or raw_output.get("message") or "真实工具执行失败")
                real_artifacts = self._extract_output_paths_from_payload(raw_output.get("output_artifacts") or raw_output)
                if real_artifacts:
                    step.output_files = list(dict.fromkeys(real_artifacts))
                    for p in step.output_files:
                        step.artifacts.append({"type": "real_tool_output", "path": p, "tool": step.tool_name})

            if isinstance(raw_output, dict) and raw_output.get("execution_mode") == "gaussian_job":
                gauss_artifacts = self._extract_output_paths_from_payload(raw_output.get("output_artifacts") or raw_output.get("job_state") or {})
                if gauss_artifacts:
                    step.output_files = list(dict.fromkeys((step.output_files or []) + gauss_artifacts))

            # 跨步 .log 传递：把本步产出的 Gaussian 日志按顺序记入，供后续解析步在拿不到上一步 .log 时回退。
            _log_cands = list(step.output_files or [])
            # 直接从 gaussian_job 的 job_state 兜底取 log_path：output_files 走的是 output_artifacts(=expected_outputs)
            # 或 job_state 的路径抽取，若 expected_outputs 非空却不含 .log 字符串会漏掉，这里显式补一手。
            if isinstance(raw_output, dict):
                _js = raw_output.get("job_state") if isinstance(raw_output.get("job_state"), dict) else {}
                for _lp in (raw_output.get("log_path"), _js.get("log_path")):
                    if isinstance(_lp, str) and _lp:
                        _log_cands.append(_lp)
            for _art in _log_cands:
                if isinstance(_art, str) and _art.lower().endswith((".log", ".out")) and _art not in self._recent_gaussian_logs:
                    self._recent_gaussian_logs.append(_art)
                if isinstance(_art, str) and _art.lower().endswith(".json") and _art not in self._recent_gaussian_jsons:
                    self._recent_gaussian_jsons.append(_art)

            if raw_output is not None:
                if (
                    self.is_gaussian_related_tool(tool)
                    and isinstance(raw_output, dict)
                    and raw_output.get("execution_mode") == "gaussian_job"
                ):
                    parsed_payload = raw_output.get("parsed_results")
                    if raw_output.get("completed") and isinstance(parsed_payload, dict):
                        step.parsed_results = self._normalize_gaussian_result(parsed_payload)
                    else:
                        step.parsed_results = None
                elif is_simulated and "gaussian" in tool.tool_path.lower():
                    step.parsed_results = self.parse_gaussian_output(raw_output)
                else:
                    step.parsed_results = None

                if self.enable_validation and step.parsed_results:
                    validator, validation_type = self.select_validator(step, step.parsed_results)
                    step.validation = validator(step.parsed_results)
                    step.validation["validation_type"] = validation_type
                elif self.enable_validation:
                    step.validation = {"status": ValidationStatus.UNKNOWN.value, "details": "无解析结果可用于验证"}

                # ARCHE-Chem expert analysis for Gaussian results
                if self.is_gaussian_related_tool(tool) and step.parsed_results:
                    scientific_context = step.scientific_context or (step.preflight_check.get('scientific_context') if step.preflight_check else "未知科学上下文")
                    step.gaussian_analysis = self.analyze_gaussian_result_with_arche_chem(
                        step, raw_output, step.parsed_results, scientific_context
                    )
                    logger.info(f"[ARCHE-Chem] 结果分析完成: {step.gaussian_analysis.get('status')}")

            # 可信度修正:原先在 try 末尾无条件标 SUCCESS —— gaussian_job 即使作业失败也被标成功,
            # 与 parsed_results=None / raw_output.job_state 自相矛盾,下游与用户把失败的 Gaussian 当成功。
            # 仅在有「明确失败信号」(非正常终止 / job_state=failed|error)时翻成 FAILED,不误伤 pending/正常作业。
            gaussian_failed = False
            if isinstance(raw_output, dict) and raw_output.get("execution_mode") == "gaussian_job":
                parsed = raw_output.get("parsed_results")
                normal_term = parsed.get("normal_termination") if isinstance(parsed, dict) else None
                js = raw_output.get("job_state")
                js_status = str(js.get("status", "")).lower() if isinstance(js, dict) else ""
                if normal_term is False or js_status in ("failed", "error"):
                    gaussian_failed = True
            if gaussian_failed:
                step.status = StepStatus.FAILED.value
                if not getattr(step, "error", None):
                    step.error = "Gaussian 作业未正常完成（normal_termination/job_state 指示失败）"
            else:
                step.status = StepStatus.SUCCESS.value

        except Exception as e:
            error_category, repair_suggestion = self.classify_execution_error(e)
            step.status = StepStatus.FAILED.value
            step.error = str(e)
            step.error_info = {
                "category": self._normalize_error_category(error_category),
                "message": step.error,
                "repair_suggestion": repair_suggestion
            }
            # ARCHE-Chem expert error diagnosis for Gaussian-related steps
            if tool is not None and self.is_gaussian_related_tool(tool):
                scientific_context = step.scientific_context or (step.preflight_check.get('scientific_context') if step.preflight_check else "未知科学上下文")
                step.expert_error_analysis = self.analyze_gaussian_error_with_arche_chem(step, str(e), scientific_context)
                logger.info(f"[ARCHE-Chem] 错误诊断完成: {step.expert_error_analysis.get('status')}")
                # If expert analysis provides error category and repair suggestions, augment error_info
                if step.expert_error_analysis and step.expert_error_analysis.get('error_category'):
                    step.error_info['expert_category'] = step.expert_error_analysis['error_category']
                    step.error_info['expert_repair_suggestion'] = step.expert_error_analysis.get('repair_suggestions', '')
            if retry_count < max_retries:
                logger.warning(f"步骤 {step.step_id} 失败,准备重试 ({retry_count + 1}/{max_retries})")
                self.current_step_retries[step.step_id] = retry_count + 1
                step.status = StepStatus.RETRYING.value

        finally:
            step.end_time = time.time()
            step.duration = step.end_time - step.start_time

        return step

    def execute_python_tool(self, tool_path: str, input_data: Any) -> Any:
        """
        执行Python工具(实际调用Python模块/函数)

        注意:当前为模拟实现
        """
        logger.info(f"[模拟] 执行Python工具: {tool_path}")
        return f"[模拟] Python工具 {tool_path} 执行成功,输入: {str(input_data)[:100]}..."

    def execute_shell_tool(self, command: str, args: List[str] = None, timeout: float = None) -> Any:
        """
        执行Shell命令工具

        注意:当前为模拟实现
        """
        logger.info(f"[模拟] 执行Shell命令: {command} {args if args else ''}")
        return f"[模拟] Shell命令执行成功: {command}"

    def execute_mock_tool(self, tool_name: str, input_data: Any) -> Any:
        """已彻底移除 mock:任何走到这里的工具都无法真实执行 → 诚实抛错,绝不返回伪造结果。"""
        raise RuntimeError(
            f"工具 '{tool_name}' 无法真实执行,已移除所有 mock 回退(拒绝伪造结果)。输入: {str(input_data)[:120]}"
        )

    def _is_top_level_tool_path(self, tool_path: str) -> bool:
        if not tool_path:
            return False
        norm = str(tool_path).replace("\\", "/")
        if not norm.endswith(".py"):
            return False
        if "/" not in norm:
            return True
        parts = [p for p in norm.split("/") if p]
        return len(parts) >= 2 and parts[-2] == "tools"

    def _resolve_tool_module_name(self, script_path: str) -> str:
        base = os.path.splitext(os.path.basename(script_path))[0]
        safe = re.sub(r"[^A-Za-z0-9_]", "_", base)
        return f"chemagent_tools_{safe}_{abs(hash(os.path.abspath(script_path))) % 100000}"

    def _resolve_top_level_tool_script_path(self, tool: ToolDefinition) -> Optional[str]:
        if tool is None:
            return None

        candidates: List[str] = []
        tool_path = str(tool.tool_path or "")
        tool_name = str(tool.tool_name or "")

        if self._is_top_level_tool_path(tool_path):
            candidates.append(os.path.basename(tool_path))

        if tool_name in self.real_tool_name_to_file:
            candidates.append(self.real_tool_name_to_file[tool_name])

        last_token = tool_path.split(".")[-1] if tool_path else ""
        if last_token and last_token in self.real_tool_name_to_file:
            candidates.append(self.real_tool_name_to_file[last_token])
        if last_token.endswith(".py"):
            candidates.append(os.path.basename(last_token))

        if tool_name and tool_name.endswith(".py"):
            candidates.append(os.path.basename(tool_name))

        dedup = []
        seen = set()
        for name in candidates:
            if not name:
                continue
            if name not in seen:
                seen.add(name)
                dedup.append(name)

        for name in dedup:
            script_path = os.path.abspath(os.path.join(self.tools_root_dir, name))
            if os.path.exists(script_path) and os.path.isfile(script_path):
                try:
                    within_tools = os.path.commonpath([script_path, self.tools_root_dir]) == self.tools_root_dir
                except ValueError:
                    within_tools = False
                parent = os.path.abspath(os.path.dirname(script_path))
                if name in self.real_top_level_tool_files and parent == os.path.abspath(self.tools_root_dir):
                    return script_path
                if within_tools and str(name).replace("\\", "/") == "23_TSPipeline/run.py":
                    return script_path
        return None

    def _is_real_tool_eligible(self, tool: ToolDefinition, step: Optional[ExecutionStep] = None) -> Tuple[bool, Optional[str], str]:
        script_path = self._resolve_top_level_tool_script_path(tool)
        if not script_path:
            return False, None, "tool_not_in_top_level_whitelist"
        basename = os.path.basename(script_path)
        try:
            within_tools = os.path.commonpath([script_path, self.tools_root_dir]) == self.tools_root_dir
        except ValueError:
            within_tools = False
        if not within_tools:
            return False, None, "not_top_level_tools_dir"
        if basename not in self.real_top_level_tool_files and not getattr(tool, "is_pipeline", False):
            return False, None, "not_whitelisted"
        return True, script_path, "eligible"

    def _collect_tool_payload(self, input_data: Any, step: Optional[ExecutionStep] = None) -> Dict[str, Any]:
        payload: Dict[str, Any] = {}
        if isinstance(input_data, dict):
            payload.update(input_data)
            for nested_key in ("raw_result", "result"):
                nested = input_data.get(nested_key)
                if isinstance(nested, dict):
                    for k, v in nested.items():
                        payload.setdefault(k, v)
        elif isinstance(input_data, str):
            payload["input_text"] = input_data
        elif input_data is not None:
            payload["input_data"] = input_data

        if step is not None:
            payload.setdefault("step_description", step.description)
            payload.setdefault("expected_input", step.expected_input)
            payload.setdefault("expected_output", step.expected_output)
            payload.setdefault("route_section", step.route_section)
            payload.setdefault("scientific_context", step.scientific_context)
            if step.artifacts:
                payload.setdefault("artifacts", step.artifacts)
        return payload

    def _extract_paths_by_suffixes(self, value: Any, suffixes: List[str], step: Optional[ExecutionStep] = None) -> List[str]:
        results: List[str] = []
        for suffix in suffixes:
            suf = suffix if suffix.startswith(".") else f".{suffix}"
            results.extend(self._extract_paths_with_suffix(value, suf))
        unique = []
        seen = set()
        for p in results:
            ap = self._resolve_path_hint(p, step=step) or os.path.abspath(os.path.expanduser(str(p).strip().strip("\"'")))
            if ap not in seen:
                seen.add(ap)
                unique.append(ap)
        return unique

    def _filter_recent_artifacts_by_expected_names(self,
                                                   recent_paths: List[str],
                                                   expected_input: Any,
                                                   suffixes: List[str]) -> List[str]:
        expected_names = set()
        for suffix in suffixes:
            suf = suffix if suffix.startswith(".") else f".{suffix}"
            for token in self._extract_paths_with_suffix(expected_input, suf):
                name = os.path.basename(str(token).strip())
                if name:
                    expected_names.add(name)
        if not expected_names:
            return list(recent_paths)
        return [path for path in recent_paths if os.path.basename(path) in expected_names]

    def _choose_path(self, candidates: List[str], must_exist: bool = False) -> Optional[str]:
        for p in candidates:
            if not p:
                continue
            ap = os.path.abspath(os.path.expanduser(str(p)))
            if not must_exist or os.path.exists(ap):
                return ap
        return None

    def _resolve_path_hint(self, raw_path: Any, step: Optional[ExecutionStep] = None) -> Optional[str]:
        if raw_path is None:
            return None
        text = str(raw_path).strip().strip("\"'")
        if not text:
            return None
        expanded = os.path.expanduser(text)
        if os.path.isabs(expanded):
            return os.path.abspath(expanded)
        base_dir = (
            (step.working_directory if step is not None and step.working_directory else None)
            or getattr(self, "work_dir", None)
            or self.gaussian_job_root
        )
        return os.path.abspath(os.path.join(os.path.abspath(os.path.expanduser(base_dir)), text))

    def _scan_workdir_for_latest_artifact(self,
                                          suffixes: List[str],
                                          step: Optional[ExecutionStep],
                                          payload: Optional[Dict[str, Any]] = None) -> Optional[str]:
        """兜底:跨步骤文件传递缺失时,在本次运行的工作目录里按后缀扫描最近产物。

        上游步骤(如 smiles2sdf)产出的 .sdf 会落在 step.working_directory /
        gaussian_job_root,但若规划器为下游步骤填了描述性 Input,previous_output 会被
        丢弃、产物路径无法透传。此处按修改时间取最新的匹配文件,确保
        smiles2sdf -> sdf_to_gjf -> gaussian 链路不因步骤间文件传递断裂而中断。
        """
        norm_suffixes = tuple(
            (s if s.startswith(".") else f".{s}").lower() for s in suffixes
        )
        if not norm_suffixes:
            return None

        search_dirs: List[str] = []

        def add_dir(path: Optional[str]) -> None:
            if not path:
                return
            ap = os.path.abspath(os.path.expanduser(str(path)))
            if os.path.isdir(ap) and ap not in search_dirs:
                search_dirs.append(ap)

        if step is not None and step.working_directory:
            add_dir(step.working_directory)
        add_dir(getattr(self, "work_dir", None))
        # expected_input/expected_output 里若带了路径,取其所在目录
        if payload is not None:
            for hint_key in ("expected_input", "expected_output"):
                for p in self._extract_paths_by_suffixes(payload.get(hint_key), list(suffixes), step=step):
                    add_dir(os.path.dirname(p))
        add_dir(self.gaussian_job_root)

        best_path: Optional[str] = None
        best_mtime = -1.0
        for d in search_dirs:
            try:
                entries = os.listdir(d)
            except OSError:
                continue
            for name in entries:
                if not name.lower().endswith(norm_suffixes):
                    continue
                fp = os.path.join(d, name)
                if not os.path.isfile(fp):
                    continue
                try:
                    mtime = os.path.getmtime(fp)
                except OSError:
                    continue
                if mtime > best_mtime:
                    best_mtime = mtime
                    best_path = os.path.abspath(fp)
        return best_path

    def _default_output_path(self,
                             suffix: str,
                             step: Optional[ExecutionStep],
                             input_path: Optional[str] = None,
                             stem_hint: Optional[str] = None) -> str:
        if step is not None and step.working_directory:
            base_dir = os.path.abspath(os.path.expanduser(step.working_directory))
        elif input_path:
            base_dir = os.path.dirname(os.path.abspath(input_path))
        else:
            base_dir = os.path.abspath(self.gaussian_job_root)
        os.makedirs(base_dir, exist_ok=True)
        suffix = suffix if suffix.startswith(".") else f".{suffix}"
        if input_path:
            stem = os.path.splitext(os.path.basename(input_path))[0]
        elif stem_hint:
            stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", stem_hint)
        elif step is not None and step.step_id:
            stem = f"step_{step.step_id}"
        else:
            stem = "tool_output"
        return os.path.join(base_dir, f"{stem}{suffix}")

    # 常见分子名 → SMILES 兜底表(规划器只给分子名/标签、不给真实 SMILES 时用）。
    _COMMON_NAME_TO_SMILES = {
        "benzene": "c1ccccc1", "苯": "c1ccccc1", "c6h6": "c1ccccc1",
        "water": "O", "水": "O", "h2o": "O",
        "nitrogen": "N#N", "dinitrogen": "N#N", "n2": "N#N",
        "hydrogen": "[H][H]", "dihydrogen": "[H][H]", "h2": "[H][H]",
        "methane": "C", "甲烷": "C", "ch4": "C",
        "ammonia": "N", "氨": "N", "nh3": "N",
        "ethane": "CC", "乙烷": "CC",
        "ethanol": "CCO", "乙醇": "CCO",
        "methanol": "CO", "甲醇": "CO",
        "acetic acid": "CC(=O)O", "乙酸": "CC(=O)O", "醋酸": "CC(=O)O",
        "formaldehyde": "C=O", "甲醛": "C=O",
        "toluene": "Cc1ccccc1", "甲苯": "Cc1ccccc1",
        "phenol": "Oc1ccccc1", "苯酚": "Oc1ccccc1",
        "aniline": "Nc1ccccc1", "苯胺": "Nc1ccccc1",
        "pyridine": "c1ccncc1", "吡啶": "c1ccncc1",
        "ethylene": "C=C", "乙烯": "C=C", "acetylene": "C#C", "乙炔": "C#C",
        "carbon dioxide": "O=C=O", "二氧化碳": "O=C=O", "co2": "O=C=O",
    }
    _BUILTIN_GEOMETRY_TEMPLATES = {
        "O": {
            "charge_mult": "0 1",
            "coords": "\n".join([
                "O      0.00000000    0.00000000    0.00000000",
                "H      0.75860200    0.00000000    0.50428400",
                "H     -0.75860200    0.00000000    0.50428400",
            ]),
            "atom_count": 3,
            "elements": ["H", "O"],
            "formal_charge": 0,
        },
        "c1ccccc1": {
            "charge_mult": "0 1",
            "coords": "\n".join([
                "C      0.00000000    1.39679200    0.00000000",
                "C      1.20965700    0.69839600    0.00000000",
                "C      1.20965700   -0.69839600    0.00000000",
                "C      0.00000000   -1.39679200    0.00000000",
                "C     -1.20965700   -0.69839600    0.00000000",
                "C     -1.20965700    0.69839600    0.00000000",
                "H      0.00000000    2.49029000    0.00000000",
                "H      2.15666000    1.24514500    0.00000000",
                "H      2.15666000   -1.24514500    0.00000000",
                "H      0.00000000   -2.49029000    0.00000000",
                "H     -2.15666000   -1.24514500    0.00000000",
                "H     -2.15666000    1.24514500    0.00000000",
            ]),
            "atom_count": 12,
            "elements": ["C", "H"],
            "formal_charge": 0,
        },
        "N#N": {
            "charge_mult": "0 1",
            "coords": "\n".join([
                "N      0.00000000    0.00000000   -0.55000000",
                "N      0.00000000    0.00000000    0.55000000",
            ]),
            "atom_count": 2,
            "elements": ["N"],
            "formal_charge": 0,
        },
        "[H][H]": {
            "charge_mult": "0 1",
            "coords": "\n".join([
                "H      0.00000000    0.00000000   -0.37000000",
                "H      0.00000000    0.00000000    0.37000000",
            ]),
            "atom_count": 2,
            "elements": ["H"],
            "formal_charge": 0,
        },
        "N": {
            "charge_mult": "0 1",
            "coords": "\n".join([
                "N      0.00000000    0.00000000    0.10000000",
                "H      0.94000000    0.00000000   -0.25000000",
                "H     -0.47000000    0.81400000   -0.25000000",
                "H     -0.47000000   -0.81400000   -0.25000000",
            ]),
            "atom_count": 4,
            "elements": ["H", "N"],
            "formal_charge": 0,
        },
    }

    def _normalize_smiles_list(self, value: Any) -> List[str]:
        if value is None:
            items: List[str] = []
        elif isinstance(value, list):
            items = [str(x).strip() for x in value if str(x).strip()]
        else:
            text = str(value).strip()
            if not text:
                items = []
            elif text.startswith("[") and text.endswith("]"):
                try:
                    parsed = json.loads(text)
                    items = [str(x).strip() for x in parsed if str(x).strip()] if isinstance(parsed, list) else []
                except Exception:
                    items = [p.strip() for p in re.split(r"[\n,;]+", text) if p.strip()]
            else:
                items = [p.strip() for p in re.split(r"[\n,;]+", text) if p.strip()]
        # 清洗:推理模型常把标签(SMILES:/分子:/molecule=)和引号/反引号一起写进输入;剥标签去引号,
        # 再用 RDKit 校验,丢掉解析不通过的项(如纯标签 "SMILES:"),避免下游拿垃圾去解析直接报错。
        cleaned: List[str] = []
        for raw in items:
            s = re.sub(r"(?i)^\s*(smiles|分子|molecule)\s*[:=]\s*", "", str(raw)).strip().strip("`")
            candidates: List[str] = []
            if s:
                candidates.append(s.strip().strip("\"'"))
                quoted = re.match(r"""^\s*["'`](.+?)["'`](?:\s*\(.*\))?\s*$""", s)
                if quoted:
                    candidates.append(quoted.group(1).strip())
                first_token = s.split()[0].strip().strip("\"'") if s.split() else ""
                if first_token:
                    candidates.append(first_token)

            chosen = None
            try:
                from rdkit import Chem  # type: ignore
                for candidate in candidates:
                    if candidate and Chem.MolFromSmiles(candidate) is not None:
                        chosen = candidate
                        break
            except Exception:
                pass  # RDKit 不可用时不强校验,保留原值
            if not chosen:
                chosen = next((candidate for candidate in candidates if candidate), None)
            if not chosen:
                continue
            cleaned.append(chosen)
        return cleaned

    def _resolve_species_from_input_hints(self, *values: Any) -> List[str]:
        matches: List[str] = []
        seen = set()
        for raw in values:
            if raw is None:
                continue
            text = str(raw).lower()
            for name, smi in self._COMMON_NAME_TO_SMILES.items():
                if name not in text:
                    continue
                if name.isalnum():
                    if not re.search(rf"(?<![a-z0-9]){re.escape(name)}(?![a-z0-9])", text):
                        continue
                if smi not in seen:
                    seen.add(smi)
                    matches.append(smi)
        return matches

    def _resolve_smiles_from_context(self, step: Optional[ExecutionStep], payload: Optional[Dict[str, Any]], input_data: Any) -> List[str]:
        """规划器没给有效 SMILES 时的兜底:从步骤描述/上下文里识别常见分子名 → SMILES,
        避免在"分子准备"这一步就因输入是标签/分子名而断链(典型:输入是 'SMILES:' 占位符)。"""
        focused_parts = []
        if step is not None:
            focused_parts.append(str(getattr(step, "description", "") or ""))
            focused_parts.append(str(getattr(step, "tool_name", "") or ""))
            focused_parts.append(str(getattr(step, "expected_input", "") or ""))
        if isinstance(payload, dict):
            for k in ("expected_input", "expected_output", "description", "molecule", "name"):
                v = payload.get(k)
                if v:
                    focused_parts.append(str(v))
        if isinstance(input_data, str):
            focused_parts.append(input_data)

        hint_smiles = self._resolve_species_from_input_hints(
            getattr(step, "expected_input", None) if step is not None else None,
            payload.get("expected_input") if isinstance(payload, dict) else None,
            payload.get("input_file_path") if isinstance(payload, dict) else None,
            input_data if isinstance(input_data, str) else None,
        )
        if len(hint_smiles) == 1:
            return hint_smiles

        context_parts = []
        # 整体问题 / 策略名常含分子名(如 "...for Benzene"),而单个执行步骤描述未必带 → 一并纳入识别,
        # 这样即便没有现成 .sdf 几何、且步骤描述无分子名,也能从问题/策略名定位分子。
        run_question = str(getattr(self, "_run_question", "") or "")
        strategy_name = str(getattr(self, "_current_strategy_name", "") or "")
        context_parts.append(run_question)
        context_parts.append(strategy_name)
        blob_parts = focused_parts + context_parts
        blob = " ".join(blob_parts).lower()
        guard_parts = focused_parts + ([run_question] if run_question else ([strategy_name] if strategy_name else []))
        guard_blob = " ".join(guard_parts).lower()

        # 多组分机理/TS/IRC 任务里，常见小分子名经常只是副产物/溶剂/反应物片段。
        # 例如 "enamine + H2O" 不能被解释为“本步目标分子是水”。没有明确单一
        # SMILES/几何时必须失败，避免把无关 H2O 结果包装成目标机理证据。
        if self._looks_like_multispecies_mechanism_context(guard_blob):
            focused_blob = " ".join(focused_parts)
            focused_smiles = self._extract_embedded_smiles(focused_blob)
            if len(focused_smiles) == 1:
                return focused_smiles
            if focused_smiles:
                logger.warning("[smiles兜底] 复杂机理步骤包含多个 SMILES,拒绝任选一个作为 Gaussian 目标")
            else:
                logger.warning("[smiles兜底] 复杂机理步骤缺少明确单一目标 SMILES,拒绝用常见分子名兜底")
            return []

        for name, smi in self._COMMON_NAME_TO_SMILES.items():
            if name in blob:
                logger.info(f"[smiles兜底] 规划器未给有效 SMILES,从上下文识别分子 '{name}' → {smi}")
                return [smi]
        return []

    def _looks_like_multispecies_mechanism_context(self, blob: str) -> bool:
        text = (blob or "").lower()
        if not text:
            return False
        mechanism_hit = re.search(
            r"\b(mechanism|reaction|reactant|product|catalyst|transition state|ts|irc|"
            r"barrier|free energy|enantio|stereo|aldol|enamine|asymmetric)\b|"
            r"机理|反应|反应物|产物|催化|过渡态|自由能|活化能",
            text,
            re.I,
        )
        if not mechanism_hit:
            return False
        multi_markers = len(re.findall(r"\b(with|and|plus|between|under|->|→|\+)\b|->|→|\+", text, re.I))
        explicit_smiles = self._extract_embedded_smiles(blob)
        return multi_markers > 0 or len(explicit_smiles) > 1

    def _builtin_coords_from_smiles(self, smiles: str) -> Optional[Dict[str, Any]]:
        smi = str(smiles or "").strip()
        if not smi:
            return None
        template = self._BUILTIN_GEOMETRY_TEMPLATES.get(smi)
        if template is not None:
            return dict(template)
        mapped = self._COMMON_NAME_TO_SMILES.get(smi.lower())
        if mapped and mapped in self._BUILTIN_GEOMETRY_TEMPLATES:
            return dict(self._BUILTIN_GEOMETRY_TEMPLATES[mapped])
        return None

    @staticmethod
    def _looks_like_gaussian_method_token(token: str) -> bool:
        text = str(token or "")
        if "/" not in text:
            return False
        return bool(re.search(
            r"\b(B3LYP|CAM-B3LYP|wB97X-?D|M06-?2X|PBE0|PBE1PBE|TPSSh|B3PW91|HF|MP2|CCSD|def2|6-31|cc-pV|aug-cc)\b",
            text,
            re.I,
        ))

    def _extract_embedded_smiles(self, text: str) -> List[str]:
        """Conservatively extract explicit SMILES-like tokens embedded in natural language."""
        if not text:
            return []
        candidates = re.findall(r"(?<![A-Za-z0-9_])(?:[A-Z][A-Za-z0-9@\+\-\[\]\(\)=#$\\/\.]{2,}|[cnops][A-Za-z0-9@\+\-\[\]\(\)=#$\\/\.]{2,})(?![A-Za-z0-9_])", text)
        out: List[str] = []
        seen = set()
        for token in candidates:
            s = token.strip(".,;:，。；：'\"`")
            if self._looks_like_gaussian_method_token(s):
                continue
            if not any(ch in s for ch in ("=", "(", ")", "[", "]", "1", "2", "#", "@")):
                continue
            try:
                from rdkit import Chem  # type: ignore
                if Chem.MolFromSmiles(s) is None:
                    continue
            except Exception:
                continue
            if s not in seen:
                seen.add(s)
                out.append(s)
        return out

    def _resolve_real_tool_callable(self, module: Any, tool: ToolDefinition, script_basename: str) -> Tuple[Optional[Any], Optional[str]]:
        per_script = {
            "smiles2sdf.py": ["smiles_to_sdf"],
            "sdf2gjf.py": ["sdf_to_gjf"],
            "sdf_to_xyz.py": ["sdf_to_xyz"],
            "xyz2gjf.py": ["xyz_to_gjf"],
            "stereoisomer.py": ["enumerate_stereoisomers"],
            "Get_gjf_from_log.py": ["get_gjf_from_log"],
            "gen_conformation.py": ["generate_conformations", "generate_visualize_and_save_conformers"],
            "gen_gaussiancode.py": ["generate_gaussian_code_result", "generate_gaussian_code"],
            "output_parser.py": ["parse_gaussian_output"],
            "process_spectrum.py": ["run_spectrum_pipeline"],
            "plot_spectrum.py": ["generate_spectrum_plot", "plot_spectrum"],
            "Ni_plot.py": ["plot_ni_spectrum"],
            "plot_tools.py": ["draw_spectrum_from_file", "draw_spectrum"],
            "reaction_thermochemistry.py": ["compute_reaction_thermochemistry", "run_tool"],
        }
        candidate_names: List[str] = ["run_tool", tool.tool_name]
        candidate_names.extend(per_script.get(script_basename, []))
        candidate_names.extend([
            "smiles_to_sdf", "sdf_to_gjf", "xyz_to_gjf", "enumerate_stereoisomers",
            "get_gjf_from_log", "generate_conformations", "generate_gaussian_code_result",
            "parse_gaussian_output", "run_spectrum_pipeline", "generate_spectrum_plot",
            "plot_ni_spectrum", "draw_spectrum_from_file", "draw_spectrum",
            "compute_reaction_thermochemistry",
        ])

        seen = set()
        for name in candidate_names:
            if not name or name in seen:
                continue
            seen.add(name)
            func = getattr(module, name, None)
            if callable(func):
                return func, name

        if script_basename == "Get_gjf_from_log.py":
            gen_cls = getattr(module, "GjfGenerator", None)
            generate = getattr(gen_cls, "generate_gjf", None) if gen_cls else None
            if callable(generate):
                return generate, "GjfGenerator.generate_gjf"

        return None, None

    def _build_real_tool_call_context(self,
                                      tool: ToolDefinition,
                                      input_data: Any,
                                      step: Optional[ExecutionStep],
                                      script_path: str,
                                      callable_name: str) -> Tuple[Dict[str, Any], List[str], Optional[str]]:
        payload = self._collect_tool_payload(input_data, step=step)
        basename = os.path.basename(script_path)
        artifacts: List[str] = []

        def get_first(*keys):
            for k in keys:
                if k in payload and payload.get(k) is not None:
                    return payload.get(k)
            return None

        expected_input_paths = self._extract_paths_by_suffixes(payload.get("expected_input"), ["sdf", "xyz", "gjf", "log", "out", "json", "png"], step=step)
        expected_output_paths = self._extract_paths_by_suffixes(payload.get("expected_output"), ["sdf", "xyz", "gjf", "log", "out", "json", "png"], step=step)

        kwargs: Dict[str, Any] = {}

        if basename == "smiles2sdf.py":
            smiles_value = get_first("smiles_list", "smiles", "input_smiles")
            if smiles_value is None and isinstance(input_data, str):
                smiles_value = input_data
            smiles_list = self._normalize_smiles_list(smiles_value)
            if not smiles_list:
                # 兜底:规划器常把分子名/标签(如 "SMILES:")当输入、没给真实 SMILES;从上下文识别分子名解析。
                smiles_list = self._resolve_smiles_from_context(step, payload, input_data)
            if not smiles_list:
                return {}, [], "缺少有效的 smiles 输入(规划器未给出分子 SMILES,且上下文无法识别分子)"
            output_sdf = get_first("output_sdf_path", "output_path", "sdf_path")
            if isinstance(output_sdf, str):
                output_sdf = self._resolve_path_hint(output_sdf, step=step)
            if not output_sdf:
                raw_expected_sdfs = self._extract_paths_with_suffix(payload.get("expected_output"), ".sdf")
                if raw_expected_sdfs:
                    output_sdf = self._resolve_path_hint(raw_expected_sdfs[0], step=step)
            if not output_sdf:
                output_sdf = self._default_output_path(".sdf", step, stem_hint=tool.tool_name)
            kwargs = {
                "smiles_list": smiles_list,
                "smiles": smiles_list[0] if smiles_list else None,
                "output_sdf_path": output_sdf,
                "output_path": output_sdf,
            }
            artifacts = [output_sdf]

        elif basename == "sdf2gjf.py":
            sdf_candidates = []
            sdf_candidates.extend(
                [p for p in (self._resolve_path_hint(v, step=step) for v in self._extract_paths_with_suffix(payload.get("expected_input"), ".sdf")) if p]
            )
            sdf_candidates.extend(self._extract_paths_by_suffixes(payload, ["sdf"], step=step))
            sdf_candidates.extend(expected_input_paths)
            sdf_path = self._choose_path(sdf_candidates, must_exist=True)
            if not sdf_path:
                # 兜底:上游 smiles2sdf 的产物未透传时,扫描工作目录取最近的 .sdf
                sdf_path = self._scan_workdir_for_latest_artifact(["sdf"], step, payload)
            if not sdf_path:
                return {}, [], "缺少有效的sdf输入文件"
            gjf_path = get_first("gjf_path", "output_gjf_path", "output_path")
            if isinstance(gjf_path, str):
                gjf_path = self._resolve_path_hint(gjf_path, step=step)
            if not gjf_path:
                gjf_path = self._choose_path(self._extract_paths_by_suffixes(payload, ["gjf"], step=step), must_exist=False)
            if not gjf_path:
                gjf_path = self._choose_path(expected_output_paths, must_exist=False)
            if not gjf_path:
                gjf_path = self._default_output_path(".gjf", step, input_path=sdf_path)
            route = (
                get_first("route_parameters", "route_section", "gaussian_route")
                or (step.route_section if step else None)
                or getattr(self, "_latest_gaussian_route_section", None)
            )
            title = get_first("title", "job_name") or (step.step_name if step else "Generated Gaussian Input")
            kwargs = {
                "sdf_path": sdf_path,
                "input_sdf_path": sdf_path,
                "gjf_path": gjf_path,
                "output_gjf_path": gjf_path,
                "output_path": gjf_path,
                "route_parameters": route,
                "route_section": route,
                "title": title,
                "charge": get_first("charge"),
                "multiplicity": get_first("multiplicity"),
            }
            artifacts = [gjf_path]

        elif basename == "sdf_to_xyz.py":
            sdf_candidates = []
            sdf_candidates.extend(
                [p for p in (self._resolve_path_hint(v, step=step) for v in self._extract_paths_with_suffix(payload.get("expected_input"), ".sdf")) if p]
            )
            sdf_candidates.extend(self._extract_paths_by_suffixes(payload, ["sdf"], step=step))
            sdf_candidates.extend(expected_input_paths)
            sdf_path = self._choose_path(sdf_candidates, must_exist=True)
            if not sdf_path:
                sdf_path = self._scan_workdir_for_latest_artifact(["sdf"], step, payload)
            if not sdf_path:
                return {}, [], "缺少有效的sdf输入文件"
            xyz_path = get_first("xyz_path", "output_xyz_path", "output_path")
            if isinstance(xyz_path, str):
                xyz_path = self._resolve_path_hint(xyz_path, step=step)
            if not xyz_path:
                xyz_path = self._default_output_path(".xyz", step, input_path=sdf_path)
            else:
                raw_xyz = get_first("xyz_path", "output_xyz_path", "output_path")
                if not raw_xyz:
                    xyz_path = self._default_output_path(".xyz", step, input_path=sdf_path)
            kwargs = {
                "input_sdf_path": sdf_path,
                "sdf_path": sdf_path,
                "output_xyz_path": xyz_path,
                "output_path": xyz_path,
                "title": get_first("title") or (step.step_name if step else "Converted from SDF file"),
            }
            artifacts = [xyz_path]

        elif basename == "xyz2gjf.py":
            xyz_candidates = []
            xyz_candidates.extend(
                [p for p in (self._resolve_path_hint(v, step=step) for v in self._extract_paths_with_suffix(payload.get("expected_input"), ".xyz")) if p]
            )
            xyz_candidates.extend(self._extract_paths_by_suffixes(payload, ["xyz"], step=step))
            xyz_candidates.extend(expected_input_paths)
            xyz_path = self._choose_path(xyz_candidates, must_exist=True)
            if not xyz_path:
                # 兜底:上游产物未透传时,扫描工作目录取最近的 .xyz
                xyz_path = self._scan_workdir_for_latest_artifact(["xyz"], step, payload)
            if not xyz_path:
                return {}, [], "缺少有效的xyz输入文件"
            gjf_path = get_first("gjf_path", "output_gjf_path", "output_path")
            if isinstance(gjf_path, str):
                gjf_path = self._resolve_path_hint(gjf_path, step=step)
            if not gjf_path:
                gjf_path = self._choose_path(self._extract_paths_by_suffixes(payload, ["gjf"], step=step), must_exist=False)
            if not gjf_path:
                gjf_path = self._choose_path(expected_output_paths, must_exist=False)
            if not gjf_path:
                gjf_path = self._default_output_path(".gjf", step, input_path=xyz_path)
            route = (
                get_first("route_section", "route_parameters", "gaussian_route")
                or (step.route_section if step else None)
                or getattr(self, "_latest_gaussian_route_section", None)
            )
            kwargs = {
                "xyz_path": xyz_path,
                "input_xyz_path": xyz_path,
                "input_file_path": xyz_path,
                "gjf_path": gjf_path,
                "output_gjf_path": gjf_path,
                "output_path": gjf_path,
                "route_section": route,
                "route_parameters": route,
                "charge": get_first("charge"),
                "multiplicity": get_first("multiplicity"),
            }
            artifacts = [gjf_path]

        elif basename == "gen_conformation.py":
            smiles = get_first("smiles")
            if smiles is None and isinstance(input_data, str):
                smiles = input_data
            if isinstance(smiles, list):
                smiles = smiles[0] if smiles else None
            if not smiles:
                return {}, [], "缺少smiles输入"
            out_dir = get_first("individual_sdf_dir", "output_dir")
            if not out_dir:
                if step is not None and step.working_directory:
                    base_dir = os.path.abspath(os.path.expanduser(step.working_directory))
                else:
                    base_dir = os.path.abspath(self.gaussian_job_root)
                os.makedirs(base_dir, exist_ok=True)
                stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", f"conf_{tool.tool_name}_{step.step_id if step else 'step'}")
                out_dir = os.path.join(base_dir, stem)
            out_dir = os.path.abspath(os.path.expanduser(str(out_dir)))
            os.makedirs(out_dir, exist_ok=True)
            kwargs = {
                "smiles": smiles,
                "num_conformers": int(get_first("num_conformers") or 20),
                "max_iter": int(get_first("max_iter") or 1000),
                "top_n": int(get_first("top_n") or 5),
                "individual_sdf_dir": out_dir,
                "individual_sdf_prefix": str(get_first("individual_sdf_prefix") or "conf"),
                "output_dir": out_dir,
            }
            artifacts = [out_dir]

        elif basename == "gen_gaussiancode.py":
            user_input = get_first("user_input", "query", "request_text", "scientific_question")
            if user_input is None and isinstance(payload.get("scientific_context"), dict):
                sc = payload.get("scientific_context", {})
                user_input = sc.get("scientific_question") or sc.get("question") or sc.get("summary")
            if user_input is None and step is not None:
                if isinstance(step.scientific_context, dict):
                    user_input = (
                        step.scientific_context.get("scientific_question")
                        or step.scientific_context.get("question")
                        or step.scientific_context.get("summary")
                    )
                elif isinstance(step.scientific_context, str) and step.scientific_context.strip():
                    user_input = step.scientific_context.strip()
            if user_input is None:
                user_input = payload.get("step_description") or str(input_data)
            user_input = str(user_input).strip() if user_input is not None else ""
            if not user_input:
                return {}, [], "缺少generate_gaussian_code所需user_input"
            kwargs = {
                "user_input": user_input,
                "query": user_input,
                "request_text": user_input,
                "timeout": get_first("timeout"),
                "local_backend": get_first("local_backend", "backend"),
                "local_model_path": get_first("local_model_path", "model_path", "model"),
                "local_model_name": get_first("local_model_name", "model_name"),
            }

        elif basename == "output_parser.py":
            log_candidates = []
            log_candidates.extend(
                [p for p in (self._resolve_path_hint(v, step=step) for v in self._extract_paths_with_suffix(payload.get("expected_input"), ".log")) if p]
            )
            log_candidates.extend(
                [p for p in (self._resolve_path_hint(v, step=step) for v in self._extract_paths_with_suffix(payload.get("expected_input"), ".out")) if p]
            )
            log_candidates.extend(self._extract_paths_by_suffixes(payload, ["log", "out"], step=step))
            log_candidates.extend(expected_input_paths)
            # 回退：上一步真实产出的 Gaussian 日志（planner 声明的 expected_input 常是占位、拿不到真路径）。
            # 最近产出的优先（reversed），_choose_path(must_exist=True) 会跳过不存在的。
            log_candidates.extend(
                self._filter_recent_artifacts_by_expected_names(
                    list(reversed(getattr(self, "_recent_gaussian_logs", []))),
                    payload.get("expected_input"),
                    ["log", "out"],
                )
            )
            input_log = self._choose_path(log_candidates, must_exist=True)
            if not input_log:
                return {}, [], "缺少Gaussian日志文件(.log/.out)"
            save_json = get_first("save_json_path", "output_json_path", "output_path")
            if isinstance(save_json, str):
                save_json = self._resolve_path_hint(save_json, step=step)
            if not save_json:
                save_json = self._choose_path(expected_output_paths, must_exist=False)
            if not save_json:
                save_json = self._default_output_path(".json", step, input_path=input_log)
            kwargs = {
                "input_file_path": input_log,
                "save_json_path": save_json,
                "properties": get_first("properties"),
                "include_metadata": bool(get_first("include_metadata") if get_first("include_metadata") is not None else True),
            }
            artifacts = [save_json]

        elif basename == "Get_gjf_from_log.py":
            log_candidates = []
            log_candidates.extend(
                [p for p in (self._resolve_path_hint(v, step=step) for v in self._extract_paths_with_suffix(payload.get("expected_input"), ".log")) if p]
            )
            log_candidates.extend(
                [p for p in (self._resolve_path_hint(v, step=step) for v in self._extract_paths_with_suffix(payload.get("expected_input"), ".out")) if p]
            )
            log_candidates.extend(self._extract_paths_by_suffixes(payload, ["log", "out"], step=step))
            log_candidates.extend(expected_input_paths)
            # 回退：上一步真实产出的 Gaussian 日志（同 output_parser，最近产出优先）。
            log_candidates.extend(
                self._filter_recent_artifacts_by_expected_names(
                    list(reversed(getattr(self, "_recent_gaussian_logs", []))),
                    payload.get("expected_input"),
                    ["log", "out"],
                )
            )
            input_log = self._choose_path(log_candidates, must_exist=True)
            if not input_log:
                return {}, [], "缺少Gaussian日志文件(.log/.out)"
            out_gjf = get_first("output_gjf_path", "gjf_path", "output_path")
            if isinstance(out_gjf, str):
                out_gjf = self._resolve_path_hint(out_gjf, step=step)
            if not out_gjf:
                out_gjf = self._choose_path(expected_output_paths, must_exist=False)
            if not out_gjf:
                out_gjf = self._default_output_path(".gjf", step, input_path=input_log)
            kwargs = {
                "input_file_path": input_log,
                "output_gjf_path": out_gjf,
                "route_line": (
                    get_first("route_line", "route_section")
                    or (step.route_section if step else None)
                    or getattr(self, "_latest_gaussian_route_section", None)
                ),
                "title": get_first("title") or (step.step_name if step else None),
                "chk_file": get_first("chk_file"),
            }
            artifacts = [out_gjf]

        elif basename == "reaction_thermochemistry.py":
            json_candidates = []
            json_candidates.extend(
                [p for p in (self._resolve_path_hint(v, step=step) for v in self._extract_paths_with_suffix(payload.get("expected_input"), ".json")) if p]
            )
            json_candidates.extend(self._extract_paths_by_suffixes(payload, ["json"], step=step))
            json_candidates.extend(expected_input_paths)
            json_candidates.extend(reversed(getattr(self, "_recent_gaussian_jsons", [])))
            input_jsons = list(dict.fromkeys([p for p in json_candidates if p and os.path.exists(p)]))
            if not input_jsons:
                return {}, [], "缺少反应热计算所需的已解析 JSON 输入"
            output_json = get_first("output_json_path", "save_json_path", "output_path")
            if isinstance(output_json, str):
                output_json = self._resolve_path_hint(output_json, step=step)
            if not output_json:
                output_json = self._choose_path(expected_output_paths, must_exist=False)
            if not output_json:
                output_json = self._default_output_path(".json", step, stem_hint="reaction_thermochemistry")
            reaction_expression = ""
            if step is not None and isinstance(step.scientific_context, dict):
                reaction_expression = str(
                    step.scientific_context.get("scientific_question")
                    or step.scientific_context.get("question")
                    or ""
                )
            if not reaction_expression and step is not None:
                reaction_expression = str(step.description or "")
            kwargs = {
                "input_file_paths": input_jsons,
                "reaction_expression": reaction_expression,
                "output_json_path": output_json,
            }
            artifacts = [output_json]

        elif basename == "stereoisomer.py":
            smiles = get_first("smiles")
            if smiles is None and isinstance(input_data, str):
                smiles = input_data
            if isinstance(smiles, list):
                smiles = smiles[0] if smiles else None
            if not smiles:
                return {}, [], "缺少smiles输入"
            kwargs = {
                "smiles": smiles,
                "try_embedding": bool(get_first("try_embedding") if get_first("try_embedding") is not None else True),
                "unique": bool(get_first("unique") if get_first("unique") is not None else True),
                "max_isomers": get_first("max_isomers"),
            }

        elif basename == "process_spectrum.py":
            input_candidates = self._extract_paths_by_suffixes(payload, ["log", "out", "fchk", "molden", "txt", "dat"], step=step)
            input_path = self._choose_path(input_candidates, must_exist=True)
            if not input_path:
                return {}, [], "缺少光谱输入文件"
            out_image = get_first("output_image_path", "output_path")
            if isinstance(out_image, str):
                out_image = os.path.abspath(os.path.expanduser(out_image))
            if not out_image:
                out_image = self._choose_path(expected_output_paths, must_exist=False)
            if not out_image:
                out_image = self._default_output_path(".png", step, input_path=input_path)
            kwargs = {
                "input_file_path": input_path,
                "output_image_path": out_image,
                "spectrum_type": str(get_first("spectrum_type") or "IR"),
            }
            artifacts = [out_image]

        elif basename == "plot_tools.py":
            json_candidates = self._extract_paths_by_suffixes(payload.get("expected_input"), ["json"], step=step)
            json_candidates.extend(self._extract_paths_by_suffixes(payload, ["json"], step=step))
            json_candidates.extend(reversed(getattr(self, "_recent_gaussian_jsons", [])))
            existing_json = [p for p in json_candidates if os.path.exists(p)]

            log_candidates = self._extract_paths_by_suffixes(payload.get("expected_input"), ["log", "out"], step=step)
            log_candidates.extend(self._extract_paths_by_suffixes(payload, ["log", "out"], step=step))
            input_source: Any = None
            if existing_json:
                input_source = existing_json if len(existing_json) > 1 else existing_json[0]
            else:
                log_candidates.extend(reversed(getattr(self, "_recent_gaussian_logs", [])))
                input_source = self._choose_path(log_candidates, must_exist=True)
            if not input_source:
                return {}, [], "缺少plot_tools所需输入文件"

            out_image = get_first("output_image_path", "output_path")
            if isinstance(out_image, str):
                out_image = self._resolve_path_hint(out_image, step=step)
            if not out_image:
                png_outputs = self._extract_paths_by_suffixes(payload.get("expected_output"), ["png"], step=step)
                out_image = self._choose_path(png_outputs, must_exist=False)
            if not out_image:
                out_image = self._default_output_path(".png", step)
            desc_lower = " ".join(
                str(v or "") for v in [
                    getattr(step, "description", "") if step is not None else "",
                    getattr(step, "expected_input", "") if step is not None else "",
                    payload.get("expected_input") if isinstance(payload, dict) else "",
                ]
            ).lower()
            if "raman" in desc_lower:
                spectrum_type = "Raman"
            elif "uv" in desc_lower:
                spectrum_type = "UV-Vis"
            elif "nmr" in desc_lower:
                spectrum_type = "NMR"
            else:
                spectrum_type = "IR"
            kwargs = {
                "input_file_path": input_source,
                "output_image_path": out_image,
                "spectrum_type": spectrum_type,
                "title": get_first("title") or (step.step_name if step else None),
                "xlabel": get_first("xlabel"),
                "ylabel": get_first("ylabel"),
                "xleft": get_first("xleft"),
                "xright": get_first("xright"),
                "ybottom": get_first("ybottom"),
                "ytop": get_first("ytop"),
                "x_reverse": get_first("x_reverse"),
                "y_reverse": get_first("y_reverse"),
                "dpi": get_first("dpi"),
                "FWHM": get_first("FWHM"),
            }
            artifacts = [out_image]

        elif basename == "plot_spectrum.py":
            curve_path = self._choose_path(self._extract_paths_by_suffixes(payload, ["txt", "dat", "csv"], step=step), must_exist=True)
            out_image = get_first("output_path", "output_image_path")
            if isinstance(out_image, str):
                out_image = os.path.abspath(os.path.expanduser(out_image))
            if not out_image:
                out_image = self._default_output_path(".png", step, stem_hint="spectrum")
            kwargs = {
                "curve_path": curve_path,
                "output_path": out_image,
            }
            if curve_path:
                artifacts = [out_image]
            else:
                return {}, [], "缺少曲线数据文件"

        elif basename == "run.py" and "23_TSPipeline" in os.path.abspath(script_path):
            config_path = get_first("config_path", "pipeline_config_path")
            if isinstance(config_path, str):
                config_path = os.path.abspath(os.path.expanduser(config_path))
            config_data = get_first("config", "pipeline_config")
            if not config_path and isinstance(config_data, dict):
                out_dir = get_first("base_results_dir", "output_dir")
                if isinstance(out_dir, str) and out_dir.strip():
                    out_dir = os.path.abspath(os.path.expanduser(out_dir))
                elif step is not None and step.working_directory:
                    out_dir = os.path.abspath(step.working_directory)
                else:
                    out_dir = os.path.abspath(os.path.join(self.gaussian_job_root, "ts_pipeline"))
                os.makedirs(out_dir, exist_ok=True)
                config_path = os.path.join(out_dir, "ts_pipeline_config.json")
                with open(config_path, "w", encoding="utf-8") as f:
                    json.dump(config_data, f, ensure_ascii=False, indent=2)
            if not config_path:
                return {}, [], "缺少23_TSPipeline所需config_path或config"
            kwargs = {"config_path": config_path}
            artifacts = [config_path]

        else:
            kwargs = dict(payload)

        filtered_kwargs = {k: v for k, v in kwargs.items() if v is not None}
        return filtered_kwargs, artifacts, None

    def _invoke_real_tool_callable(self, func: Any, kwargs: Dict[str, Any]) -> Tuple[Any, Optional[str]]:
        try:
            sig = inspect.signature(func)
        except Exception:
            try:
                return func(**kwargs), None
            except Exception as exc:
                return None, str(exc)

        accepts_var_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
        if accepts_var_kw:
            filtered = dict(kwargs)
        else:
            filtered = {k: v for k, v in kwargs.items() if k in sig.parameters}

        missing = []
        for name, p in sig.parameters.items():
            if p.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
                continue
            if p.default is inspect._empty and name not in filtered:
                missing.append(name)
        if missing:
            return None, f"缺少必需参数: {', '.join(missing)}"

        try:
            result = func(**filtered)
            return result, None
        except Exception as exc:
            return None, str(exc)

    def _extract_output_paths_from_payload(self, payload: Any) -> List[str]:
        paths: List[str] = []
        valid_suffixes = (".sdf", ".xyz", ".gjf", ".log", ".out", ".json", ".png", ".jpg", ".jpeg", ".chk", ".txt", ".csv", ".jsonl")

        def _walk(v: Any):
            if isinstance(v, str):
                s = v.strip().strip("\"'")
                if not s:
                    return
                lowered = s.lower()
                if lowered.endswith(valid_suffixes) or os.path.exists(os.path.expanduser(s)):
                    paths.append(os.path.abspath(os.path.expanduser(s)))
            elif isinstance(v, dict):
                for vv in v.values():
                    _walk(vv)
            elif isinstance(v, (list, tuple, set)):
                for vv in v:
                    _walk(vv)

        _walk(payload)
        unique = []
        seen = set()
        for p in paths:
            if p not in seen:
                seen.add(p)
                unique.append(p)
        return unique

    def _build_real_tool_response(self,
                                  success: bool,
                                  tool: ToolDefinition,
                                  script_path: str,
                                  backend: str,
                                  raw_result: Any,
                                  output_artifacts: Optional[List[str]] = None,
                                  message: str = "",
                                  error: Optional[str] = None) -> Dict[str, Any]:
        return {
            "success": bool(success),
            "tool_name": tool.tool_name,
            "tool_path": tool.tool_path,
            "script_path": script_path,
            "execution_mode": "real_tool",
            "execution_backend": backend,
            "raw_result": raw_result,
            "output_artifacts": output_artifacts or [],
            "message": message,
            "error": error,
        }

    def _execute_real_tool_via_import(self,
                                      tool: ToolDefinition,
                                      input_data: Any,
                                      step: Optional[ExecutionStep],
                                      script_path: str) -> Dict[str, Any]:
        module_name = self._resolve_tool_module_name(script_path)
        script_dir = os.path.abspath(os.path.dirname(script_path))
        inserted = False
        try:
            if script_dir not in sys.path:
                sys.path.insert(0, script_dir)
                inserted = True
            spec = importlib.util.spec_from_file_location(module_name, script_path)
            if spec is None or spec.loader is None:
                return self._build_real_tool_response(False, tool, script_path, "python_import", None, [], "模块加载失败", "无法创建模块spec")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        except Exception as exc:
            logger.warning(f"[RealTool] 模块导入失败 {script_path}: {exc}")
            return self._build_real_tool_response(False, tool, script_path, "python_import", None, [], "模块导入失败", str(exc))
        finally:
            if inserted:
                try:
                    sys.path.remove(script_dir)
                except ValueError:
                    pass

        func, callable_name = self._resolve_real_tool_callable(module, tool, os.path.basename(script_path))
        if not callable(func):
            logger.warning(f"[RealTool] 未找到可调用入口: {script_path}")
            return self._build_real_tool_response(False, tool, script_path, "python_import", None, [], "未找到可调用入口", "unsupported_callable")

        kwargs, artifacts, build_err = self._build_real_tool_call_context(tool, input_data, step, script_path, callable_name or "")
        if build_err:
            return self._build_real_tool_response(False, tool, script_path, "python_import", None, artifacts, "参数构建失败", build_err)

        raw_result, call_err = self._invoke_real_tool_callable(func, kwargs)
        if call_err:
            logger.warning(f"[RealTool] 调用失败 {tool.tool_name}: {call_err}")
            return self._build_real_tool_response(False, tool, script_path, "python_import", None, artifacts, "函数调用失败", call_err)

        success_flag = True
        if isinstance(raw_result, dict) and raw_result.get("success") is False:
            success_flag = False

        discovered_artifacts = self._extract_output_paths_from_payload({"artifacts": artifacts, "raw_result": raw_result})
        return self._build_real_tool_response(
            success=success_flag,
            tool=tool,
            script_path=script_path,
            backend="python_import",
            raw_result=raw_result,
            output_artifacts=discovered_artifacts,
            message="真实工具执行完成" if success_flag else "真实工具执行返回失败",
            error=(raw_result.get("error") if isinstance(raw_result, dict) else None) if not success_flag else None,
        )

    def _build_real_tool_subprocess_cmd(self, script_path: str, kwargs: Dict[str, Any]) -> Optional[List[str]]:
        base = os.path.basename(script_path)
        preferred_python = os.environ.get("ARCHE_TOOL_PYTHON")
        if not preferred_python:
            candidate = os.path.join(REPO_ROOT, ".venv", "bin", "python")
            preferred_python = candidate if os.path.exists(candidate) else sys.executable
        cmd = [preferred_python, script_path]
        if base == "gen_gaussiancode.py":
            user_input = kwargs.get("user_input") or kwargs.get("query") or kwargs.get("request_text")
            if not user_input or not str(user_input).strip():
                return None
            cmd.extend(["--user_input", str(user_input)])
            timeout_val = kwargs.get("timeout")
            if timeout_val is not None:
                try:
                    cmd.extend(["--timeout", str(int(timeout_val))])
                except Exception:
                    pass
            local_backend = kwargs.get("local_backend")
            if local_backend:
                cmd.extend(["--local_backend", str(local_backend)])
            local_model_path = kwargs.get("local_model_path")
            if local_model_path:
                cmd.extend(["--local_model_path", str(local_model_path)])
            local_model_name = kwargs.get("local_model_name")
            if local_model_name:
                cmd.extend(["--local_model_name", str(local_model_name)])
            return cmd
        if base == "sdf_to_xyz.py":
            input_file = kwargs.get("input_sdf_path") or kwargs.get("sdf_path")
            output_xyz = kwargs.get("output_xyz_path") or kwargs.get("output_path")
            if not input_file or not output_xyz:
                return None
            cmd.extend(["--input", str(input_file), "--output", str(output_xyz)])
            title = kwargs.get("title")
            if title:
                cmd.extend(["--title", str(title)])
            return cmd
        if base == "xyz2gjf.py":
            input_xyz = kwargs.get("input_xyz_path") or kwargs.get("xyz_path") or kwargs.get("input_file_path")
            output_gjf = kwargs.get("output_gjf_path") or kwargs.get("gjf_path") or kwargs.get("output_path")
            if not input_xyz or not output_gjf:
                return None
            cmd.extend(["single", str(input_xyz), str(output_gjf)])
            route_section = kwargs.get("route_section") or kwargs.get("route_parameters")
            if route_section:
                cmd.extend(["--route_section", str(route_section)])
            charge = kwargs.get("charge")
            if charge is not None:
                cmd.extend(["--charge", str(charge)])
            multiplicity = kwargs.get("multiplicity")
            if multiplicity is not None:
                cmd.extend(["--multiplicity", str(multiplicity)])
            title = kwargs.get("title")
            if title:
                cmd.extend(["--title", str(title)])
            chk_file = kwargs.get("chk_file")
            if chk_file:
                cmd.extend(["--chk_file", str(chk_file)])
            nprocshared = kwargs.get("nprocshared")
            if nprocshared is not None:
                cmd.extend(["--nprocshared", str(nprocshared)])
            mem = kwargs.get("mem")
            if mem:
                cmd.extend(["--mem", str(mem)])
            if kwargs.get("write_back_fixed_xyz"):
                cmd.append("--write_back_fixed_xyz")
            if kwargs.get("fix_atom_count_line") is False:
                cmd.append("--no_fix_atom_count_line")
            return cmd
        if base == "output_parser.py":
            input_file = kwargs.get("input_file_path")
            if not input_file:
                return None
            cmd.extend(["--input", str(input_file)])
            output_json = kwargs.get("save_json_path")
            if output_json:
                cmd.extend(["--output", str(output_json)])
            return cmd
        if base == "run.py" and "23_TSPipeline" in os.path.abspath(script_path):
            config_path = kwargs.get("config_path")
            if not config_path:
                return None
            cmd.append(str(config_path))
            return cmd
        if base == "plot_spectrum.py":
            curve = kwargs.get("curve_path")
            output = kwargs.get("output_path")
            if not curve or not output:
                return None
            cmd.extend(["--curve", str(curve), "--output", str(output)])
            return cmd
        return None

    def _execute_real_tool_via_subprocess(self,
                                          tool: ToolDefinition,
                                          input_data: Any,
                                          step: Optional[ExecutionStep],
                                          script_path: str) -> Dict[str, Any]:
        kwargs, artifacts, build_err = self._build_real_tool_call_context(tool, input_data, step, script_path, "subprocess")
        if build_err:
            return self._build_real_tool_response(False, tool, script_path, "subprocess", None, artifacts, "参数构建失败", build_err)
        cmd = self._build_real_tool_subprocess_cmd(script_path, kwargs)
        if not cmd:
            return self._build_real_tool_response(False, tool, script_path, "subprocess", None, artifacts, "未提供可用CLI映射", "unsupported_subprocess_cli")

        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=int(kwargs.get("timeout", 600)))
        except Exception as exc:
            logger.warning(f"[RealTool] 子进程执行失败 {script_path}: {exc}")
            return self._build_real_tool_response(False, tool, script_path, "subprocess", None, artifacts, "子进程执行失败", str(exc))

        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        success = proc.returncode == 0
        discovered_artifacts = self._extract_output_paths_from_payload({"artifacts": artifacts, "stdout": stdout})
        raw = {
            "returncode": proc.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "cmd": cmd,
        }
        return self._build_real_tool_response(
            success=success,
            tool=tool,
            script_path=script_path,
            backend="subprocess",
            raw_result=raw,
            output_artifacts=discovered_artifacts,
            message="CLI执行完成" if success else "CLI执行失败",
            error=stderr.strip() if not success else None,
        )

    def _execute_real_top_level_tool(self,
                                     tool: ToolDefinition,
                                     input_data: Any,
                                     step: Optional[ExecutionStep],
                                     script_path: str) -> Dict[str, Any]:
        import_result = self._execute_real_tool_via_import(tool, input_data, step, script_path)
        if import_result.get("success"):
            return import_result

        logger.info(f"[RealTool] import后端失败,尝试CLI后端: {tool.tool_name}")
        subprocess_result = self._execute_real_tool_via_subprocess(tool, input_data, step, script_path)
        if subprocess_result.get("success"):
            return subprocess_result

        # 保留最具信息量的错误
        if import_result.get("error") and subprocess_result.get("error"):
            subprocess_result["message"] = f"import与CLI均失败: {import_result.get('error')} | {subprocess_result.get('error')}"
        return subprocess_result

    def _infer_gaussian_job_type(self, tool_name: str, step: Optional[ExecutionStep] = None) -> str:
        text_parts = [str(tool_name or "")]
        if step is not None:
            text_parts.extend([
                str(step.description or ""),
                str(step.step_name or ""),
                str(step.job_type or ""),
                str(step.route_section or ""),
            ])
        text = " ".join(text_parts).lower()

        if "irc" in text:
            return "irc"
        if "transition" in text or re.search(r"\bts\b", text):
            return "ts"
        if "high" in text and "sp" in text:
            return "high_level_sp"
        if "single" in text or re.search(r"\bsp\b", text) or "single-point" in text:
            return "high_level_sp"
        if "frequency" in text or "freq" in text:
            return "freq"
        if "optimization" in text or re.search(r"\bopt\b", text):
            return "small_opt"
        return "small_opt"

    def _extract_paths_with_suffix(self, value: Any, suffix: str) -> List[str]:
        paths: List[str] = []
        if isinstance(value, str):
            text = value.strip().strip("\"'")
            token_pat = rf"([A-Za-z0-9_./+\-]+{re.escape(suffix)})"
            token_matches = re.findall(token_pat, text, flags=re.IGNORECASE)
            for token in token_matches:
                paths.append(token)
            if text.lower().endswith(suffix.lower()) and not token_matches:
                paths.append(text)
        elif isinstance(value, dict):
            for v in value.values():
                paths.extend(self._extract_paths_with_suffix(v, suffix))
        elif isinstance(value, (list, tuple, set)):
            for item in value:
                paths.extend(self._extract_paths_with_suffix(item, suffix))
        return paths

    def _resolve_gjf_path(self, input_data: Any, step: Optional[ExecutionStep] = None) -> Optional[str]:
        candidates: List[str] = []

        if step is not None:
            if isinstance(step.expected_input, str):
                candidates.extend(self._extract_paths_with_suffix(step.expected_input, ".gjf"))
            for artifact in (step.artifacts or []):
                if isinstance(artifact, dict):
                    for key in ("path", "file", "artifact_path", "output_path", "input_path"):
                        if key in artifact and isinstance(artifact[key], str):
                            candidates.extend(self._extract_paths_with_suffix(artifact[key], ".gjf"))

        candidates.extend(self._extract_paths_with_suffix(input_data, ".gjf"))
        if isinstance(input_data, dict):
            for key in ("gjf_path", "input_gjf", "gaussian_input", "input_file_path"):
                value = input_data.get(key)
                if isinstance(value, str):
                    candidates.append(value)

        unique_candidates = []
        seen = set()
        for path in candidates:
            expanded = self._resolve_path_hint(path, step=step) or os.path.abspath(os.path.expanduser(path))
            if expanded not in seen:
                seen.add(expanded)
                unique_candidates.append(expanded)

        for path in unique_candidates:
            if os.path.exists(path):
                return path
        return unique_candidates[0] if unique_candidates else None

    def _execute_existing_gaussian_job(self, step: Optional[ExecutionStep], input_data: Any, tool: Optional[ToolDefinition]) -> Optional[Dict[str, Any]]:
        """For execution-intent steps, prefer an already prepared .gjf over regenerating geometry."""
        gjf_path = self._resolve_gjf_path(input_data, step=step)
        if not gjf_path or not os.path.exists(gjf_path):
            return None
        return self.execute_gaussian_related_tool(
            "run_gaussian_deterministic",
            {"gjf_path": gjf_path},
            step=step,
            tool=tool,
        )

    def _resolve_gaussian_work_dir(self, gjf_path: str, step: Optional[ExecutionStep] = None) -> str:
        if step is not None and step.working_directory:
            return os.path.abspath(os.path.expanduser(step.working_directory))
        if gjf_path:
            return os.path.dirname(os.path.abspath(gjf_path))
        return os.path.abspath(self.gaussian_job_root)

    def _prepare_gaussian_job_inputs(self, tool_name: str, input_data: Any, step: Optional[ExecutionStep]) -> Dict[str, Any]:
        gjf_path = self._resolve_gjf_path(input_data, step=step)
        if not gjf_path:
            return {"success": False, "error": "未提供可用的.gjf输入文件路径"}
        if not os.path.exists(gjf_path):
            return {"success": False, "error": f".gjf文件不存在: {gjf_path}"}

        work_dir = self._resolve_gaussian_work_dir(gjf_path, step=step)
        os.makedirs(work_dir, exist_ok=True)

        base = os.path.splitext(os.path.basename(gjf_path))[0]
        step_id = (step.step_id if step is not None and step.step_id else str(uuid.uuid4())[:8])
        job_name = f"gauss_{base}_{step_id}"
        log_path = os.path.join(work_dir, f"{base}.log")
        chk_path = os.path.join(work_dir, f"{base}.chk")
        exit_code_path = os.path.join(work_dir, f".{base}_{step_id}.exitcode")
        state_path = self._job_state_path(work_dir, step_id, tool_name, stem_hint=base)

        return {
            "success": True,
            "step_id": step_id,
            "tool_name": tool_name,
            "work_dir": work_dir,
            "job_name": job_name,
            "gjf_path": os.path.abspath(gjf_path),
            "log_path": os.path.abspath(log_path),
            "chk_path": os.path.abspath(chk_path),
            "exit_code_path": os.path.abspath(exit_code_path),
            "state_path": os.path.abspath(state_path),
        }

    def _allocate_gaussian_resources(self, job_class: str, input_data: Any = None) -> Dict[str, Any]:
        cpu_total = os.cpu_count() or 4
        profiles = {
            "small_opt": {"cpus": 4, "mem_gb": 16, "walltime": "04:00:00"},
            "freq": {"cpus": 6, "mem_gb": 24, "walltime": "08:00:00"},
            "ts": {"cpus": 8, "mem_gb": 32, "walltime": "12:00:00"},
            "irc": {"cpus": 8, "mem_gb": 32, "walltime": "24:00:00"},
            "high_level_sp": {"cpus": 12, "mem_gb": 48, "walltime": "12:00:00"},
        }
        base = dict(profiles.get(job_class, profiles["small_opt"]))
        cpus = min(base["cpus"], max(1, cpu_total))
        mem_gb = max(base["mem_gb"], cpus * 2)

        overrides = {}
        if isinstance(input_data, dict):
            for key in ("cpus", "mem_gb", "walltime", "partition", "job_class"):
                if key in input_data and input_data[key] is not None:
                    overrides[key] = input_data[key]
            resources = input_data.get("resources")
            if isinstance(resources, dict):
                for key in ("cpus", "mem_gb", "walltime", "partition", "job_class"):
                    if key in resources and resources[key] is not None:
                        overrides[key] = resources[key]

        return {
            "cpus": int(overrides.get("cpus", cpus)),
            "mem_gb": int(overrides.get("mem_gb", mem_gb)),
            "walltime": str(overrides.get("walltime", base["walltime"])),
            "partition": overrides.get("partition", self.gaussian_slurm_partition),
            "job_class": str(overrides.get("job_class", job_class)),
        }

    def _job_state_path(self, work_dir: str, step_id: str, tool_name: str, stem_hint: Optional[str] = None) -> str:
        safe_step = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(step_id or "step"))
        safe_tool = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(tool_name or "gaussian"))
        safe_stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(stem_hint or "job"))
        return os.path.join(work_dir, f".gaussian_job_state_{safe_stem}_{safe_step}_{safe_tool}.json")

    def _write_job_state(self, state_path: str, state: Dict[str, Any]) -> None:
        os.makedirs(os.path.dirname(state_path), exist_ok=True)
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)

    def _read_job_state(self, state_path: str) -> Optional[Dict[str, Any]]:
        if not os.path.exists(state_path):
            return None
        try:
            with open(state_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
            return None
        except Exception:
            return None

    def _generate_gaussian_shell_script(self, job_state: Dict[str, Any]) -> str:
        script_path = os.path.join(job_state["work_dir"], f"run_{job_state['step_id']}.sh")
        command = self.gaussian_command
        gjf_q = shlex.quote(job_state["gjf_path"])
        log_q = shlex.quote(job_state["log_path"])
        exit_q = shlex.quote(job_state["exit_code_path"])
        lines = [
            "#!/bin/bash",
            "set -e",
            f"cd {shlex.quote(job_state['work_dir'])}",
        ]
        if self.gaussian_module_load:
            lines.append(self.gaussian_module_load)
        if self.gaussian_environment_hook:
            lines.append(self.gaussian_environment_hook)
        lines.extend([
            f"export OMP_NUM_THREADS={job_state['resources']['cpus']}",
            "set +e",
            f"{command} < {gjf_q} > {log_q} 2>&1",
            "rc=$?",
            f"echo $rc > {exit_q}",
            "exit $rc",
        ])
        with open(script_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        os.chmod(script_path, 0o755)
        return script_path

    def _generate_gaussian_sbatch_script(self, job_state: Dict[str, Any]) -> str:
        script_path = os.path.join(job_state["work_dir"], f"run_{job_state['step_id']}.sbatch")
        partition = job_state["resources"].get("partition")
        command = self.gaussian_command
        gjf_q = shlex.quote(job_state["gjf_path"])
        log_q = shlex.quote(job_state["log_path"])
        exit_q = shlex.quote(job_state["exit_code_path"])

        lines = [
            "#!/bin/bash",
            f"#SBATCH --job-name={job_state['job_name']}",
            f"#SBATCH --cpus-per-task={job_state['resources']['cpus']}",
            f"#SBATCH --mem={job_state['resources']['mem_gb']}G",
            f"#SBATCH --time={job_state['resources']['walltime']}",
        ]
        if partition:
            lines.append(f"#SBATCH --partition={partition}")
        lines.extend([
            "",
            f"cd {shlex.quote(job_state['work_dir'])}",
        ])
        if self.gaussian_module_load:
            lines.append(self.gaussian_module_load)
        if self.gaussian_environment_hook:
            lines.append(self.gaussian_environment_hook)
        lines.extend([
            f"export OMP_NUM_THREADS={job_state['resources']['cpus']}",
            "set +e",
            f"{command} < {gjf_q} > {log_q} 2>&1",
            "rc=$?",
            f"echo $rc > {exit_q}",
            "exit $rc",
        ])
        with open(script_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        os.chmod(script_path, 0o755)
        return script_path

    def _submit_slurm_job(self, job_state: Dict[str, Any]) -> Dict[str, Any]:
        if shutil.which("sbatch") is None:
            return {"success": False, "error": "sbatch命令不可用"}
        try:
            proc = subprocess.run(
                ["sbatch", job_state["script_path"]],
                cwd=job_state["work_dir"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            output = f"{proc.stdout}\n{proc.stderr}".strip()
            if proc.returncode != 0:
                return {"success": False, "error": output or f"sbatch失败(code={proc.returncode})"}
            match = re.search(r"Submitted batch job\s+(\d+)", output)
            if not match:
                return {"success": False, "error": f"无法解析Slurm job id: {output}"}
            return {"success": True, "job_id": match.group(1), "message": output}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def _submit_local_gaussian_job(self, job_state: Dict[str, Any]) -> Dict[str, Any]:
        try:
            proc = subprocess.Popen(
                ["bash", job_state["script_path"]],
                cwd=job_state["work_dir"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return {"success": True, "job_id": str(proc.pid), "pid": proc.pid}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def _map_slurm_state(self, raw_state: str) -> str:
        state = (raw_state or "").strip().upper()
        if state in {"PENDING", "CONFIGURING"}:
            return "queued"
        if state in {"RUNNING", "COMPLETING"}:
            return "running"
        if state == "COMPLETED":
            return "completed"
        if state in {"FAILED", "NODE_FAIL", "OUT_OF_MEMORY"}:
            return "failed"
        if state == "TIMEOUT":
            return "timed_out"
        if state.startswith("CANCELLED"):
            return "cancelled"
        return "unknown"

    def _poll_slurm_job(self, job_state: Dict[str, Any]) -> Dict[str, Any]:
        job_id = str(job_state.get("job_id") or "")
        if not job_id:
            return {"status": "unknown", "error": "缺少Slurm job_id"}
        if shutil.which("squeue") is not None:
            try:
                proc = subprocess.run(
                    ["squeue", "-j", job_id, "-h", "-o", "%T"],
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                if proc.returncode == 0 and proc.stdout.strip():
                    return {"status": self._map_slurm_state(proc.stdout.splitlines()[0].strip())}
            except Exception:
                pass
        if shutil.which("sacct") is not None:
            try:
                proc = subprocess.run(
                    ["sacct", "-j", job_id, "--format=State", "--noheader"],
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                if proc.returncode == 0 and proc.stdout.strip():
                    first = proc.stdout.strip().split()[0]
                    return {"status": self._map_slurm_state(first)}
            except Exception:
                pass
        return {"status": "unknown", "error": "无法通过squeue/sacct获取状态"}

    def _poll_local_job(self, job_state: Dict[str, Any]) -> Dict[str, Any]:
        exit_code_path = job_state.get("exit_code_path")
        if exit_code_path and os.path.exists(exit_code_path):
            try:
                with open(exit_code_path, "r", encoding="utf-8") as f:
                    code = int((f.read() or "").strip())
                return {"status": "completed" if code == 0 else "failed", "exit_code": code}
            except Exception as exc:
                return {"status": "unknown", "error": str(exc)}

        pid_val = job_state.get("pid")
        try:
            pid = int(pid_val) if pid_val is not None else None
        except Exception:
            pid = None
        if pid is not None:
            try:
                os.kill(pid, 0)
                return {"status": "running"}
            except ProcessLookupError:
                return {"status": "unknown", "error": "进程已结束但未找到退出码"}
            except PermissionError:
                return {"status": "running"}
        return {"status": "unknown", "error": "缺少本地进程PID"}

    def _format_gaussian_job_response(self,
                                      job_state: Dict[str, Any],
                                      parsed_results: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        status = str(job_state.get("status") or "unknown")
        completed = status == "completed"
        response: Dict[str, Any] = {
            "execution_mode": "gaussian_job",
            "scheduler": job_state.get("scheduler"),
            "job_state": job_state,
            "job_id": job_state.get("job_id"),
            "work_dir": job_state.get("work_dir"),
            "status": status,
            "submitted": bool(job_state.get("submit_time")),
            "completed": completed,
            "parsed_results": parsed_results,
            "output_artifacts": job_state.get("expected_outputs", {}),
            "resources": job_state.get("resources", {}),
            "message": job_state.get("message", ""),
            "error": job_state.get("error"),
            "job_type": job_state.get("job_type", "unknown"),
            "normal_termination": None,
            "converged": None,
            "metadata": {
                "tool": job_state.get("tool_name"),
                "status": status,
                "execution_mode": "gaussian_job",
                "scheduler": job_state.get("scheduler"),
                "job_id": job_state.get("job_id"),
            }
        }

        if parsed_results:
            response.update({
                "normal_termination": parsed_results.get("normal_termination"),
                "converged": parsed_results.get("converged"),
                "scf_converged": parsed_results.get("scf_converged"),
                "scf_energy": parsed_results.get("scf_energy"),
                "free_energy": parsed_results.get("free_energy"),
                "zero_point_energy": parsed_results.get("zero_point_energy"),
                "n_imag_freq": parsed_results.get("n_imag_freq"),
                "imag_freqs": parsed_results.get("imag_freqs"),
                "frequencies": parsed_results.get("frequencies"),
                "irc_verified": parsed_results.get("irc_verified"),
                "irc_path": parsed_results.get("irc_path"),
                "raw_output_preview": parsed_results.get("raw_output_preview"),
            })
        return response

    def _collect_finished_gaussian_outputs(self, job_state: Dict[str, Any]) -> Dict[str, Any]:
        log_path = job_state.get("log_path")
        parsed_results = None
        if log_path and os.path.exists(log_path):
            try:
                with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                    raw_text = f.read()
                parsed_results = self.parse_gaussian_output(raw_text)
            except Exception as exc:
                job_state["error"] = f"解析Gaussian日志失败: {exc}"

        if job_state.get("status") == "completed":
            job_state["message"] = "Gaussian作业已完成"
        elif job_state.get("status") in {"failed", "timed_out", "cancelled"}:
            job_state["message"] = f"Gaussian作业终止: {job_state.get('status')}"
        else:
            job_state["message"] = f"Gaussian作业状态: {job_state.get('status')}"

        return self._format_gaussian_job_response(job_state, parsed_results=parsed_results)

    def _recover_gaussian_job(self, job_state: Dict[str, Any]) -> Dict[str, Any]:
        terminal_status = {"completed", "failed", "timed_out", "cancelled"}
        status = str(job_state.get("status") or "unknown")
        if status not in terminal_status:
            if job_state.get("scheduler") == "slurm":
                poll_result = self._poll_slurm_job(job_state)
            else:
                poll_result = self._poll_local_job(job_state)
            job_state["status"] = poll_result.get("status", "unknown")
            if poll_result.get("error"):
                job_state["error"] = poll_result.get("error")
            if poll_result.get("exit_code") is not None:
                job_state["exit_code"] = poll_result.get("exit_code")
            status = job_state["status"]

        job_state["last_check_time"] = datetime.datetime.utcnow().isoformat() + "Z"
        self._write_job_state(job_state["state_path"], job_state)

        if status in terminal_status:
            return self._collect_finished_gaussian_outputs(job_state)
        return self._format_gaussian_job_response(job_state, parsed_results=None)

    def _execute_gaussian_job_backend(self, tool_name: str, input_data: Any, step: Optional[ExecutionStep] = None) -> Dict[str, Any]:
        job_class = self._infer_gaussian_job_type(tool_name, step=step)
        prepared = self._prepare_gaussian_job_inputs(tool_name, input_data, step)
        if not prepared.get("success"):
            return {
                "execution_mode": "gaussian_job",
                "scheduler": self.gaussian_execution_mode,
                "status": "failed",
                "submitted": False,
                "completed": False,
                "message": "Gaussian作业准备失败",
                "error": prepared.get("error"),
                "metadata": {"tool": tool_name, "status": "failed"},
                "job_type": job_class,
            }

        existing_state = self._read_job_state(prepared["state_path"])
        if isinstance(existing_state, dict) and existing_state.get("execution_mode") == "gaussian_job":
            existing_status = str(existing_state.get("status") or "unknown")
            if not (existing_status == "prepared" and not existing_state.get("job_id")):
                return self._recover_gaussian_job(existing_state)

        resources = self._allocate_gaussian_resources(job_class, input_data=input_data)
        scheduler = self.gaussian_execution_mode if self.gaussian_execution_mode in {"slurm", "api"} else "local_shell"
        state: Dict[str, Any] = {
            "step_id": prepared["step_id"],
            "tool_name": prepared["tool_name"],
            "execution_mode": "gaussian_job",
            "scheduler": scheduler,
            "work_dir": prepared["work_dir"],
            "gjf_path": prepared["gjf_path"],
            "script_path": None,
            "log_path": prepared["log_path"],
            "chk_path": prepared["chk_path"],
            "exit_code_path": prepared["exit_code_path"],
            "state_path": prepared["state_path"],
            "job_id": None,
            "status": "prepared",
            "submit_time": None,
            "last_check_time": None,
            "retry_count": 0,
            "expected_outputs": {
                "gjf": prepared["gjf_path"],
                "log": prepared["log_path"],
                "chk": prepared["chk_path"],
                "exit_code": prepared["exit_code_path"],
            },
            "resources": resources,
            "error": None,
            "message": "作业已准备",
            "job_name": prepared["job_name"],
            "job_type": job_class,
        }

        try:
            if scheduler == "api":
                return self._run_gaussian_via_api(state)
            if scheduler == "slurm":
                state["script_path"] = self._generate_gaussian_sbatch_script(state)
                submit_result = self._submit_slurm_job(state)
            else:
                state["script_path"] = self._generate_gaussian_shell_script(state)
                submit_result = self._submit_local_gaussian_job(state)

            if not submit_result.get("success"):
                state["status"] = "failed"
                state["error"] = submit_result.get("error")
                state["message"] = "作业提交失败"
                self._write_job_state(state["state_path"], state)
                return self._format_gaussian_job_response(state, parsed_results=None)

            state["job_id"] = submit_result.get("job_id")
            if submit_result.get("pid") is not None:
                state["pid"] = submit_result.get("pid")
            state["status"] = "submitted"
            state["submit_time"] = datetime.datetime.utcnow().isoformat() + "Z"
            state["message"] = "作业已提交"
            self._write_job_state(state["state_path"], state)
            return self._recover_gaussian_job(state)
        except Exception as exc:
            state["status"] = "failed"
            state["error"] = str(exc)
            state["message"] = "作业执行异常"
            self._write_job_state(state["state_path"], state)
            return self._format_gaussian_job_response(state, parsed_results=None)

    def _run_gaussian_via_api(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Gaussian 远程 API 后端（mode=api）：把 .gjf 内容 POST 给 Gaussian-as-a-Service，
        同步取回完整 log + normal_termination；写入 log_path/exit_code 后复用既有 recover/解析链路。"""
        import base64

        if not self.gaussian_api_base_url:
            if self.local_pyscf_available and self.enable_local_pyscf_fallback:
                logger.warning("[GaussianAPI] 未配置远程端点，回退到本地 PySCF 后端")
                return self._run_gaussian_via_pyscf(state)
            state["status"] = "failed"
            state["error"] = "GAUSSIAN_BASE_URL 未配置，无法走 api 模式"
            state["message"] = "Gaussian API 未配置"
            self._write_job_state(state["state_path"], state)
            return self._format_gaussian_job_response(state, parsed_results=None)

        try:
            with open(state["gjf_path"], "r", encoding="utf-8") as f:
                gjf_content = f.read()
        except Exception as exc:
            state["status"] = "failed"
            state["error"] = f"Gaussian API 调用失败: {exc}"
            state["message"] = "Gaussian API 调用失败"
            self._write_job_state(state["state_path"], state)
            return self._format_gaussian_job_response(state, parsed_results=None)

        headers = {"content-type": "application/json"}
        if self.gaussian_api_key:
            headers["x-api-key"] = self.gaussian_api_key
        if self.gaussian_api_ingress_ak and self.gaussian_api_ingress_sk:
            token = base64.b64encode(
                f"{self.gaussian_api_ingress_ak}:{self.gaussian_api_ingress_sk}".encode("utf-8")
            ).decode("ascii")
            headers["Authorization"] = f"Basic {token}"

        def _format_api_exc(exc: Exception) -> str:
            detail = str(exc)
            response = getattr(exc, "response", None)
            status = getattr(response, "status_code", None)
            if status is not None and str(status) not in detail:
                detail = f"{detail} (status={status})"
            try:
                body = (response.text or "").strip() if response is not None else ""
            except Exception:
                body = ""
            if body:
                detail = f"{detail} body={body[:300]}"
            return detail

        def _is_retryable_api_exc(exc: Exception) -> bool:
            if isinstance(exc, (requests.Timeout, requests.ConnectionError)):
                return True
            response = getattr(exc, "response", None)
            return getattr(response, "status_code", None) in {429, 500, 502, 503, 504}

        data = None
        total_attempts = max(1, int(self.gaussian_api_max_retries) + 1)
        for attempt in range(1, total_attempts + 1):
            try:
                logger.info(
                    f"[GaussianAPI] 提交作业到 {self.gaussian_api_base_url}/v1/gaussian/run "
                    f"(attempt {attempt}/{total_attempts})"
                )
                resp = requests.post(
                    f"{self.gaussian_api_base_url}/v1/gaussian/run",
                    headers=headers,
                    json={"input": gjf_content, "timeout_seconds": self.gaussian_api_timeout},
                    timeout=self.gaussian_api_timeout + 60,
                )
                resp.raise_for_status()
                data = resp.json()
                state["retry_count"] = attempt - 1
                break
            except Exception as exc:
                state["retry_count"] = attempt - 1
                if attempt < total_attempts and _is_retryable_api_exc(exc):
                    delay = self.gaussian_api_retry_backoff * attempt
                    logger.warning(
                        f"[GaussianAPI] attempt {attempt}/{total_attempts} failed, retrying in {delay:.1f}s: "
                        f"{_format_api_exc(exc)}"
                    )
                    if delay > 0:
                        time.sleep(delay)
                    continue
                if self.local_pyscf_available and self.enable_local_pyscf_fallback:
                    logger.warning(f"[GaussianAPI] 远程后端失败，回退到本地 PySCF: {_format_api_exc(exc)}")
                    return self._run_gaussian_via_pyscf(state)
                state["status"] = "failed"
                state["error"] = f"Gaussian API 调用失败: {_format_api_exc(exc)}"
                state["message"] = "Gaussian API 调用失败"
                self._write_job_state(state["state_path"], state)
                return self._format_gaussian_job_response(state, parsed_results=None)

        if not isinstance(data, dict):
            state["status"] = "failed"
            state["error"] = "Gaussian API 调用失败: 空响应或非 JSON 响应"
            state["message"] = "Gaussian API 调用失败"
            self._write_job_state(state["state_path"], state)
            return self._format_gaussian_job_response(state, parsed_results=None)

        log_text = data.get("log") or ""
        normal = bool(data.get("normal_termination"))
        returncode = data.get("returncode")
        try:
            with open(state["log_path"], "w", encoding="utf-8") as f:
                f.write(log_text)
            with open(state["exit_code_path"], "w", encoding="utf-8") as f:
                f.write("0" if (data.get("ok") and normal) else str(returncode if returncode is not None else 1))
        except Exception as exc:
            logger.warning(f"[GaussianAPI] 写入结果失败: {exc}")

        state["job_id"] = data.get("job_id")
        state["scheduler"] = "api"
        state["submit_time"] = datetime.datetime.utcnow().isoformat() + "Z"
        state["last_check_time"] = state["submit_time"]
        state["status"] = "completed" if normal else "failed"
        state["message"] = "Gaussian API 正常结束" if normal else "Gaussian API 异常结束"
        if not normal:
            state["error"] = f"Gaussian 未正常结束 (returncode={returncode}, timed_out={data.get('timed_out')})"
        self._write_job_state(state["state_path"], state)
        return self._recover_gaussian_job(state)

    def _run_gaussian_via_pyscf(self, state: Dict[str, Any]) -> Dict[str, Any]:
        if not self.local_pyscf_available or run_pyscf_job is None or dump_pyscf_log is None:
            state["status"] = "failed"
            state["error"] = "本地PySCF后端不可用"
            state["message"] = "PySCF 后端不可用"
            self._write_job_state(state["state_path"], state)
            return self._format_gaussian_job_response(state, parsed_results=None)
        try:
            result = run_pyscf_job(state["gjf_path"])
            dump_pyscf_log(result, state["log_path"])
            with open(state["exit_code_path"], "w", encoding="utf-8") as f:
                f.write("0")
        except Exception as exc:
            state["status"] = "failed"
            state["error"] = f"本地PySCF计算失败: {exc}"
            state["message"] = "PySCF 后端失败"
            self._write_job_state(state["state_path"], state)
            return self._format_gaussian_job_response(state, parsed_results=None)

        state["job_id"] = f"pyscf:{os.path.basename(state['gjf_path'])}"
        state["scheduler"] = "local_pyscf"
        state["submit_time"] = datetime.datetime.utcnow().isoformat() + "Z"
        state["last_check_time"] = state["submit_time"]
        state["status"] = "completed"
        state["message"] = "PySCF 本地计算完成"
        state["error"] = None
        self._write_job_state(state["state_path"], state)
        return self._recover_gaussian_job(state)

    def _should_use_real_gaussian_backend(self, tool_name: str, input_data: Any, step: Optional[ExecutionStep] = None) -> bool:
        # 已移除 replay:只要能解析到 .gjf 或工具语义是"执行/提交",就走真实后端。
        if self._resolve_gjf_path(input_data, step=step):
            return True
        tool_lower = str(tool_name or "").lower()
        if any(k in tool_lower for k in ["generate_gaussian_code", "xyz_to_gjf", "sdf_to_gjf", "smiles_to_sdf"]):
            return False
        return any(k in tool_lower for k in ["run", "submit", "execute", "gaussian_job"])

    def _is_gaussian_execution_intent(self, step: Optional[ExecutionStep], tool: Optional[ToolDefinition]) -> bool:
        """识别"执行 Gaussian"意图的步骤(区别于"生成输入/转换/解析")。planner 会把这类步骤
        钳制成 generate_gaussian_code(代码生成),据此判定后改走确定性真实计算链。"""
        if step is None:
            return False
        tool_name = str(getattr(tool, "tool_name", "") or getattr(step, "tool_name", "") or "").lower()
        if tool_name in {
            "smiles2sdf",
            "sdf_to_xyz",
            "sdf_to_gjf",
            "xyz_to_gjf",
            "parse_gaussian_output",
            "get_gjf_from_log",
        }:
            return False
        desc = " ".join(str(x) for x in [
            getattr(step, "description", "") or "",
            getattr(tool, "tool_name", "") if tool else "",
            getattr(step, "tool_name", "") or "",
        ]).lower()
        if "gaussian" not in desc and "homo" not in desc and "scf" not in desc and "dft" not in desc:
            return False
        if (
            ("route section" in desc or "keywords" in desc or "keyword" in desc or "关键字" in desc or "路由" in desc)
            and "gjf" not in desc
            and "log" not in desc
            and "run gaussian" not in desc
        ):
            return False
        # 排除纯生成/转换/解析步骤(它们不该被改写)
        if any(k in desc for k in ["generate gaussian input", "write gaussian input", "prepare gaussian input",
                                    "create a gaussian input file", "create gaussian input file",
                                    "convert ", "extract ", "parse ", "read the gaussian", "summarize", "report ",
                                    "生成 gaussian 输入", "生成gaussian输入", "解析", "提取", "转换", "读取", "汇总", "整理", "输出最终结果"]):
            return False
        return any(k in desc for k in ["execute", "run ", "optimization", "optimize", "single-point", "single point",
                                        "energy calculation", "scf", "calculate", "calculation", "compute", "执行", "运行", "优化", "单点"])

    def _select_route_section(
        self,
        step_dict: Dict[str, Any],
        calculation_context: Optional[Dict[str, Any]],
        gaussian_review: Optional[Dict[str, Any]],
    ) -> Optional[str]:
        """Pick the route_section that should reach the deterministic Gaussian run.

        When expert review says the route was revised/corrected, the revised
        recommended_route must override the planner's earlier route fields. Accept
        common revised-status variants and normalize route strings that omit the
        leading Gaussian "#" marker.
        """
        step_dict = step_dict if isinstance(step_dict, dict) else {}
        calculation_context = calculation_context if isinstance(calculation_context, dict) else {}
        gaussian_review = gaussian_review if isinstance(gaussian_review, dict) else {}
        review_status = str(gaussian_review.get("review_status") or "").lower()
        recommended_route = self._normalize_route_section(gaussian_review.get("recommended_route"))
        revised_statuses = {
            "revised",
            "revision",
            "corrected",
            "modified",
            "updated",
            "fixed",
            "needs_revision",
            "requires_revision",
            "needs-revision",
            "requires-revision",
        }
        if review_status in revised_statuses and recommended_route and recommended_route.startswith("#"):
            return recommended_route
        return (
            self._normalize_route_section(step_dict.get("route_section"))
            or self._normalize_route_section(step_dict.get("gaussian_keywords"))
            or self._normalize_route_section(step_dict.get("current_route"))
            or self._normalize_route_section(calculation_context.get("current_route_or_keywords"))
            or recommended_route
        )

    @staticmethod
    def _normalize_route_section(value: Any) -> Optional[str]:
        """Return a cleaned Gaussian route string, adding '#' when omitted."""
        if not isinstance(value, str):
            return None
        route = value.strip()
        if not route:
            return None
        fenced = re.findall(r"`([^`]*#[^`]*)`", route)
        if fenced:
            route = fenced[-1].strip()
        else:
            line_hits = [
                line.strip()
                for line in route.splitlines()
                if "#" in line and re.search(r"\b(opt|freq|irc|td|scf|sp|nosymm|geom|scrf)\b", line, re.I)
            ]
            if line_hits:
                route = line_hits[0]
            else:
                hash_hit = re.search(r"(#.*)", route)
                if hash_hit:
                    route = hash_hit.group(1).strip()
        route = route.strip().strip("`").strip()
        route = route.replace("**", "")
        route = route.strip(" \"'")
        if route.startswith("#"):
            stripped = route[1:].lstrip()
            if stripped.startswith(("P ", "p ")) or re.match(r"^[Pp]\b", stripped):
                route = "#" + stripped
            else:
                route = "# " + stripped
        route = re.sub(r"(?<=[A-Za-z0-9*+\)])['\"](?=\s|$)", "", route)
        route = re.sub(r"\s+", " ", route).strip()
        while route.endswith(")") and route.count("(") < route.count(")"):
            route = route[:-1].rstrip()
        if route.startswith("#"):
            return route
        looks_like_route = (
            re.search(r"\b[A-Za-z][A-Za-z0-9+\-*()]*\s*/\s*[A-Za-z0-9+\-*(),]+", route) is not None
            or re.search(r"\b(opt|freq|irc|td|scf|sp|nosymm|geom|scrf)\b", route, re.I) is not None
        )
        if looks_like_route:
            return f"#{route}" if route.lower().startswith("p ") else f"# {route}"
        return route

    def _step_wants_frequency(self, step: Optional[ExecutionStep]) -> bool:
        """Detect frequency intent from structured context, not only description text."""
        if step is None:
            return False
        if str(getattr(step, "job_type", "") or "").lower() == "freq":
            return True
        parts: List[str] = [str(getattr(step, "expected_output", "") or "")]
        ctx = getattr(step, "scientific_context", None)
        if isinstance(ctx, dict):
            for key in ("expected_validation_requirements", "validation_plan",
                        "calculation_purpose", "scientific_question", "question"):
                val = ctx.get(key)
                if isinstance(val, (list, tuple)):
                    parts.extend(str(v) for v in val)
                elif val:
                    parts.append(str(val))
        elif isinstance(ctx, str):
            parts.append(ctx)
        blob = " ".join(p for p in parts if p)
        return bool(re.search(
            r"\bIR\b|infrared|红外|振动|vibrat|frequenc|频率|raman|拉曼|zero.?point|零点|"
            r"thermo|热力学|enthalp|焓|gibbs|自由能|反应热|spectr|光谱",
            blob, re.I))

    def _deterministic_gaussian_route(self, step: Optional[ExecutionStep], input_data: Any) -> str:
        """Resolve the Gaussian route used by the deterministic real-run path.

        Explicit or corrected step.route_section wins. Otherwise infer method, basis,
        and job keywords from text plus structured frequency intent.
        """
        explicit = (getattr(step, "route_section", None) or "").strip() if step else ""
        if explicit.startswith("#"):
            return explicit
        blob = " ".join(str(x) for x in [
            getattr(step, "description", "") if step else "",
            getattr(self, "_run_question", "") or getattr(self, "user_question", "") or getattr(self, "question", ""),
            getattr(self, "_current_strategy_name", "") or "",
            input_data if isinstance(input_data, str) else "",
        ] if x)
        m = re.search(r"\b(CAM-B3LYP|wB97X-?D|M06-?2X|B3LYP|PBE0|PBE1PBE|TPSSh|B3PW91|HF|MP2)\b\s*/\s*([A-Za-z0-9\-\+\(\),\*']+)", blob, re.I)
        func, basis = (m.group(1), m.group(2)) if m else ("B3LYP", "6-31G(d)")
        want_opt = bool(re.search(r"optim|geometry opt|结构优化|几何优化|optimi[sz]e|平衡构型", blob, re.I))
        want_freq = bool(re.search(r"\bIR\b|infrared|红外|振动|vibrat|frequenc|频率|raman|拉曼|zero.?point|零点|thermo|热力学|enthalp|焓|gibbs|自由能|反应热", blob, re.I))
        want_freq = want_freq or self._step_wants_frequency(step)
        want_td = bool(re.search(r"UV.?Vis|紫外|可见光|excit|激发态|TD.?DFT|td-dft|electronic transition|发射光谱|emission spectrum|跃迁", blob, re.I))
        kws: List[str] = []
        if want_freq:
            kws.append("opt")  # IR/拉曼/热力学需先优化到极小点,否则出虚频
            kws.append("freq")
        elif want_opt:
            kws.append("opt")
        if want_td:
            kws.append("td")
        route = f"# {func}/{basis}"
        if kws:
            route += " " + " ".join(dict.fromkeys(kws))  # 去重保序
        return route.rstrip()

    def _find_latest_geometry_file(self, suffixes: List[str]) -> Optional[str]:
        """在本次运行受控目录里查找最近的几何文件(.sdf/.xyz/.gjf)。

        这里不能扫描 cwd、项目根或系统 /tmp；那些位置可能残留其它 run 的几何文件，
        会把无关分子绑定到当前科学问题上。
        """
        import glob
        sufs = tuple((s if s.startswith(".") else f".{s}").lower() for s in suffixes)
        dirs: List[str] = []
        for d in [getattr(self, "work_dir", None), getattr(self, "gaussian_job_root", None),
                  os.environ.get("ARCHE_DETERMINISTIC_DIR")]:
            if d and os.path.isdir(d) and d not in dirs:
                dirs.append(d)
        cands: List[str] = []
        for d in dirs:
            for suf in sufs:
                cands.extend(glob.glob(os.path.join(d, "**", f"*{suf}"), recursive=True))
        cands = [c for c in cands if os.path.isfile(c)]
        if not cands:
            return None
        return max(cands, key=lambda p: os.path.getmtime(p))

    def _charge_mult_str(self, mol) -> str:
        """从 RDKit 分子推 Gaussian 的「电荷 自旋多重度」:formal charge + (自由基电子数+1);失败回退中性单重态。"""
        try:
            from rdkit import Chem  # type: ignore
            charge = Chem.GetFormalCharge(mol)
            n_rad = sum(a.GetNumRadicalElectrons() for a in mol.GetAtoms())
            return f"{charge} {n_rad + 1}"
        except Exception:
            return "0 1"

    def _coords_from_sdf(self, sdf_path: str) -> Tuple[Optional[str], str]:
        """从 .sdf 读 3D 坐标 → (Gaussian 笛卡尔坐标块, "电荷 多重度")。"""
        try:
            from rdkit import Chem  # type: ignore
            m = Chem.MolFromMolFile(sdf_path, removeHs=False, sanitize=False)
            if m is None or m.GetNumConformers() == 0:
                return None, "0 1"
            conf = m.GetConformer()
            coords = "\n".join(
                f"{a.GetSymbol():<2} {conf.GetAtomPosition(a.GetIdx()).x:>14.8f} "
                f"{conf.GetAtomPosition(a.GetIdx()).y:>14.8f} {conf.GetAtomPosition(a.GetIdx()).z:>14.8f}"
                for a in m.GetAtoms())
            return coords, self._charge_mult_str(m)
        except Exception as exc:
            logger.warning(f"[确定性Gaussian] 读取 sdf 坐标失败 {sdf_path}: {exc}")
            return None, "0 1"

    def _sdf_metadata(self, sdf_path: str) -> Dict[str, Any]:
        try:
            from rdkit import Chem  # type: ignore
            m = Chem.MolFromMolFile(sdf_path, removeHs=False, sanitize=False)
            if m is None:
                return {}
            return {
                "atom_count": int(m.GetNumAtoms()),
                "elements": sorted({a.GetSymbol() for a in m.GetAtoms()}),
                "formal_charge": int(Chem.GetFormalCharge(m)),
            }
        except Exception:
            return {}

    # ---- 分子身份匹配:把几何绑定到"本步应算的目标分子",拒绝复用无关几何(如残留 water.sdf) ----
    def _canonical_smiles(self, smi: str) -> Optional[str]:
        """SMILES → 规范化 SMILES(去显式 H、统一芳香/顺反),无法解析返回 None。"""
        try:
            from rdkit import Chem  # type: ignore
            m = Chem.MolFromSmiles(str(smi))
            return Chem.MolToSmiles(m) if m is not None else None
        except Exception:
            return None

    def _formula_from_smiles(self, smi: str) -> Optional[str]:
        """SMILES → 含氢分子式(与从 .sdf 读到的显式-H 分子式可比)。"""
        try:
            from rdkit import Chem  # type: ignore
            from rdkit.Chem import rdMolDescriptors  # type: ignore
            m = Chem.MolFromSmiles(str(smi))
            if m is None:
                return None
            return rdMolDescriptors.CalcMolFormula(Chem.AddHs(m))
        except Exception:
            return None

    def _sdf_identity(self, sdf_path: str) -> Dict[str, Optional[str]]:
        """读 .sdf → {canonical_smiles, formula}(尽力而为;bond order 缺失时靠分子式兜底)。"""
        out: Dict[str, Optional[str]] = {"canonical_smiles": None, "formula": None}
        try:
            from rdkit import Chem  # type: ignore
            from rdkit.Chem import rdMolDescriptors  # type: ignore
            m = Chem.MolFromMolFile(sdf_path, removeHs=False, sanitize=True)
            if m is None:
                m = Chem.MolFromMolFile(sdf_path, removeHs=False, sanitize=False)
            if m is None:
                return out
            try:
                out["canonical_smiles"] = Chem.MolToSmiles(Chem.RemoveHs(m))
            except Exception:
                try:
                    out["canonical_smiles"] = Chem.MolToSmiles(m)
                except Exception:
                    pass
            try:
                out["formula"] = rdMolDescriptors.CalcMolFormula(m)
            except Exception:
                pass
        except Exception:
            pass
        return out

    def _sdf_matches_target(self, sdf_path: str, target_smiles: List[str]) -> bool:
        """.sdf 分子是否与任一目标 SMILES 一致(规范 SMILES 相等或含氢分子式相等)。
        无法解析几何身份时返回 False(由调用方决定是否拒绝复用)。"""
        if not target_smiles:
            return False
        ident = self._sdf_identity(sdf_path)
        sdf_canon = ident.get("canonical_smiles")
        sdf_formula = ident.get("formula")
        if not sdf_canon and not sdf_formula:
            return False
        for smi in target_smiles:
            tcanon = self._canonical_smiles(smi)
            if tcanon and sdf_canon and tcanon == sdf_canon:
                return True
            tform = self._formula_from_smiles(smi)
            if tform and sdf_formula and tform == sdf_formula:
                return True
        return False

    def _parse_spectroscopy_from_log(self, log_path: str) -> Dict[str, Any]:
        """从 Gaussian log 解析 IR 峰(频率 cm⁻¹ + 强度)与激发态(eV/nm/f)。解析器只取频率列表、
        不取 IR 强度/激发态,这里补上,供结论 surface IR/UV-Vis 真实结果。"""
        out: Dict[str, Any] = {}
        try:
            with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                log = f.read()
        except Exception:
            return out
        freqs: List[float] = []
        for m in re.finditer(r"Frequencies\s*--\s*(.*)", log):
            freqs.extend(float(x) for x in re.findall(r"-?\d+\.\d+", m.group(1)))
        inten: List[float] = []
        for m in re.finditer(r"IR Inten\s*--\s*(.*)", log):
            inten.extend(float(x) for x in re.findall(r"-?\d+\.\d+", m.group(1)))
        if freqs:
            if inten and len(inten) == len(freqs):
                peaks = sorted(zip(freqs, inten), key=lambda t: -t[1])[:5]
                out["ir_peaks"] = [{"freq_cm1": round(fr, 1), "intensity_km_mol": round(it, 1)} for fr, it in peaks if fr > 0]
            out["vibrational_frequencies_cm1"] = [round(fr, 1) for fr in freqs[:10]]
        exc = []
        for m in re.finditer(r"Excited State\s+\d+:.*?(\d+\.\d+)\s*eV\s+(\d+\.\d+)\s*nm\s+f=\s*(\d+\.\d+)", log):
            exc.append({"energy_ev": float(m.group(1)), "wavelength_nm": float(m.group(2)), "oscillator_strength": float(m.group(3))})
        if exc:
            out["excited_states"] = sorted(exc, key=lambda e: -e["oscillator_strength"])[:5]
        return out

    def _deterministic_gaussian_calc(self, step: Optional[ExecutionStep], input_data: Any, tool: Optional[ToolDefinition]) -> Optional[Dict[str, Any]]:
        """确定性真实 Gaussian:绕开 LLM-protocol 编排,复用流水线几何(或从分子名生成 3D)→ 写 .gjf →
        委托真实后端(统一真实 API 调用/日志解析/归一化/结论流转)。无法定位分子或后端未配置则返回 None。"""
        if not self.gaussian_api_base_url:
            return None
        payload = input_data if isinstance(input_data, dict) else {}
        route = self._deterministic_gaussian_route(step, input_data)
        coords: Optional[str] = None
        mol_label: Optional[str] = None
        charge_mult: str = "0 1"
        provenance: Dict[str, Any] = {}

        # 0) 先确定"本步应算的目标分子":用于把几何绑定到目标分子,避免盲目复用 work_dir 里
        #    最新但无关的 .sdf(典型:残留 water_initial.sdf 被下游任意步骤当成目标)。
        target_smiles = self._resolve_smiles_from_context(step, payload, input_data)
        mechanism_context = self._looks_like_multispecies_mechanism_context(
            " ".join(str(x or "") for x in [
                getattr(step, "description", "") if step is not None else "",
                getattr(step, "expected_input", "") if step is not None else "",
                getattr(self, "_run_question", ""),
            ]).lower()
        )

        # 1) 优先复用流水线已产出的几何(smiles2sdf 等的 .sdf),但必须与目标分子一致才复用。
        sdf_path = None
        try:
            sdf_path = self._scan_workdir_for_latest_artifact([".sdf"], step, payload)
        except Exception:
            sdf_path = None
        if not sdf_path or not os.path.exists(sdf_path):
            sdf_path = self._find_latest_geometry_file([".sdf"])
        if sdf_path and os.path.exists(sdf_path):
            reuse_ok = True
            match_state = "unverified"
            if target_smiles:
                if self._sdf_matches_target(sdf_path, target_smiles):
                    match_state = "verified"
                else:
                    reuse_ok = False
                    match_state = "mismatch_rejected"
                    logger.warning(
                        f"[确定性Gaussian] 最新几何 {os.path.basename(sdf_path)} 与本步目标分子 "
                        f"{target_smiles} 不匹配,拒绝复用,改用目标分子几何"
                    )
            elif mechanism_context:
                # 无法确定单一目标分子 + 机理/多组分上下文:复用任意几何会把无关分子伪装成机理证据 → 拒绝。
                reuse_ok = False
                match_state = "ambiguous_target_rejected"
                logger.warning(
                    f"[确定性Gaussian] 机理/多组分步骤无法确定单一目标分子,拒绝盲目复用 "
                    f"{os.path.basename(sdf_path)}(避免伪造机理证据)"
                )
            if reuse_ok:
                coords, charge_mult = self._coords_from_sdf(sdf_path)
                if coords:
                    mol_label = f"sdf:{os.path.basename(sdf_path)}"
                    provenance = {
                        "geometry_source": "run_artifact",
                        "geometry_path": os.path.abspath(sdf_path),
                        "mol_label": mol_label,
                        "geometry_match": match_state,
                        "target_smiles": target_smiles or None,
                        **self._sdf_metadata(sdf_path),
                    }
                    logger.info(f"[确定性Gaussian] 复用流水线几何 {sdf_path}  (匹配={match_state}, 电荷/多重度 {charge_mult})")

        # 2) 没有可复用几何 → 从目标 SMILES(上下文/常见分子名)RDKit 生成 3D。
        #    机理/多组分步骤解析不出单一目标 SMILES 时 target_smiles 为空 → 诚实返回 None,不再兜底成随机分子。
        if not coords:
            smiles_list = target_smiles or self._resolve_smiles_from_context(step, payload, input_data)
            if not smiles_list:
                return None
            smiles = smiles_list[0]
            try:
                from rdkit import Chem  # type: ignore
                from rdkit.Chem import AllChem  # type: ignore
                mol = Chem.MolFromSmiles(smiles)
                if mol is None:
                    return None
                mol = Chem.AddHs(mol)
                if AllChem.EmbedMolecule(mol, randomSeed=42) != 0:
                    AllChem.EmbedMolecule(mol, useRandomCoords=True)
                try:
                    AllChem.MMFFOptimizeMolecule(mol)
                except Exception:
                    pass
                conf = mol.GetConformer()
                coords = "\n".join(
                    f"{a.GetSymbol():<2} {conf.GetAtomPosition(a.GetIdx()).x:>14.8f} "
                    f"{conf.GetAtomPosition(a.GetIdx()).y:>14.8f} {conf.GetAtomPosition(a.GetIdx()).z:>14.8f}"
                    for a in mol.GetAtoms())
                mol_label = smiles
                charge_mult = self._charge_mult_str(mol)
                provenance = {
                    "geometry_source": "context_smiles",
                    "smiles": smiles,
                    "mol_label": mol_label,
                    "geometry_match": "generated_from_target",
                    "atom_count": int(mol.GetNumAtoms()),
                    "elements": sorted({a.GetSymbol() for a in mol.GetAtoms()}),
                    "formal_charge": int(Chem.GetFormalCharge(mol)),
                }
            except Exception as exc:
                fallback = self._builtin_coords_from_smiles(smiles)
                if fallback is None:
                    logger.warning(f"[确定性Gaussian] RDKit 几何生成失败 {smiles}: {exc}")
                    return None
                coords = str(fallback["coords"])
                mol_label = smiles
                charge_mult = str(fallback.get("charge_mult") or "0 1")
                provenance = {
                    "geometry_source": "context_smiles",
                    "geometry_builder": "builtin_smiles_template",
                    "smiles": smiles,
                    "mol_label": mol_label,
                    "geometry_match": "generated_from_builtin_template",
                    "atom_count": int(fallback.get("atom_count") or 0),
                    "elements": list(fallback.get("elements") or []),
                    "formal_charge": int(fallback.get("formal_charge") or 0),
                }
                logger.warning(
                    f"[确定性Gaussian] RDKit 几何生成失败 {smiles}: {exc}; "
                    f"改用内置几何模板"
                )
        gjf = f"%mem=4GB\n%nprocshared=4\n{route}\n\n{mol_label} (deterministic)\n\n{charge_mult}\n{coords}\n\n"
        # 同一 run 内按 (几何+方法) 缓存真实结果:拦截可能命中多步(生成/执行),避免对同一分子重复真跑 Gaussian。
        import hashlib
        cache_key = hashlib.md5(gjf.encode("utf-8")).hexdigest()
        if not hasattr(self, "_det_gaussian_cache"):
            self._det_gaussian_cache = {}
        if cache_key in self._det_gaussian_cache:
            logger.info(f"[确定性Gaussian] 命中缓存,复用同几何/方法的真实结果 {mol_label}(免重复真跑)")
            return self._det_gaussian_cache[cache_key]
        # 写 .gjf 落盘,委托真实后端(_execute_gaussian_job_backend → _run_gaussian_via_api):
        # 复用生产的真实 API 调用 + 日志解析 + 归一化,结果 execution_mode=gaussian_job + parsed_results,
        # 能正确流入反思/结论(避免自造 dict 导致 HOMO-LUMO 进不了 final_conclusion)。
        import tempfile
        import uuid
        # 确定性 Gaussian 真跑的落盘目录。server 启动子进程时把 ARCHE_DETERMINISTIC_DIR 收敛到
        # 本次 run 的 work_dir 内,使 .log 与 work_dir 同寿命、且落在 harvest 的唯一受控根下
        # (不再泄漏到跨 run 共享的 /tmp/arche_deterministic);未设置时回退到旧的共享默认值。
        gdir = os.environ.get("ARCHE_DETERMINISTIC_DIR") or os.path.join(tempfile.gettempdir(), "arche_deterministic")
        os.makedirs(gdir, exist_ok=True)
        gjf_path = os.path.join(gdir, f"det_{uuid.uuid4().hex[:8]}.gjf")
        with open(gjf_path, "w", encoding="utf-8") as f:
            f.write(gjf)
        logger.info(f"[确定性Gaussian] 提交真实作业 {mol_label}  route={route}  (委托真实后端,统一解析/结论流转)")
        result = self.execute_gaussian_related_tool(
            "run_gaussian_deterministic", {"gjf_path": gjf_path}, step=step, tool=tool
        )
        if isinstance(result, dict):
            provenance.update({
                "route": route,
                "gjf_path": gjf_path,
                "step_id": getattr(step, "step_id", None) if step is not None else None,
                "step_number": getattr(step, "step_number", None) if step is not None else None,
            })
            result["deterministic_provenance"] = provenance
            pr = result.get("parsed_results")
            if isinstance(pr, dict):
                pr.setdefault("provenance", provenance)
        # 增补光谱量(IR 峰 / 激发态):从刚跑的 log 解析,合并进 parsed_results 供结论 surface IR/UV-Vis 真实结果
        try:
            spec = self._parse_spectroscopy_from_log(os.path.splitext(gjf_path)[0] + ".log")
            if spec and isinstance(result, dict):
                pr = result.get("parsed_results")
                if isinstance(pr, dict):
                    pr.update(spec)
                else:
                    result["parsed_results"] = dict(spec)
                result["spectroscopy"] = spec
        except Exception as exc:
            logger.warning(f"[确定性Gaussian] 光谱解析失败: {exc}")
        self._det_gaussian_cache[cache_key] = result
        return result

    def execute_gaussian_related_tool(self, tool_name: str, input_data: Any, step: Optional[ExecutionStep] = None, tool: Optional[ToolDefinition] = None) -> Any:
        """执行 Gaussian 相关工具:一律走真实后端;无可跑 .gjf / 真实后端不可用时诚实抛错。
        已彻底移除 replay 模式与所有伪造(scf_energy=-123.456789 那套)输出。"""
        if self._should_use_real_gaussian_backend(tool_name, input_data, step=step):
            return self._execute_gaussian_job_backend(tool_name, input_data, step=step)
        raise RuntimeError(
            f"Gaussian 工具 '{tool_name}' 无法真实执行(无可解析的 .gjf 或真实后端不可用),"
            f"已移除所有 mock/replay 回退,拒绝伪造结果。"
        )

    def resolve_step_inputs(self, step: ExecutionStep, previous_output: Any, previous_format: str) -> Tuple[Any, str, List[ExecutionStep]]:
        """
        解析步骤输入,处理格式兼容性

        返回:
            (input_data, input_format, conversion_steps)
        """
        conversion_steps = []
        input_data = None
        input_format = None

        # 确定预期输入格式
        expected_input_format = step.tool.input_format if step.tool else None

        # 如果步骤指定了输入,使用指定输入
        if step.expected_input and step.expected_input.lower() != "none":
            input_data = step.expected_input
            # 尝试推断格式
            input_format = self._infer_format_from_data(input_data)
        elif previous_output is not None:
            input_data = previous_output
            input_format = previous_format

        # 检查格式兼容性
        if input_format and expected_input_format and input_format != expected_input_format:
            compatible, conversion_plan = self.check_input_output_compatibility(
                input_format, expected_input_format, input_data
            )
            if not compatible:
                # 记录格式不匹配错误
                step.error_info = {
                    "category": ErrorCategory.FORMAT_MISMATCH.value,
                    "message": f"输入格式不匹配: {input_format} -> {expected_input_format}",
                    "repair_suggestion": f"需要格式转换,但未找到转换工具"
                }
                # 创建转换子步骤(如果可能)
                if conversion_plan:
                    conversion_step = ExecutionStep(
                        step_number=step.step_number * 100,  # 子步骤编号
                        description=f"格式转换: {input_format} -> {expected_input_format}",
                        tool_name=conversion_plan.get("converter_tool"),
                        status=StepStatus.PENDING.value
                    )
                    conversion_steps.append(conversion_step)

        return input_data, input_format, conversion_steps

    def check_input_output_compatibility(self, source_format: str, target_format: str, data: Any) -> Tuple[bool, Dict[str, Any]]:
        """
        检查输入输出格式兼容性

        返回:
            (是否兼容, 转换计划)
        """
        if source_format == target_format:
            return True, {}

        converter_key = (source_format, target_format)
        if converter_key in self.format_converters:
            converter_tool = self.format_converters[converter_key]
            if converter_tool in self.tools:
                return False, {"converter_tool": converter_tool, "converter_key": converter_key}
            logger.warning(f"格式转换工具 '{converter_tool}' 不存在")
            return False, {"converter_tool": None, "converter_key": converter_key, "note": "有转换映射但无可用转换工具"}

        if self.requires_format_conversion(source_format, target_format):
            return False, {"converter_tool": None, "converter_key": None, "note": "格式不兼容且无转换路径"}

        return False, {"converter_tool": None, "converter_key": None, "note": "格式不同且未证明兼容"}

    def requires_format_conversion(self, source_format: str, target_format: str) -> bool:
        """判断是否需要格式转换。"""
        return source_format != target_format

    def parse_gaussian_output(self, output: Any) -> Dict[str, Any]:
        """
        解析Gaussian输出,返回归一化schema
        """
        raw_result = {
            "energy": None,
            "frequencies": None,
            "convergence": None,
            "irc_path": None,
            "has_imaginary_frequencies": None,
            "imaginary_frequencies_count": None,
            "scf_converged": None,
            "normal_termination": None,
            "E_HOMO": None,
            "E_LUMO": None,
            "HOMO_LUMO_gap": None,
            "job_type": "unknown",
            "raw_output_preview": str(output)[:500] if output else None
        }

        if isinstance(output, dict):
            raw_result.update(output)
        else:
            output_str = str(output)
            output_lower = output_str.lower()

            stripped = output_str.lstrip()
            if stripped.startswith("{"):
                try:
                    payload = json.loads(stripped)
                    if isinstance(payload, dict):
                        raw_result.update(payload)
                        return self._normalize_gaussian_result(raw_result)
                except Exception:
                    pass

            energy_pattern = r"SCF Done.*?=\s*([-+]?\d*\.\d+)"
            match = re.search(energy_pattern, output_str, re.IGNORECASE)
            if match:
                try:
                    raw_result["energy"] = float(match.group(1))
                except Exception:
                    pass

            freq_pattern = r"Frequencies.*?--.*?([-+]?\d*\.\d+)"
            freq_matches = re.findall(freq_pattern, output_str, re.IGNORECASE)
            if freq_matches:
                try:
                    frequencies = [float(f) for f in freq_matches[:10]]
                    raw_result["frequencies"] = frequencies
                    imaginary = [f for f in frequencies if f < 0]
                    raw_result["has_imaginary_frequencies"] = len(imaginary) > 0
                    raw_result["imaginary_frequencies_count"] = len(imaginary)
                except Exception:
                    pass

            # 解析分子轨道本征值,计算 HOMO/LUMO/能隙(单位Hartree)
            # Gaussian 日志格式:
            #   " Alpha  occ. eigenvalues --   -10.18  -10.18   -0.86  -0.64"  (升序,可多行)
            #   " Alpha virt. eigenvalues --     0.04   0.08   0.12"           (升序,可多行)
            try:
                float_token = r"[-+]?\d+\.\d+"
                occ_lines = re.findall(
                    rf"occ\.\s*eigenvalues\s*--\s*((?:\s*{float_token})+)", output_str, re.IGNORECASE
                )
                virt_lines = re.findall(
                    rf"virt\.\s*eigenvalues\s*--\s*((?:\s*{float_token})+)", output_str, re.IGNORECASE
                )
                occ_values = [float(v) for line in occ_lines for v in re.findall(float_token, line)]
                virt_values = [float(v) for line in virt_lines for v in re.findall(float_token, line)]
                # 仅在数据完整时填充,缺失则保持 None(不杜撰)
                if occ_values:
                    raw_result["E_HOMO"] = occ_values[-1]  # 最后一个占据轨道
                if virt_values:
                    raw_result["E_LUMO"] = virt_values[0]  # 第一个虚轨道
                if occ_values and virt_values:
                    raw_result["HOMO_LUMO_gap"] = virt_values[0] - occ_values[-1]
            except Exception:
                # 解析失败时保持 None,不崩溃也不伪造数值
                pass

            if "convergence achieved" in output_lower:
                raw_result["convergence"] = "achieved"
            if "scf converged" in output_lower:
                raw_result["scf_converged"] = True
            if "normal termination" in output_lower:
                raw_result["normal_termination"] = True

            if "irc" in output_lower:
                raw_result["job_type"] = "irc"
            elif "transition state" in output_lower or re.search(r"\bts\b", output_lower):
                raw_result["job_type"] = "ts"
            elif "single point" in output_lower or re.search(r"\bsp\b", output_lower):
                raw_result["job_type"] = "sp"
            elif "frequency" in output_lower or re.search(r"\bfreq\b", output_lower):
                raw_result["job_type"] = "freq"
            elif "optimization" in output_lower or re.search(r"\bopt\b", output_lower):
                raw_result["job_type"] = "opt"

        return self._normalize_gaussian_result(raw_result)

    def extract_energy_info(self, parsed_output: Dict[str, Any]) -> Dict[str, Any]:
        """提取能量信息"""
        return {
            "energy": parsed_output.get("scf_energy"),
            "energy_units": "Hartree",
            "relative_energy": None,
            "zero_point_energy": parsed_output.get("zero_point_energy")
        }

    def extract_frequency_info(self, parsed_output: Dict[str, Any]) -> Dict[str, Any]:
        """提取频率信息"""
        freqs = parsed_output.get("frequencies", []) or []
        imag_freqs = parsed_output.get("imag_freqs", []) or [f for f in freqs if isinstance(f, (int, float)) and f < 0]
        imag_count = parsed_output.get("n_imag_freq")
        if imag_count is None:
            imag_count = len(imag_freqs)
        return {
            "frequencies": freqs,
            "imaginary_frequencies": imag_freqs,
            "imaginary_count": imag_count,
            "has_imaginary": (imag_count or 0) > 0
        }

    def extract_convergence_info(self, parsed_output: Dict[str, Any]) -> Dict[str, Any]:
        """提取收敛信息"""
        job_type = parsed_output.get("job_type")
        normal_termination = parsed_output.get("normal_termination") is True
        geometry_converged = parsed_output.get("converged")
        scf_converged = parsed_output.get("scf_converged")

        if geometry_converged is None and job_type in {"opt", "ts"}:
            geometry_converged = normal_termination
        if scf_converged is None and parsed_output.get("scf_energy") is not None:
            scf_converged = True
        if scf_converged is None and normal_termination and job_type == "sp":
            scf_converged = True

        return {
            "converged": geometry_converged is True,
            "scf_converged": scf_converged is True,
            "geometry_converged": geometry_converged is True,
            "normal_termination": normal_termination,
        }

    def extract_irc_info(self, parsed_output: Dict[str, Any]) -> Dict[str, Any]:
        """提取IRC信息"""
        return {
            "irc_path": parsed_output.get("irc_path"),
            "irc_points": None,
            "irc_energies": None,
            "irc_converged": parsed_output.get("irc_verified")
        }

    def determine_validation_type(self, step: ExecutionStep, parsed_output: Dict[str, Any]) -> str:
        """
        根据步骤信息和解析结果确定验证类型

        返回:
            "opt" | "ts" | "irc" | "sp" | "freq"
        """
        parsed_job_type = str((parsed_output or {}).get("job_type") or "unknown").lower()
        if parsed_job_type in {"opt", "ts", "irc", "sp", "freq"}:
            return parsed_job_type

        inferred = self._detect_step_job_type(step)
        if inferred in {"opt", "ts", "irc", "sp", "freq"}:
            return inferred
        return "opt"

    def select_validator(self, step: ExecutionStep, parsed_output: Dict[str, Any]) -> Tuple[callable, str]:
        """
        选择适当的验证函数

        返回:
            (验证函数, 验证类型)
        """
        validation_type = self.determine_validation_type(step, parsed_output)
        validator_map = {
            "opt": self.validate_optimization_result,
            "ts": self.validate_ts_result,
            "irc": self.validate_irc_result,
            "sp": self.validate_single_point_result,
            "freq": self.validate_frequency_result,
        }
        return validator_map.get(validation_type, self.validate_optimization_result), validation_type

    def validate_optimization_result(self, parsed_output: Dict[str, Any]) -> Dict[str, Any]:
        """验证优化结果"""
        convergence = self.extract_convergence_info(parsed_output)

        if not convergence.get("converged", False):
            return {
                "status": ValidationStatus.FAIL.value,
                "details": "几何优化未收敛",
                "checks": {"convergence": False}
            }

        return {
            "status": ValidationStatus.PASS.value,
            "details": "几何优化收敛",
            "checks": {"convergence": True}
        }

    def validate_ts_result(self, parsed_output: Dict[str, Any]) -> Dict[str, Any]:
        """验证过渡态结果"""
        # 检查收敛
        convergence = self.extract_convergence_info(parsed_output)
        if not convergence.get("converged", False):
            return {
                "status": ValidationStatus.FAIL.value,
                "details": "过渡态优化未收敛",
                "checks": {"convergence": False}
            }

        # 检查虚频
        freq_info = self.extract_frequency_info(parsed_output)
        imaginary_count = freq_info.get("imaginary_count", 0)

        if imaginary_count == 0:
            return {
                "status": ValidationStatus.FAIL.value,
                "details": "过渡态应有且仅有一个虚频,但未检测到虚频",
                "checks": {"convergence": True, "exactly_one_imaginary": False}
            }
        elif imaginary_count > 1:
            return {
                "status": ValidationStatus.WARNING.value,
                "details": f"过渡态应有且仅有一个虚频,但检测到 {imaginary_count} 个虚频",
                "checks": {"convergence": True, "exactly_one_imaginary": False}
            }
        else:
            # 只有一个虚频
            return {
                "status": ValidationStatus.PASS.value,
                "details": "过渡态收敛且有一个虚频",
                "checks": {"convergence": True, "exactly_one_imaginary": True}
            }

    def validate_irc_result(self, parsed_output: Dict[str, Any]) -> Dict[str, Any]:
        """验证IRC结果"""
        irc_info = self.extract_irc_info(parsed_output)
        convergence = self.extract_convergence_info(parsed_output)

        if not convergence.get("normal_termination", False):
            return {
                "status": ValidationStatus.FAIL.value,
                "details": "IRC计算未正常终止",
                "checks": {"normal_termination": False}
            }

        if irc_info.get("irc_converged") is False:
            return {
                "status": ValidationStatus.FAIL.value,
                "details": "IRC路径验证失败",
                "checks": {"normal_termination": True, "irc_verified": False}
            }

        return {
            "status": ValidationStatus.PASS.value,
            "details": "IRC计算正常终止",
            "checks": {"normal_termination": True, "irc_verified": irc_info.get("irc_converged")}
        }

    def validate_frequency_result(self, parsed_output: Dict[str, Any]) -> Dict[str, Any]:
        """验证频率计算结果(不等同于几何优化验证)。"""
        convergence = self.extract_convergence_info(parsed_output)
        freq_info = self.extract_frequency_info(parsed_output)

        if not convergence.get("normal_termination", False):
            return {
                "status": ValidationStatus.FAIL.value,
                "details": "频率计算未正常终止",
                "checks": {"normal_termination": False, "frequency_data_present": False}
            }

        freqs = freq_info.get("frequencies") or []
        if not freqs:
            return {
                "status": ValidationStatus.UNKNOWN.value,
                "details": "计算正常终止,但未提取到可用频率数据",
                "checks": {"normal_termination": True, "frequency_data_present": False, "usable_for_interpretation": False}
            }

        return {
            "status": ValidationStatus.PASS.value,
            "details": "频率计算正常终止且提取到频率数据",
            "checks": {
                "normal_termination": True,
                "frequency_data_present": True,
                "usable_for_interpretation": True,
                "imaginary_count": freq_info.get("imaginary_count")
            }
        }

    def validate_single_point_result(self, parsed_output: Dict[str, Any]) -> Dict[str, Any]:
        """验证单点能结果"""
        energy_info = self.extract_energy_info(parsed_output)
        convergence = self.extract_convergence_info(parsed_output)

        if not convergence.get("scf_converged", False):
            return {
                "status": ValidationStatus.FAIL.value,
                "details": "SCF未收敛",
                "checks": {"scf_convergence": False}
            }

        if energy_info.get("energy") is None:
            return {
                "status": ValidationStatus.WARNING.value,
                "details": "未提取到能量值",
                "checks": {"scf_convergence": True, "energy_extracted": False}
            }

        return {
            "status": ValidationStatus.PASS.value,
            "details": "单点能计算成功",
            "checks": {"scf_convergence": True, "energy_extracted": True}
        }

    def classify_execution_error(self, error: Exception) -> Tuple[ErrorCategory, str]:
        """
        分类执行错误并生成修复建议
        """
        error_msg = str(error).lower()

        if "not found" in error_msg or "no module" in error_msg:
            return ErrorCategory.TOOL_NOT_FOUND, "检查工具路径和模块导入"
        elif "missing" in error_msg or "required" in error_msg:
            return ErrorCategory.INPUT_MISSING, "提供必要的输入数据"
        elif "format" in error_msg or "conversion" in error_msg:
            return ErrorCategory.FORMAT_MISMATCH, "检查输入输出格式,可能需要格式转换"
        elif "convergence" in error_msg or "not converged" in error_msg:
            return ErrorCategory.GAUSSIAN_NONCONVERGENCE, "调整计算参数(如收敛阈值、步数)或初始构型"
        elif "scf" in error_msg and "fail" in error_msg:
            return ErrorCategory.GAUSSIAN_SCF_FAILURE, "调整SCF收敛算法或初始猜测"
        elif "transition state" in error_msg or "ts" in error_msg:
            return ErrorCategory.TS_INVALID, "检查过渡态初始猜测,确保有且仅有一个虚频"
        elif "irc" in error_msg:
            return ErrorCategory.IRC_FAILURE, "调整IRC计算参数(步长、方向)"
        elif "parse" in error_msg or "parsing" in error_msg:
            return ErrorCategory.PARSING_FAILURE, "检查输出文件格式,可能需要更新解析器"
        else:
            return ErrorCategory.UNKNOWN_ERROR, "查看详细错误日志,联系开发者"

    def propose_repair_action(self, error_category: ErrorCategory, context: Dict[str, Any] = None) -> str:
        """
        根据错误分类提出修复建议
        """
        suggestions = {
            ErrorCategory.TOOL_NOT_FOUND: "安装或配置缺失的工具/模块",
            ErrorCategory.INPUT_MISSING: "检查输入数据是否完整",
            ErrorCategory.FORMAT_MISMATCH: "添加格式转换步骤或调整工具顺序",
            ErrorCategory.GAUSSIAN_NONCONVERGENCE: "增加最大迭代步数或放松收敛标准",
            ErrorCategory.GAUSSIAN_SCF_FAILURE: "尝试不同的SCF算法(如QC、DIIS)或使用更优的初始猜测",
            ErrorCategory.TS_INVALID: "重新生成过渡态初始猜测或使用不同的TS搜索方法",
            ErrorCategory.IRC_FAILURE: "调整IRC步长或尝试双向IRC",
            ErrorCategory.PARSING_FAILURE: "检查输出文件格式,更新解析器或手动检查",
            ErrorCategory.UNKNOWN_ERROR: "查看系统日志,可能需要人工干预"
        }
        return suggestions.get(error_category, "未知错误,请查看详细日志")

    def _infer_format_from_data(self, data: Any) -> Optional[str]:
        """从数据推断格式"""
        if isinstance(data, str):
            lowered = data.lower()
            if ".gjf" in lowered:
                return "gjf"
            if ".xyz" in lowered:
                return "xyz"
            if ".sdf" in lowered:
                return "sdf"
            if ".log" in lowered or ".out" in lowered:
                return "log"
            if "smiles" in lowered:
                return "smiles"
        if isinstance(data, dict):
            if self._extract_paths_with_suffix(data, ".gjf"):
                return "gjf"
            if self._extract_paths_with_suffix(data, ".xyz"):
                return "xyz"
            if self._extract_paths_with_suffix(data, ".sdf"):
                return "sdf"
            if self._extract_paths_with_suffix(data, ".log") or self._extract_paths_with_suffix(data, ".out"):
                return "log"
        return None

    # ==================== 工作流执行 ====================

    def execute_workflow(self,
                        protocol: Dict,
                        strategy_name: Optional[str] = None) -> ExecutionResult:
        """
        执行工作流(增强版,使用结构化执行方法)
        """
        # 记录策略名/问题,供确定性 Gaussian 在步骤描述无分子名时从中识别分子(如 "...for Benzene")。
        self._current_strategy_name = strategy_name or ""
        self._recent_gaussian_logs = []  # 跨步 .log 传递：每个工作流独立累积，避免串到别的工作流
        if isinstance(protocol, dict):
            for qk in ("Question", "question", "Objective", "objective", "Task", "task"):
                if protocol.get(qk):
                    self._run_question = str(protocol.get(qk))
                    break
        if "Steps" not in protocol:
            logger.error("协议中没有Steps字段")
            return ExecutionResult(
                strategy_name=strategy_name or "Unknown",
                total_steps=0,
                successful_steps=0,
                failed_steps=0,
                success_rate=0.0,
                total_duration=0.0,
                steps=[],
                issues=["协议中没有Steps字段"],
                final_output=None
            )

        steps_data = protocol["Steps"]
        if not steps_data:
            logger.warning("协议中的Steps为空")
            return ExecutionResult(
                strategy_name=strategy_name or "Unknown",
                total_steps=0,
                successful_steps=0,
                failed_steps=0,
                success_rate=1.0,
                total_duration=0.0,
                steps=[],
                issues=["协议中的Steps为空"],
                final_output=None
            )

        strategy_name = strategy_name or protocol.get("strategy_name", "Unknown_Strategy")
        logger.info(f"开始执行策略工作流: {strategy_name} ({len(steps_data)} 个步骤)")

        execution_steps: List[ExecutionStep] = []
        intermediate_artifacts: List[Dict[str, Any]] = []
        workflow_notes: List[str] = []
        previous_output = None
        previous_format = None
        total_retry_count = 0
        recoverable_issue_count = 0
        unrecoverable_issue_count = 0
        self._latest_gaussian_route_section = None

        total_start_time = time.time()

        for step_dict in steps_data:
            step_number = step_dict.get("Step_number", 0)
            description = step_dict.get("Description", "")
            specified_tool = step_dict.get("Tool", "")
            expected_input = step_dict.get("Input", "")
            expected_output = step_dict.get("Output", "")

            calculation_context = step_dict.get("calculation_context") if isinstance(step_dict.get("calculation_context"), dict) else {}
            gaussian_review = step_dict.get("gaussian_review") if isinstance(step_dict.get("gaussian_review"), dict) else {}
            scientific_context = step_dict.get("scientific_context")
            if scientific_context is None and calculation_context:
                scientific_context = calculation_context

            route_section = self._select_route_section(step_dict, calculation_context, gaussian_review)
            job_type = step_dict.get("job_type") or calculation_context.get("job_type")

            exec_step = ExecutionStep(
                step_number=step_number,
                description=description,
                tool_name=specified_tool,
                expected_input=expected_input,
                expected_output=expected_output,
                status=StepStatus.PENDING.value,
                step_id=step_dict.get("step_id"),
                step_name=step_dict.get("step_name"),
                scientific_context=scientific_context,
                route_section=route_section,
                job_type=job_type,
                working_directory=step_dict.get("working_directory"),
                artifacts=step_dict.get("artifacts", []) if isinstance(step_dict.get("artifacts", []), list) else [],
            )
            execution_steps.append(exec_step)

            logger.info(f"步骤 {step_number}: {description[:50]}...")

            tool = None
            manual_specified_tool = str(specified_tool or "").strip().lower()
            if manual_specified_tool in {"", "none", "none (manual input)", "manual input", "manual analysis", "none (manual analysis)"} or (
                "manual" in manual_specified_tool
            ):
                pass
            elif specified_tool in self.tools:
                tool = self.tools[specified_tool]
                exec_step.tool = tool
            else:
                tool = self.find_tool(description)
                if tool:
                    exec_step.tool = tool
                    exec_step.tool_name = tool.tool_name
                else:
                    exec_step.status = StepStatus.FAILED.value
                    exec_step.error = f"未找到合适的工具: {specified_tool}"
                    exec_step.error_info = {
                        "category": ErrorCategory.TOOL_NOT_FOUND.value,
                        "message": exec_step.error,
                        "repair_suggestion": self.propose_repair_action(ErrorCategory.TOOL_NOT_FOUND)
                    }
                    unrecoverable_issue_count += 1
                    exec_step.end_time = time.time()
                    logger.error(f"❌ 步骤 {step_number} 执行失败: {exec_step.error}")
                    continue

            input_data, input_format, conversion_steps = self.resolve_step_inputs(
                exec_step, previous_output, previous_format
            )

            conversion_failed = False
            for conv_step in conversion_steps:
                logger.warning(f"⚠️  插入格式转换步骤: {conv_step.description}")
                conv_step = self.execute_tool_step(conv_step, input_data)
                if conv_step.status == StepStatus.SUCCESS.value:
                    input_data = conv_step.raw_output
                    input_format = conv_step.tool.output_format if conv_step.tool else input_format
                    intermediate_artifacts.extend(self._build_step_artifacts(exec_step, [conv_step]))
                else:
                    exec_step.status = StepStatus.FAILED.value
                    exec_step.error = f"格式转换失败: {conv_step.error}"
                    exec_step.error_info = conv_step.error_info or {
                        "category": ErrorCategory.FORMAT_MISMATCH.value,
                        "message": exec_step.error,
                        "repair_suggestion": "补充或修复格式转换工具"
                    }
                    exec_step.error_info["category"] = self._normalize_error_category(exec_step.error_info.get("category"))
                    unrecoverable_issue_count += 1
                    exec_step.end_time = time.time()
                    logger.error(f"❌ 步骤 {step_number} 格式转换失败")
                    conversion_failed = True
                    break

            if conversion_failed:
                continue

            attempts = 0
            max_attempts = self.max_step_retries + 1
            while attempts < max_attempts:
                self.current_step_retries[exec_step.step_id] = attempts
                attempts += 1
                exec_step = self.execute_tool_step(exec_step, input_data, max_retries=self.max_step_retries)
                if exec_step.status == StepStatus.SUCCESS.value:
                    break
                if exec_step.status != StepStatus.RETRYING.value:
                    break
                if attempts < max_attempts:
                    total_retry_count += 1
                    recoverable_issue_count += 1
                    logger.warning(f"🔄 步骤 {step_number} 重试尝试 {attempts + 1}/{max_attempts}")

            if not exec_step.validation:
                exec_step.validation = {"status": ValidationStatus.UNKNOWN.value, "details": "未执行验证"}
            exec_step.validation["attempts"] = attempts
            exec_step.validation["retries_used"] = max(0, attempts - 1)

            raw_output_dict = exec_step.raw_output if isinstance(exec_step.raw_output, dict) else None
            pending_gaussian_status = None
            if raw_output_dict and raw_output_dict.get("execution_mode") == "gaussian_job":
                current_status = str(raw_output_dict.get("status") or "").strip().lower()
                if current_status in {"prepared", "submitted", "queued", "running"}:
                    pending_gaussian_status = current_status

            if pending_gaussian_status:
                previous_output = exec_step.raw_output
                intermediate_artifacts.extend(self._build_step_artifacts(exec_step))
                pause_msg = f"步骤 {step_number}: Workflow paused: Gaussian job still {pending_gaussian_status}"
                workflow_notes.append(pause_msg)
                logger.info(f"⏳ {pause_msg}")
                break

            if exec_step.status == StepStatus.SUCCESS.value:
                previous_output = exec_step.raw_output
                previous_format = exec_step.tool.output_format if exec_step.tool else None
                intermediate_artifacts.extend(self._build_step_artifacts(exec_step))
                logger.info(f"✅ 步骤 {step_number} 执行成功 ({exec_step.duration:.2f}秒)")
            else:
                if exec_step.error_info:
                    exec_step.error_info["category"] = self._normalize_error_category(exec_step.error_info.get("category"))
                unrecoverable_issue_count += 1
                logger.error(f"❌ 步骤 {step_number} 执行失败: {exec_step.error}")

        total_end_time = time.time()
        total_duration = total_end_time - total_start_time

        successful_steps = sum(1 for s in execution_steps if s.status == StepStatus.SUCCESS.value)
        failed_steps = sum(1 for s in execution_steps if s.status == StepStatus.FAILED.value)
        success_rate = successful_steps / len(execution_steps) if execution_steps else 0.0

        issues: List[str] = []
        for step in execution_steps:
            if step.status == StepStatus.FAILED.value:
                if step.error_info:
                    step.error_info["category"] = self._normalize_error_category(step.error_info.get("category"))
                    issues.append(f"步骤 {step.step_number}: {step.error_info.get('category')} - {step.error_info.get('message')}")
                elif step.error:
                    issues.append(f"步骤 {step.step_number}: {step.error}")
        if workflow_notes:
            issues.extend(workflow_notes)

        final_output = None
        for step in reversed(execution_steps):
            if step.status == StepStatus.SUCCESS.value and step.raw_output:
                final_output = step.raw_output
                break
            if step.status == StepStatus.SUCCESS.value and step.actual_output:
                final_output = step.actual_output
                break

        result = ExecutionResult(
            strategy_name=strategy_name,
            total_steps=len(execution_steps),
            successful_steps=successful_steps,
            failed_steps=failed_steps,
            success_rate=success_rate,
            total_duration=total_duration,
            steps=execution_steps,
            issues=issues,
            final_output=final_output
        )

        # Aggregate expert analysis across steps
        expert_aggregation = self._aggregate_expert_analysis(execution_steps)
        result.expert_analysis_summary = expert_aggregation
        if expert_aggregation.get("has_expert_analysis"):
            result.expert_confidence = expert_aggregation.get("overall_confidence")
            result.recommended_next_action = "; ".join(expert_aggregation.get("recommended_next_actions", []))

        validation_overview = {
            ValidationStatus.PASS.value: 0,
            ValidationStatus.FAIL.value: 0,
            ValidationStatus.WARNING.value: 0,
            ValidationStatus.UNKNOWN.value: 0,
            "critical_total": 0,
            "critical_failed": 0,
        }

        for step in execution_steps:
            vstatus = (step.validation or {}).get("status", ValidationStatus.UNKNOWN.value)
            if vstatus not in {ValidationStatus.PASS.value, ValidationStatus.FAIL.value, ValidationStatus.WARNING.value, ValidationStatus.UNKNOWN.value}:
                vstatus = ValidationStatus.UNKNOWN.value
            validation_overview[vstatus] += 1

            job_type = "unknown"
            if isinstance(step.parsed_results, dict):
                job_type = step.parsed_results.get("job_type", "unknown")
            if job_type == "unknown":
                job_type = self._detect_step_job_type(step)

            if job_type in self.validation_critical_types:
                validation_overview["critical_total"] += 1
                if vstatus == ValidationStatus.FAIL.value:
                    validation_overview["critical_failed"] += 1

        workflow_outcome = self._classify_workflow_outcome(
            total_steps=len(execution_steps),
            successful_steps=successful_steps,
            failed_steps=failed_steps,
            validation_overview=validation_overview,
            has_final_output=final_output is not None,
        )

        # 运行时状态(执行层)与科学结果(workflow_outcome)分离
        if successful_steps == len(execution_steps):
            runtime_status = StepStatus.SUCCESS.value
        elif failed_steps == len(execution_steps):
            runtime_status = StepStatus.FAILED.value
        elif successful_steps > 0:
            runtime_status = "partial_success"
        else:
            runtime_status = StepStatus.FAILED.value

        result.overall_status = runtime_status
        result.workflow_outcome = workflow_outcome
        result.intermediate_artifacts = intermediate_artifacts

        result.summary = result.summary or {}
        result.summary.update({
            "overall_status": runtime_status,
            "workflow_outcome": workflow_outcome,
            "retry_count": total_retry_count,
            "recoverable_issue_count": recoverable_issue_count,
            "unrecoverable_issue_count": unrecoverable_issue_count,
            "validation_overview": validation_overview,
            "final_output_available": final_output is not None,
            "key_scientific_outputs_available": any(
                isinstance(step.parsed_results, dict) and step.parsed_results.get("scf_energy") is not None
                for step in execution_steps if step.status == StepStatus.SUCCESS.value
            ),
            "intermediate_artifacts_count": len(intermediate_artifacts),
        })
        result.metadata = result.metadata or {}
        result.metadata["intermediate_artifacts"] = intermediate_artifacts
        result.metadata["workflow_outcome"] = workflow_outcome

        logger.info(f"策略 '{strategy_name}' 执行完成: {successful_steps}/{len(execution_steps)} 成功 ({success_rate:.2%})")

        self.execution_history.append({
            "strategy_name": strategy_name,
            "timestamp": time.time(),
            "result": asdict(result)
        })

        return result

    def execute_multiple_workflows(self, protocols: List[Dict]) -> Dict[str, Any]:
        """
        执行多个工作流

        参数:
            protocols: 协议列表

        返回:
            执行结果汇总
        """
        logger.info(f"开始执行 {len(protocols)} 个工作流")

        results = []
        total_start_time = time.time()

        for i, protocol in enumerate(protocols):
            strategy_name = protocol.get("strategy_name", f"Strategy_{i+1}")
            logger.info(f"执行工作流 {i+1}/{len(protocols)}: {strategy_name}")

            result = self.execute_workflow(protocol, strategy_name)
            results.append(asdict(result))

        total_end_time = time.time()
        total_duration = total_end_time - total_start_time

        # 计算总体统计
        total_steps = sum(r["total_steps"] for r in results)
        total_successful = sum(r["successful_steps"] for r in results)
        overall_success_rate = total_successful / total_steps if total_steps > 0 else 0.0

        summary = {
            "total_workflows": len(protocols),
            "total_steps": total_steps,
            "successful_steps": total_successful,
            "failed_steps": total_steps - total_successful,
            "overall_success_rate": overall_success_rate,
            "total_duration": total_duration,
            "results": results,
            "timestamp": time.time()
        }

        logger.info(f"所有工作流执行完成: {total_successful}/{total_steps} 步骤成功 ({overall_success_rate:.2%})")

        # 保存执行摘要
        self._save_execution_summary(summary)

        return summary

    def _save_execution_summary(self, summary: Dict):
        """保存执行摘要到文件"""
        try:
            output_dir = os.path.join(project_root, "outputs", "execution")
            os.makedirs(output_dir, exist_ok=True)

            timestamp = int(time.time())
            summary_file = os.path.join(output_dir, f"execution_summary_{timestamp}.json")

            with open(summary_file, "w", encoding="utf-8") as f:
                json.dump(summary, f, indent=2, ensure_ascii=False)

            logger.info(f"执行摘要已保存到: {summary_file}")

        except Exception as e:
            logger.error(f"保存执行摘要失败: {e}")

    # ==================== 报告生成 ====================

    def generate_execution_report(self, result: ExecutionResult) -> Dict:
        """生成详细的执行报告"""
        report = {
            "strategy_name": result.strategy_name,
            "summary": {
                "total_steps": result.total_steps,
                "successful_steps": result.successful_steps,
                "failed_steps": result.failed_steps,
                "success_rate": result.success_rate,
                "total_duration": result.total_duration
            },
            "detailed_steps": [
                {
                    "step_number": step.step_number,
                    "description": step.description,
                    "tool": step.tool_name,
                    "status": step.status,
                    "error": step.error,
                    "duration": step.duration,
                    "input_preview": str(step.actual_input)[:200] if step.actual_input else None,
                    "output_preview": str(step.actual_output)[:200] if step.actual_output else None
                }
                for step in result.steps
            ],
            "issues": result.issues,
            "final_output_preview": str(result.final_output)[:500] if result.final_output else None
        }

        return report


# ==================== 兼容旧接口 ====================

def execute_workflow_steps(steps: List[Dict]) -> List[ExecutionStep]:
    """
    兼容旧接口: 执行工作流步骤

    参数:
        steps: 步骤列表,每个步骤是包含Step_number, Description, Tool, Input, Output的字典

    返回:
        执行步骤结果列表
    """
    agent = ExecutionAgent()

    # 创建协议
    protocol = {"Steps": steps}

    # 执行工作流
    result = agent.execute_workflow(protocol)

    return result.steps


# ==================== 主函数 ====================

def main():
    """测试函数"""
    import argparse

    parser = argparse.ArgumentParser(description="Execution Agent - 工作流执行智能体")
    parser.add_argument("--protocol", "-p", help="协议文件路径(JSON)")
    parser.add_argument("--protocols", "-P", help="多个协议文件路径(JSON数组)")
    parser.add_argument("--toolpool", "-t", help="工具定义文件路径")
    parser.add_argument("--api-key", help="Deepseek API密钥")
    parser.add_argument("--test", action="store_true", help="运行测试模式")

    args = parser.parse_args()

    # 创建Agent
    agent = ExecutionAgent(
        deepseek_api_key=args.api_key,
        toolpool_path=args.toolpool
    )

    if args.test:
        # 测试模式
        print("运行测试模式...")

        # 创建测试协议
        test_protocol = {
            "strategy_name": "Test_Strategy",
            "Steps": [
                {
                    "Step_number": 1,
                    "Description": "Generate Gaussian input for DFT optimization",
                    "Tool": "generate_gaussian_code",
                    "Input": "DFT optimization with B3LYP/6-31G*",
                    "Output": "Gaussian keywords and route section"
                },
                {
                    "Step_number": 2,
                    "Description": "Convert XYZ to GJF",
                    "Tool": "xyz_to_gjf",
                    "Input": "XYZ coordinates and Gaussian keywords",
                    "Output": "GJF input file"
                }
            ]
        }

        result = agent.execute_workflow(test_protocol)
        report = agent.generate_execution_report(result)

        print("\n" + "="*60)
        print("测试执行结果")
        print("="*60)
        print(f"策略: {report['strategy_name']}")
        print(f"成功率: {report['summary']['success_rate']:.2%}")
        print(f"总耗时: {report['summary']['total_duration']:.2f}秒")

    elif args.protocols:
        # 执行多个协议
        try:
            with open(args.protocols, "r", encoding="utf-8") as f:
                protocols = json.load(f)

            summary = agent.execute_multiple_workflows(protocols)

            print("\n" + "="*60)
            print("多工作流执行摘要")
            print("="*60)
            print(f"工作流数量: {summary['total_workflows']}")
            print(f"总步骤数: {summary['total_steps']}")
            print(f"成功步骤: {summary['successful_steps']}")
            print(f"失败步骤: {summary['failed_steps']}")
            print(f"总体成功率: {summary['overall_success_rate']:.2%}")
            print(f"总耗时: {summary['total_duration']:.2f}秒")

        except Exception as e:
            print(f"执行多个协议失败: {e}")

    elif args.protocol:
        # 执行单个协议
        try:
            with open(args.protocol, "r", encoding="utf-8") as f:
                protocol = json.load(f)

            result = agent.execute_workflow(protocol)
            report = agent.generate_execution_report(result)

            print("\n" + "="*60)
            print("工作流执行结果")
            print("="*60)
            print(f"策略: {report['strategy_name']}")
            print(f"总步骤数: {report['summary']['total_steps']}")
            print(f"成功步骤: {report['summary']['successful_steps']}")
            print(f"失败步骤: {report['summary']['failed_steps']}")
            print(f"成功率: {report['summary']['success_rate']:.2%}")
            print(f"总耗时: {report['summary']['total_duration']:.2f}秒")

            if report['issues']:
                print(f"\n⚠️  问题:")
                for issue in report['issues']:
                    print(f"  - {issue}")

        except Exception as e:
            print(f"执行协议失败: {e}")

    else:
        # 显示工具信息
        print("Execution Agent 工具信息:")
        print(f"加载工具数量: {len(agent.tools)}")
        print("\n可用工具:")
        for name, tool in agent.tools.items():
            print(f"  - {name}: {tool.description}")


if __name__ == "__main__":
    main()
