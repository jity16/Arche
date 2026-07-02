#!/bin/sh
# 书安OS 在发布 helm release 时注入的默认 LLM 凭据是 A3S_LLM_* 形态；这里在启动边界
# 把它们映射成项目源码实际读取的环境变量，不改源码。mock 模式（ARCHE_MOCK=1，默认）
# 不触发真实 LLM 调用，缺凭据也能起服务。
set -eu

if [ -n "${A3S_LLM_API_KEY:-}" ]; then
  # 主 LLM 通道（llm_api / execution / reflection / planner / gen_gaussiancode）读 DEEPSEEK_*。
  export DEEPSEEK_API_KEY="${A3S_LLM_API_KEY}"
  [ -n "${A3S_LLM_BASE_URL:-}" ] && export DEEPSEEK_BASE_URL="${A3S_LLM_BASE_URL}"
  [ -n "${A3S_LLM_MODEL_ID:-}" ] && export DEEPSEEK_MODEL="${A3S_LLM_MODEL_ID}"
  # openai SDK 直读路径（gen_gaussiancode 的 OPENAI_API_KEY 兜底）一并喂。
  export OPENAI_API_KEY="${A3S_LLM_API_KEY}"
  [ -n "${A3S_LLM_BASE_URL:-}" ] && export OPENAI_BASE_URL="${A3S_LLM_BASE_URL}"
fi

exec python /app/server.py
