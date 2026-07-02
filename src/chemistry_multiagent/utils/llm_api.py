import os
import re
import json
import time
import openai
from typing import List, Dict, Any, Optional

from .llm_headers import api_key_headers

# 推理模型(MiniMax-2.7-w8a8 / interns2-preview-sft 等)会在正文前吐 <think>...</think> 思维链。
# 下游按 Python list / JSON 解析模型输出，思维链不剥离会污染关键词、令 json.loads 报 "Extra data"。
_THINK_BLOCK_RE = re.compile(r"<think\b[^>]*>.*?</think\s*>", re.DOTALL | re.IGNORECASE)


def strip_reasoning(text: str) -> str:
    """剥离推理模型输出的 <think>...</think> 思维链，返回真正的答案文本。

    三种情况：
      1) 完整 <think>…</think> 块 —— 整块删除；
      2) 仅剩闭合标签（开标签被更早截断）—— 取最后一个 </think> 之后的内容；
      3) 仅有未闭合 <think>（推理被 max_tokens 截断）—— 开标签之后全是残缺推理，丢弃，
         返回空串让上游走重试/降级，而不是把推理当答案。
    """
    if not text:
        return text
    cleaned = _THINK_BLOCK_RE.sub("", text)
    low = cleaned.lower()
    if "</think>" in low:
        cleaned = cleaned[low.rfind("</think>") + len("</think>") :]
    elif "<think>" in low:
        cleaned = cleaned[: low.find("<think>")]
    return cleaned.strip()

# 配置Deepseek API
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
# base_url / 模型 / 鉴权全部 env 驱动（chart extraEnv 或 .env.local 提供）：
#   DEEPSEEK_BASE_URL  OpenAI 兼容端点（.../v1）
#   DEEPSEEK_MODEL     模型名（本 agent 默认 interns2-preview-sft）
#   鉴权见 llm_headers：DEEPSEEK_API_KEY → x-api-key 头；ARCHE_LLM_INGRESS_AK/SK → ingress Basic Auth
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEFAULT_MODEL = os.environ.get("DEEPSEEK_MODEL", "interns2-preview-sft")  # 可经 DEEPSEEK_MODEL 覆盖
# 推理模型先吐 <think> 再给答案；max_tokens 太小 → budget 全耗在思维链被截断、strip_reasoning
# 只能返回空串。各 agent 历史上按非推理模型写死了很小的 max_tokens(512/600/1000)，这里统一兜
# 一个输出下限，给「思考+答案」留足空间。端点上限更小或要控成本时可经此 env 调低。
MIN_OUTPUT_TOKENS = int(os.environ.get("ARCHE_LLM_MIN_OUTPUT_TOKENS", "4096"))

# 初始化OpenAI客户端（全局唯一模型：retrieval / hypothesis / planner / execution / reflection 共用）
client = openai.OpenAI(
    api_key=DEEPSEEK_API_KEY or "EMPTY",
    base_url=DEEPSEEK_BASE_URL,
    default_headers=api_key_headers(DEEPSEEK_API_KEY),  # x-api-key + 可选 ingress Basic Auth（见 llm_headers）
)

