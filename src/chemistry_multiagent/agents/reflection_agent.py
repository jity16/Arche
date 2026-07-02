#!/usr/bin/env python3
"""
Reflection Agent - 执行后反思智能体

功能：
1. 对执行结果进行结构化、可审计的反思
2. 识别技术问题与科学证据冲突
3. 给出保守的闭环决策（accept / revise_workflow / revise_hypothesis / stop）
4. 为后续控制器提供可执行的修订指令

闭环目标：
Retrieval → Hypothesis → Planner → Execution → Reflection → Revision Decision
"""

import os
import sys
import logging
from typing import Dict, List, Any, Optional, Tuple

# 添加项目根目录到Python路径
project_root = os.path.dirname(os.path.abspath(__file__))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

try:
    from utils.llm_api import call_deepseek_api
    LLM_API_AVAILABLE = True
except ImportError:
    LLM_API_AVAILABLE = False

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class ReflectionAgent:
    """执行后反思智能体（默认规则驱动，支持可选LLM扩展钩子）。"""

    def __init__(
        self,
        deepseek_api_key: Optional[str] = None,
        max_reflection_rounds: int = 3,
        max_retry_count: int = 2,
        enable_llm_extension: bool = False,
    ):
        """
        初始化ReflectionAgent。

        参数:
            deepseek_api_key: DeepSeek API密钥（仅用于可选扩展）
            max_reflection_rounds: 最大反思轮次
            max_retry_count: 最大重试次数阈值
            enable_llm_extension: 是否启用LLM扩展建议（默认关闭）
        """
        self.deepseek_api_key = deepseek_api_key or os.environ.get("DEEPSEEK_API_KEY", "")
        self.max_reflection_rounds = max_reflection_rounds
        self.max_retry_count = max_retry_count
        self.enable_llm_extension = enable_llm_extension and LLM_API_AVAILABLE

        self.technical_error_categories = {
            "tool_not_found",
            "input_missing",
            "format_mismatch",
            "parsing_failure",
            "unknown_error",
            "gaussian_nonconvergence",
            "gaussian_scf_failure",
            "ts_invalid",
            "irc_failure",
        }

        logger.info("Reflection Agent 初始化完成")

    # ==================== 通用工具 ====================

    def _to_dict(self, obj: Any) -> Dict[str, Any]:
        """将对象尽量转换为字典，兼容dataclass/普通对象/字典。"""
        if obj is None:
            return {}
        if isinstance(obj, dict):
            return obj
        if hasattr(obj, "__dataclass_fields__"):
            return {k: getattr(obj, k, None) for k in obj.__dataclass_fields__.keys()}
        if hasattr(obj, "__dict__"):
            return dict(obj.__dict__)
        return {}

    def _obj_get(self, obj: Any, key: str, default: Any = None) -> Any:
        """兼容字典与对象属性读取。"""
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)

    def _aggregate_bool(self, values: List[Optional[bool]]) -> Optional[bool]:
        """聚合布尔值：有False则False；无False且有True则True；否则None。"""
        clean = [v for v in values if isinstance(v, bool)]
        if not clean:
            return None
        if any(v is False for v in clean):
            return False
        return True

    def _extract_step_identity(self, step: Any) -> Dict[str, Any]:
        """提取步骤标识信息，兼容新旧执行schema。"""
        step_id = self._obj_get(step, "step_id", None)
        if step_id is None:
            step_id = self._obj_get(step, "step_number", None)

        step_name = self._obj_get(step, "step_name", None)
        if not step_name:
            step_name = self._obj_get(step, "description", "")

        tool_name = self._obj_get(step, "tool_name", None)
        if not tool_name:
            tool_obj = self._obj_get(step, "tool", None)
            if isinstance(tool_obj, dict):
                tool_name = tool_obj.get("tool_name") or tool_obj.get("name")
            elif tool_obj is not None:
                tool_name = getattr(tool_obj, "tool_name", None) or getattr(tool_obj, "name", None)

        error_info = self._obj_get(step, "error_info", {}) or {}
        if not isinstance(error_info, dict):
            error_info = {}

        error_message = error_info.get("message")
        if error_message is None:
            error_message = self._obj_get(step, "error", None)

        return {
            "step_id": step_id,
            "step_name": step_name,
            "tool": tool_name,
            "error_info": error_info,
            "error_message": error_message,
        }

    def _extract_workflow_signals(self, workflow: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """轻量提取workflow结构信号，用于反思规则。"""
        steps = []
        if isinstance(workflow, dict):
            if isinstance(workflow.get("Steps"), list):
                steps = workflow.get("Steps")
            elif isinstance(workflow.get("steps"), list):
                steps = workflow.get("steps")

        has_freq_step = False
        has_irc_step = False
        has_result_validation_step = False
        ts_intent = False

        for st in steps:
            if isinstance(st, dict):
                text = " ".join([
                    str(st.get("step_name", "")),
                    str(st.get("Description", "")),
                    str(st.get("description", "")),
                    str(st.get("Tool", "")),
                    str(st.get("tool_name", "")),
                    str(st.get("Output", "")),
                    str(st.get("output", "")),
                ]).lower()
            else:
                text = " ".join([
                    str(getattr(st, "step_name", "")),
                    str(getattr(st, "description", "")),
                    str(getattr(st, "tool_name", "")),
                ]).lower()

            if "transition state" in text or " ts " in f" {text} " or "ts_" in text or "_ts" in text:
                ts_intent = True
            if "frequency" in text or " freq " in f" {text} " or "freq" in text:
                has_freq_step = True
            if "irc" in text:
                has_irc_step = True
            if any(k in text for k in ["validate", "validation", "parse", "parser", "check", "verify"]):
                has_result_validation_step = True

        return {
            "step_count": len(steps),
            "ts_intent": ts_intent,
            "has_freq_step": has_freq_step,
            "has_irc_step": has_irc_step,
            "has_result_validation_step": has_result_validation_step,
        }

    def extract_expert_signals(
        self,
        execution_result: Any,
        workflow: Optional[Dict[str, Any]] = None,
        scientific_evidence: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """提取Planner/Execution中可用的专家信号（可选字段，缺失时安全回退）。"""
        scientific_evidence = scientific_evidence or {}

        execution_steps = self._obj_get(execution_result, "steps", []) or []
        if not isinstance(execution_steps, list):
            execution_steps = []

        workflow_steps = []
        if isinstance(workflow, dict):
            if isinstance(workflow.get("Steps"), list):
                workflow_steps = workflow.get("Steps")
            elif isinstance(workflow.get("steps"), list):
                workflow_steps = workflow.get("steps")

        keyword_risks: List[str] = []
        route_mismatch_detected = False
        gaussian_review_statuses: List[str] = []
        gaussian_diagnosis_summary: List[Dict[str, Any]] = []
        confidence_values: List[float] = []
        recommended_next_actions: List[str] = []

        for st in workflow_steps:
            if not isinstance(st, dict):
                continue
            review = st.get("gaussian_review", {})
            if not isinstance(review, dict):
                continue
            review_status = review.get("review_status")
            if isinstance(review_status, str):
                gaussian_review_statuses.append(review_status)
            risks = review.get("keyword_risks")
            if isinstance(risks, list):
                keyword_risks.extend([str(x) for x in risks if str(x).strip()])
            elif isinstance(risks, str) and risks.strip():
                keyword_risks.append(risks.strip())

            if review.get("route_mismatch_detected") is True:
                route_mismatch_detected = True

            if review_status in {"revised", "rejected"}:
                route_mismatch_detected = True

        for st in execution_steps:
            step_id = self._obj_get(st, "step_id", self._obj_get(st, "step_number"))
            g_analysis = self._obj_get(st, "gaussian_analysis", {}) or {}
            err_analysis = self._obj_get(st, "expert_error_analysis", {}) or {}

            if isinstance(g_analysis, dict) and g_analysis:
                gaussian_diagnosis_summary.append({
                    "step_id": step_id,
                    "type": "gaussian_analysis",
                    "status": g_analysis.get("status"),
                    "job_type": g_analysis.get("job_type"),
                })
                action = g_analysis.get("recommended_next_action")
                if isinstance(action, str) and action.strip():
                    recommended_next_actions.append(action.strip())
                conf = g_analysis.get("expert_confidence")
                if isinstance(conf, (int, float)):
                    confidence_values.append(float(conf))
                if g_analysis.get("route_mismatch_detected") is True:
                    route_mismatch_detected = True

            if isinstance(err_analysis, dict) and err_analysis:
                gaussian_diagnosis_summary.append({
                    "step_id": step_id,
                    "type": "expert_error_analysis",
                    "status": err_analysis.get("status"),
                    "error_category": err_analysis.get("error_category"),
                })
                if err_analysis.get("route_mismatch_detected") is True:
                    route_mismatch_detected = True

        # 控制器汇总透传（若存在）
        ext_review_hist = scientific_evidence.get("expert_review_history", [])
        if isinstance(ext_review_hist, list):
            for item in ext_review_hist:
                if not isinstance(item, dict):
                    continue
                summary = item.get("summary", {})
                if isinstance(summary, dict):
                    if summary.get("reviewed_steps", 0):
                        gaussian_diagnosis_summary.append({
                            "type": "planner_review_summary",
                            "reviewed_steps": summary.get("reviewed_steps"),
                            "expert_model_name": summary.get("expert_model_name"),
                        })

        ext_gauss_hist = scientific_evidence.get("gaussian_analysis_history", [])
        if isinstance(ext_gauss_hist, list):
            for item in ext_gauss_hist:
                if not isinstance(item, dict):
                    continue
                analysis = item.get("analysis", {})
                if isinstance(analysis, dict):
                    action = analysis.get("recommended_next_action")
                    if isinstance(action, str) and action.strip():
                        recommended_next_actions.append(action.strip())
                    conf = analysis.get("expert_confidence")
                    if isinstance(conf, (int, float)):
                        confidence_values.append(float(conf))

        keyword_risks = sorted(set([k for k in keyword_risks if k]))
        recommended_next_actions = sorted(set([a for a in recommended_next_actions if a]))
        expert_confidence = (sum(confidence_values) / len(confidence_values)) if confidence_values else None

        # 技术可靠性与科学支持度（轻量等级）
        workflow_technical_reliability = "unknown"
        if route_mismatch_detected:
            workflow_technical_reliability = "low"
        elif gaussian_diagnosis_summary:
            workflow_technical_reliability = "medium"

        scientific_support_level = "unknown"
        if scientific_evidence.get("barrier_consistent") is True and scientific_evidence.get("irc_connectivity_consistent") is True:
            scientific_support_level = "high"
        elif scientific_evidence.get("barrier_consistent") is False or scientific_evidence.get("irc_connectivity_consistent") is False:
            scientific_support_level = "low"

        expert_flags = []
        if route_mismatch_detected:
            expert_flags.append("route_mismatch_detected")
        if keyword_risks:
            expert_flags.append("keyword_risks_detected")
        if any(s in {"rejected", "revised"} for s in gaussian_review_statuses):
            expert_flags.append("planner_gaussian_review_not_approved")

        return {
            "expert_flags": expert_flags,
            "keyword_risk_summary": keyword_risks,
            "route_mismatch_detected": route_mismatch_detected,
            "gaussian_diagnosis_summary": gaussian_diagnosis_summary,
            "expert_confidence": expert_confidence,
            "workflow_technical_reliability": workflow_technical_reliability,
            "scientific_support_level": scientific_support_level,
            "recommended_next_actions": recommended_next_actions,
        }

    def merge_rule_and_expert_evidence(
        self,
        evidence_summary: Dict[str, Any],
        expert_signals: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """将专家信号并入规则摘要，不替换原字段。"""
        merged = dict(evidence_summary or {})
        expert_signals = expert_signals or {}
        merged["expert_flags"] = expert_signals.get("expert_flags", [])
        merged["keyword_risk_summary"] = expert_signals.get("keyword_risk_summary", [])
        merged["route_mismatch_detected"] = bool(expert_signals.get("route_mismatch_detected", False))
        merged["gaussian_diagnosis_summary"] = expert_signals.get("gaussian_diagnosis_summary", [])
        merged["expert_confidence"] = expert_signals.get("expert_confidence")
        merged["workflow_technical_reliability"] = expert_signals.get("workflow_technical_reliability", "unknown")
        merged["scientific_support_level"] = expert_signals.get("scientific_support_level", "unknown")
        merged["expert_recommended_next_actions"] = expert_signals.get("recommended_next_actions", [])
        return merged

    # ==================== 核心辅助方法 ====================

    def summarize_evidence(
        self,
        execution_result: Any,
        scientific_evidence: Optional[Dict[str, Any]] = None,
        workflow: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        汇总执行与科学证据，生成结构化摘要。

        返回字段示例：
            normal_termination, convergence, n_imag_freq, irc_verified,
            energy_summary, failed_steps, error_categories
        """
        result_dict = self._to_dict(execution_result)
        summary = self._obj_get(execution_result, "summary", {}) if execution_result is not None else {}
        if not isinstance(summary, dict):
            summary = {}

        steps = self._obj_get(execution_result, "steps", result_dict.get("steps", []))
        if steps is None:
            steps = []

        failed_steps: List[Dict[str, Any]] = []
        error_categories: List[str] = []
        normal_termination_values: List[Optional[bool]] = []
        convergence_values: List[Optional[bool]] = []
        n_imag_freq_values: List[int] = []
        irc_verified_values: List[Optional[bool]] = []
        scf_energies: List[float] = []
        ts_validation_failures: List[Dict[str, Any]] = []

        for step in steps:
            step_status = self._obj_get(step, "status", "")
            step_info = self._extract_step_identity(step)
            step_id = step_info.get("step_id")
            step_name = step_info.get("step_name")

            if step_status == "failed":
                err_info = step_info.get("error_info") or {}
                err_message = step_info.get("error_message")
                if not err_info and err_message:
                    err_info = {"message": err_message}

                failed_steps.append(
                    {
                        "step_id": step_id,
                        "step_name": step_name,
                        "tool": step_info.get("tool"),
                        "error_info": err_info,
                        # 向后兼容字段
                        "step_number": step_id,
                        "description": step_name,
                        "error": err_message,
                    }
                )

                category = err_info.get("category") if isinstance(err_info, dict) else None
                if isinstance(category, str):
                    error_categories.append(category)
                else:
                    error_categories.append("unknown_error")

            parsed = self._obj_get(step, "parsed_results", {}) or {}
            if isinstance(parsed, dict):
                normal_termination_values.append(parsed.get("normal_termination"))
                convergence_values.append(parsed.get("converged"))
                if isinstance(parsed.get("n_imag_freq"), int):
                    n_imag_freq_values.append(parsed.get("n_imag_freq"))
                irc_verified_values.append(parsed.get("irc_verified"))
                if isinstance(parsed.get("scf_energy"), (int, float)):
                    scf_energies.append(float(parsed.get("scf_energy")))

            validation = self._obj_get(step, "validation", {}) or {}
            if isinstance(validation, dict):
                vtype = validation.get("validation_type")
                checks = validation.get("checks", {}) if isinstance(validation.get("checks"), dict) else {}
                if vtype == "ts":
                    imag_ok = checks.get("exactly_one_imaginary")
                    if imag_ok is False:
                        ts_validation_failures.append(
                            {
                                "step_id": step_id,
                                "step_name": step_name,
                                "details": validation.get("details", "TS虚频特征不满足"),
                            }
                        )

        energy_summary = {
            "count": len(scf_energies),
            "min_scf_energy": min(scf_energies) if scf_energies else None,
            "max_scf_energy": max(scf_energies) if scf_energies else None,
            "latest_scf_energy": scf_energies[-1] if scf_energies else None,
        }

        evidence_summary = {
            "overall_status": self._obj_get(execution_result, "overall_status", result_dict.get("overall_status")),
            "workflow_outcome": self._obj_get(execution_result, "workflow_outcome", summary.get("workflow_outcome")),
            "normal_termination": self._aggregate_bool(normal_termination_values),
            "convergence": self._aggregate_bool(convergence_values),
            "n_imag_freq": n_imag_freq_values if n_imag_freq_values else None,
            "irc_verified": self._aggregate_bool(irc_verified_values),
            "energy_summary": energy_summary,
            "failed_steps": failed_steps,
            "error_categories": sorted(set(error_categories)),
            "ts_validation_failures": ts_validation_failures,
            "validation_overview": summary.get("validation_overview", {}),
            "scientific_evidence": scientific_evidence or {},
        }

        expert_signals = self.extract_expert_signals(
            execution_result=execution_result,
            workflow=workflow,
            scientific_evidence=scientific_evidence,
        )
        return self.merge_rule_and_expert_evidence(evidence_summary, expert_signals)

    def identify_problems(
        self,
        selected_strategy: Optional[Dict[str, Any]],
        workflow: Optional[Dict[str, Any]],
        execution_result: Any,
        evidence_summary: Dict[str, Any],
        prior_reflections: Optional[List[Dict[str, Any]]] = None,
        retry_count: int = 0,
        reflection_round: int = 0,
    ) -> List[Dict[str, Any]]:
        """识别执行和科学层面的主要问题。"""
        problems: List[Dict[str, Any]] = []

        prior_rounds = len(prior_reflections or [])
        if reflection_round >= self.max_reflection_rounds or prior_rounds >= self.max_reflection_rounds:
            problems.append(
                {
                    "type": "control_limit",
                    "code": "max_reflection_rounds_exceeded",
                    "severity": "high",
                    "message": "反思轮次超过阈值，建议停止继续自动修订",
                }
            )

        ts_failures = evidence_summary.get("ts_validation_failures") or []
        if ts_failures:
            problems.append(
                {
                    "type": "scientific_validation",
                    "code": "ts_validation_failed",
                    "severity": "high",
                    "message": "TS验证失败（虚频数量或特征异常）",
                    "details": ts_failures,
                }
            )

        failed_steps = evidence_summary.get("failed_steps") or []
        error_categories = set(evidence_summary.get("error_categories") or [])
        technical_categories = sorted(c for c in error_categories if c in self.technical_error_categories)
        non_technical_categories = sorted(c for c in error_categories if c not in self.technical_error_categories)
        if failed_steps and (technical_categories or not error_categories):
            problems.append(
                {
                    "type": "technical_execution",
                    "code": "technical_failures",
                    "severity": "medium",
                    "message": "失败主要来自工具/格式/收敛/解析等工作流技术问题",
                    "details": {
                        "technical_error_categories": technical_categories,
                        "other_error_categories": non_technical_categories,
                        "failed_step_count": len(failed_steps),
                    },
                }
            )

        workflow_signals = self._extract_workflow_signals(workflow)
        if workflow_signals.get("ts_intent") and not workflow_signals.get("has_freq_step"):
            problems.append(
                {
                    "type": "workflow_design",
                    "code": "missing_frequency_for_ts_validation",
                    "severity": "medium",
                    "message": "工作流包含TS意图但缺少频率分析步骤，无法稳健确认TS特征",
                    "details": workflow_signals,
                }
            )

        if workflow_signals.get("ts_intent") and not workflow_signals.get("has_irc_step"):
            problems.append(
                {
                    "type": "workflow_design",
                    "code": "missing_irc_for_ts_pathway",
                    "severity": "medium",
                    "message": "工作流包含TS意图但缺少IRC步骤，难以确认反应路径连通性",
                    "details": workflow_signals,
                }
            )

        if workflow_signals.get("step_count", 0) > 0 and not workflow_signals.get("has_result_validation_step"):
            problems.append(
                {
                    "type": "workflow_design",
                    "code": "missing_result_validation_step",
                    "severity": "low",
                    "message": "工作流未显式包含结果解析/验证步骤，后验可审计性偏弱",
                    "details": workflow_signals,
                }
            )

        # 专家信号增强：优先识别Gaussian路线/关键词设计问题（技术性工作流问题）
        if evidence_summary.get("route_mismatch_detected") is True or (evidence_summary.get("keyword_risk_summary") or []):
            problems.append(
                {
                    "type": "workflow_design",
                    "code": "gaussian_route_or_keyword_flawed",
                    "severity": "high",
                    "message": "专家信号指示Gaussian路线或关键词设计存在风险，建议优先修订工作流",
                    "details": {
                        "route_mismatch_detected": evidence_summary.get("route_mismatch_detected"),
                        "keyword_risk_summary": evidence_summary.get("keyword_risk_summary", []),
                    },
                }
            )

        if retry_count >= self.max_retry_count and failed_steps:
            problems.append(
                {
                    "type": "control_limit",
                    "code": "max_retry_reached",
                    "severity": "high",
                    "message": "重试次数过多且仍存在失败步骤",
                    "details": {"retry_count": retry_count, "max_retry_count": self.max_retry_count},
                }
            )

        scientific = evidence_summary.get("scientific_evidence") or {}
        strategy_text = str(selected_strategy or "").lower()

        barrier_inconsistent = scientific.get("barrier_consistent") is False or scientific.get("barrier_inconsistent") is True
        if barrier_inconsistent:
            problems.append(
                {
                    "type": "hypothesis_contradiction",
                    "code": "barrier_contradiction",
                    "severity": "high",
                    "message": "能垒证据与当前机理假设不一致",
                }
            )

        photo_related = any(k in strategy_text for k in ["photo", "photochemical", "excitation", "激发"])
        if photo_related and scientific.get("photochemical_feasible") is False:
            problems.append(
                {
                    "type": "hypothesis_contradiction",
                    "code": "photochemical_infeasible",
                    "severity": "high",
                    "message": "当前证据显示光化学激发路径不可行",
                }
            )

        if scientific.get("irc_connectivity_consistent") is False:
            problems.append(
                {
                    "type": "hypothesis_contradiction",
                    "code": "irc_connectivity_mismatch",
                    "severity": "high",
                    "message": "IRC连通性与目标反应路径不一致",
                }
            )

        # 专家信号增强：技术可接受但科学支持不足 -> 倾向修订假设
        runtime_status = evidence_summary.get("overall_status")
        has_technical_failures = any(p.get("code") in {"technical_failures", "gaussian_route_or_keyword_flawed", "ts_validation_failed"} for p in problems)
        scientific_support_level = evidence_summary.get("scientific_support_level", "unknown")
        if runtime_status in {"success", "partial_success"} and (not has_technical_failures) and scientific_support_level == "low":
            problems.append(
                {
                    "type": "hypothesis_contradiction",
                    "code": "expert_scientific_inconsistency",
                    "severity": "high",
                    "message": "专家信号显示技术执行基本可接受，但对当前机理假设支持不足",
                }
            )

        if evidence_summary.get("irc_verified") is False and "irc" in strategy_text:
            problems.append(
                {
                    "type": "hypothesis_contradiction",
                    "code": "irc_not_verified",
                    "severity": "medium",
                    "message": "策略依赖IRC，但当前结果未验证IRC路径",
                }
            )

        return problems

    def make_decision(
        self,
        evidence_summary: Dict[str, Any],
        identified_problems: List[Dict[str, Any]],
        reflection_round: int = 0,
        retry_count: int = 0,
    ) -> Tuple[str, str]:
        """根据证据与问题列表生成保守决策。"""
        codes = {p.get("code") for p in identified_problems}
        types = {p.get("type") for p in identified_problems}

        if "max_reflection_rounds_exceeded" in codes:
            return "stop", "反思轮次超过阈值，停止自动修订以避免无效循环"

        if "max_retry_reached" in codes and "technical_execution" in types:
            return "stop", "技术性失败在多次重试后仍未解决，建议停止并人工介入"

        if "gaussian_route_or_keyword_flawed" in codes:
            return "revise_workflow", "专家信号显示Gaussian关键词/路线设计存在缺陷，优先修订工作流"

        if "hypothesis_contradiction" in types:
            return "revise_hypothesis", "执行结果可用但科学证据与当前假设冲突，建议修订假设"

        if "ts_validation_failed" in codes:
            return "revise_workflow", "TS关键验证失败，优先修订工作流与计算设置"

        if "technical_failures" in codes or "workflow_design" in types:
            return "revise_workflow", "存在流程技术性问题或工作流设计缺口，建议先修订工作流"

        runtime_status = evidence_summary.get("overall_status")
        workflow_outcome = evidence_summary.get("workflow_outcome")
        val_overview = evidence_summary.get("validation_overview", {})
        critical_failed = val_overview.get("critical_failed", 0) if isinstance(val_overview, dict) else 0
        failed_steps = evidence_summary.get("failed_steps") or []

        if runtime_status == "success" and critical_failed == 0 and not failed_steps and workflow_outcome == "supported":
            return "accept", "执行成功、工作流结果受支持且无关键验证失败，当前结果可接受"

        if runtime_status == "success" and workflow_outcome == "partially_supported":
            return "revise_workflow", "当前仅部分支持，证据尚不足以直接接受，建议继续修订工作流"

        # 技术层基本可接受但专家判断科学支持弱：优先修订假设
        if runtime_status in {"success", "partial_success"} and evidence_summary.get("workflow_technical_reliability") in {"medium", "high"} and evidence_summary.get("scientific_support_level") == "low":
            return "revise_hypothesis", "技术执行基本可接受但科学支持不足，建议修订假设"

        if runtime_status == "failed":
            if retry_count >= self.max_retry_count or reflection_round >= self.max_reflection_rounds:
                return "stop", "执行失败且已达到迭代上限，建议停止"
            return "revise_workflow", "执行失败但仍可继续修订工作流"

        if runtime_status == "partial_success":
            return "revise_workflow", "部分成功，建议修订工作流以提高关键步骤成功率"

        return "revise_workflow", "证据不充分或存在未决风险，保守建议修订工作流"

    def build_workflow_revision_instructions(
        self,
        identified_problems: List[Dict[str, Any]],
        evidence_summary: Dict[str, Any],
        workflow: Optional[Dict[str, Any]] = None,
    ) -> List[str]:
        """生成工作流修订建议。"""
        instructions: List[str] = []
        codes = {p.get("code") for p in identified_problems}

        if "ts_validation_failed" in codes:
            instructions.append("对TS步骤增加强制检查：仅接受恰好1个虚频的结构。")
            instructions.append("在TS失败时自动加入备选初猜与更稳健的优化关键词，并重新执行TS+Freq。")

        if "technical_failures" in codes:
            instructions.append("针对失败步骤补充输入/格式预检，必要时插入显式格式转换子步骤。")
            instructions.append("将高频错误类别映射到定向修复动作（tool_not_found、format_mismatch、input_missing）。")

        if "max_retry_reached" in codes:
            instructions.append("降低自动重试次数并提升单次失败诊断信息，避免重复执行同一失败路径。")

        if "missing_frequency_for_ts_validation" in codes:
            instructions.append("为TS相关流程补充频率计算步骤，并在TS后强制执行虚频检查。")

        if "missing_irc_for_ts_pathway" in codes:
            instructions.append("在TS确认后补充IRC步骤，用于验证反应路径连通性。")

        if "missing_result_validation_step" in codes:
            instructions.append("补充结果解析/验证步骤，明确输出normal_termination、收敛性与关键频率指标。")

        if "gaussian_route_or_keyword_flawed" in codes:
            instructions.append("根据专家反馈修订Gaussian route/keywords，确保任务类型、溶剂、基组与元素兼容。")
            instructions.append("将关键词风险项写入预检清单，并在执行前强制检查。")

        if workflow:
            workflow_signals = self._extract_workflow_signals(workflow)
            if workflow_signals.get("ts_intent") and not workflow_signals.get("has_freq_step"):
                instructions.append("检测到TS意图但无Freq步骤：建议在TS优化后立即执行Freq。")

        if not instructions and evidence_summary.get("failed_steps"):
            instructions.append("优先修订失败步骤的工具选择与输入构建逻辑。")

        return instructions

    def build_hypothesis_revision_instructions(
        self,
        identified_problems: List[Dict[str, Any]],
        selected_strategy: Optional[Dict[str, Any]],
        evidence_summary: Dict[str, Any],
    ) -> List[str]:
        """生成假设修订建议。"""
        instructions: List[str] = []
        codes = {p.get("code") for p in identified_problems}

        if "barrier_contradiction" in codes:
            instructions.append("修订反应机理假设中的速控步与能垒排序假设，使其与当前能量剖面一致。")

        if "photochemical_infeasible" in codes:
            instructions.append("移除或弱化光激发主导机理，转向热反应或替代激发路径假设。")

        if "irc_connectivity_mismatch" in codes or "irc_not_verified" in codes:
            instructions.append("重新定义关键中间体连通关系，并补充与目标路径一致的反应通道假设。")

        if "expert_scientific_inconsistency" in codes:
            instructions.append("在保留技术可行计算框架的前提下，修订机理假设与关键反应路径解释。")

        if not instructions and identified_problems:
            instructions.append("根据当前证据收缩假设空间，优先保留可被现有计算支持的机制分支。")

        return instructions

    # ==================== 可选扩展 ====================

    def _optional_llm_reflection(
        self,
        decision_context: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """
        可选LLM扩展钩子（默认不启用，不影响规则决策主链路）。
        """
        if not self.enable_llm_extension:
            return None
        if not LLM_API_AVAILABLE:
            return None

        prompt = (
            "Provide up to 3 concise risk-focused reflection notes as JSON keys: "
            "additional_risks, optional_actions, confidence_adjustment.\n"
            f"Context: {decision_context}"
        )

        try:
            response = call_deepseek_api(
                [
                    {"role": "system", "content": "You are a conservative computational chemistry reflection assistant."},
                    {"role": "user", "content": prompt},
                ],
                model=os.environ.get("DEEPSEEK_MODEL", "interns2-preview-sft"),
                max_tokens=400,
            )
            return {"llm_note": response.strip()}
        except Exception as e:
            logger.warning(f"LLM扩展反思失败: {e}")
            return None

    # ==================== 主入口 ====================

    def reflect(
        self,
        selected_strategy: Optional[Dict[str, Any]] = None,
        workflow: Optional[Dict[str, Any]] = None,
        execution_result: Any = None,
        scientific_evidence: Optional[Dict[str, Any]] = None,
        prior_reflections: Optional[List[Dict[str, Any]]] = None,
        retry_count: int = 0,
        reflection_round: int = 0,
    ) -> Dict[str, Any]:
        """
        执行结构化反思并输出决策。

        返回:
            {
                "decision": "accept" | "revise_workflow" | "revise_hypothesis" | "stop",
                "reasoning": str,
                "evidence_summary": {...},
                "identified_problems": [...],
                "recommended_actions": [...],
                "workflow_revision_instructions": [...],
                "hypothesis_revision_instructions": [...],
                "updated_queries": [...],
                "confidence": float
            }
        """
        evidence_summary = self.summarize_evidence(
            execution_result,
            scientific_evidence,
            workflow=workflow,
        )

        identified_problems = self.identify_problems(
            selected_strategy=selected_strategy,
            workflow=workflow,
            execution_result=execution_result,
            evidence_summary=evidence_summary,
            prior_reflections=prior_reflections,
            retry_count=retry_count,
            reflection_round=reflection_round,
        )

        decision, reasoning = self.make_decision(
            evidence_summary=evidence_summary,
            identified_problems=identified_problems,
            reflection_round=reflection_round,
            retry_count=retry_count,
        )

        workflow_revision_instructions: List[str] = []
        hypothesis_revision_instructions: List[str] = []

        if decision == "revise_workflow":
            workflow_revision_instructions = self.build_workflow_revision_instructions(
                identified_problems=identified_problems,
                evidence_summary=evidence_summary,
                workflow=workflow,
            )
        elif decision == "revise_hypothesis":
            hypothesis_revision_instructions = self.build_hypothesis_revision_instructions(
                identified_problems=identified_problems,
                selected_strategy=selected_strategy,
                evidence_summary=evidence_summary,
            )

        updated_queries: List[str] = []
        if decision == "revise_hypothesis":
            if any(p.get("code") == "barrier_contradiction" for p in identified_problems):
                updated_queries.append("What alternative low-barrier mechanisms are consistent with current computed barrier trends?")
            if any(p.get("code") == "photochemical_infeasible" for p in identified_problems):
                updated_queries.append("What non-photochemical pathways could explain the observed reactivity under current conditions?")
            if any(p.get("code") in {"irc_connectivity_mismatch", "irc_not_verified"} for p in identified_problems):
                updated_queries.append("Which alternative transition states and IRC pathways connect the intended reactant-product pair?")

        recommended_actions: List[str] = []
        if decision == "accept":
            recommended_actions.append("保留当前策略并进入结果归档/报告阶段。")
        elif decision == "revise_workflow":
            recommended_actions.extend(workflow_revision_instructions)
        elif decision == "revise_hypothesis":
            recommended_actions.extend(hypothesis_revision_instructions)
            if updated_queries:
                recommended_actions.append("基于updated_queries触发检索与假设再生成。")
        elif decision == "stop":
            recommended_actions.append("停止自动迭代，转人工审阅关键失败原因。")

        for action in evidence_summary.get("expert_recommended_next_actions", []) or []:
            if isinstance(action, str) and action and action not in recommended_actions:
                recommended_actions.append(action)

        # 置信度：规则主导，保守估计
        confidence = 0.55
        if decision == "accept":
            confidence = 0.80 if not identified_problems else 0.68
        elif decision == "stop":
            confidence = 0.85
        elif decision == "revise_hypothesis":
            confidence = 0.78
        elif decision == "revise_workflow":
            confidence = 0.72

        # 若证据缺失，降低置信度
        if evidence_summary.get("normal_termination") is None and evidence_summary.get("energy_summary", {}).get("count", 0) == 0:
            confidence = min(confidence, 0.60)

        # 可选LLM扩展（不改变核心决策，仅附加备注）
        llm_extension = self._optional_llm_reflection(
            {
                "decision": decision,
                "reasoning": reasoning,
                "identified_problems": identified_problems,
                "evidence_summary": evidence_summary,
            }
        )
        if llm_extension:
            recommended_actions.append("[LLM扩展] 已生成附加风险备注（仅供参考）。")

        result = {
            "decision": decision,
            "reasoning": reasoning,
            "evidence_summary": evidence_summary,
            "identified_problems": identified_problems,
            "recommended_actions": recommended_actions,
            "workflow_revision_instructions": workflow_revision_instructions,
            "hypothesis_revision_instructions": hypothesis_revision_instructions,
            "updated_queries": updated_queries,
            "confidence": round(float(confidence), 3),
        }

        if llm_extension:
            result["llm_extension"] = llm_extension

        return result
