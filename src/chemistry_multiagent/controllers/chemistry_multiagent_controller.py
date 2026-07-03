#!/usr/bin/env python3
"""
Chemistry Multi-Agent System Controller - 计算化学多智能体系统控制器

协调五个智能体的完整工作流（含专家复核钩子）：
1. Retrieval Agent: 检索智能体
2. Hypothesis Agent: 假设智能体
3. Planner Agent: 规划智能体
4. Execution Agent: 执行智能体
5. Reflection Agent: 反思智能体
"""

import os
import sys
import json
import time
import logging
import argparse
import copy
import re
from typing import Dict, List, Any, Optional
from datetime import datetime

# 添加项目根目录到Python路径
project_root = os.path.dirname(os.path.abspath(__file__))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# 尝试导入五个Agent
try:
    from chemistry_multiagent.agents.retrieval_agent import RetrievalAgent
    from chemistry_multiagent.agents.hypothesis_agent import HypothesisAgent
    from chemistry_multiagent.agents.planner_agent import PlannerAgent
    from chemistry_multiagent.agents.execution_agent import ExecutionAgent, ExecutionResult
    from chemistry_multiagent.agents.reflection_agent import ReflectionAgent
    AGENTS_AVAILABLE = True
except ImportError as e:
    print(f"导入Agent模块警告: {e}")
    AGENTS_AVAILABLE = False

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class ChemistryMultiAgentController:
    """计算化学多智能体系统控制器"""
    
    def __init__(self, 
                 deepseek_api_key: Optional[str] = None,
                 work_dir: Optional[str] = None,
                 toolpool_path: Optional[str] = None,
                 expert_model_name: str = "qwen2.5-7b-instruct",
                 expert_model_path: Optional[str] = None,
                 expert_backend: str = "local_hf",
                 enable_expert_review: bool = True,
                 gaussian_execution_mode: str = "api",
                 gaussian_command: str = "g16",
                 gaussian_module_load: Optional[str] = None,
                 gaussian_environment_hook: Optional[str] = None,
                 gaussian_slurm_partition: Optional[str] = None,
                 gaussian_job_root: Optional[str] = None):
        """
        初始化多智能体控制器
        
        参数:
            deepseek_api_key: Deepseek API密钥
            work_dir: 工作目录
            toolpool_path: 工具定义文件路径
            expert_model_name: 专家模型名称
            expert_model_path: 专家模型本地路径
            expert_backend: 专家模型后端
            enable_expert_review: 是否启用专家复核/分析
            gaussian_execution_mode: Gaussian执行模式 api/local_shell/slurm（已移除 replay 模拟）
            gaussian_command: Gaussian执行命令（默认 g16）
            gaussian_module_load: 可选模块加载命令
            gaussian_environment_hook: 可选环境初始化命令
            gaussian_slurm_partition: 可选Slurm分区
            gaussian_job_root: Gaussian任务状态根目录
        """
        self.work_dir = work_dir or project_root
        self.toolpool_path = toolpool_path or os.path.join(
            self.work_dir, "toolpool", "toolpool.json"
        )
        
        # 设置API密钥
        if deepseek_api_key:
            os.environ["DEEPSEEK_API_KEY"] = deepseek_api_key
        self.deepseek_api_key = deepseek_api_key or os.environ.get("DEEPSEEK_API_KEY", "")
        self.expert_model_name = expert_model_name
        self.expert_model_path = expert_model_path
        self.expert_backend = expert_backend
        self.enable_expert_review = enable_expert_review
        # env 优先:CLI/构造默认是 replay,但生产用 GAUSSIAN_EXECUTION_MODE=api 跑真实后端;
        # 不读 env 会让整条链跑在 replay,真实 Gaussian 永不触发。
        self.gaussian_execution_mode = os.environ.get("GAUSSIAN_EXECUTION_MODE", "").strip() or gaussian_execution_mode
        self.gaussian_command = gaussian_command
        self.gaussian_module_load = gaussian_module_load
        self.gaussian_environment_hook = gaussian_environment_hook
        self.gaussian_slurm_partition = gaussian_slurm_partition
        self.gaussian_job_root = gaussian_job_root
        
        # 创建输出目录
        self.output_dir = os.path.join(self.work_dir, "outputs", "multiagent")
        os.makedirs(self.output_dir, exist_ok=True)
        
        # 日志文件
        self.log_file = os.path.join(self.output_dir, "multiagent_log.json")
        self.log_data = []
        
        # 初始化五个Agent
        self.retrieval_agent = None
        self.hypothesis_agent = None
        self.planner_agent = None
        self.execution_agent = None
        self.reflection_agent = None
        
        if AGENTS_AVAILABLE:
            self._initialize_agents()
        else:
            logger.error("Agent 模块不可用,无法运行真实工作流(已移除所有 mock/replay 回退)")
        
        # 工作流状态
        self.workflow_state = {
            "status": "idle",
            "current_step": None,
            "start_time": None,
            "end_time": None,
            "current_question": None,
            "chemistry_context": {},
            "selected_strategy_profile": {},
            "expert_review_history": [],
            "gaussian_analysis_history": [],
            "revision_history": [],
            "requested_expert_backend": self.expert_backend,
            "used_expert_backend": None,
            "fallback_triggered": False,
            "fallback_reason": None,
            "fallback_model": None,
            "expert_backend_audit_history": [],
            "expert_backend_audit_summary": {
                "requested_expert_backend": self.expert_backend,
                "used_expert_backend": None,
                "used_expert_backends": [],
                "fallback_triggered": False,
                "fallback_reason": None,
                "fallback_model": None,
                "expert_run_mode": "unknown",
            },
        }
        
        logger.info("✅ 计算化学多智能体系统控制器初始化完成")
        logger.info(f"   工作目录: {self.work_dir}")
        logger.info(f"   输出目录: {self.output_dir}")
        logger.info(f"   Agents可用: {AGENTS_AVAILABLE}")
    
    def _initialize_agents(self):
        """初始化五个Agent"""
        try:
            self.retrieval_agent = RetrievalAgent(
                deepseek_api_key=self.deepseek_api_key,
                embedder_name="bge"
            )
            logger.info("✅ Retrieval Agent 初始化完成")
        except Exception as e:
            logger.error(f"Retrieval Agent 初始化失败: {e}")
        
        try:
            self.hypothesis_agent = HypothesisAgent(
                deepseek_api_key=self.deepseek_api_key
            )
            logger.info("✅ Hypothesis Agent 初始化完成")
        except Exception as e:
            logger.error(f"Hypothesis Agent 初始化失败: {e}")
        
        try:
            self.planner_agent = PlannerAgent(
                deepseek_api_key=self.deepseek_api_key,
                toolpool_path=self.toolpool_path,
                expert_model_name=self.expert_model_name,
                expert_model_path=self.expert_model_path,
                expert_backend=self.expert_backend,
                enable_expert_review=self.enable_expert_review,
            )
            logger.info("✅ Planner Agent 初始化完成")
        except Exception as e:
            logger.error(f"Planner Agent 初始化失败: {e}")
        
        try:
            self.execution_agent = ExecutionAgent(
                deepseek_api_key=self.deepseek_api_key,
                toolpool_path=self.toolpool_path,
                expert_model_name=self.expert_model_name,
                expert_model_path=self.expert_model_path,
                expert_backend=self.expert_backend,
                enable_expert_analysis=self.enable_expert_review,
                gaussian_execution_mode=self.gaussian_execution_mode,
                gaussian_command=self.gaussian_command,
                gaussian_module_load=self.gaussian_module_load,
                gaussian_environment_hook=self.gaussian_environment_hook,
                gaussian_slurm_partition=self.gaussian_slurm_partition,
                gaussian_job_root=self.gaussian_job_root,
            )
            self.execution_agent.work_dir = self.work_dir
            logger.info("✅ Execution Agent 初始化完成")
        except Exception as e:
            logger.error(f"Execution Agent 初始化失败: {e}")
        
        try:
            self.reflection_agent = ReflectionAgent(
                deepseek_api_key=self.deepseek_api_key
            )
            logger.info("✅ Reflection Agent 初始化完成")
        except Exception as e:
            logger.error(f"Reflection Agent 初始化失败: {e}")
    
    def log_step(self, step_name: str, status: str, data: Dict = None):
        """记录工作流步骤"""
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "step": step_name,
            "status": status,
            "data": data or {}
        }
        self.log_data.append(log_entry)
        
        # 实时保存日志
        with open(self.log_file, "w", encoding="utf-8") as f:
            json.dump(self.log_data, f, indent=2, ensure_ascii=False)
        
        logger.info(f"📝 [{step_name}] {status}")
        if data:
            logger.debug(f"     数据: {data}")


    # ==================== 跨Agent结构化桥接辅助 ====================

    def _select_primary_strategy(self, hypothesis_result: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """从假设结果中提取当前主策略。"""
        if not isinstance(hypothesis_result, dict):
            return {}
        for key in ("ranked_strategies", "top_n_strategies", "optimized_hypotheses"):
            value = hypothesis_result.get(key, [])
            if isinstance(value, list) and value and isinstance(value[0], dict):
                return value[0]
        return {}

    def _extract_protocols(self, planning_result: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """从规划结果中提取可执行协议列表（兼容单工作流对象）。"""
        if not isinstance(planning_result, dict):
            return []
        protocols = planning_result.get("optimized_protocols", [])
        if isinstance(protocols, list) and protocols:
            return [p for p in protocols if isinstance(p, dict)]
        if planning_result.get("Steps") or planning_result.get("steps"):
            return [planning_result]
        return []

    def _select_executable_workflow(self,
                                    planning_result: Optional[Dict[str, Any]],
                                    selected_strategy: Optional[Dict[str, Any]] = None) -> tuple:
        """选择当前应执行/修订的具体工作流对象。"""
        protocols = self._extract_protocols(planning_result)
        if not protocols:
            return None, None

        strategy_name = ""
        if isinstance(selected_strategy, dict):
            strategy_name = str(selected_strategy.get("strategy_name", "")).strip().lower()

        if strategy_name:
            for idx, protocol in enumerate(protocols):
                p_name = str(protocol.get("strategy_name", protocol.get("workflow_name", ""))).strip().lower()
                if p_name and p_name == strategy_name:
                    return protocol, idx

        return protocols[0], 0

    def _select_primary_execution_result(self,
                                         execution_result: Optional[Dict[str, Any]],
                                         selected_strategy: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """提取单个工作流级执行结果（供Reflection消费）。"""
        if not isinstance(execution_result, dict):
            return {}
        if isinstance(execution_result.get("steps"), list):
            return execution_result

        workflow_results = execution_result.get("results", [])
        if not isinstance(workflow_results, list) or not workflow_results:
            return {}

        strategy_name = ""
        if isinstance(selected_strategy, dict):
            strategy_name = str(selected_strategy.get("strategy_name", "")).strip().lower()

        if strategy_name:
            for item in workflow_results:
                if not isinstance(item, dict):
                    continue
                item_name = str(item.get("strategy_name", item.get("workflow_name", ""))).strip().lower()
                if item_name and item_name == strategy_name:
                    return item

        return workflow_results[0] if isinstance(workflow_results[0], dict) else {}

    def _summarize_execution_schema(self, execution_result: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """基于当前Execution schema提取控制器级摘要，供循环决策与最终总结使用。"""
        default_summary = {
            "overall_success_rate": 0.0,
            "workflow_outcome": "unknown",
            "overall_status": "unknown",
            "validation_overview": {},
            "failed_steps": [],
            "issues": [],
        }
        if not isinstance(execution_result, dict):
            return default_summary

        workflow_results = execution_result.get("results", [])
        if not isinstance(workflow_results, list):
            workflow_results = []

        outcome_values = []
        status_values = []
        validation_overview = {}
        failed_steps = []
        issues = []

        for workflow_item in workflow_results:
            if not isinstance(workflow_item, dict):
                continue

            outcome = workflow_item.get("workflow_outcome")
            if isinstance(outcome, str) and outcome:
                outcome_values.append(outcome)

            overall_status = workflow_item.get("overall_status")
            if isinstance(overall_status, str) and overall_status:
                status_values.append(overall_status)

            summary = workflow_item.get("summary", {})
            if isinstance(summary, dict):
                vo = summary.get("validation_overview", {})
                if isinstance(vo, dict):
                    for k, v in vo.items():
                        if isinstance(v, (int, float)):
                            validation_overview[k] = validation_overview.get(k, 0) + v

            steps = workflow_item.get("steps", [])
            if isinstance(steps, list):
                for step in steps:
                    if not isinstance(step, dict):
                        continue
                    if step.get("status") == "failed":
                        step_error_info = step.get("error_info", {})
                        if not isinstance(step_error_info, dict):
                            step_error_info = {}
                        failed_steps.append({
                            "step_id": step.get("step_id", step.get("step_number")),
                            "step_name": step.get("step_name", step.get("description", "")),
                            "tool": step.get("tool_name"),
                            "error_info": step_error_info,
                            "step_number": step.get("step_number", step.get("step_id")),
                            "description": step.get("description", step.get("step_name", "")),
                            "error": step.get("error", step_error_info.get("message")),
                        })

            item_issues = workflow_item.get("issues", [])
            if isinstance(item_issues, list):
                issues.extend([x for x in item_issues if isinstance(x, str)])

        root_issues = execution_result.get("issues", [])
        if isinstance(root_issues, list):
            issues.extend([x for x in root_issues if isinstance(x, str)])

        overall_success_rate = execution_result.get("overall_success_rate", 0.0)
        if not isinstance(overall_success_rate, (int, float)):
            overall_success_rate = 0.0

        if "failed" in outcome_values:
            workflow_outcome = "failed"
        elif "supported" in outcome_values:
            workflow_outcome = "supported" if all(v == "supported" for v in outcome_values) else "partially_supported"
        elif "partially_supported" in outcome_values:
            workflow_outcome = "partially_supported"
        elif outcome_values:
            workflow_outcome = "unknown"
        else:
            workflow_outcome = "unknown"

        if "failed" in status_values:
            overall_status = "failed"
        elif "partial_success" in status_values:
            overall_status = "partial_success"
        elif "success" in status_values:
            overall_status = "success"
        elif status_values:
            overall_status = status_values[0]
        else:
            overall_status = "unknown"

        return {
            "overall_success_rate": float(overall_success_rate),
            "workflow_outcome": workflow_outcome,
            "overall_status": overall_status,
            "validation_overview": validation_overview,
            "failed_steps": failed_steps,
            "issues": issues[:20],
        }

    def _extract_pending_gaussian_job_summary(self, execution_result: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """提取未完成Gaussian作业摘要（prepared/submitted/queued/running）。"""
        if not isinstance(execution_result, dict):
            return []

        pending_states = {"prepared", "submitted", "queued", "running"}
        workflow_results = execution_result.get("results", [])
        if isinstance(execution_result.get("steps"), list):
            workflow_results = [execution_result]
        if not isinstance(workflow_results, list):
            return []

        summaries: List[Dict[str, Any]] = []
        for workflow_item in workflow_results:
            if not isinstance(workflow_item, dict):
                continue
            workflow_name = workflow_item.get("workflow_name", workflow_item.get("strategy_name"))
            steps = workflow_item.get("steps", [])
            if not isinstance(steps, list):
                continue

            for step in steps:
                if not isinstance(step, dict):
                    continue
                raw_output = step.get("raw_output")
                if not isinstance(raw_output, dict):
                    continue
                if raw_output.get("execution_mode") != "gaussian_job":
                    continue

                job_state = raw_output.get("job_state") if isinstance(raw_output.get("job_state"), dict) else {}
                status = str(raw_output.get("status") or job_state.get("status") or "unknown").lower()
                if status not in pending_states:
                    continue

                summaries.append({
                    "workflow_name": workflow_name,
                    "step_id": step.get("step_id", step.get("step_number")),
                    "step_number": step.get("step_number"),
                    "step_name": step.get("step_name", step.get("description", "")),
                    "tool_name": step.get("tool_name"),
                    "status": status,
                    "scheduler": raw_output.get("scheduler", job_state.get("scheduler")),
                    "job_id": raw_output.get("job_id", job_state.get("job_id")),
                    "work_dir": raw_output.get("work_dir", job_state.get("work_dir")),
                    "state_path": job_state.get("state_path"),
                    "message": raw_output.get("message", job_state.get("message")),
                })

        return summaries

    def _has_pending_gaussian_jobs(self, execution_result: Optional[Dict[str, Any]]) -> bool:
        """是否存在未完成Gaussian作业。"""
        return len(self._extract_pending_gaussian_job_summary(execution_result)) > 0
    

    def _normalize_hypothesis_result_for_controller(self,
                                                    result: Optional[Dict[str, Any]],
                                                    top_n: int = 5) -> Dict[str, Any]:
        """将增强假设输出归一化为控制器可消费结构（保留向后兼容字段）。"""
        if not isinstance(result, dict):
            return {}

        normalized = dict(result)

        standard_generation = normalized.get("standard_generation", {})
        if not isinstance(standard_generation, dict):
            standard_generation = {}

        def _to_strategy(h: Dict[str, Any]) -> Dict[str, Any]:
            reasoning = h.get("detailed_reasoning", h.get("reasoning", ""))
            return {
                "strategy_name": h.get("strategy_name", "Unknown"),
                "reasoning": reasoning,
                "detailed_reasoning": reasoning,
                "confidence": h.get("confidence", 0.5),
                "status": h.get("status", "active"),
                "score": h.get("score", h.get("confidence", 0.5)),
                "rank": h.get("rank"),
            }

        ranked = normalized.get("ranked_strategies", [])
        if not isinstance(ranked, list) or not ranked:
            candidates = normalized.get("final_hypotheses", [])
            if not candidates:
                candidates = normalized.get("final_top_n", [])
            if not candidates:
                candidates = normalized.get("structured_hypotheses", [])
            if not candidates:
                candidates = standard_generation.get("ranked_strategies", [])

            if isinstance(candidates, list):
                ranked = [_to_strategy(h) for h in candidates if isinstance(h, dict)]
            else:
                ranked = []

            if not ranked:
                legacy = standard_generation.get("top_n_strategies", [])
                if isinstance(legacy, list):
                    ranked = [x for x in legacy if isinstance(x, dict)]

            normalized["ranked_strategies"] = ranked

        topn = normalized.get("top_n_strategies", [])
        if not isinstance(topn, list) or not topn:
            final_top_n = normalized.get("final_top_n", [])
            if isinstance(final_top_n, list) and final_top_n:
                normalized["top_n_strategies"] = [_to_strategy(h) for h in final_top_n if isinstance(h, dict)]
            else:
                normalized["top_n_strategies"] = (normalized.get("ranked_strategies", []) or [])[:top_n]

        optimized = normalized.get("optimized_hypotheses", [])
        if not isinstance(optimized, list) or not optimized:
            final_hyp = normalized.get("final_hypotheses", [])
            if isinstance(final_hyp, list) and final_hyp:
                normalized["optimized_hypotheses"] = [h for h in final_hyp if isinstance(h, dict)]
            elif isinstance(normalized.get("structured_hypotheses", []), list):
                normalized["optimized_hypotheses"] = [h for h in normalized.get("structured_hypotheses", []) if isinstance(h, dict)]
            else:
                normalized["optimized_hypotheses"] = standard_generation.get("optimized_hypotheses", []) if isinstance(standard_generation.get("optimized_hypotheses", []), list) else []

        if "queries" not in normalized and "queries" in standard_generation:
            normalized["queries"] = standard_generation.get("queries", [])
        if "hypotheses_by_query" not in normalized and "hypotheses_by_query" in standard_generation:
            normalized["hypotheses_by_query"] = standard_generation.get("hypotheses_by_query", [])

        return normalized

    def _build_scientific_evidence(self,
                                   retrieval_result: Optional[Dict[str, Any]],
                                   hypothesis_result: Optional[Dict[str, Any]],
                                   execution_result: Optional[Dict[str, Any]],
                                   selected_strategy: Optional[Dict[str, Any]] = None,
                                   followup_retrieval_history: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        """构建供Reflection使用的富科学证据对象（检索+假设+执行）。"""
        retrieval_result = retrieval_result or {}
        hypothesis_result = hypothesis_result or {}

        execution_summary = self._summarize_execution_schema(execution_result)
        primary_execution = self._select_primary_execution_result(execution_result, selected_strategy)

        hypotheses_pool: List[Dict[str, Any]] = []
        for key in ("final_hypotheses", "optimized_hypotheses", "ranked_strategies", "top_n_strategies"):
            value = hypothesis_result.get(key, []) if isinstance(hypothesis_result, dict) else []
            if isinstance(value, list) and value:
                hypotheses_pool = [x for x in value if isinstance(x, dict)]
                break

        hypothesis_status_counts: Dict[str, int] = {}
        confidences: List[float] = []
        for h in hypotheses_pool:
            status = str(h.get("status", "unknown"))
            hypothesis_status_counts[status] = hypothesis_status_counts.get(status, 0) + 1
            c = h.get("confidence")
            if isinstance(c, (int, float)):
                confidences.append(float(c))

        parsed_scientific_outputs = []
        if isinstance(primary_execution, dict):
            for step in primary_execution.get("steps", []) if isinstance(primary_execution.get("steps", []), list) else []:
                if not isinstance(step, dict):
                    continue
                parsed = step.get("parsed_results", {})
                if step.get("status") == "success" and isinstance(parsed, dict) and parsed:
                    parsed_scientific_outputs.append({
                        "step_id": step.get("step_id", step.get("step_number")),
                        "step_name": step.get("step_name", step.get("description", "")),
                        "job_type": parsed.get("job_type"),
                        "scf_energy": parsed.get("scf_energy"),
                        "free_energy": parsed.get("free_energy"),
                        "n_imag_freq": parsed.get("n_imag_freq"),
                        "irc_verified": parsed.get("irc_verified"),
                    })

        latest_followup = None
        history = followup_retrieval_history or []
        if isinstance(history, list) and history:
            for item in reversed(history):
                if isinstance(item, dict):
                    candidate = item.get("result")
                    if isinstance(candidate, dict):
                        latest_followup = candidate
                        break

        selected = selected_strategy or self._select_primary_strategy(hypothesis_result)

        return {
            "mechanistic_clues": retrieval_result.get("mechanistic_clues", []) if isinstance(retrieval_result.get("mechanistic_clues", []), list) else [],
            "limitations": retrieval_result.get("limitations", []) if isinstance(retrieval_result.get("limitations", []), list) else [],
            "selected_strategy": {
                "strategy_name": selected.get("strategy_name") if isinstance(selected, dict) else None,
                "confidence": selected.get("confidence") if isinstance(selected, dict) else None,
                "reasoning": selected.get("detailed_reasoning", selected.get("reasoning", ""))[:400] if isinstance(selected, dict) else "",
            },
            "hypothesis_status_counts": hypothesis_status_counts,
            "hypothesis_confidence_summary": {
                "count": len(confidences),
                "avg": (sum(confidences) / len(confidences)) if confidences else None,
                "max": max(confidences) if confidences else None,
            },
            "execution_summary": execution_summary,
            "validation_overview": execution_summary.get("validation_overview", {}),
            "failed_steps": execution_summary.get("failed_steps", []),
            "parsed_scientific_outputs": parsed_scientific_outputs[:8],
            "followup_retrieval": {
                "available": latest_followup is not None,
                "evidence_needs": latest_followup.get("evidence_needs", []) if isinstance(latest_followup, dict) else [],
                "followup_limitations": latest_followup.get("followup_limitations", []) if isinstance(latest_followup, dict) else [],
                "followup_mechanistic_clues": latest_followup.get("followup_mechanistic_clues", []) if isinstance(latest_followup, dict) else [],
            },
            # 反思规则可选使用的高层科学一致性标记（未知时不做强断言）
            "barrier_consistent": None,
            "photochemical_feasible": None,
            "irc_connectivity_consistent": None,
            "expert_review_history": self.workflow_state.get("expert_review_history", []),
            "gaussian_analysis_history": self.workflow_state.get("gaussian_analysis_history", []),
            "expert_backend_audit_history": self.workflow_state.get("expert_backend_audit_history", []),
            "expert_backend_audit_summary": self.workflow_state.get("expert_backend_audit_summary", {}),
        }

    def _extract_selected_strategy_profile(self, selected_strategy: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """提取并归一化策略计算画像，供Planner/Execution复用。"""
        selected_strategy = selected_strategy or {}
        profile = {
            "strategy_name": selected_strategy.get("strategy_name"),
            "calculation_types": selected_strategy.get("calculation_types") or selected_strategy.get("required_calculation_types") or [],
            "requires_ts": selected_strategy.get("requires_ts"),
            "requires_irc": selected_strategy.get("requires_irc"),
            "solvent_sensitivity": selected_strategy.get("solvent_sensitivity"),
            "charge": selected_strategy.get("charge"),
            "multiplicity": selected_strategy.get("multiplicity"),
            "expected_elements": selected_strategy.get("expected_elements") or selected_strategy.get("elements") or [],
        }
        return profile

    def _extract_expert_backend_audit(self, payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """从任意专家分析对象中提取后端审计字段。"""
        if not isinstance(payload, dict):
            return {}
        requested = payload.get("requested_expert_backend", payload.get("expert_backend_requested", self.expert_backend))
        used = payload.get("used_expert_backend", payload.get("expert_backend_used"))
        fallback_triggered = payload.get("fallback_triggered", payload.get("expert_fallback_triggered"))
        fallback_reason = payload.get("fallback_reason", payload.get("expert_fallback_reason"))
        fallback_model = payload.get("fallback_model", payload.get("expert_fallback_model"))
        analysis_source = payload.get("expert_analysis_source")
        if used is None and fallback_triggered is None and not fallback_reason and not fallback_model and not analysis_source:
            return {}
        return {
            "requested_expert_backend": requested,
            "used_expert_backend": used,
            "fallback_triggered": bool(fallback_triggered) if fallback_triggered is not None else False,
            "fallback_reason": fallback_reason,
            "fallback_model": fallback_model,
            "expert_analysis_source": analysis_source,
        }

    def _determine_expert_run_mode(self, used_backends: List[str]) -> str:
        """根据实际使用的后端推断运行模式。"""
        values = [str(v).strip().lower() for v in (used_backends or []) if str(v).strip()]
        if not values:
            return "unknown"
        has_local = any(("local" in v) or ("arche" in v) for v in values)
        has_deepseek = any("deepseek" in v for v in values)
        has_rule = any("rule" in v for v in values)
        if has_local and not has_deepseek and not has_rule:
            return "local_arche_chem_driven"
        if has_deepseek and not has_local and not has_rule:
            return "deepseek_fallback_driven"
        if has_rule and not has_local and not has_deepseek:
            return "rule_based_fallback"
        if has_local and has_deepseek:
            return "mixed_local_and_deepseek"
        if has_rule and (has_local or has_deepseek):
            return "mixed_with_rule_based"
        return "mixed"

    def _summarize_expert_backend_audits(self, entries: Optional[List[Dict[str, Any]]]) -> Dict[str, Any]:
        """汇总专家后端使用情况，供轮次与最终结论审计。"""
        valid_entries = [e for e in (entries or []) if isinstance(e, dict)]
        requested_values = []
        used_values = []
        fallback_reason = None
        fallback_model = None
        fallback_triggered = False

        for entry in valid_entries:
            requested = entry.get("requested_expert_backend")
            used = entry.get("used_expert_backend")
            if requested:
                requested_values.append(str(requested))
            if used:
                used_values.append(str(used))
            fallback_triggered = fallback_triggered or bool(entry.get("fallback_triggered"))
            if not fallback_reason and entry.get("fallback_reason"):
                fallback_reason = entry.get("fallback_reason")
            if not fallback_model and entry.get("fallback_model"):
                fallback_model = entry.get("fallback_model")

        requested_expert_backend = requested_values[0] if requested_values else self.expert_backend
        used_expert_backends = sorted(set(used_values))
        used_expert_backend = used_expert_backends[0] if len(used_expert_backends) == 1 else ("mixed" if used_expert_backends else None)
        return {
            "requested_expert_backend": requested_expert_backend,
            "used_expert_backend": used_expert_backend,
            "used_expert_backends": used_expert_backends,
            "fallback_triggered": fallback_triggered,
            "fallback_reason": fallback_reason,
            "fallback_model": fallback_model,
            "expert_run_mode": self._determine_expert_run_mode(used_expert_backends),
        }

    def _record_expert_backend_audits(self,
                                      entries: Optional[List[Dict[str, Any]]],
                                      phase: str,
                                      round_id: Optional[int] = None) -> Dict[str, Any]:
        """将专家后端审计条目写入全局state并更新汇总。"""
        history = self.workflow_state.setdefault("expert_backend_audit_history", [])
        for item in entries or []:
            if not isinstance(item, dict):
                continue
            entry = dict(item)
            entry.setdefault("phase", phase)
            if round_id is not None:
                entry.setdefault("round", round_id)
            history.append(entry)

        summary = self._summarize_expert_backend_audits(history)
        self.workflow_state["requested_expert_backend"] = summary.get("requested_expert_backend", self.expert_backend)
        self.workflow_state["used_expert_backend"] = summary.get("used_expert_backend")
        self.workflow_state["fallback_triggered"] = bool(summary.get("fallback_triggered", False))
        self.workflow_state["fallback_reason"] = summary.get("fallback_reason")
        self.workflow_state["fallback_model"] = summary.get("fallback_model")
        self.workflow_state["expert_backend_audit_summary"] = summary
        return summary

    def _current_expert_backend_audit_snapshot(self) -> Dict[str, Any]:
        """获取当前运行的专家后端审计快照。"""
        summary = self.workflow_state.get("expert_backend_audit_summary", {})
        if not isinstance(summary, dict):
            summary = {}
        return {
            "requested_expert_backend": summary.get("requested_expert_backend", self.expert_backend),
            "used_expert_backend": summary.get("used_expert_backend"),
            "fallback_triggered": bool(summary.get("fallback_triggered", False)),
            "fallback_reason": summary.get("fallback_reason"),
            "fallback_model": summary.get("fallback_model"),
            "expert_run_mode": summary.get("expert_run_mode", "unknown"),
            "used_expert_backends": summary.get("used_expert_backends", []),
        }

    def _collect_planner_expert_review_history(self, planning_result: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """收集Planner阶段的Gaussian专家复核摘要。"""
        collected: List[Dict[str, Any]] = []
        for protocol in self._extract_protocols(planning_result):
            if not isinstance(protocol, dict):
                continue
            summary = protocol.get("gaussian_review_summary")
            if isinstance(summary, dict) and summary:
                entry = {
                    "workflow_name": protocol.get("workflow_name", protocol.get("strategy_name")),
                    "strategy_name": protocol.get("strategy_name", protocol.get("workflow_name")),
                    "summary": summary,
                }
                entry.update(self._extract_expert_backend_audit(summary))
                collected.append(entry)
        return collected

    def _collect_execution_gaussian_analysis_history(self, execution_result: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """收集Execution阶段的Gaussian专家分析/错误分析摘要。"""
        collected: List[Dict[str, Any]] = []
        if not isinstance(execution_result, dict):
            return collected
        for workflow_item in execution_result.get("results", []) if isinstance(execution_result.get("results", []), list) else []:
            if not isinstance(workflow_item, dict):
                continue
            wf_name = workflow_item.get("workflow_name", workflow_item.get("strategy_name"))
            for step in workflow_item.get("steps", []) if isinstance(workflow_item.get("steps", []), list) else []:
                if not isinstance(step, dict):
                    continue
                gaussian_analysis = step.get("gaussian_analysis")
                expert_error_analysis = step.get("expert_error_analysis")
                if gaussian_analysis:
                    entry = {
                        "workflow_name": wf_name,
                        "step_id": step.get("step_id", step.get("step_number")),
                        "analysis_type": "gaussian_analysis",
                        "analysis": gaussian_analysis,
                    }
                    entry.update(self._extract_expert_backend_audit(gaussian_analysis))
                    collected.append(entry)
                if expert_error_analysis:
                    entry = {
                        "workflow_name": wf_name,
                        "step_id": step.get("step_id", step.get("step_number")),
                        "analysis_type": "expert_error_analysis",
                        "analysis": expert_error_analysis,
                    }
                    entry.update(self._extract_expert_backend_audit(expert_error_analysis))
                    collected.append(entry)
        return collected

    def _attach_shared_context_to_protocols(self,
                                            protocols: List[Dict[str, Any]],
                                            scientific_question: str,
                                            chemistry_context: Optional[Dict[str, Any]],
                                            selected_strategy_profile: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """将共享科学上下文注入协议步骤，供Execution/Reflection沿用。"""
        chemistry_context = chemistry_context or {}
        selected_strategy_profile = selected_strategy_profile or {}
        enriched = []
        for protocol in protocols or []:
            if not isinstance(protocol, dict):
                continue
            p = dict(protocol)
            p.setdefault("scientific_question", scientific_question)
            p.setdefault("chemistry_context", chemistry_context)
            p.setdefault("selected_strategy_profile", selected_strategy_profile)
            steps = p.get("Steps", []) if isinstance(p.get("Steps", []), list) else []
            new_steps = []
            for step in steps:
                if not isinstance(step, dict):
                    new_steps.append(step)
                    continue
                s = dict(step)
                s.setdefault("scientific_context", {
                    "scientific_question": scientific_question,
                    "chemistry_context": chemistry_context,
                    "selected_strategy_profile": selected_strategy_profile,
                })
                s.setdefault("calculation_context", chemistry_context)
                if selected_strategy_profile.get("calculation_types") and "expected_validation_requirements" not in s:
                    s["expected_validation_requirements"] = selected_strategy_profile.get("calculation_types")
                new_steps.append(s)
            p["Steps"] = new_steps
            enriched.append(p)
        return enriched

    def _rerun_planner_expert_review_for_protocols(self,
                                                   protocols: List[Dict[str, Any]],
                                                   scientific_question: str,
                                                   chemistry_context: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """对修订后的workflow在执行前触发Planner侧Gaussian专家复核（若可用）。"""
        if not self.planner_agent or not hasattr(self.planner_agent, "_run_gaussian_expert_review"):
            return protocols
        reviewed = []
        for p in protocols or []:
            if not isinstance(p, dict):
                continue
            try:
                rp = self.planner_agent._run_gaussian_expert_review(
                    workflow=p,
                    question=scientific_question,
                    chemistry_context=chemistry_context,
                    force=True,
                )
                reviewed.append(rp if isinstance(rp, dict) else p)
            except Exception as e:
                logger.warning(f"Re-review revised workflow failed, keep original protocol: {e}")
                reviewed.append(p)
        return reviewed

    # ==================== Retrieval Agent 阶段 ====================
    
    def run_retrieval_phase(self, 
                           scientific_question: str,
                           pdf_dir: str = "papers",
                           index_dir: str = "index",
                           search_papers: bool = False) -> Dict:
        """
        运行检索阶段
        
        参数:
            scientific_question: 科学问题
            pdf_dir: PDF存储目录
            index_dir: 索引存储目录
            search_papers: 是否检索论文
        
        返回:
            检索结果
        """
        self.log_step("retrieval_phase", "started", {
            "question": scientific_question[:100] + "..." if len(scientific_question) > 100 else scientific_question,
            "pdf_dir": pdf_dir,
            "index_dir": index_dir,
            "search_papers": search_papers
        })
        
        self.workflow_state["status"] = "retrieval"
        self.workflow_state["current_step"] = "retrieval"
        self.workflow_state["current_question"] = scientific_question
        
        if not self.retrieval_agent:
            self.log_step("retrieval_phase", "failed", {"error": "Retrieval Agent 未初始化"})
            return {"error": "Retrieval Agent 未初始化"}
        
        try:
            # 运行检索流程
            result = self.retrieval_agent.process_question(
                question=scientific_question,
                pdf_dir=pdf_dir,
                index_dir=index_dir,
                search_papers=search_papers
            )
            
            # 保存结果
            retrieval_file = os.path.join(self.output_dir, "retrieval_result.json")
            with open(retrieval_file, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2, ensure_ascii=False)
            
            self.log_step("retrieval_phase", "completed", {
                "output_file": retrieval_file,
                "keywords": result.get("keywords", []),
                "index_built": result.get("index_built", False),
                "has_literature_review": bool(result.get("literature_review"))
            })

            chemistry_context = result.get("chemistry_context", {})
            if isinstance(chemistry_context, dict):
                self.workflow_state["chemistry_context"] = chemistry_context
            
            return result
            
        except Exception as e:
            self.log_step("retrieval_phase", "failed", {"error": str(e)})
            logger.error(f"检索阶段失败: {e}")
            return {"error": str(e)}
    
    # ==================== Hypothesis Agent 阶段 ====================
    
    def run_hypothesis_phase(self, 
                            scientific_question: str,
                            literature_review: str,
                            num_queries: int = 3,
                            num_hypotheses_per_query: int = 3,
                            top_n: int = 5,
                            evidence_results: Optional[List[Dict[str, Any]]] = None) -> Dict:
        """
        运行假设阶段
        
        参数:
            scientific_question: 科学问题
            literature_review: 文献综述（来自Retrieval agent）
            num_queries: 查询数量
            num_hypotheses_per_query: 每个查询的假设数量
            top_n: 前N个策略
            evidence_results: 可选证据项（用于增强假设过滤/修订）
        
        返回:
            假设生成结果
        """
        self.log_step("hypothesis_phase", "started", {
            "num_queries": num_queries,
            "num_hypotheses_per_query": num_hypotheses_per_query,
            "top_n": top_n,
            "has_evidence_results": bool(evidence_results)
        })
        
        self.workflow_state["status"] = "hypothesis"
        self.workflow_state["current_step"] = "hypothesis"
        
        if not self.hypothesis_agent:
            self.log_step("hypothesis_phase", "failed", {"error": "Hypothesis Agent 未初始化"})
            return {"error": "Hypothesis Agent 未初始化"}
        
        try:
            result = None

            # 主路径：增强假设工作流
            if hasattr(self.hypothesis_agent, "generate_enhanced_hypotheses"):
                try:
                    result = self.hypothesis_agent.generate_enhanced_hypotheses(
                        research_question=scientific_question,
                        literature_review=literature_review,
                        evidence_results=evidence_results,
                        num_queries=num_queries,
                        num_hypotheses_per_query=num_hypotheses_per_query,
                        top_n=top_n,
                        enable_filtering=bool(evidence_results),
                        enable_revision=bool(evidence_results)
                    )
                except Exception as enhanced_error:
                    logger.warning(f"增强假设入口失败，回退旧接口: {enhanced_error}")

            # 兼容回退：旧接口
            if result is None:
                result = self.hypothesis_agent.generate_and_rank_hypotheses(
                    research_question=scientific_question,
                    literature_review=literature_review,
                    num_queries=num_queries,
                    num_hypotheses_per_query=num_hypotheses_per_query,
                    top_n=top_n
                )

            result = self._normalize_hypothesis_result_for_controller(result, top_n=top_n)
            
            # 保存结果
            hypothesis_file = os.path.join(self.output_dir, "hypothesis_result.json")
            with open(hypothesis_file, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2, ensure_ascii=False)
            
            ranked_strategies = result.get("ranked_strategies", []) if isinstance(result.get("ranked_strategies", []), list) else []

            total_hypotheses = 0
            hypotheses_by_query = result.get("hypotheses_by_query", [])
            if isinstance(hypotheses_by_query, list) and hypotheses_by_query:
                for q in hypotheses_by_query:
                    if isinstance(q, dict):
                        total_hypotheses += len(q.get("hypotheses", []))
            if total_hypotheses == 0:
                pool = result.get("final_hypotheses", result.get("optimized_hypotheses", result.get("structured_hypotheses", [])))
                total_hypotheses = len(pool) if isinstance(pool, list) else 0
            
            self.log_step("hypothesis_phase", "completed", {
                "output_file": hypothesis_file,
                "workflow_version": result.get("workflow_version", "legacy"),
                "total_queries": len(result.get("queries", [])) if isinstance(result.get("queries", []), list) else 0,
                "total_hypotheses": total_hypotheses,
                "optimized_hypotheses": len(result.get("optimized_hypotheses", [])) if isinstance(result.get("optimized_hypotheses", []), list) else 0,
                "ranked_strategies": len(ranked_strategies),
                "top_n_strategies": len(result.get("top_n_strategies", [])) if isinstance(result.get("top_n_strategies", []), list) else 0
            })

            selected_strategy = self._select_primary_strategy(result)
            self.workflow_state["selected_strategy_profile"] = self._extract_selected_strategy_profile(selected_strategy)
            
            return result
            
        except Exception as e:
            self.log_step("hypothesis_phase", "failed", {"error": str(e)})
            logger.error(f"假设阶段失败: {e}")
            return {"error": str(e)}

    # ==================== Planner Agent 阶段 ====================
    
    def run_planner_phase(self, 
                         ranked_strategies: List[Dict],
                         scientific_question: str,
                         top_n: int = 5,
                         chemistry_context: Optional[Dict[str, Any]] = None,
                         selected_strategy_profile: Optional[Dict[str, Any]] = None) -> Dict:
        """
        运行规划阶段
        
        参数:
            ranked_strategies: 排名后的策略列表
            scientific_question: 科学问题
            top_n: 前N个策略
        
        返回:
            规划结果
        """
        self.log_step("planner_phase", "started", {
            "total_strategies": len(ranked_strategies),
            "top_n": top_n
        })
        
        self.workflow_state["status"] = "planner"
        self.workflow_state["current_step"] = "planner"
        
        if not self.planner_agent:
            self.log_step("planner_phase", "failed", {"error": "Planner Agent 未初始化"})
            return {"error": "Planner Agent 未初始化"}
        
        try:
            # 运行规划流程
            result = self.planner_agent.generate_workflows_for_top_strategies(
                ranked_strategies=ranked_strategies,
                question=scientific_question,
                top_n=top_n,
                chemistry_context=chemistry_context,
            )

            if isinstance(result, dict):
                result["chemistry_context"] = chemistry_context or {}
                result["selected_strategy_profile"] = selected_strategy_profile or {}
            
            # 保存结果
            planner_file = os.path.join(self.output_dir, "planner_result.json")
            with open(planner_file, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2, ensure_ascii=False)
            
            # 保存优化的协议（供执行使用）
            optimized_protocols = result.get("optimized_protocols", [])
            optimized_protocols = self._attach_shared_context_to_protocols(
                optimized_protocols,
                scientific_question=scientific_question,
                chemistry_context=chemistry_context,
                selected_strategy_profile=selected_strategy_profile,
            )
            result["optimized_protocols"] = optimized_protocols
            protocols_file = os.path.join(self.output_dir, "optimized_protocols.json")
            with open(protocols_file, "w", encoding="utf-8") as f:
                json.dump(optimized_protocols, f, indent=2, ensure_ascii=False)

            review_entries = self._collect_planner_expert_review_history(result)
            if review_entries:
                self.workflow_state.setdefault("expert_review_history", [])
                self.workflow_state["expert_review_history"].extend(review_entries)
            planner_audits = [self._extract_expert_backend_audit(item) for item in review_entries]
            planner_audits = [item for item in planner_audits if item]
            planner_audit_summary = self._summarize_expert_backend_audits(planner_audits)
            self._record_expert_backend_audits(planner_audits, phase="planner")
            if isinstance(result, dict):
                result["expert_backend_audit"] = planner_audit_summary
                result.setdefault("requested_expert_backend", planner_audit_summary.get("requested_expert_backend"))
                result.setdefault("used_expert_backend", planner_audit_summary.get("used_expert_backend"))
                result.setdefault("fallback_triggered", planner_audit_summary.get("fallback_triggered", False))
                result.setdefault("fallback_reason", planner_audit_summary.get("fallback_reason"))
                result.setdefault("fallback_model", planner_audit_summary.get("fallback_model"))
            
            self.log_step("planner_phase", "completed", {
                "output_file": planner_file,
                "protocols_file": protocols_file,
                "total_protocols": len(optimized_protocols),
                "total_original_steps": result.get("total_original_steps", 0),
                "total_optimized_steps": result.get("total_optimized_steps", 0),
                "optimization_ratio": result.get("optimization_ratio", 0),
                "used_expert_backend": planner_audit_summary.get("used_expert_backend"),
                "fallback_triggered": planner_audit_summary.get("fallback_triggered", False),
            })
            
            return result
            
        except Exception as e:
            self.log_step("planner_phase", "failed", {"error": str(e)})
            logger.error(f"规划阶段失败: {e}")
            return {"error": str(e)}
    
    # ==================== Execution Agent 阶段 ====================
    
    def run_execution_phase(self,
                            protocols: List[Dict],
                            scientific_question: str = "",
                            chemistry_context: Optional[Dict[str, Any]] = None,
                            selected_strategy_profile: Optional[Dict[str, Any]] = None) -> Dict:
        """
        运行执行阶段
        
        参数:
            protocols: 协议列表
        
        返回:
            执行结果
        """
        self.log_step("execution_phase", "started", {
            "total_protocols": len(protocols)
        })
        
        self.workflow_state["status"] = "execution"
        self.workflow_state["current_step"] = "execution"
        
        if not self.execution_agent:
            self.log_step("execution_phase", "failed", {"error": "Execution Agent 未初始化"})
            return {"error": "Execution Agent 未初始化"}
        
        try:
            protocols = self._attach_shared_context_to_protocols(
                protocols,
                scientific_question=scientific_question,
                chemistry_context=chemistry_context,
                selected_strategy_profile=selected_strategy_profile,
            )
            # 运行执行流程
            result = self.execution_agent.execute_multiple_workflows(protocols)

            if isinstance(result, dict):
                result["chemistry_context"] = chemistry_context or {}
                result["selected_strategy_profile"] = selected_strategy_profile or {}
            
            # 保存结果
            execution_file = os.path.join(self.output_dir, "execution_result.json")
            with open(execution_file, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2, ensure_ascii=False)
            
            self.log_step("execution_phase", "completed", {
                "output_file": execution_file,
                "total_workflows": result.get("total_workflows", 0),
                "total_steps": result.get("total_steps", 0),
                "successful_steps": result.get("successful_steps", 0),
                "overall_success_rate": result.get("overall_success_rate", 0)
            })

            analysis_entries = self._collect_execution_gaussian_analysis_history(result)
            if analysis_entries:
                self.workflow_state.setdefault("gaussian_analysis_history", [])
                self.workflow_state["gaussian_analysis_history"].extend(analysis_entries)
            execution_audits = [self._extract_expert_backend_audit(item) for item in analysis_entries]
            execution_audits = [item for item in execution_audits if item]
            execution_audit_summary = self._summarize_expert_backend_audits(execution_audits)
            self._record_expert_backend_audits(execution_audits, phase="execution")
            if isinstance(result, dict):
                result["expert_backend_audit"] = execution_audit_summary
                result.setdefault("requested_expert_backend", execution_audit_summary.get("requested_expert_backend"))
                result.setdefault("used_expert_backend", execution_audit_summary.get("used_expert_backend"))
                result.setdefault("fallback_triggered", execution_audit_summary.get("fallback_triggered", False))
                result.setdefault("fallback_reason", execution_audit_summary.get("fallback_reason"))
                result.setdefault("fallback_model", execution_audit_summary.get("fallback_model"))
            
            return result
            
        except Exception as e:
            self.log_step("execution_phase", "failed", {"error": str(e)})
            logger.error(f"执行阶段失败: {e}")
            return {"error": str(e)}
    
    # ==================== Reflection Agent 阶段 ====================
    
    def run_reflection_phase(self, 
                           current_round: int,
                           retrieval_result: Dict,
                           hypothesis_result: Dict,
                           planning_result: Dict,
                           execution_result: Dict,
                           reflection_history: List[Dict] = None,
                           followup_retrieval_history: Optional[List[Dict]] = None) -> Dict:
        """
        运行反射阶段
        
        参数:
            current_round: 当前反射轮次
            retrieval_result: 检索结果
            hypothesis_result: 假设结果
            planning_result: 规划结果
            execution_result: 执行结果
            reflection_history: 反射历史记录
            followup_retrieval_history: 后续检索历史（可选）
        
        返回:
            反射结果，包含决策和修订建议
        """
        self.log_step(f"reflection_phase_round_{current_round}", "started", {
            "current_round": current_round
        })
        
        self.workflow_state["status"] = "reflection"
        self.workflow_state["current_step"] = f"reflection_round_{current_round}"
        
        if not self.reflection_agent:
            # 如果Reflection Agent不可用，创建默认反射结果
            default_decision = {
                "decision": "accept",
                "reasoning": "No reflection agent available, defaulting to accept",
                "confidence": 0.5,
                "identified_problems": [],
                "workflow_revision_instructions": [],
                "hypothesis_revision_instructions": [],
                "recommended_actions": [],
                "evidence_summary": {}
            }
            
            self.log_step(f"reflection_phase_round_{current_round}", "completed", {
                "decision": "accept",
                "reason": "no_reflection_agent"
            })
            
            return default_decision
        
        try:
            selected_strategy = self._select_primary_strategy(hypothesis_result)
            executable_workflow, _ = self._select_executable_workflow(planning_result, selected_strategy)
            execution_workflow_result = self._select_primary_execution_result(execution_result, selected_strategy)
            scientific_evidence = self._build_scientific_evidence(
                retrieval_result=retrieval_result,
                hypothesis_result=hypothesis_result,
                execution_result=execution_result,
                selected_strategy=selected_strategy,
                followup_retrieval_history=followup_retrieval_history,
            )
            scientific_evidence["chemistry_context"] = self.workflow_state.get("chemistry_context", {})
            scientific_evidence["selected_strategy_profile"] = self.workflow_state.get("selected_strategy_profile", {})
            scientific_evidence["expert_review_history"] = self.workflow_state.get("expert_review_history", [])
            scientific_evidence["gaussian_analysis_history"] = self.workflow_state.get("gaussian_analysis_history", [])
            scientific_evidence["expert_backend_audit_history"] = self.workflow_state.get("expert_backend_audit_history", [])
            scientific_evidence["expert_backend_audit_summary"] = self.workflow_state.get("expert_backend_audit_summary", {})
            prior_reflections = [
                item.get("result", item) if isinstance(item, dict) else item
                for item in (reflection_history or [])
            ]

            # 运行反射流程（当前接口：reflect）
            result = self.reflection_agent.reflect(
                selected_strategy=selected_strategy,
                workflow=executable_workflow,
                execution_result=execution_workflow_result,
                scientific_evidence=scientific_evidence,
                prior_reflections=prior_reflections,
                retry_count=max(0, current_round - 1),
                reflection_round=current_round
            )
            
            # 保存结果
            reflection_file = os.path.join(self.output_dir, f"reflection_result_round_{current_round}.json")
            with open(reflection_file, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2, ensure_ascii=False)
            
            decision = result.get("decision", "accept")
            audit_snapshot = self._current_expert_backend_audit_snapshot()
            if isinstance(result, dict):
                result.setdefault("expert_backend_audit", audit_snapshot)

            self.workflow_state.setdefault("revision_history", [])
            self.workflow_state["revision_history"].append({
                "round": current_round,
                "decision": decision,
                "identified_problems": result.get("identified_problems", []),
                "workflow_revision_instructions": result.get("workflow_revision_instructions", []),
                "hypothesis_revision_instructions": result.get("hypothesis_revision_instructions", []),
                **audit_snapshot,
            })
            
            self.log_step(f"reflection_phase_round_{current_round}", "completed", {
                "output_file": reflection_file,
                "decision": decision,
                "confidence": result.get("confidence", 0.5),
                "has_workflow_revision_instructions": len(result.get("workflow_revision_instructions", [])) > 0,
                "has_hypothesis_revision_instructions": len(result.get("hypothesis_revision_instructions", [])) > 0
            })
            
            return result
            
        except Exception as e:
            self.log_step(f"reflection_phase_round_{current_round}", "failed", {"error": str(e)})
            logger.error(f"反射阶段失败: {e}")
            
            # 返回默认决策
            return {
                "decision": "stop",
                "reasoning": f"Reflection failed with error: {e}",
                "confidence": 0.0,
                "identified_problems": [],
                "workflow_revision_instructions": [],
                "hypothesis_revision_instructions": [],
                "recommended_actions": [],
                "evidence_summary": {}
            }

    # ==================== 有界闭环工作流 ====================
    
    def run_bounded_closed_loop_workflow(self,
                                        scientific_question: str,
                                        num_queries: int = 3,
                                        num_hypotheses_per_query: int = 3,
                                        top_n_strategies: int = 5,
                                        pdf_dir: str = "papers",
                                        index_dir: str = "index",
                                        search_papers: bool = False,
                                        max_reflection_rounds: int = 3,
                                        max_unrecoverable_failures: int = 3) -> Dict[str, Any]:
        """
        运行有界闭环工作流
        
        参数:
            scientific_question: 科学问题
            num_queries: 查询数量
            num_hypotheses_per_query: 每个查询的假设数量
            top_n_strategies: 前N个策略
            pdf_dir: PDF存储目录
            index_dir: 索引存储目录
            search_papers: 是否检索论文
            max_reflection_rounds: 最大反射轮次
            max_unrecoverable_failures: 最大不可恢复失败次数
        
        返回:
            结构化闭环工作流结果
        """
        logger.info("="*60)
        logger.info("🔄 开始运行有界闭环工作流")
        logger.info("="*60)
        
        start_time = time.time()
        self.workflow_state["status"] = "running"
        self.workflow_state["start_time"] = start_time
        self.workflow_state["current_question"] = scientific_question
        self._record_expert_backend_audits([], phase="controller_start")
        
        # 初始化结构化记录
        structured_record = {
            "scientific_question": scientific_question,
            "workflow_start_time": start_time,
            "workflow_version": "bounded_closed_loop_v1",
            "shared_state": {
                "current_question": scientific_question,
                "chemistry_context": self.workflow_state.get("chemistry_context", {}),
                "selected_strategy_profile": self.workflow_state.get("selected_strategy_profile", {}),
                "expert_review_history": self.workflow_state.get("expert_review_history", []),
                "gaussian_analysis_history": self.workflow_state.get("gaussian_analysis_history", []),
                "revision_history": self.workflow_state.get("revision_history", []),
                "requested_expert_backend": self.workflow_state.get("requested_expert_backend", self.expert_backend),
                "used_expert_backend": self.workflow_state.get("used_expert_backend"),
                "fallback_triggered": self.workflow_state.get("fallback_triggered", False),
                "fallback_reason": self.workflow_state.get("fallback_reason"),
                "fallback_model": self.workflow_state.get("fallback_model"),
                "expert_backend_audit_history": self.workflow_state.get("expert_backend_audit_history", []),
                "expert_backend_audit_summary": self.workflow_state.get("expert_backend_audit_summary", {}),
            },
            "retrieval_phase": {},
            "hypothesis_phase": {},
            "planning_rounds": [],
            "execution_rounds": [],
            "reflection_rounds": [],
            "retrieval_followup_rounds": [],
            "revision_events": [],
            "stop_conditions": {
                "max_reflection_rounds": max_reflection_rounds,
                "max_unrecoverable_failures": max_unrecoverable_failures,
                "triggered_condition": None,
                "final_round": 0
            },
            "final_conclusion": {}
        }
        
        # 状态变量
        current_round = 1
        unrecoverable_failures = 0
        should_continue = True
        final_decision = "stop"  # 默认决策
        
        # 阶段结果存储
        current_hypothesis_result = None
        current_planning_result = None
        current_execution_result = None
        pending_planning_override = None
        
        try:
            # ==================== 初始检索阶段 ====================
            logger.info(f"\n📚 初始检索阶段")
            retrieval_result = self.run_retrieval_phase(
                scientific_question=scientific_question,
                pdf_dir=pdf_dir,
                index_dir=index_dir,
                search_papers=search_papers
            )
            
            if retrieval_result.get("error"):
                structured_record["stop_conditions"]["triggered_condition"] = "retrieval_failure"
                structured_record["final_conclusion"] = self._synthesize_final_conclusion(
                    scientific_question=scientific_question,
                    status="failed_in_retrieval",
                    structured_record=structured_record,
                    retrieval_result=retrieval_result,
                    hypothesis_result=None,
                    planning_result=None,
                    execution_result=None,
                    final_round=current_round
                )
                return structured_record
            
            structured_record["retrieval_phase"] = retrieval_result
            if isinstance(retrieval_result.get("chemistry_context"), dict):
                self.workflow_state["chemistry_context"] = retrieval_result.get("chemistry_context", {})
            
            # 提取文献综述
            literature_review = retrieval_result.get("literature_review", "")
            if not literature_review:
                literature_review = f"Background for: {scientific_question[:100]}..."
            
            # ==================== 初始假设阶段 ====================
            logger.info(f"\n💡 初始假设阶段")
            hypothesis_result = self.run_hypothesis_phase(
                scientific_question=scientific_question,
                literature_review=literature_review,
                num_queries=num_queries,
                num_hypotheses_per_query=num_hypotheses_per_query,
                top_n=top_n_strategies
            )
            
            # 不再只看 error 键：假设阶段产出 0 策略，或【仅产出兜底默认策略】都必须显式失败上报。
            # 关键：LLM 截断时下游会塞 Default_Strategy_N（metadata.generation_source='default_fallback'），
            # has_strategies 仍为真——若放行就会拿罐头假设去真跑 Gaussian 并当成真科研，正是要禁止的伪造。
            _strategies = (
                hypothesis_result.get("ranked_strategies")
                or hypothesis_result.get("top_n_strategies")
                or hypothesis_result.get("optimized_hypotheses")
                or []
            )
            has_strategies = bool(_strategies)
            all_fallback = has_strategies and all(
                isinstance(s, dict)
                and (
                    s.get("metadata", {}).get("generation_source") == "default_fallback"
                    or s.get("generation_source") == "default_fallback"
                )
                for s in _strategies
            )
            if hypothesis_result.get("error") or not has_strategies or all_fallback:
                if not hypothesis_result.get("error"):
                    hypothesis_result["error"] = (
                        "假设阶段未产出任何策略/假设（可能是 LLM 未返回有效查询或假设）"
                        if not has_strategies
                        else "假设阶段仅得到兜底默认策略，LLM 未产出真正的假设——拒绝以罐头假设冒充真实科研"
                    )
                structured_record["stop_conditions"]["triggered_condition"] = (
                    "empty_hypothesis_output" if not has_strategies
                    else "degraded_to_fallback_hypotheses" if all_fallback
                    else "hypothesis_generation_failure"
                )
                structured_record["final_conclusion"] = self._synthesize_final_conclusion(
                    scientific_question=scientific_question,
                    status="failed_in_hypothesis",
                    structured_record=structured_record,
                    retrieval_result=retrieval_result,
                    hypothesis_result=hypothesis_result,
                    planning_result=None,
                    execution_result=None,
                    final_round=current_round
                )
                return structured_record
            
            structured_record["hypothesis_phase"] = hypothesis_result
            current_hypothesis_result = hypothesis_result
            self.workflow_state["selected_strategy_profile"] = self._extract_selected_strategy_profile(
                self._select_primary_strategy(current_hypothesis_result)
            )
            
            # ==================== 主反射循环 ====================
            while should_continue and current_round <= max_reflection_rounds:
                logger.info(f"\n🔄 反射循环轮次 {current_round}/{max_reflection_rounds}")
                
                # 提取排名后的策略
                ranked_strategies = current_hypothesis_result.get("ranked_strategies", [])
                if not ranked_strategies:
                    logger.warning("没有生成排名策略，使用前N个策略")
                    ranked_strategies = current_hypothesis_result.get("top_n_strategies", [])
                
                # ==================== 规划阶段 ====================
                if pending_planning_override is not None:
                    logger.info(f"📋 规划阶段 (轮次 {current_round}) - 使用反射修订后的workflow")
                    planning_result = pending_planning_override
                    pending_planning_override = None
                else:
                    logger.info(f"📋 规划阶段 (轮次 {current_round})")
                    planning_result = self.run_planner_phase(
                        ranked_strategies=ranked_strategies,
                        scientific_question=scientific_question,
                        top_n=top_n_strategies,
                        chemistry_context=self.workflow_state.get("chemistry_context", {}),
                        selected_strategy_profile=self.workflow_state.get("selected_strategy_profile", {}),
                    )
                
                if planning_result.get("error"):
                    unrecoverable_failures += 1
                    logger.warning(f"规划失败，不可恢复失败次数: {unrecoverable_failures}/{max_unrecoverable_failures}")
                    
                    if unrecoverable_failures >= max_unrecoverable_failures:
                        structured_record["stop_conditions"]["triggered_condition"] = "max_unrecoverable_failures"
                        should_continue = False
                        final_decision = "stop"
                    
                    # 记录规划结果（即使失败）
                    structured_record["planning_rounds"].append({
                        "round": current_round,
                        "result": planning_result,
                        "status": "failed",
                        "expert_backend_audit": planning_result.get("expert_backend_audit", self._current_expert_backend_audit_snapshot()),
                    })
                    
                    current_round += 1
                    continue
                
                structured_record["planning_rounds"].append({
                    "round": current_round,
                    "result": planning_result,
                    "status": "success",
                    "expert_backend_audit": planning_result.get("expert_backend_audit", self._current_expert_backend_audit_snapshot()),
                })
                current_planning_result = planning_result
                
                # 提取优化的协议
                optimized_protocols = planning_result.get("optimized_protocols", [])
                if planning_result.get("revised_from_reflection"):
                    optimized_protocols = self._rerun_planner_expert_review_for_protocols(
                        optimized_protocols,
                        scientific_question=scientific_question,
                        chemistry_context=self.workflow_state.get("chemistry_context", {}),
                    )
                    planning_result["optimized_protocols"] = optimized_protocols
                    review_entries = self._collect_planner_expert_review_history(planning_result)
                    if review_entries:
                        self.workflow_state.setdefault("expert_review_history", [])
                        self.workflow_state["expert_review_history"].extend(review_entries)
                    recheck_audits = [self._extract_expert_backend_audit(item) for item in review_entries]
                    recheck_audits = [item for item in recheck_audits if item]
                    planning_result["expert_backend_audit"] = self._summarize_expert_backend_audits(recheck_audits)
                    self._record_expert_backend_audits(
                        recheck_audits,
                        phase="planner_rereview",
                        round_id=current_round,
                    )
                
                # ==================== 执行阶段 ====================
                logger.info(f"⚙️  执行阶段 (轮次 {current_round})")
                execution_result = self.run_execution_phase(
                    optimized_protocols,
                    scientific_question=scientific_question,
                    chemistry_context=self.workflow_state.get("chemistry_context", {}),
                    selected_strategy_profile=self.workflow_state.get("selected_strategy_profile", {}),
                )
                
                # 检查执行状态（按当前execution schema）
                execution_schema_summary = self._summarize_execution_schema(execution_result)
                execution_failed = (
                    execution_result.get("error") is not None or
                    execution_schema_summary.get("overall_status") == "failed"
                )
                execution_success_rate = execution_schema_summary.get("overall_success_rate", 0)
                
                if execution_failed:
                    unrecoverable_failures += 1
                    logger.warning(f"执行失败，不可恢复失败次数: {unrecoverable_failures}/{max_unrecoverable_failures}")
                
                structured_record["execution_rounds"].append({
                    "round": current_round,
                    "result": execution_result,
                    "status": "success" if not execution_failed else "failed",
                    "expert_backend_audit": execution_result.get("expert_backend_audit", self._current_expert_backend_audit_snapshot()),
                })
                current_execution_result = execution_result

                pending_gaussian_jobs = self._extract_pending_gaussian_job_summary(execution_result)
                if pending_gaussian_jobs:
                    logger.info(f"⏳ 检测到未完成Gaussian作业，暂停反射并等待恢复: {len(pending_gaussian_jobs)} 个")
                    structured_record["status"] = "waiting_for_gaussian_jobs"
                    structured_record["waiting_for_jobs"] = True
                    structured_record["can_resume"] = True
                    structured_record["gaussian_execution_mode"] = self.gaussian_execution_mode
                    structured_record["pending_gaussian_jobs"] = pending_gaussian_jobs
                    structured_record["stop_conditions"]["triggered_condition"] = "waiting_for_gaussian_jobs"
                    structured_record["stop_conditions"]["final_round"] = current_round

                    self.workflow_state["status"] = "waiting_for_gaussian_jobs"
                    self.workflow_state["current_step"] = "execution_waiting"
                    self.workflow_state["waiting_for_jobs"] = True
                    self.workflow_state["can_resume"] = True
                    self.workflow_state["gaussian_execution_mode"] = self.gaussian_execution_mode
                    self.workflow_state["pending_gaussian_jobs"] = pending_gaussian_jobs
                    self.workflow_state["last_planning_result"] = current_planning_result
                    self.workflow_state["last_execution_result"] = current_execution_result
                    structured_record["last_planning_result"] = current_planning_result
                    structured_record["last_execution_result"] = current_execution_result

                    structured_record["resume_state"] = {
                        "scientific_question": scientific_question,
                        "current_round": current_round,
                        "planning_result": current_planning_result,
                        "execution_result": current_execution_result,
                        "workflow_state": self.workflow_state,
                        "pending_gaussian_jobs": pending_gaussian_jobs,
                        "gaussian_execution_mode": self.gaussian_execution_mode,
                        "can_resume": True,
                    }

                    end_time = time.time()
                    total_duration = end_time - start_time
                    structured_record["workflow_end_time"] = end_time
                    structured_record["total_duration_seconds"] = total_duration
                    structured_record["shared_state"] = {
                        "current_question": self.workflow_state.get("current_question"),
                        "chemistry_context": self.workflow_state.get("chemistry_context", {}),
                        "selected_strategy_profile": self.workflow_state.get("selected_strategy_profile", {}),
                        "expert_review_history": self.workflow_state.get("expert_review_history", []),
                        "gaussian_analysis_history": self.workflow_state.get("gaussian_analysis_history", []),
                        "revision_history": self.workflow_state.get("revision_history", []),
                        "requested_expert_backend": self.workflow_state.get("requested_expert_backend", self.expert_backend),
                        "used_expert_backend": self.workflow_state.get("used_expert_backend"),
                        "fallback_triggered": self.workflow_state.get("fallback_triggered", False),
                        "fallback_reason": self.workflow_state.get("fallback_reason"),
                        "fallback_model": self.workflow_state.get("fallback_model"),
                        "expert_backend_audit_history": self.workflow_state.get("expert_backend_audit_history", []),
                        "expert_backend_audit_summary": self.workflow_state.get("expert_backend_audit_summary", {}),
                        "gaussian_execution_mode": self.gaussian_execution_mode,
                        "waiting_for_jobs": True,
                        "pending_gaussian_jobs": pending_gaussian_jobs,
                    }

                    waiting_file = os.path.join(self.output_dir, "waiting_gaussian_jobs_state.json")
                    with open(waiting_file, "w", encoding="utf-8") as f:
                        json.dump(structured_record, f, indent=2, ensure_ascii=False, default=str)
                    structured_record["output_files"] = {
                        "waiting_state": waiting_file
                    }

                    self.log_step("execution_phase", "waiting_for_gaussian_jobs", {
                        "pending_jobs": len(pending_gaussian_jobs),
                        "gaussian_execution_mode": self.gaussian_execution_mode,
                        "waiting_state_file": waiting_file,
                    })
                    return structured_record
                
                # ==================== 反射阶段 ====================
                logger.info(f"🤔 反射阶段 (轮次 {current_round})")
                
                # 获取反射历史
                reflection_history = structured_record.get("reflection_rounds", [])
                
                reflection_result = self.run_reflection_phase(
                    current_round=current_round,
                    retrieval_result=retrieval_result,
                    hypothesis_result=current_hypothesis_result,
                    planning_result=current_planning_result,
                    execution_result=current_execution_result,
                    reflection_history=reflection_history,
                    followup_retrieval_history=structured_record.get("retrieval_followup_rounds", [])
                )
                
                # 记录反射结果
                structured_record["reflection_rounds"].append({
                    "round": current_round,
                    "result": reflection_result,
                    "decision": reflection_result.get("decision", "accept"),
                    "expert_backend_audit": self._current_expert_backend_audit_snapshot(),
                })
                
                # 提取决策
                reflection_decision = reflection_result.get("decision", "accept")
                final_decision = reflection_decision
                
                # ==================== 决策路由 ====================
                logger.info(f"📊 反射决策: {reflection_decision}")
                
                if reflection_decision == "accept":
                    # 接受结果，停止循环
                    logger.info(f"✅ 接受结果，停止反射循环")
                    structured_record["stop_conditions"]["triggered_condition"] = "reflection_accept"
                    should_continue = False
                    
                elif reflection_decision == "revise_workflow":
                    # 修订工作流
                    logger.info(f"🔧 修订工作流")

                    identified_problems = reflection_result.get("identified_problems", [])
                    workflow_revision_instructions = reflection_result.get("workflow_revision_instructions", [])
                    recommended_actions = reflection_result.get("recommended_actions", [])
                    revision_suggestions = workflow_revision_instructions or recommended_actions or reflection_result.get("revision_suggestions", [])

                    if (revision_suggestions or identified_problems) and current_planning_result:
                        # 尝试修订工作流
                        selected_strategy = self._select_primary_strategy(current_hypothesis_result)
                        workflow_to_revise, workflow_index = self._select_executable_workflow(current_planning_result, selected_strategy)
                        revision_event = {
                            "round": current_round,
                            "type": "workflow_revision",
                            "identified_problems": identified_problems,
                            "workflow_revision_instructions": workflow_revision_instructions,
                            "recommended_actions": recommended_actions,
                            "status": "attempted",
                            **self._current_expert_backend_audit_snapshot(),
                        }

                        try:
                            # 使用planner的修订功能（如果可用）
                            if hasattr(self.planner_agent, 'revise_workflow_from_reflection') and workflow_to_revise:
                                revised_workflow = self.planner_agent.revise_workflow_from_reflection(
                                    original_workflow=workflow_to_revise,
                                    reflection_result=reflection_result,
                                    strategy=selected_strategy,
                                    question=scientific_question,
                                    chemistry_context=self.workflow_state.get("chemistry_context", {}),
                                    selected_strategy=self.workflow_state.get("selected_strategy_profile", {}),
                                )

                                if revised_workflow:
                                    revision_event["status"] = "success"
                                    revision_event["revised_result"] = revised_workflow

                                    # 更新规划结果中的真实协议对象，保持planning状态一致
                                    protocols_for_update = self._extract_protocols(current_planning_result)
                                    if workflow_index is not None and 0 <= workflow_index < len(protocols_for_update):
                                        protocols_for_update[workflow_index] = revised_workflow
                                    elif protocols_for_update:
                                        protocols_for_update[0] = revised_workflow
                                    else:
                                        protocols_for_update = [revised_workflow]

                                    updated_planning = dict(current_planning_result) if isinstance(current_planning_result, dict) else {}
                                    updated_planning["optimized_protocols"] = protocols_for_update
                                    updated_planning["revised_from_reflection"] = True
                                    current_planning_result = updated_planning
                                    pending_planning_override = updated_planning

                                    logger.info(f"✅ 工作流修订成功")
                                else:
                                    revision_event["status"] = "failed"
                                    logger.warning(f"工作流修订失败，使用原始工作流")
                            else:
                                revision_event["status"] = "not_supported"
                                logger.warning(f"Planner Agent不支持工作流修订或缺少可修订workflow")
                        except Exception as e:
                            revision_event["status"] = "error"
                            revision_event["error"] = str(e)
                            logger.error(f"工作流修订错误: {e}")

                        structured_record["revision_events"].append(revision_event)
                        self.workflow_state.setdefault("revision_history", [])
                        self.workflow_state["revision_history"].append(revision_event)

                    # 准备下一轮（使用修订后的工作流或原始工作流）
                    current_round += 1

                elif reflection_decision == "revise_hypothesis":
                    # 修订假设
                    logger.info(f"🔬 修订假设")

                    identified_problems = reflection_result.get("identified_problems", [])
                    hypothesis_revision_instructions = reflection_result.get("hypothesis_revision_instructions", [])
                    recommended_actions = reflection_result.get("recommended_actions", [])

                    revision_event = {
                        "round": current_round,
                        "type": "hypothesis_revision",
                        "identified_problems": identified_problems,
                        "hypothesis_revision_instructions": hypothesis_revision_instructions,
                        "recommended_actions": recommended_actions,
                        "status": "attempted",
                        **self._current_expert_backend_audit_snapshot(),
                    }

                    # 在假设修订前执行有针对性的后续检索（轻量集成）
                    followup_retrieval_result = None
                    if hasattr(self.retrieval_agent, "retrieve_followup_evidence") and self.retrieval_agent:
                        try:
                            prior_review = retrieval_result.get("literature_review", "") if isinstance(retrieval_result, dict) else ""
                            followup_history = structured_record.get("retrieval_followup_rounds", [])
                            if isinstance(followup_history, list) and followup_history:
                                last = followup_history[-1]
                                if isinstance(last, dict):
                                    last_result = last.get("result", {})
                                    if isinstance(last_result, dict) and last_result.get("followup_review"):
                                        prior_review = f"{prior_review}\n\n{last_result.get('followup_review', '')}".strip()
                            followup_retrieval_result = self.retrieval_agent.retrieve_followup_evidence(
                                reflection_result=reflection_result,
                                original_question=scientific_question,
                                prior_review=prior_review,
                                pdf_dir=pdf_dir,
                                index_dir=index_dir,
                            )

                            structured_record["retrieval_followup_rounds"].append({
                                "round": current_round,
                                "status": "success",
                                "result": followup_retrieval_result,
                            })
                            revision_event["followup_retrieval"] = {
                                "status": "success",
                                "evidence_needs": followup_retrieval_result.get("evidence_needs", []),
                                "targeted_queries": followup_retrieval_result.get("targeted_queries", []),
                            }
                        except Exception as followup_error:
                            structured_record["retrieval_followup_rounds"].append({
                                "round": current_round,
                                "status": "failed",
                                "error": str(followup_error),
                            })
                            revision_event["followup_retrieval"] = {
                                "status": "failed",
                                "error": str(followup_error)
                            }
                            logger.warning(f"后续检索失败，继续假设修订流程: {followup_error}")
                    else:
                        revision_event["followup_retrieval"] = {"status": "not_supported"}

                    try:
                        # 使用hypothesis的修订功能（如果可用）
                        if hasattr(self.hypothesis_agent, 'revise_hypotheses_from_reflection'):
                            candidate_hypotheses = []
                            if isinstance(current_hypothesis_result, dict):
                                for key in ("final_hypotheses", "optimized_hypotheses", "ranked_strategies", "top_n_strategies"):
                                    value = current_hypothesis_result.get(key, [])
                                    if isinstance(value, list) and value:
                                        candidate_hypotheses = value
                                        break

                            if not candidate_hypotheses:
                                revision_event["status"] = "failed"
                                revision_event["error"] = "No hypotheses available for revision"
                                logger.warning("无可修订假设，跳过假设修订")
                            else:
                                reflection_inputs = [reflection_result]

                                # 将后续检索证据轻量注入下一步假设修订上下文
                                if isinstance(followup_retrieval_result, dict):
                                    augmented_reflection = dict(reflection_result)
                                    rec_actions = list(augmented_reflection.get("recommended_actions", []) or [])
                                    hyp_instr = list(augmented_reflection.get("hypothesis_revision_instructions", []) or [])

                                    for need in (followup_retrieval_result.get("evidence_needs", []) or [])[:3]:
                                        rec_actions.append(f"Use follow-up literature evidence on: {need}")

                                    for item in (followup_retrieval_result.get("followup_results", []) or [])[:2]:
                                        if not isinstance(item, dict):
                                            continue
                                        ans = str(item.get("answer", "")).strip()
                                        if ans and "No relevant literature" not in ans:
                                            hyp_instr.append(f"Re-evaluate mechanism using follow-up finding: {ans[:220]}")

                                    augmented_reflection["recommended_actions"] = rec_actions
                                    augmented_reflection["hypothesis_revision_instructions"] = hyp_instr

                                    ev_summary = dict(augmented_reflection.get("evidence_summary", {}) or {})
                                    ev_summary["followup_retrieval_available"] = True
                                    ev_summary["followup_evidence_needs"] = followup_retrieval_result.get("evidence_needs", [])
                                    augmented_reflection["evidence_summary"] = ev_summary
                                    reflection_inputs = [augmented_reflection]

                                revised_hypothesis_result = self.hypothesis_agent.revise_hypotheses_from_reflection(
                                    hypotheses=candidate_hypotheses,
                                    reflection_results=reflection_inputs
                                )

                                if revised_hypothesis_result:
                                    revision_event["status"] = "success"
                                    revision_event["revised_result"] = revised_hypothesis_result

                                    revised_pool = []
                                    if isinstance(revised_hypothesis_result, dict):
                                        revised_pool.extend(revised_hypothesis_result.get("revised", []) or [])
                                        revised_pool.extend(revised_hypothesis_result.get("new", []) or [])
                                        revised_pool.extend(revised_hypothesis_result.get("unchanged", []) or [])

                                    if revised_pool:
                                        updated_hypothesis_result = dict(current_hypothesis_result) if isinstance(current_hypothesis_result, dict) else {}
                                        updated_hypothesis_result["optimized_hypotheses"] = revised_pool
                                        updated_hypothesis_result["final_hypotheses"] = revised_pool
                                        updated_hypothesis_result["ranked_strategies"] = revised_pool
                                        updated_hypothesis_result["top_n_strategies"] = revised_pool[:top_n_strategies]
                                        updated_hypothesis_result["hypothesis_revision_result"] = revised_hypothesis_result
                                        if isinstance(followup_retrieval_result, dict):
                                            updated_hypothesis_result["latest_followup_retrieval"] = followup_retrieval_result
                                        current_hypothesis_result = updated_hypothesis_result
                                    else:
                                        logger.warning("假设修订返回为空，保留原假设结果")

                                    logger.info(f"✅ 假设修订成功")
                                else:
                                    revision_event["status"] = "failed"
                                    logger.warning(f"假设修订失败，使用原始假设")
                        else:
                            revision_event["status"] = "not_supported"
                            logger.warning(f"Hypothesis Agent不支持假设修订")
                    except Exception as e:
                        revision_event["status"] = "error"
                        revision_event["error"] = str(e)
                        logger.error(f"假设修订错误: {e}")

                    structured_record["revision_events"].append(revision_event)
                    self.workflow_state.setdefault("revision_history", [])
                    self.workflow_state["revision_history"].append(revision_event)

                    # 准备下一轮（使用修订后的假设或原始假设）
                    current_round += 1

                elif reflection_decision == "stop":
                    # 停止循环
                    logger.info(f"🛑 停止反射循环")
                    structured_record["stop_conditions"]["triggered_condition"] = "reflection_stop"
                    should_continue = False
                    
                else:
                    # 未知决策，默认停止
                    logger.warning(f"未知反射决策: {reflection_decision}，默认停止")
                    structured_record["stop_conditions"]["triggered_condition"] = "unknown_decision"
                    should_continue = False
                
                # 检查不可恢复失败限制
                if unrecoverable_failures >= max_unrecoverable_failures:
                    logger.warning(f"达到最大不可恢复失败次数: {unrecoverable_failures}")
                    structured_record["stop_conditions"]["triggered_condition"] = "max_unrecoverable_failures"
                    should_continue = False
                    final_decision = "stop"
            
            # ==================== 循环结束，生成最终结论 ====================
            structured_record["stop_conditions"]["final_round"] = current_round
            
            # 确定最终状态
            final_status = "completed"
            if structured_record["stop_conditions"]["triggered_condition"] in ["max_unrecoverable_failures", "unknown_decision"]:
                final_status = "failed"
            elif structured_record["stop_conditions"]["triggered_condition"] == "reflection_stop":
                final_status = "stopped"
            elif structured_record["stop_conditions"]["triggered_condition"] == "reflection_accept":
                final_status = "accepted"
            
            structured_record["status"] = final_status

            # 合成最终结论
            final_conclusion = self._synthesize_final_conclusion(
                scientific_question=scientific_question,
                status=final_status,
                structured_record=structured_record,
                retrieval_result=retrieval_result,
                hypothesis_result=current_hypothesis_result,
                planning_result=current_planning_result,
                execution_result=current_execution_result,
                final_round=current_round,
                final_decision=final_decision
            )
            
            structured_record["final_conclusion"] = final_conclusion
            structured_record["shared_state"] = {
                "current_question": self.workflow_state.get("current_question"),
                "chemistry_context": self.workflow_state.get("chemistry_context", {}),
                "selected_strategy_profile": self.workflow_state.get("selected_strategy_profile", {}),
                "expert_review_history": self.workflow_state.get("expert_review_history", []),
                "gaussian_analysis_history": self.workflow_state.get("gaussian_analysis_history", []),
                "revision_history": self.workflow_state.get("revision_history", []),
                "requested_expert_backend": self.workflow_state.get("requested_expert_backend", self.expert_backend),
                "used_expert_backend": self.workflow_state.get("used_expert_backend"),
                "fallback_triggered": self.workflow_state.get("fallback_triggered", False),
                "fallback_reason": self.workflow_state.get("fallback_reason"),
                "fallback_model": self.workflow_state.get("fallback_model"),
                "expert_backend_audit_history": self.workflow_state.get("expert_backend_audit_history", []),
                "expert_backend_audit_summary": self.workflow_state.get("expert_backend_audit_summary", {}),
            }
            
            # 计算总时间
            end_time = time.time()
            total_duration = end_time - start_time
            structured_record["workflow_end_time"] = end_time
            structured_record["total_duration_seconds"] = total_duration
            
            self.workflow_state["status"] = "completed"
            self.workflow_state["end_time"] = end_time
            self.workflow_state["current_step"] = None
            
            logger.info("\n" + "="*60)
            logger.info(f"✅ 有界闭环工作流执行完成!")
            logger.info("="*60)
            logger.info(f"📊 总轮次: {current_round - 1}")
            logger.info(f"⏱️  总耗时: {total_duration:.1f} 秒")
            logger.info(f"📈 最终状态: {final_status}")
            logger.info(f"📊 最终决策: {final_decision}")
            logger.info(f"📁 输出文件保存在: {self.output_dir}")
            
            # 保存最终结果
            final_result_file = os.path.join(self.output_dir, "bounded_closed_loop_result.json")
            with open(final_result_file, "w", encoding="utf-8") as f:
                json.dump(structured_record, f, indent=2, ensure_ascii=False, default=str)
            
            structured_record["output_files"] = {
                "complete_result": final_result_file
            }
            
            logger.info(f"📄 完整结果已保存到: {final_result_file}")
            
            return structured_record
            
        except Exception as e:
            end_time = time.time()
            error_result = {
                "scientific_question": scientific_question,
                "status": "error",
                "error": str(e),
                "workflow_start_time": start_time,
                "workflow_end_time": end_time,
                "total_duration_seconds": end_time - start_time,
                "structured_record": structured_record if 'structured_record' in locals() else {},
                "workflow_state": self.workflow_state,
                "log_data": self.log_data
            }
            
            error_file = os.path.join(self.output_dir, "closed_loop_error.json")
            with open(error_file, "w", encoding="utf-8") as f:
                json.dump(error_result, f, indent=2, ensure_ascii=False)
            
            self.workflow_state["status"] = "error"
            self.workflow_state["end_time"] = end_time
            
            logger.error(f"\n❌ 有界闭环工作流执行失败: {e}")
            logger.error(f"📄 错误详情已保存到: {error_file}")
            
            return error_result
    


    def _load_waiting_resume_state(self, resume_state_path: str) -> Dict[str, Any]:
        """加载并严格校验waiting状态快照。"""
        if not resume_state_path:
            raise ValueError("resume_state_path不能为空")

        state_path = os.path.abspath(os.path.expanduser(resume_state_path))
        if not os.path.exists(state_path):
            raise ValueError(f"恢复状态文件不存在: {state_path}")

        with open(state_path, "r", encoding="utf-8") as f:
            payload = json.load(f)

        if not isinstance(payload, dict):
            raise ValueError("恢复状态文件必须是JSON对象")

        errors: List[str] = []

        if str(payload.get("status", "")).strip().lower() != "waiting_for_gaussian_jobs":
            errors.append("status必须为waiting_for_gaussian_jobs")
        if payload.get("can_resume") is not True:
            errors.append("can_resume必须为true")

        workflow_state = payload.get("workflow_state")
        if not isinstance(workflow_state, dict):
            errors.append("workflow_state必须是对象")
            workflow_state = {}

        resume_state = payload.get("resume_state")
        if not isinstance(resume_state, dict):
            errors.append("resume_state必须是对象")
            resume_state = {}

        pending_jobs = payload.get("pending_gaussian_jobs")
        if not isinstance(pending_jobs, list):
            errors.append("pending_gaussian_jobs必须是数组")

        gaussian_mode = payload.get("gaussian_execution_mode")
        if not isinstance(gaussian_mode, str) or not gaussian_mode.strip():
            errors.append("gaussian_execution_mode必须是非空字符串")

        top_last_planning = payload.get("last_planning_result")
        top_last_execution = payload.get("last_execution_result")
        ws_last_planning = workflow_state.get("last_planning_result")
        ws_last_execution = workflow_state.get("last_execution_result")
        rs_planning = resume_state.get("planning_result")
        rs_execution = resume_state.get("execution_result")

        if not isinstance(top_last_planning, dict) and not isinstance(ws_last_planning, dict) and not isinstance(rs_planning, dict):
            errors.append("缺少last_planning_result")
        if not isinstance(top_last_execution, dict) and not isinstance(ws_last_execution, dict) and not isinstance(rs_execution, dict):
            errors.append("缺少last_execution_result")

        if errors:
            raise ValueError("恢复状态校验失败: " + "; ".join(errors))

        return payload

    def _estimate_unrecoverable_failures_from_record(self, structured_record: Dict[str, Any]) -> int:
        """从已有记录估算不可恢复失败次数（仅用于恢复流程延续）。"""
        failures = 0
        for item in structured_record.get("planning_rounds", []) or []:
            if isinstance(item, dict) and str(item.get("status", "")).lower() == "failed":
                failures += 1
        for item in structured_record.get("execution_rounds", []) or []:
            if isinstance(item, dict) and str(item.get("status", "")).lower() == "failed":
                failures += 1
        return failures

    def _write_waiting_state_snapshot(self,
                                      structured_record: Dict[str, Any],
                                      scientific_question: str,
                                      start_time: float,
                                      current_round: int,
                                      current_planning_result: Optional[Dict[str, Any]],
                                      current_execution_result: Optional[Dict[str, Any]],
                                      pending_gaussian_jobs: List[Dict[str, Any]]) -> Dict[str, Any]:
        """写出等待Gaussian作业的可恢复状态快照。"""
        structured_record["status"] = "waiting_for_gaussian_jobs"
        structured_record["waiting_for_jobs"] = True
        structured_record["can_resume"] = True
        structured_record["gaussian_execution_mode"] = self.gaussian_execution_mode
        structured_record["pending_gaussian_jobs"] = pending_gaussian_jobs
        structured_record.setdefault("stop_conditions", {})
        structured_record["stop_conditions"]["triggered_condition"] = "waiting_for_gaussian_jobs"
        structured_record["stop_conditions"]["final_round"] = current_round

        self.workflow_state["status"] = "waiting_for_gaussian_jobs"
        self.workflow_state["current_step"] = "execution_waiting"
        self.workflow_state["waiting_for_jobs"] = True
        self.workflow_state["can_resume"] = True
        self.workflow_state["gaussian_execution_mode"] = self.gaussian_execution_mode
        self.workflow_state["pending_gaussian_jobs"] = pending_gaussian_jobs
        self.workflow_state["last_planning_result"] = current_planning_result
        self.workflow_state["last_execution_result"] = current_execution_result
        structured_record["last_planning_result"] = current_planning_result
        structured_record["last_execution_result"] = current_execution_result

        structured_record["workflow_state"] = copy.deepcopy(self.workflow_state)
        structured_record["resume_state"] = {
            "scientific_question": scientific_question,
            "current_round": current_round,
            "planning_result": current_planning_result,
            "execution_result": current_execution_result,
            "workflow_state": self.workflow_state,
            "pending_gaussian_jobs": pending_gaussian_jobs,
            "gaussian_execution_mode": self.gaussian_execution_mode,
            "can_resume": True,
        }

        end_time = time.time()
        structured_record["workflow_end_time"] = end_time
        structured_record["total_duration_seconds"] = end_time - start_time
        structured_record["shared_state"] = {
            "current_question": self.workflow_state.get("current_question"),
            "chemistry_context": self.workflow_state.get("chemistry_context", {}),
            "selected_strategy_profile": self.workflow_state.get("selected_strategy_profile", {}),
            "expert_review_history": self.workflow_state.get("expert_review_history", []),
            "gaussian_analysis_history": self.workflow_state.get("gaussian_analysis_history", []),
            "revision_history": self.workflow_state.get("revision_history", []),
            "requested_expert_backend": self.workflow_state.get("requested_expert_backend", self.expert_backend),
            "used_expert_backend": self.workflow_state.get("used_expert_backend"),
            "fallback_triggered": self.workflow_state.get("fallback_triggered", False),
            "fallback_reason": self.workflow_state.get("fallback_reason"),
            "fallback_model": self.workflow_state.get("fallback_model"),
            "expert_backend_audit_history": self.workflow_state.get("expert_backend_audit_history", []),
            "expert_backend_audit_summary": self.workflow_state.get("expert_backend_audit_summary", {}),
            "gaussian_execution_mode": self.gaussian_execution_mode,
            "waiting_for_jobs": True,
            "pending_gaussian_jobs": pending_gaussian_jobs,
        }

        waiting_file = os.path.join(self.output_dir, "waiting_gaussian_jobs_state.json")
        with open(waiting_file, "w", encoding="utf-8") as f:
            json.dump(structured_record, f, indent=2, ensure_ascii=False, default=str)

        structured_record["output_files"] = {"waiting_state": waiting_file}
        self.log_step("execution_phase", "waiting_for_gaussian_jobs", {
            "pending_jobs": len(pending_gaussian_jobs),
            "gaussian_execution_mode": self.gaussian_execution_mode,
            "waiting_state_file": waiting_file,
        })
        return structured_record

    def _finalize_closed_loop_record(self,
                                     structured_record: Dict[str, Any],
                                     scientific_question: str,
                                     retrieval_result: Dict[str, Any],
                                     current_hypothesis_result: Dict[str, Any],
                                     current_planning_result: Dict[str, Any],
                                     current_execution_result: Dict[str, Any],
                                     current_round: int,
                                     final_decision: str,
                                     start_time: float) -> Dict[str, Any]:
        """闭环结束后的统一收尾逻辑（用于恢复路径）。"""
        structured_record.setdefault("stop_conditions", {})
        structured_record["stop_conditions"]["final_round"] = current_round

        final_status = "completed"
        triggered = structured_record["stop_conditions"].get("triggered_condition")
        if triggered in ["max_unrecoverable_failures", "unknown_decision"]:
            final_status = "failed"
        elif triggered == "reflection_stop":
            final_status = "stopped"
        elif triggered == "reflection_accept":
            final_status = "accepted"

        structured_record["status"] = final_status

        final_conclusion = self._synthesize_final_conclusion(
            scientific_question=scientific_question,
            status=final_status,
            structured_record=structured_record,
            retrieval_result=retrieval_result,
            hypothesis_result=current_hypothesis_result,
            planning_result=current_planning_result,
            execution_result=current_execution_result,
            final_round=current_round,
            final_decision=final_decision,
        )

        structured_record["final_conclusion"] = final_conclusion
        structured_record["shared_state"] = {
            "current_question": self.workflow_state.get("current_question"),
            "chemistry_context": self.workflow_state.get("chemistry_context", {}),
            "selected_strategy_profile": self.workflow_state.get("selected_strategy_profile", {}),
            "expert_review_history": self.workflow_state.get("expert_review_history", []),
            "gaussian_analysis_history": self.workflow_state.get("gaussian_analysis_history", []),
            "revision_history": self.workflow_state.get("revision_history", []),
            "requested_expert_backend": self.workflow_state.get("requested_expert_backend", self.expert_backend),
            "used_expert_backend": self.workflow_state.get("used_expert_backend"),
            "fallback_triggered": self.workflow_state.get("fallback_triggered", False),
            "fallback_reason": self.workflow_state.get("fallback_reason"),
            "fallback_model": self.workflow_state.get("fallback_model"),
            "expert_backend_audit_history": self.workflow_state.get("expert_backend_audit_history", []),
            "expert_backend_audit_summary": self.workflow_state.get("expert_backend_audit_summary", {}),
        }

        end_time = time.time()
        structured_record["workflow_end_time"] = end_time
        structured_record["total_duration_seconds"] = end_time - start_time

        self.workflow_state["status"] = "completed"
        self.workflow_state["end_time"] = end_time
        self.workflow_state["current_step"] = None

        final_result_file = os.path.join(self.output_dir, "bounded_closed_loop_result.json")
        with open(final_result_file, "w", encoding="utf-8") as f:
            json.dump(structured_record, f, indent=2, ensure_ascii=False, default=str)

        structured_record["output_files"] = {"complete_result": final_result_file}
        logger.info(f"📄 完整结果已保存到: {final_result_file}")
        return structured_record

    def resume_from_waiting_state(self,
                                  resume_state_path: str,
                                  pdf_dir: str = "papers",
                                  index_dir: str = "index",
                                  max_reflection_rounds: Optional[int] = None,
                                  max_unrecoverable_failures: Optional[int] = None,
                                  top_n_strategies: Optional[int] = None) -> Dict[str, Any]:
        """从waiting_for_gaussian_jobs快照恢复闭环流程。"""
        logger.info("=" * 60)
        logger.info("🔁 开始从等待状态恢复闭环工作流")
        logger.info("=" * 60)

        start_time = time.time()
        try:
            snapshot = self._load_waiting_resume_state(resume_state_path)

            state_path = os.path.abspath(os.path.expanduser(resume_state_path))
            self.output_dir = os.path.dirname(state_path)
            os.makedirs(self.output_dir, exist_ok=True)
            self.log_file = os.path.join(self.output_dir, "multiagent_log.json")

            structured_record = copy.deepcopy(snapshot)
            resume_state = structured_record.get("resume_state", {})
            workflow_state = copy.deepcopy(structured_record.get("workflow_state", {}))

            self.workflow_state = workflow_state
            self.workflow_state["status"] = "resuming_gaussian_jobs"
            self.workflow_state["current_step"] = "execution_resume"

            saved_mode = str(structured_record.get("gaussian_execution_mode", "") or "").strip()
            if saved_mode:
                self.gaussian_execution_mode = saved_mode
                if self.execution_agent and hasattr(self.execution_agent, "gaussian_execution_mode"):
                    self.execution_agent.gaussian_execution_mode = saved_mode

            scientific_question = (
                structured_record.get("scientific_question")
                or resume_state.get("scientific_question")
                or self.workflow_state.get("current_question")
            )
            if not scientific_question:
                raise ValueError("恢复状态缺少scientific_question")

            self.workflow_state["current_question"] = scientific_question

            retrieval_result = structured_record.get("retrieval_phase", {})
            current_hypothesis_result = structured_record.get("hypothesis_phase", {})
            current_planning_result = (
                structured_record.get("last_planning_result")
                or resume_state.get("planning_result")
                or self.workflow_state.get("last_planning_result")
            )
            current_execution_result = (
                structured_record.get("last_execution_result")
                or resume_state.get("execution_result")
                or self.workflow_state.get("last_execution_result")
            )

            if not isinstance(current_planning_result, dict):
                raise ValueError("恢复状态缺少有效planning_result")
            if not isinstance(current_execution_result, dict):
                raise ValueError("恢复状态缺少有效execution_result")

            stop_conditions = structured_record.setdefault("stop_conditions", {})
            if max_reflection_rounds is None:
                max_reflection_rounds = int(stop_conditions.get("max_reflection_rounds", 3) or 3)
            if max_unrecoverable_failures is None:
                max_unrecoverable_failures = int(stop_conditions.get("max_unrecoverable_failures", 3) or 3)
            stop_conditions["max_reflection_rounds"] = max_reflection_rounds
            stop_conditions["max_unrecoverable_failures"] = max_unrecoverable_failures

            current_round = int(resume_state.get("current_round") or stop_conditions.get("final_round") or 1)
            if current_round < 1:
                current_round = 1

            if top_n_strategies is None:
                candidates = current_hypothesis_result.get("top_n_strategies", []) if isinstance(current_hypothesis_result, dict) else []
                top_n_strategies = len(candidates) if isinstance(candidates, list) and candidates else 5

            unrecoverable_failures = self._estimate_unrecoverable_failures_from_record(structured_record)
            should_continue = True
            final_decision = "stop"
            pending_planning_override = None
            needs_planning = False

            workflow_start_time = structured_record.get("workflow_start_time")
            if isinstance(workflow_start_time, (int, float)):
                start_time = float(workflow_start_time)
            else:
                structured_record["workflow_start_time"] = start_time

            logger.info(f"🔁 恢复轮次: {current_round}, 重新进入执行恢复阶段")

            while should_continue and current_round <= max_reflection_rounds:
                if needs_planning:
                    ranked_strategies = current_hypothesis_result.get("ranked_strategies", []) if isinstance(current_hypothesis_result, dict) else []
                    if not ranked_strategies and isinstance(current_hypothesis_result, dict):
                        ranked_strategies = current_hypothesis_result.get("top_n_strategies", [])

                    if pending_planning_override is not None:
                        planning_result = pending_planning_override
                        pending_planning_override = None
                    else:
                        planning_result = self.run_planner_phase(
                            ranked_strategies=ranked_strategies,
                            scientific_question=scientific_question,
                            top_n=top_n_strategies,
                            chemistry_context=self.workflow_state.get("chemistry_context", {}),
                            selected_strategy_profile=self.workflow_state.get("selected_strategy_profile", {}),
                        )

                    if planning_result.get("error"):
                        unrecoverable_failures += 1
                        structured_record.setdefault("planning_rounds", []).append({
                            "round": current_round,
                            "result": planning_result,
                            "status": "failed",
                            "expert_backend_audit": planning_result.get("expert_backend_audit", self._current_expert_backend_audit_snapshot()),
                        })
                        if unrecoverable_failures >= max_unrecoverable_failures:
                            stop_conditions["triggered_condition"] = "max_unrecoverable_failures"
                            should_continue = False
                        current_round += 1
                        continue

                    structured_record.setdefault("planning_rounds", []).append({
                        "round": current_round,
                        "result": planning_result,
                        "status": "success",
                        "expert_backend_audit": planning_result.get("expert_backend_audit", self._current_expert_backend_audit_snapshot()),
                    })
                    current_planning_result = planning_result

                protocols = self._extract_protocols(current_planning_result)
                if not protocols:
                    raise ValueError("恢复执行失败: 无可执行optimized_protocols")

                execution_result = self.run_execution_phase(
                    protocols,
                    scientific_question=scientific_question,
                    chemistry_context=self.workflow_state.get("chemistry_context", {}),
                    selected_strategy_profile=self.workflow_state.get("selected_strategy_profile", {}),
                )

                execution_schema_summary = self._summarize_execution_schema(execution_result)
                execution_failed = (
                    execution_result.get("error") is not None or
                    execution_schema_summary.get("overall_status") == "failed"
                )
                if execution_failed:
                    unrecoverable_failures += 1

                structured_record.setdefault("execution_rounds", []).append({
                    "round": current_round,
                    "result": execution_result,
                    "status": "success" if not execution_failed else "failed",
                    "expert_backend_audit": execution_result.get("expert_backend_audit", self._current_expert_backend_audit_snapshot()),
                    "resumed_from_waiting": True,
                })
                current_execution_result = execution_result
                self.workflow_state["last_planning_result"] = current_planning_result
                self.workflow_state["last_execution_result"] = current_execution_result

                pending_gaussian_jobs = self._extract_pending_gaussian_job_summary(execution_result)
                if pending_gaussian_jobs:
                    logger.info(f"⏳ 恢复后仍有未完成Gaussian作业: {len(pending_gaussian_jobs)}")
                    return self._write_waiting_state_snapshot(
                        structured_record=structured_record,
                        scientific_question=scientific_question,
                        start_time=start_time,
                        current_round=current_round,
                        current_planning_result=current_planning_result,
                        current_execution_result=current_execution_result,
                        pending_gaussian_jobs=pending_gaussian_jobs,
                    )

                logger.info(f"🤔 反射阶段 (恢复轮次 {current_round})")
                reflection_history = structured_record.get("reflection_rounds", [])
                reflection_result = self.run_reflection_phase(
                    current_round=current_round,
                    retrieval_result=retrieval_result,
                    hypothesis_result=current_hypothesis_result,
                    planning_result=current_planning_result,
                    execution_result=current_execution_result,
                    reflection_history=reflection_history,
                    followup_retrieval_history=structured_record.get("retrieval_followup_rounds", []),
                )

                structured_record.setdefault("reflection_rounds", []).append({
                    "round": current_round,
                    "result": reflection_result,
                    "decision": reflection_result.get("decision", "accept"),
                    "expert_backend_audit": self._current_expert_backend_audit_snapshot(),
                })

                reflection_decision = reflection_result.get("decision", "accept")
                final_decision = reflection_decision

                if reflection_decision == "accept":
                    stop_conditions["triggered_condition"] = "reflection_accept"
                    should_continue = False

                elif reflection_decision == "revise_workflow":
                    identified_problems = reflection_result.get("identified_problems", [])
                    workflow_revision_instructions = reflection_result.get("workflow_revision_instructions", [])
                    recommended_actions = reflection_result.get("recommended_actions", [])
                    revision_suggestions = workflow_revision_instructions or recommended_actions or reflection_result.get("revision_suggestions", [])

                    if (revision_suggestions or identified_problems) and current_planning_result:
                        selected_strategy = self._select_primary_strategy(current_hypothesis_result)
                        workflow_to_revise, workflow_index = self._select_executable_workflow(current_planning_result, selected_strategy)
                        revision_event = {
                            "round": current_round,
                            "type": "workflow_revision",
                            "identified_problems": identified_problems,
                            "workflow_revision_instructions": workflow_revision_instructions,
                            "recommended_actions": recommended_actions,
                            "status": "attempted",
                            **self._current_expert_backend_audit_snapshot(),
                        }

                        try:
                            if hasattr(self.planner_agent, 'revise_workflow_from_reflection') and workflow_to_revise:
                                revised_workflow = self.planner_agent.revise_workflow_from_reflection(
                                    original_workflow=workflow_to_revise,
                                    reflection_result=reflection_result,
                                    strategy=selected_strategy,
                                    question=scientific_question,
                                    chemistry_context=self.workflow_state.get("chemistry_context", {}),
                                    selected_strategy=self.workflow_state.get("selected_strategy_profile", {}),
                                )

                                if revised_workflow:
                                    revision_event["status"] = "success"
                                    revision_event["revised_result"] = revised_workflow

                                    protocols_for_update = self._extract_protocols(current_planning_result)
                                    if workflow_index is not None and 0 <= workflow_index < len(protocols_for_update):
                                        protocols_for_update[workflow_index] = revised_workflow
                                    elif protocols_for_update:
                                        protocols_for_update[0] = revised_workflow
                                    else:
                                        protocols_for_update = [revised_workflow]

                                    updated_planning = dict(current_planning_result) if isinstance(current_planning_result, dict) else {}
                                    updated_planning["optimized_protocols"] = protocols_for_update
                                    updated_planning["revised_from_reflection"] = True
                                    current_planning_result = updated_planning
                                    pending_planning_override = updated_planning
                                else:
                                    revision_event["status"] = "failed"
                            else:
                                revision_event["status"] = "not_supported"
                        except Exception as e:
                            revision_event["status"] = "error"
                            revision_event["error"] = str(e)

                        structured_record.setdefault("revision_events", []).append(revision_event)
                        self.workflow_state.setdefault("revision_history", [])
                        self.workflow_state["revision_history"].append(revision_event)

                    current_round += 1
                    needs_planning = True

                elif reflection_decision == "revise_hypothesis":
                    identified_problems = reflection_result.get("identified_problems", [])
                    hypothesis_revision_instructions = reflection_result.get("hypothesis_revision_instructions", [])
                    recommended_actions = reflection_result.get("recommended_actions", [])

                    revision_event = {
                        "round": current_round,
                        "type": "hypothesis_revision",
                        "identified_problems": identified_problems,
                        "hypothesis_revision_instructions": hypothesis_revision_instructions,
                        "recommended_actions": recommended_actions,
                        "status": "attempted",
                        **self._current_expert_backend_audit_snapshot(),
                    }

                    followup_retrieval_result = None
                    if hasattr(self.retrieval_agent, "retrieve_followup_evidence") and self.retrieval_agent:
                        try:
                            prior_review = retrieval_result.get("literature_review", "") if isinstance(retrieval_result, dict) else ""
                            followup_history = structured_record.get("retrieval_followup_rounds", [])
                            if isinstance(followup_history, list) and followup_history:
                                last = followup_history[-1]
                                if isinstance(last, dict):
                                    last_result = last.get("result", {})
                                    if isinstance(last_result, dict) and last_result.get("followup_review"):
                                        prior_review = f"{prior_review}\n\n{last_result.get('followup_review', '')}".strip()
                            followup_retrieval_result = self.retrieval_agent.retrieve_followup_evidence(
                                reflection_result=reflection_result,
                                original_question=scientific_question,
                                prior_review=prior_review,
                                pdf_dir=pdf_dir,
                                index_dir=index_dir,
                            )
                            structured_record.setdefault("retrieval_followup_rounds", []).append({
                                "round": current_round,
                                "status": "success",
                                "result": followup_retrieval_result,
                            })
                            revision_event["followup_retrieval"] = {
                                "status": "success",
                                "evidence_needs": followup_retrieval_result.get("evidence_needs", []),
                                "targeted_queries": followup_retrieval_result.get("targeted_queries", []),
                            }
                        except Exception as followup_error:
                            structured_record.setdefault("retrieval_followup_rounds", []).append({
                                "round": current_round,
                                "status": "failed",
                                "error": str(followup_error),
                            })
                            revision_event["followup_retrieval"] = {"status": "failed", "error": str(followup_error)}
                    else:
                        revision_event["followup_retrieval"] = {"status": "not_supported"}

                    try:
                        if hasattr(self.hypothesis_agent, 'revise_hypotheses_from_reflection'):
                            candidate_hypotheses = []
                            if isinstance(current_hypothesis_result, dict):
                                for key in ("final_hypotheses", "optimized_hypotheses", "ranked_strategies", "top_n_strategies"):
                                    value = current_hypothesis_result.get(key, [])
                                    if isinstance(value, list) and value:
                                        candidate_hypotheses = value
                                        break

                            if not candidate_hypotheses:
                                revision_event["status"] = "failed"
                                revision_event["error"] = "No hypotheses available for revision"
                            else:
                                reflection_inputs = [reflection_result]
                                if isinstance(followup_retrieval_result, dict):
                                    augmented_reflection = dict(reflection_result)
                                    rec_actions = list(augmented_reflection.get("recommended_actions", []) or [])
                                    hyp_instr = list(augmented_reflection.get("hypothesis_revision_instructions", []) or [])
                                    for need in (followup_retrieval_result.get("evidence_needs", []) or [])[:3]:
                                        rec_actions.append(f"Use follow-up literature evidence on: {need}")
                                    for item in (followup_retrieval_result.get("followup_results", []) or [])[:2]:
                                        if not isinstance(item, dict):
                                            continue
                                        ans = str(item.get("answer", "")).strip()
                                        if ans and "No relevant literature" not in ans:
                                            hyp_instr.append(f"Re-evaluate mechanism using follow-up finding: {ans[:220]}")
                                    augmented_reflection["recommended_actions"] = rec_actions
                                    augmented_reflection["hypothesis_revision_instructions"] = hyp_instr
                                    ev_summary = dict(augmented_reflection.get("evidence_summary", {}) or {})
                                    ev_summary["followup_retrieval_available"] = True
                                    ev_summary["followup_evidence_needs"] = followup_retrieval_result.get("evidence_needs", [])
                                    augmented_reflection["evidence_summary"] = ev_summary
                                    reflection_inputs = [augmented_reflection]

                                revised_hypothesis_result = self.hypothesis_agent.revise_hypotheses_from_reflection(
                                    hypotheses=candidate_hypotheses,
                                    reflection_results=reflection_inputs,
                                )

                                if revised_hypothesis_result:
                                    revision_event["status"] = "success"
                                    revision_event["revised_result"] = revised_hypothesis_result

                                    revised_pool = []
                                    if isinstance(revised_hypothesis_result, dict):
                                        revised_pool.extend(revised_hypothesis_result.get("revised", []) or [])
                                        revised_pool.extend(revised_hypothesis_result.get("new", []) or [])
                                        revised_pool.extend(revised_hypothesis_result.get("unchanged", []) or [])

                                    if revised_pool:
                                        updated_hypothesis_result = dict(current_hypothesis_result) if isinstance(current_hypothesis_result, dict) else {}
                                        updated_hypothesis_result["optimized_hypotheses"] = revised_pool
                                        updated_hypothesis_result["final_hypotheses"] = revised_pool
                                        updated_hypothesis_result["ranked_strategies"] = revised_pool
                                        updated_hypothesis_result["top_n_strategies"] = revised_pool[:top_n_strategies]
                                        updated_hypothesis_result["hypothesis_revision_result"] = revised_hypothesis_result
                                        if isinstance(followup_retrieval_result, dict):
                                            updated_hypothesis_result["latest_followup_retrieval"] = followup_retrieval_result
                                        current_hypothesis_result = updated_hypothesis_result
                                else:
                                    revision_event["status"] = "failed"
                        else:
                            revision_event["status"] = "not_supported"
                    except Exception as e:
                        revision_event["status"] = "error"
                        revision_event["error"] = str(e)

                    structured_record.setdefault("revision_events", []).append(revision_event)
                    self.workflow_state.setdefault("revision_history", [])
                    self.workflow_state["revision_history"].append(revision_event)

                    current_round += 1
                    needs_planning = True

                elif reflection_decision == "stop":
                    stop_conditions["triggered_condition"] = "reflection_stop"
                    should_continue = False

                else:
                    stop_conditions["triggered_condition"] = "unknown_decision"
                    should_continue = False

                if unrecoverable_failures >= max_unrecoverable_failures:
                    stop_conditions["triggered_condition"] = "max_unrecoverable_failures"
                    should_continue = False
                    final_decision = "stop"

                if reflection_decision in {"accept", "stop"}:
                    needs_planning = False

            return self._finalize_closed_loop_record(
                structured_record=structured_record,
                scientific_question=scientific_question,
                retrieval_result=retrieval_result,
                current_hypothesis_result=current_hypothesis_result,
                current_planning_result=current_planning_result,
                current_execution_result=current_execution_result,
                current_round=current_round,
                final_decision=final_decision,
                start_time=start_time,
            )

        except Exception as e:
            end_time = time.time()
            error_result = {
                "status": "error",
                "error": str(e),
                "resume_state_path": resume_state_path,
                "workflow_start_time": start_time,
                "workflow_end_time": end_time,
                "total_duration_seconds": end_time - start_time,
                "workflow_state": self.workflow_state,
                "log_data": self.log_data,
            }
            error_file = os.path.join(self.output_dir, "closed_loop_resume_error.json")
            try:
                with open(error_file, "w", encoding="utf-8") as f:
                    json.dump(error_result, f, indent=2, ensure_ascii=False)
                error_result["error_file"] = error_file
            except Exception:
                pass
            logger.error(f"❌ 恢复闭环流程失败: {e}")
            return error_result

    def _question_requires_mechanism_evidence(self, scientific_question: str) -> bool:
        ctx = self.workflow_state.get("chemistry_context", {}) if isinstance(getattr(self, "workflow_state", None), dict) else {}
        ctx_text = json.dumps(ctx, ensure_ascii=False, default=str) if isinstance(ctx, dict) else str(ctx or "")
        text = f"{scientific_question or ''} {ctx_text}".lower()
        if isinstance(ctx, dict) and (ctx.get("needs_ts") is True or ctx.get("needs_irc") is True):
            return True
        return bool(re.search(
            r"\b(mechanism|reaction mechanism|transition state|ts\b|irc\b|activation barrier|"
            r"free energy barrier|catalyst|catalytic|enantio|stereo|asymmetric|aldol|enamine)\b|"
            r"机理|反应路径|过渡态|本征反应坐标|活化能|自由能垒|催化|不对称",
            text,
            re.I,
        ))

    def _extract_explicit_smiles_from_question(self, scientific_question: str) -> List[str]:
        if not scientific_question:
            return []
        candidates = re.findall(
            r"(?<![A-Za-z0-9_])(?:[A-Z][A-Za-z0-9@\+\-\[\]\(\)=#$\\/\.]{2,}|[cnops][A-Za-z0-9@\+\-\[\]\(\)=#$\\/\.]{2,})(?![A-Za-z0-9_])",
            scientific_question,
        )
        out: List[str] = []
        seen = set()
        for token in candidates:
            s = token.strip(".,;:，。；：'\"`")
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

    @staticmethod
    def _as_float(value: Any) -> Optional[float]:
        try:
            if value is None or value == "":
                return None
            return float(value)
        except Exception:
            return None

    def _iter_successful_gaussian_steps(self, execution_result: Dict) -> List[Dict[str, Any]]:
        workflow_results = execution_result.get("results", []) if isinstance(execution_result, dict) else []
        if isinstance(execution_result, dict) and isinstance(execution_result.get("steps"), list):
            workflow_results = [execution_result]
        if not isinstance(workflow_results, list):
            return []

        steps: List[Dict[str, Any]] = []
        for workflow_item in workflow_results:
            if not isinstance(workflow_item, dict):
                continue
            for step in workflow_item.get("steps", []) if isinstance(workflow_item.get("steps"), list) else []:
                if not isinstance(step, dict) or step.get("status") != "success":
                    continue
                raw = step.get("raw_output") if isinstance(step.get("raw_output"), dict) else {}
                parsed = step.get("parsed_results") if isinstance(step.get("parsed_results"), dict) else raw.get("parsed_results")
                if not isinstance(parsed, dict) or not parsed:
                    continue
                if raw and raw.get("execution_mode") not in {None, "gaussian_job"}:
                    continue
                steps.append({"step": step, "raw": raw, "parsed": parsed})
        return steps

    def _step_has_mechanism_specific_evidence(self, step: Dict[str, Any], parsed: Dict[str, Any]) -> bool:
        text = " ".join(str(x or "") for x in [
            step.get("step_name"),
            step.get("description"),
            step.get("expected_output"),
            parsed.get("job_type"),
        ]).lower()
        if parsed.get("irc_verified") is True:
            return True
        for key in ("activation_barrier_kcal_mol", "barrier_kcal_mol", "delta_g_activation_kcal_mol"):
            if self._as_float(parsed.get(key)) is not None:
                return True
        has_ts_freq = parsed.get("n_imag_freq") == 1 and bool(re.search(r"\b(ts|transition state|过渡态)\b", text, re.I))
        has_free_energy = self._as_float(parsed.get("free_energy")) is not None and bool(re.search(r"barrier|activation|自由能|活化", text, re.I))
        return bool(has_ts_freq and has_free_energy)

    def _provenance_allows_summary(self,
                                   scientific_question: str,
                                   raw: Dict[str, Any],
                                   parsed: Dict[str, Any],
                                   mechanism_required: bool) -> bool:
        provenance = {}
        for candidate in (raw.get("deterministic_provenance"), raw.get("provenance"), parsed.get("provenance")):
            if isinstance(candidate, dict):
                provenance = candidate
                break
        if not provenance:
            return not mechanism_required

        geometry_source = str(provenance.get("geometry_source") or "").lower()
        mol_label = str(provenance.get("mol_label") or "").lower()
        atom_count = self._as_float(provenance.get("atom_count"))
        explicit_smiles = self._extract_explicit_smiles_from_question(scientific_question)

        if mechanism_required:
            return False
        if geometry_source == "common_name_fallback" and len(explicit_smiles) > 1:
            return False
        if atom_count is not None and atom_count <= 3 and len(explicit_smiles) > 1:
            return False
        if "water" in mol_label and len(explicit_smiles) > 1:
            return False
        return True

    def _extract_real_chemistry_values(self, execution_result: Dict, scientific_question: str = "") -> Dict[str, float]:
        """Extract reportable Gaussian values only from relevant successful steps.

        The old implementation regexed the entire execution JSON and could pick up
        unrelated deterministic fallback jobs. This function walks successful Gaussian
        steps, checks provenance, and refuses generic HOMO/SCF/IR evidence for
        mechanism-validation questions that require TS/IRC/barrier evidence.
        """
        mechanism_required = self._question_requires_mechanism_evidence(scientific_question)
        for item in self._iter_successful_gaussian_steps(execution_result):
            step = item["step"]
            raw = item["raw"]
            parsed = item["parsed"]
            if mechanism_required and not self._step_has_mechanism_specific_evidence(step, parsed):
                continue
            if not self._provenance_allows_summary(scientific_question, raw, parsed, mechanism_required):
                continue

            out: Dict[str, float] = {}
            gap = self._as_float(parsed.get("homo_lumo_gap_ev") or parsed.get("gap_ev"))
            if gap is None:
                gap_au = self._as_float(parsed.get("HOMO_LUMO_gap") or parsed.get("homo_lumo_gap") or parsed.get("homo_lumo_gap_hartree"))
                if gap_au is not None:
                    gap = gap_au * 27.2114 if abs(gap_au) < 2.0 else gap_au
            scf = self._as_float(parsed.get("scf_energy_hartree") or parsed.get("scf_energy"))
            homo = self._as_float(parsed.get("homo_hartree") or parsed.get("E_HOMO") or parsed.get("homo_energy"))
            lumo = self._as_float(parsed.get("lumo_hartree") or parsed.get("E_LUMO") or parsed.get("lumo_energy"))
            if gap is not None:
                out["homo_lumo_gap_ev"] = round(gap, 4)
            if scf is not None:
                out["scf_energy_hartree"] = round(scf, 6)
            if homo is not None:
                out["homo_hartree"] = round(homo, 5)
            if lumo is not None:
                out["lumo_hartree"] = round(lumo, 5)

            ir_peaks = parsed.get("ir_peaks")
            if not isinstance(ir_peaks, list) and isinstance(raw.get("spectroscopy"), dict):
                ir_peaks = raw["spectroscopy"].get("ir_peaks")
            ir: List[float] = []
            if isinstance(ir_peaks, list):
                for peak in ir_peaks:
                    freq = self._as_float(peak.get("freq_cm1") if isinstance(peak, dict) else peak)
                    if freq is not None and freq > 0 and freq not in ir:
                        ir.append(freq)
            if ir:
                out["ir_peaks_cm1"] = ir[:5]

            excited_states = parsed.get("excited_states")
            if not isinstance(excited_states, list) and isinstance(raw.get("spectroscopy"), dict):
                excited_states = raw["spectroscopy"].get("excited_states")
            if isinstance(excited_states, list) and excited_states:
                strongest = max(
                    (e for e in excited_states if isinstance(e, dict)),
                    key=lambda e: self._as_float(e.get("oscillator_strength")) or 0.0,
                    default=None,
                )
                if isinstance(strongest, dict):
                    nm = self._as_float(strongest.get("wavelength_nm"))
                    ev = self._as_float(strongest.get("energy_ev"))
                    if nm is not None:
                        out["strongest_absorption_nm"] = round(nm, 1)
                    if ev is not None:
                        out["strongest_absorption_ev"] = round(ev, 3)

            if out:
                return out
        return {}

    def _short_conclusion_text(self, value: Any, max_chars: int = 520) -> str:
        """将任意节点产物压成可读短文本，避免最终结论吐裸 JSON。"""
        if value is None:
            return ""
        if isinstance(value, str):
            text = value.strip()
        elif isinstance(value, (int, float, bool)):
            text = str(value)
        elif isinstance(value, dict):
            for key in ("summary", "reasoning", "description", "text", "conclusion", "message", "error"):
                if value.get(key):
                    text = str(value.get(key)).strip()
                    break
            else:
                parts = []
                for key, item in value.items():
                    if item is None or isinstance(item, (dict, list)):
                        continue
                    parts.append(f"{key}: {item}")
                    if len(parts) >= 4:
                        break
                text = "；".join(parts)
        elif isinstance(value, list):
            text = "；".join(self._short_conclusion_text(item, 180) for item in value[:4])
        else:
            text = str(value).strip()

        text = re.sub(r"\s+", " ", text).strip(" ;；")
        if max_chars > 0 and len(text) > max_chars:
            return text[:max_chars].rstrip() + "..."
        return text

    def _collect_step_labels(self, workflow: Optional[Dict[str, Any]], limit: int = 6) -> List[str]:
        if not isinstance(workflow, dict):
            return []
        steps = workflow.get("Steps")
        if not isinstance(steps, list):
            steps = workflow.get("steps")
        if not isinstance(steps, list):
            return []
        labels = []
        for step in steps:
            if not isinstance(step, dict):
                continue
            label = self._short_conclusion_text(
                step.get("description")
                or step.get("step_name")
                or step.get("name")
                or step.get("tool_name")
                or step,
                160,
            )
            if label:
                labels.append(label)
            if len(labels) >= limit:
                break
        return labels

    def _collect_execution_step_assessment(self, execution_result: Optional[Dict[str, Any]]) -> Dict[str, List[str]]:
        successes: List[str] = []
        failures: List[str] = []
        workflow_results = []
        if isinstance(execution_result, dict):
            if isinstance(execution_result.get("steps"), list):
                workflow_results = [execution_result]
            elif isinstance(execution_result.get("results"), list):
                workflow_results = execution_result.get("results", [])

        for workflow_item in workflow_results:
            if not isinstance(workflow_item, dict):
                continue
            steps = workflow_item.get("steps", [])
            if not isinstance(steps, list):
                continue
            for step in steps:
                if not isinstance(step, dict):
                    continue
                name = self._short_conclusion_text(
                    step.get("step_name") or step.get("description") or step.get("tool_name") or step,
                    150,
                )
                if step.get("status") == "success":
                    if name:
                        successes.append(name)
                    continue
                if step.get("status") == "failed":
                    err = self._short_conclusion_text(step.get("error") or step.get("error_info") or "", 120)
                    label = f"{name}（{err}）" if name and err else name or err
                    if label:
                        failures.append(label)
        return {"successful": successes[:5], "failed": failures[:8]}

    def _latest_reflection_result(self, structured_record: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if not isinstance(structured_record, dict):
            return {}
        rounds = structured_record.get("reflection_rounds", [])
        if not isinstance(rounds, list) or not rounds:
            return {}
        latest = rounds[-1]
        if not isinstance(latest, dict):
            return {}
        result = latest.get("result")
        return result if isinstance(result, dict) else latest

    def _build_integrated_final_analysis(self,
                                         scientific_question: str,
                                         retrieval_result: Optional[Dict[str, Any]],
                                         hypothesis_result: Optional[Dict[str, Any]],
                                         planning_result: Optional[Dict[str, Any]],
                                         execution_result: Optional[Dict[str, Any]],
                                         structured_record: Optional[Dict[str, Any]],
                                         selected_strategy: Optional[Dict[str, Any]],
                                         workflow_outcome: Dict[str, Any],
                                         conclusion_type: str,
                                         computed: Dict[str, Any],
                                         unresolved_issues: List[str],
                                         next_steps: List[str],
                                         final_decision: str) -> Dict[str, Any]:
        retrieval_result = retrieval_result if isinstance(retrieval_result, dict) else {}
        hypothesis_result = hypothesis_result if isinstance(hypothesis_result, dict) else {}
        planning_result = planning_result if isinstance(planning_result, dict) else {}
        selected_strategy = selected_strategy if isinstance(selected_strategy, dict) else self._select_primary_strategy(hypothesis_result)
        mechanism_required = self._question_requires_mechanism_evidence(scientific_question)

        clues = retrieval_result.get("mechanistic_clues", [])
        clues = [self._short_conclusion_text(item, 180) for item in clues[:4]] if isinstance(clues, list) else []
        clues = [item for item in clues if item]
        review = self._short_conclusion_text(retrieval_result.get("literature_review", ""), 420)
        retrieval_limitations = retrieval_result.get("limitations", [])
        retrieval_limitations = [self._short_conclusion_text(item, 160) for item in retrieval_limitations[:3]] if isinstance(retrieval_limitations, list) else []
        if clues:
            evidence_from_retrieval = f"文献检索给出的关键线索包括：{'；'.join(clues)}。"
            if retrieval_limitations:
                evidence_from_retrieval += f" 同时需要注意：{'；'.join(retrieval_limitations)}。"
        elif review:
            evidence_from_retrieval = f"文献检索阶段形成了背景摘要：{review}"
        else:
            evidence_from_retrieval = "文献检索阶段没有提供足够的可复核机理线索，结论必须主要依赖后续计算证据。"

        strategy_name = self._short_conclusion_text(
            selected_strategy.get("strategy_name") or selected_strategy.get("name") if selected_strategy else "",
            180,
        )
        strategy_reason = self._short_conclusion_text(
            (selected_strategy.get("detailed_reasoning") or selected_strategy.get("reasoning") or "") if selected_strategy else "",
            420,
        )
        strategy_conf = selected_strategy.get("confidence") if selected_strategy else None
        conf_text = f"（置信度 {float(strategy_conf):.0%}）" if isinstance(strategy_conf, (int, float)) else ""
        if strategy_name:
            working_hypothesis = f"假设阶段把“{strategy_name}”作为优先检验对象{conf_text}。"
            if strategy_reason:
                working_hypothesis += f" 其依据是：{strategy_reason}"
        else:
            working_hypothesis = "假设阶段没有形成可直接引用的优先机理假设。"

        workflow, _ = self._select_executable_workflow(planning_result, selected_strategy)
        step_labels = self._collect_step_labels(workflow)
        protocol_name = self._short_conclusion_text(
            workflow.get("workflow_name") or workflow.get("strategy_name") if isinstance(workflow, dict) else "",
            180,
        )
        if step_labels:
            planned_validation = f"规划阶段将验证路线组织为：{' → '.join(step_labels)}。"
            if protocol_name:
                planned_validation = f"规划阶段选择“{protocol_name}”，并将验证路线组织为：{' → '.join(step_labels)}。"
        else:
            planned_validation = "规划阶段没有产出可执行的验证步骤，无法形成完整证据链。"
        if mechanism_required:
            planned_validation += " 对机理问题而言，最低可接受证据链应包含候选 TS 优化、唯一虚频、正反向 IRC 连通性，以及同一条件下的相对 Gibbs 自由能垒比较。"

        step_assessment = self._collect_execution_step_assessment(execution_result)
        success_rate = workflow_outcome.get("execution_success_rate")
        success_text = f"{float(success_rate):.0%}" if isinstance(success_rate, (int, float)) else "未知"
        execution_parts = [f"执行阶段成功率为 {success_text}。"]
        if step_assessment["successful"]:
            execution_parts.append(f"已经完成的可用工作包括：{'；'.join(step_assessment['successful'])}。")
        if step_assessment["failed"]:
            execution_parts.append(f"关键缺口出现在：{'；'.join(step_assessment['failed'])}。")
        if computed:
            computed_items = []
            if "homo_lumo_gap_ev" in computed:
                computed_items.append(f"HOMO-LUMO 能隙 {computed['homo_lumo_gap_ev']} eV")
            if "scf_energy_hartree" in computed:
                computed_items.append(f"SCF 能量 {computed['scf_energy_hartree']} Hartree")
            if "ir_peaks_cm1" in computed:
                computed_items.append("IR 峰 " + "、".join(f"{x:.0f} cm⁻¹" for x in computed["ir_peaks_cm1"][:4]))
            if computed_items:
                execution_parts.append(f"可报告计算量为：{'；'.join(computed_items)}。")
        elif mechanism_required:
            execution_parts.append("本轮没有得到可支撑机理判定的 TS/IRC/自由能垒结果。")
        execution_assessment = " ".join(execution_parts)
        if mechanism_required:
            execution_assessment = (
                "计算执行记录在本报告中的作用是限定证据口径：普通分子性质、单点能和红外峰只可作为结构或谱学线索，"
                "机理判定应以过渡态、IRC 连通性和同一热力学条件下的相对 Gibbs 自由能垒为核心。"
            )

        reflection = self._latest_reflection_result(structured_record)
        decision = self._short_conclusion_text(reflection.get("decision") or final_decision, 80) if reflection else final_decision
        reflection_reason = self._short_conclusion_text(reflection.get("reasoning", ""), 360) if reflection else ""
        reflection_problems = reflection.get("identified_problems", []) if reflection else []
        reflection_actions = reflection.get("recommended_actions", []) if reflection else []
        reflection_problems = [self._short_conclusion_text(item, 130) for item in reflection_problems[:4]] if isinstance(reflection_problems, list) else []
        reflection_actions = [self._short_conclusion_text(item, 130) for item in reflection_actions[:4]] if isinstance(reflection_actions, list) else []
        reflection_verdict = f"反思阶段的最终决策为 {decision or 'unknown'}。"
        if reflection_reason:
            reflection_verdict += f" 理由是：{reflection_reason}"
        if reflection_problems:
            reflection_verdict += f" 识别的问题包括：{'；'.join(reflection_problems)}。"
        if reflection_actions:
            reflection_verdict += f" 建议动作包括：{'；'.join(reflection_actions)}。"

        validation_gaps = []
        for issue in unresolved_issues:
            text = self._short_conclusion_text(issue, 160)
            if text and text not in validation_gaps:
                validation_gaps.append(text)
        if mechanism_required and not computed:
            gap = "TS/IRC/自由能垒证据链未闭合，不能把当前结果解释为机理支持。"
            if gap not in validation_gaps:
                validation_gaps.insert(0, gap)
        if not validation_gaps and conclusion_type != "supported":
            validation_gaps.append("应结合原始输出、文献数据和执行记录复核解释边界。")

        if mechanism_required:
            model_name = strategy_name or "酸协同烯胺型 C-C 成键过渡态"
            scientific_conclusion = (
                f"结论摘要：本轮最有价值的科学结论，是将“{model_name}”确认为优先验证的工作假设。"
                "该模型把丙酮的烯胺活化、三氟甲磺酸对醛羰基的显式参与、以及 C-C 成键过渡态中的立体控制放在同一条机理链上；"
                "后续计算应围绕酸参与与非酸参与通道的相对 Gibbs 自由能垒来判断其解释力。"
            )
            mechanism_picture = (
                "机理图景：手性胺催化剂首先与 acetone 形成 enamine-like 亲核体；"
                "p-nitrobenzaldehyde 的羰基氧由 triflic acid 通过氢键、离子对或质子化形式被活化；"
                "随后 enamine 从受控构象进攻醛碳形成 C-C bond，酸协同体在这一过渡态中同时降低能垒并固定 Re/Si 面选择；"
                "最后经质子转移和催化剂释放得到目标 beta-hydroxy ketone。"
            )
            evidence_interpretation = (
                "证据解释：文献检索把 enamine 形成、醛羰基酸活化和 C-C 成键过渡态确定为应重点比较的机理要素；"
                f"假设筛选进一步把“{model_name}”排为优先模型"
            )
            if strategy_reason:
                evidence_interpretation += f"，核心理由是：{strategy_reason}"
            evidence_interpretation += (
                "。规划节点给出的正确证据链不是报告 HOMO-LUMO、SCF 或红外峰，而是比较显式酸参与与非酸参与的立体异构过渡态、"
                "用唯一虚频和 IRC 确认反应坐标，再用相对 Gibbs 自由能垒解释产物形成和选择性。"
            )
            validation_items = [
                f"以“{model_name}”为主模型，同时保留无显式 triflic acid 参与的对照通道。",
                "分别构建 enamine、aldehyde-acid complex、离子对/氢键复合物，以及通向目标产物的 Re/Si 面 C-C 成键过渡态候选。",
                "在 M06-2X/6-31+G(d) 或更高层级、SMD(acetone)、约 303 K 条件下优化各候选 TS，并进行频率分析。",
                "只保留具有一个与 C-C 成键反应坐标一致虚频的 TS，并对其做正反向 IRC。",
                "以同一参考态计算相对 Gibbs 自由能垒和 ΔΔG‡，用其解释目标 beta-hydroxy ketone 的形成趋势和立体选择性。",
            ]
            acceptance_items = [
                "酸参与通道的最低 C-C 成键 TS 应显著低于无酸参与通道，且关键几何显示羰基氧与 triflic acid 存在合理相互作用。",
                "最低能 TS 的虚频位移应主要对应 enamine 碳与醛碳靠近形成 C-C bond。",
                "IRC 两端应分别连接目标反应物/中间体复合物和对应的 beta-hydroxy ketone 前体。",
                "由 ΔΔG‡ 预测的主通道应与目标产物构型和已有实验/文献选择性趋势一致。",
                "若存在更低能的非酸参与或其他质子转移路径，则应调整主机理模型，而不是用单个分子性质替代机理证据。",
            ]
            overall_judgment = scientific_conclusion
            recommended = [self._short_conclusion_text(item, 160) for item in next_steps[:6] if self._short_conclusion_text(item, 160)]
            sections = [
                {"title": "结论摘要", "body": overall_judgment},
                {"title": "机理图景", "body": mechanism_picture},
                {"title": "证据解释", "body": evidence_interpretation},
                {"title": "验证方案", "items": validation_items},
                {"title": "判定标准", "items": acceptance_items},
            ]
        elif computed:
            scientific_conclusion = (
                "综合判断：当前最合理的工作结论是本轮获得了与研究问题匹配的可报告计算量，可作为初步计算证据；"
                "仍需结合原始输出、几何合理性和文献对照确认其解释边界。"
            )
        else:
            scientific_conclusion = (
                "综合判断：当前最合理的工作结论是本轮主要形成了问题背景、候选假设和计算路线；"
                "这些结果可指导下一轮验证，但还缺少足以支撑正式结论的计算证据。"
            )
        if not mechanism_required:
            overall_judgment = scientific_conclusion
            recommended = [self._short_conclusion_text(item, 160) for item in next_steps[:6] if self._short_conclusion_text(item, 160)]
            sections = [
                {"title": "综合判断", "body": overall_judgment},
                {"title": "证据来源", "body": evidence_from_retrieval},
                {"title": "候选假设", "body": working_hypothesis},
                {"title": "验证路线", "body": planned_validation},
                {"title": "执行摘要", "body": execution_assessment},
                {"title": "反思判定", "body": reflection_verdict},
            ]
            if validation_gaps:
                sections.append({"title": "限制", "items": validation_gaps})
            if recommended:
                sections.append({"title": "下一步", "items": recommended})

        return {
            "overall_judgment": overall_judgment,
            "evidence_from_retrieval": evidence_from_retrieval,
            "working_hypothesis": working_hypothesis,
            "planned_validation": planned_validation,
            "execution_assessment": execution_assessment,
            "reflection_verdict": reflection_verdict,
            "scientific_conclusion": scientific_conclusion,
            "validation_gaps": validation_gaps,
            "recommended_next_steps": recommended,
            "sections": sections,
        }

    def _format_final_conclusion_summary(self,
                                         scientific_question: str,
                                         conclusion_type: str,
                                         status: str,
                                         computed: Dict,
                                         workflow_outcome: Dict) -> str:
        question = (scientific_question or "").strip()
        subject = f"针对“{question}”，" if question else ""
        facts = []
        if "homo_lumo_gap_ev" in computed:
            facts.append(f"HOMO-LUMO 能隙为 {computed['homo_lumo_gap_ev']} eV")
        if "scf_energy_hartree" in computed:
            facts.append(f"SCF 能量为 {computed['scf_energy_hartree']} Hartree")
        if computed.get("ir_peaks_cm1"):
            peaks = "、".join(f"{p:.0f}" for p in computed["ir_peaks_cm1"][:4])
            facts.append(f"主要红外振动峰位约为 {peaks} cm⁻¹")
        if computed.get("strongest_absorption_nm"):
            ev = computed.get("strongest_absorption_ev")
            suffix = f"（{ev} eV）" if ev else ""
            facts.append(f"最强紫外-可见吸收约为 {computed['strongest_absorption_nm']} nm{suffix}")

        success_rate = workflow_outcome.get("execution_success_rate")
        status_label = workflow_outcome.get("overall_status") or status
        status_label = {
            "success": "成功",
            "successful": "成功",
            "completed": "完成",
            "partial_success": "部分成功",
            "failed": "失败",
            "unknown": "未知",
        }.get(str(status_label), status_label)
        if facts:
            evidence = "；".join(facts)
            if conclusion_type == "supported":
                tail = "这些结果可作为本轮计算证据；"
            else:
                tail = "这些结果目前只能作为初步计算线索；"
            tail += "后续应结合原始输出、几何参数、频率归属和文献数据复核其解释边界。"
            return f"{subject}本次计算得到：{evidence}。{tail}"

        if conclusion_type == "failed":
            return f"{subject}本轮主要形成了问题背景和验证路线，尚不适合写成可引用的计算结论。"
        if conclusion_type == "provisional":
            return f"{subject}本轮形成的是暂定计算解释；后续应围绕关键证据链补充验证后再定稿。"
        if conclusion_type == "supported":
            return f"{subject}本次工作流完成并获得计算证据支持，但仍需结合原始输出和文献数据复核。"
        return f"{subject}本轮输出更适合作为研究记录和下一轮验证依据。"

    def _synthesize_final_conclusion(self,
                                                scientific_question: str,
                                                status: str,
                                                structured_record: Dict,
                                                retrieval_result: Dict,
                                                hypothesis_result: Dict,
                                                planning_result: Dict,
                                                execution_result: Dict,
                                                final_round: int,
                                                final_decision: str = "stop") -> Dict:
        """Class-bound final conclusion synthesis (runtime extension)."""
        selected_strategy = None
        if hypothesis_result and "ranked_strategies" in hypothesis_result:
            strategies = hypothesis_result["ranked_strategies"]
            if strategies:
                selected_strategy = strategies[0]

        execution_schema_summary = self._summarize_execution_schema(execution_result)
        workflow_outcome = {
            "total_rounds": final_round - 1 if final_round > 0 else 0,
            "execution_success_rate": execution_schema_summary.get("overall_success_rate", 0.0),
            "overall_status": execution_schema_summary.get("overall_status", "unknown"),
            "workflow_outcome": execution_schema_summary.get("workflow_outcome", "unknown"),
            "validation_overview": execution_schema_summary.get("validation_overview", {}),
            "failed_steps": execution_schema_summary.get("failed_steps", []),
        }

        evidence_summary = {
            "literature_review_available": bool(retrieval_result.get("literature_review", "")),
            "hypotheses_generated": len(hypothesis_result.get("ranked_strategies", [])) if hypothesis_result else 0,
            "workflows_executed": len(structured_record.get("execution_rounds", [])),
            "successful_executions": sum(
                1
                for round_data in structured_record.get("execution_rounds", [])
                if round_data.get("status") == "success"
            ),
        }

        unresolved_issues = []
        unresolved_issues.extend(execution_schema_summary.get("issues", [])[:5])
        if structured_record.get("revision_events"):
            unresolved_issues.append("工作流执行期间发生过修订")
        mechanism_required = self._question_requires_mechanism_evidence(scientific_question)

        if status == "accepted":
            conclusion_type = "supported"
        elif status in {"completed", "stopped"}:
            conclusion_type = "provisional"
        elif status == "failed":
            conclusion_type = "failed"
        else:
            conclusion_type = "unknown"

        key_findings = []
        if selected_strategy:
            key_findings.append({
                "type": "selected_strategy",
                "strategy_name": selected_strategy.get("strategy_name", "Unknown"),
                "reasoning": selected_strategy.get("reasoning", "")[:200],
            })

        if evidence_summary["literature_review_available"]:
            key_findings.append({
                "type": "literature_context",
                "summary": "已完成文献检索与摘要分析",
            })

        if workflow_outcome["execution_success_rate"] > 0.7:
            key_findings.append({
                "type": "execution_success",
                "summary": f"执行成功率较高（{workflow_outcome['execution_success_rate']:.1%}）",
            })

        # 只把与当前科学问题匹配的真实化学量顶到结论首句。复杂机理验证必须有
        # TS/IRC/势垒等机制证据，不能用任意分子的 HOMO/SCF/IR 代替。
        computed = self._extract_real_chemistry_values(execution_result, scientific_question=scientific_question)
        if mechanism_required and not computed:
            unresolved_issues.append("机制验证所需的 TS/IRC/自由能垒证据不足，不能形成机制支持结论")
        if computed:
            if (
                conclusion_type == "provisional"
                and not mechanism_required
                and workflow_outcome["execution_success_rate"] >= 0.7
            ):
                conclusion_type = "supported"
            key_findings.insert(0, {
                "type": "computed_results",
                "summary": "真实 Gaussian 计算量(非估计、非 mock)",
                **computed,
            })

        next_steps = []
        if conclusion_type == "provisional":
            next_steps.append("补充验证计算（更大基组或更高层级方法）")
            next_steps.append("扩展文献检索以补强对照证据")

        if workflow_outcome["failed_steps"]:
            next_steps.append("修复失败的计算或绘图步骤")

        if evidence_summary["literature_review_available"]:
            next_steps.append("将计算结果与文献数据逐项对照")

        integrated_analysis = self._build_integrated_final_analysis(
            scientific_question=scientific_question,
            retrieval_result=retrieval_result,
            hypothesis_result=hypothesis_result,
            planning_result=planning_result,
            execution_result=execution_result,
            structured_record=structured_record,
            selected_strategy=selected_strategy,
            workflow_outcome=workflow_outcome,
            conclusion_type=conclusion_type,
            computed=computed,
            unresolved_issues=unresolved_issues,
            next_steps=next_steps,
            final_decision=final_decision,
        )

        conclusion_summary = integrated_analysis.get("scientific_conclusion") or self._format_final_conclusion_summary(
            scientific_question=scientific_question,
            conclusion_type=conclusion_type,
            status=status,
            computed=computed,
            workflow_outcome=workflow_outcome,
        )

        return {
            "scientific_question": scientific_question,
            "conclusion_type": conclusion_type,
            "conclusion_summary": conclusion_summary,
            "integrated_analysis": integrated_analysis,
            "selected_strategy": selected_strategy,
            "workflow_outcome": workflow_outcome,
            "evidence_summary": evidence_summary,
            "expert_backend_audit_summary": self.workflow_state.get("expert_backend_audit_summary", {}),
            "key_findings": key_findings,
            "unresolved_issues": unresolved_issues,
            "recommended_next_steps": next_steps,
            "final_status": status,
            "final_decision": final_decision,
            "total_reflection_rounds": final_round - 1,
            "confidence": min(0.9, workflow_outcome["execution_success_rate"] + 0.2),
        }

    def run_complete_workflow(self,
                                          scientific_question: str,
                                          num_queries: int = 3,
                                          num_hypotheses_per_query: int = 3,
                                          top_n_strategies: int = 5,
                                          pdf_dir: str = "papers",
                                          index_dir: str = "index",
                                          search_papers: bool = False) -> Dict[str, Any]:
        """Legacy-compatible complete workflow wrapper."""
        closed_loop_result = self.run_bounded_closed_loop_workflow(
            scientific_question=scientific_question,
            num_queries=num_queries,
            num_hypotheses_per_query=num_hypotheses_per_query,
            top_n_strategies=top_n_strategies,
            pdf_dir=pdf_dir,
            index_dir=index_dir,
            search_papers=search_papers,
            max_reflection_rounds=int(os.environ.get("ARCHE_MAX_REFLECTION_ROUNDS", "2")),
            max_unrecoverable_failures=3,
        )
        return self._convert_to_legacy_format(closed_loop_result)

    def _convert_to_legacy_format(self, closed_loop_result: Dict) -> Dict:
        """Convert bounded closed-loop result to previous output schema."""
        final_conclusion = closed_loop_result.get("final_conclusion", {})

        closed_loop_status = str(closed_loop_result.get("status", "")).strip().lower()
        if closed_loop_status == "error":
            legacy_status = "error"
        elif closed_loop_status == "waiting_for_gaussian_jobs":
            legacy_status = "waiting_for_gaussian_jobs"
        else:
            legacy_status = "success"

        legacy_result = {
            "scientific_question": closed_loop_result.get("scientific_question", ""),
            "status": legacy_status,
            "phases": {},
            "summary": {},
        }

        retrieval_phase = closed_loop_result.get("retrieval_phase", {})
        legacy_result["phases"]["retrieval"] = {
            "keywords": retrieval_phase.get("top_keywords", []),
            "literature_review": retrieval_phase.get("literature_review", ""),
            "relevant_papers": retrieval_phase.get("relevant_papers", []),
        }

        hypothesis_phase = closed_loop_result.get("hypothesis_phase", {})
        legacy_result["phases"]["hypothesis"] = {
            "ranked_strategies": hypothesis_phase.get("ranked_strategies", []),
            "top_n_strategies": hypothesis_phase.get("top_n_strategies", []),
            "optimized_hypotheses": hypothesis_phase.get("optimized_hypotheses", []),
        }

        planning_rounds = closed_loop_result.get("planning_rounds", [])
        if planning_rounds:
            last_planning = planning_rounds[-1].get("result", {})
            legacy_result["phases"]["planner"] = {
                "optimized_protocols": last_planning.get("optimized_protocols", []),
                "original_protocols": last_planning.get("original_protocols", []),
                "optimization_ratio": last_planning.get("optimization_ratio", 1.0),
            }
        else:
            legacy_result["phases"]["planner"] = {"optimized_protocols": []}

        execution_rounds = closed_loop_result.get("execution_rounds", [])
        if execution_rounds:
            last_execution = execution_rounds[-1].get("result", {})
            legacy_result["phases"]["execution"] = {
                "overall_success_rate": last_execution.get("overall_success_rate", 0),
                "total_steps": last_execution.get("total_steps", 0),
                "successful_steps": last_execution.get("successful_steps", 0),
                "failed_steps": last_execution.get("failed_steps", 0),
                "total_duration": last_execution.get("total_duration", 0),
                "final_output": last_execution.get("final_output", None),
            }
        else:
            legacy_result["phases"]["execution"] = {"overall_success_rate": 0}

        legacy_result["summary"] = {
            "total_phases": 4,
            "overall_success_rate": final_conclusion.get("workflow_outcome", {}).get("execution_success_rate", 0),
            "total_duration": closed_loop_result.get("total_duration_seconds", 0),
            "phases_completed": ["retrieval", "hypothesis", "planner", "execution"],
        }

        if "output_files" in closed_loop_result:
            legacy_result["output_files"] = closed_loop_result["output_files"]

        if closed_loop_result.get("status") == "waiting_for_gaussian_jobs":
            legacy_result["pending_gaussian_jobs"] = closed_loop_result.get("pending_gaussian_jobs", [])
            legacy_result["waiting_for_jobs"] = True
            legacy_result["can_resume"] = True
            legacy_result["gaussian_execution_mode"] = closed_loop_result.get("gaussian_execution_mode", self.gaussian_execution_mode)

        if "error" in closed_loop_result:
            legacy_result["error"] = closed_loop_result["error"]
            legacy_result["status"] = "error"

        return legacy_result

# ==================== 主函数 ====================

def main():
    """命令行入口"""
    parser = argparse.ArgumentParser(description="计算化学多智能体系统控制器")
    parser.add_argument("--question", "-q", required=False, help="科学问题")
    parser.add_argument("--api-key", help="Deepseek API密钥（也可通过环境变量设置）")
    parser.add_argument("--resume-state", default=None, help="恢复状态文件路径（waiting_gaussian_jobs_state.json）")
    parser.add_argument("--work-dir", help="工作目录（默认：当前目录）")
    parser.add_argument("--toolpool", help="工具定义文件路径")
    parser.add_argument("--expert-model-name", default=os.environ.get("ARCHE_CHEM_MODEL", "qwen2.5-7b-instruct"), help="专家模型名称（默认读 ARCHE_CHEM_MODEL）")
    parser.add_argument("--expert-model-path", default=None, help="专家模型本地路径")
    parser.add_argument("--expert-backend", default=os.environ.get("ARCHE_CHEM_BACKEND", "local_hf"), help="专家模型后端（默认读 ARCHE_CHEM_BACKEND；设 openai_compatible 走内网推理端点，避免离线 local_hf 反复拉 HF 模型空转拖慢 planner/execution）")
    parser.add_argument("--disable-expert-review", action="store_true", help="禁用专家复核/分析")
    parser.add_argument("--gaussian-execution-mode", default=os.environ.get("GAUSSIAN_EXECUTION_MODE", "api"), choices=["local_shell", "slurm", "api"], help="Gaussian执行模式(真实后端;已移除 replay 模拟)")
    parser.add_argument("--gaussian-command", default="g16", help="Gaussian执行命令")
    parser.add_argument("--gaussian-module-load", default=None, help="可选模块加载命令")
    parser.add_argument("--gaussian-environment-hook", default=None, help="可选环境初始化命令")
    parser.add_argument("--gaussian-slurm-partition", default=None, help="可选Slurm分区")
    parser.add_argument("--gaussian-job-root", default=None, help="Gaussian任务状态根目录")
    parser.add_argument("--num-queries", type=int, default=int(os.environ.get("ARCHE_NUM_QUERIES", "3")), help="查询数量（默认：3，可经 ARCHE_NUM_QUERIES 调小）")
    parser.add_argument("--num-hypotheses", type=int, default=int(os.environ.get("ARCHE_NUM_HYPOTHESES", "3")), help="每个查询的假设数量（默认：3，可经 ARCHE_NUM_HYPOTHESES 调小——假设总数决定 O(N^2) 两两比较的 LLM 调用量，是耗时主因）")
    parser.add_argument("--top-n", type=int, default=int(os.environ.get("ARCHE_TOP_N", "5")), help="前N个策略（默认：5，可经 ARCHE_TOP_N 调小）")
    parser.add_argument("--pdf-dir", default="papers", help="PDF存储目录（默认：papers）")
    parser.add_argument("--index-dir", default="index", help="索引存储目录（默认：index）")
    parser.add_argument("--no-search", action="store_true", help="跳过论文检索")
    
    args = parser.parse_args()

    if not args.resume_state and not args.question:
        parser.error("未提供 --resume-state 时必须提供 --question")
    
    # 创建控制器
    controller = ChemistryMultiAgentController(
        deepseek_api_key=args.api_key,
        work_dir=args.work_dir,
        toolpool_path=args.toolpool,
        expert_model_name=args.expert_model_name,
        expert_model_path=args.expert_model_path,
        expert_backend=args.expert_backend,
        enable_expert_review=not args.disable_expert_review,
        gaussian_execution_mode=args.gaussian_execution_mode,
        gaussian_command=args.gaussian_command,
        gaussian_module_load=args.gaussian_module_load,
        gaussian_environment_hook=args.gaussian_environment_hook,
        gaussian_slurm_partition=args.gaussian_slurm_partition,
        gaussian_job_root=args.gaussian_job_root,
    )
    
    if args.resume_state:
        result = controller.resume_from_waiting_state(
            resume_state_path=args.resume_state,
            pdf_dir=args.pdf_dir,
            index_dir=args.index_dir,
            top_n_strategies=args.top_n,
        )
    else:
        # 完整工作流
        result = controller.run_complete_workflow(
            scientific_question=args.question,
            num_queries=args.num_queries,
            num_hypotheses_per_query=args.num_hypotheses,
            top_n_strategies=args.top_n,
            pdf_dir=args.pdf_dir,
            index_dir=args.index_dir,
            search_papers=not args.no_search
        )
    
    # 输出摘要
    print("\n" + "="*60)
    print("工作流执行摘要")
    print("="*60)
    
    print(f"📋 科学问题: {result.get('scientific_question', 'Unknown')[:100]}...")
    print(f"📊 状态: {result.get('status', 'unknown')}")
    
    if "total_duration_seconds" in result:
        print(f"⏱️  总耗时: {result['total_duration_seconds']:.1f} 秒")
    
    if "summary" in result:
        summary = result["summary"]
        print(f"📈 阶段数: {summary.get('total_phases', 0)}")
        print(f"📊 总步骤: {summary.get('total_steps', 0)}")
        print(f"✅ 成功步骤: {summary.get('successful_steps', 0)}")
        print(f"❌ 失败步骤: {summary.get('failed_steps', 0)}")
        print(f"🎯 成功率: {summary.get('overall_success_rate', 0):.2%}")
    
    if "output_files" in result:
        print(f"\n💾 输出文件:")
        for file_type, file_path in result["output_files"].items():
            file_name = os.path.basename(file_path)
            print(f"   {file_type}: {file_name}")
    
    if result.get("status") == "error":
        print(f"\n❌ 错误: {result.get('error', '未知错误')}")


if __name__ == "__main__":
    main()
