#!/usr/bin/env python3
"""
Planner Agent - 工作流规划智能体

功能：
1. 根据排序后的科学假设生成计算工作流
2. 优化工作流以提高效率和正确性
3. 整合：exp_protocol.py + opt_protocol.py + workflow.py

输入: 排序后的科学假设 + 科学问题 + 工具定义
输出: 优化的计算工作流
"""

import os
import sys
import json
import re
import logging
from typing import List, Dict, Any, Optional

# 添加项目根目录到Python路径
project_root = os.path.dirname(os.path.abspath(__file__))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

try:
    from chemistry_multiagent.utils.llm_api import call_deepseek_api, extract_json_from_response
    LLM_API_AVAILABLE = True
except ImportError:
    LLM_API_AVAILABLE = False
    print("警告: utils.llm_api模块不可用，需要备用方案")

try:
    from chemistry_multiagent.utils.arche_chem_client import call_arche_chem as shared_call_arche_chem
    ARCHE_CHEM_CLIENT_AVAILABLE = True
except ImportError:
    try:
        from utils.arche_chem_client import call_arche_chem as shared_call_arche_chem
        ARCHE_CHEM_CLIENT_AVAILABLE = True
    except ImportError:
        try:
            from arche_chem_client import call_arche_chem as shared_call_arche_chem
            ARCHE_CHEM_CLIENT_AVAILABLE = True
        except ImportError:
            shared_call_arche_chem = None
            ARCHE_CHEM_CLIENT_AVAILABLE = False

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class PlannerAgent:
    """工作流规划智能体"""
    
    def __init__(self, 
                 deepseek_api_key: Optional[str] = None,
                 toolpool_path: Optional[str] = None,
                 general_model_name: str = os.environ.get("DEEPSEEK_MODEL", "interns2-preview-sft"),
                 expert_model_name: str = "qwen2.5-7b-instruct",
                 expert_model_path: Optional[str] = None,
                 expert_backend: str = "local_hf",
                 enable_expert_review: bool = True):
        """
        初始化规划智能体
        
        参数:
            deepseek_api_key: Deepseek API密钥
            toolpool_path: 工具定义文件路径
            general_model_name: 通用规划模型名称
            expert_model_name: ARCHE-Chem专家模型名称
            expert_model_path: ARCHE-Chem专家模型本地路径（可选）
            expert_backend: 专家模型后端类型
            enable_expert_review: 是否启用Gaussian专家复核
        """
        self.deepseek_api_key = deepseek_api_key or os.environ.get("DEEPSEEK_API_KEY", "")
        self.general_model_name = general_model_name
        self.expert_model_name = expert_model_name
        self.expert_model_path = expert_model_path
        self.expert_backend = expert_backend
        self.enable_expert_review = enable_expert_review
        
        # 工具定义
        self.toolpool_path = toolpool_path or self._find_toolpool_path()
        self.tools = self._load_tools()
        
        # 系统提示词（增强版，保持向后兼容）
        self.protocol_generation_prompt = """
        You are a computational chemistry research assistant specialized in automating quantum chemistry workflows.  
        You have expert knowledge of Gaussian, RDKit, OpenBabel, xTB, and Python-based tools for reaction mechanism exploration.  

        Your role:  
        - Take as input a scientific question, a proposed strategy, its reasoning, and a JSON file containing tool definitions.  

        **CRITICAL CHEMISTRY REQUIREMENTS:**
        1. **Transition State (TS) workflows MUST include frequency analysis after TS optimization.**
        2. **If reaction pathway verification is needed, include IRC calculations.**
        3. **Specify success criteria for key steps (e.g., convergence criteria for optimizations).**
        4. **Include validation steps where appropriate (e.g., frequency analysis, energy comparisons).**

        Tool usage rules:  
        1. **Always use tools from the JSON file if they exist for a task.** Only use standard software (Gaussian, RDKit, etc.) if no suitable JSON tool exists.  
        2. **Gaussian preparation rule**: Every time a Gaussian calculation is required (optimization, frequency, TS search, energy calculation), **you must first call `"generate_gaussian_code"`** to generate the Gaussian route/input file. The output of `"generate_gaussian_code"` is then the input for Gaussian.  
        3. **TS-specific rules**:
           - Use `"main"` to generate initial TS structures.  
           - Do not perform additional SMILES→SDF or conformer generation for TS if `"main"` is used.  
           - After `"main"`, always call `"generate_gaussian_code"` before Gaussian TS optimization.  
           - **TS workflows MUST include frequency analysis after TS optimization.**
        4. **Reactant/product conformers**: Only perform conformer search if needed and not handled by `"main"` or another JSON tool.  

        Each step should specify:  
        - Description of the task  
        - Tool/software/script used (must match JSON tool if applicable)  
        - Input (SMILES, xyz, gjf, sdf, etc.)  
        - Output (optimized structure, energy, TS geometry, etc.)  
        - **Success criteria (recommended, e.g., convergence thresholds)**
        - **Error handling hints (recommended, e.g., what to try if step fails)**

        File format consistency rule:
        - At every step, check whether the output format of the previous step matches the required input format of the next step.  
        - If the formats do not match, you must insert an intermediate step using the appropriate format conversion tool (e.g., xyz_to_gjf, sdf_to_xyz, smiles_to_sdf, etc.).  
        - Do not skip this check. Always ensure input/output compatibility between steps.  

        Tool I/O summary:
        - main: Input = SMILES → Output = XYZ (transition state guess structures)
        - generate_gaussian_code: Input = question → Output = Gaussian keywords and route section
        - xyz_to_gjf: Input = XYZ and Gaussian keywords and route section → Output = GJF
        - smiles_to_sdf: Input = SMILES → Output = SDF
        - sdf_to_xyz: Input = SDF → Output = XYZ

        Output requirements:  
        - Strict JSON format with a list of steps  
        - Required step fields: "Step_number", "Description", "Tool", "Input", "Output"  
        - Recommended additional fields: "success_criteria", "error_handling_hint"
        - No extra text outside JSON  
        - Decompose major computational tasks into detailed sub-steps **without duplicating functionality already in JSON tools**  
        - Steps must be ordered for practical Python automation  
        """
        
        self.protocol_optimization_prompt = """
        You are an expert in computational chemistry and Gaussian software. 
        You are assisting in refining an automated workflow where each step is stored as a dictionary inside a list.

        Your responsibilities:
        1. **Chemistry-aware optimization**:
           - Ensure TS workflows include mandatory frequency analysis.
           - Add IRC calculations if reaction pathway verification is needed.
           - Include success criteria for key steps (e.g., convergence thresholds).
           - Add validation steps where missing (frequency analysis, energy comparisons).
           
        2. **Workflow completeness checking**:
           - Verify workflow has all necessary components for its type.
           - Add missing steps (e.g., format conversions, validation steps).
           - Ensure proper step ordering and dependencies.
           
        3. Carefully reflect on the workflow to identify redundant or overlapping steps.
        4. Merge or delete redundant steps while preserving the scientific integrity of the workflow.
        5. For steps that use external tools (e.g., RDKit, Open Babel, ORCA, xTB, etc.), check whether the same goal can be achieved with Gaussian. 
           - If Gaussian can perform the task, replace the tool with Gaussian and adapt the step description accordingly.
        6. Validate all Gaussian-related steps:
           - Check the correctness of Gaussian keywords (functional, basis set, solvation model, job type, etc.).
           - If keywords are invalid, missing, or inconsistent with the described task, propose corrections.
           - Ensure Gaussian input is internally consistent (e.g., no mixed basis set errors, job type matches calculation goal).
        7. Apply the following rules for workflow granularity and detail:
           - **Every major computational task must be decomposed into detailed sub-steps.**
             For example, "Geometry optimization" must be split into:
               a) Generate Gaussian input file.
               b) Run Gaussian calculation.
               c) Check convergence criteria.
               d) Extract optimized geometry.
           - **For transition state (TS) calculations**, explicitly include the following sub-steps:
               1) Perform conformer search for reactants and products (using RDKit or CREST).
               2) Optimize the lowest-energy conformers with a chosen quantum chemistry method.
               3) Generate initial guesses for TS search by docking/aligning reactants into reasonable reactive orientations.
               4) Generate Gaussian input (.gjf) and perform TS optimization and frequency analysis.
               5) **MUST include frequency analysis after TS optimization.**
               6) Include IRC if pathway verification needed.
           - Ensure steps are **ordered for execution** so that the workflow can be automated.
        8. **Enhance step information**:
           - Add success criteria where missing.
           - Add error handling hints for critical steps.
           - Ensure step names and descriptions are clear and chemistry-aware.
           
        9. Always return the result in the same structured format: a list of dictionaries, where each dictionary represents a step.
        """
        
        # 工作流类型常量
        self.WORKFLOW_TYPES = [
            "optimization",
            "transition_state",
            "irc",
            "single_point",
            "frequency_analysis",
            "conformer_search",
            "reaction_pathway",
            "multi_step_mechanism"
        ]
        
        # 增强的系统提示词（用于结构化工作流生成）
        self.enhanced_generation_prompt = """
        You are a computational chemistry research assistant specialized in automating quantum chemistry workflows.  
        You have expert knowledge of Gaussian, RDKit, OpenBabel, xTB, and Python-based tools for reaction mechanism exploration.  

        Your role:  
        - Take as input a scientific question, a proposed strategy, its reasoning, and a JSON file containing tool definitions.  
        - Generate a **structured, chemistry-aware workflow** with detailed steps that can be executed automatically.

        **CRITICAL CHEMISTRY REQUIREMENTS:**
        1. **Transition State (TS) workflows MUST include:**
           - Structure preparation (SMILES to 3D, conformer generation if needed)
           - Gaussian input generation with appropriate TS search keywords
           - TS optimization with frequency analysis
           - Frequency analysis to confirm exactly ONE imaginary frequency
           - IRC calculation (if reaction pathway verification is required)
           
        2. **IRC workflows MUST include:**
           - IRC forward calculation
           - IRC reverse calculation (if bidirectional verification needed)
           - Energy extraction along IRC path
           - Connection to reactant/product verification
           
        3. **Success criteria MUST be specified for each step:**
           - Optimization: geometry convergence (<0.001 RMS gradient)
           - TS: exactly one imaginary frequency, negative frequency magnitude check
           - IRC: smooth energy profile, connection to minima
           - Frequency: no negative frequencies (except TS), thermochemical data extraction
           
        4. **Validation steps MUST be explicitly included:**
           - Convergence checks after optimizations
           - Frequency analysis after TS optimization
           - IRC verification after TS confirmation
           - Energy comparison for barrier calculations
           
        5. **Error handling hints for each step:**
           - What to do if optimization fails to converge
           - How to adjust TS search if no/multiple imaginary frequencies
           - What to try if IRC calculation fails
           - Alternative methods if primary tool fails

        Tool usage rules:  
        1. **Always use tools from the JSON file if they exist for a task.** Only use standard software (Gaussian, RDKit, etc.) if no suitable JSON tool exists.  
        2. **Gaussian preparation rule**: Every time a Gaussian calculation is required (optimization, frequency, TS search, energy calculation), **you must first call `"generate_gaussian_code"`** to generate the Gaussian route/input file. The output of `"generate_gaussian_code"` is then the input for Gaussian.  
        3. **TS-specific rules**:
           - Use `"main"` to generate initial TS structures.  
           - Do not perform additional SMILES→SDF or conformer generation for TS if `"main"` is used.  
           - After `"main"`, always call `"generate_gaussian_code"` before Gaussian TS optimization.  
        4. **Reactant/product conformers**: Only perform conformer search if needed and not handled by `"main"` or another JSON tool.  

        **STRUCTURED WORKFLOW SCHEMA:**
        Each workflow must include:
        - workflow_name: Descriptive name for the workflow
        - goal: Scientific goal of this workflow
        - strategy_name: Which strategy this implements
        - steps: List of structured steps

        Each step must include:
        - step_id: Unique identifier (e.g., "step_1", "conversion_1a")
        - step_name: Short descriptive name (e.g., "SMILES_to_SDF", "TS_optimization")
        - description: Detailed explanation of the task
        - tool: Exact tool name from JSON or standard software
        - inputs: List or description of input data/format
        - expected_outputs: List or description of expected outputs/format
        - success_criteria: How to determine if step succeeded
        - error_handling_hint: What to try if step fails
        - depends_on: List of step_ids this step depends on (optional)

        File format compatibility rule:
        - At every step, check whether the output format of the previous step matches the required input format of the next step.  
        - If the formats do not match, you must insert an intermediate step using the appropriate format conversion tool (e.g., xyz_to_gjf, sdf_to_xyz, smiles_to_sdf, etc.).  
        - Do not skip this check. Always ensure input/output compatibility between steps.  

        Tool I/O summary:
        - main: Input = SMILES → Output = XYZ (transition state guess structures)
        - generate_gaussian_code: Input = question → Output = Gaussian keywords and route section
        - xyz_to_gjf: Input = XYZ and Gaussian keywords and route section → Output = GJF
        - smiles_to_sdf: Input = SMILES → Output = SDF
        - sdf_to_xyz: Input = SDF → Output = XYZ

        Output requirements:  
        - Strict JSON format matching the enhanced schema
        - Steps must be ordered for practical Python automation  
        - Decompose major computational tasks into detailed sub-steps **without duplicating functionality already in JSON tools**  
        - No extra text outside JSON  
        """
        
        # 增强的优化提示词（用于chemistry-aware优化）
        self.enhanced_optimization_prompt = """
        You are an expert computational chemist specializing in workflow optimization and validation.
        
        Your responsibilities:
        1. **Chemistry-aware validation**: Ensure workflow follows proper computational chemistry protocols:
           - TS workflows include mandatory frequency analysis
           - Optimization workflows include convergence checks
           - Barrier calculations include reactant/product energy comparisons
           - IRC workflows properly verify TS connectivity
           
        2. **Completeness checking**: Verify workflow has all necessary steps:
           - Structure preparation, optimization, analysis, validation
           - Format conversions where needed
           - Error handling and validation steps
           
        3. **Success criteria refinement**: Ensure each step has clear, measurable success criteria:
           - Numerical thresholds (e.g., gradient < 0.001)
           - Expected outcomes (e.g., exactly one imaginary frequency)
           - Validation methods (e.g., frequency analysis output)
           
        4. **Error handling enhancement**: Improve error handling hints:
           - Suggest specific parameter adjustments
           - Provide alternative methods/tools
           - Include diagnostic checks
           
        5. **Format compatibility verification**: Explicitly check input/output format compatibility between consecutive steps.
           Insert conversion steps where needed and document why.
           
        6. **Workflow type identification**: Classify workflow type and ensure it has appropriate steps.
           
        Return the optimized workflow in the enhanced structured format.
        """
        
        logger.info(f"Planner Agent 初始化完成，加载了 {len(self.tools)} 个工具")
    
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
        logger.warning("未找到工具定义文件，使用默认工具")
        return os.path.join(project_root, "toolpool", "toolpool.json")
    
    def _load_tools(self) -> List[Dict]:
        """加载工具定义"""
        if not os.path.exists(self.toolpool_path):
            logger.warning(f"工具定义文件不存在: {self.toolpool_path}")
            return self._create_default_tools()
        
        try:
            with open(self.toolpool_path, "r", encoding="utf-8") as f:
                tools = json.load(f)
            
            logger.info(f"从 {self.toolpool_path} 加载了 {len(tools)} 个工具")
            return tools
            
        except Exception as e:
            logger.error(f"加载工具定义失败: {e}")
            return self._create_default_tools()
    
    def _create_default_tools(self) -> List[Dict]:
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
                "tool_name": "parse_gaussian_output",
                "tool_path": "../tools/output_parser.py",
                "description": "Parse a completed Gaussian .log/.out file and extract energies, geometry-convergence, frequencies, thermochemistry and properties as JSON. Use for any step that reads/parses results from a finished Gaussian calculation; never use generate_gaussian_code for parsing."
            }
        ]

        return default_tools
    
    # ==================== 辅助函数 ====================
    
    def _extract_json_object(self, raw_text: str) -> Dict:
        """从模型输出中提取JSON对象"""
        cleaned_text = re.sub(r"```(?:json)?", "", raw_text, flags=re.IGNORECASE).strip()
        match = re.search(r'\{.*\}', cleaned_text, flags=re.DOTALL)
        if not match:
            logger.warning("No JSON object found in model output.")
            return {}
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError as e:
            logger.error(f"JSON对象解析失败: {e}")
            return {}
    
    def _call_llm(self, messages: List[Dict], model: str = os.environ.get("DEEPSEEK_MODEL", "interns2-preview-sft"), **kwargs) -> str:
        """调用LLM API"""
        if not LLM_API_AVAILABLE:
            raise ImportError("LLM API模块不可用")

        return call_deepseek_api(messages, model=model, **kwargs)


    def normalize_step_schema(self, step: Dict[str, Any], index: Optional[int] = None) -> Dict[str, Any]:
        """将单个步骤标准化为增强schema，兼容旧/新字段。"""
        if not isinstance(step, dict):
            step = {}

        step_number = step.get("Step_number")
        if step_number is None:
            step_number = index + 1 if index is not None else 1

        raw_step_id = step.get("step_id")
        if raw_step_id is None:
            raw_step_id = step_number

        step_id = str(raw_step_id)
        step_name = step.get("step_name") or step.get("Description") or step.get("description") or f"step_{step_number}"
        description = step.get("description") or step.get("Description") or step_name
        tool = step.get("tool") or step.get("Tool") or step.get("tool_name") or ""
        inputs = step.get("inputs") if "inputs" in step else step.get("Input", "")
        expected_outputs = step.get("expected_outputs") if "expected_outputs" in step else step.get("Output", "")

        normalized = {
            "step_id": step_id,
            "step_name": step_name,
            "description": description,
            "tool": tool,
            "inputs": inputs,
            "expected_outputs": expected_outputs,
            "success_criteria": step.get("success_criteria", ""),
            "error_handling_hint": step.get("error_handling_hint", ""),
            "depends_on": step.get("depends_on", []),
            "calculation_context": step.get("calculation_context", {}),
            "gaussian_review": step.get("gaussian_review", {}),
            "_legacy_step_number": step_number,
        }
        return normalized

    def _to_legacy_step(self, normalized_step: Dict[str, Any], index: int) -> Dict[str, Any]:
        """将标准化步骤导出为execution兼容旧schema步骤。"""
        return {
            "Step_number": index,
            "Description": normalized_step.get("description") or normalized_step.get("step_name") or f"Step {index}",
            "Tool": normalized_step.get("tool", ""),
            "Input": normalized_step.get("inputs", ""),
            "Output": normalized_step.get("expected_outputs", ""),
            # 保留增强字段（兼容下游增强处理）
            "step_id": normalized_step.get("step_id", str(index)),
            "step_name": normalized_step.get("step_name", f"step_{index}"),
            "inputs": normalized_step.get("inputs", ""),
            "expected_outputs": normalized_step.get("expected_outputs", ""),
            "success_criteria": normalized_step.get("success_criteria", ""),
            "error_handling_hint": normalized_step.get("error_handling_hint", ""),
            "depends_on": normalized_step.get("depends_on", []),
            "calculation_context": normalized_step.get("calculation_context", {}),
            "gaussian_review": normalized_step.get("gaussian_review", {}),
        }

    def normalize_workflow_schema(self, workflow: Dict[str, Any]) -> Dict[str, Any]:
        """标准化工作流schema，内部统一使用 `steps`（增强步骤结构）。"""
        if not isinstance(workflow, dict):
            workflow = {}

        raw_steps = []
        if isinstance(workflow.get("steps"), list) and workflow.get("steps"):
            raw_steps = workflow.get("steps", [])
        elif isinstance(workflow.get("Steps"), list):
            raw_steps = workflow.get("Steps", [])

        normalized_steps = [self.normalize_step_schema(step, i) for i, step in enumerate(raw_steps)]

        normalized_workflow = dict(workflow)
        normalized_workflow["steps"] = normalized_steps
        return normalized_workflow

    def export_execution_compatible_workflow(self, workflow: Dict[str, Any]) -> Dict[str, Any]:
        """导出同时包含新旧schema的工作流，确保execution层可直接消费。"""
        normalized = self.normalize_workflow_schema(workflow)
        exported = dict(normalized)
        exported["steps"] = normalized.get("steps", [])
        exported["Steps"] = [self._to_legacy_step(s, i + 1) for i, s in enumerate(normalized.get("steps", []))]
        return exported

    _PARSER_OUTPUT_WORDS = {
        "output",
        "outputs",
        "out",
        "log",
        "logs",
        "result",
        "results",
        "freq",
        "freqs",
        "frequency",
        "frequencies",
        "thermo",
        "energy",
        "energies",
        "gaussian",
    }
    _PARSER_READER_WORDS = {
        "analyze",
        "analyzer",
        "analysis",
        "analyse",
        "extract",
        "read",
        "reader",
        "summarize",
        "summary",
        "summarise",
        "get",
        "load",
        "fetch",
        "collect",
        "harvest",
        "dump",
        "json",
        "csv",
        "report",
        "postprocess",
        "postprocessor",
        "post",
        "process",
    }
    _PARSER_EXPLICIT_WORDS = {"parse", "parser", "parsing", "postprocess", "postprocessor"}
    _CODEGEN_BLOCK_WORDS = {
        "input",
        "gjf",
        "route",
        "keyword",
        "keywords",
        "builder",
        "build",
        "prepare",
        "writer",
        "write",
        "codegen",
        "code",
        "generator",
    }
    _CHINESE_PARSER_MARKERS = ("解析", "分析", "读取", "提取", "汇总")
    _CHINESE_OUTPUT_MARKERS = ("输出", "日志", "结果")
    _CHINESE_CODEGEN_MARKERS = ("生成", "构建", "输入")

    @staticmethod
    def _tool_name_words(tool_lower: str) -> List[str]:
        return re.findall(r"[a-z0-9]+", tool_lower)

    @classmethod
    def _is_result_parser_name(cls, tool_lower: str) -> bool:
        """Return whether a tool name reads/parses a completed Gaussian output."""
        words = set(cls._tool_name_words(tool_lower))
        has_chinese_parser = any(marker in tool_lower for marker in cls._CHINESE_PARSER_MARKERS)
        has_chinese_output = any(marker in tool_lower for marker in cls._CHINESE_OUTPUT_MARKERS)
        has_chinese_codegen = any(marker in tool_lower for marker in cls._CHINESE_CODEGEN_MARKERS)
        has_output_context = bool(words & cls._PARSER_OUTPUT_WORDS) or has_chinese_output
        has_reader = bool(words & cls._PARSER_READER_WORDS) or has_chinese_parser
        has_explicit_parser = bool(words & cls._PARSER_EXPLICIT_WORDS) or "output_parser" in tool_lower
        hard_codegen = bool(words & cls._CODEGEN_BLOCK_WORDS) or has_chinese_codegen

        if "input" in words and has_explicit_parser and not has_output_context:
            return False
        if hard_codegen:
            return False
        if has_explicit_parser and has_output_context:
            return True
        return has_reader and has_output_context

    def _resolve_tool_status(self, tool_name: str) -> Dict[str, Any]:
        """解析工具状态：registered / recognized_family / unknown。"""
        tool = (tool_name or "").strip()
        tool_lower = tool.lower()
        registered = [t.get("tool_name", "") for t in self.tools if isinstance(t, dict)]

        if tool in registered:
            return {"status": "registered", "tool": tool, "mapped_tool": tool}

        families = {
            "gaussian": ["gaussian"],
            "rdkit": ["rdkit"],
            "openbabel": ["openbabel", "open babel"],
            "xtb": ["xtb", "gfn"],
            "orca": ["orca"],
            "pyscf": ["pyscf"],
            "psi4": ["psi4"],
            "ase": ["ase"],
            "python": ["python"],
        }

        detected_family = None
        if self._is_result_parser_name(tool_lower):
            detected_family = "parser"
        else:
            for family, kws in families.items():
                if any(kw in tool_lower for kw in kws):
                    detected_family = family
                    break

        if detected_family:
            mapped_tool = None
            for rt in registered:
                rt_lower = rt.lower()
                if detected_family == "parser" and ("parse" in rt_lower or "parser" in rt_lower):
                    mapped_tool = rt
                    break
                if (
                    detected_family == "gaussian"
                    and ("gaussian" in rt_lower or "gjf" in rt_lower)
                    and "parse" not in rt_lower
                    and "parser" not in rt_lower
                ):
                    mapped_tool = rt
                    break
                if detected_family == "rdkit" and "rdkit" in rt_lower:
                    mapped_tool = rt
                    break
                if detected_family == "openbabel" and ("babel" in rt_lower or "sdf_to_xyz" in rt_lower):
                    mapped_tool = rt
                    break
            return {
                "status": "recognized_family",
                "tool": tool,
                "family": detected_family,
                "mapped_tool": mapped_tool,
            }

        return {"status": "unknown", "tool": tool, "mapped_tool": None}

    def _choose_registered_tool(self, candidates: List[str]) -> Optional[str]:
        """从候选中选择已注册工具。"""
        registered = {t.get("tool_name", "") for t in self.tools if isinstance(t, dict)}
        for c in candidates:
            if c in registered:
                return c
        return None

    def _validate_protocol_tools(self, protocol: Dict[str, Any]) -> Dict[str, Any]:
        """Validate every step's Tool against the registered toolpool.

        Registered tools are kept. Recognized families are mapped to their registered
        tool. Unknown tools fall back to the explicit Gaussian code-generation default
        when available, otherwise execution will fail loudly with the original name.
        """
        if not isinstance(protocol, dict):
            return protocol

        steps = protocol.get("Steps")
        if not isinstance(steps, list):
            return protocol

        gaussian_default = self._choose_registered_tool(["generate_gaussian_code"])

        for i, step in enumerate(steps, 1):
            if not isinstance(step, dict):
                continue

            original_tool = str(step.get("Tool") or step.get("tool") or "").strip()
            if not original_tool:
                continue

            status = self._resolve_tool_status(original_tool)

            if status["status"] == "registered":
                continue

            mapped_tool = status.get("mapped_tool")
            if status["status"] == "recognized_family" and mapped_tool:
                logger.warning(
                    f"Step {i}: tool '{original_tool}' is not registered; recognized "
                    f"{status.get('family')} family and mapped to '{mapped_tool}'"
                )
                step["Tool"] = mapped_tool
                if "tool" in step:
                    step["tool"] = mapped_tool
                continue

            if status["status"] == "recognized_family" and status.get("family") == "gaussian" and gaussian_default:
                logger.warning(
                    f"Step {i}: tool '{original_tool}' is not in the toolpool; "
                    f"mapping to default Gaussian code-generation tool '{gaussian_default}'"
                )
                step["Tool"] = gaussian_default
                if "tool" in step:
                    step["tool"] = gaussian_default
                continue
            logger.warning(
                f"Step {i}: tool '{original_tool}' is not in the toolpool and has no "
                "registered mapping; keeping the original name so execution fails loudly"
            )

        return protocol

    def _is_gaussian_related_step(self, step: Dict[str, Any]) -> bool:
        tool = str(step.get("tool") or step.get("Tool") or "").lower()
        desc = str(step.get("description") or step.get("Description") or "").lower()
        io_text = f"{step.get('inputs', step.get('Input', ''))} {step.get('expected_outputs', step.get('Output', ''))}".lower()
        gaussian_keywords = ["gaussian", "generate_gaussian_code", "freq", "opt", "ts", "irc", "single point", "route section"]
        return any(k in tool or k in desc or k in io_text for k in gaussian_keywords)

    def _infer_target_species(self, step: Dict[str, Any]) -> str:
        text = f"{step.get('description', '')} {step.get('step_name', '')}".lower()
        if "transition state" in text or re.search(r"\bts\b", text):
            return "TS"
        if "irc" in text:
            return "IRC"
        if "intermediate" in text:
            return "intermediate"
        if "reactant" in text:
            return "reactant"
        if "product" in text:
            return "product"
        if "conformer" in text:
            return "conformer"
        if "single-point" in text or "single point" in text or re.search(r"\bsp\b", text):
            return "single-point refinement"
        return "unknown"

    def _extract_expected_elements(self, step: Dict[str, Any], chemistry_context: Optional[Dict[str, Any]] = None) -> List[str]:
        chemistry_context = chemistry_context or {}
        candidates = chemistry_context.get("expected_elements") or chemistry_context.get("elements") or []
        if isinstance(candidates, str):
            candidates = re.findall(r"[A-Z][a-z]?", candidates)
        if isinstance(candidates, list) and candidates:
            return [str(x) for x in candidates]

        text = f"{step.get('description', '')} {step.get('inputs', '')} {step.get('expected_outputs', '')}"
        elems = re.findall(r"\b([A-Z][a-z]?)\b", text)
        non_elements = {"TS", "IRC", "SCF", "RMS", "SMILES", "XYZ", "GJF", "SDF"}
        out = []
        for e in elems:
            if e.upper() in non_elements:
                continue
            if e not in out:
                out.append(e)
        return out

    def call_arche_chem(self, messages: List[Dict[str, str]], max_tokens: int = 2048, temperature: float = 0.2) -> str:
        """调用ARCHE-Chem专家模型文本输出（保持向后兼容接口）。"""
        text, _audit = self._call_arche_chem_with_audit(messages, max_tokens=max_tokens, temperature=temperature)
        return text

    def _call_arche_chem_with_audit(self,
                                    messages: List[Dict[str, str]],
                                    max_tokens: int = 2048,
                                    temperature: float = 0.2) -> tuple:
        """调用ARCHE-Chem并返回(文本, 审计信息)。优先本地ARCHE-Chem，失败后回退DeepSeek。"""
        if not self.enable_expert_review:
            raise RuntimeError("Expert review is disabled by configuration")

        audit = {
            "expert_backend_requested": self.expert_backend,
            "expert_backend_used": None,
            "expert_fallback_triggered": False,
            "expert_fallback_reason": None,
            "expert_fallback_model": None,
        }

        local_error = None
        if ARCHE_CHEM_CLIENT_AVAILABLE and shared_call_arche_chem is not None:
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
                return text, audit
            except Exception as e:
                local_error = str(e)
                logger.warning(f"Local ARCHE-Chem failed, fallback to DeepSeek: {e}")
        else:
            local_error = "local_arche_chem_client_unavailable"

        audit["expert_fallback_triggered"] = True
        audit["expert_fallback_reason"] = local_error

        if LLM_API_AVAILABLE:
            fallback_model = self.general_model_name or os.environ.get("DEEPSEEK_MODEL", "interns2-preview-sft")
            text = self._call_llm(
                messages,
                model=fallback_model,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            audit["expert_backend_used"] = "deepseek_api"
            audit["expert_fallback_model"] = fallback_model
            return text, audit

        raise RuntimeError(
            f"ARCHE-Chem local backend failed ({local_error}) and DeepSeek fallback is unavailable"
        )

    def extract_gaussian_related_steps(self, workflow: Dict[str, Any]) -> List[Dict[str, Any]]:
        normalized = self.normalize_workflow_schema(workflow)
        related = []
        for idx, step in enumerate(normalized.get("steps", [])):
            if self._is_gaussian_related_step(step):
                related.append({"index": idx, "step": step})
        return related

    def ensure_gaussian_context_fields(self,
                                       step: Dict[str, Any],
                                       question: str,
                                       chemistry_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        chemistry_context = chemistry_context or {}
        base_ctx = dict(step.get("calculation_context") or {})
        base_ctx.setdefault("scientific_question", question)
        base_ctx.setdefault("calculation_purpose", chemistry_context.get("calculation_purpose") or step.get("description", ""))
        base_ctx.setdefault("job_type", chemistry_context.get("job_type") or step.get("step_name", step.get("tool", "")))
        base_ctx.setdefault("target_species", chemistry_context.get("target_species") or self._infer_target_species(step))
        base_ctx.setdefault("elements", self._extract_expected_elements(step, chemistry_context))
        base_ctx.setdefault("charge", chemistry_context.get("charge"))
        base_ctx.setdefault("multiplicity", chemistry_context.get("multiplicity"))
        base_ctx.setdefault("solvent", chemistry_context.get("solvent"))
        base_ctx.setdefault("temperature", chemistry_context.get("temperature"))
        base_ctx.setdefault("expected_artifacts", chemistry_context.get("expected_artifacts") or [step.get("expected_outputs", "")])
        base_ctx.setdefault("validation_plan", chemistry_context.get("validation_plan") or chemistry_context.get("expected_validation_requirements") or [
            "freq after optimization/TS when needed",
            "exactly one imaginary frequency for TS",
            "IRC when pathway verification is required",
            "follow-up refinement when relevant",
        ])
        step["calculation_context"] = base_ctx
        return base_ctx

    def build_gaussian_review_request(self,
                                      step: Dict[str, Any],
                                      workflow: Dict[str, Any],
                                      question: str,
                                      chemistry_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        calc_ctx = self.ensure_gaussian_context_fields(step, question, chemistry_context)
        return {
            "step_id": step.get("step_id"),
            "step_name": step.get("step_name"),
            "step_description": step.get("description"),
            "scientific_question": calc_ctx.get("scientific_question"),
            "calculation_purpose": calc_ctx.get("calculation_purpose"),
            "job_type": calc_ctx.get("job_type"),
            "target_species": calc_ctx.get("target_species"),
            "solvent": calc_ctx.get("solvent"),
            "charge": calc_ctx.get("charge"),
            "multiplicity": calc_ctx.get("multiplicity"),
            "expected_elements": calc_ctx.get("elements", []),
            "expected_validation_requirements": calc_ctx.get("validation_plan", []),
            "current_route_or_keywords": step.get("route_section")
                or step.get("gaussian_keywords")
                or step.get("Input")
                or step.get("inputs"),
            "success_criteria": step.get("success_criteria", ""),
            "workflow_name": workflow.get("workflow_name", ""),
            "workflow_goal": workflow.get("goal", ""),
        }

    def build_arche_chem_review_prompt(self, review_request: Dict[str, Any]) -> str:
        prompt = (
            "You are ARCHE-Chem, a domain expert for Gaussian workflow review.\\n"
            "Review the following Gaussian-related workflow step and return ONLY JSON.\\n\\n"
            f"Review request:\\n{json.dumps(review_request, indent=2, ensure_ascii=False)}\\n\\n"
            "Required checks:\\n"
            "1) Whether Gaussian is appropriate for this task\\n"
            "2) Whether route/keywords match scientific goal\\n"
            "3) Whether solvent setup is appropriate when needed\\n"
            "4) Whether basis set is compatible with expected elements\\n"
            "5) Whether job type matches intended purpose\\n"
            "6) Whether validation is sufficient (freq after opt/TS, one imaginary freq for TS, IRC when needed, follow-up refinement)\\n\\n"
            "Return JSON with fields:\\n"
            "- review_status: approved/revised/rejected\\n"
            "- expert_comments\\n"
            "- recommended_route\\n"
            "- keyword_risks\\n"
            "- basis_set_check\\n"
            "- solvent_check\\n"
            "- element_compatibility_check\\n"
            "- validation_requirements\\n"
            "- route_rationale"
        )
        return prompt

    def review_gaussian_steps_with_arche_chem(self,
                                              workflow: Dict[str, Any],
                                              question: str,
                                              chemistry_context: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        review_results = []
        for item in self.extract_gaussian_related_steps(workflow):
            idx = item["index"]
            step = item["step"]
            request = self.build_gaussian_review_request(step, workflow, question, chemistry_context)
            messages = [
                {"role": "system", "content": "You are ARCHE-Chem, a Gaussian protocol specialist."},
                {"role": "user", "content": self.build_arche_chem_review_prompt(request)},
            ]
            try:
                raw, audit = self._call_arche_chem_with_audit(messages, max_tokens=2048, temperature=0.2)
                parsed = self._extract_json_object(raw)
                if not isinstance(parsed, dict) or not parsed:
                    parsed = {
                        "review_status": "revised",
                        "expert_comments": raw[:500],
                        "recommended_route": request.get("current_route_or_keywords", ""),
                        "keyword_risks": ["Expert response not in strict JSON; manual check recommended"],
                        "basis_set_check": "unknown",
                        "solvent_check": "unknown",
                        "element_compatibility_check": "unknown",
                        "validation_requirements": request.get("expected_validation_requirements", []),
                        "route_rationale": "Fallback parse path",
                    }
                parsed.setdefault("expert_backend_requested", audit.get("expert_backend_requested"))
                parsed.setdefault("expert_backend_used", audit.get("expert_backend_used"))
                parsed.setdefault("expert_fallback_triggered", audit.get("expert_fallback_triggered", False))
                parsed.setdefault("expert_fallback_reason", audit.get("expert_fallback_reason"))
                parsed.setdefault("expert_fallback_model", audit.get("expert_fallback_model"))
                review_results.append({
                    "step_index": idx,
                    "step_id": step.get("step_id"),
                    "calculation_context": step.get("calculation_context", {}),
                    "gaussian_review": parsed,
                    "expert_review_audit": audit,
                })
            except Exception as e:
                logger.warning(f"ARCHE-Chem review unavailable for step {step.get('step_id', idx)}: {e}")
                fallback_audit = {
                    "expert_backend_requested": self.expert_backend,
                    "expert_backend_used": None,
                    "expert_fallback_triggered": True,
                    "expert_fallback_reason": str(e),
                    "expert_fallback_model": self.general_model_name or os.environ.get("DEEPSEEK_MODEL", "interns2-preview-sft"),
                }
                review_results.append({
                    "step_index": idx,
                    "step_id": step.get("step_id"),
                    "calculation_context": step.get("calculation_context", {}),
                    "gaussian_review": {
                        "review_status": "revised",
                        "expert_comments": f"Expert review unavailable: {e}",
                        "recommended_route": request.get("current_route_or_keywords", ""),
                        "keyword_risks": ["Expert backend unavailable; manual review recommended"],
                        "basis_set_check": "unknown",
                        "solvent_check": "unknown",
                        "element_compatibility_check": "unknown",
                        "validation_requirements": request.get("expected_validation_requirements", []),
                        "route_rationale": "Structured failure fallback",
                        "expert_backend_requested": fallback_audit["expert_backend_requested"],
                        "expert_backend_used": fallback_audit["expert_backend_used"],
                        "expert_fallback_triggered": fallback_audit["expert_fallback_triggered"],
                        "expert_fallback_reason": fallback_audit["expert_fallback_reason"],
                        "expert_fallback_model": fallback_audit["expert_fallback_model"],
                    },
                    "expert_review_audit": fallback_audit,
                })
        return review_results

    def apply_expert_review_to_workflow(self,
                                        workflow: Dict[str, Any],
                                        review_results: List[Dict[str, Any]]) -> Dict[str, Any]:
        normalized = self.normalize_workflow_schema(workflow)
        steps = normalized.get("steps", [])
        for item in review_results:
            idx = item.get("step_index")
            if isinstance(idx, int) and 0 <= idx < len(steps):
                steps[idx]["calculation_context"] = item.get("calculation_context", steps[idx].get("calculation_context", {}))
                steps[idx]["gaussian_review"] = item.get("gaussian_review", {})
                steps[idx]["expert_review_audit"] = item.get("expert_review_audit", {})
        normalized["steps"] = steps

        used_backends = []
        fallback_reasons = []
        fallback_models = []
        any_fallback = False
        for item in review_results:
            audit = item.get("expert_review_audit", {}) if isinstance(item, dict) else {}
            if not isinstance(audit, dict):
                continue
            used = audit.get("expert_backend_used")
            if used:
                used_backends.append(str(used))
            if audit.get("expert_fallback_triggered") is True:
                any_fallback = True
            if audit.get("expert_fallback_reason"):
                fallback_reasons.append(str(audit.get("expert_fallback_reason")))
            if audit.get("expert_fallback_model"):
                fallback_models.append(str(audit.get("expert_fallback_model")))

        normalized["gaussian_review_summary"] = {
            "reviewed_steps": len(review_results),
            "expert_model_name": self.expert_model_name,
            "expert_backend": self.expert_backend,
            "expert_review_enabled": self.enable_expert_review,
            "expert_backend_requested": self.expert_backend,
            "expert_backend_used": sorted(set(used_backends)) if used_backends else [],
            "expert_fallback_triggered": any_fallback,
            "expert_fallback_reason": sorted(set(fallback_reasons)) if fallback_reasons else [],
            "expert_fallback_model": sorted(set(fallback_models)) if fallback_models else [],
        }
        return self.export_execution_compatible_workflow(normalized)

    def _gaussian_step_fingerprint(self, step: Dict[str, Any]) -> str:
        key_data = {
            "description": step.get("description") or step.get("Description"),
            "tool": step.get("tool") or step.get("Tool"),
            "inputs": step.get("inputs") if "inputs" in step else step.get("Input"),
            "expected_outputs": step.get("expected_outputs") if "expected_outputs" in step else step.get("Output"),
        }
        return json.dumps(key_data, ensure_ascii=False, sort_keys=True)

    def _gaussian_steps_changed(self, old_workflow: Dict[str, Any], new_workflow: Dict[str, Any]) -> bool:
        old_steps = [self._gaussian_step_fingerprint(s["step"]) for s in self.extract_gaussian_related_steps(old_workflow)]
        new_steps = [self._gaussian_step_fingerprint(s["step"]) for s in self.extract_gaussian_related_steps(new_workflow)]
        return old_steps != new_steps

    def _run_gaussian_expert_review(self,
                                    workflow: Dict[str, Any],
                                    question: str,
                                    chemistry_context: Optional[Dict[str, Any]] = None,
                                    force: bool = False,
                                    previous_workflow: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if not self.enable_expert_review:
            return self.export_execution_compatible_workflow(workflow)

        if previous_workflow is not None and (not force) and (not self._gaussian_steps_changed(previous_workflow, workflow)):
            return self.export_execution_compatible_workflow(workflow)

        related = self.extract_gaussian_related_steps(workflow)
        if not related:
            return self.export_execution_compatible_workflow(workflow)

        try:
            review_results = self.review_gaussian_steps_with_arche_chem(workflow, question, chemistry_context)
            if review_results:
                return self.apply_expert_review_to_workflow(workflow, review_results)
            logger.warning("Gaussian-related steps detected but no expert review result returned; keep original workflow")
            return self.export_execution_compatible_workflow(workflow)
        except Exception as e:
            logger.warning(f"Gaussian expert review failed, fallback to original planner workflow: {e}")
            return self.export_execution_compatible_workflow(workflow)
    
    # ==================== 增强辅助函数 ====================
    
    def _enhance_workflow_schema(self, protocol: Dict[str, Any], strategy_name: str, question: str) -> None:
        """
        增强工作流schema，添加缺失字段，并保持新旧schema双写。
        """
        if "workflow_name" not in protocol:
            protocol["workflow_name"] = f"Workflow_{strategy_name}"
        if "goal" not in protocol:
            protocol["goal"] = f"Address: {question[:100]}"

        normalized = self.normalize_workflow_schema(protocol)
        steps = normalized.get("steps", [])

        for i, step in enumerate(steps):
            step_number = i + 1
            if not step.get("step_id"):
                step["step_id"] = str(step_number)
            if not step.get("step_name"):
                words = str(step.get("description", f"Step {step_number}")).split()[:3]
                step["step_name"] = "_".join(words).replace(".", "").replace(",", "")[:30]

            if not step.get("inputs"):
                step["inputs"] = "Not specified"
            if not step.get("expected_outputs"):
                step["expected_outputs"] = "Not specified"

            if not step.get("success_criteria"):
                tool = str(step.get("tool", "")).lower()
                description = str(step.get("description", "")).lower()
                if "optimization" in description or "opt" in tool:
                    step["success_criteria"] = "Geometry convergence (RMS gradient < 0.001)"
                elif "transition" in description or re.search(r"\bts\b", description):
                    step["success_criteria"] = "Exactly one imaginary frequency"
                elif "frequency" in description or "freq" in description:
                    step["success_criteria"] = "Frequency calculation completed, no unexpected imaginary frequencies"
                elif "irc" in description:
                    step["success_criteria"] = "IRC calculation completed, smooth energy profile"
                else:
                    step["success_criteria"] = "Step completed without errors"

            if not step.get("error_handling_hint"):
                tool = str(step.get("tool", "")).lower()
                description = str(step.get("description", "")).lower()
                if "gaussian" in tool or "gaussian" in description:
                    step["error_handling_hint"] = "Check input format, keywords, convergence criteria. Try different initial guess if SCF fails."
                elif "optimization" in description:
                    step["error_handling_hint"] = "Try different initial geometry, increase max iterations, relax convergence criteria"
                elif "transition" in description or re.search(r"\bts\b", description):
                    step["error_handling_hint"] = "Try different TS guess, use QST3 method, adjust step size"
                else:
                    step["error_handling_hint"] = "Check input data, tool configuration, and system resources"

        normalized["steps"] = steps

        workflow_type = self.infer_workflow_type(normalized)
        normalized["inferred_workflow_type"] = workflow_type

        if workflow_type.startswith("transition_state"):
            descriptions = " ".join([str(step.get("description", "")).lower() for step in steps])
            if "frequency" not in descriptions and "freq" not in descriptions:
                normalized.setdefault("validation_notes", []).append(
                    "TS workflow should include frequency analysis after TS optimization"
                )

        exported = self.export_execution_compatible_workflow(normalized)
        protocol.clear()
        protocol.update(exported)

    def infer_workflow_type(self, workflow: Dict[str, Any]) -> str:
        """推断工作流类型（兼容新旧schema）。"""
        normalized = self.normalize_workflow_schema(workflow)
        steps = normalized.get("steps", [])
        if not steps:
            return "unknown"

        descriptions = " ".join([str(step.get("description", "")).lower() for step in steps])
        tools = " ".join([str(step.get("tool", "")).lower() for step in steps])
        full_text = descriptions + " " + tools

        if "transition" in full_text or re.search(r"\bts\b", full_text):
            if "irc" in full_text:
                return "transition_state_with_irc"
            return "transition_state"
        if "irc" in full_text:
            return "irc"
        if "optimization" in full_text or re.search(r"\bopt\b", full_text):
            return "optimization"
        if "single point" in full_text or re.search(r"\bsp\b", full_text) or "energy" in full_text:
            return "single_point"
        if "frequency" in full_text or "freq" in full_text:
            return "frequency_analysis"
        if "conformer" in full_text:
            return "conformer_search"
        if "reaction" in full_text or "pathway" in full_text:
            return "reaction_pathway"

        goal = str(normalized.get("goal", "")).lower()
        if "barrier" in goal or "activation" in goal:
            return "transition_state"
        if "energy" in goal or "relative" in goal:
            return "single_point"
        return "unknown"

    def check_workflow_completeness(self, workflow: Dict[str, Any]) -> Dict[str, Any]:
        """检查工作流完整性（chemistry-aware，兼容新旧schema）。"""
        normalized = self.normalize_workflow_schema(workflow)
        steps = normalized.get("steps", [])
        workflow_type = self.infer_workflow_type(normalized)

        result = {
            "workflow_type": workflow_type,
            "total_steps": len(steps),
            "missing_components": [],
            "validation_issues": [],
            "tool_resolution_warnings": [],
            "format_compatibility_issues": [],
            "success_criteria_issues": [],
            "chemistry_issues": [],
            "is_complete": True,
        }

        if not steps:
            result["missing_components"].append("No steps defined")
            result["is_complete"] = False
            return result

        # 1) 字段检查（基于归一化schema）
        required_fields = ["step_id", "step_name", "description", "tool", "inputs", "expected_outputs"]
        for i, step in enumerate(steps, 1):
            for field in required_fields:
                if field not in step or step[field] in [None, "", []]:
                    result["validation_issues"].append(f"Step {i}: Missing required field '{field}'")
                    result["is_complete"] = False

        # 2) 步骤顺序检查（按导出顺序）
        expected_ids = [str(i) for i in range(1, len(steps) + 1)]
        current_ids = [str(s.get("step_id", "")) for s in steps]
        if all(cid.isdigit() for cid in current_ids):
            if current_ids != expected_ids:
                result["validation_issues"].append(f"Step ids not sequential: {current_ids}")

        # 3) 工具检查：registered/recognized_family/unknown
        for i, step in enumerate(steps, 1):
            tool_name = str(step.get("tool", ""))
            status = self._resolve_tool_status(tool_name)
            if status["status"] == "unknown":
                result["validation_issues"].append(f"Step {i}: Unknown tool '{tool_name}'")
                result["is_complete"] = False
            elif status["status"] == "recognized_family" and tool_name:
                mapped = status.get("mapped_tool")
                result["tool_resolution_warnings"].append(
                    f"Step {i}: Tool '{tool_name}' recognized as {status.get('family')} family"
                    + (f", mappable to '{mapped}'" if mapped else ", requires runtime mapping")
                )

        # 4) Chemistry-aware完整性检查
        descriptions = " ".join([str(step.get("description", "")).lower() for step in steps])
        tools = " ".join([str(step.get("tool", "")).lower() for step in steps])
        full_text = descriptions + " " + tools

        if workflow_type in ["transition_state", "transition_state_with_irc"]:
            required_components = {
                "structure_preparation": any(w in full_text for w in ["smiles", "sdf", "xyz", "conformer", "structure"]),
                "gaussian_input_generation": "generate_gaussian_code" in tools,
                "ts_optimization": any(w in full_text for w in ["ts optimization", "transition state", "ts search"]),
                "frequency_analysis": ("frequency" in full_text or "freq" in full_text),
            }
            for component, present in required_components.items():
                if not present:
                    result["missing_components"].append(f"TS workflow missing: {component}")
                    result["chemistry_issues"].append(f"TS workflow should include {component}")
                    result["is_complete"] = False

            if workflow_type == "transition_state_with_irc" and "irc" not in full_text:
                result["missing_components"].append("IRC calculation missing")
                result["chemistry_issues"].append("TS with IRC workflow should include IRC calculation")
                result["is_complete"] = False

            goal = str(normalized.get("goal", "")).lower()
            if "barrier" in goal or "activation" in goal:
                if "energy" not in full_text and "compare" not in full_text:
                    result["chemistry_issues"].append("Barrier calculation workflow should include energy comparison steps")

        elif workflow_type == "irc":
            if "irc" not in descriptions:
                result["missing_components"].append("IRC calculation steps missing")
                result["is_complete"] = False
            if "energy" not in descriptions and "extract" not in descriptions:
                result["chemistry_issues"].append("IRC workflow should include energy extraction along path")

        elif workflow_type == "optimization":
            if "convergence" not in descriptions and "check" not in descriptions:
                result["chemistry_issues"].append("Optimization workflow should include convergence checks")

        # 5) 格式兼容性检查（基于归一化输入输出）
        format_keywords = {
            "gjf": ["gjf", "gaussian"],
            "xyz": ["xyz", "coordinate"],
            "sdf": ["sdf", "molecule"],
            "smiles": ["smiles"],
        }
        for i in range(len(steps) - 1):
            current_output = str(steps[i].get("expected_outputs", "")).lower()
            next_input = str(steps[i + 1].get("inputs", "")).lower()
            current_format = None
            next_format = None
            for fmt, keywords in format_keywords.items():
                if any(k in current_output for k in keywords):
                    current_format = fmt
                if any(k in next_input for k in keywords):
                    next_format = fmt
            if current_format and next_format and current_format != next_format:
                result["format_compatibility_issues"].append(
                    f"Step {i + 1} output format ({current_format}) doesn't match Step {i + 2} input format ({next_format})"
                )

        # 6) 成功标准检查
        if not any(str(step.get("success_criteria", "")).strip() for step in steps):
            result["success_criteria_issues"].append("No success criteria specified in any step")

        return result

    def validate_workflow(self, workflow: Dict[str, Any]) -> Dict[str, Any]:
        """验证工作流（包括完整性和合理性，兼容新旧schema）。"""
        normalized = self.normalize_workflow_schema(workflow)
        execution_workflow = self.export_execution_compatible_workflow(normalized)

        completeness_result = self.check_workflow_completeness(normalized)

        steps = execution_workflow.get("Steps", [])
        basic_issues = self.validate_step_sequence(steps)

        validation_result = {
            "basic_validation": {
                "issues": basic_issues,
                "is_valid": len(basic_issues) == 0,
            },
            "completeness_check": completeness_result,
            "overall_is_valid": len(basic_issues) == 0 and completeness_result["is_complete"],
            "recommendations": [],
        }

        if not completeness_result["is_complete"]:
            validation_result["recommendations"].append("Workflow is incomplete. Consider adding missing components.")

        for missing in completeness_result.get("missing_components", []):
            validation_result["recommendations"].append(f"Add: {missing}")

        for issue in completeness_result.get("chemistry_issues", []):
            validation_result["recommendations"].append(f"Chemistry concern: {issue}")

        for issue in completeness_result.get("format_compatibility_issues", []):
            validation_result["recommendations"].append(f"Format issue: {issue}")

        for warn in completeness_result.get("tool_resolution_warnings", []):
            validation_result["recommendations"].append(f"Tool mapping note: {warn}")

        return validation_result

    def revise_workflow_from_reflection(self,
                                       original_workflow: Dict[str, Any],
                                       reflection_result: Dict[str, Any],
                                       strategy: Dict[str, Any],
                                       question: str = "",
                                       chemistry_context: Optional[Dict[str, Any]] = None,
                                       selected_strategy: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """根据反思结果局部修订工作流（兼容当前reflection schema）。"""
        revised = self.export_execution_compatible_workflow(dict(original_workflow))
        normalized = self.normalize_workflow_schema(revised)
        steps = list(normalized.get("steps", []))

        decision = reflection_result.get("decision", "")
        identified_problems = reflection_result.get("identified_problems", []) or []
        workflow_revision_instructions = reflection_result.get("workflow_revision_instructions", []) or []
        recommended_actions = reflection_result.get("recommended_actions", []) or []

        # 兼容旧字段
        legacy_issues = reflection_result.get("issues", []) or []
        legacy_suggestions = reflection_result.get("suggestions", []) or []
        evidence_summary = reflection_result.get("evidence_summary", {}) or {}
        failed_steps = evidence_summary.get("failed_steps", []) or reflection_result.get("failed_steps", []) or []

        logger.info(
            f"基于反思修订工作流: decision={decision}, problems={len(identified_problems)}, failed_steps={len(failed_steps)}"
        )

        # 给失败步骤补充error_handling_hint（不依赖旧字段名）
        for failed_step_info in failed_steps:
            target_id = failed_step_info.get("step_id") or failed_step_info.get("step_number")
            target_name = failed_step_info.get("step_name") or failed_step_info.get("description")
            err_info = failed_step_info.get("error_info", {}) if isinstance(failed_step_info.get("error_info", {}), dict) else {}
            err_msg = err_info.get("message") or failed_step_info.get("error") or ""
            err_cat = err_info.get("category") or failed_step_info.get("error_category")

            target_idx = -1
            for i, step in enumerate(steps):
                sid = str(step.get("step_id", ""))
                sname = str(step.get("step_name", "")).lower()
                if target_id is not None and sid == str(target_id):
                    target_idx = i
                    break
                if target_name and target_name.lower() in sname:
                    target_idx = i
                    break

            if target_idx >= 0:
                hint = f"Reflection: {err_cat or 'execution_issue'} - {err_msg}".strip()
                old_hint = str(steps[target_idx].get("error_handling_hint", "")).strip()
                steps[target_idx]["error_handling_hint"] = (old_hint + "; " + hint).strip("; ") if old_hint else hint

        problem_codes = {p.get("code") for p in identified_problems if isinstance(p, dict)}

        # 工作流设计缺口：尽量使用已注册工具生成可执行补丁步骤
        if "missing_frequency_for_ts_validation" in problem_codes:
            has_freq = any("frequency" in str(s.get("description", "")).lower() or "freq" in str(s.get("description", "")).lower() for s in steps)
            if not has_freq:
                prep_tool = self._choose_registered_tool(["generate_gaussian_code"])
                if prep_tool:
                    insert_idx = len(steps)
                    for i, s in enumerate(steps):
                        if "transition" in str(s.get("description", "")).lower() or re.search(r"\bts\b", str(s.get("description", "")).lower()):
                            insert_idx = i + 1
                    steps.insert(insert_idx, {
                        "step_id": f"step_{len(steps)+1}",
                        "step_name": "prepare_frequency_validation",
                        "description": "Prepare Gaussian frequency-analysis job settings for TS validation",
                        "tool": prep_tool,
                        "inputs": "Optimized TS structure and validation intent",
                        "expected_outputs": "Gaussian frequency route section/keywords",
                        "success_criteria": "Frequency job settings generated",
                        "error_handling_hint": "If frequency execution tool is unavailable, keep this as preparation step and resolve runtime mapping",
                        "depends_on": [],
                    })

        if "missing_irc_for_ts_pathway" in problem_codes:
            has_irc = any("irc" in str(s.get("description", "")).lower() for s in steps)
            if not has_irc:
                prep_tool = self._choose_registered_tool(["generate_gaussian_code"])
                if prep_tool:
                    steps.append({
                        "step_id": f"step_{len(steps)+1}",
                        "step_name": "prepare_irc_verification",
                        "description": "Prepare IRC verification job settings for TS connectivity check",
                        "tool": prep_tool,
                        "inputs": "Validated TS geometry and pathway verification objective",
                        "expected_outputs": "Gaussian IRC route section/keywords",
                        "success_criteria": "IRC job settings generated",
                        "error_handling_hint": "If dedicated IRC execution tool is unavailable, mark for runtime resolution before execution",
                        "depends_on": [],
                    })

        if "missing_result_validation_step" in problem_codes and steps:
            last_idx = len(steps) - 1
            note = "Add explicit parsing/validation checks for termination, convergence, and key frequencies."
            old_hint = str(steps[last_idx].get("error_handling_hint", "")).strip()
            steps[last_idx]["error_handling_hint"] = (old_hint + "; " + note).strip("; ") if old_hint else note

        # 收集反思说明
        revised_notes = []
        revised_notes.extend(workflow_revision_instructions)
        revised_notes.extend(recommended_actions)
        revised_notes.extend(legacy_suggestions)
        revised_notes.extend(legacy_issues)
        if revised_notes:
            normalized.setdefault("reflection_notes", [])
            normalized["reflection_notes"].extend(revised_notes)

        normalized["steps"] = [self.normalize_step_schema(step, i) for i, step in enumerate(steps)]
        normalized["revised_from_reflection"] = True
        normalized["original_workflow_name"] = original_workflow.get("workflow_name", original_workflow.get("strategy_name", "unknown"))
        normalized["reflection_decision"] = decision

        revised_export = self.export_execution_compatible_workflow(normalized)
        revised_export = self._run_gaussian_expert_review(
            revised_export,
            question or strategy.get("question", ""),
            chemistry_context=chemistry_context,
            force=True,
            previous_workflow=original_workflow,
        )
        validation = self.validate_workflow(revised_export)
        revised_export["validation_after_revision"] = validation

        logger.info(f"工作流修订完成: {len(revised_export.get('Steps', []))} steps, validation: {validation['overall_is_valid']}")
        return revised_export

    def generate_workflow_with_general_reasoner(self, 
                                               strategy: Dict[str, Any], 
                                               question: str) -> Dict[str, Any]:
        """
        使用通用推理器生成工作流（高层规划）
        
        参数:
            strategy: 策略字典
            question: 科学问题
        
        返回:
            结构化工作流
        """
        strategy_name = strategy.get("strategy_name", "Unknown_Strategy")
        reasoning = strategy.get("reasoning", "")
        
        logger.info(f"使用通用推理器生成工作流: {strategy_name}")
        
        # 用户提示词 - 高层规划
        user_prompt = f"""
        Scientific question: {question}
        
        Strategy to address the question: {strategy_name}
        
        Reasoning behind this strategy: {reasoning}
        
        Available tools: {json.dumps([tool['tool_name'] for tool in self.tools], indent=2)}
        
        Based on the scientific question and strategy, generate a HIGH-LEVEL workflow plan.
        Focus on:
        1. Major computational phases needed
        2. Key decision points
        3. Expected inputs and outputs for each phase
        4. Success criteria for the overall workflow
        
        Return the plan as a JSON object with:
        - workflow_name: Descriptive name
        - goal: Scientific goal
        - strategy_name: Which strategy this implements
        - phases: List of major phases
        - critical_decisions: Key decisions needed during execution
        - overall_success_criteria: How to know if workflow succeeded
        """
        
        messages = [
            {"role": "system", "content": "You are a computational chemistry research planner. Generate high-level workflow plans."},
            {"role": "user", "content": user_prompt}
        ]
        
        try:
            response = self._call_llm(
                messages, 
                model=self.general_model_name,
                temperature=0.7,
                max_tokens=2048
            )
            
            plan = self._extract_json_object(response)
            
            # 确保必要字段
            if not isinstance(plan, dict):
                plan = {}
            
            plan["strategy_name"] = strategy_name
            plan["reasoning"] = reasoning
            plan["question"] = question
            
            return plan
            
        except Exception as e:
            logger.error(f"通用推理器生成失败: {e}")
            # 返回基本计划
            return {
                "workflow_name": f"Workflow_for_{strategy_name}",
                "goal": f"Address question: {question[:100]}...",
                "strategy_name": strategy_name,
                "phases": ["Structure preparation", "Quantum chemistry calculation", "Analysis"],
                "critical_decisions": ["Method selection", "Convergence criteria", "Validation approach"],
                "overall_success_criteria": "Calculation completes and produces chemically reasonable results"
            }
    
    def optimize_workflow_with_chemistry_expert(self, 
                                               workflow_plan: Dict[str, Any], 
                                               question: str,
                                               chemistry_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        使用化学专家细化工作流（详细步骤生成）
        
        参数:
            workflow_plan: 高层工作流计划
            question: 科学问题
        
        返回:
            详细结构化工作流
        """
        logger.info(f"使用化学专家细化工作流: {workflow_plan.get('workflow_name', 'unknown')}")
        
        # 使用增强的提示词生成详细工作流
        strategy_name = workflow_plan.get("strategy_name", "Unknown_Strategy")
        reasoning = workflow_plan.get("reasoning", "")
        
        # 用户提示词
        user_prompt = f"""
        Scientific question: {question}
        
        High-level workflow plan:
        {json.dumps(workflow_plan, indent=2)}
        
        Tool definitions (from tools.json):  
        {json.dumps(self.tools, indent=2)}
        
        Based on the high-level plan, generate a **detailed, executable workflow** with structured steps.
        
        **CRITICAL CHEMISTRY REQUIREMENTS:**
        1. **Transition State (TS) workflows MUST include:**
           - Structure preparation (SMILES to 3D, conformer generation if needed)
           - Gaussian input generation with appropriate TS search keywords
           - TS optimization with frequency analysis
           - Frequency analysis to confirm exactly ONE imaginary frequency
           - IRC calculation (if reaction pathway verification is required)
           
        2. **IRC workflows MUST include:**
           - IRC forward calculation
           - IRC reverse calculation (if bidirectional verification needed)
           - Energy extraction along IRC path
           - Connection to reactant/product verification
           
        3. **Success criteria MUST be specified for each step:**
           - Optimization: geometry convergence (<0.001 RMS gradient)
           - TS: exactly one imaginary frequency, negative frequency magnitude check
           - IRC: smooth energy profile, connection to minima
           - Frequency: no negative frequencies (except TS), thermochemical data extraction
           
        4. **Validation steps MUST be explicitly included:**
           - Convergence checks after optimizations
           - Frequency analysis after TS optimization
           - IRC verification after TS confirmation
           - Energy comparison for barrier calculations
           
        5. **Error handling hints for each step:**
           - What to do if optimization fails to converge
           - How to adjust TS search if no/multiple imaginary frequencies
           - What to try if IRC calculation fails
           - Alternative methods if primary tool fails

        **STRUCTURED WORKFLOW SCHEMA:**
        Each workflow must include:
        - workflow_name: Descriptive name for the workflow
        - goal: Scientific goal of this workflow
        - strategy_name: Which strategy this implements
        - steps: List of structured steps

        Each step must include:
        - step_id: Unique identifier (e.g., "step_1", "conversion_1a")
        - step_name: Short descriptive name (e.g., "SMILES_to_SDF", "TS_optimization")
        - description: Detailed explanation of the task
        - tool: Exact tool name from JSON or standard software
        - inputs: List or description of input data/format
        - expected_outputs: List or description of expected outputs/format
        - success_criteria: How to determine if step succeeded
        - error_handling_hint: What to try if step fails
        - depends_on: List of step_ids this step depends on (optional)

        File format compatibility rule:
        - At every step, check whether the output format of the previous step matches the required input format of the next step.  
        - If the formats do not match, you must insert an intermediate step using the appropriate format conversion tool (e.g., xyz_to_gjf, sdf_to_xyz, smiles_to_sdf, etc.).  
        - Do not skip this check. Always ensure input/output compatibility between steps.  

        Output requirements:  
        - Strict JSON format matching the enhanced schema
        - Steps must be ordered for practical Python automation  
        - Decompose major computational tasks into detailed sub-steps **without duplicating functionality already in JSON tools**  
        - No extra text outside JSON  
        """
        
        messages = [
            {"role": "system", "content": self.enhanced_generation_prompt},
            {"role": "user", "content": user_prompt}
        ]
        
        try:
            response = self._call_llm(
                messages, 
                model=self.general_model_name,
                temperature=0.7,
                max_tokens=4096
            )
            
            workflow = self._extract_json_object(response)
            
            # 确保必要字段
            if not isinstance(workflow, dict):
                workflow = {}
            
            workflow["strategy_name"] = strategy_name
            workflow["reasoning"] = reasoning
            workflow["question"] = question
            
            workflow = self.export_execution_compatible_workflow(workflow)
            workflow = self._run_gaussian_expert_review(
                workflow,
                question,
                chemistry_context=chemistry_context,
                force=True,
            )
            # 验证工作流
            validation = self.validate_workflow(workflow)
            workflow["validation"] = validation
            
            logger.info(f"化学专家细化完成: {len(workflow.get('Steps', []))} steps, valid: {validation['overall_is_valid']}")
            
            return workflow
            
        except Exception as e:
            logger.error(f"化学专家细化失败: {e}")
            # 返回基本工作流
            return {
                "workflow_name": f"Detailed_{workflow_plan.get('workflow_name', 'Workflow')}",
                "goal": workflow_plan.get("goal", ""),
                "strategy_name": strategy_name,
                "Steps": [],
                "error": str(e)
            }
    
    def generate_enhanced_workflow(self, 
                                  strategy: Dict[str, Any], 
                                  question: str,
                                  chemistry_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        生成增强的工作流（两步法：通用规划 + 化学细化）
        
        参数:
            strategy: 策略字典
            question: 科学问题
        
        返回:
            增强的工作流
        """
        # 步骤1: 通用推理器生成高层计划
        high_level_plan = self.generate_workflow_with_general_reasoner(strategy, question)
        
        # 步骤2: 化学专家细化详细步骤
        detailed_workflow = self.optimize_workflow_with_chemistry_expert(
            high_level_plan,
            question,
            chemistry_context=chemistry_context,
        )
        
        # 链接信息
        detailed_workflow["high_level_plan"] = high_level_plan
        detailed_workflow["generation_method"] = "enhanced_two_step"
        
        return self.export_execution_compatible_workflow(detailed_workflow)
    
    # ==================== 工作流生成 ====================
    
    def generate_experiment_protocol(self, 
                                    strategy: Dict,
                                    question: str,
                                    top_n: int = 5,
                                    chemistry_context: Optional[Dict[str, Any]] = None,
                                    selected_strategy: Optional[Dict[str, Any]] = None) -> Dict:
        """
        为单个策略生成实验协议
        
        参数:
            strategy: 策略字典（包含strategy_name和reasoning）
            question: 科学问题
            top_n: 前N个策略
        
        返回:
            协议字典（包含Steps等）
        """
        strategy_name = strategy.get("strategy_name", "Unknown_Strategy")
        reasoning = strategy.get("reasoning", "")
        
        logger.info(f"为策略生成协议: {strategy_name}")
        
        # 用户提示词
        user_prompt = f"""
        Scientific question to be addressed: {question}

        Proposed strategy for solving the problem: {strategy_name}

        Reasoning behind the strategy: {reasoning}

        Tool definitions (from tools.json):  
        {json.dumps(self.tools, indent=2)}

        Based on the above, generate a **detailed sequence of computational steps** required.  

        Important requirements:  
        - Output must be a **strict JSON array of steps**.  
        - Every Gaussian calculation must be preceded by a step calling `"generate_gaussian_code"`. The output from this tool is the input for the Gaussian calculation.
        - Every step that reads, parses, analyzes or extracts results (energies, geometry convergence, frequencies, thermochemistry, HOMO/LUMO, etc.) from a completed Gaussian calculation MUST use `"parse_gaussian_output"`. Never reuse `"generate_gaussian_code"` for parsing — it only generates input keywords and never reads a `.log`.
        - Each step must include:
          - "Step_number"
          - "Description" (detailed explanation of the task, including sub-steps)
          - "Tool" (exact match from tools.json if applicable)
          - "Input" (SMILES, xyz, gjf, sdf, etc.)
          - "Output" (optimized structure, energy, TS geometry, etc.)

        Rules for transition state (TS) calculations:
        1. Use `"main"` to generate initial TS structures.  
        2. Do not perform additional SMILES→SDF or conformer generation for TS if `"main"` is used.  
        3. Use `"generate_gaussian_code"` to create Gaussian input.  
        4. Perform TS optimization and frequency analysis in Gaussian as a separate step.  
        5. Only pre-optimize reactants/products if necessary and not handled by `"main"`.  

        **Respond ONLY in the specified JSON format, no extra text.**

        {{
        "Steps": [
            {{
                "Step_number": 1,
                "Description": "[Describe the first computational step in detail]",
                "Tool": "[Select from tools.json if possible, otherwise specify Gaussian / Python / RDKit / OpenBabel / Other]",
                "Input": "[What data is required: SMILES, gjf, sdf, etc.]",
                "Output": "[What the step will produce: optimized structure, energy, conformer set, etc.]"
            }},
            {{
                "Step_number": 2,
                "Description": "...",
                "Tool": "...",
                "Input": "...",
                "Output": "..."
            }},
            ...
        ]
        }}
        """
        
        messages = [
            {"role": "system", "content": self.protocol_generation_prompt},
            {"role": "user", "content": user_prompt}
        ]
        
        try:
            response = self._call_llm(
                messages, 
                model=self.general_model_name,
                temperature=0.7,
                max_tokens=4096
            )
            
            protocol = self._extract_json_object(response)
            
            # 添加策略信息
            if isinstance(protocol, dict):
                protocol["strategy_name"] = strategy_name
                protocol["reasoning"] = reasoning

                # 校验每步 "Tool" 是否为已注册工具：钳制到合法工具或显式标记非法，
                # 避免 LLM 生成的未知工具名让 execution 静默以 "Tool not found" 失败。
                self._validate_protocol_tools(protocol)

                # 增强工作流schema
                self._enhance_workflow_schema(protocol, strategy_name, question)
                
                protocol = self.export_execution_compatible_workflow(protocol)
                protocol = self._run_gaussian_expert_review(
                    protocol,
                    question,
                    chemistry_context=chemistry_context,
                    force=True,
                )
                protocol["original_steps_count"] = len(protocol.get("Steps", []))
                logger.info(f"为策略 '{strategy_name}' 生成了 {protocol['original_steps_count']} 个步骤 (增强schema)")
            else:
                logger.warning(f"协议不是有效的JSON对象: {response[:200]}...")
                # 创建默认协议
                protocol = {
                    "strategy_name": strategy_name,
                    "reasoning": reasoning,
                    "Steps": [
                        {
                            "Step_number": 1,
                            "Description": f"Default step for {strategy_name}",
                            "Tool": "generate_gaussian_code",
                            "Input": f"Calculation requirements for {strategy_name}",
                            "Output": "Gaussian keywords and route section"
                        }
                    ],
                    "original_steps_count": 1
                }
            
            return self.export_execution_compatible_workflow(protocol)
            
        except Exception as e:
            logger.error(f"生成实验协议失败: {e}")
            # 返回默认协议
            return {
                "strategy_name": strategy_name,
                "reasoning": reasoning,
                "error": str(e),
                "Steps": []
            }
    
    # ==================== 工作流优化 ====================
    
    def optimize_protocol(self,
                         protocol: Dict,
                         question: str,
                         chemistry_context: Optional[Dict[str, Any]] = None,
                         selected_strategy: Optional[Dict[str, Any]] = None) -> Dict:
        """
        优化实验协议
        
        参数:
            protocol: 原始协议
            question: 科学问题
        
        返回:
            优化后的协议
        """
        strategy_name = protocol.get("strategy_name", "Unknown_Strategy")
        original_steps = protocol.get("Steps", [])
        
        if not original_steps:
            logger.warning(f"策略 '{strategy_name}' 没有步骤，跳过优化")
            return self.export_execution_compatible_workflow(protocol)
        
        logger.info(f"优化策略协议: {strategy_name} (原始步骤: {len(original_steps)})")
        
        # 用户提示词
        user_prompt = f"""
        Scientific question to be addressed: {question}

        Now, based on the above information, please analyze the current workflow.
        Here is the current workflow (each step is a dictionary in a list). 
        Please analyze it, remove or merge redundant steps, replace tool-specific steps with Gaussian where possible, 
        and check whether Gaussian-related steps contain keyword errors or inconsistencies. 
        Decompose all major tasks into detailed sub-steps according to the rules for level of detail. 
        For transition state (TS) calculations, explicitly follow the stepwise protocol given in the rules. 
        Return only the modified workflow as a list of dictionaries.

        Current workflow:
        {json.dumps(original_steps, indent=2)}

        **Respond ONLY in the specified JSON format below, do not include any text outside the JSON object.**

        {{
        "Steps": [
            {{
                "Step_number": 1,
                "Description": "[Describe the first computational step in detail]",
                "Tool": "[Specify Gaussian / Python / RDKit / OpenBabel / Other tool]",
                "Input": "[What data is required: SMILES, gjf, sdf, etc.]",
                "Output": "[What the step will produce: optimized structure, energy, conformer set, etc.]"
            }},
            {{
                "Step_number": 2,
                "Description": "...",
                "Tool": "...",
                "Input": "...",
                "Output": "..."
            }},
            ...
        ]
        }}
        """
        
        messages = [
            {"role": "system", "content": self.protocol_optimization_prompt},
            {"role": "user", "content": user_prompt}
        ]
        
        try:
            response = self._call_llm(
                messages, 
                model=self.general_model_name,
                temperature=0.7,
                max_tokens=4096
            )
            
            optimized_steps = self._extract_json_object(response)
            
            if isinstance(optimized_steps, dict) and "Steps" in optimized_steps:
                # 合并优化结果
                optimized_protocol = dict(protocol)
                optimized_protocol["Steps"] = optimized_steps["Steps"]
                optimized_protocol["optimized_steps_count"] = len(optimized_steps["Steps"])
                optimized_protocol["optimization_ratio"] = len(optimized_steps["Steps"]) / len(original_steps) if original_steps else 1.0
                
                # 增强优化后的工作流schema
                self._enhance_workflow_schema(optimized_protocol, strategy_name, question)
                optimized_protocol = self.export_execution_compatible_workflow(optimized_protocol)
                optimized_protocol = self._run_gaussian_expert_review(
                    optimized_protocol,
                    question,
                    chemistry_context=chemistry_context,
                    force=False,
                    previous_workflow=protocol,
                )
                
                logger.info(f"策略 '{strategy_name}' 优化完成: {len(original_steps)} → {len(optimized_steps['Steps'])} 个步骤 (增强schema)")
                return optimized_protocol
            else:
                logger.warning(f"优化后的工作流不是有效格式: {response[:200]}...")
                return self.export_execution_compatible_workflow(protocol)
                
        except Exception as e:
            logger.error(f"优化协议失败: {e}")
            return self.export_execution_compatible_workflow(protocol)
    
    # ==================== 主工作流程 ====================
    
    def generate_workflows_for_top_strategies(self,
                                             ranked_strategies: List[Dict],
                                             question: str,
                                             top_n: int = 5,
                                             chemistry_context: Optional[Dict[str, Any]] = None) -> Dict[str, any]:
        """
        为前N个策略生成工作流
        
        参数:
            ranked_strategies: 排名后的策略列表
            question: 科学问题
            top_n: 前N个策略
        
        返回:
            工作流结果字典
        """
        logger.info(f"为前{top_n}个策略生成工作流")
        
        result = {
            "question": question,
            "top_n": top_n,
            "total_strategies": len(ranked_strategies),
            "processed_strategies": min(top_n, len(ranked_strategies)),
            "protocols": [],
            "optimized_protocols": []
        }
        
        try:
            # 选择前N个策略
            top_strategies = ranked_strategies[:top_n]
            
            # 为每个策略生成协议
            raw_protocols = []
            for i, strategy in enumerate(top_strategies):
                logger.info(f"处理策略 {i+1}/{len(top_strategies)}: {strategy.get('strategy_name', 'Unknown')}")
                
                protocol = self.generate_experiment_protocol(
                    strategy,
                    question,
                    top_n,
                    chemistry_context=chemistry_context,
                    selected_strategy=strategy,
                )
                raw_protocols.append(protocol)
            
            result["protocols"] = raw_protocols
            
            # 优化每个协议
            optimized_protocols = []
            for i, protocol in enumerate(raw_protocols):
                logger.info(f"优化协议 {i+1}/{len(raw_protocols)}")
                
                optimized = self.optimize_protocol(
                    protocol,
                    question,
                    chemistry_context=chemistry_context,
                    selected_strategy=protocol,
                )
                optimized_protocols.append(optimized)
            
            result["optimized_protocols"] = optimized_protocols
            
            # 计算统计
            total_original_steps = sum(len(p.get("Steps", [])) for p in raw_protocols)
            total_optimized_steps = sum(len(p.get("Steps", [])) for p in optimized_protocols)
            
            result["total_original_steps"] = total_original_steps
            result["total_optimized_steps"] = total_optimized_steps
            result["optimization_ratio"] = total_optimized_steps / total_original_steps if total_original_steps > 0 else 0
            
            logger.info(f"工作流生成完成: {len(raw_protocols)} 个原始协议，{len(optimized_protocols)} 个优化协议")
            logger.info(f"步骤统计: 原始 {total_original_steps} → 优化 {total_optimized_steps} (比率: {result['optimization_ratio']:.2f})")
            
            # 保存结果
            self._save_workflow_results(result, question)
            
        except Exception as e:
            logger.error(f"工作流生成失败: {e}")
            result["error"] = str(e)
        
        return result
    
    def _save_workflow_results(self, result: Dict, question: str):
        """保存工作流结果到文件"""
        try:
            # 创建输出目录
            output_dir = os.path.join(project_root, "outputs", "workflow")
            os.makedirs(output_dir, exist_ok=True)
            
            # 生成文件名
            safe_name = re.sub(r'[^\w\s-]', '', question[:30]).strip().replace(' ', '_')
            
            # 保存完整结果
            result_file = os.path.join(output_dir, f"workflow_result_{safe_name}.json")
            with open(result_file, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2, ensure_ascii=False)
            
            # 保存优化的协议（供执行使用）
            protocols_file = os.path.join(output_dir, f"optimized_protocols_{safe_name}.json")
            optimized_protocols = result.get("optimized_protocols", [])
            with open(protocols_file, "w", encoding="utf-8") as f:
                json.dump(optimized_protocols, f, indent=2, ensure_ascii=False)
            
            logger.info(f"工作流结果已保存到: {result_file}")
            logger.info(f"优化协议已保存到: {protocols_file}")
            
        except Exception as e:
            logger.error(f"保存工作流结果失败: {e}")
    
    # ==================== 工具函数（供Execution Agent使用） ====================
    
    def get_workflow_steps(self, protocol: Dict) -> List[Dict]:
        """从协议中提取执行兼容步骤列表（兼容新旧schema）。"""
        exported = self.export_execution_compatible_workflow(protocol)
        return exported.get("Steps", [])

    def validate_step_sequence(self, steps: List[Dict]) -> List[str]:
        """验证步骤序列合理性（兼容新旧schema，工具识别更保守）。"""
        issues = []
        if not steps:
            issues.append("步骤列表为空")
            return issues

        normalized_steps = [self.normalize_step_schema(s, i) for i, s in enumerate(steps)]

        # 检查step_id连续性（仅当为纯数字时）
        step_ids = [str(s.get("step_id", "")) for s in normalized_steps]
        if all(sid.isdigit() for sid in step_ids):
            expected = [str(i) for i in range(1, len(normalized_steps) + 1)]
            if step_ids != expected:
                issues.append("步骤编号不连续或不正确")

        required_fields = ["step_id", "step_name", "description", "tool", "inputs", "expected_outputs"]
        for i, step in enumerate(normalized_steps, 1):
            for field in required_fields:
                if field not in step or step[field] in [None, "", []]:
                    issues.append(f"步骤 {i} 缺少字段 '{field}'")

        # 工具检查：仅对 truly unknown 报错，recognized_family 允许通过并留待映射
        for i, step in enumerate(normalized_steps, 1):
            status = self._resolve_tool_status(str(step.get("tool", "")))
            if status.get("status") == "unknown":
                issues.append(f"步骤 {i} 使用了未知工具: {step.get('tool', '')}")

        return issues
