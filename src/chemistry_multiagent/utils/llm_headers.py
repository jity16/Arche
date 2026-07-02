"""OpenAI 兼容网关的自定义鉴权头工具。

本项目对接的推理服务（interns2 / pjlab ingress）有两层鉴权，均经环境变量注入、部署可覆盖：

  1. 模型 API key —— 用自定义请求头（默认 `x-api-key`）而非 OpenAI SDK 默认的
     `Authorization: Bearer`。头名取 ARCHE_LLM_API_KEY_HEADER（默认 "x-api-key"；
     置空字符串则禁用、回退标准 Bearer），值取传入的 api_key。

  2. ingress 网关 Basic Auth —— 当 ARCHE_LLM_INGRESS_AK / ARCHE_LLM_INGRESS_SK 同时非空时，
     附加 `Authorization: Basic base64(AK:SK)`（等价于 curl 的 `-u AK:SK`）。该头会覆盖
     openai SDK 默认写入的 Bearer：网关在 ingress 层做 Basic 鉴权，模型在应用层认 x-api-key。

把返回值作为 openai SDK 客户端的 default_headers 传入即可，全部客户端共用本函数。
"""

import base64
import os
from typing import Dict, Optional


def api_key_headers(
    api_key: Optional[str],
    *,
    header_name_env: str = "ARCHE_LLM_API_KEY_HEADER",
    ak_env: str = "ARCHE_LLM_INGRESS_AK",
    sk_env: str = "ARCHE_LLM_INGRESS_SK",
) -> Optional[Dict[str, str]]:
    """构造 openai SDK 的 default_headers（按需叠加 x-api-key 与 ingress Basic Auth）。

    无任何可用凭证时返回 None（不附加自定义头，走 SDK 默认行为）。
    """
    headers: Dict[str, str] = {}

    header_name = os.environ.get(header_name_env, "x-api-key").strip()
    if api_key and header_name:
        headers[header_name] = api_key

    ak = os.environ.get(ak_env, "").strip()
    sk = os.environ.get(sk_env, "").strip()
    if ak and sk:
        token = base64.b64encode(f"{ak}:{sk}".encode("utf-8")).decode("ascii")
        # 覆盖 SDK 默认的 Authorization: Bearer —— ingress 网关要 Basic。
        headers["Authorization"] = f"Basic {token}"

    return headers or None
