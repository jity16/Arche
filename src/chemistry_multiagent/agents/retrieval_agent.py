#!/usr/bin/env python3
"""
Retrieval Agent - 检索智能体

功能：
1. 解析用户科学问题，提取关键词
2. 检索相关学术论文
3. 构建论文索引
4. 提供背景信息和回答用户问题
5. 为后续假设生成Agent提供文献综述

整合自：
- extract_keywords_for_search_paper.py
- search.py
"""

import os
import ast
import json
import re
import logging
import time
import numpy as np
from typing import List, Dict, Tuple, Optional, Any
from tenacity import retry, stop_after_attempt, wait_exponential

# PDF处理
import fitz  # PyMuPDF

# 向量检索
import faiss

# 论文爬取
try:
    import paperscraper
    PAPERSCRAPER_AVAILABLE = True
    # paperscraper 对每篇下不到的论文（付费墙 403 / 无免费 PDF / PMC 缺失 / 它自身的 URL 拼接 bug）
    # 都把整段 traceback 抛到 stderr —— 这些是「下载失败」而非程序错误，却把日志刷成一片红、像崩了。
    # 这是第三方库的固有噪声（免费全文抓取本就大量失败）；压掉它（及其异步依赖）的日志级别，
    # 改由本 agent 在 search_papers 末尾输出一行干净汇总，避免误导成「代码报错」。
    for _noisy_logger in ("paperscraper", "aiohttp", "asyncio", "urllib3"):
        logging.getLogger(_noisy_logger).setLevel(logging.CRITICAL)
        logging.getLogger(_noisy_logger).propagate = False
except ImportError:
    PAPERSCRAPER_AVAILABLE = False
    print("警告: paperscraper 不可用，将跳过论文下载功能")

# 嵌入模型
try:
    from sentence_transformers import SentenceTransformer
    SENTENCE_TRANSFORMERS_AVAILABLE = True
except ImportError:
    SENTENCE_TRANSFORMERS_AVAILABLE = False
    print("警告: sentence-transformers 不可用，将使用简单文本匹配")

# Deepseek API
import openai

# 推理模型 <think> 思维链剥离(retrieval_agent 直接用 self.client，需在提取处显式剥离）
try:
    from chemistry_multiagent.utils.llm_api import strip_reasoning
