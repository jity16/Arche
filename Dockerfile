# 书安OS 内置 ARCHE 计算化学多智能体 —— 单容器：后端 Python 服务 + 前端 SPA。
# 多阶段构建：阶段一用 node 构建 React/Vite 前端，阶段二的 Python 运行时托管它。
# server.py(Flask/waitress) 暴露 /healthz、/api/run、/api/run/stream、/api/runs、/api/config，
# 并在 /app/frontend/dist 存在时直接托管 SPA（单容器单端口）。
# 真实多智能体工作流；LLM 地址/模型/Key 经环境变量或 /api/config 注入，外部化学工具链由环境提供。

# --- 阶段一：构建前端 SPA（Vite） ---
FROM node:20-slim AS frontend
WORKDIR /fe
# 依赖先装（命中 lockfile 缓存层，源码变更不重装）
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci --no-audit --no-fund || npm install --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# --- 阶段二：Python 运行时 ---
FROM python:3.11-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8501 \
    ARCHE_PROJECT_ROOT=/app \
    HF_HOME=/app/.cache/huggingface \
    HF_ENDPOINT=https://hf-mirror.com

# 国内构建走清华镜像 —— deb.debian.org 在内网环境反复超时，会把 build 卡死。
RUN sed -i 's|http://deb.debian.org/debian|https://mirrors.tuna.tsinghua.edu.cn/debian|g; s|http://deb.debian.org/debian-security|https://mirrors.tuna.tsinghua.edu.cn/debian-security|g; s|http://security.debian.org/debian-security|https://mirrors.tuna.tsinghua.edu.cn/debian-security|g' /etc/apt/sources.list.d/debian.sources 2>/dev/null \
    || sed -i 's|http://deb.debian.org/debian|https://mirrors.tuna.tsinghua.edu.cn/debian|g; s|http://security.debian.org/debian-security|https://mirrors.tuna.tsinghua.edu.cn/debian-security|g' /etc/apt/sources.list \
    || true

# git 供 pip 安装 paperscraper 的 git+ 源(见下);ca-certificates/curl 同前。
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl git \
    && rm -rf /var/lib/apt/lists/*

# pip 走 TUNA，避开外网 pypi 慢/超时（容器内 pip 没 docker 镜像加速兜底）。
RUN pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple \
    && pip config set global.trusted-host pypi.tuna.tsinghua.edu.cn

WORKDIR /app

# 依赖先装：requirements.txt（含 Agent 真实依赖 numpy/pymupdf/faiss）+ flask + waitress 单独成层。
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt "flask>=3.0,<4" "waitress>=3.0,<4"

# 文献检索工具 paperscraper —— retrieval_agent.py 的 `import paperscraper`。
# 必须用 blackadad fork 的 v1.8.1：其它版本搜索接口与本仓库代码不兼容，会让
# `import paperscraper` 失败 → 检索静默降级为「paperscraper 不可用，跳过论文下载」。
# 用 tag tarball 而非 git+：master-1 构建网络下 `git clone`(blob:none 部分克隆 +
# promisor 取 blob)会被 GitHub 连接重置(early EOF);tarball 是单次 HTTPS GET，
# 同一 v1.8.1 代码但稳得多。依赖项仍走上面配置的 TUNA index。
RUN pip install --no-cache-dir --retries 8 "https://github.com/blackadad/paper-scraper/archive/refs/tags/v1.8.1.tar.gz"

# 语义检索嵌入：sentence-transformers + BAAI/bge-small-en-v1.5（retrieval_agent 的 embedder；
# 缺失则 SENTENCE_TRANSFORMERS_AVAILABLE=False、回退到简单词频匹配，语义召回质量低）。
# torch 强制装 CPU 轮子（默认会拉 ~2GB CUDA 轮子），其余依赖仍走 TUNA；构建期把 bge-small
# 模型烤进 HF_HOME(/app/.cache，下方 chown 给 uid 10001)→ 运行时不再联网下载（pod 未必能上 HF）。
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu --extra-index-url https://pypi.tuna.tsinghua.edu.cn/simple \
    && pip install --no-cache-dir "sentence-transformers>=2.7,<4"
# 把 bge-small 烤进 HF_HOME —— 经 HF_ENDPOINT=hf-mirror.com（master-1 直连 huggingface.co 被墙、超时）。
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-small-en-v1.5')"
# 运行时离线用缓存模型（pod 同样上不了 HF）；放在 bake 之后，不影响上面的下载。
ENV HF_HUB_OFFLINE=1

# 真实多智能体工作流（检索嵌入 + 多轮推理模型调用 + 远程 Gaussian）耗时远超默认 600s；放开运行
# 超时，否则 server.py 的 /api/run(/stream) 会在 RUN_TIMEOUT 处把子进程 kill 掉、拿不到最终结果。
# 烤进镜像（而非 deployment env）—— 构建即生效、无需改集群配置；仍可被 deployment env 覆盖。
ENV ARCHE_RUN_TIMEOUT=5400

# 镜像版本号 —— 构建时 `--build-arg ARCHE_VERSION=<tag>` 注入；/api/info 返回、前端 Header 展示，
# 让用户一眼确认当前部署到了哪个版本（升级是否生效）。默认 dev 表示构建未显式传版本。
ARG ARCHE_VERSION=dev
ENV ARCHE_VERSION=${ARCHE_VERSION}

# 业务源码 + 服务包装 + 启动脚本 + 前端构建产物
COPY src /app/src
COPY server.py /app/server.py
COPY entrypoint.sh /app/entrypoint.sh
COPY --from=frontend /fe/dist /app/frontend/dist
RUN chmod +x /app/entrypoint.sh

# 非 root 运行（满足 K8s restricted PSS / runAsNonRoot）：建 uid 10001 用户并移交 /app 属主。
# 运行时工作目录走 /tmp（server.py 用 tempfile），无需写 /app。
RUN groupadd -g 10001 arche \
    && useradd -u 10001 -g 10001 -m -s /usr/sbin/nologin arche \
    && chown -R 10001:10001 /app
USER 10001

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PORT}/healthz" >/dev/null || exit 1

ENTRYPOINT ["/app/entrypoint.sh"]
