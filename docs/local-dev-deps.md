# 本地开发扩展依赖（真实文献检索 + Gaussian 解析）

部署镜像的 `requirements.txt` 只装最小基线（controller CLI + mock 验证够用）。要在**本地**跑通
「真实文献检索 + 语义向量 + Gaussian 输出解析」，需要额外几项依赖。缺失时对应环节会**优雅降级**、
整轮 workflow 仍能跑通：

| 缺失依赖 | 降级行为 |
|---|---|
| `paper-scraper` | 跳过论文下载（`PAPERSCRAPER_AVAILABLE=False`），仅用本地语料/摘要做语义检索 |
| `sentence-transformers` | 检索退化为弱词频向量（质量低） |
| `cclib` | `parse_gaussian_output` 无法解析 `.log/.out`（有真实 Gaussian 结果时才影响） |

## 安装（在项目 venv 内）

```bash
cd "$ARCHE_PROJECT_ROOT"          # 或仓库根
source .venv/bin/activate         # 用项目 venv，不要用系统/conda 的 python

# 1) 直接可 pip 装的两项
pip install -r requirements-dev.txt

# 2) 文献检索用的 paper-scraper —— 必须从仓库内 vendored 目录装（见下方“坑”）
SETUPTOOLS_SCM_PRETEND_VERSION_FOR_PAPER_SCRAPER=1.0.0 \
  pip install ./src/chemistry_multiagent/tools/paper-scraper
```

验证：

```bash
python -c "import paperscraper, cclib, sentence_transformers; \
print('paperscraper.search_papers:', callable(paperscraper.search_papers)); \
print('cclib', cclib.__version__)"
```

## ⚠ paper-scraper 的坑（务必看）

- **不要 `pip install paperscraper`**。PyPI 上同名的 `paperscraper`（jannisborn 版）是**另一个不兼容的包**，
  靠预下载的 biorxiv/chemrxiv 数据 dump，**没有 `search_papers`**。装了它会盖住仓库自带的、正确的
  `paper-scraper`（whitead 版），检索直接报：

  ```
  module 'paperscraper' has no attribute 'search_papers'
  ```

  若已误装：`pip uninstall -y paperscraper`，再按上面第 2 步装 vendored 版。

- 为什么要 `SETUPTOOLS_SCM_PRETEND_VERSION_FOR_PAPER_SCRAPER=1.0.0`：vendored 目录是源码快照、没有自己的
  git 元数据，setuptools-scm 取不到版本会构建失败；给个伪版本号即可（构建时会自动生成被 gitignore 的
  `paperscraper/version.py`，无需手工维护）。

## 真正下到论文：Semantic Scholar API key（可选）

装好后检索能连上 Semantic Scholar，但匿名请求会撞限流（日志出现
`Failed to avoid a service limit`），下到 0 篇属正常。要真正下 PDF，填一个免费的 S2 key：

- 前端配置弹窗的 **Semantic Scholar API Key**（`s2Key`），或
- `.env.local` 里 `SEMANTIC_SCHOLAR_API_KEY=...`

## 说明

- 这些依赖**不进部署镜像**（`sentence-transformers` 会拉入 torch，体积大）；生产环境按需在自己的
  基础镜像里加装。
- `requirements-dev.txt` 只列可直接 pip 安装的 `cclib` / `sentence-transformers`；paper-scraper 因需路径 +
  伪版本号，单独按上面命令装。