def call_deepseek_api(
    messages: List[Dict[str, str]],
    model: str = DEFAULT_MODEL,
    temperature: float = 0.7,
    max_tokens: int = 8192,
    timeout: int = int(os.environ.get("ARCHE_LLM_TIMEOUT", "300")),  # 120s 对 planner 长协议生成太短→易超时空转;env 可调
    max_retries: int = 3,
    retry_delay: int = 5
) -> str:
    """
    调用Deepseek API，带有重试机制
    """
    base_max_tokens = max(int(max_tokens), MIN_OUTPUT_TOKENS)
    for attempt in range(max_retries):
        # 截断重试时逐步加大输出预算（×1/×2/×3），让重试真有机会吐出答案，而不是用同样过小的
        # budget 再被 <think> 占满一遍（否则重试纯属浪费时延/成本、零恢复概率）。
        effective_max_tokens = base_max_tokens * (attempt + 1)
        try:
            response = client.chat.completions.create(
                # 端点只有一个模型；忽略各 agent 硬编码的 "deepseek-chat"，强制用 DEEPSEEK_MODEL。
                model=DEFAULT_MODEL,
                messages=messages,
                temperature=temperature,
                max_tokens=effective_max_tokens,
                timeout=timeout
            )
            raw = response.choices[0].message.content or ""
            answer = strip_reasoning(raw)
            # 原始内容非空但剥离后为空 = 推理模型 budget 全花在未闭合 <think>、没吐出真答案。
            # 绝不能把空串当成功返回（上游会把它当 0 查询/0 假设静默降级）；视为瞬时失败 → 重试，
            # 耗尽后 raise，让调用方 except 兜底（默认值/规则化降级/显式失败）触发。
            if raw.strip() and not answer:
                raise RuntimeError("LLM 响应被未闭合 <think> 思维链占满、无有效答案（max_tokens 可能仍偏小）")
            return answer
        except Exception as e:
            print(f"Deepseek API调用失败 (尝试 {attempt+1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                print(f"将在{retry_delay}秒后重试...")
                time.sleep(retry_delay)
                retry_delay *= 2  # 指数退避
            else:
                print("达到最大重试次数，放弃请求")
                raise

def extract_json_from_response(response_text: str) -> Any:
    """
    从响应文本中提取JSON对象或数组。

    先剥离 <think> 思维链与 markdown 代码块标记，再从第一个 { / [ 处用 raw_decode 解析，
    这样模型在合法 JSON 之后附带的解释文字（典型 "Extra data: line N" 错因）不再导致解析失败。
    """
    # 先去思维链，再去 markdown 围栏
    cleaned_text = strip_reasoning(response_text)
    cleaned_text = re.sub(r"```(?:json)?", "", cleaned_text, flags=re.IGNORECASE).strip()

    # 定位第一个 JSON 起始符，raw_decode 只吃掉合法的那一段、忽略尾部多余内容
    decoder = json.JSONDecoder()
    for idx, ch in enumerate(cleaned_text):
        if ch in "{[":
            try:
                obj, _ = decoder.raw_decode(cleaned_text[idx:])
                return obj
            except json.JSONDecodeError:
                continue

    # 如果没有找到有效的JSON，返回剥离后的文本（已去思维链，便于上游降级处理）
    return cleaned_text or response_text

def safe_json_call(
    messages: List[Dict[str, str]],
    expect_array: bool = False,
    model: str = DEFAULT_MODEL,
    **kwargs
) -> Any:
    """
    安全的JSON调用：调用API并尝试解析JSON响应
    返回解析后的JSON对象/数组，或原始文本
    """
    response = call_deepseek_api(messages, model=model, **kwargs)
    parsed = extract_json_from_response(response)
    
    if expect_array and not isinstance(parsed, list):
        print("警告：期望数组但未得到数组")
        return []
    elif not expect_array and isinstance(parsed, list) and len(parsed) == 1:
        # 如果期望对象但得到单元素数组，返回数组中的元素
        return parsed[0]
    
    return parsed

# 工具函数：分割查询字符串（按<>分割）
def split_queries(text: str) -> List[str]:
    """
    将模型生成的queries字符串按 <> 分割，并去除多余空格
    """
    parts = text.split('<>')
    queries = [p.strip() for p in parts if p.strip()]
    return queries

# 工具函数：保存模型输出到JSON文件
def save_model_output(
    raw_output: List[Dict], 
    filepath: str, 
    parse_hypotheses: bool = False
) -> None:
    """
    处理模型生成的列表并保存为 JSON 文件
    """
    import re
    
    processed: List[Dict[str, Any]] = []
    
    for item in raw_output:
        new_item = dict(item)  # 拷贝，避免修改原始数据
        
        if parse_hypotheses and isinstance(new_item.get("hypotheses"), str):
            # 去掉 ```json ... ``` 标记
            hypotheses_str = re.sub(r"```json|```", "", new_item["hypotheses"]).strip()
            
            # 尝试解析为 JSON
            try:
                new_item["hypotheses"] = json.loads(hypotheses_str)
            except json.JSONDecodeError:
                # 如果解析失败，就保持为原始字符串
                new_item["hypotheses"] = hypotheses_str
        
        processed.append(new_item)
    
    # 保存到文件
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(processed, f, indent=4, ensure_ascii=False)
    
    print(f"✅ 模型输出已成功保存为 {filepath}")