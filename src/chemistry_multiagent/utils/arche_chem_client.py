#!/usr/bin/env python3
"""Shared local ARCHE-Chem client for Planner/Execution expert calls."""

from __future__ import annotations

import os
from typing import Dict, List, Optional, Tuple

from .llm_api import strip_reasoning  # 复用统一的 <think> 思维链剥离


_CLIENT_CACHE: Dict[Tuple[str, str, str], "ArcheChemClient"] = {}


def _nonempty_answer(raw: str) -> str:
    """剥离 <think> 思维链后若为空、但原文非空，说明推理被 max_tokens 截断、无有效答案 →
    raise 让调用方走规则化降级，而不是把空串当专家分析返回。"""
    answer = strip_reasoning(raw or "")
    if (raw or "").strip() and not answer:
        raise RuntimeError("ARCHE-Chem 响应被未闭合 <think> 占满、无有效答案（max_tokens 可能偏小）")
    return answer


class ArcheChemClient:
    """Small backend-agnostic ARCHE-Chem client with lazy model loading."""

    def __init__(self, model_name: str, model_path: Optional[str] = None, backend: str = "local_hf"):
        self.model_name = model_name
        self.model_path = model_path
        self.backend = (backend or "local_hf").lower()

        self._tokenizer = None
        self._model = None
        self._vllm_llm = None
        self._openai_client = None

    @property
    def backend_info(self) -> Dict[str, Optional[str]]:
        """Lightweight backend/model provenance for audit/debug use."""
        return {
            "model_name": self.model_name,
            "model_path": self.model_path,
            "backend": self.backend,
        }

    def get_backend_info(self) -> Dict[str, Optional[str]]:
        """Return backend/model provenance."""
        return dict(self.backend_info)

    def _model_id(self) -> str:
        return self.model_path or self.model_name

    def _messages_to_prompt(self, messages: List[Dict[str, str]]) -> str:
        if not isinstance(messages, list) or not messages:
            raise RuntimeError("messages must be a non-empty chat list")

        if self._tokenizer is not None and hasattr(self._tokenizer, "apply_chat_template"):
            try:
                return self._tokenizer.apply_chat_template(
                    messages,
                    tokenize=False,
                    add_generation_prompt=True,
                )
            except Exception:
                pass

        # Conservative fallback format for local inference
        lines: List[str] = []
        for m in messages:
            role = str((m or {}).get("role", "user")).strip().lower() or "user"
            content = str((m or {}).get("content", ""))
            lines.append(f"[{role}]\n{content}")
        lines.append("[assistant]\n")
        return "\n\n".join(lines)

    def _ensure_loaded(self) -> None:
        if self.backend == "local_hf":
            if self._model is not None and self._tokenizer is not None:
                return
            try:
                from transformers import AutoModelForCausalLM, AutoTokenizer

                model_id = self._model_id()
                self._tokenizer = AutoTokenizer.from_pretrained(model_id, trust_remote_code=True)
                if self._tokenizer.pad_token_id is None and self._tokenizer.eos_token_id is not None:
                    self._tokenizer.pad_token = self._tokenizer.eos_token

                self._model = AutoModelForCausalLM.from_pretrained(
                    model_id,
                    trust_remote_code=True,
                    device_map="auto",
                    torch_dtype="auto",
                )
                self._model.eval()
            except Exception as e:
                raise RuntimeError(f"Failed to load local_hf model '{self._model_id()}': {e}") from e
            return

        if self.backend == "vllm":
            if self._vllm_llm is not None:
                return
            try:
                from vllm import LLM

                self._vllm_llm = LLM(model=self._model_id(), trust_remote_code=True)
            except Exception as e:
                raise RuntimeError(f"Failed to load vllm model '{self._model_id()}': {e}") from e
            return

        if self.backend == "openai_compatible":
            if self._openai_client is not None:
                return
            try:
                from openai import OpenAI

                base_url = os.environ.get("ARCHE_CHEM_BASE_URL")
                if not base_url:
                    raise RuntimeError("ARCHE_CHEM_BASE_URL is required for openai_compatible backend")
                api_key = os.environ.get("ARCHE_CHEM_API_KEY", "EMPTY")
                from .llm_headers import api_key_headers

                self._openai_client = OpenAI(
                    api_key=api_key,
                    base_url=base_url,
                    # 化学专家 profile 独立鉴权（interns2 两层：x-api-key + ingress Basic Auth）。
                    default_headers=api_key_headers(
                        None if api_key in ("", "EMPTY") else api_key,
                        header_name_env="ARCHE_CHEM_API_KEY_HEADER",
                        ak_env="ARCHE_CHEM_INGRESS_AK",
                        sk_env="ARCHE_CHEM_INGRESS_SK",
                    ),
                )
            except Exception as e:
                raise RuntimeError(f"Failed to init openai_compatible client: {e}") from e
            return

        raise RuntimeError(f"Unsupported backend: {self.backend}")

    def generate(self, messages: List[Dict[str, str]], max_tokens: int = 1024, temperature: float = 0.2) -> str:
        """Generate assistant text from chat messages."""
        self._ensure_loaded()
        # 化学专家(interns2 等)同为推理模型；与 llm_api 一致兜一个输出下限，给「思考+答案」
        # 留足 token，否则 strip_reasoning 会因 <think> 截断返回空串。env 可调。
        max_tokens = max(int(max_tokens), int(os.environ.get("ARCHE_LLM_MIN_OUTPUT_TOKENS", "4096")))
        # interns2 等小上下文模型(max_model_len 默认 8192):prompt + max_tokens 超窗会被端点 400 拒掉。
        # 按 prompt 粗估动态压顶,给输出留下限 256,避免专家复核因超窗直接失败。
        _ctx_limit = int(os.environ.get("ARCHE_CHEM_MAX_MODEL_LEN", "8192"))
        _prompt_est = sum(len(str(m.get("content", ""))) for m in messages) // 3
        max_tokens = max(256, min(max_tokens, _ctx_limit - _prompt_est - 256))

        if self.backend == "local_hf":
            try:
                prompt = self._messages_to_prompt(messages)
                inputs = self._tokenizer(prompt, return_tensors="pt")
                if hasattr(self._model, "device"):
                    inputs = {k: v.to(self._model.device) for k, v in inputs.items()}

                pad_token_id = self._tokenizer.pad_token_id
                if pad_token_id is None:
                    pad_token_id = self._tokenizer.eos_token_id
                if pad_token_id is None:
                    raise RuntimeError("Tokenizer has neither pad_token_id nor eos_token_id")

                do_sample = bool(temperature and temperature > 0)
                outputs = self._model.generate(
                    **inputs,
                    max_new_tokens=int(max_tokens),
                    do_sample=do_sample,
                    temperature=float(temperature) if do_sample else 1.0,
                    top_p=0.95 if do_sample else 1.0,
                    pad_token_id=pad_token_id,
                )
                prompt_len = inputs["input_ids"].shape[1]
                gen_ids = outputs[0][prompt_len:]
                text = self._tokenizer.decode(gen_ids, skip_special_tokens=True)
                return _nonempty_answer(text)
            except Exception as e:
                raise RuntimeError(f"local_hf inference failed: {e}") from e

        if self.backend == "vllm":
            try:
                from vllm import SamplingParams

                prompt = self._messages_to_prompt(messages)
                params = SamplingParams(
                    temperature=float(temperature),
                    max_tokens=int(max_tokens),
                    top_p=0.95,
                )
                outputs = self._vllm_llm.generate([prompt], params)
                if not outputs or not outputs[0].outputs:
                    raise RuntimeError("empty vllm output")
                return _nonempty_answer(outputs[0].outputs[0].text)
            except Exception as e:
                raise RuntimeError(f"vllm inference failed: {e}") from e

        if self.backend == "openai_compatible":
            try:
                response = self._openai_client.chat.completions.create(
                    model=os.environ.get("ARCHE_CHEM_MODEL", self.model_name),
                    messages=messages,
                    temperature=float(temperature),
                    max_tokens=int(max_tokens),
                )
                content = response.choices[0].message.content if response and response.choices else ""
                return _nonempty_answer(content)
            except Exception as e:
                raise RuntimeError(f"openai_compatible inference failed: {e}") from e

        raise RuntimeError(f"Unsupported backend at inference time: {self.backend}")


def _get_cached_client(model: str, model_path: Optional[str], backend: str) -> ArcheChemClient:
    """Get/create a cached ARCHE-Chem client by (model, model_path, backend)."""
    key = (str(model), str(model_path or ""), str((backend or "local_hf").lower()))
    client = _CLIENT_CACHE.get(key)
    if client is None:
        client = ArcheChemClient(model_name=model, model_path=model_path, backend=backend)
        _CLIENT_CACHE[key] = client
    return client


def call_arche_chem(
    messages: List[Dict[str, str]],
    model: str,
    model_path: Optional[str] = None,
    backend: str = "local_hf",
    max_tokens: int = 1024,
    temperature: float = 0.2,
) -> str:
    """Convenience function used by Planner/Execution for ARCHE-Chem calls."""
    client = _get_cached_client(model=model, model_path=model_path, backend=backend)
    return client.generate(messages=messages, max_tokens=max_tokens, temperature=temperature)
