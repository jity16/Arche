import os
import sys
import json

from path_utils import TESTS_DIR, add_src_to_path

add_src_to_path()

from chemistry_multiagent.agents.retrieval_agent import RetrievalAgent
from chemistry_multiagent.agents.hypothesis_agent import HypothesisAgent

WORK_DIR = str(TESTS_DIR)
QUESTION_FILE = os.path.join(WORK_DIR, "inputs", "questions", "q1_ts_support.txt")

with open(QUESTION_FILE, "r", encoding="utf-8") as f:
    question = f.read().strip()

# ---------- Retrieval ----------
retrieval = RetrievalAgent(
    deepseek_api_key=os.environ.get("DEEPSEEK_API_KEY"),
    embedder_name="bge"
)

retrieval_result = retrieval.process_question(
    question=question,
    pdf_dir=os.path.join(WORK_DIR, "inputs", "papers"),
    index_dir=os.path.join(WORK_DIR, "temp", "index"),
    search_papers=False
)

os.makedirs(os.path.join(WORK_DIR, "outputs"), exist_ok=True)

with open(os.path.join(WORK_DIR, "outputs", "retrieval_result.json"), "w", encoding="utf-8") as f:
    json.dump(retrieval_result, f, indent=2, ensure_ascii=False)

print("Retrieval finished.")
print("  literature_review exists:", bool(retrieval_result.get("literature_review")))
print("  chemistry_context exists:", isinstance(retrieval_result.get("chemistry_context"), dict))

# ---------- Hypothesis ----------
hypothesis = HypothesisAgent(
    deepseek_api_key=os.environ.get("DEEPSEEK_API_KEY")
)

hypothesis_result = hypothesis.generate_and_rank_hypotheses(
    research_question=question,
    literature_review=retrieval_result.get("literature_review", ""),
    num_queries=2,
    num_hypotheses_per_query=2,
    top_n=2
)

with open(os.path.join(WORK_DIR, "outputs", "hypothesis_result.json"), "w", encoding="utf-8") as f:
    json.dump(hypothesis_result, f, indent=2, ensure_ascii=False)

ranked = hypothesis_result.get("ranked_strategies", [])
topn = hypothesis_result.get("top_n_strategies", [])

print("Hypothesis finished.")
print("  queries:", len(hypothesis_result.get("queries", [])))
print("  optimized hypotheses:", len(hypothesis_result.get("optimized_hypotheses", [])))
print("  ranked strategies:", len(ranked))
print("  top_n_strategies:", len(topn))
print("Saved retrieval_result.json and hypothesis_result.json")
