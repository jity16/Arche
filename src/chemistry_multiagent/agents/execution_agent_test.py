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
import subprocess
import importlib
import logging
import time
import datetime
import traceback
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
                 enable_expert_analysis: bool = True):
        """
        初始化执行智能体

        参数:
            deepseek_api_key: Deepseek API密钥
            toolpool_path: 工具定义文件路径
            expert_model_name: ARCHE-Chem专家模型名称 (默认 qwen2.5-7b-instruct)
            expert_model_path: 本地模型路径 (可选)
            expert_backend: 模型后端 (默认 local_hf)
            enable_expert_analysis: 是否启用专家分析 (默认 True)
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
        self.enable_validation = True  # 启用验证
        self.enable_error_recovery = True  # 启用错误恢复建议
        self.simulated_tools = [
            "generate_gaussian_code", "main", "xyz_to_gjf",
            "smiles_to_sdf", "sdf_to_xyz"
        ]  # 模拟工具列表
        # ARCHE-Chem expert integration
        self.expert_model_name = expert_model_name
        self.expert_model_path = expert_model_path
        self.expert_backend = expert_backend
        self.enable_expert_analysis = enable_expert_analysis
        self.arche_chem_client = None
        if self.enable_expert_analysis:
            self.arche_chem_client = self._create_arche_chem_client()
        self.validation_critical_types = {"opt", "ts", "irc", "sp"}

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
                "tool_path": "gaussian.tools.main",
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
                "tool_path": "openbabel.tools.sdf_to_xyz",
                "description": "Convert SDF molecular structure to XYZ coordinates."
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
            fallback_model = "deepseek-chat"
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
        audit["expert_fallback_model"] = "deepseek-chat" if LLM_API_AVAILABLE else None
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
            ], model="deepseek-chat", max_tokens=50)

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
        ], model="deepseek-chat", max_tokens=1000)

        return response.strip()

    # ==================== 工具执行 ====================

    def execute_tool(self, tool: ToolDefinition, input_data: Any) -> Any:
        """
        执行工具(增强版,明确标注模拟行为)

        注意:这是一个框架实现,当前为模拟执行。
        所有模拟行为都会明确标注,不会伪装成真实执行。
        """
        logger.info(f"[模拟] 执行工具: {tool.tool_name}")

        try:
            # 解析工具路径
            tool_path = tool.tool_path

            # 处理不同的工具路径格式
            if tool_path.startswith("gaussian.tools."):
                # Gaussian工具包 - 模拟执行(明确标注)
                logger.info(f"[模拟] 执行Gaussian相关工具: {tool.tool_name}")
                # 调用专门的模拟方法
                return self.execute_gaussian_related_tool(tool.tool_name, input_data)

            elif tool_path.startswith("rdkit.tools."):
                # RDKit工具 - 模拟执行
                logger.info(f"[模拟] 执行RDKit工具: {tool.tool_name}")
                return self.execute_mock_tool(tool.tool_name, input_data)

            elif tool_path.startswith("openbabel.tools."):
                # OpenBabel工具 - 模拟执行
                logger.info(f"[模拟] 执行OpenBabel工具: {tool.tool_name}")
                return self.execute_mock_tool(tool.tool_name, input_data)

            elif tool_path.endswith(".py"):
                # Python脚本 - 模拟执行
                script_path = os.path.join(project_root, tool_path)

                if not os.path.exists(script_path):
                    # 尝试在toolpool目录下查找
                    script_path = os.path.join(project_root, "toolpool", tool_path)

                if os.path.exists(script_path):
                    # 执行Python脚本(模拟)
                    logger.info(f"[模拟] 执行Python脚本: {script_path}")
                    return self.execute_python_tool(tool_path, input_data)
                else:
                    logger.warning(f"[模拟] 脚本文件不存在: {script_path}")
                    return None

            else:
                # 其他工具 - 模拟执行
                logger.info(f"[模拟] 执行通用工具: {tool.tool_name}")
                return self.execute_mock_tool(tool.tool_name, input_data)

        except Exception as e:
            logger.error(f"[模拟] 工具执行失败: {e}")
            return None

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

            is_simulated = tool.tool_name in self.simulated_tools or any(
                tool.tool_path.startswith(prefix) for prefix in ["gaussian.tools.", "rdkit.tools.", "openbabel.tools."]
            )

            raw_output = self.execute_tool(tool, input_data)
            step.raw_output = raw_output
            step.actual_output = str(raw_output)[:500] + "..." if raw_output and len(str(raw_output)) > 500 else raw_output

            if raw_output is not None:
                if is_simulated and "gaussian" in tool.tool_path.lower():
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
        """
        执行模拟工具(明确标注为模拟)
        """
        logger.info(f"[模拟] 执行模拟工具: {tool_name}")
        return f"[模拟] 工具 {tool_name} 执行成功,输入: {str(input_data)[:100]}..."

    def execute_gaussian_related_tool(self, tool_name: str, input_data: Any) -> Any:
        """
        执行Gaussian相关工具(明确标注为模拟,返回归一化schema)
        """
        logger.info(f"[模拟] 执行Gaussian相关工具: {tool_name}")

        # 根据工具名推断job_type
        tool_lower = tool_name.lower()
        job_type = "unknown"
        if "optimization" in tool_lower or "opt" in tool_lower:
            job_type = "opt"
        elif "transition" in tool_lower or "ts" in tool_lower:
            job_type = "ts"
        elif "frequency" in tool_lower or "freq" in tool_lower:
            job_type = "freq"
        elif "irc" in tool_lower:
            job_type = "irc"
        elif "single" in tool_lower or "sp" in tool_lower or "energy" in tool_lower:
            job_type = "sp"

        # 根据job_type生成适当的模拟数据
        mock_output = {
            "job_type": job_type,
            "normal_termination": True,
            "converged": True if job_type in ["opt", "ts"] else None,
            "scf_converged": True,
            "scf_energy": -123.456789 if job_type in ["opt", "ts", "sp", "freq"] else None,
            "free_energy": -123.456789 if job_type in ["opt", "ts", "sp", "freq"] else None,
            "zero_point_energy": -123.4 if job_type in ["opt", "ts", "freq"] else None,
            "n_imag_freq": 1 if job_type == "ts" else (0 if job_type in ["opt", "freq", "sp"] else None),
            "imag_freqs": [-10.5] if job_type == "ts" else [],
            "frequencies": [-10.5, 20.3, 30.1, 40.2, 50.4] if job_type in ["freq", "ts", "opt"] else [],
            "irc_verified": True if job_type == "irc" else None,
            "irc_path": "/mock/irc/path.log" if job_type == "irc" else None,
            "notes": ["此为模拟输出,非真实Gaussian计算结果"],
            "raw_output_preview": f"[模拟] Gaussian {job_type} 计算完成",
            "metadata": {
                "tool": tool_name,
                "input": str(input_data)[:200],
                "status": "completed",
                "simulated": True
            }
        }
        return mock_output

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
            "job_type": "unknown",
            "raw_output_preview": str(output)[:500] if output else None
        }

        if isinstance(output, dict):
            raw_result.update(output)
        else:
            output_str = str(output)
            output_lower = output_str.lower()

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
        return {
            "converged": parsed_output.get("converged") is True,
            "scf_converged": parsed_output.get("scf_converged") is True,
            "geometry_converged": parsed_output.get("converged") is True,
            "normal_termination": parsed_output.get("normal_termination") is True
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
            if ".gjf" in data.lower():
                return "gjf"
            elif ".xyz" in data.lower():
                return "xyz"
            elif ".sdf" in data.lower():
                return "sdf"
            elif "smiles" in data.lower():
                return "smiles"
        return None

    # ==================== 工作流执行 ====================

    def execute_workflow(self,
                        protocol: Dict,
                        strategy_name: Optional[str] = None) -> ExecutionResult:
        """
        执行工作流(增强版,使用结构化执行方法)
        """
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
        previous_output = None
        previous_format = None
        total_retry_count = 0
        recoverable_issue_count = 0
        unrecoverable_issue_count = 0

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

            route_section = (
                step_dict.get("route_section")
                or step_dict.get("gaussian_keywords")
                or step_dict.get("current_route")
                or calculation_context.get("current_route_or_keywords")
                or gaussian_review.get("recommended_route")
            )
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
            if specified_tool in self.tools:
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
