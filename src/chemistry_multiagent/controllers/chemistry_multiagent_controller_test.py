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
    from ChemistryAgent.src.chemistry_multiagent.agents.execution_agent_test import ExecutionAgent, ExecutionResult
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
                 enable_expert_review: bool = True):
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
            logger.warning("Agent模块不可用，将使用模拟模式")
        
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
            )
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
            
            if hypothesis_result.get("error"):
                structured_record["stop_conditions"]["triggered_condition"] = "hypothesis_generation_failure"
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
        """
        合成最终结论
        
        参数:
            scientific_question: 科学问题
            status: 最终状态
            structured_record: 结构化记录
            retrieval_result: 检索结果
            hypothesis_result: 假设结果
            planning_result: 规划结果
            execution_result: 执行结果
            final_round: 最终轮次
            final_decision: 最终决策
        
        返回:
            结构化最终结论
        """
        # 提取关键信息
        selected_strategy = None
        if hypothesis_result and "ranked_strategies" in hypothesis_result:
            strategies = hypothesis_result["ranked_strategies"]
            if strategies:
                selected_strategy = strategies[0]  # 最高排名策略
        
        execution_schema_summary = self._summarize_execution_schema(execution_result)
        workflow_outcome = {
            "total_rounds": final_round - 1 if final_round > 0 else 0,
            "execution_success_rate": execution_schema_summary.get("overall_success_rate", 0.0),
            "overall_status": execution_schema_summary.get("overall_status", "unknown"),
            "workflow_outcome": execution_schema_summary.get("workflow_outcome", "unknown"),
            "validation_overview": execution_schema_summary.get("validation_overview", {}),
            "failed_steps": execution_schema_summary.get("failed_steps", [])
        }

        # 证据摘要
        evidence_summary = {
            "literature_review_available": bool(retrieval_result.get("literature_review", "")),
            "hypotheses_generated": len(hypothesis_result.get("ranked_strategies", [])) if hypothesis_result else 0,
            "workflows_executed": len(structured_record.get("execution_rounds", [])),
            "successful_executions": sum(1 for round_data in structured_record.get("execution_rounds", []) 
                                         if round_data.get("status") == "success")
        }
        
        # 未解决的问题
        unresolved_issues = []
        unresolved_issues.extend(execution_schema_summary.get("issues", [])[:5])  # 最多5个问题
        
        if structured_record.get("revision_events"):
            unresolved_issues.append("Workflow required revisions during execution")
        
        # 确定结论类型
        if status == "accepted":
            conclusion_type = "supported"
            conclusion_summary = f"The scientific question '{scientific_question[:50]}...' has been successfully addressed with computational evidence."
        elif status == "completed" or status == "stopped":
            conclusion_type = "provisional"
            conclusion_summary = f"Preliminary results for '{scientific_question[:50]}...' require further validation."
        elif status == "failed":
            conclusion_type = "failed"
            conclusion_summary = f"Unable to adequately address '{scientific_question[:50]}...' due to execution failures."
        else:
            conclusion_type = "unknown"
            conclusion_summary = f"Workflow completed with status: {status}"
        
        # 生成关键发现
        key_findings = []
        
        if selected_strategy:
            key_findings.append({
                "type": "selected_strategy",
                "strategy_name": selected_strategy.get("strategy_name", "Unknown"),
                "reasoning": selected_strategy.get("reasoning", "")[:200]
            })
        
        if evidence_summary["literature_review_available"]:
            key_findings.append({
                "type": "literature_context",
                "summary": "Literature review successfully retrieved and analyzed"
            })
        
        if workflow_outcome["execution_success_rate"] > 0.7:
            key_findings.append({
                "type": "execution_success",
                "summary": f"High execution success rate ({workflow_outcome['execution_success_rate']:.1%})"
            })
        
        # 建议下一步
        next_steps = []
        
        if conclusion_type == "provisional":
            next_steps.append("Run additional validation calculations")
            next_steps.append("Expand literature search for supporting evidence")
            
        if workflow_outcome["failed_steps"]:
            next_steps.append("Debug failed computational steps")
            
        if evidence_summary["literature_review_available"]:
            next_steps.append("Compare computational results with literature findings")
        
        # 构建最终结论
        final_conclusion = {
            "scientific_question": scientific_question,
            "conclusion_type": conclusion_type,
            "conclusion_summary": conclusion_summary,
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
            "confidence": min(0.9, workflow_outcome["execution_success_rate"] + 0.2)  # 基于成功率的置信度
        }
        
        return final_conclusion
    
    # ==================== 完整工作流 ====================
    
    def run_complete_workflow(self, 
                             scientific_question: str,
                             num_queries: int = 3,
                             num_hypotheses_per_query: int = 3,
                             top_n_strategies: int = 5,
                             pdf_dir: str = "papers",
                             index_dir: str = "index",
                             search_papers: bool = False) -> Dict[str, Any]:
        """
        运行完整工作流（向后兼容包装器）
        
        注意：此方法现在调用有界闭环工作流，但保持原有的输出结构
        以确保向后兼容性
        """
        # 运行有界闭环工作流
        closed_loop_result = self.run_bounded_closed_loop_workflow(
            scientific_question=scientific_question,
            num_queries=num_queries,
            num_hypotheses_per_query=num_hypotheses_per_query,
            top_n_strategies=top_n_strategies,
            pdf_dir=pdf_dir,
            index_dir=index_dir,
            search_papers=search_papers,
            max_reflection_rounds=2,  # 默认2轮反射
            max_unrecoverable_failures=3
        )
        
        # 转换结果为原始格式以保持向后兼容
        return self._convert_to_legacy_format(closed_loop_result)
    
    def _convert_to_legacy_format(self, closed_loop_result: Dict) -> Dict:
        """
        将有界闭环结果转换为旧格式以保持向后兼容
        
        参数:
            closed_loop_result: 有界闭环结果
        
        返回:
            向后兼容的结果字典
        """
        # 提取关键信息
        final_conclusion = closed_loop_result.get("final_conclusion", {})
        
        # 构建向后兼容的结构
        legacy_result = {
            "scientific_question": closed_loop_result.get("scientific_question", ""),
            "status": "success" if closed_loop_result.get("status", "") != "error" else "error",
            "phases": {},
            "summary": {}
        }
        
        # 检索阶段
        retrieval_phase = closed_loop_result.get("retrieval_phase", {})
        legacy_result["phases"]["retrieval"] = {
            "keywords": retrieval_phase.get("top_keywords", []),
            "literature_review": retrieval_phase.get("literature_review", ""),
            "relevant_papers": retrieval_phase.get("relevant_papers", [])
        }
        
        # 假设阶段
        hypothesis_phase = closed_loop_result.get("hypothesis_phase", {})
        legacy_result["phases"]["hypothesis"] = {
            "ranked_strategies": hypothesis_phase.get("ranked_strategies", []),
            "top_n_strategies": hypothesis_phase.get("top_n_strategies", []),
            "optimized_hypotheses": hypothesis_phase.get("optimized_hypotheses", [])
        }
        
        # 规划阶段（取最后一轮）
        planning_rounds = closed_loop_result.get("planning_rounds", [])
        if planning_rounds:
            last_planning = planning_rounds[-1].get("result", {})
            legacy_result["phases"]["planner"] = {
                "optimized_protocols": last_planning.get("optimized_protocols", []),
                "original_protocols": last_planning.get("original_protocols", []),
                "optimization_ratio": last_planning.get("optimization_ratio", 1.0)
            }
        else:
            legacy_result["phases"]["planner"] = {"optimized_protocols": []}
        
        # 执行阶段（取最后一轮）
        execution_rounds = closed_loop_result.get("execution_rounds", [])
        if execution_rounds:
            last_execution = execution_rounds[-1].get("result", {})
            legacy_result["phases"]["execution"] = {
                "overall_success_rate": last_execution.get("overall_success_rate", 0),
                "total_steps": last_execution.get("total_steps", 0),
                "successful_steps": last_execution.get("successful_steps", 0),
                "failed_steps": last_execution.get("failed_steps", 0),
                "total_duration": last_execution.get("total_duration", 0),
                "final_output": last_execution.get("final_output", None)
            }
        else:
            legacy_result["phases"]["execution"] = {"overall_success_rate": 0}
        
        # 总结
        legacy_result["summary"] = {
            "total_phases": 4,
            "overall_success_rate": final_conclusion.get("workflow_outcome", {}).get("execution_success_rate", 0),
            "total_duration": closed_loop_result.get("total_duration_seconds", 0),
            "phases_completed": ["retrieval", "hypothesis", "planner", "execution"]
        }
        
        # 输出文件（如果可用）
        if "output_files" in closed_loop_result:
            legacy_result["output_files"] = closed_loop_result["output_files"]
        
        # 如果有错误
        if "error" in closed_loop_result:
            legacy_result["error"] = closed_loop_result["error"]
            legacy_result["status"] = "error"
        
        return legacy_result

    
    # ==================== 模拟模式 ====================
    
    def run_mock_workflow(self, scientific_question: str) -> Dict:
        """Run a mock workflow (no API key needed).
        
        Args:
            scientific_question: Scientific question
        
        Returns:
            Mock workflow result
        """
        logger.info("运行模拟工作流（无需API密钥）...")
        
        mock_result = {
            "scientific_question": scientific_question,
            "status": "mock_success",
            "phases": {
                "retrieval": {
                    "keywords": ["mock_keyword_1", "mock_keyword_2"],
                    "literature_review": f"Mock literature review for: {scientific_question[:50]}..."
                },
                "hypothesis": {
                    "ranked_strategies": [
                        {"strategy_name": "Mock_Strategy_1", "reasoning": "Mock DFT calculation"},
                        {"strategy_name": "Mock_Strategy_2", "reasoning": "Mock transition state search"}
                    ]
                },
                "planner": {
                    "optimized_protocols": [
                        {
                            "strategy_name": "Mock_Strategy_1",
                            "Steps": [
                                {
                                    "Step_number": 1,
                                    "Description": "Mock Gaussian input generation",
                                    "Tool": "generate_gaussian_code",
                                    "Input": "Mock input",
                                    "Output": "Mock Gaussian keywords"
                                }
                            ]
                        }
                    ]
                },
                "execution": {
                    "overall_success_rate": 1.0,
                    "total_steps": 1,
                    "successful_steps": 1
                }
            },
            "summary": {
                "total_phases": 4,
                "overall_success_rate": 1.0,
                "total_duration": 2.5
            }
        }
        
        # 保存模拟结果
        mock_file = os.path.join(self.output_dir, "mock_workflow_result.json")
        with open(mock_file, "w", encoding="utf-8") as f:
            json.dump(mock_result, f, indent=2, ensure_ascii=False)
        
        logger.info(f"模拟工作流完成，结果保存到: {mock_file}")
        return mock_result


