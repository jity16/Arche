#!/usr/bin/env python3
"""
Hypothesis Agent - 假设生成与优化智能体

功能：
1. 基于Retrieval agent提供的背景生成科学假设
2. 生成查询→根据查询生成假设→优化假设→排序策略
3. 整合：gen_hypothese.py + compare_hyp.py + rank_hypptheses.py

输入: 科学问题 + 文献背景
输出: 排序后的科学假设列表
"""

import os
import sys
import json
import re
import logging
import time
import copy
from typing import List, Dict, Any, Optional, Tuple
from itertools import combinations
from collections import defaultdict
import concurrent.futures

# 添加项目根目录到Python路径
project_root = os.path.dirname(os.path.abspath(__file__))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

try:
    from chemistry_multiagent.utils.llm_api import (
        call_deepseek_api,
        extract_json_from_response,
        safe_json_call,
        strip_reasoning,
    )
    # from utils.llm_api import call_deepseek_api, extract_json_from_response, safe_json_call, strip_reasoning
    LLM_API_AVAILABLE = True
except ImportError:
    LLM_API_AVAILABLE = False
    print("警告: utils.llm_api模块不可用，需要备用方案")

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class HypothesisAgent:
    """假设生成与优化智能体"""
    
    def __init__(self, deepseek_api_key: Optional[str] = None):
        """
        初始化假设智能体
        
        参数:
            deepseek_api_key: Deepseek API密钥
        """
        self.deepseek_api_key = deepseek_api_key or os.environ.get("DEEPSEEK_API_KEY", "")
        
        # 提示词模板
        self.query_prompt_template = [
            {
                "role": "system",
                "content": "You are a highly experienced computational chemistry research strategist. Your task is to propose mechanistic hypotheses and computational strategies that can guide the design and validation of chemical reactions, catalytic systems, or molecular properties. Your focus is on quantum chemical simulations, reaction mechanism exploration, catalyst modeling, and computational methodology development. You do not consider wet-lab or experimental synthesis procedures."
            },
            {
                "role": "user",
                "content": "Return a list of {num_queries} queries (separated by <>) that would be useful in doing research to generate detailed, mechanistic, and cross-disciplinary insights relevant to computational chemistry investigations of {research_topic}. These queries will be provided to a scientific team for in-depth literature review, so each should be comprehensive (30+ words), capturing broadly relevant information that spans computational chemistry, theoretical chemistry, electronic structure methods, reaction mechanisms, catalysis, spectroscopy, machine learning applications, and methodological innovations. Do not focus on specific molecules or experiments; instead, formulate queries that generalize to fundamental principles, computational approaches, and mechanistic underpinnings. You have {num_queries} queries, so spread them out to cover as much ground as possible: from theoretical background and computational methods to interdisciplinary perspectives and future directions. In formatting, don't number the queries, just output a string with {num_queries} queries separated by <>."
            }
        ]
        
        self.hypothesis_prompt_template = [
            {
                "role": "system",
                "content": """
                You are a professional computational chemist, highly experienced in quantum chemistry, 
                reaction mechanism exploration, and computational methodology development.
                Your task is to propose **computational mechanistic hypotheses** and corresponding strategies 
                that can be investigated using quantum chemical simulations and Python-based computational chemistry tools.
                
                Focus on hypotheses that can be tested using methods such as DFT, coupled-cluster, multireference approaches, 
                transition state optimization, intrinsic reaction coordinate analysis, solvation models, 
                basis set and functional selection, or dispersion corrections.
                Your proposals should emphasize feasibility within Gaussian and/or other Python-based packages 
                (e.g., PySCF, Psi4, ASE, RDKit). 
                Do not include wet-lab experiments, synthesis procedures, or biological assays. 

                **Output Format Specification (Strict Adherence Required):** 
                Your entire output MUST be a single, valid JSON array with exactly {num_hypotheses} objects.
                Each object must have the following structure:

                [
                {{
                    "strategy_name": "string",
                    "reasoning": "string"
                }}
                // ... up to {num_hypotheses} total
                ]
                """
            },
            {
                "role": "user",
                "content": (
                    "Generate exactly {num_hypotheses} distinct and scientifically rigorous computational chemistry hypotheses "
                    "that can be explored to address the following research question: {research_question}. "
                    "Here is some relevant background literature review to guide your proposals:\n"
                    "{lit_review_output}"
                ),
            },
        ]
        
        # 假设状态常量
        self.HYPOTHESIS_STATUS = {
            "active": "active",        # 新生成，待测试
            "supported": "supported",    # 有证据支持
            "rejected": "rejected",      # 被证据否定
            "uncertain": "uncertain",    # 证据不足或矛盾
            "pending_revision": "pending_revision"  # 需要修订
        }
        
        # 假设生命周期方法
        self.hypothesis_lifecycle = {
            "initial": "active",
            "supported": ["active", "uncertain"],
            "rejected": ["active", "uncertain"],
            "uncertain": ["active", "supported"],
            "pending_revision": ["active", "uncertain", "supported"]
        }
        
        logger.info("Hypothesis Agent 初始化完成 (增强版)")
    
    # ==================== 辅助函数 ====================
    
    def _split_queries(self, text: str) -> List[str]:
        """将查询字符串按 <> 分割"""
        parts = text.split('<>')
        queries = [p.strip() for p in parts if p.strip()]
        return queries
    
    def _extract_json_array(self, raw_text: str) -> List:
        """从模型输出中提取JSON数组（剥离 <think> 思维链 + raw_decode 容忍合法 JSON 后的解释文字）"""
        cleaned_text = strip_reasoning(raw_text)
        cleaned_text = re.sub(r"```(?:json)?", "", cleaned_text, flags=re.IGNORECASE).strip()
        decoder = json.JSONDecoder()
        for idx, ch in enumerate(cleaned_text):
            if ch == "[":
                try:
                    obj, _ = decoder.raw_decode(cleaned_text[idx:])
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, list):
                    return obj
        logger.warning("No JSON array found in model output.")
        return []

    def _extract_json_object(self, raw_text: str) -> Dict:
        """从模型输出中提取JSON对象（剥离 <think> 思维链 + raw_decode 容忍尾部多余内容）"""
        cleaned_text = strip_reasoning(raw_text)
        cleaned_text = re.sub(r"```(?:json)?", "", cleaned_text, flags=re.IGNORECASE).strip()
        decoder = json.JSONDecoder()
        for idx, ch in enumerate(cleaned_text):
            if ch == "{":
                try:
                    obj, _ = decoder.raw_decode(cleaned_text[idx:])
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict):
                    return obj
        logger.warning("No JSON object found in model output.")
        return {}
    
    def _call_llm(self, messages: List[Dict], model: str = os.environ.get("DEEPSEEK_MODEL", "interns2-preview-sft"), **kwargs) -> str:
        """调用LLM API"""
        if not LLM_API_AVAILABLE:
            raise ImportError("LLM API模块不可用")

        return call_deepseek_api(messages, model=model, **kwargs)

    def _parallel_map(self, fn, items) -> List:
        """并发执行 fn(item)，返回与 items 同序的结果列表。用于把【相互独立】的 LLM 调用并行化：
        计算内容、聚合方式、结果都与串行完全一致（只改并发度，不改业务逻辑），仅大幅缩短墙钟时间。
        单项异常 → 该位置返回 None（调用方按原有容错分支处理）。并发度经 ARCHE_LLM_CONCURRENCY 调（默认 6）。"""
        items = list(items)
        if not items:
            return []

        def _safe(it):
            try:
                return fn(it)
            except Exception as e:
                logger.error(f"并行子任务失败: {e}")
                return None

        workers = max(1, int(os.environ.get("ARCHE_LLM_CONCURRENCY", "6")))
        if workers <= 1 or len(items) == 1:
            return [_safe(it) for it in items]
        results: List = [None] * len(items)
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
            fut_to_idx = {ex.submit(_safe, it): i for i, it in enumerate(items)}
            for fut in concurrent.futures.as_completed(fut_to_idx):
                results[fut_to_idx[fut]] = fut.result()
        return results

    def infer_required_calculations(self,
                                    hypothesis: Dict[str, Any],
                                    chemistry_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """保守推断假设所需计算类型与Gaussian约束。"""
        chemistry_context = chemistry_context or {}
        text = " ".join([
            str(hypothesis.get("strategy_name", "")),
            str(hypothesis.get("core_claim", "")),
            str(hypothesis.get("detailed_reasoning", hypothesis.get("reasoning", ""))),
            str(hypothesis.get("required_computations", "")),
        ]).lower()

        def has_any(*kws: str) -> bool:
            return any(k in text for k in kws)

        needs_ts = has_any("transition state", " ts ", "ts search", "saddle")
        needs_irc = has_any("irc", "intrinsic reaction coordinate", "pathway")
        needs_conformer = has_any("conformer", "rotamer", "conformation")
        needs_barrier = has_any("barrier", "activation", "delta g", "rate-determining")
        needs_sp_refine = has_any("single point", "single-point", "sp ", "refinement", "ccsd", "dlpno")
        needs_freq = has_any("frequency", "vibrational", "imaginary frequency", "thermochemistry")
        needs_opt = has_any("optimization", "optimiz", "geometry") or needs_ts

        required_job_types: List[str] = []
        if needs_opt:
            required_job_types.append("opt")
        if needs_freq:
            required_job_types.append("freq")
        if needs_ts:
            required_job_types.append("ts")
        if needs_irc:
            required_job_types.append("irc")
        if needs_sp_refine:
            required_job_types.append("sp")
        if not required_job_types:
            required_job_types = ["opt", "sp"]

        expected_species = []
        species_keywords = {
            "reactant": ["reactant"],
            "intermediate": ["intermediate"],
            "TS": ["transition state", " ts "],
            "product": ["product"],
            "conformer": ["conformer", "rotamer"],
            "IRC": ["irc"],
            "single-point refinement": ["single point", "single-point", "refinement"],
        }
        for label, kws in species_keywords.items():
            if any(k in text for k in kws):
                expected_species.append(label)
        if not expected_species:
            expected_species = ["reactant", "product"]

        likely_elements = chemistry_context.get("expected_elements") or chemistry_context.get("elements") or []
        if isinstance(likely_elements, str):
            likely_elements = re.findall(r"[A-Z][a-z]?", likely_elements)
        if not isinstance(likely_elements, list):
            likely_elements = []

        solvent_sensitive = bool(
            chemistry_context.get("solvent")
            or chemistry_context.get("solvent_sensitive")
            or has_any("solvent", "solvation", "pcm", "smd")
        )

        validation_requirements = chemistry_context.get("expected_validation_requirements") or chemistry_context.get("validation_plan") or []
        if not isinstance(validation_requirements, list):
            validation_requirements = [str(validation_requirements)] if validation_requirements else []
        if needs_ts:
            validation_requirements.extend([
                "TS should have exactly one imaginary frequency",
                "Run frequency analysis after TS optimization",
            ])
        if needs_irc:
            validation_requirements.append("Use IRC to verify pathway connectivity when needed")
        if needs_opt and not needs_ts:
            validation_requirements.append("Check geometry convergence and frequency sanity")
        validation_requirements = sorted(set([x for x in validation_requirements if x]))

        computation_profile = {
            "required_job_types": required_job_types,
            "needs_ts": bool(needs_ts),
            "needs_irc": bool(needs_irc),
            "needs_conformer_search": bool(needs_conformer),
            "needs_barrier_comparison": bool(needs_barrier),
            "needs_single_point_refinement": bool(needs_sp_refine),
            "solvent_sensitive": bool(solvent_sensitive),
            "expected_species": expected_species,
            "likely_elements": [str(x) for x in likely_elements],
            "validation_requirements": validation_requirements,
        }

        gaussian_constraints = {
            "must_specify_solvent": bool(solvent_sensitive),
            "must_check_elements_vs_basis": bool(likely_elements),
            "must_check_charge_multiplicity": bool(
                chemistry_context.get("charge") is not None or chemistry_context.get("multiplicity") is not None or needs_ts
            ),
            "must_provide_route_rationale": True,
        }

        return {
            "computation_profile": computation_profile,
            "gaussian_constraints": gaussian_constraints,
        }

    def enrich_hypothesis_with_computation_profile(self,
                                                   hypothesis: Dict[str, Any],
                                                   chemistry_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """在不破坏原字段的前提下为假设补充计算画像。"""
        h = dict(hypothesis or {})
        inferred = self.infer_required_calculations(h, chemistry_context=chemistry_context)
        h.setdefault("computation_profile", inferred.get("computation_profile", {}))
        h.setdefault("gaussian_constraints", inferred.get("gaussian_constraints", {}))

        # 若已有部分字段，做非破坏性补全
        if isinstance(h.get("computation_profile"), dict):
            for k, v in inferred.get("computation_profile", {}).items():
                if k not in h["computation_profile"] or h["computation_profile"].get(k) in [None, "", [], {}]:
                    h["computation_profile"][k] = v
        if isinstance(h.get("gaussian_constraints"), dict):
            for k, v in inferred.get("gaussian_constraints", {}).items():
                if k not in h["gaussian_constraints"]:
                    h["gaussian_constraints"][k] = v

        return h
    
    # ==================== 结构化假设方法 ====================
    
    def create_structured_hypothesis(self, 
                                   hypothesis_data: Dict[str, Any],
                                   hypothesis_id: Optional[str] = None,
                                   chemistry_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        创建结构化假设表示
        
        参数:
            hypothesis_data: 原始假设数据（包含strategy_name和reasoning）
            hypothesis_id: 假设ID（可选，自动生成）
        
        返回:
            结构化假设字典
        """
        if hypothesis_id is None:
            hypothesis_id = f"hyp_{int(time.time())}_{hash(str(hypothesis_data)) % 10000:04d}"
        
        strategy_name = hypothesis_data.get("strategy_name", "Unknown_Strategy")
        reasoning = hypothesis_data.get("reasoning", "")
        
        # 从reasoning中提取关键信息
        key_species = []
        predicted_observables = []
        required_computations = []
        
        # 简单的关键词提取（可扩展）
        reasoning_lower = reasoning.lower()
        
        # 提取关键物种（简单模式匹配）
        species_keywords = ["reactant", "product", "catalyst", "intermediate", "transition state", "ts", "complex"]
        for keyword in species_keywords:
            if keyword in reasoning_lower:
                key_species.append(keyword)
        
        # 提取预测的可观测值
        observable_keywords = ["energy", "barrier", "frequency", "spectrum", "rate", "selectivity", "yield", "mechanism"]
        for keyword in observable_keywords:
            if keyword in reasoning_lower:
                predicted_observables.append(keyword)
        
        # 提取所需计算
        computation_keywords = ["dft", "optimization", "frequency analysis", "irc", "solvation", "md", "qm/mm", "single point"]
        for keyword in computation_keywords:
            if keyword in reasoning_lower:
                required_computations.append(keyword)
        
        # 创建结构化假设
        structured_hypothesis = {
            "hypothesis_id": hypothesis_id,
            "strategy_name": strategy_name,
            "core_claim": reasoning[:200] + ("..." if len(reasoning) > 200 else ""),
            "detailed_reasoning": reasoning,
            "key_species": list(set(key_species)) if key_species else ["Not specified"],
            "predicted_observables": list(set(predicted_observables)) if predicted_observables else ["Not specified"],
            "required_computations": list(set(required_computations)) if required_computations else ["Not specified"],
            "priority": hypothesis_data.get("priority", 1),  # 默认优先级
            "status": hypothesis_data.get("status", self.HYPOTHESIS_STATUS["active"]),
            "evidence": hypothesis_data.get("evidence", []),  # 证据列表
            "confidence": hypothesis_data.get("confidence", 0.5),  # 置信度 0-1
            "generation_timestamp": time.time(),
            "revision_history": [],
            "metadata": {
                "original_data": hypothesis_data,
                "structured_version": "1.0"
            }
        }

        structured_hypothesis = self.enrich_hypothesis_with_computation_profile(
            structured_hypothesis,
            chemistry_context=chemistry_context,
        )

        return structured_hypothesis


    def is_structured_hypothesis(self, hypothesis: Dict[str, Any]) -> bool:
        """判断假设是否已经是结构化格式。"""
        if not isinstance(hypothesis, dict):
            return False
        has_strategy = bool(hypothesis.get("strategy_name"))
        has_reasoning = bool(hypothesis.get("detailed_reasoning") or hypothesis.get("reasoning"))
        has_status_conf = "status" in hypothesis and "confidence" in hypothesis
        has_structural_markers = "hypothesis_id" in hypothesis or "core_claim" in hypothesis or "metadata" in hypothesis
        return has_strategy and has_reasoning and (has_status_conf or has_structural_markers)

    def ensure_structured_hypothesis(self,
                                   hypothesis: Dict[str, Any],
                                   hypothesis_id: Optional[str] = None,
                                   chemistry_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """确保假设为结构化格式，已结构化时尽量保留原字段。"""
        if self.is_structured_hypothesis(hypothesis):
            h = dict(hypothesis)
            if not h.get("hypothesis_id"):
                h["hypothesis_id"] = hypothesis_id or f"hyp_{int(time.time())}_{hash(str(hypothesis)) % 10000:04d}"

            detailed_reasoning = h.get("detailed_reasoning", h.get("reasoning", ""))
            h["detailed_reasoning"] = detailed_reasoning
            h.setdefault("reasoning", detailed_reasoning)
            h.setdefault("core_claim", detailed_reasoning[:200] + ("..." if len(detailed_reasoning) > 200 else ""))
            h.setdefault("required_computations", ["Not specified"])
            h.setdefault("status", self.HYPOTHESIS_STATUS["active"])
            h["confidence"] = max(0.0, min(1.0, float(h.get("confidence", 0.5))))
            h.setdefault("revision_history", [])
            h.setdefault("metadata", {})
            return self.enrich_hypothesis_with_computation_profile(h, chemistry_context=chemistry_context)

        return self.create_structured_hypothesis(
            hypothesis,
            hypothesis_id=hypothesis_id,
            chemistry_context=chemistry_context,
        )

    def _is_evidence_relevant(self, hypothesis: Dict[str, Any], evidence: Dict[str, Any]) -> bool:
        """判断证据与假设的相关性（兼容全局证据与定向证据）。"""
        if not isinstance(evidence, dict):
            return False

        hid = hypothesis.get("hypothesis_id")
        ev_hid = evidence.get("hypothesis_id")
        if ev_hid is not None:
            return str(ev_hid) == str(hid)

        strategy_name = str(hypothesis.get("strategy_name", "")).lower().strip()
        ev_strategy = str(evidence.get("strategy_name", evidence.get("strategy", ""))).lower().strip()
        if ev_strategy:
            return ev_strategy == strategy_name

        # 未指定假设目标时，视作全局证据
        return True

    def _analyze_evidence_item(self, evidence: Dict[str, Any]) -> Dict[str, Any]:
        """解析单条证据信号，支持execution/reflection新schema与旧schema。"""
        signal = {
            "supportive": 0,
            "contradictory": 0,
            "revision": 0,
            "ambiguous": 0,
            "source": "unknown",
            "notes": []
        }

        if not isinstance(evidence, dict):
            signal["ambiguous"] = 1
            signal["notes"].append("non_dict_evidence")
            return signal

        # Reflection schema
        if any(k in evidence for k in ["decision", "identified_problems", "evidence_summary", "recommended_actions"]):
            signal["source"] = "reflection"
            decision = str(evidence.get("decision", "")).lower()
            if decision == "accept":
                signal["supportive"] += 2
            elif decision == "revise_hypothesis":
                signal["revision"] += 2
            elif decision == "revise_workflow":
                signal["revision"] += 1
            elif decision == "stop":
                signal["ambiguous"] += 1

            for p in evidence.get("identified_problems", []) or []:
                if not isinstance(p, dict):
                    continue
                p_type = str(p.get("type", ""))
                p_code = str(p.get("code", ""))
                if p_type == "hypothesis_contradiction" or p_code in {
                    "barrier_contradiction", "photochemical_infeasible", "irc_connectivity_mismatch", "irc_not_verified"
                }:
                    signal["contradictory"] += 2
                elif p_code in {"technical_failures", "ts_validation_failed", "max_retry_reached"}:
                    signal["revision"] += 1

            evidence_summary = evidence.get("evidence_summary", {}) or {}
            if isinstance(evidence_summary, dict):
                workflow_outcome = str(evidence_summary.get("workflow_outcome", "")).lower()
                if workflow_outcome == "supported":
                    signal["supportive"] += 1
                elif workflow_outcome == "failed":
                    signal["contradictory"] += 1
                elif workflow_outcome == "partially_supported":
                    signal["revision"] += 1

                validation_overview = evidence_summary.get("validation_overview", {})
                if isinstance(validation_overview, dict) and validation_overview.get("critical_failed", 0) > 0:
                    signal["contradictory"] += 1

                if evidence_summary.get("failed_steps"):
                    signal["revision"] += 1

        # Execution schema
        if any(k in evidence for k in ["overall_status", "workflow_outcome", "validation", "parsed_results"]):
            signal["source"] = "execution"
            overall_status = str(evidence.get("overall_status", "")).lower()
            workflow_outcome = str(evidence.get("workflow_outcome", "")).lower()

            if overall_status == "success" and workflow_outcome == "supported":
                signal["supportive"] += 2
            elif overall_status == "failed" or workflow_outcome == "failed":
                signal["contradictory"] += 1
            elif workflow_outcome == "partially_supported":
                signal["revision"] += 1

            validation = evidence.get("validation")
            if isinstance(validation, dict):
                v_status = str(validation.get("status", "")).lower()
                if v_status == "pass":
                    signal["supportive"] += 1
                elif v_status == "fail":
                    signal["contradictory"] += 1
                elif v_status in {"warning", "unknown"}:
                    signal["ambiguous"] += 1

            parsed = evidence.get("parsed_results")
            if isinstance(parsed, dict):
                if parsed.get("irc_verified") is False:
                    signal["contradictory"] += 1
                n_imag = parsed.get("n_imag_freq")
                if isinstance(n_imag, int) and n_imag > 1:
                    signal["revision"] += 1

        # 旧schema fallback
        evidence_type = str(evidence.get("type", "")).lower()
        evidence_result = str(evidence.get("result", "")).lower()
        if evidence_type or evidence_result:
            if evidence_type == "execution" and evidence_result == "success":
                signal["supportive"] += 1
            elif evidence_type == "execution" and evidence_result == "failed":
                signal["contradictory"] += 1
            elif evidence_type == "literature" and evidence_result in {"support", "supported"}:
                signal["supportive"] += 1
            elif evidence_type == "literature" and evidence_result in {"contradict", "reject", "rejected"}:
                signal["contradictory"] += 1
            else:
                signal["ambiguous"] += 1

        if signal["supportive"] == 0 and signal["contradictory"] == 0 and signal["revision"] == 0:
            signal["ambiguous"] += 1

        return signal

    def _extract_reflection_signal(self, reflection: Any) -> Dict[str, Any]:
        """提取结构化反思信号，兼容旧式自由文本。"""
        if isinstance(reflection, dict):
            decision = str(reflection.get("decision", "")).lower()
            return {
                "decision": decision,
                "identified_problems": reflection.get("identified_problems", []) or [],
                "hypothesis_revision_instructions": reflection.get("hypothesis_revision_instructions", []) or [],
                "recommended_actions": reflection.get("recommended_actions", []) or [],
                "evidence_summary": reflection.get("evidence_summary", {}) or {},
                "confidence": float(reflection.get("confidence", 0.5)) if str(reflection.get("confidence", "")).strip() else 0.5,
                "raw_text": json.dumps(reflection, ensure_ascii=False).lower(),
            }

        text = str(reflection).lower()
        decision = ""
        if "revise_hypothesis" in text or "revise hypothesis" in text:
            decision = "revise_hypothesis"
        elif "revise_workflow" in text or "revise workflow" in text:
            decision = "revise_workflow"
        elif "accept" in text:
            decision = "accept"
        elif "stop" in text:
            decision = "stop"

        return {
            "decision": decision,
            "identified_problems": [],
            "hypothesis_revision_instructions": [],
            "recommended_actions": [],
            "evidence_summary": {},
            "confidence": 0.5,
            "raw_text": text,
        }
    
    def structure_existing_hypotheses(self, hypotheses: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        将现有假设列表转换为结构化格式，已结构化对象保持字段不丢失。
        """
        structured_hypotheses = []
        ts = int(time.time())
        for i, hypothesis in enumerate(hypotheses):
            hypothesis_id = f"hyp_{ts}_{i:04d}"
            structured = self.ensure_structured_hypothesis(hypothesis, hypothesis_id)
            structured_hypotheses.append(structured)

        logger.info(f"结构化 {len(structured_hypotheses)} 个假设")
        return structured_hypotheses

    def update_hypothesis_status(self, 
                               hypothesis: Dict[str, Any], 
                               new_status: str,
                               evidence: Optional[Dict[str, Any]] = None,
                               confidence: Optional[float] = None,
                               notes: Optional[str] = None) -> Dict[str, Any]:
        """
        更新假设状态
        
        参数:
            hypothesis: 结构化假设
            new_status: 新状态（必须为有效状态）
            evidence: 证据数据（可选）
            confidence: 新的置信度（可选）
            notes: 状态变更说明（可选）
        
        返回:
            更新后的假设
        """
        if new_status not in self.HYPOTHESIS_STATUS.values():
            logger.warning(f"无效的状态: {new_status}，使用默认状态")
            new_status = self.HYPOTHESIS_STATUS["uncertain"]
        
        # 创建状态变更记录
        status_change = {
            "timestamp": time.time(),
            "from_status": hypothesis.get("status", "active"),
            "to_status": new_status,
            "evidence": evidence,
            "confidence_before": hypothesis.get("confidence", 0.5),
            "confidence_after": confidence if confidence is not None else hypothesis.get("confidence", 0.5),
            "notes": notes or "Status updated"
        }
        
        # 更新假设
        updated_hypothesis = dict(hypothesis)
        updated_hypothesis["status"] = new_status
        
        if confidence is not None:
            updated_hypothesis["confidence"] = max(0.0, min(1.0, confidence))
        
        if evidence:
            if "evidence" not in updated_hypothesis:
                updated_hypothesis["evidence"] = []
            updated_hypothesis["evidence"].append(evidence)
        
        # 添加到修订历史
        if "revision_history" not in updated_hypothesis:
            updated_hypothesis["revision_history"] = []
        updated_hypothesis["revision_history"].append(status_change)
        
        logger.info(f"假设 {hypothesis.get('hypothesis_id', 'unknown')} 状态更新: {status_change['from_status']} → {new_status}")
        
        return updated_hypothesis
    
    def filter_hypotheses_by_evidence(self,
                                    hypotheses: List[Dict[str, Any]],
                                    evidence_results: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        基于证据过滤假设（支持execution/reflection新schema，兼容旧schema）。
        """
        supported_hypotheses = []
        rejected_hypotheses = []
        uncertain_hypotheses = []
        pending_revision_hypotheses = []

        for hypothesis in hypotheses:
            hypothesis = self.ensure_structured_hypothesis(hypothesis)
            current_status = hypothesis.get("status", self.HYPOTHESIS_STATUS["active"])

            relevant_evidence = [e for e in (evidence_results or []) if self._is_evidence_relevant(hypothesis, e)]
            if not relevant_evidence:
                updated = self.update_hypothesis_status(
                    hypothesis,
                    self.HYPOTHESIS_STATUS["uncertain"],
                    evidence={"reason": "no_relevant_evidence"},
                    confidence=max(0.3, hypothesis.get("confidence", 0.5) - 0.05),
                    notes="No relevant evidence found"
                )
                uncertain_hypotheses.append(updated)
                continue

            supportive_count = 0
            contradictory_count = 0
            revision_count = 0
            ambiguous_count = 0
            source_breakdown = defaultdict(int)

            for evidence in relevant_evidence:
                s = self._analyze_evidence_item(evidence)
                supportive_count += s.get("supportive", 0)
                contradictory_count += s.get("contradictory", 0)
                revision_count += s.get("revision", 0)
                ambiguous_count += s.get("ambiguous", 0)
                source_breakdown[s.get("source", "unknown")] += 1

            total_signal = supportive_count + contradictory_count + revision_count + ambiguous_count

            if total_signal == 0:
                new_status = self.HYPOTHESIS_STATUS["uncertain"]
            elif contradictory_count >= max(2, supportive_count + 2) and revision_count == 0:
                new_status = self.HYPOTHESIS_STATUS["rejected"]
            elif supportive_count >= max(2, contradictory_count + 2) and revision_count == 0:
                new_status = self.HYPOTHESIS_STATUS["supported"]
            elif revision_count > 0 or (supportive_count > 0 and contradictory_count > 0):
                new_status = self.HYPOTHESIS_STATUS["pending_revision"]
            elif ambiguous_count >= max(supportive_count, contradictory_count):
                new_status = self.HYPOTHESIS_STATUS["uncertain"]
            else:
                new_status = current_status

            new_confidence = hypothesis.get("confidence", 0.5)
            if new_status == self.HYPOTHESIS_STATUS["supported"]:
                new_confidence = min(0.95, new_confidence + 0.15)
            elif new_status == self.HYPOTHESIS_STATUS["rejected"]:
                new_confidence = max(0.05, new_confidence - 0.25)
            elif new_status == self.HYPOTHESIS_STATUS["pending_revision"]:
                new_confidence = max(0.2, new_confidence - 0.15)
            elif new_status == self.HYPOTHESIS_STATUS["uncertain"]:
                new_confidence = max(0.25, min(0.65, new_confidence))

            evidence_summary = {
                "supportive_count": supportive_count,
                "contradictory_count": contradictory_count,
                "revision_count": revision_count,
                "ambiguous_count": ambiguous_count,
                "total_signal": total_signal,
                "source_breakdown": dict(source_breakdown),
                "relevant_evidence_items": len(relevant_evidence),
            }

            updated_hypothesis = self.update_hypothesis_status(
                hypothesis,
                new_status,
                evidence=evidence_summary,
                confidence=new_confidence,
                notes=f"Filtered with schema-aware evidence ({len(relevant_evidence)} items)"
            )

            if new_status == self.HYPOTHESIS_STATUS["supported"]:
                supported_hypotheses.append(updated_hypothesis)
            elif new_status == self.HYPOTHESIS_STATUS["rejected"]:
                rejected_hypotheses.append(updated_hypothesis)
            elif new_status == self.HYPOTHESIS_STATUS["pending_revision"]:
                pending_revision_hypotheses.append(updated_hypothesis)
            else:
                uncertain_hypotheses.append(updated_hypothesis)

        result = {
            "supported": supported_hypotheses,
            "rejected": rejected_hypotheses,
            "uncertain": uncertain_hypotheses,
            "pending_revision": pending_revision_hypotheses,
            "total_processed": len(hypotheses),
            "evidence_items": len(evidence_results or []),
            "timestamp": time.time()
        }

        logger.info(f"假设过滤完成: {len(supported_hypotheses)} 支持, {len(rejected_hypotheses)} 拒绝, "
                   f"{len(uncertain_hypotheses)} 不确定, {len(pending_revision_hypotheses)} 待修订")

        return result

    def revise_hypotheses_from_reflection(self,
                                        hypotheses: List[Dict[str, Any]],
                                        reflection_results: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        基于反思结果修订假设（优先使用结构化reflection schema，兼容旧文本结果）。
        """
        revised_hypotheses = []
        new_hypotheses = []
        unchanged_hypotheses = []

        signals = [self._extract_reflection_signal(r) for r in (reflection_results or [])]
        decisions = [s.get("decision") for s in signals if s.get("decision")]

        # 决策优先级：revise_hypothesis > stop > revise_workflow > accept
        decision_priority = ["revise_hypothesis", "stop", "revise_workflow", "accept"]
        global_decision = ""
        for d in decision_priority:
            if d in decisions:
                global_decision = d
                break

        all_problem_codes = set()
        all_problem_types = set()
        revision_instructions = []
        recommended_actions = []
        confidence_values = []
        legacy_text_blob = " ".join([s.get("raw_text", "") for s in signals])

        for s in signals:
            for p in s.get("identified_problems", []) or []:
                if isinstance(p, dict):
                    all_problem_codes.add(str(p.get("code", "")))
                    all_problem_types.add(str(p.get("type", "")))
            revision_instructions.extend(s.get("hypothesis_revision_instructions", []) or [])
            recommended_actions.extend(s.get("recommended_actions", []) or [])
            try:
                confidence_values.append(float(s.get("confidence", 0.5)))
            except Exception:
                pass

        mean_reflection_confidence = sum(confidence_values) / len(confidence_values) if confidence_values else 0.5

        has_hypothesis_contradiction = (
            "hypothesis_contradiction" in all_problem_types or
            any(code in all_problem_codes for code in [
                "barrier_contradiction", "photochemical_infeasible", "irc_connectivity_mismatch", "irc_not_verified"
            ])
        )

        # 旧式文本fallback
        if not global_decision:
            if "revise_hypothesis" in legacy_text_blob or "revise hypothesis" in legacy_text_blob:
                global_decision = "revise_hypothesis"
            elif "stop" in legacy_text_blob:
                global_decision = "stop"
            elif "revise_workflow" in legacy_text_blob or "revise workflow" in legacy_text_blob:
                global_decision = "revise_workflow"
            elif "accept" in legacy_text_blob:
                global_decision = "accept"

        for hypothesis in hypotheses:
            hypothesis = self.ensure_structured_hypothesis(hypothesis)
            hypothesis_id = hypothesis.get("hypothesis_id", "unknown")
            current_status = hypothesis.get("status", self.HYPOTHESIS_STATUS["active"])

            needs_revision = (
                current_status in [self.HYPOTHESIS_STATUS["pending_revision"], self.HYPOTHESIS_STATUS["uncertain"]] or
                global_decision in ["revise_hypothesis", "stop"] or
                has_hypothesis_contradiction
            )

            if not needs_revision:
                unchanged_hypotheses.append(hypothesis)
                continue

            # 强矛盾 + 高置信反思：可拒绝
            if global_decision == "stop" and has_hypothesis_contradiction and mean_reflection_confidence >= 0.75:
                rejected_hypothesis = self.update_hypothesis_status(
                    hypothesis,
                    self.HYPOTHESIS_STATUS["rejected"],
                    evidence={
                        "reflection_decision": global_decision,
                        "problem_codes": list(all_problem_codes),
                        "confidence": mean_reflection_confidence,
                    },
                    confidence=max(0.05, hypothesis.get("confidence", 0.5) - 0.35),
                    notes="Rejected due to high-confidence contradiction from structured reflection"
                )
                revised_hypotheses.append(rejected_hypothesis)
                continue

            # 其他修订场景：去优先级并标记待修订
            revised_hypothesis = self.update_hypothesis_status(
                hypothesis,
                self.HYPOTHESIS_STATUS["pending_revision"],
                evidence={
                    "reflection_decision": global_decision,
                    "problem_codes": list(all_problem_codes),
                    "problem_types": list(all_problem_types),
                },
                confidence=max(0.2, hypothesis.get("confidence", 0.5) * 0.8),
                notes="Marked pending revision based on structured reflection"
            )
            revised_hypotheses.append(revised_hypothesis)

            # 基于结构化修订指令生成替代假设（保守生成）
            combined_instructions = [x for x in (revision_instructions + recommended_actions) if isinstance(x, str) and x.strip()]
            if global_decision == "revise_hypothesis" and combined_instructions:
                instruction_text = " ".join(combined_instructions[:3])
                alt_data = {
                    "strategy_name": f"{hypothesis.get('strategy_name', 'Hypothesis')}_alternative",
                    "reasoning": (
                        f"{hypothesis.get('detailed_reasoning', hypothesis.get('reasoning', ''))}\n"
                        f"Revision focus: {instruction_text}"
                    ),
                    "status": self.HYPOTHESIS_STATUS["active"],
                    "confidence": min(0.7, max(0.45, hypothesis.get("confidence", 0.5) * 0.9)),
                    "metadata": {
                        "generation_source": "reflection_structured_revision",
                        "replaces_hypothesis_id": hypothesis_id,
                        "reflection_decision": global_decision,
                    }
                }
                new_hypothesis = self.ensure_structured_hypothesis(alt_data)
                new_hypothesis["replaces_hypothesis_id"] = hypothesis_id
                new_hypotheses.append(new_hypothesis)

        result = {
            "revised": revised_hypotheses,
            "new": new_hypotheses,
            "unchanged": unchanged_hypotheses,
            "total_input": len(hypotheses),
            "reflection_items": len(reflection_results or []),
            "decision_used": global_decision,
            "timestamp": time.time()
        }

        logger.info(f"假设修订完成: {len(revised_hypotheses)} 修订, {len(new_hypotheses)} 新假设, "
                   f"{len(unchanged_hypotheses)} 未变")

        return result

    def generate_refined_hypotheses(self, 
                                  original_hypotheses: List[Dict[str, Any]],
                                  research_question: str,
                                  evidence_summary: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """
        基于证据总结生成细化的假设
        
        参数:
            original_hypotheses: 原始结构化假设
            research_question: 研究问题
            evidence_summary: 证据总结（可选）
        
        返回:
            细化的假设列表
        """
        # 收集所有假设的核心观点
        core_claims = [hyp.get("core_claim", "") for hyp in original_hypotheses]
        evidence_text = json.dumps(evidence_summary) if evidence_summary else "No evidence available"
        
        prompt = f"""
        研究问题: {research_question}
        
        现有假设的核心观点:
        {chr(10).join([f"- {claim}" for claim in core_claims])}
        
        可用证据总结:
        {evidence_text}
        
        基于上述信息，请生成2-3个更精细、更具体的假设，专注于：
        1. 解决现有假设的不足
        2. 整合证据支持的观点
        3. 提出可测试的计算化学策略
        
        返回JSON数组格式，每个元素包含strategy_name和reasoning字段。
        """
        
        try:
            response = self._call_llm(
                [{"role": "user", "content": prompt}],
                model=os.environ.get("DEEPSEEK_MODEL", "interns2-preview-sft"),
                max_tokens=2000
            )
            
            refined_hypotheses_data = self._extract_json_array(response)
            
            if not refined_hypotheses_data:
                logger.warning("未生成细化假设，返回原始假设")
                return original_hypotheses
            
            # 转换为结构化格式
            refined_hypotheses = []
            for i, hyp_data in enumerate(refined_hypotheses_data):
                structured = self.create_structured_hypothesis(hyp_data)
                structured["status"] = self.HYPOTHESIS_STATUS["active"]
                structured["confidence"] = 0.7  # 细化假设较高置信度
                structured["generation_source"] = "refined_from_evidence"
                refined_hypotheses.append(structured)
            
            logger.info(f"生成 {len(refined_hypotheses)} 个细化假设")
            return refined_hypotheses
            
        except Exception as e:
            logger.error(f"生成细化假设失败: {e}")
            return original_hypotheses
    
    # ==================== 查询生成 ====================
    
    def generate_queries(self, research_topic: str, num_queries: int = 3) -> List[str]:
        """
        生成研究查询
        
        参数:
            research_topic: 研究主题
            num_queries: 查询数量
        
        返回:
            查询列表
        """
        logger.info(f"生成查询: {research_topic[:50]}...")
        
        # 格式化提示词
        prompt = self.query_prompt_template.copy()
        prompt[1]["content"] = prompt[1]["content"].format(
            num_queries=num_queries,
            research_topic=research_topic
        )
        
        try:
            response = self._call_llm(prompt, model=os.environ.get("DEEPSEEK_MODEL", "interns2-preview-sft"), max_tokens=1000)
            queries = self._split_queries(response)
            
            logger.info(f"生成了 {len(queries)} 个查询")
            return queries
            
        except Exception as e:
            logger.error(f"查询生成失败: {e}")
            # 生成默认查询
            default_queries = [
                f"Computational chemistry methods for studying {research_topic[:50]}...",
                f"Quantum chemical simulations of reaction mechanisms related to {research_topic[:30]}",
                f"DFT and transition state analysis for {research_topic[:30]}"
            ]
            return default_queries[:num_queries]
    
    # ==================== 假设生成 ====================
    
    def generate_hypotheses_for_query(self, query: str, research_question: str,
                                     literature_review: str, num_hypotheses: int = 3) -> List[Dict]:
        """
        为单个查询生成假设（显式query-conditioned）。
        """
        logger.info(f"为查询生成假设: {query[:50]}...")

        prompt = copy.deepcopy(self.hypothesis_prompt_template)
        prompt[1]["content"] = (
            "You are generating hypotheses for ONE specific query angle.\n\n"
            f"Current query angle/subproblem:\n{query}\n\n"
            f"Main research question:\n{research_question}\n\n"
            f"Relevant literature review:\n{literature_review}\n\n"
            f"Generate exactly {num_hypotheses} distinct computational chemistry hypotheses that directly address "
            "the query angle above while staying relevant to the main research question. "
            "Avoid repeating generic hypotheses that ignore the query-specific focus."
        )

        try:
            response = self._call_llm(prompt, model=os.environ.get("DEEPSEEK_MODEL", "interns2-preview-sft"), max_tokens=2200)
            hypotheses = self._extract_json_array(response)

            if not hypotheses or len(hypotheses) < num_hypotheses:
                logger.warning(f"生成的假设数量不足: {len(hypotheses) if hypotheses else 0}")
                hypotheses = self._generate_default_hypotheses(research_question, num_hypotheses, query=query)

            structured_hypotheses = self.structure_existing_hypotheses(hypotheses)
            for h in structured_hypotheses:
                h["query_context"] = query
                h.setdefault("metadata", {})
                h["metadata"]["query_context"] = query

            logger.info(f"生成了 {len(structured_hypotheses)} 个结构化假设")
            return structured_hypotheses

        except Exception as e:
            logger.error(f"假设生成失败: {e}")
            return self._generate_default_hypotheses(research_question, num_hypotheses, query=query)

    def _generate_default_hypotheses(self, research_question: str, num_hypotheses: int, query: Optional[str] = None) -> List[Dict]:
        """生成默认假设（备用），支持query上下文并避免重复结构化。"""
        default_hypotheses = []
        for i in range(num_hypotheses):
            reasoning = f"Computational investigation of {research_question[:80]} using DFT methods and transition state analysis."
            if query:
                reasoning += f" Query focus: {query[:120]}"
            default_hypotheses.append({
                "strategy_name": f"Default_Strategy_{i+1}",
                "reasoning": reasoning,
                "metadata": {"generation_source": "default_fallback", "query_context": query}
            })

        return self.structure_existing_hypotheses(default_hypotheses)

    def compare_hypotheses(self, h1: Dict, h2: Dict) -> str:
        """
        比较两个假设的关系
        
        返回: merge, complement, conflict, independent 中的一个
        """
        # 支持结构化假设和原始假设
        h1_reasoning = h1.get("detailed_reasoning", h1.get("reasoning", ""))
        h2_reasoning = h2.get("detailed_reasoning", h2.get("reasoning", ""))
        
        prompt = f"""
        Compare the following two computational chemistry hypotheses:

        Hypothesis 1: {h1_reasoning}
        Hypothesis 2: {h2_reasoning}

        Decide their relationship (answer ONLY with one word):
        - merge (if they describe the same mechanism and can be combined)
        - complement (if they address different aspects and can be combined)
        - conflict (if they propose contradictory mechanisms)
        - independent (if they are unrelated)

        Return ONLY the single relationship word.
        """
        
        try:
            response = self._call_llm(
                [{"role": "user", "content": prompt}],
                model=os.environ.get("DEEPSEEK_MODEL", "interns2-preview-sft"),
                max_tokens=600
            )
            
            relationship = response.lower().strip()
            valid_relationships = ["merge", "complement", "conflict", "independent"]
            
            if relationship in valid_relationships:
                return relationship
            else:
                logger.warning(f"无效的关系响应: '{relationship}'，默认使用 'independent'")
                return "independent"
                
        except Exception as e:
            logger.error(f"假设比较失败: {e}")
            return "independent"
    
    def merge_hypotheses(self, hypotheses: List[Dict]) -> Dict:
        """合并多个假设"""
        # 支持结构化假设和原始假设
        reasoning_texts = []
        for h in hypotheses:
            reasoning = h.get('detailed_reasoning', h.get('reasoning', ''))
            reasoning_texts.append(reasoning)
        
        prompt = f"""
        Integrate the following hypotheses into a single rigorous hypothesis:

        {' '.join(reasoning_texts)}

        Output a JSON object with exactly these fields:
        {{
            "strategy_name": "string (combined strategy name)",
            "reasoning": "string (integrated reasoning)"
        }}
        
        Return ONLY the JSON object, no additional text.
        """
        
        try:
            response = self._call_llm(
                [{"role": "user", "content": prompt}],
                model=os.environ.get("DEEPSEEK_MODEL", "interns2-preview-sft"),
                max_tokens=1000
            )
            
            merged = self._extract_json_object(response)
            if not merged:
                # 创建默认合并
                merged = {
                    "strategy_name": f"Merged_Strategy",
                    "reasoning": "Combined approach integrating multiple computational methods."
                }
            
            # 将合并结果转换为结构化假设
            structured_merged = self.create_structured_hypothesis(merged)
            structured_merged["status"] = self.HYPOTHESIS_STATUS["active"]
            structured_merged["confidence"] = 0.6  # 合并假设中等置信度
            
            # 记录合并来源
            source_ids = [h.get("hypothesis_id", "unknown") for h in hypotheses if "hypothesis_id" in h]
            if source_ids:
                structured_merged["merged_from"] = source_ids
            
            return structured_merged
            
        except Exception as e:
            logger.error(f"假设合并失败: {e}")
            # 返回默认结构化假设
            default_merged = {
                "strategy_name": "Merged_Strategy",
                "reasoning": " ".join(reasoning_texts[:200])
            }
            return self.create_structured_hypothesis(default_merged)
    
    # ==================== 假设优化 ====================
    
    def optimize_hypotheses(self, hypotheses: List[Dict]) -> List[Dict]:
        """
        优化假设列表：合并相似的，处理冲突的
        
        参数:
            hypotheses: 原始假设列表
        
        返回:
            优化后的假设列表
        """
        if len(hypotheses) <= 1:
            return hypotheses
        
        # compare_hypotheses(h_i,h_j) 只依赖这对假设、与合并顺序无关 → 先并行预计算所有 i<j 关系，
        # 再用与原来逐字一致的贪心合并逻辑消费它（结果不变，只是把 N² 次 LLM 调用并行化）。
        pair_keys = [(i, j) for i in range(len(hypotheses)) for j in range(i + 1, len(hypotheses))]
        rels = self._parallel_map(
            lambda k: self.compare_hypotheses(hypotheses[k[0]], hypotheses[k[1]]),
            pair_keys,
        )
        relations = {pair_keys[idx]: rels[idx] for idx in range(len(pair_keys))}

        used_indices = set()
        optimized = []

        for i, h1 in enumerate(hypotheses):
            if i in used_indices:
                continue
            group_to_merge = [h1]
            # 比较与其他假设的关系（消费上面预计算的并行结果，逻辑与原串行完全一致）
            for j, h2 in enumerate(hypotheses):
                if j <= i or j in used_indices:
                    continue
                relation = relations.get((i, j))
                if relation == "merge":
                    group_to_merge.append(h2)
                    used_indices.add(j)
                # 其他关系（complement, conflict）暂时不处理

            # 合并需要合并的假设
            if len(group_to_merge) > 1:
                merged = self.merge_hypotheses(group_to_merge)
                optimized.append(merged)
            else:
                optimized.append(h1)

            used_indices.add(i)
        
        logger.info(f"假设优化完成: {len(hypotheses)} -> {len(optimized)}")
        return optimized
    
    # ==================== 策略排名 ====================
    
    def rank_strategies(self, strategies: List[Dict], research_question: str) -> List[Dict]:
        """
        对策略进行排名
        
        参数:
            strategies: 策略列表
            research_question: 研究问题
        
        返回:
            排名后的策略列表
        """
        if len(strategies) < 2:
            # 如果只有一个策略，直接返回
            for i, strategy in enumerate(strategies):
                strategy["rank"] = i + 1
                strategy["score"] = 0
            return strategies
        
        # 生成所有策略对
        pairs = list(combinations(strategies, 2))
        logger.info(f"比较 {len(pairs)} 对策略...")
        
        wins = defaultdict(int)
        losses = defaultdict(int)
        
        # 两两比较相互独立 → 并行执行；wins/losses 用 +1 聚合（与完成顺序无关），输出与串行一致。
        def _judge(item):
            idx, (strat_a, strat_b) = item
            a_name = strat_a.get("strategy_name", f"Strategy_A_{idx}")
            b_name = strat_b.get("strategy_name", f"Strategy_B_{idx}")
            # 支持结构化假设和原始假设
            a_reasoning = strat_a.get("detailed_reasoning", strat_a.get("reasoning", ""))
            b_reasoning = strat_b.get("detailed_reasoning", strat_b.get("reasoning", ""))
            prompt = f"""
            Scientific question: {research_question}

            Compare two computational chemistry strategies:

            Strategy A: {a_name}
            Analysis: {a_reasoning}

            Strategy B: {b_name}
            Analysis: {b_reasoning}

            Which strategy is better for addressing the scientific question?
            Consider: scientific soundness, feasibility, novelty, and computational efficiency.

            Return ONLY: "A" or "B"
            """
            response = self._call_llm(
                [{"role": "user", "content": prompt}],
                model=os.environ.get("DEEPSEEK_MODEL", "interns2-preview-sft"),
                max_tokens=600
            )
            return (a_name, b_name, response.strip().upper())

        for verdict in self._parallel_map(_judge, list(enumerate(pairs))):
            if not verdict:
                continue  # 比较失败（_parallel_map 已记录）→ 视为平局，与原 except 分支一致
            a_name, b_name, winner = verdict
            if winner == "A":
                wins[a_name] += 1
                losses[b_name] += 1
            elif winner == "B":
                wins[b_name] += 1
                losses[a_name] += 1
            # 平局不处理
        
        # 计算排名
        all_strategies = set()
        for strategy in strategies:
            name = strategy.get("strategy_name", "")
            if name:
                all_strategies.add(name)
        
        ranking = []
        for strategy_name in all_strategies:
            w = wins[strategy_name]
            l = losses[strategy_name]
            score = w - l
            
            # 找到对应的策略对象
            for strategy in strategies:
                if strategy.get("strategy_name", "") == strategy_name:
                    ranked_strategy = dict(strategy)
                    ranked_strategy["wins"] = w
                    ranked_strategy["losses"] = l
                    ranked_strategy["score"] = score
                    ranking.append(ranked_strategy)
                    break
        
        # 按分数排序
        ranking.sort(key=lambda x: (x.get("score", 0), x.get("wins", 0)), reverse=True)
        
        # 添加排名
        for i, strategy in enumerate(ranking):
            strategy["rank"] = i + 1
        
        logger.info(f"策略排名完成: 排名了 {len(ranking)} 个策略")
        return ranking
    
    # ==================== 主工作流程 ====================
    
    def generate_and_rank_hypotheses(self, 
                                    research_question: str,
                                    literature_review: str,
                                    num_queries: int = 3,
                                    num_hypotheses_per_query: int = 3,
                                    top_n: int = 5) -> Dict[str, any]:
        """
        完整的假设生成与排名流程
        
        参数:
            research_question: 研究问题
            literature_review: 文献综述（来自Retrieval agent）
            num_queries: 查询数量
            num_hypotheses_per_query: 每个查询的假设数量
            top_n: 返回前N个假设
        
        返回:
            处理结果字典
        """
        logger.info(f"开始假设生成流程: {research_question[:50]}...")
        
        result = {
            "research_question": research_question,
            "queries": [],
            "hypotheses_by_query": [],
            "optimized_hypotheses": [],
            "ranked_strategies": [],
            "top_n_strategies": []
        }
        
        try:
            # 1. 生成查询
            queries = self.generate_queries(research_question, num_queries)
            result["queries"] = queries
            
            # 2. 为每个查询生成假设（各查询相互独立 → 并行；按查询顺序汇总，结果与串行一致）
            all_hypotheses = []
            per_query_hyps = self._parallel_map(
                lambda q: self.generate_hypotheses_for_query(
                    query=q,
                    research_question=research_question,
                    literature_review=literature_review,
                    num_hypotheses=num_hypotheses_per_query,
                ),
                queries,
            )
            for query, hypotheses in zip(queries, per_query_hyps):
                hypotheses = hypotheses or []
                result["hypotheses_by_query"].append({"query": query, "hypotheses": hypotheses})
                all_hypotheses.extend(hypotheses)
            
            # 3. 优化假设
            optimized = self.optimize_hypotheses(all_hypotheses)
            result["optimized_hypotheses"] = optimized
            
            # 4. 策略排名
            ranked = self.rank_strategies(optimized, research_question)
            result["ranked_strategies"] = ranked
            
            # 5. 选择前N个策略
            top_strategies = ranked[:top_n]
            result["top_n_strategies"] = top_strategies
            
            logger.info(f"假设生成流程完成: 生成了 {len(all_hypotheses)} 个假设，优化为 {len(optimized)} 个，排名了 {len(ranked)} 个策略")
            
            # 保存结果到文件
            self._save_results(result, research_question)
            
        except Exception as e:
            logger.error(f"假设生成流程失败: {e}")
            result["error"] = str(e)
        
        return result
    
    def _save_results(self, result: Dict, research_question: str):
        """保存结果到文件"""
        try:
            # 创建输出目录
            output_dir = os.path.join(project_root, "outputs", "hypothesis")
            os.makedirs(output_dir, exist_ok=True)
            
            # 生成文件名（使用研究问题的前几个词）
            safe_name = re.sub(r'[^\w\s-]', '', research_question[:30]).strip().replace(' ', '_')
            timestamp = int(time.time())
            
            # 保存完整结果
            result_file = os.path.join(output_dir, f"hypothesis_result_{safe_name}_{timestamp}.json")
            with open(result_file, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2, ensure_ascii=False)
            
            logger.info(f"结果已保存到: {result_file}")
            
        except Exception as e:
            logger.error(f"保存结果失败: {e}")

    def _extract_chemistry_context_from_evidence(self,
                                               evidence_results: Optional[List[Dict[str, Any]]]) -> Dict[str, Any]:
        """从证据项中提取可用的化学上下文（可选）。"""
        for item in evidence_results or []:
            if not isinstance(item, dict):
                continue
            if isinstance(item.get("chemistry_context"), dict):
                return item.get("chemistry_context", {})
            ev_sum = item.get("evidence_summary", {})
            if isinstance(ev_sum, dict) and isinstance(ev_sum.get("scientific_evidence"), dict):
                sci = ev_sum.get("scientific_evidence", {})
                if isinstance(sci.get("chemistry_context"), dict):
                    return sci.get("chemistry_context", {})
        return {}
    
    # ==================== 增强假设生成 ====================
    
    def generate_enhanced_hypotheses(self, 
                                   research_question: str,
                                   literature_review: str,
                                   evidence_results: Optional[List[Dict[str, Any]]] = None,
                                   num_queries: int = 3,
                                   num_hypotheses_per_query: int = 3,
                                   top_n: int = 5,
                                   enable_filtering: bool = True,
                                   enable_revision: bool = True,
                                   chemistry_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        增强的假设生成流程（支持结构化表示、证据过滤和修订）
        
        参数:
            research_question: 研究问题
            literature_review: 文献综述
            evidence_results: 证据结果列表（可选）
            num_queries: 查询数量
            num_hypotheses_per_query: 每个查询的假设数量
            top_n: 返回前N个假设
            enable_filtering: 是否启用证据过滤
            enable_revision: 是否启用修订
        
        返回:
            增强的处理结果字典
        """
        logger.info(f"开始增强假设生成流程: {research_question[:50]}...")
        
        result = {
            "research_question": research_question,
            "workflow_version": "enhanced",
            "chemistry_context": chemistry_context or {},
            "structured_hypotheses": [],
            "filtered_results": None,
            "revision_results": None,
            "final_hypotheses": [],
            "hypothesis_lifecycle": {}
        }
        
        try:
            # 1. 生成初始假设（使用标准流程）
            standard_result = self.generate_and_rank_hypotheses(
                research_question=research_question,
                literature_review=literature_review,
                num_queries=num_queries,
                num_hypotheses_per_query=num_hypotheses_per_query,
                top_n=top_n
            )
            
            result["standard_generation"] = standard_result
            
            # 获取优化后的假设
            optimized_hypotheses = standard_result.get("optimized_hypotheses", [])

            if not chemistry_context:
                chemistry_context = self._extract_chemistry_context_from_evidence(evidence_results)
            result["chemistry_context"] = chemistry_context or {}
            
            # 2. 确保所有假设都是结构化格式
            structured_hypotheses = []
            for hyp in optimized_hypotheses:
                structured = self.ensure_structured_hypothesis(hyp, chemistry_context=chemistry_context)
                structured_hypotheses.append(structured)
            
            result["structured_hypotheses"] = structured_hypotheses
            
            # 3. 证据过滤（如果提供证据且启用过滤）
            if evidence_results and enable_filtering:
                logger.info(f"基于 {len(evidence_results)} 个证据项进行假设过滤")
                
                filtering_result = self.filter_hypotheses_by_evidence(
                    structured_hypotheses, 
                    evidence_results
                )
                
                result["filtered_results"] = filtering_result
                
                # 收集所有过滤后的假设（包括支持、拒绝等）
                all_filtered_hypotheses = []
                all_filtered_hypotheses.extend(filtering_result.get("supported", []))
                all_filtered_hypotheses.extend(filtering_result.get("uncertain", []))
                all_filtered_hypotheses.extend(filtering_result.get("pending_revision", []))
                
                current_hypotheses = all_filtered_hypotheses
                
                # 记录生命周期状态
                result["hypothesis_lifecycle"]["after_filtering"] = {
                    "supported": len(filtering_result.get("supported", [])),
                    "rejected": len(filtering_result.get("rejected", [])),
                    "uncertain": len(filtering_result.get("uncertain", [])),
                    "pending_revision": len(filtering_result.get("pending_revision", []))
                }
            else:
                current_hypotheses = structured_hypotheses
            
            # 4. 假设修订（如果启用）
            if enable_revision and evidence_results:
                logger.info("执行假设修订")
                
                # 仅修订需要修订的假设
                hypotheses_needing_revision = [
                    h for h in current_hypotheses 
                    if h.get("status") in [
                        self.HYPOTHESIS_STATUS["pending_revision"],
                        self.HYPOTHESIS_STATUS["uncertain"]
                    ]
                ]
                
                if hypotheses_needing_revision:
                    revision_result = self.revise_hypotheses_from_reflection(
                        hypotheses_needing_revision,
                        evidence_results
                    )
                    
                    result["revision_results"] = revision_result
                    
                    # 合并修订后的假设
                    revised_and_new = []
                    revised_and_new.extend(revision_result.get("revised", []))
                    revised_and_new.extend(revision_result.get("new", []))
                    
                    # 保留未改变的假设
                    final_hypotheses = []
                    final_hypotheses.extend(revision_result.get("unchanged", []))
                    final_hypotheses.extend(revised_and_new)
                    
                    # 添加从未需要修订的假设
                    hypotheses_not_needing_revision = [
                        h for h in current_hypotheses 
                        if h.get("status") not in [
                            self.HYPOTHESIS_STATUS["pending_revision"],
                            self.HYPOTHESIS_STATUS["uncertain"]
                        ]
                    ]
                    final_hypotheses.extend(hypotheses_not_needing_revision)
                    
                    current_hypotheses = final_hypotheses
                    
                    # 记录生命周期状态
                    result["hypothesis_lifecycle"]["after_revision"] = {
                        "revised": len(revision_result.get("revised", [])),
                        "new": len(revision_result.get("new", [])),
                        "unchanged": len(revision_result.get("unchanged", []))
                    }
            
            # 5. 生成细化假设（如果有很多不确定的假设）
            uncertain_count = sum(1 for h in current_hypotheses 
                                 if h.get("status") == self.HYPOTHESIS_STATUS["uncertain"])
            
            if uncertain_count > len(current_hypotheses) * 0.5:  # 超过50%不确定
                logger.info(f"生成细化假设 ({uncertain_count}/{len(current_hypotheses)} 不确定)")
                
                refined_hypotheses = self.generate_refined_hypotheses(
                    current_hypotheses,
                    research_question,
                    evidence_summary=result.get("filtered_results")
                )
                
                # 添加细化假设
                current_hypotheses.extend(refined_hypotheses)
                result["refined_hypotheses"] = refined_hypotheses
            
            # 6. 最终排名
            if current_hypotheses:
                # 提取策略信息用于排名
                strategies_for_ranking = []
                for hyp in current_hypotheses:
                    hyp = self.enrich_hypothesis_with_computation_profile(hyp, chemistry_context=chemistry_context)
                    strategy_info = {
                        "strategy_name": hyp.get("strategy_name", "Unknown"),
                        "detailed_reasoning": hyp.get("detailed_reasoning", hyp.get("reasoning", "")),
                        "confidence": hyp.get("confidence", 0.5),
                        "status": hyp.get("status", "active"),
                        "computation_profile": hyp.get("computation_profile", {}),
                        "gaussian_constraints": hyp.get("gaussian_constraints", {}),
                    }
                    strategies_for_ranking.append(strategy_info)
                
                # 使用置信度调整排名分数
                ranked_strategies = self.rank_strategies(strategies_for_ranking, research_question)
                
                # 将排名信息添加回结构化假设
                rank_map = {}
                for i, strategy in enumerate(ranked_strategies):
                    strategy_name = strategy.get("strategy_name")
                    rank_map[strategy_name] = {
                        "rank": i + 1,
                        "score": strategy.get("score", 0)
                    }
                
                for hyp in current_hypotheses:
                    strategy_name = hyp.get("strategy_name")
                    if strategy_name in rank_map:
                        hyp["rank"] = rank_map[strategy_name]["rank"]
                        hyp["score"] = rank_map[strategy_name]["score"]
                
                # 按排名排序
                current_hypotheses.sort(key=lambda x: x.get("rank", 999))
                
                # 选择前N个
                final_top_n = current_hypotheses[:top_n]
                
                result["final_hypotheses"] = current_hypotheses
                result["final_top_n"] = final_top_n
                result["ranking_summary"] = {
                    "total_hypotheses": len(current_hypotheses),
                    "supported_count": sum(1 for h in current_hypotheses if h.get("status") == self.HYPOTHESIS_STATUS["supported"]),
                    "rejected_count": sum(1 for h in current_hypotheses if h.get("status") == self.HYPOTHESIS_STATUS["rejected"]),
                    "uncertain_count": sum(1 for h in current_hypotheses if h.get("status") == self.HYPOTHESIS_STATUS["uncertain"]),
                    "pending_revision_count": sum(1 for h in current_hypotheses if h.get("status") == self.HYPOTHESIS_STATUS["pending_revision"]),
                }
            
            logger.info(f"增强假设生成流程完成: {len(current_hypotheses)} 个最终假设")
            
            # 保存增强结果
            self._save_enhanced_results(result, research_question)
            
        except Exception as e:
            logger.error(f"增强假设生成流程失败: {e}")
            result["error"] = str(e)
        
        return result
    
    def _save_enhanced_results(self, result: Dict, research_question: str):
        """保存增强结果到文件"""
        try:
            # 创建输出目录
            output_dir = os.path.join(project_root, "outputs", "enhanced_hypothesis")
            os.makedirs(output_dir, exist_ok=True)
            
            # 生成文件名
            safe_name = re.sub(r'[^\w\s-]', '', research_question[:30]).strip().replace(' ', '_')
            timestamp = int(time.time())
            
            # 保存完整结果
            result_file = os.path.join(output_dir, f"enhanced_hypothesis_{safe_name}_{timestamp}.json")
            with open(result_file, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2, ensure_ascii=False, default=str)
            
            # 保存简化版本（仅最终假设）
            simplified_file = os.path.join(output_dir, f"enhanced_hypothesis_simple_{safe_name}_{timestamp}.json")
            simplified = {
                "research_question": research_question,
                "final_hypotheses": result.get("final_hypotheses", []),
                "final_top_n": result.get("final_top_n", [])
            }
            with open(simplified_file, "w", encoding="utf-8") as f:
                json.dump(simplified, f, indent=2, ensure_ascii=False, default=str)
            
            logger.info(f"增强结果已保存到: {result_file}")
            
        except Exception as e:
            logger.error(f"保存增强结果失败: {e}")
    
    # ==================== 兼容旧接口 ====================
    
    def gen_hypotheses(self, num_queries: int, num_hypotheses: int, scientific_question: str) -> str:
        """
        兼容旧接口: gen_hypothese.py的主函数
        
        参数:
            num_queries: 查询数量
            num_hypotheses: 每个查询的假设数量
            scientific_question: 科学问题
        
        返回:
            生成的假设文件路径
        """
        # 这里需要一个简单的文献综述（在实际使用中应该来自Retrieval agent）
        literature_review = f"Background for: {scientific_question[:100]}..."
        
        result = self.generate_and_rank_hypotheses(
            research_question=scientific_question,
            literature_review=literature_review,
            num_queries=num_queries,
            num_hypotheses_per_query=num_hypotheses,
            top_n=5
        )
        
        # 返回文件路径
        output_dir = os.path.join(project_root, "outputs", "hypothesis")
        safe_name = re.sub(r'[^\w\s-]', '', scientific_question[:30]).strip().replace(' ', '_')
        return os.path.join(output_dir, f"generated_hypotheses_{safe_name}.json")


# ==================== 工具函数（兼容旧接口） ====================

def optimize_all_hypotheses(input_file: str, output_file: Optional[str] = None) -> List[Dict]:
    """
    兼容旧接口: compare_hyp.py的主函数
    """
    agent = HypothesisAgent()
    
    try:
        with open(input_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        optimized_data = []
        for entry in data:
            hypotheses = entry.get("hypotheses", [])
            
            # 如果hypotheses是字符串，尝试解析
            if isinstance(hypotheses, str):
                try:
                    hypotheses = json.loads(hypotheses)
                except:
                    hypotheses = []
            
            optimized = agent.optimize_hypotheses(hypotheses)
            
            optimized_entry = {
                "query": entry.get("query", ""),
                "optimized_hypotheses": optimized
            }
            optimized_data.append(optimized_entry)
        
        # 保存结果
        if output_file is None:
            output_dir = os.path.dirname(input_file)
            output_file = os.path.join(output_dir, "optimized_hypotheses.json")
        
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(optimized_data, f, indent=2, ensure_ascii=False)
        
        return optimized_data
        
    except Exception as e:
        logger.error(f"优化假设失败: {e}")
        return []


def rank_strategies(opt_hypothesis_file: str, question: str, output_file: str) -> str:
    """
    兼容旧接口: rank_hypptheses.py的主函数
    """
    agent = HypothesisAgent()
    
    try:
        with open(opt_hypothesis_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        # 提取策略
        strategies = []
        for entry in data:
            optimized_hypotheses = entry.get("optimized_hypotheses", [])
            if optimized_hypotheses:
                # 取第一个假设作为策略
                hyp = optimized_hypotheses[0]
                if isinstance(hyp, dict):
                    strategies.append(hyp)
        
        # 排名策略
        ranked = agent.rank_strategies(strategies, question)
        
        # 保存结果
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump({
                "ranking": ranked,
                "ranked_list": [
                    f"{i+1}. {s.get('strategy_name', 'Unknown')} (Score: {s.get('score', 0)})"
                    for i, s in enumerate(ranked)
                ]
            }, f, indent=2, ensure_ascii=False)
        
        return output_file
        
    except Exception as e:
        logger.error(f"策略排名失败: {e}")
        return ""


# ==================== 主函数 ====================

def main():
    """测试函数"""
    import argparse
    
    parser = argparse.ArgumentParser(description="Hypothesis Agent - 假设生成智能体")
    parser.add_argument("--question", "-q", required=True, help="科学问题")
    parser.add_argument("--review", "-r", help="文献综述（可选）")
    parser.add_argument("--num-queries", type=int, default=3, help="查询数量")
    parser.add_argument("--num-hypotheses", type=int, default=3, help="每个查询的假设数量")
    parser.add_argument("--top-n", type=int, default=5, help="前N个策略")
    parser.add_argument("--api-key", help="Deepseek API密钥")
    
    args = parser.parse_args()
    
    # 创建Agent
    agent = HypothesisAgent(deepseek_api_key=args.api_key)
    
    # 文献综述
    literature_review = args.review or f"Computational chemistry background for: {args.question}"
    
    # 运行完整流程
    result = agent.generate_and_rank_hypotheses(
        research_question=args.question,
        literature_review=literature_review,
        num_queries=args.num_queries,
        num_hypotheses_per_query=args.num_hypotheses,
        top_n=args.top_n
    )
    
    # 输出结果
    print("\n" + "="*60)
    print("假设生成结果")
    print("="*60)
    
    print(f"\n📋 研究问题: {result['research_question'][:100]}...")
    print(f"\n🔍 生成的查询: {len(result.get('queries', []))} 个")
    
    if result.get('hypotheses_by_query'):
        total_hypotheses = sum(len(q['hypotheses']) for q in result['hypotheses_by_query'])
        print(f"\n💡 生成的假设: {total_hypotheses} 个")
    
    if result.get('optimized_hypotheses'):
        print(f"\n🔧 优化的假设: {len(result['optimized_hypotheses'])} 个")
    
    if result.get('ranked_strategies'):
        print(f"\n🏆 排名的策略: {len(result['ranked_strategies'])} 个")
        print("\n前5个策略:")
        for i, strategy in enumerate(result['ranked_strategies'][:5], 1):
            print(f"{i}. {strategy.get('strategy_name', 'Unknown')} (分数: {strategy.get('score', 0)})")
    
    if result.get('error'):
        print(f"\n❌ 错误: {result['error']}")


if __name__ == "__main__":
    main()