except ImportError:
    from utils.llm_api import strip_reasoning

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class RetrievalAgent:
    """检索智能体"""
    
    def __init__(self, 
                 deepseek_api_key: Optional[str] = None,
                 embedder_name: str = "bge",
                 model_path: Optional[str] = None):
        """
        初始化检索智能体
        
        参数:
            deepseek_api_key: Deepseek API密钥
            embedder_name: 嵌入模型名称 ('bge' 或 'openai')
            model_path: 本地模型路径（当embedder_name='bge'时）
        """
        # 设置API密钥
        self.deepseek_api_key = deepseek_api_key or os.environ.get("DEEPSEEK_API_KEY", "")
        self.embedder_name = embedder_name
        self.model_path = model_path
        
        # 初始化Deepseek客户端
        # 全系统只有本 agent 直接用裸 client.chat.completions.create（其余 agent 走 llm_api.call_deepseek_api，
        # 那里默认带 300s 超时 + 有限重试）。若这里不设 timeout，openai SDK 默认 600s 读超时且自带 2 次自动重试，
        # 网关半开/卡死时 extract_keywords / answer_question / generate_literature_review 会各自静默阻塞
        # 最长 ~600s×3 ≈ 30 分钟，表现为「检索很久不出来、整轮 workflow 卡死在检索阶段」。
        # 与 call_deepseek_api 对齐：显式设 timeout(ARCHE_LLM_TIMEOUT，默认 300s) 与 max_retries，检索必有界。
        self._llm_timeout = float(os.environ.get("ARCHE_LLM_TIMEOUT", "300"))
        self.client = None
        if self.deepseek_api_key:
            try:
                from chemistry_multiagent.utils.llm_headers import api_key_headers

                self.client = openai.OpenAI(
                    api_key=self.deepseek_api_key,
                    base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
                    default_headers=api_key_headers(self.deepseek_api_key),
                    timeout=self._llm_timeout,
                    max_retries=int(os.environ.get("ARCHE_LLM_MAX_RETRIES", "2")),
                )
            except Exception as e:
                logger.warning(f"Deepseek客户端初始化失败: {e}")
        
        # 初始化嵌入模型
        self.embedder = None
        if embedder_name == "bge" and SENTENCE_TRANSFORMERS_AVAILABLE:
            try:
                if model_path and os.path.exists(model_path):
                    self.embedder = SentenceTransformer(model_path)
                else:
                    # 默认模型
                    self.embedder = SentenceTransformer("BAAI/bge-small-en-v1.5")
                logger.info(f"加载嵌入模型: {embedder_name}")
            except Exception as e:
                logger.error(f"加载嵌入模型失败: {e}")
        
        # 索引状态
        self.index = None
        self.documents = []
        self.metadatas = []
        
        logger.info("Retrieval Agent 初始化完成")

    # ==================== LLM 调用（带输出底线 + 空响应守卫） ====================

    def _chat_guarded(self, messages: List[Dict], max_tokens: int, temperature: float = 0.7) -> str:
        """统一的 LLM 调用：抬高输出 token 底线并剥离 <think> 思维链，杜绝"被 <think>
        占满后静默返回空串"。

        - max_tokens 抬到 ARCHE_LLM_MIN_OUTPUT_TOKENS（默认 4096）的下限，避免推理模型
          把预算全花在思维链上、正文被截断成空。
        - 若原始响应非空但剥离 <think> 后无有效答案，则显式抛错（诚实降级，不伪装成结果）。
        """
        floored = max(int(max_tokens), int(os.environ.get("ARCHE_LLM_MIN_OUTPUT_TOKENS", "4096")))
        resp = self.client.chat.completions.create(
            model=os.environ.get("DEEPSEEK_MODEL", "interns2-preview-sft"),
            messages=messages,
            temperature=temperature,
            max_tokens=floored,
            # 每次调用再显式带一次超时（防御性：即便 client 在别处以默认超时创建，单次调用仍有界）。
            timeout=getattr(self, "_llm_timeout", float(os.environ.get("ARCHE_LLM_TIMEOUT", "300"))),
        )
        raw = resp.choices[0].message.content or ""
        answer = strip_reasoning(raw)
        if raw.strip() and not answer:
            raise RuntimeError("retrieval LLM 响应被 <think> 占满、无有效答案")
        return answer

    # ==================== 关键词提取 ====================
    
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def extract_keywords(self, question: str) -> List[str]:
        """
        使用Deepseek提取计算化学相关的关键词
        
        参数:
            question: 科学问题
        
        返回:
            关键词列表
        """
        if not self.client:
            logger.warning("Deepseek客户端未初始化，使用简单关键词提取")
            return self._simple_keyword_extraction(question)
        
        prompt = """
        You are an expert in computational and quantum chemistry. 
        Given the following research question, extract a concise list of domain-specific keywords or short keyword phrases that are directly relevant to computational chemistry, quantum chemistry, and reaction mechanism studies. 

        Guidelines:
        - The **first keyword must be the main research subject**, e.g., asymmetric catalytic reaction mechanisms, reaction type or molecular system, if mentioned.  
        - Then include the key computational/quantum chemical methods and protocols (e.g., DFT, coupled-cluster, transition state optimization).  
        - Include important analysis techniques (e.g., intrinsic reaction coordinate analysis, solvation models).  
        - Restrict to computational chemistry and reaction mechanisms.  
        - Exclude unrelated fields (e.g., machine learning, biology) unless explicitly mentioned.  
        - Provide 5–10 keyword phrases only.
        - Use plain English search terms ONLY. Do NOT use LaTeX, math/chemistry markup (e.g. \\ce{...}, $...$), chemical equations, or reaction arrows (<=>, ->, →). Write species/reactions in words, e.g. "ammonia synthesis from nitrogen and hydrogen", NOT "$\\ce{N2 + 3H2 <=> 2NH3}$".
        - Output must be a valid Python list of strings, without explanations or extra text.

        Question: {question}
        Keywords:
        """
        
        try:
            # 用 replace 而非 .format()：prompt 里含字面花括号示例（\ce{...} 等），
            # .format() 会把它们当占位符报 "Replacement index out of range"。
            text_output = self._chat_guarded(
                messages=[{"role": "user", "content": prompt.replace("{question}", question)}],
                temperature=0.7,
                max_tokens=700,
            )
            logger.info(f"Deepseek关键词提取响应: {text_output[:100]}...")
            
            # 清理响应文本
            if text_output.startswith("```") and text_output.endswith("```"):
                text_output = "\n".join(text_output.split("\n")[1:-1]).strip()
            
            # 尝试解析为Python列表（literal_eval 而非 eval：LLM 输出当代码执行有注入风险）
            try:
                parsed = ast.literal_eval(text_output)
                if isinstance(parsed, list):
                    keywords = self._clean_keywords(parsed)
                    logger.info(f"提取到 {len(keywords)} 个关键词(清洗前 {len(parsed)}): {keywords}")
                    if keywords:
                        return keywords
            except (ValueError, SyntaxError):
                pass

            # 如果解析失败，使用简单分割（同样清洗）
            keywords = self._clean_keywords([k.strip() for k in text_output.split(',') if k.strip()])
            if keywords:
                return keywords
            
            # 最终回退
            return self._simple_keyword_extraction(question)
            
        except Exception as e:
            logger.error(f"关键词提取失败: {e}")
            return self._simple_keyword_extraction(question)
    
    def _simple_keyword_extraction(self, question: str) -> List[str]:
        """简单关键词提取（备用方法）"""
        # 提取名词短语和计算化学术语
        computational_terms = [
            "DFT", "quantum chemistry", "reaction mechanism", "transition state",
            "catalysis", "molecular dynamics", "conformer", "optimization",
            "frequency analysis", "solvation", "basis set", "functional",
            "energy", "barrier", "kinetics", "thermodynamics"
        ]
        
        # 查找问题中的计算化学术语
        found_terms = []
        for term in computational_terms:
            if term.lower() in question.lower():
                found_terms.append(term)
        
        # 如果没找到术语，返回问题中的主要名词
        if not found_terms:
            words = question.split()
            found_terms = words[:5]  # 取前5个词
        
        return self._clean_keywords(found_terms) or ["computational chemistry"]

    def _clean_keywords(self, keywords: List[str]) -> List[str]:
        """清洗关键词：剥 LaTeX/mhchem 标记、化学方程算符/箭头，丢纯符号或字母不足的碎片。

        防止把化学方程式 ``$\\ce{N2 + 3H2 <=> 2NH3}$`` 按空格切成的碎片
        ('$\\ce{N2' / '3H2' / '<=>')当搜索词去打 PubMed —— 必然搜不到，
        且反复重试触发 paperscraper 的 service-limit 限流、拖垮整轮检索。
        """
        cleaned: List[str] = []
        seen = set()
        for kw in keywords:
            if not isinstance(kw, str):
                continue
            s = kw.replace("$", " ")
            s = re.sub(r"\\ce\{[^}]*\}", " ", s)          # \ce{...} 整体
            s = re.sub(r"\\[a-zA-Z]+", " ", s)            # 残余 \命令（\ce 无闭合括号等）
            s = s.replace("{", " ").replace("}", " ")
            s = re.sub(r"<=>|<->|->|=>|⇌|→|↔", " ", s)    # 反应箭头
            s = re.sub(r"\s+", " ", s).strip(" +-=,;")
            # 丢弃：空 / 过短 / 字母太少（纯公式碎片如 '3H2'/'2NH3' 字母数 < 3）
            if len(s) < 3 or len(re.sub(r"[^A-Za-z]", "", s)) < 3:
                continue
            key = s.lower()
            if key in seen:
                continue
            seen.add(key)
            cleaned.append(s)
        return cleaned

    # ==================== 论文检索 ====================
    
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def search_papers(self, keywords: List[str], pdf_dir: str, limit_per_keyword: int = 3) -> List[str]:
        """
        检索并下载相关论文
        
        参数:
            keywords: 关键词列表
            pdf_dir: PDF存储目录
            limit_per_keyword: 每个关键词下载的论文数量
        
        返回:
            下载的PDF文件路径列表
        """
        if not PAPERSCRAPER_AVAILABLE:
            logger.warning("paperscraper不可用，跳过论文下载")
            return []
        
        os.makedirs(pdf_dir, exist_ok=True)
        downloaded_files = []

        # 防御性清洗（无论关键词来自哪条抽取路径，搜索前都过滤掉 LaTeX/公式碎片）
        raw_count = len(keywords)
        keywords = self._clean_keywords(keywords)
        if raw_count != len(keywords):
            logger.info(f"检索关键词清洗：{raw_count} → {len(keywords)}（剔除公式/符号碎片）")
        if not keywords:
            logger.warning("无有效检索关键词（清洗后为空），跳过论文下载")
            return []

        # 论文下载是「尽力而为」的接地素材而非必需：Semantic Scholar 限流(429)+ 付费墙会让每个
        # 关键词的下载耗时不可控，关键词一多就把整轮工作流拖垮(检索吃满超时，假设/执行根本跑不到)。
        # 兜底：只用前 N 个关键词下载，且每个关键词用 daemon 线程 + join 设硬超时；超时即放弃该词、
        # 带着已下到的继续。全部关键词仍用于后续语义检索/嵌入，主检索质量不受影响。env 可调。
        import threading as _threading
        max_dl_kw = max(1, int(os.environ.get("ARCHE_MAX_DOWNLOAD_KEYWORDS", "2")))
        per_kw_timeout = float(os.environ.get("ARCHE_PAPER_DOWNLOAD_TIMEOUT_S", "90"))
        dl_keywords = keywords[:max_dl_kw]
        if len(keywords) > len(dl_keywords):
            logger.info(
                f"论文下载仅用前 {len(dl_keywords)}/{len(keywords)} 个关键词"
                "（其余仅用于语义检索，避免 paperscraper 限流拖垮检索阶段）"
            )

        for keyword in dl_keywords:
            logger.info(f"检索论文: {keyword}")
            holder = {}

            def _download(kw=keyword, sink=holder):
                try:
                    sink["papers"] = paperscraper.search_papers(kw, limit=limit_per_keyword, pdir=pdf_dir)
                except Exception as exc:  # 下载失败不阻断检索
                    sink["error"] = exc

            worker = _threading.Thread(target=_download, daemon=True)
            worker.start()
            worker.join(per_kw_timeout)
            if worker.is_alive():
                logger.warning(
                    f"检索论文 '{keyword}' 超过 {per_kw_timeout}s 硬超时，放弃该词、带着已下到的继续"
                    "（后台 daemon 线程自行结束，不阻塞工作流）"
                )
                continue
            if holder.get("error") is not None:
                logger.error(f"检索关键词 '{keyword}' 失败: {holder['error']}")
                continue
            papers = holder.get("papers")
            if papers:
                downloaded_files.extend(papers)
                logger.info(f"为关键词 '{keyword}' 下载了 {len(papers)} 篇论文")
            time.sleep(1)  # 礼貌延迟
        
        # 清理空PDF文件
        self._remove_empty_pdfs(pdf_dir)

        logger.info(
            f"论文检索完成：实际下载 {len(downloaded_files)} 篇 PDF"
            f"（仅尝试前 {len(dl_keywords)} 个关键词；其余论文因付费墙/无免费 PDF 被跳过，属正常，"
            f"不影响后续基于本地语料 + 摘要的语义检索）。"
        )
        return downloaded_files
    
    def _remove_empty_pdfs(self, folder_path: str):
        """删除空PDF文件"""
        if not os.path.exists(folder_path):
            return
        
        for filename in os.listdir(folder_path):
            if filename.lower().endswith(".pdf"):
                file_path = os.path.join(folder_path, filename)
                try:
                    # 检查文件大小
                    if os.path.getsize(file_path) == 0:
                        logger.info(f"删除空文件: {file_path}")
                        os.remove(file_path)
                        continue
                    
                    # 尝试打开PDF
                    doc = fitz.open(file_path)
                    if doc.page_count == 0:
                        logger.info(f"删除空PDF（无页）: {file_path}")
                        doc.close()
                        os.remove(file_path)
                    else:
                        doc.close()
                        
                except Exception as e:
                    logger.warning(f"检查PDF时出错 {file_path}: {e}")
    
    # ==================== 文本处理 ====================
    
    def _load_pdf_text(self, pdf_path: str) -> List[Tuple[str, int]]:
        """从PDF加载文本（按段落）"""
        try:
            doc = fitz.open(pdf_path)
            paragraphs = []
            
            for page_num, page in enumerate(doc, start=1):
                text = page.get_text("text")
                if text.strip():
                    # 按空行分段
                    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
                    for para in paras:
                        paragraphs.append((para, page_num))
            
            doc.close()
            return paragraphs
            
        except Exception as e:
            logger.error(f"读取PDF失败 {pdf_path}: {e}")
            return []
    
    def _chunk_text(self, text: str, chunk_size: int = 500, overlap: int = 50) -> List[str]:
        """文本分块"""
        words = text.split()
        chunks = []
        start = 0
        
        while start < len(words):
            end = min(start + chunk_size, len(words))
            chunk = " ".join(words[start:end])
            chunks.append(chunk)
            start += chunk_size - overlap
        
        return chunks
    
    # ==================== 向量嵌入 ====================
    
    def _get_embeddings(self, texts: List[str]) -> np.ndarray:
        """获取文本嵌入向量"""
        if self.embedder and self.embedder_name == "bge":
            try:
                vecs = self.embedder.encode(texts, normalize_embeddings=True)
                return vecs.astype("float32")
            except Exception as e:
                logger.error(f"嵌入生成失败: {e}")
        
        # 备用：简单词频向量（仅用于测试）
        logger.warning("使用简单词频向量（质量较低）")
        vocab = set()
        for text in texts:
            vocab.update(text.lower().split())
        
        vocab = list(vocab)
        vecs = np.zeros((len(texts), len(vocab)), dtype="float32")
        
        for i, text in enumerate(texts):
            words = text.lower().split()
            for word in words:
                if word in vocab:
                    vecs[i, vocab.index(word)] += 1
        
        # 归一化
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        norms[norms == 0] = 1
        vecs = vecs / norms
        
        return vecs
    
    # ==================== 索引构建 ====================
    
    def build_index(self, pdf_dir: str, index_dir: str):
        """
        构建论文索引
        
        参数:
            pdf_dir: PDF目录
            index_dir: 索引存储目录
        """
        os.makedirs(index_dir, exist_ok=True)
        
        self.documents = []
        self.metadatas = []
        
        # 加载所有PDF文本
        for filename in os.listdir(pdf_dir):
            if filename.lower().endswith(".pdf"):
                pdf_path = os.path.join(pdf_dir, filename)
                paragraphs = self._load_pdf_text(pdf_path)
                
                for text, page_num in paragraphs:
                    chunks = self._chunk_text(text)
                    
                    for chunk in chunks:
                        # 检测章节类型
                        section_type = "methods" if re.search(
                            r"computational|method|calculation|theory", 
                            chunk, re.I
                        ) else "general"
                        
                        self.documents.append(chunk)
                        self.metadatas.append({
                            "source": filename,
                            "page": page_num,
                            "section": section_type
                        })
        
        if not self.documents:
            logger.warning("没有找到可索引的文档")
            return
        
        # 生成嵌入向量
        logger.info(f"为 {len(self.documents)} 个文档块生成嵌入...")
        vectors = self._get_embeddings(self.documents)
        
        # 构建FAISS索引
        dim = vectors.shape[1]
        self.index = faiss.IndexFlatIP(dim)
        self.index.add(vectors)
        
        # 保存索引
        faiss.write_index(self.index, os.path.join(index_dir, "index.faiss"))
        
        with open(os.path.join(index_dir, "docs.json"), "w", encoding="utf-8") as f:
            json.dump({
                "documents": self.documents,
                "metadatas": self.metadatas
            }, f, indent=2, ensure_ascii=False)
        
        logger.info(f"索引构建完成: {len(self.documents)} 个文档块")
    
    def load_index(self, index_dir: str):
        """加载现有索引"""
        try:
            # 加载FAISS索引
            index_path = os.path.join(index_dir, "index.faiss")
            if os.path.exists(index_path):
                self.index = faiss.read_index(index_path)
            else:
                logger.warning(f"索引文件不存在: {index_path}")
                return False
            
            # 加载文档元数据
            docs_path = os.path.join(index_dir, "docs.json")
            if os.path.exists(docs_path):
                with open(docs_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.documents = data["documents"]
                    self.metadatas = data["metadatas"]
            else:
                logger.warning(f"文档文件不存在: {docs_path}")
                return False
            
            logger.info(f"索引加载成功: {len(self.documents)} 个文档块")
            return True
            
        except Exception as e:
            logger.error(f"加载索引失败: {e}")
            return False
    
    # ==================== 检索查询 ====================
    
    def search(self, query: str, top_k: int = 5) -> List[Dict]:
        """
        检索相关文档
        
        参数:
            query: 查询文本
            top_k: 返回结果数量
        
        返回:
            检索结果列表
        """
        if self.index is None or not self.documents:
            logger.warning("索引未加载，无法检索")
            return []
        
        # 获取查询向量
        query_vec = self._get_embeddings([query])
        
        # 搜索
        scores, indices = self.index.search(query_vec, top_k)
        
        results = []
        for i, idx in enumerate(indices[0]):
            if idx < len(self.documents):
                results.append({
                    "text": self.documents[idx],
                    "score": float(scores[0][i]),
                    "metadata": self.metadatas[idx]
                })
        
        # Methods部分优先排序
        results.sort(key=lambda x: 0 if x["metadata"]["section"] == "methods" else 1)
        
        return results
    
    # ==================== 问答系统 ====================
    
    def answer_question(self, question: str, top_k: int = 7) -> str:
        """
        基于检索结果回答问题
        
        参数:
            question: 问题
            top_k: 使用的检索结果数量
        
        返回:
            答案文本
        """
        if not self.client:
            return "Deepseek客户端未初始化，无法回答问题"
        
        # 检索相关文档
        results = self.search(question, top_k)
        
        if not results:
            return "未找到相关文献"
        
        # 构建上下文
        context_parts = []
        for r in results:
            md = r["metadata"]
            context_parts.append(
                f"[论文: {md['source']} | 章节: {md['section']} | 页码: {md['page']}] {r['text']}"
            )
        
        context_text = "\n\n".join(context_parts)
        
        # 系统提示
        system_prompt = """
        You are a precise academic assistant specialized in computational chemistry.
        Answer the user's question primarily based on the provided document excerpts.
        For each factual statement taken from the documents, indicate its source in the format [filename p.page_number].
        Provide a clear, concise, and well-structured answer suitable for academic use.
        """
        
        user_prompt = f"Question: {question}\n\nContext:\n{context_text}"
        
        try:
            answer = self._chat_guarded(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.7,
                max_tokens=1000,
            )
            return answer

        except Exception as e:
            logger.error(f"回答问题失败: {e}")
            return f"回答问题失败: {e}"
    
    def generate_literature_review(self, research_topic: str, top_k: int = 7) -> str:
        """
        生成文献综述
        
        参数:
            research_topic: 研究主题
            top_k: 使用的检索结果数量
        
        返回:
            文献综述文本
        """
        if not self.client:
            return "Deepseek客户端未初始化，无法生成文献综述"
        
        # 检索相关文档
        results = self.search(research_topic, top_k)
        
        if not results:
            return "未找到相关文献"
        
        # 构建上下文
        context_parts = []
        for r in results:
            md = r["metadata"]
            context_parts.append(
                f"[论文: {md['source']} | 章节: {md['section']} | 页码: {md['page']}] {r['text']}"
            )
        
        context_text = "\n\n".join(context_parts)
        
        # 系统提示 - 专注于计算化学
        system_prompt = """
        You are an expert computational chemist, highly experienced in quantum chemistry, reaction mechanism exploration, and computational methodology development. 
        Your task is to read the retrieved literature excerpts and generate a concise but comprehensive literature review focused on computational chemistry approaches. 
        Emphasize methods, tools, and protocols relevant for performing calculations with Gaussian software and other Python-based computational chemistry packages. 
        The review should highlight computational strategies for studying molecular properties, reaction mechanisms, transition state searches, conformational sampling, solvation models, basis set and functional choices, and relevant methodological benchmarks. 
        Always remain within the scope of computational chemistry. Do not include wet-lab synthesis, biology, or experimental assays.
        """
        
        user_prompt = f"""
        Research Topic: {research_topic}
        
        Literature Excerpts:
        {context_text}
        
        Please write a structured literature review based on these excerpts.
        Focus on computational chemistry methods, tools, protocols, and their applications.
        """
        
        try:
            review = self._chat_guarded(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.7,
                max_tokens=1500,
            )
            return review

        except Exception as e:
            logger.error(f"生成文献综述失败: {e}")
            return f"生成文献综述失败: {e}"
    
    # ==================== 结构化检索输出 ====================
    
    def _normalize_retrieval_result(self,
                                  question: str,
                                  keywords: List[str],
                                  retrieved_papers: List[str],
                                  answer: str,
                                  literature_review: str,
                                  index_built: bool) -> Dict[str, Any]:
        """
        规范化检索结果为结构化输出
        
        参数:
            question: 科学问题
            keywords: 关键词列表
            retrieved_papers: 检索到的论文列表
            answer: 答案文本
            literature_review: 文献综述
            index_built: 索引是否构建成功
        
        返回:
            结构化检索结果
        """
        # 提取机制线索
        mechanistic_clues = self._extract_mechanistic_clues(literature_review)
        
        # 识别局限性
        limitations = self._identify_limitations(literature_review, answer)
        
        # 结构化结果
        structured_result = {
            "question": question,
            "keywords": keywords,
            "retrieved_papers": retrieved_papers,
            "downloaded_papers": retrieved_papers,  # 向后兼容
            "literature_review": literature_review,
            "answer": answer,
            "index_built": index_built,
            "mechanistic_clues": mechanistic_clues,
            "limitations": limitations,
            "retrieval_timestamp": time.time(),
            "structured_version": "1.0"
        }
        
        return structured_result
    
    def _extract_mechanistic_clues(self, literature_review: str) -> List[str]:
        """
        从文献综述中提取机制线索
        
        参数:
            literature_review: 文献综述文本
        
        返回:
            机制线索列表
        """
        clues = []
        
        if not literature_review:
            return clues
        
        # 查找机制相关的关键词
        mechanistic_patterns = [
            r"transition state(?:s)? (?:for|of|in) ([^.]*?)[.]",
            r"reaction mechanism (?:for|of|involving) ([^.]*?)[.]",
            r"catalytic cycle (?:for|of) ([^.]*?)[.]",
            r"rate-determining step(?:s)? (?:for|of) ([^.]*?)[.]",
            r"key intermediate(?:s)? (?:in|for) ([^.]*?)[.]",
            r"activation barrier(?:s)? (?:for|of) ([^.]*?)[.]",
            r"photochemical excitation (?:of|for) ([^.]*?)[.]",
            r"conformational change(?:s)? (?:in|for) ([^.]*?)[.]",
            r"solvent effect(?:s)? (?:on|in) ([^.]*?)[.]",
            r"stereoselectivity (?:of|in) ([^.]*?)[.]"
        ]
        
        for pattern in mechanistic_patterns:
            matches = re.findall(pattern, literature_review, re.IGNORECASE)
            for match in matches:
                clue = match.strip()
                if clue and clue not in clues:
                    clues.append(clue)
        
        # 如果没有找到模式，尝试提取包含关键词的句子
        if not clues:
            sentences = re.split(r"[.!?]", literature_review)
            for sentence in sentences:
                sentence = sentence.strip()
                if any(keyword in sentence.lower() for keyword in [
                    "mechanism", "transition state", "catalytic", "barrier",
                    "intermediate", "reaction pathway", "ts", "irc"
                ]):
                    if sentence and len(sentence) < 200:
                        clues.append(sentence)
        
        return clues[:10]  # 最多10个线索
    
    def _identify_limitations(self, literature_review: str, answer: str) -> List[str]:
        """
        识别文献综述和答案中的局限性
        
        参数:
            literature_review: 文献综述文本
            answer: 答案文本
        
        返回:
            局限性列表
        """
        limitations = []
        
        combined_text = (literature_review + " " + answer).lower()
        
        # 查找局限性相关的关键词
        limitation_keywords = [
            "limitation", "drawback", "challenge", "difficulty",
            "restriction", "constraint", "shortcoming", "weakness",
            "need further", "requires more", "not fully", "lack of",
            "limited to", "cannot", "unable to", "insufficient"
        ]
        
        # 查找包含这些关键词的句子
        sentences = re.split(r"[.!?]", combined_text)
        for sentence in sentences:
            sentence = sentence.strip()
            if any(keyword in sentence for keyword in limitation_keywords):
                if sentence and len(sentence) < 200:
                    limitations.append(sentence)
        
        # 如果没有找到，添加默认局限性
        if not limitations:
            limitations = [
                "Limited to available literature in the indexed papers",
                "Computational methods may have inherent approximations",
                "Experimental validation may be required for confirmation"
            ]
        
        return limitations[:5]  # 最多5个局限性

    def infer_molecular_metadata(self, question: str, literature_review: str = "") -> Dict[str, Any]:
        """从问题与综述中保守提取分子/体系元数据。"""
        text = f"{question} {literature_review}"

        # 简单元素符号提取（轻量启发式）
        symbols = re.findall(r"\b([A-Z][a-z]?)\b", text)
        non_elements = {"DFT", "TS", "IRC", "PCM", "SMD", "TD", "TDDFT", "SCF", "RMS", "UV", "IR", "NMR"}
        candidate_elements = []
        for s in symbols:
            if s.upper() in non_elements:
                continue
            if s not in candidate_elements:
                candidate_elements.append(s)

        lower = text.lower()
        solvent = None
        solvent_patterns = [
            r"in\s+(water|methanol|ethanol|acetonitrile|dmso|toluene|chloroform|thf|hexane)",
            r"solvent\s*[:=]\s*([a-zA-Z0-9\-_/]+)",
        ]
        for p in solvent_patterns:
            m = re.search(p, lower)
            if m:
                solvent = m.group(1)
                break

        temperature = None
        t_match = re.search(r"(\d{2,4})\s*(k|kelvin|°c|celsius)", lower)
        if t_match:
            temperature = f"{t_match.group(1)} {t_match.group(2)}"

        charge = None
        c_match = re.search(r"charge\s*[:=]?\s*([+-]?\d+)", lower)
        if c_match:
            try:
                charge = int(c_match.group(1))
            except Exception:
                charge = None

        multiplicity = None
        m_match = re.search(r"multiplicity\s*[:=]?\s*(\d+)", lower)
        if m_match:
            try:
                multiplicity = int(m_match.group(1))
            except Exception:
                multiplicity = None

        species_roles = []
        for role in ["reactant", "intermediate", "transition state", "product", "catalyst", "conformer"]:
            if role in lower:
                species_roles.append(role)

        return {
            "solvent": solvent,
            "temperature": temperature,
            "charge": charge,
            "multiplicity": multiplicity,
            "species_roles": species_roles,
            "candidate_elements": candidate_elements,
        }

    def extract_chemistry_context(self, question: str, literature_review: str = "", answer: str = "") -> Dict[str, Any]:
        """提取可供下游复用的轻量化学上下文（可选且允许部分缺失）。"""
        q = (question or "").lower()
        combined = f"{question} {literature_review} {answer}".lower()
        molecular_meta = self.infer_molecular_metadata(question, literature_review)

        reaction_type = "unknown"
        if any(k in combined for k in ["substitution", "sn1", "sn2"]):
            reaction_type = "substitution"
        elif any(k in combined for k in ["addition", "cycloaddition"]):
            reaction_type = "addition"
        elif any(k in combined for k in ["elimination", "e1", "e2"]):
            reaction_type = "elimination"
        elif any(k in combined for k in ["catal", "catalytic"]):
            reaction_type = "catalytic_transformation"
        elif any(k in combined for k in ["photochemical", "excited", "excitation", "td-dft", "tddft"]):
            reaction_type = "photochemical"

        mechanistic_goal = "unknown"
        if any(k in q for k in ["mechanism", "pathway", "how"]):
            mechanistic_goal = "mechanism_elucidation"
        elif any(k in q for k in ["barrier", "activation", "kinetic"]):
            mechanistic_goal = "barrier_quantification"
        elif any(k in q for k in ["selectivity", "stereo", "regio"]):
            mechanistic_goal = "selectivity_rationalization"

        needs_ts = any(k in combined for k in ["transition state", " ts ", "barrier", "activation"])
        needs_irc = any(k in combined for k in ["irc", "intrinsic reaction coordinate", "pathway connectivity"])
        needs_excited_state = any(k in combined for k in ["photochemical", "excited", "excitation", "td-dft", "tddft"])

        suspected_job_types = []
        if any(k in combined for k in ["optimization", "geometry", "opt"]):
            suspected_job_types.append("opt")
        if any(k in combined for k in ["frequency", "vibration", "imaginary"]):
            suspected_job_types.append("freq")
        if needs_ts:
            suspected_job_types.append("ts")
        if needs_irc:
            suspected_job_types.append("irc")
        if any(k in combined for k in ["single point", "single-point", "refinement", "energy"]):
            suspected_job_types.append("sp")
        if needs_excited_state:
            suspected_job_types.append("excited_state")
        if not suspected_job_types:
            suspected_job_types = ["opt", "sp"]

        evidence_gaps = []
        if "no relevant literature found" in combined or "未找到相关文献" in combined:
            evidence_gaps.append("limited_relevant_literature")
        if needs_ts and "frequency" not in combined and "freq" not in combined:
            evidence_gaps.append("ts_validation_details_missing")
        if needs_irc and "irc" not in combined:
            evidence_gaps.append("irc_connectivity_evidence_missing")

        ctx = {
            "reaction_type": reaction_type,
            "mechanistic_goal": mechanistic_goal,
            "suspected_job_types": sorted(set(suspected_job_types)),
            "solvent": molecular_meta.get("solvent"),
            "temperature": molecular_meta.get("temperature"),
            "charge": molecular_meta.get("charge"),
            "multiplicity": molecular_meta.get("multiplicity"),
            "species_roles": molecular_meta.get("species_roles", []),
            "candidate_elements": molecular_meta.get("candidate_elements", []),
            "needs_ts": bool(needs_ts),
            "needs_irc": bool(needs_irc),
            "needs_excited_state": bool(needs_excited_state),
            "evidence_gaps": evidence_gaps,
        }
        return ctx

    def build_planning_context(self, retrieval_result: Dict[str, Any]) -> Dict[str, Any]:
        """从检索结果构建轻量规划上下文。"""
        retrieval_result = retrieval_result or {}
        chemistry_context = retrieval_result.get("chemistry_context", {}) if isinstance(retrieval_result.get("chemistry_context", {}), dict) else {}
        return {
            "question": retrieval_result.get("question", ""),
            "keywords": retrieval_result.get("keywords", []),
            "mechanistic_clues": retrieval_result.get("mechanistic_clues", []),
            "limitations": retrieval_result.get("limitations", []),
            "chemistry_context": chemistry_context,
            "suspected_job_types": chemistry_context.get("suspected_job_types", []),
            "needs_ts": chemistry_context.get("needs_ts"),
            "needs_irc": chemistry_context.get("needs_irc"),
            "needs_excited_state": chemistry_context.get("needs_excited_state"),
        }
    
    # ==================== 主工作流程 ====================
    
    def process_question(self, 
                        question: str, 
                        pdf_dir: str = "papers",
                        index_dir: str = "index",
                        search_papers: bool = True) -> Dict[str, Any]:
        """
        处理完整检索流程，返回结构化输出
        
        参数:
            question: 科学问题
            pdf_dir: PDF存储目录
            index_dir: 索引存储目录
            search_papers: 是否检索论文
        
        返回:
            结构化处理结果字典
        """
        logger.info(f"开始处理问题: {question[:100]}...")
        
        # 基础结果字段
        question_text = question
        keywords_list = []
        retrieved_papers_list = []
        answer_text = ""
        literature_review_text = ""
        index_built_flag = False
        caught_error = None
        
        try:
            # 1. 提取关键词
            keywords_list = self.extract_keywords(question)
            logger.info(f"提取关键词: {keywords_list}")
            
            # 2. 检索论文（如果需要）
            if search_papers and PAPERSCRAPER_AVAILABLE:
                retrieved_papers_list = self.search_papers(keywords_list, pdf_dir)
            
            # 3. 构建索引
            self.build_index(pdf_dir, index_dir)
            index_built_flag = len(self.documents) > 0
            
            # 4. 回答问题
            if self.documents:
                answer_text = self.answer_question(question)
            
            # 5. 生成文献综述（为后续Agent提供背景）
            if self.documents:
                literature_review_text = self.generate_literature_review(question)
            
            logger.info("检索流程完成")
            
        except Exception as e:
            caught_error = str(e)
            logger.error(f"处理流程失败: {caught_error}")
            # 即使失败也返回结构化结果
        
        # 规范化结果为结构化输出
        structured_result = self._normalize_retrieval_result(
            question=question_text,
            keywords=keywords_list,
            retrieved_papers=retrieved_papers_list,
            answer=answer_text,
            literature_review=literature_review_text,
            index_built=index_built_flag
        )

        chemistry_context = self.extract_chemistry_context(
            question=question_text,
            literature_review=literature_review_text,
            answer=answer_text,
        )
        structured_result["chemistry_context"] = chemistry_context
        structured_result["planning_context"] = self.build_planning_context(structured_result)
        
        # 如果有错误，添加错误字段（保持向后兼容）
        if caught_error is not None:
            structured_result["error"] = caught_error
        
        return structured_result
    
    # ==================== 有针对性的后续检索 ====================
    
    def retrieve_followup_evidence(self,
                                 reflection_result: Dict[str, Any],
                                 original_question: str,
                                 prior_review: Optional[str] = None,
                                 pdf_dir: str = "papers",
                                 index_dir: str = "index") -> Dict[str, Any]:
        """
        执行有针对性的后续检索以获取额外证据
        
        参数:
            reflection_result: 反思结果（包含决策、建议、证据需求）
            original_question: 原始科学问题
            prior_review: 先前文献综述（可选）
            pdf_dir: PDF存储目录
            index_dir: 索引存储目录
        
        返回:
            结构化后续检索结果
        """
        logger.info("执行有针对性的后续检索")
        
        # 从反思结果中提取证据需求
        evidence_needs = self._extract_evidence_needs(reflection_result, original_question, prior_review)
        
        # 构建针对性查询
        targeted_queries = self._build_targeted_queries(evidence_needs)
        
        # 执行针对性检索
        followup_results = []
        for i, query in enumerate(targeted_queries):
            logger.info(f"针对性检索 {i+1}/{len(targeted_queries)}: {query[:100]}...")
            
            try:
                # 使用现有检索流程
                search_result = self.search(query, top_k=5)
                
                if search_result:
                    # 从检索结果中提取答案
                    answer = self._generate_followup_answer(query, search_result, evidence_needs[i])
                    
                    followup_results.append({
                        "query": query,
                        "evidence_need": evidence_needs[i],
                        "retrieved_documents": len(search_result),
                        "answer": answer,
                        "relevant_excerpts": [r["text"][:300] for r in search_result[:3]]  # 前3个片段
                    })
                else:
                    followup_results.append({
                        "query": query,
                        "evidence_need": evidence_needs[i],
                        "retrieved_documents": 0,
                        "answer": "No relevant literature found",
                        "relevant_excerpts": []
                    })
                    
            except Exception as e:
                logger.error(f"针对性检索失败: {e}")
                followup_results.append({
                    "query": query,
                    "evidence_need": evidence_needs[i],
                    "error": str(e)
                })
        
        # 生成后续文献综述
        followup_review = self._generate_followup_review(followup_results, original_question, prior_review)
        
        # 规范化结果
        structured_followup = {
            "original_question": original_question,
            "reflection_summary": reflection_result.get("decision", "unknown") + ": " + reflection_result.get("reasoning", "")[:200],
            "evidence_needs": evidence_needs,
            "targeted_queries": targeted_queries,
            "followup_results": followup_results,
            "followup_review": followup_review,
            "followup_mechanistic_clues": self._extract_mechanistic_clues(followup_review),
            "followup_limitations": self._identify_limitations(followup_review, ""),
            "retrieval_timestamp": time.time(),
            "followup_type": "targeted_evidence"
        }

        followup_answers = "\n".join([
            str(item.get("answer", ""))
            for item in followup_results if isinstance(item, dict)
        ])
        structured_followup["chemistry_context"] = self.extract_chemistry_context(
            question=original_question,
            literature_review=followup_review,
            answer=followup_answers,
        )
        structured_followup["planning_context"] = self.build_planning_context({
            "question": original_question,
            "keywords": targeted_queries,
            "mechanistic_clues": structured_followup.get("followup_mechanistic_clues", []),
            "limitations": structured_followup.get("followup_limitations", []),
            "chemistry_context": structured_followup.get("chemistry_context", {}),
        })
        
        logger.info(f"后续检索完成: {len(followup_results)} 个查询, {sum(r.get('retrieved_documents', 0) for r in followup_results)} 个文档")
        
        return structured_followup
    
    def _extract_evidence_needs(self, 
                               reflection_result: Dict[str, Any], 
                               original_question: str,
                               prior_review: Optional[str] = None) -> List[str]:
        """
        从反思结果中提取证据需求（优先使用当前结构化reflection schema，兼容旧字段）。
        """
        evidence_needs: List[str] = []

        def add_need(need: str):
            need = str(need).strip()
            if need and need not in evidence_needs:
                evidence_needs.append(need)

        reflection_result = reflection_result or {}
        decision = str(reflection_result.get("decision", "")).lower().strip()

        identified_problems = reflection_result.get("identified_problems", []) or []
        workflow_revision_instructions = reflection_result.get("workflow_revision_instructions", []) or []
        hypothesis_revision_instructions = reflection_result.get("hypothesis_revision_instructions", []) or []
        recommended_actions = reflection_result.get("recommended_actions", []) or []
        evidence_summary = reflection_result.get("evidence_summary", {}) or {}

        # 旧字段fallback
        revision_suggestions = reflection_result.get("revision_suggestions", []) or []

        # 1) 结构化问题码/类型驱动
        for problem in identified_problems:
            if not isinstance(problem, dict):
                continue
            code = str(problem.get("code", "")).lower()
            ptype = str(problem.get("type", "")).lower()
            message = str(problem.get("message", "")).lower()
            signals = " ".join([code, ptype, message])

            if any(k in signals for k in ["barrier_contradiction", "mechanis", "hypothesis_contradiction"]):
                add_need(f"mechanistic precedent for {original_question[:50]}")
                add_need(f"activation barrier literature for {original_question[:50]}")

            if any(k in signals for k in ["ts_invalid", "ts_validation_failed", "missing_frequency_for_ts_validation"]):
                add_need(f"transition-state family support and frequency-character validation for {original_question[:50]}")

            if any(k in signals for k in ["photochemical_infeasible", "photochemical"]):
                add_need(f"photochemical excitation feasibility for {original_question[:50]}")

            if any(k in signals for k in ["irc_connectivity_mismatch", "irc_not_verified", "missing_irc_for_ts_pathway", "irc_failure"]):
                add_need(f"missing pathway-validation evidence (IRC/pathway connectivity) for {original_question[:50]}")

            if "catalytic" in signals:
                add_need(f"catalytic cycle comparison for {original_question[:50]}")

        # 2) 从结构化指令/推荐动作提取
        instruction_texts: List[str] = []
        for bucket in [workflow_revision_instructions, hypothesis_revision_instructions, recommended_actions, revision_suggestions]:
            if isinstance(bucket, list):
                instruction_texts.extend([str(x) for x in bucket if str(x).strip()])

        for text in instruction_texts:
            low = text.lower()
            if any(k in low for k in ["mechanis", "precedent", "pathway", "reaction pathway"]):
                add_need(f"mechanistic precedent for {original_question[:50]}")
            if any(k in low for k in ["transition state", " ts", "frequency", "imaginary"]):
                add_need(f"transition-state family support and frequency-character validation for {original_question[:50]}")
            if "photochemical" in low or "excitation" in low:
                add_need(f"photochemical excitation feasibility for {original_question[:50]}")
            if "catalytic" in low or "catalyst" in low:
                add_need(f"catalytic cycle comparison for {original_question[:50]}")
            if "irc" in low or "connectivity" in low:
                add_need(f"missing pathway-validation evidence (IRC/pathway connectivity) for {original_question[:50]}")
            if "barrier" in low or "activation" in low:
                add_need(f"activation barrier literature for {original_question[:50]}")

        # 3) 从evidence_summary补充
        if isinstance(evidence_summary, dict):
            n_imag = evidence_summary.get("n_imag_freq")
            if isinstance(n_imag, int) and n_imag != 1:
                add_need(f"transition-state family support and frequency-character validation for {original_question[:50]}")

            if evidence_summary.get("irc_verified") is False:
                add_need(f"missing pathway-validation evidence (IRC/pathway connectivity) for {original_question[:50]}")

            workflow_outcome = str(evidence_summary.get("workflow_outcome", "")).lower()
            if workflow_outcome in {"failed", "partially_supported"}:
                add_need(f"mechanistic precedent for {original_question[:50]}")

            error_categories = evidence_summary.get("error_categories", [])
            if isinstance(error_categories, list) and any(str(c).lower() in {"ts_invalid", "irc_failure"} for c in error_categories):
                add_need(f"missing pathway-validation evidence (IRC/pathway connectivity) for {original_question[:50]}")

        # 4) 基于决策的保守补充
        if decision == "revise_hypothesis":
            add_need(f"mechanistic precedent for {original_question[:50]}")
        elif decision == "revise_workflow":
            add_need(f"result-validation and workflow-protocol evidence for {original_question[:50]}")

        # 如果没有明确的证据需求，基于原始问题生成
        if not evidence_needs:
            evidence_needs = [
                f"computational studies of {original_question[:50]}",
                f"transition state search methods for {original_question[:50]}",
                f"literature support for reaction mechanisms similar to {original_question[:50]}"
            ]

        return evidence_needs[:5]  # 最多5个证据需求

    def _build_targeted_queries(self, evidence_needs: List[str]) -> List[str]:
        """
        根据证据需求构建针对性查询
        """
        queries = []
        
        query_templates = {
            "mechanistic": "computational study transition state mechanism {topic}",
            "photochemical": "photochemical excitation TD-DFT calculation {topic}",
            "catalytic": "catalytic cycle DFT calculation {topic}",
            "barrier": "activation barrier DFT calculation {topic}",
            "general": "computational chemistry {topic}"
        }
        
        for need in evidence_needs:
            need_lower = need.lower()
            
            # 确定查询类型
            if any(keyword in need_lower for keyword in ["mechanis", "ts", "transition state"]):
                query_type = "mechanistic"
            elif "photochemical" in need_lower:
                query_type = "photochemical"
            elif "catalytic" in need_lower:
                query_type = "catalytic"
            elif "barrier" in need_lower or "activation" in need_lower:
                query_type = "barrier"
            else:
                query_type = "general"
            
            # 从需求中提取主题
            topic = need
            # 移除常见前缀
            for prefix in ["mechanistic precedent for", "photochemical excitation feasibility for", 
                          "catalytic cycle examples for", "activation barrier literature for", 
                          "computational studies of", "transition state search methods for", 
                          "literature support for reaction mechanisms similar to"]:
                if need.lower().startswith(prefix.lower()):
                    topic = need[len(prefix):].strip()
                    break
            
            # 构建查询
            template = query_templates.get(query_type, query_templates["general"])
            query = template.format(topic=topic)
            queries.append(query)
        
        return queries
    
    def _generate_followup_answer(self, 
                                 query: str, 
                                 search_results: List[Dict],
                                 evidence_need: str) -> str:
        """
        为后续检索生成针对性答案
        """
        if not self.client:
            return "Deepseek客户端未初始化"
        
        if not search_results:
            return "No relevant literature found for this query."
        
        # 构建上下文
        context_parts = []
        for r in search_results:
            md = r["metadata"]
            context_parts.append(
                f"[Source: {md['source']}] {r['text']}"
            )
        
        context_text = "\n\n".join(context_parts)
        
        # 系统提示 - 专注于证据提取
        system_prompt = """
        You are an expert computational chemist reviewing literature for specific evidence.
        Based on the provided document excerpts, provide a concise answer addressing the evidence need.
        Focus on extracting factual information, computational methods used, key findings, and limitations.
        Be precise and cite sources where possible.
        """
        
        user_prompt = f"""
        Evidence Need: {evidence_need}
        
        Specific Query: {query}
        
        Literature Excerpts:
        {context_text}
        
        Please provide a concise answer addressing the evidence need based on the literature excerpts.
        """
        
        try:
            answer = self._chat_guarded(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.7,
                max_tokens=800,
            )
            return answer

        except Exception as e:
            logger.error(f"生成后续答案失败: {e}")
            return f"Failed to generate answer: {e}"
    
    def _generate_followup_review(self, 
                                 followup_results: List[Dict],
                                 original_question: str,
                                 prior_review: Optional[str] = None) -> str:
        """
        生成后续文献综述
        """
        if not self.client:
            return "Deepseek客户端未初始化"
        
        # 收集所有答案和证据
        evidence_summaries = []
        for i, result in enumerate(followup_results):
            if "answer" in result and result["answer"] and "No relevant literature" not in result["answer"]:
                evidence_summaries.append(
                    f"Evidence Need {i+1}: {result.get('evidence_need', 'Unknown')}\n"
                    f"Query: {result.get('query', 'Unknown')}\n"
                    f"Findings: {result['answer']}\n"
                    f"Documents Retrieved: {result.get('retrieved_documents', 0)}"
                )
        
        if not evidence_summaries:
            return "No new evidence found in follow-up retrieval."
        
        evidence_text = "\n\n".join(evidence_summaries)
        
        # 系统提示
        system_prompt = """
        You are a computational chemistry expert synthesizing follow-up literature evidence.
        Based on the evidence summaries from targeted retrieval, write a concise literature review update.
        Focus on how the new evidence relates to the original research question.
        Highlight new mechanistic insights, methodological approaches, or limitations discovered.
        Structure your response as: Introduction, New Evidence Summary, Implications, Limitations.
        """
        
        user_prompt = f"""
        Original Research Question: {original_question}
        
        Prior Literature Review (if any): {prior_review[:1000] if prior_review else "Not provided"}
        
        New Evidence from Follow-up Retrieval:
        {evidence_text}
        
        Please write a concise literature review update based on the new evidence.
        """
        
        try:
            review = self._chat_guarded(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.7,
                max_tokens=1200,
            )
            return review

        except Exception as e:
            logger.error(f"生成后续综述失败: {e}")
            return f"Failed to generate follow-up review: {e}"


# ==================== 工具函数（兼容旧接口） ====================

def PaperSearchQA(pdf_dir: str, index_dir: str, question: str, 
                 embedder_name: str = "bge", search_papers: bool = True) -> str:
    """
    兼容旧接口的函数
    
    参数:
        pdf_dir: PDF目录
        index_dir: 索引目录
        question: 问题
        embedder_name: 嵌入模型名称
        search_papers: 是否检索论文
    
    返回:
        答案文本
    """
    agent = RetrievalAgent(embedder_name=embedder_name)
    result = agent.process_question(question, pdf_dir, index_dir, search_papers)
    return result.get("answer", "")


def summary(pdf_dir: str, index_dir: str, question: str, embedder_name: str = "bge") -> str:
    """
    兼容旧接口的函数 - 生成文献综述
    
    参数:
        pdf_dir: PDF目录
        index_dir: 索引目录
        question: 问题
        embedder_name: 嵌入模型名称
    
    返回:
        文献综述文本
    """
    agent = RetrievalAgent(embedder_name=embedder_name)
    agent.build_index(pdf_dir, index_dir)
    review = agent.generate_literature_review(question)
    return review


# ==================== 主函数 ====================

def main():
    """测试函数"""
    import argparse
    
    parser = argparse.ArgumentParser(description="Retrieval Agent - 检索智能体")
    parser.add_argument("--question", "-q", required=True, help="科学问题")
    parser.add_argument("--pdf-dir", default="papers", help="PDF存储目录")
    parser.add_argument("--index-dir", default="index", help="索引存储目录")
    parser.add_argument("--api-key", help="Deepseek API密钥")
    parser.add_argument("--no-search", action="store_true", help="跳过论文检索")
    
    args = parser.parse_args()
    
    # 创建Agent
    agent = RetrievalAgent(deepseek_api_key=args.api_key)
    
    # 处理问题
    result = agent.process_question(
        question=args.question,
        pdf_dir=args.pdf_dir,
        index_dir=args.index_dir,
        search_papers=not args.no_search
    )
    
    # 输出结果
    print("\n" + "="*60)
    print("检索结果")
    print("="*60)
    
    print(f"\n📋 问题: {result['question']}")
    print(f"\n🔑 关键词: {result.get('keywords', [])}")
    
    papers = result.get('retrieved_papers', result.get('downloaded_papers', []))
    if papers:
        print(f"\n📄 检索论文: {len(papers)} 篇")
    
    if result.get('answer'):
        print(f"\n🤖 答案:\n{result['answer']}")
    
    if result.get('literature_review'):
        print(f"\n📚 文献综述:\n{result['literature_review']}")
    
    if result.get('error'):
        print(f"\n❌ 错误: {result['error']}")


if __name__ == "__main__":
    main()