# ==================== 主函数 ====================

def main():
    """命令行入口"""
    parser = argparse.ArgumentParser(description="计算化学多智能体系统控制器")
    parser.add_argument("--question", "-q", required=True, help="科学问题")
    parser.add_argument("--api-key", help="Deepseek API密钥（也可通过环境变量设置）")
    parser.add_argument("--work-dir", help="工作目录（默认：当前目录）")
    parser.add_argument("--toolpool", help="工具定义文件路径")
    parser.add_argument("--expert-model-name", default="qwen2.5-7b-instruct", help="专家模型名称")
    parser.add_argument("--expert-model-path", default=None, help="专家模型本地路径")
    parser.add_argument("--expert-backend", default="local_hf", help="专家模型后端")
    parser.add_argument("--disable-expert-review", action="store_true", help="禁用专家复核/分析")
    parser.add_argument("--num-queries", type=int, default=3, help="查询数量（默认：3）")
    parser.add_argument("--num-hypotheses", type=int, default=3, help="每个查询的假设数量（默认：3）")
    parser.add_argument("--top-n", type=int, default=5, help="前N个策略（默认：5）")
    parser.add_argument("--pdf-dir", default="papers", help="PDF存储目录（默认：papers）")
    parser.add_argument("--index-dir", default="index", help="索引存储目录（默认：index）")
    parser.add_argument("--no-search", action="store_true", help="跳过论文检索")
    parser.add_argument("--mock", action="store_true", help="运行模拟模式（无需API密钥）")
    
    args = parser.parse_args()
    
    # 创建控制器
    controller = ChemistryMultiAgentController(
        deepseek_api_key=args.api_key,
        work_dir=args.work_dir,
        toolpool_path=args.toolpool,
        expert_model_name=args.expert_model_name,
        expert_model_path=args.expert_model_path,
        expert_backend=args.expert_backend,
        enable_expert_review=not args.disable_expert_review,
    )
    
    if args.mock:
        # 模拟模式
        result = controller.run_mock_workflow(args.question)
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
