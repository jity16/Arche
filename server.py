"""书安OS 内置 ARCHE 计算化学多智能体 —— 长驻服务包装层。

ARCHE 本体是 CLI（chemistry_multiagent.controllers.chemistry_multiagent_controller），
本文件用一个极薄的 Flask 服务把它包成 书安OS application 型智能体所需的长驻 HTTP 服务：

  GET  /healthz        —— 健康检查（K8s liveness/readiness 与 Docker HEALTHCHECK）
  GET  /api/info       —— 智能体元信息
  POST /api/run        —— 运行一次工作流：body {"question": "..."}
  GET  /api/runs       —— 执行记录列表（最近优先，轻量）
  GET  /api/runs/<id>  —— 执行记录详情（含 stdout/stderr）

设计原则：不改 ARCHE 源码，按 README 文档化的 CLI 入口 subprocess 调用真实多智能体工作流。
Agent 依赖随镜像安装（见 requirements.txt）；LLM 服务地址/模型/Key 经环境变量或 /api/config 注入。
真实化学工具链（Gaussian/Multiwfn 等）由部署环境提供，缺失则相关阶段优雅降级。
"""

from __future__ import annotations

import http.client
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from urllib.parse import urlparse

from flask import Flask, jsonify, request, send_from_directory


def _load_local_env() -> None:
    """本地开发：加载同目录 .env.local（gitignore + dockerignore，不入库/不进镜像）。
    用 setdefault —— 部署时由 helm extraEnv/Secret 注入的真实环境变量优先，绝不被文件覆盖。"""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env.local")
    if not os.path.isfile(path):
        return
    try:
        with open(path, encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key:
                    os.environ.setdefault(key, value)
    except OSError:
        pass


_load_local_env()

# static_folder=None: 关掉 Flask 自带的 /static/<path> 路由，否则它会遮蔽下面服务 SPA 的
# /<path:path> 兜底路由（前端资源全在 FRONTEND_DIST/static 下，由兜底路由统一托管，单进程即可跑通）。
app = Flask(__name__, static_folder=None)

PROJECT_ROOT = os.environ.get("ARCHE_PROJECT_ROOT", "/app")
SRC_DIR = os.path.join(PROJECT_ROOT, "src")
RUN_TIMEOUT = int(os.environ.get("ARCHE_RUN_TIMEOUT", "600"))
MAX_QUESTION_LEN = int(os.environ.get("ARCHE_MAX_QUESTION_LEN", "8000"))
CONTROLLER_MODULE = "chemistry_multiagent.controllers.chemistry_multiagent_controller"


def _stage_run_inputs(work_dir: str) -> None:
    """controller 写死从 work_dir/toolpool/toolpool.json 读工具定义，并默认用相对目录 'papers'
    存检索 PDF。每次运行前把仓库内置 toolpool.json 拷进去并建好 papers 目录，否则真实工作流会报
    「工具定义文件不存在」「No such file or directory: 'papers'」。"""
    os.makedirs(os.path.join(work_dir, "toolpool"), exist_ok=True)
    os.makedirs(os.path.join(work_dir, "papers"), exist_ok=True)
    # controller 的 pdf_dir 默认相对目录 "papers"，subprocess cwd=PROJECT_ROOT，故落在 /app/papers。
    os.makedirs(os.path.join(PROJECT_ROOT, "papers"), exist_ok=True)
    src_toolpool = os.path.join(SRC_DIR, "chemistry_multiagent", "tools", "toolpool.json")
    if os.path.exists(src_toolpool):
        shutil.copyfile(src_toolpool, os.path.join(work_dir, "toolpool", "toolpool.json"))
# 前端 SPA 构建产物（pnpm build 后生成）。存在则由本服务直接托管，实现单容器单端口。
FRONTEND_DIST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frontend", "dist")

# 限制请求体大小，避免超大 payload 撑爆内存（agent 问题文本本就不应很大）。
app.config["MAX_CONTENT_LENGTH"] = int(os.environ.get("ARCHE_MAX_CONTENT_LENGTH", str(1024 * 1024)))

# === 执行记录持久化（JSONL / 线程安全 / 容量上限）===
# 默认写临时目录；生产环境用 ARCHE_HISTORY_PATH 指向挂载卷以跨重启保留（见 chart/values.yaml）。
HISTORY_PATH = os.environ.get("ARCHE_HISTORY_PATH", os.path.join(tempfile.gettempdir(), "arche-history.jsonl"))
HISTORY_MAX = int(os.environ.get("ARCHE_HISTORY_MAX", "200"))
_history_lock = threading.Lock()
_STATUS_RE = re.compile(r"状态[:：]\s*(\S+)")


def _parse_status(stdout: str) -> str | None:
    m = _STATUS_RE.search(stdout or "")
    return m.group(1) if m else None


# 把 controller 真实日志行解析成结构化进度事件（驱动前端 AgentLoop 可视化）。
# 事件源：controller 的 `📝 [<phase>] started/completed` 与 `🔄 反射循环轮次 N/M`。
_STEP_RE = re.compile(r"\[([a-zA-Z0-9_]+)\]\s*(started|completed)")
_ROUND_RE = re.compile(r"轮次\s*(\d+)\s*/\s*(\d+)")
# 去掉 `2026-06-01 03:01:01,441 - INFO - ` 这类日志前缀，只留消息主体，前端展示更干净。
_LOG_PREFIX_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[ T][\d:,\.]+\s*-\s*\w+\s*-\s*")

# 进度行 → info 级活动事件。只保留有信息量的里程碑（关键词/数量/工具调用/Gaussian/决策/结论）；
# 阶段开始结束已由 step 事件覆盖，这里不再重复推送"开始处理/流程完成/开始增强…"等冗余行，避免噪声。
_PROGRESS_PATS = (
    "提取关键词", "生成了", "调用工具", "执行工具", "工具调用",
    "gaussian", "高斯", "单点能", "频率分析", "irc",
    "采纳", "停止条件", "最终结论",
)
# 降级/回退/模拟行 → warn 级事件，让"多次降级处理"对用户可见（尤其 Gaussian 模拟假数据）。
_DEGRADE_PATS = (
    "回退", "fallback", "降级", "模拟", "simulated", "simulation", "mock",
    "rule_based", "规则启发", "default_fallback", "默认假设", "默认工具", "synthetic",
    "不可用", "未配置", "跳过", "skip", "not_supported", "兜底", "stale", "degrade",
)


def _parse_stream_event(line: str) -> dict | None:
    m = _STEP_RE.search(line)
    if m:
        return {"type": "step", "step": m.group(1), "status": m.group(2)}
    m = _ROUND_RE.search(line)
    if m:
        return {"type": "round", "round": int(m.group(1)), "total": int(m.group(2))}
    if "修订工作流" in line:
        return {"type": "revise"}
    low = line.lower()
    msg = _LOG_PREFIX_RE.sub("", line.strip())[:300]
    # 良性可选依赖告警(paperscraper/sentence-transformers 缺失)不算工作流降级，
    # 否则每次运行都误报"含降级 N"、污染可信度判断。归为普通 info。
    if "paperscraper" in low or "sentence-transformers" in low or "sentence_transformers" in low:
        return {"type": "log", "level": "info", "message": msg}
    # 分级优先级（一行只产一个事件）：
    #   1) 降级/回退/模拟 → warn —— 先于错误，避免"失败回退到备选"这类恢复被误判 ERROR；
    #   2) 强错误标记(traceback/exception/refused/英文 error) → error；
    #   3) "失败"但不含"成功"且无上述标记 → warn —— "API调用失败(尝试1/3)"是可重试告警而非致命；
    #      含"成功"的叙述(如"成功识别了失败的方向")不在此误红，落到进度/忽略；
    #   4) 进度关键词 → info。
    if any(p in low for p in _DEGRADE_PATS):
        return {"type": "log", "level": "warn", "message": msg}
    if "traceback" in low or "exception" in low or "refused" in low or "error" in low:
        return {"type": "log", "level": "error", "message": msg}
    if "失败" in line and "成功" not in line:
        return {"type": "log", "level": "warn", "message": msg}
    if any(p in low for p in _PROGRESS_PATS):
        return {"type": "log", "level": "info", "message": msg}
    return None


# controller 把最终结论/各阶段结果写进 work_dir/outputs/multiagent；这里在删 work_dir 前读回，
# 让前端拿到真实科学产物（最终结论、计算设置、逐轮明细），而不是只对 stdout 刮词。
ARTIFACT_RESULT_MAX = int(os.environ.get("ARCHE_ARTIFACT_MAX_BYTES", str(400 * 1024)))


def _read_artifacts(output_dir: str) -> tuple:
    """返回 (最终结构化结果 dict 或 None, 产物文件名列表)。"""
    result = None
    files: list[str] = []
    if not os.path.isdir(output_dir):
        return result, files
    try:
        files = sorted(n for n in os.listdir(output_dir) if os.path.isfile(os.path.join(output_dir, n)))
    except OSError:
        files = []
    for candidate in ("bounded_closed_loop_result.json", "closed_loop_error.json", "mock_workflow_result.json"):
        path = os.path.join(output_dir, candidate)
        if os.path.isfile(path):
            try:
                if os.path.getsize(path) <= ARTIFACT_RESULT_MAX:
                    with open(path, encoding="utf-8") as f:
                        result = json.load(f)
                else:
                    result = {"_truncated": True, "_file": candidate, "_bytes": os.path.getsize(path)}
            except (OSError, ValueError):
                result = None
            break
    return result, files


# 全过程时间线：controller 的 log_step() 把每个阶段/轮的 started/completed/failed/waiting 实时写进
# multiagent_log.json（按时间顺序的事件数组，每条 {timestamp, step, status, data}，data 含关键词/假设数/
# 决策与原因/工具与 Gaussian 摘要等科学内容）。读回它即可让前端把"整个自动化计算化学科研过程"渲染成
# 一条可展开的研究时间线 —— 实时完成态与历史回看共用同一数据源（history replay 天然成立）。
TIMELINE_MAX_BYTES = int(os.environ.get("ARCHE_TIMELINE_MAX_BYTES", str(800 * 1024)))


def _read_timeline(output_dir: str) -> list:
    path = os.path.join(output_dir, "multiagent_log.json")
    if not os.path.isfile(path):
        return []
    try:
        oversized = os.path.getsize(path) > TIMELINE_MAX_BYTES
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    steps = [e for e in data if isinstance(e, dict)][-400:]
    if oversized:
        # 异常大（个别 data 载荷很长）：保留骨架，去掉 data 以控响应/记录体积，完整内容仍可下载产物文件。
        steps = [
            {"timestamp": e.get("timestamp"), "step": e.get("step"), "status": e.get("status"), "data": {"_omitted": True}}
            for e in steps
        ]
    return steps


# 产物持久化：controller 把图谱/JSON/光谱等写进临时 work_dir/outputs/multiagent，run 结束后 work_dir 被
# rmtree 删除 → 产物全丢、无法下载。这里在删除前把产物复制到独立持久目录，按 run_id 归档，供下载端点取用。
# 默认落 HISTORY 同级目录（生产用 ARCHE_HISTORY_PATH 指向 PVC 即可跨重启保留，与历史记录同寿命）。
ARTIFACTS_DIR = os.environ.get(
    "ARCHE_ARTIFACTS_DIR",
    os.path.join(os.path.dirname(HISTORY_PATH) or tempfile.gettempdir(), "arche-artifacts"),
)
_RUN_ID_RE = re.compile(r"^[a-fA-F0-9]{8,64}$")


def _persist_artifacts(run_id: str, output_dir: str) -> list:
    """把本次 run 的产物从临时 work_dir 复制到持久目录，返回 [{name, size}]（供前端展示+下载）。"""
    saved: list[dict] = []
    if not os.path.isdir(output_dir) or not _RUN_ID_RE.match(run_id):
        return saved
    dest = os.path.join(ARTIFACTS_DIR, run_id)
    try:
        os.makedirs(dest, exist_ok=True)
        for name in sorted(os.listdir(output_dir)):
            src = os.path.join(output_dir, name)
            if not os.path.isfile(src):
                continue
            try:
                shutil.copy2(src, os.path.join(dest, name))
                saved.append({"name": name, "size": os.path.getsize(src)})
            except OSError:
                continue
    except OSError:
        return saved
    return saved


# Gaussian .log/.out files are written by execution into this run's job directories
# (GAUSSIAN_JOB_ROOT and ARCHE_DETERMINISTIC_DIR are narrowed by _build_run_env to
# per-run directories). _persist_artifacts only copies output_dir's top-level files,
# so this harvest step copies explicitly recorded log paths into the persisted
# artifacts directory before the temporary work_dir is removed.
#
# Trust boundary: result/timeline JSON contains model output and user input, so every
# path found there is untrusted. A path is copied only after realpath normalization,
# controlled-root confinement, regular-file checks, and symlink-leaf rejection.
LOG_ARTIFACT_MAX = int(os.environ.get("ARCHE_LOG_ARTIFACT_MAX_BYTES", str(25 * 1024 * 1024)))
_LOG_SCAN_JSON_MAX = int(os.environ.get("ARCHE_LOG_SCAN_JSON_MAX_BYTES", str(8 * 1024 * 1024)))
_LOG_SCAN_JSON_MAX_DEPTH = int(os.environ.get("ARCHE_LOG_SCAN_JSON_MAX_DEPTH", "80"))
_LOG_SCAN_JSON_MAX_NODES = int(os.environ.get("ARCHE_LOG_SCAN_JSON_MAX_NODES", "20000"))
_LOG_SUFFIXES = (".log", ".out")


def _allowed_log_roots(output_dir: str, run_id: str) -> set:
    """Return realpath-normalized roots trusted for this run's log harvest.

    The default root is the run's temporary work_dir, derived from output_dir. If
    operators configure shared GAUSSIAN_JOB_ROOT or ARCHE_DETERMINISTIC_DIR values,
    only their per-run child (<root>/<run_id>) is trusted, never the whole shared
    root. ARCHE_LOG_HARVEST_ROOTS remains an explicit operator escape hatch.
    """
    roots: set = set()
    try:
        work_dir = os.path.dirname(os.path.dirname(os.path.realpath(output_dir)))
        if work_dir:
            roots.add(work_dir)
    except OSError:
        pass
    if _RUN_ID_RE.match(run_id or ""):
        for var in ("GAUSSIAN_JOB_ROOT", "ARCHE_DETERMINISTIC_DIR"):
            val = (os.environ.get(var) or "").strip()
            if val:
                roots.add(os.path.realpath(os.path.join(val, run_id)))  # 仅信任本次 run 子目录
    for extra in (os.environ.get("ARCHE_LOG_HARVEST_ROOTS") or "").split(os.pathsep):
        extra = extra.strip()
        if extra:
            roots.add(os.path.realpath(extra))
    return {r for r in roots if r and r != os.sep}


def _is_within_roots(real_path: str, roots: set) -> bool:
    """Return whether real_path is equal to or contained by a trusted root."""
    return any(real_path == root or real_path.startswith(root + os.sep) for root in roots)


def _safe_log_realpath(src, roots: set):
    """Return a safe realpath for a trusted .log/.out file, otherwise None."""
    if not isinstance(src, str) or not src:
        return None
    if not os.path.isabs(src) or not src.lower().endswith(_LOG_SUFFIXES):
        return None
    try:
        if os.path.islink(src):
            return None
        real = os.path.realpath(src)
        if not os.path.isfile(real):
            return None
    except (OSError, ValueError):
        return None
    return real if (roots and _is_within_roots(real, roots)) else None


def _collect_log_paths_from_json(obj, acc: set) -> None:
    """Collect absolute .log/.out-looking strings without trusting them.

    The JSON may be model/user-controlled, so traversal is iterative and bounded by
    depth and node count. Existence, symlink, and root checks stay centralized in
    _safe_log_realpath.
    """
    stack = [(obj, 0)]
    visited = 0
    while stack:
        value, depth = stack.pop()
        visited += 1
        if visited > _LOG_SCAN_JSON_MAX_NODES or depth > _LOG_SCAN_JSON_MAX_DEPTH:
            continue
        if isinstance(value, dict):
            stack.extend((child, depth + 1) for child in value.values())
        elif isinstance(value, list):
            stack.extend((child, depth + 1) for child in value)
        elif isinstance(value, str):
            if os.path.isabs(value) and value.lower().endswith(_LOG_SUFFIXES):
                acc.add(value)


def _harvest_log_artifacts(run_id: str, output_dir: str, existing: list) -> list:
    """Copy this run's Gaussian .log/.out artifacts into persistent storage.

    Sources are explicit paths recorded in execution_result, bounded result, and
    timeline JSON. Those JSON files are untrusted; only _safe_log_realpath can approve
    a candidate for copying.
    """
    saved: list[dict] = []
    if not os.path.isdir(output_dir) or not _RUN_ID_RE.match(run_id):
        return saved
    roots = _allowed_log_roots(output_dir, run_id)
    if not roots:
        return saved
    candidates: set = set()
    for name in ("execution_result.json", "bounded_closed_loop_result.json", "multiagent_log.json"):
        path = os.path.join(output_dir, name)
        try:
            if not os.path.isfile(path) or os.path.getsize(path) > _LOG_SCAN_JSON_MAX:
                continue
            with open(path, encoding="utf-8") as f:
                _collect_log_paths_from_json(json.load(f), candidates)
        except (OSError, ValueError):
            continue
    real_paths = {real for real in (_safe_log_realpath(p, roots) for p in candidates) if real}
    if not real_paths:
        return saved
    dest = os.path.join(ARTIFACTS_DIR, run_id)
    try:
        os.makedirs(dest, exist_ok=True)
    except OSError:
        return saved
    used = {str(e.get("name")) for e in (existing or []) if isinstance(e, dict)}
    for real in sorted(real_paths):
        try:
            size = os.path.getsize(real)
            if size > LOG_ARTIFACT_MAX:
                continue
            base = os.path.basename(real)
            name = base
            i = 1
            while name in used:
                stem, ext = os.path.splitext(base)
                name = f"{stem}_{i}{ext}"
                i += 1
            shutil.copy2(real, os.path.join(dest, name))
            used.add(name)
            saved.append({"name": name, "size": size})
        except OSError:
            continue
    return saved


def _delete_artifacts(run_id: str) -> None:
    if _RUN_ID_RE.match(run_id):
        shutil.rmtree(os.path.join(ARTIFACTS_DIR, run_id), ignore_errors=True)


def _gc_orphan_artifacts(kept_lines: list) -> None:
    """历史裁剪后回收：删除 ARTIFACTS_DIR 下 run_id 已不在保留历史中的产物目录，
    让产物与历史记录真正同寿命（否则历史裁到 HISTORY_MAX 条后，旧产物会永久滞留撑爆磁盘）。"""
    if not os.path.isdir(ARTIFACTS_DIR):
        return
    keep: set = set()
    for ln in kept_lines:
        try:
            rid = json.loads(ln).get("id")
        except (json.JSONDecodeError, AttributeError):
            continue
        if rid:
            keep.add(rid)
    try:
        for name in os.listdir(ARTIFACTS_DIR):
            if name not in keep and _RUN_ID_RE.match(name):
                shutil.rmtree(os.path.join(ARTIFACTS_DIR, name), ignore_errors=True)
    except OSError:
        pass


def _append_history(record: dict) -> None:
    with _history_lock:
        directory = os.path.dirname(HISTORY_PATH)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(HISTORY_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        # 超出上限则重写为最近 HISTORY_MAX 条，避免文件无限增长。
        try:
            with open(HISTORY_PATH, encoding="utf-8") as f:
                lines = [ln for ln in f if ln.strip()]
            if len(lines) > HISTORY_MAX:
                kept_lines = lines[-HISTORY_MAX:]
                with open(HISTORY_PATH, "w", encoding="utf-8") as f:
                    f.writelines(kept_lines)
                _gc_orphan_artifacts(kept_lines)  # 同步回收被裁掉的 run 的产物目录
        except OSError:
            pass


def _read_history() -> list:
    if not os.path.isfile(HISTORY_PATH):
        return []
    out = []
    with _history_lock:
        with open(HISTORY_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return out


def _delete_history(run_id: str) -> bool:
    with _history_lock:
        if not os.path.isfile(HISTORY_PATH):
            return False
        kept: list[str] = []
        removed = False
        with open(HISTORY_PATH, encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if not s:
                    continue
                try:
                    rec = json.loads(s)
                except json.JSONDecodeError:
                    continue
                if rec.get("id") == run_id:
                    removed = True
                    continue
                kept.append(line if line.endswith("\n") else line + "\n")
        if removed:
            with open(HISTORY_PATH, "w", encoding="utf-8") as f:
                f.writelines(kept)
        return removed


def _update_history(run_id: str, patch: dict) -> None:
    """按 id 原地更新一条记录（用于 running → 终态；流式断连标记 interrupted）。"""
    with _history_lock:
        if not os.path.isfile(HISTORY_PATH):
            return
        out = []
        with open(HISTORY_PATH, encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if not s:
                    continue
                try:
                    rec = json.loads(s)
                except json.JSONDecodeError:
                    continue
                if rec.get("id") == run_id:
                    rec.update(patch)
                out.append(json.dumps(rec, ensure_ascii=False) + "\n")
        with open(HISTORY_PATH, "w", encoding="utf-8") as f:
            f.writelines(out)


def _finalize_orphan_runs() -> None:
    """启动自愈：进程刚起，上一进程里任何在飞的 run 都已随旧进程 / Pod 一起死掉，
    其后台 runner 线程来不及把记录从 'running' 翻成终态，会永远卡 running、UI 显示
    「无日志」。这里在 serve 前把历史里残留的 status='running' 一律标为 'interrupted'。
    单 replica + /data 持久卷，新进程在此之后才会产生新的 running 记录，故无竞态。
    幂等：已是终态的记录不动。"""
    healed = 0
    with _history_lock:
        if not os.path.isfile(HISTORY_PATH):
            return
        out: list[str] = []
        try:
            with open(HISTORY_PATH, encoding="utf-8") as f:
                for line in f:
                    s = line.strip()
                    if not s:
                        continue
                    try:
                        rec = json.loads(s)
                    except json.JSONDecodeError:
                        out.append(line if line.endswith("\n") else line + "\n")
                        continue
                    if rec.get("status") == "running":
                        rec["status"] = "interrupted"
                        if not rec.get("stderr"):
                            rec["stderr"] = "run interrupted：ARCHE 进程 / Pod 在该 run 完成前重启（如版本部署），后台执行被中断。请重新发起。"
                        healed += 1
                    out.append(json.dumps(rec, ensure_ascii=False) + "\n")
            if healed:
                with open(HISTORY_PATH, "w", encoding="utf-8") as f:
                    f.writelines(out)
        except OSError:
            return
    if healed:
        print(f"[ARCHE] startup self-heal: marked {healed} orphan running run(s) as interrupted", file=sys.stderr, flush=True)


# === 运行时可覆盖配置（前端右上角配置弹窗写入；落盘 gitignored 文件，跨重启保留）===
# 部署锁定：设 ARCHE_UI_CONFIG_ENABLED=0 即禁止 UI 改配置（只认 helm 注入的环境变量）。
RUNTIME_CONFIG_PATH = os.environ.get(
    "ARCHE_RUNTIME_CONFIG_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".arche-runtime.json"),
)
UI_CONFIG_ENABLED = os.environ.get("ARCHE_UI_CONFIG_ENABLED", "1") != "0"
_config_lock = threading.Lock()


def _read_runtime_config() -> dict:
    if not os.path.isfile(RUNTIME_CONFIG_PATH):
        return {}
    try:
        with open(RUNTIME_CONFIG_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_runtime_config(data: dict) -> None:
    with _config_lock:
        with open(RUNTIME_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)


def _runtime_env_overrides() -> dict:
    """UI 运行时配置 → 子进程环境变量覆盖（仅白名单字段）。"""
    cfg = _read_runtime_config()
    out: dict = {}
    if cfg.get("baseUrl"):
        out["DEEPSEEK_BASE_URL"] = str(cfg["baseUrl"])
    if cfg.get("model"):
        out["DEEPSEEK_MODEL"] = str(cfg["model"])
    if cfg.get("apiKey"):
        out["DEEPSEEK_API_KEY"] = str(cfg["apiKey"])
    if cfg.get("apiKeyHeader"):
        out["ARCHE_LLM_API_KEY_HEADER"] = str(cfg["apiKeyHeader"])
    if cfg.get("ingressAk"):
        out["ARCHE_LLM_INGRESS_AK"] = str(cfg["ingressAk"])
    if cfg.get("ingressSk"):
        out["ARCHE_LLM_INGRESS_SK"] = str(cfg["ingressSk"])
    # 可选：Semantic Scholar API key（文献检索提额，避免匿名池 service-limit 限流）。
    if cfg.get("s2Key"):
        out["SEMANTIC_SCHOLAR_API_KEY"] = str(cfg["s2Key"])
    return out


def _build_run_env(work_dir: str, overrides: dict, run_id: str) -> dict:
    """Build the controller subprocess environment with per-run Gaussian output dirs.

    GAUSSIAN_JOB_ROOT and ARCHE_DETERMINISTIC_DIR are isolated per run. Explicit
    shared/cluster roots are narrowed to <root>/<run_id>; otherwise both land under
    work_dir so logs have the same lifetime and trusted boundary as the run.
    """
    env = dict(os.environ)
    env.update(overrides)
    env["PYTHONPATH"] = SRC_DIR + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env["ARCHE_PROJECT_ROOT"] = PROJECT_ROOT
    shared_job_root = (env.get("GAUSSIAN_JOB_ROOT") or "").strip()
    env["GAUSSIAN_JOB_ROOT"] = (
        os.path.join(shared_job_root, run_id) if shared_job_root else os.path.join(work_dir, "gaussian_jobs")
    )
    shared_det_dir = (env.get("ARCHE_DETERMINISTIC_DIR") or "").strip()
    env["ARCHE_DETERMINISTIC_DIR"] = (
        os.path.join(shared_det_dir, run_id) if shared_det_dir else os.path.join(env["GAUSSIAN_JOB_ROOT"], "deterministic")
    )
    return env


def _mask_secret(secret: str) -> str:
    if not secret:
        return ""
    return "••••" + secret[-4:] if len(secret) > 4 else "••••"


def _model_health() -> dict:
    """轻量探活当前配置的模型端点（TCP 连通性），不放进 /healthz（K8s liveness 要快且只测服务本体）。"""
    overrides = _runtime_env_overrides()
    base = overrides.get("DEEPSEEK_BASE_URL") or os.environ.get("DEEPSEEK_BASE_URL", "")
    key = overrides.get("DEEPSEEK_API_KEY") or os.environ.get("DEEPSEEK_API_KEY", "")
    reachable = None
    if base:
        reachable = False
        try:
            u = urlparse(base)
            host = u.hostname
            port = u.port or (443 if u.scheme == "https" else 80)
            if host:
                # 发一次 HTTP 请求：TCP 通但不回 HTTP（empty reply）也算不可达，避免误报"在线"。
                conn_cls = http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection
                conn = conn_cls(host, port, timeout=3)
                try:
                    conn.request("GET", u.path or "/")
                    conn.getresponse()  # 任何 HTTP 响应（含 4xx/5xx）即视为可达
                    reachable = True
                finally:
                    conn.close()
        except (OSError, http.client.HTTPException):
            reachable = False
    return {"configured": bool(base and key), "reachable": reachable, "baseUrl": base or None}


@app.get("/healthz")
def healthz():
    return jsonify({"status": "ok", "agent": "ARCHE"})


@app.get("/api/info")
def info():
    return jsonify(
        {
            "name": "ARCHE",
            "kind": "application",
            "description": "Computational-chemistry research multi-agent (retrieval → hypothesis → planning → execution → reflection).",
            "entry": CONTROLLER_MODULE,
            # 构建时 --build-arg ARCHE_VERSION=<tag> 烤进镜像；前端 Header 展示，用户据此确认部署版本。
            "version": os.environ.get("ARCHE_VERSION", "unknown"),
        }
    )


@app.post("/api/run")
def run():
    body = request.get_json(silent=True) or {}
    question = str(body.get("question") or body.get("input") or "").strip()
    if not question:
        return jsonify({"error": "missing 'question'"}), 400
    if len(question) > MAX_QUESTION_LEN:
        return jsonify({"error": f"'question' too long (>{MAX_QUESTION_LEN} chars)"}), 413
    overrides = _runtime_env_overrides()
    run_id = uuid.uuid4().hex
    created_at = int(time.time() * 1000)  # UTC epoch ms（前端按本地时区格式化）

    with tempfile.TemporaryDirectory(prefix="arche-run-") as work_dir:
        _stage_run_inputs(work_dir)
        cmd = [
            sys.executable,
            "-m",
            CONTROLLER_MODULE,
            "--question",
            question,
            "--work-dir",
            work_dir,
        ]

        env = _build_run_env(work_dir, overrides, run_id)

        try:
            proc = subprocess.run(
                cmd,
                cwd=PROJECT_ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=RUN_TIMEOUT,
            )
        except subprocess.TimeoutExpired as exc:
            # 保留超时前已捕获的部分输出（看清卡在哪一步），不要丢成空 stdout —— 否则"原始日志"无内容可看。
            partial_out = (exc.stdout or "") if isinstance(exc.stdout, str) else (exc.stdout.decode("utf-8", "replace") if exc.stdout else "")
            partial_err = (exc.stderr or "") if isinstance(exc.stderr, str) else (exc.stderr.decode("utf-8", "replace") if exc.stderr else "")
            record = {
                "id": run_id,
                "createdAt": created_at,
                "question": question,
                "exitCode": None,
                "status": "timeout",
                "stdout": partial_out[-20000:],
                "stderr": (f"run timed out after {RUN_TIMEOUT}s\n" + partial_err)[-8000:],
            }
            _append_history(record)
            return jsonify({"id": run_id, "createdAt": created_at, "error": record["stderr"]}), 504

        # 与流式路径一致：在 work_dir 被销毁前读回结构化结果并持久化产物，
        # 否则非流式记录缺 result/artifacts、产物随临时目录一起删除（两条路径契约不一致）。
        output_dir = os.path.join(work_dir, "outputs", "multiagent")
        result_json, _ = _read_artifacts(output_dir)
        timeline = _read_timeline(output_dir)
        artifact_files = _persist_artifacts(run_id, output_dir)
        artifact_files += _harvest_log_artifacts(run_id, output_dir, artifact_files)
        # stdout/stderr 截尾，避免超大日志撑爆响应/记录。
        record = {
            "id": run_id,
            "createdAt": created_at,
            "question": question,
            "exitCode": proc.returncode,
            "status": _parse_status(proc.stdout),
            "result": result_json,
            "timeline": timeline,
            "artifacts": artifact_files,
            "stdout": proc.stdout[-20000:],
            "stderr": proc.stderr[-8000:],
        }
        _append_history(record)
        return jsonify(
            {
                "id": run_id,
                "createdAt": created_at,
                "exitCode": record["exitCode"],
                "status": record["status"],
                "result": result_json,
                "timeline": timeline,
                "artifacts": artifact_files,
                "stdout": record["stdout"],
                "stderr": record["stderr"],
            }
        ), (200 if proc.returncode == 0 else 500)


@app.post("/api/run/stream")
def run_stream():
    """流式运行：把 controller 真实阶段/轮次事件按 NDJSON 实时推给前端（驱动 AgentLoop）。"""
    body = request.get_json(silent=True) or {}
    question = str(body.get("question") or body.get("input") or "").strip()
    if not question:
        return jsonify({"error": "missing 'question'"}), 400
    if len(question) > MAX_QUESTION_LEN:
        return jsonify({"error": f"'question' too long (>{MAX_QUESTION_LEN} chars)"}), 413

    overrides = _runtime_env_overrides()
    run_id = uuid.uuid4().hex
    created_at = int(time.time() * 1000)

    # 运行与客户端连接解耦：子进程在后台线程跑到完成并落历史；客户端断连只停止推流、
    # 不再 terminate 子进程（断连≠取消）。长任务因此不会被网关/浏览器的流读超时杀成
    # "interrupted"；前端在流中断后回退轮询 /api/runs/<id> 即可拿到最终结果。
    event_q: "queue.Queue" = queue.Queue(maxsize=2000)
    client_open = threading.Event()
    client_open.set()
    _SENTINEL = object()

    def _emit(evt: dict) -> None:
        # 仅在客户端仍连接时推流；断连后 runner 照常把 run 跑完，只是不再喂事件（避免无人消费时堆积）。
        if client_open.is_set():
            try:
                event_q.put_nowait(evt)
            except queue.Full:
                pass

    def _runner() -> None:
        work_dir = tempfile.mkdtemp(prefix="arche-run-")
        proc = None
        timer = None
        timed_out = threading.Event()
        captured: list[str] = []
        try:
            _stage_run_inputs(work_dir)
            cmd = [sys.executable, "-m", CONTROLLER_MODULE, "--question", question, "--work-dir", work_dir]
            env = _build_run_env(work_dir, overrides, run_id)
            env["PYTHONUNBUFFERED"] = "1"  # 子进程行缓冲，保证实时

            # 立即落一条 running 记录：run 出现在历史里，流断也不会凭空消失。
            _append_history(
                {
                    "id": run_id,
                    "createdAt": created_at,
                    "question": question,
                    "status": "running",
                    "exitCode": None,
                    "result": None,
                    "artifacts": [],
                    "stdout": "",
                    "stderr": "",
                }
            )

            proc = subprocess.Popen(
                cmd, cwd=PROJECT_ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1
            )

            def _kill_on_timeout() -> None:
                if proc and proc.poll() is None:
                    timed_out.set()
                    proc.kill()

            timer = threading.Timer(RUN_TIMEOUT, _kill_on_timeout)
            timer.daemon = True
            timer.start()
            assert proc.stdout is not None
            for line in proc.stdout:
                captured.append(line)
                if len(captured) > 5000:
                    captured.pop(0)
                evt = _parse_stream_event(line)
                if evt:
                    _emit(evt)
            proc.wait()

            full = "".join(captured)
            output_dir = os.path.join(work_dir, "outputs", "multiagent")
            result_json, _ = _read_artifacts(output_dir)
            timeline = _read_timeline(output_dir)  # 全过程时间线（multiagent_log.json）
            # 在 work_dir 被 rmtree 前持久化产物文件，artifacts 改为 [{name,size}] 供下载。
            artifact_files = _persist_artifacts(run_id, output_dir)
            artifact_files += _harvest_log_artifacts(run_id, output_dir, artifact_files)
            status = "timeout" if timed_out.is_set() else _parse_status(full)
            record = {
                "id": run_id,
                "createdAt": created_at,
                "question": question,
                "exitCode": proc.returncode,
                "status": status,
                "result": result_json,
                "timeline": timeline,
                "artifacts": artifact_files,
                "stdout": full[-20000:],
                "stderr": ("run timed out after %ds" % RUN_TIMEOUT) if timed_out.is_set() else "",
            }
            _update_history(run_id, record)
            _emit(
                {
                    "type": "done",
                    "id": run_id,
                    "createdAt": created_at,
                    "exitCode": proc.returncode,
                    "status": status,
                    "result": result_json,
                    "timeline": timeline,
                    "artifacts": artifact_files,
                    "stdout": record["stdout"],
                    "stderr": record["stderr"],
                }
            )
        except Exception as exc:  # 后台执行异常也落历史，避免 run 永远卡在 running、原因彻底丢失。
            partial = "".join(captured)
            _update_history(
                run_id,
                {
                    "status": "failed",
                    "exitCode": proc.returncode if proc is not None else None,
                    "stdout": partial[-20000:],
                    "stderr": str(exc)[-8000:],
                },
            )
            _emit({"type": "done", "id": run_id, "status": "failed", "stderr": str(exc)[-2000:]})
        finally:
            if timer is not None:
                timer.cancel()
            shutil.rmtree(work_dir, ignore_errors=True)
            try:
                event_q.put_nowait(_SENTINEL)
            except queue.Full:
                pass

    worker = threading.Thread(target=_runner, name="arche-run-%s" % run_id, daemon=True)
    worker.start()

    def generate():
        yield json.dumps({"type": "start", "id": run_id, "createdAt": created_at, "question": question}, ensure_ascii=False) + "\n"
        try:
            while True:
                try:
                    evt = event_q.get(timeout=1.0)
                except queue.Empty:
                    # runner 结束且队列已空即收尾（哨兵可能因队列满被丢弃，这里兜底退出）。
                    if not worker.is_alive() and event_q.empty():
                        break
                    continue
                if evt is _SENTINEL:
                    break
                yield json.dumps(evt, ensure_ascii=False) + "\n"
        finally:
            # 客户端断连：仅停止推流；后台 runner 仍把 run 跑完并落历史（断连≠取消）。
            client_open.clear()

    return app.response_class(generate(), mimetype="application/x-ndjson")


@app.get("/api/runs")
def list_runs():
    limit = request.args.get("limit", default=50, type=int) or 50
    records = _read_history()
    items = list(reversed(records))[: max(1, limit)]
    light = [{k: r.get(k) for k in ("id", "createdAt", "question", "exitCode", "status")} for r in items]
    return jsonify({"items": light, "total": len(records)})


@app.get("/api/runs/<run_id>")
def get_run(run_id: str):
    for r in reversed(_read_history()):
        if r.get("id") == run_id:
            return jsonify(r)
    return jsonify({"error": "not found"}), 404


@app.get("/api/runs/<run_id>/artifacts")
def list_artifacts(run_id: str):
    """列出某次 run 已持久化的产物文件（name + size）。"""
    items: list[dict] = []
    if _RUN_ID_RE.match(run_id):
        d = os.path.join(ARTIFACTS_DIR, run_id)
        if os.path.isdir(d):
            for name in sorted(os.listdir(d)):
                p = os.path.join(d, name)
                if os.path.isfile(p):
                    items.append({"name": name, "size": os.path.getsize(p)})
    return jsonify({"items": items})


@app.get("/api/runs/<run_id>/artifacts/<path:name>")
def download_artifact(run_id: str, name: str):
    """下载单个产物文件。send_from_directory 自带 safe_join 防路径遍历。"""
    if not _RUN_ID_RE.match(run_id):
        return jsonify({"error": "not found"}), 404
    d = os.path.join(ARTIFACTS_DIR, run_id)
    if not os.path.isdir(d):
        return jsonify({"error": "not found"}), 404
    try:
        return send_from_directory(d, name, as_attachment=True)
    except Exception:
        # send_from_directory 对越界/不存在的文件抛 NotFound；统一回 JSON 404。
        return jsonify({"error": "not found"}), 404


@app.delete("/api/runs/<run_id>")
def delete_run(run_id: str):
    ok = _delete_history(run_id)
    _delete_artifacts(run_id)
    return jsonify({"deleted": ok}), (200 if ok else 404)


@app.get("/api/config")
def get_config():
    cfg = _read_runtime_config()

    def eff(field: str, env_name: str, default: str) -> str:
        v = cfg.get(field)
        return v if v not in (None, "") else os.environ.get(env_name, default)

    api_key = cfg.get("apiKey") or os.environ.get("DEEPSEEK_API_KEY", "")
    ingress_ak = cfg.get("ingressAk") or os.environ.get("ARCHE_LLM_INGRESS_AK", "")
    ingress_sk = cfg.get("ingressSk") or os.environ.get("ARCHE_LLM_INGRESS_SK", "")
    s2_key = cfg.get("s2Key") or os.environ.get("SEMANTIC_SCHOLAR_API_KEY", "")
    return jsonify(
        {
            "enabled": UI_CONFIG_ENABLED,
            "baseUrl": eff("baseUrl", "DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
            "model": eff("model", "DEEPSEEK_MODEL", "interns2-preview-sft"),
            "apiKeyHeader": eff("apiKeyHeader", "ARCHE_LLM_API_KEY_HEADER", "x-api-key"),
            "apiKeySet": bool(api_key),
            "apiKeyMasked": _mask_secret(api_key),
            "ingressAkSet": bool(ingress_ak),
            "ingressAkMasked": _mask_secret(ingress_ak),
            "ingressSkSet": bool(ingress_sk),
            "ingressSkMasked": _mask_secret(ingress_sk),
            "s2KeySet": bool(s2_key),
            "s2KeyMasked": _mask_secret(s2_key),
        }
    )


@app.put("/api/config")
def update_config():
    if not UI_CONFIG_ENABLED:
        return jsonify({"error": "UI configuration disabled (ARCHE_UI_CONFIG_ENABLED=0)"}), 403
    body = request.get_json(silent=True) or {}
    cfg = _read_runtime_config()
    for field in ("baseUrl", "model", "apiKeyHeader"):
        if field in body:
            cfg[field] = str(body[field] or "").strip()
    # 密钥类字段：仅显式传入非空才更新（避免被掩码占位值覆盖真实值）；clear* 显式清除。
    if body.get("apiKey"):
        cfg["apiKey"] = str(body["apiKey"]).strip()
    elif body.get("clearApiKey"):
        cfg.pop("apiKey", None)
    if body.get("ingressAk"):
        cfg["ingressAk"] = str(body["ingressAk"]).strip()
    elif body.get("clearIngressAk"):
        cfg.pop("ingressAk", None)
    if body.get("ingressSk"):
        cfg["ingressSk"] = str(body["ingressSk"]).strip()
    elif body.get("clearIngressSk"):
        cfg.pop("ingressSk", None)
    if body.get("s2Key"):
        cfg["s2Key"] = str(body["s2Key"]).strip()
    elif body.get("clearS2Key"):
        cfg.pop("s2Key", None)
    _write_runtime_config(cfg)
    return get_config()


@app.get("/api/model-status")
def model_status():
    return jsonify({"ok": True, "model": _model_health()})


@app.get("/")
def _spa_index():
    # 有构建产物则返回 SPA 首页；否则给出友好提示（dev 时前端走 Vite，不经此路由）。
    if os.path.isfile(os.path.join(FRONTEND_DIST, "index.html")):
        return send_from_directory(FRONTEND_DIST, "index.html")
    return jsonify({"agent": "ARCHE", "ui": "not-built", "hint": "cd frontend && pnpm install && pnpm build"})


@app.get("/<path:path>")
def _spa_assets(path: str):
    # 显式 /healthz、/api/* 路由优先级更高，这里只兜 SPA 静态资源与前端路由回退。
    candidate = os.path.normpath(os.path.join(FRONTEND_DIST, path))
    if candidate.startswith(FRONTEND_DIST) and os.path.isfile(candidate):
        return send_from_directory(FRONTEND_DIST, path)
    index = os.path.join(FRONTEND_DIST, "index.html")
    if os.path.isfile(index):
        return send_from_directory(FRONTEND_DIST, "index.html")
    return jsonify({"error": "not found"}), 404


def _serve() -> None:
    # 启动即自愈被重启杀掉的孤儿 running 记录（见 _finalize_orphan_runs）。
    _finalize_orphan_runs()
    host = os.environ.get("ARCHE_BIND_HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8501"))
    try:
        # 生产级 WSGI：单进程多线程。长 /api/run 执行期间 /healthz 仍可响应，
        # 避免 K8s liveness 探针在工作流运行时被阻塞而误杀 Pod（Flask 自带服务器默认单线程会被卡死）。
        from waitress import serve

        serve(app, host=host, port=port, threads=int(os.environ.get("ARCHE_SERVER_THREADS", "8")))
    except ImportError:
        # 兜底：waitress 缺失时退回 Flask 内置服务器，仍开 threaded 以免健康检查被长任务阻塞。
        app.run(host=host, port=port, threaded=True)


if __name__ == "__main__":
    _serve()
