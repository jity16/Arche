"""Regression tests for the ARCHE workflow-execution fixes.

P1  route correction must reach the real Gaussian run (execution_agent):
    - the deterministic run honors an explicit/corrected route_section verbatim
      instead of re-deriving it from text;
    - frequency intent is detected from structured context (job_type / validation
      requirements), not only from description keywords;
    - the route_section build-bridge promotes a *revised* recommended_route over an
      uncorrected route.
P2  parse/analysis steps must reach output_parser.py instead of being clamped to
    generate_gaussian_code (planner_agent + toolpool.json), including names that embed
    "gaussian" (gaussian_log_analyzer / analyze_gaussian_output / gaussian_output_analysis).
P3  Gaussian .log files are harvested into the persisted artifacts dir so the existing
    download endpoint can serve them — but only from this run's controlled roots; arbitrary
    absolute paths, symlinks and other runs' job dirs found in the (untrusted) result JSON
    must never become downloadable artifacts (server path-safety boundary).

Dependencies & how to run
-------------------------
This file imports the real ExecutionAgent / PlannerAgent / Flask server, so it needs the
project's runtime dependencies — NOT stdlib only. Install the documented dependency
entrypoint first, then (for the server path-safety tests) Flask, matching the Dockerfile:

    pip install -r requirements.txt                 # requests, numpy, rdkit, ase, ...  (P1/P2)
    pip install "flask>=3.0,<4" "waitress>=3.0,<4"  # server import                     (P3)

Then, from the ARCHE project root:

    python -m pytest tests/test_arche_workflow_fixes.py     # or: python -m unittest

The project root is auto-inferred from this file's location; override with ARCHE_PROJECT_ROOT
if running from an unusual layout. Each test class self-skips with a clear reason when its
specific dependency is missing, so a partial environment still runs whatever it can:
  - P1 (DeterministicRouteTests)      needs execution_agent's deps (numpy / requests / ...).
  - P2 (PlannerToolResolutionTests)   needs planner_agent (stdlib-importable in practice).
  - P3 (LogHarvest / PathBoundary)    needs Flask (server import).
"""

import contextlib
import io
import json
import os
import requests
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


def find_project_root() -> Path:
    env_root = os.environ.get("ARCHE_PROJECT_ROOT")
    if env_root:
        return Path(env_root).expanduser().resolve()
    current = Path(__file__).resolve()
    for parent in [current.parent, *current.parents]:
        if (parent / "src" / "chemistry_multiagent").exists():
            return parent
    raise RuntimeError("Cannot infer ARCHE project root. Set ARCHE_PROJECT_ROOT.")


PROJECT_ROOT = find_project_root()
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

# Heavy runtime deps (numpy / rdkit / requests / ...) may be absent in a minimal checkout.
# Import defensively so the module still loads and each class can self-skip with the real
# import error, instead of erroring out the whole file at collection time.
try:
    from chemistry_multiagent.agents.execution_agent import ExecutionAgent, ExecutionStep
    _EXEC_IMPORT_ERR = None
except Exception as exc:  # noqa: BLE001 - report the missing dep, don't crash collection
    ExecutionAgent = ExecutionStep = None  # type: ignore[assignment,misc]
    _EXEC_IMPORT_ERR = exc

try:
    from chemistry_multiagent.agents.planner_agent import PlannerAgent
    _PLANNER_IMPORT_ERR = None
except Exception as exc:  # noqa: BLE001
    PlannerAgent = None  # type: ignore[assignment,misc]
    _PLANNER_IMPORT_ERR = exc

try:
    from chemistry_multiagent.agents.retrieval_agent import RetrievalAgent
    _RETRIEVAL_IMPORT_ERR = None
except Exception as exc:  # noqa: BLE001
    RetrievalAgent = None  # type: ignore[assignment,misc]
    _RETRIEVAL_IMPORT_ERR = exc


# ----------------------------------------------------------------------------- P2
@unittest.skipUnless(PlannerAgent is not None, f"planner_agent import failed: {_PLANNER_IMPORT_ERR}")
class PlannerToolResolutionTests(unittest.TestCase):
    """P2: a parse step must resolve to the parser tool, never to generate_gaussian_code."""

    @classmethod
    def setUpClass(cls):
        cls.planner = PlannerAgent(deepseek_api_key="", enable_expert_review=False)
        cls.registered = {t.get("tool_name") for t in cls.planner.tools}

    def test_parser_tools_are_registered(self):
        self.assertIn("parse_gaussian_output", self.registered)
        self.assertIn("get_gjf_from_log", self.registered)
        self.assertIn("sdf_to_xyz", self.registered)

    def test_registered_parser_short_circuits(self):
        status = self.planner._resolve_tool_status("parse_gaussian_output")
        self.assertEqual(status["status"], "registered")

    def test_unregistered_parse_name_maps_to_parser_not_codegen(self):
        status = self.planner._resolve_tool_status("output_parser")
        self.assertEqual(status["status"], "recognized_family")
        self.assertEqual(status["family"], "parser")
        self.assertEqual(status["mapped_tool"], "parse_gaussian_output")

    def test_validate_protocol_routes_parse_step_to_parser(self):
        protocol = {
            "Steps": [
                {"Step_number": 1, "Tool": "output_parser"},          # invented parse name
                {"Step_number": 2, "Tool": "parse_gaussian_output"},  # registered
                {"Step_number": 3, "Tool": "totally_unknown_widget"}, # genuinely unknown
                {"Step_number": 4, "Tool": "generate_gaussian_code"}, # registered
            ]
        }
        steps = self.planner._validate_protocol_tools(protocol)["Steps"]
        self.assertEqual(steps[0]["Tool"], "parse_gaussian_output")
        self.assertEqual(steps[1]["Tool"], "parse_gaussian_output")
        self.assertEqual(steps[2]["Tool"], "totally_unknown_widget")
        self.assertEqual(steps[3]["Tool"], "generate_gaussian_code")

    def test_validate_protocol_maps_python_parser_step_to_registered_parser(self):
        protocol = {
            "Steps": [
                {
                    "Step_number": 1,
                    "Description": "Parse the neutral Gaussian log to extract total energy and orbital energies.",
                    "Tool": "Other: Python script",
                    "Input": "neutral.log",
                    "Output": "JSON with HOMO/LUMO energies",
                }
            ]
        }

        steps = self.planner._validate_protocol_tools(protocol)["Steps"]
        self.assertEqual(steps[0]["Tool"], "parse_gaussian_output")

    def test_gen_conformation_path_fixed(self):
        tool = next(t for t in self.planner.tools if t.get("tool_name") == "gen_conformation")
        self.assertTrue(str(tool["tool_path"]).endswith("gen_conformation.py"))

    def test_gaussian_named_analysis_steps_map_to_parser_not_codegen(self):
        """Gaussian-named output readers must never be clamped to code generation."""
        for name in (
            "gaussian_log_analyzer",
            "analyze_gaussian_output",
            "gaussian_output_analysis",
            "gaussian_result_parser",
            "analyse_gaussian_log",
        ):
            with self.subTest(tool=name):
                status = self.planner._resolve_tool_status(name)
                self.assertEqual(status["status"], "recognized_family", name)
                self.assertEqual(status["family"], "parser", name)
                self.assertEqual(status["mapped_tool"], "parse_gaussian_output", name)

    def test_result_reader_verbs_with_gaussian_map_to_parser(self):
        """Common reader verbs for completed Gaussian outputs must map to the parser."""
        for name in (
            "gaussian_log_to_json",
            "extract_gaussian_freqs",
            "read_gaussian_results",
            "summarize_gaussian_output",
            "gaussian_freq_reader",
            "gaussian_output_dump",
            "generate_summary_from_log",
            "get_gaussian_results",
        ):
            with self.subTest(tool=name):
                status = self.planner._resolve_tool_status(name)
                self.assertEqual(status["family"], "parser", name)
                self.assertEqual(status["mapped_tool"], "parse_gaussian_output", name)

    def test_non_english_parse_name_maps_to_parser(self):
        """Chinese parser names for completed Gaussian output must map to the parser."""
        for name in ("解析gaussian输出", "提取gaussian日志频率", "汇总gaussian结果"):
            with self.subTest(tool=name):
                status = self.planner._resolve_tool_status(name)
                self.assertEqual(status["family"], "parser", name)
                self.assertEqual(status["mapped_tool"], "parse_gaussian_output", name)

    def test_real_gaussian_generation_names_stay_codegen(self):
        """Real Gaussian-input generation names must remain codegen."""
        for name in (
            "gaussian_input_builder",
            "run_gaussian_opt",
            "build_gaussian_gjf",
            "prepare_gaussian_input",
            "gaussian_report_generator",  # 含 reader 词根 report,但 generat 守门 → 仍属 codegen
        ):
            with self.subTest(tool=name):
                status = self.planner._resolve_tool_status(name)
                self.assertEqual(status["family"], "gaussian", name)
                self.assertEqual(status["mapped_tool"], "generate_gaussian_code", name)

    def test_non_log_analyze_not_hijacked_to_gaussian_parser(self):
        """Non-log analysis and input parsers must not be hijacked to the Gaussian parser."""
        for name in ("rdkit_analyze_geometry", "extract_smiles_features", "analyze_topology", "input_parser"):
            with self.subTest(tool=name):
                status = self.planner._resolve_tool_status(name)
                self.assertNotEqual(status.get("mapped_tool"), "parse_gaussian_output", name)

    def test_extract_json_object_ignores_trailing_brace_text(self):
        raw = """```json
{
  "Steps": [
    {
      "Step_number": 1,
      "Description": "Generate water structure",
      "Tool": "smiles2sdf",
      "Input": "O",
      "Output": "water.sdf"
    }
  ]
}
```
Extra note with braces: {not_json}
"""
        parsed = self.planner._extract_json_object(raw)
        self.assertIsInstance(parsed, dict)
        self.assertEqual(len(parsed.get("Steps", [])), 1)

    def test_extract_json_object_recovers_steps_when_outer_object_tail_is_broken(self):
        raw = """```json
{
  "workflow_name": "Malformed planner output",
  "Steps": [
    {
      "Step_number": 1,
      "Description": "Generate water structure",
      "Tool": "smiles2sdf",
      "Input": "O",
      "Output": "water.sdf"
    }
  ],
  "note": "comma is missing here"
  "other": true
}
```"""
        parsed = self.planner._extract_json_object(raw)
        self.assertIsInstance(parsed, dict)
        self.assertEqual(len(parsed.get("Steps", [])), 1)
        self.assertEqual(parsed["Steps"][0]["Tool"], "smiles2sdf")

    def test_generate_experiment_protocol_retries_when_first_response_has_no_steps(self):
        planner = PlannerAgent(deepseek_api_key="", enable_expert_review=False)
        planner._call_llm = mock.Mock(side_effect=[
            """{"workflow_name": "broken", "goal": "still no executable steps"}""",
            """{
  "Steps": [
    {
      "Step_number": 1,
      "Description": "Generate water structure",
      "Tool": "smiles2sdf",
      "Input": "O",
      "Output": "water.sdf"
    }
  ]
}""",
        ])

        protocol = planner.generate_experiment_protocol(
            {"strategy_name": "Water benchmark", "reasoning": "Need executable steps"},
            "Predict water geometry.",
        )

        self.assertEqual(planner._call_llm.call_count, 2)
        self.assertEqual(len(protocol.get("Steps", [])), 1)
        self.assertEqual(protocol["Steps"][0]["Tool"], "smiles2sdf")

    def test_generate_experiment_protocol_retries_when_parser_is_misused_for_json_postprocess(self):
        planner = PlannerAgent(deepseek_api_key="", enable_expert_review=False)
        planner._call_llm = mock.Mock(side_effect=[
            """{
  "Steps": [
    {
      "Step_number": 1,
      "Description": "Perform CBS extrapolation from parsed JSON energies.",
      "Tool": "parse_gaussian_output",
      "Input": "N2_SP_VQZ.json",
      "Output": "E_CBS_N2"
    }
  ]
}""",
            """{
  "Steps": [
    {
      "Step_number": 1,
      "Description": "Parse Gaussian single-point log to extract the electronic energy.",
      "Tool": "parse_gaussian_output",
      "Input": "N2_SP_VQZ.log",
      "Output": "N2_SP_VQZ.json"
    }
  ]
}""",
        ])

        protocol = planner.generate_experiment_protocol(
            {"strategy_name": "Reaction enthalpy", "reasoning": "Need Gaussian log parsing, not JSON post-processing"},
            "Compute the reaction enthalpy for N2 + 3H2 -> 2NH3.",
        )

        self.assertEqual(planner._call_llm.call_count, 2)
        self.assertEqual(protocol["Steps"][0]["Tool"], "parse_gaussian_output")
        self.assertEqual(protocol["Steps"][0]["Input"], "N2_SP_VQZ.log")

    def test_generate_workflows_skips_invalid_protocols_and_keeps_searching_ranked_strategies(self):
        planner = PlannerAgent(deepseek_api_key="", enable_expert_review=False)
        planner.generate_experiment_protocol = mock.Mock(side_effect=[
            {"strategy_name": "invalid-first", "Steps": []},
            {
                "strategy_name": "valid-second",
                "Steps": [{"Step_number": 1, "Description": "Build water", "Tool": "smiles2sdf", "Input": "O", "Output": "water.sdf"}],
            },
            {
                "strategy_name": "valid-third",
                "Steps": [{"Step_number": 1, "Description": "Build benzene", "Tool": "smiles2sdf", "Input": "c1ccccc1", "Output": "benzene.sdf"}],
            },
        ])
        planner.optimize_protocol = mock.Mock(side_effect=lambda protocol, *args, **kwargs: protocol)

        ranked = [
            {"strategy_name": "invalid-first", "reasoning": "bad"},
            {"strategy_name": "valid-second", "reasoning": "good"},
            {"strategy_name": "valid-third", "reasoning": "good"},
        ]

        result = planner.generate_workflows_for_top_strategies(ranked, "Test question", top_n=2)

        self.assertEqual(planner.generate_experiment_protocol.call_count, 3)
        self.assertEqual(len(result["protocols"]), 2)
        self.assertEqual([p["strategy_name"] for p in result["protocols"]], ["valid-second", "valid-third"])

    def test_generate_workflows_raises_when_no_ranked_strategy_yields_executable_protocol(self):
        planner = PlannerAgent(deepseek_api_key="", enable_expert_review=False)
        planner.generate_experiment_protocol = mock.Mock(side_effect=[
            {"strategy_name": "invalid-first", "Steps": []},
            {"strategy_name": "invalid-second", "Steps": []},
        ])

        ranked = [
            {"strategy_name": "invalid-first", "reasoning": "bad"},
            {"strategy_name": "invalid-second", "reasoning": "bad"},
        ]

        with self.assertRaisesRegex(RuntimeError, "No executable workflows generated"):
            planner.generate_workflows_for_top_strategies(ranked, "Test question", top_n=2)

    def test_protocol_with_unmapped_pyscf_step_is_not_executable(self):
        protocol = {
            "Steps": [
                {
                    "Step_number": 1,
                    "Description": "Run a Python script using PySCF to perform CASSCF(6,6) and NEVPT2 on benzene.",
                    "Tool": "PySCF (standard software)",
                    "Input": "benzene.xyz",
                    "Output": "CASSCF and NEVPT2 energies",
                }
            ]
        }

        self.assertFalse(self.planner._protocol_is_executable(protocol))
        issues = self.planner.validate_step_sequence(protocol["Steps"])
        self.assertTrue(any("PySCF" in issue or "pyscf" in issue for issue in issues))


@unittest.skipUnless(RetrievalAgent is not None, f"retrieval_agent import failed: {_RETRIEVAL_IMPORT_ERR}")
class RetrievalChemistryContextTests(unittest.TestCase):
    """Regression: generic literature mentions of IRC must not taint simple geometry questions."""

    def test_geometry_question_does_not_inherit_irc_from_generic_review(self):
        agent = RetrievalAgent(deepseek_api_key="", embedder_name="openai")
        question = r"预测 $\ce{H2O}$ 在 $\text{B3LYP/6-31G}^*$ 下的优化几何构型"
        literature_review = (
            "For reaction studies, Gaussian workflows often confirm transition states with IRC. "
            "That general note is unrelated to this standalone water geometry benchmark."
        )
        answer = "The requested task is a geometry optimization of water, not a pathway-connectivity study."

        ctx = agent.extract_chemistry_context(question, literature_review=literature_review, answer=answer)

        self.assertFalse(ctx["needs_ts"])
        self.assertFalse(ctx["needs_irc"])
        self.assertIn("opt", ctx["suspected_job_types"])
        self.assertNotIn("irc", ctx["suspected_job_types"])
        self.assertNotIn("N", ctx["candidate_elements"])
        self.assertNotIn("L", ctx["candidate_elements"])

    def test_disable_pdf_library_noise_turns_off_pymupdf_messages(self):
        from chemistry_multiagent.agents import retrieval_agent as retrieval_module

        calls = []

        class _FakeTools:
            def mupdf_display_errors(self, enabled):
                calls.append(("errors", enabled))

            def mupdf_display_warnings(self, enabled):
                calls.append(("warnings", enabled))

        fake_fitz = SimpleNamespace(TOOLS=_FakeTools())

        retrieval_module._disable_pdf_library_noise(fake_fitz)

        self.assertEqual(calls, [("errors", False), ("warnings", False)])

    def test_search_papers_suppresses_third_party_stderr_noise(self):
        from chemistry_multiagent.agents import retrieval_agent as retrieval_module

        class _FakePaperScraper:
            @staticmethod
            def search_papers(_keyword, limit=0, pdir=""):
                print("paperscraper traceback noise", file=sys.stderr)
                return []

        agent = RetrievalAgent(deepseek_api_key="", embedder_name="openai")
        old_available = retrieval_module.PAPERSCRAPER_AVAILABLE
        old_module = getattr(retrieval_module, "paperscraper", None)
        try:
            retrieval_module.PAPERSCRAPER_AVAILABLE = True
            retrieval_module.paperscraper = _FakePaperScraper
            with tempfile.TemporaryDirectory() as pdf_dir, contextlib.redirect_stderr(io.StringIO()) as err:
                files = agent.search_papers(["water geometry"], pdf_dir, limit_per_keyword=1)
            self.assertEqual(files, [])
            self.assertEqual(err.getvalue(), "")
        finally:
            retrieval_module.PAPERSCRAPER_AVAILABLE = old_available
            retrieval_module.paperscraper = old_module


# ----------------------------------------------------------------------------- P1
@unittest.skipUnless(ExecutionAgent is not None, f"execution_agent import failed: {_EXEC_IMPORT_ERR}")
class DeterministicRouteTests(unittest.TestCase):
    """P1: the real Gaussian run must honor the corrected route and structured freq intent."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.agent = ExecutionAgent(
            deepseek_api_key="",
            enable_expert_analysis=False,
            gaussian_job_root=cls._tmp.name,
        )

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    @staticmethod
    def _step(**kw) -> ExecutionStep:
        base = dict(
            step_number=1,
            description="Run Gaussian calculation",
            tool_name="generate_gaussian_code",
            expected_input="sdf",
            expected_output="energy",
        )
        base.update(kw)
        return ExecutionStep(**base)

    def test_explicit_corrected_route_is_used_verbatim(self):
        corrected = "#P B3LYP/6-31G(d) Opt=Tight Freq"
        route = self.agent._deterministic_gaussian_route(self._step(route_section=corrected), None)
        self.assertEqual(route, corrected)

    def test_freq_intent_from_job_type(self):
        route = self.agent._deterministic_gaussian_route(self._step(job_type="freq"), None)
        self.assertIn("freq", route.lower())
        self.assertIn("opt", route.lower())

    def test_freq_intent_from_structured_validation_requirements(self):
        step = self._step(
            description="single point",  # 描述里没有 freq 关键词
            scientific_context={"expected_validation_requirements": ["vibrational frequency analysis"]},
        )
        route = self.agent._deterministic_gaussian_route(step, None)
        self.assertIn("freq", route.lower())

    def test_plain_opt_step_has_no_freq_regression(self):
        step = self._step(description="geometry optimization of the molecule")
        route = self.agent._deterministic_gaussian_route(step, None)
        self.assertIn("opt", route.lower())
        self.assertNotIn("freq", route.lower())

    def test_select_route_section_promotes_revised_recommended_route(self):
        step_dict = {"route_section": "# B3LYP/6-31G(d) opt"}
        review = {"review_status": "revised", "recommended_route": "#P B3LYP/6-31G(d) Opt=Tight Freq"}
        self.assertEqual(
            self.agent._select_route_section(step_dict, {}, review),
            "#P B3LYP/6-31G(d) Opt=Tight Freq",
        )

    def test_select_route_section_accepts_revised_status_variants(self):
        step_dict = {"route_section": "# B3LYP/6-31G(d) opt"}
        review = {"review_status": "corrected", "recommended_route": "#P B3LYP/6-31G(d) Opt=Tight Freq"}
        self.assertEqual(
            self.agent._select_route_section(step_dict, {}, review),
            "#P B3LYP/6-31G(d) Opt=Tight Freq",
        )

    def test_select_route_section_normalizes_missing_hash_on_revised_route(self):
        step_dict = {"route_section": "# B3LYP/6-31G(d) opt"}
        review = {"review_status": "revised", "recommended_route": "P B3LYP/6-31G(d) Opt=Tight Freq"}
        self.assertEqual(
            self.agent._select_route_section(step_dict, {}, review),
            "#P B3LYP/6-31G(d) Opt=Tight Freq",
        )

    def test_select_route_section_keeps_explicit_when_not_revised(self):
        step_dict = {"route_section": "# B3LYP/6-31G(d) opt"}
        review = {"review_status": "approved", "recommended_route": "#P B3LYP/6-31G(d) Opt=Tight Freq"}
        self.assertEqual(self.agent._select_route_section(step_dict, {}, review), "# B3LYP/6-31G(d) opt")

    def test_select_route_section_recommended_is_last_fallback(self):
        review = {"review_status": "approved", "recommended_route": "#P wB97XD/def2SVP Opt Freq"}
        self.assertEqual(self.agent._select_route_section({}, {}, review), "#P wB97XD/def2SVP Opt Freq")

    def test_parse_step_in_chinese_is_not_execution_intent(self):
        step = self._step(
            description="使用parse_gaussian_output工具解析Gaussian输出日志，提取优化后的几何结构和能量。",
            tool_name="parse_gaussian_output",
        )
        tool = self.agent.tools["parse_gaussian_output"]
        self.assertFalse(self.agent._is_gaussian_execution_intent(step, tool))

    def test_extract_step_in_chinese_is_not_execution_intent(self):
        step = self._step(
            description="使用get_gjf_from_log工具从日志文件中提取最终几何，生成新的Gaussian输入文件。",
            tool_name="get_gjf_from_log",
        )
        tool = self.agent.tools["get_gjf_from_log"]
        self.assertFalse(self.agent._is_gaussian_execution_intent(step, tool))

    def test_report_step_in_chinese_is_not_execution_intent(self):
        step = self._step(
            description="整理并输出最终结果：从步骤6的JSON结果中提取键长和键角信息。",
            tool_name="generate_gaussian_code",
        )
        tool = self.agent.tools["generate_gaussian_code"]
        self.assertFalse(self.agent._is_gaussian_execution_intent(step, tool))

    def test_generate_route_section_in_english_is_not_execution_intent(self):
        step = self._step(
            description="Generate Gaussian route section and keywords for B3LYP/6-31G* optimization and frequency analysis of water.",
            tool_name="generate_gaussian_code",
            expected_output="Gaussian route section: '#p opt freq B3LYP/6-31G*'",
        )
        tool = self.agent.tools["generate_gaussian_code"]
        self.assertFalse(self.agent._is_gaussian_execution_intent(step, tool))

    def test_generate_route_section_in_chinese_is_not_execution_intent(self):
        step = self._step(
            description="调用generate_gaussian_code工具，根据用户问题“预测H2O在B3LYP/6-31G*下的优化几何构型”生成Gaussian关键字和路由部分。输出将包含如“# opt B3LYP/6-31G(d)”等正确路由。",
            tool_name="generate_gaussian_code",
            expected_output="Gaussian关键词字符串，包含优化和频率计算的路由",
        )
        tool = self.agent.tools["generate_gaussian_code"]
        self.assertFalse(self.agent._is_gaussian_execution_intent(step, tool))

    def test_create_gaussian_input_file_is_not_execution_intent(self):
        step = self._step(
            description="Create a Gaussian input file (.gjf) for the water optimization. Use xyz_to_gjf, which takes the XYZ coordinates and the Gaussian route section from Step 3 and writes a complete .gjf file.",
            tool_name="xyz_to_gjf",
            expected_input="Water.xyz and route section",
            expected_output="water.gjf",
        )
        tool = self.agent.tools["xyz_to_gjf"]
        self.assertFalse(self.agent._is_gaussian_execution_intent(step, tool))

    def test_execution_intent_prefers_existing_gjf_backend_over_codegen(self):
        with tempfile.TemporaryDirectory() as run_dir:
            gjf_path = os.path.join(run_dir, "water.gjf")
            with open(gjf_path, "w", encoding="utf-8") as f:
                f.write("%mem=1GB\n# B3LYP/6-31G(d) opt freq\n\nwater\n\n0 1\nO 0 0 0\nH 0 0 1\nH 0 1 0\n\n")
            step = self._step(
                description="Run Gaussian optimization and frequency calculation using water.gjf. This step uses external Gaussian software.",
                tool_name="generate_gaussian_code",
                expected_input=gjf_path,
                expected_output="water.log",
            )
            tool = self.agent.tools["generate_gaussian_code"]
            calls = {}

            def unexpected_det(*_args, **_kwargs):
                raise AssertionError("deterministic geometry fallback should not run when a .gjf already exists")

            def fake_gaussian_backend(tool_name, payload, step=None, tool=None):
                calls["tool_name"] = tool_name
                calls["payload"] = dict(payload)
                return {
                    "execution_mode": "gaussian_job",
                    "status": "completed",
                    "success": True,
                    "output_artifacts": [gjf_path.replace(".gjf", ".log")],
                }

            self.agent._deterministic_gaussian_calc = unexpected_det  # type: ignore[assignment]
            self.agent.execute_gaussian_related_tool = fake_gaussian_backend  # type: ignore[assignment]

            result = self.agent.execute_tool(tool, {"gjf_path": gjf_path}, step=step)

            self.assertEqual(result.get("execution_mode"), "gaussian_job")
            self.assertEqual(calls["tool_name"], "run_gaussian_deterministic")
            self.assertEqual(calls["payload"]["gjf_path"], gjf_path)

    def test_strategy_name_reaction_barriers_does_not_block_single_molecule_smiles_fallback(self):
        self.agent._run_question = "预测 H2O 在 B3LYP/6-31G* 下的优化几何构型"
        self.agent._current_strategy_name = (
            "Systematic basis set and dispersion sensitivity analysis for H2O geometry and reaction barriers"
        )
        step = self._step(
            description="Run Gaussian B3LYP/def2-TZVP optimization + frequency.",
            tool_name="generate_gaussian_code",
            expected_input="water_B3LYP_def2TZVP.gjf",
        )
        self.assertEqual(self.agent._resolve_smiles_from_context(step, {}, None), ["O"])

    def test_sdf_to_xyz_prefers_run_local_output_path(self):
        with tempfile.TemporaryDirectory() as run_dir:
            sdf_path = os.path.join(run_dir, "water.sdf")
            with open(sdf_path, "w", encoding="utf-8") as f:
                f.write(self._WATER_SDF)
            step = self._step(
                description="Convert the SDF file from step 2 to XYZ format using sdf_to_xyz.",
                tool_name="sdf_to_xyz",
                expected_input="water.sdf",
                expected_output="Water_initial.xyz",
            )
            self.agent.work_dir = run_dir
            kwargs, artifacts, err = self.agent._build_real_tool_call_context(
                self.agent.tools["sdf_to_xyz"], {}, step, str(PROJECT_ROOT / "src" / "chemistry_multiagent" / "tools" / "sdf_to_xyz.py"), "sdf_to_xyz"
            )
            self.assertIsNone(err)
            self.assertEqual(kwargs["input_sdf_path"], sdf_path)
            self.assertTrue(kwargs["output_xyz_path"].startswith(run_dir + os.sep))
            self.assertTrue(kwargs["output_xyz_path"].endswith(".xyz"))
            self.assertEqual(artifacts, [kwargs["output_xyz_path"]])

    def test_resolve_gjf_path_prefers_run_local_relative_input(self):
        with tempfile.TemporaryDirectory() as run_dir:
            gjf_name = "co2_run_local_only.gjf"
            gjf_path = os.path.join(run_dir, gjf_name)
            with open(gjf_path, "w", encoding="utf-8") as f:
                f.write("%mem=1GB\n# B3LYP/6-31G(d) opt\n\nco2\n\n0 1\nC 0 0 0\nO 0 0 1.16\nO 0 0 -1.16\n\n")

            step = self._step(
                description="Run Gaussian optimization using the generated input file.",
                tool_name="generate_gaussian_code",
                expected_input=gjf_name,
                expected_output="co2_run_local_only.log",
            )
            self.agent.work_dir = run_dir

            resolved = self.agent._resolve_gjf_path({}, step=step)

            self.assertEqual(resolved, gjf_path)

    def test_prepare_gaussian_job_inputs_uses_distinct_state_files_for_same_step_id(self):
        with tempfile.TemporaryDirectory() as run_dir:
            gjf_a = os.path.join(run_dir, "co2_a.gjf")
            gjf_b = os.path.join(run_dir, "co2_b.gjf")
            for path in (gjf_a, gjf_b):
                with open(path, "w", encoding="utf-8") as f:
                    f.write("%mem=1GB\n# B3LYP/6-31G(d) opt\n\nco2\n\n0 1\nC 0 0 0\nO 0 0 1.16\nO 0 0 -1.16\n\n")

            step_a = self._step(tool_name="generate_gaussian_code", expected_input="co2_a.gjf", expected_output="co2_a.log")
            step_a.step_id = "5"
            step_b = self._step(tool_name="generate_gaussian_code", expected_input="co2_b.gjf", expected_output="co2_b.log")
            step_b.step_id = "5"
            self.agent.work_dir = run_dir

            prepared_a = self.agent._prepare_gaussian_job_inputs("run_gaussian_deterministic", {"gjf_path": gjf_a}, step_a)
            prepared_b = self.agent._prepare_gaussian_job_inputs("run_gaussian_deterministic", {"gjf_path": gjf_b}, step_b)

            self.assertNotEqual(prepared_a["state_path"], prepared_b["state_path"])

    def test_smiles2sdf_prefers_run_local_output_path_for_relative_hint(self):
        with tempfile.TemporaryDirectory() as run_dir:
            step = self._step(
                description="生成水分子的初始3D结构：使用SMILES字符串 'O' 生成SDF文件。",
                tool_name="smiles2sdf",
                expected_input="SMILES: O",
                expected_output="H2O_initial.sdf (包含单个水分子的3D坐标)",
            )
            self.agent.work_dir = run_dir
            kwargs, artifacts, err = self.agent._build_real_tool_call_context(
                self.agent.tools["smiles2sdf"], {"smiles": "O"}, step, str(PROJECT_ROOT / "src" / "chemistry_multiagent" / "tools" / "smiles2sdf.py"), "smiles_to_sdf"
            )
            self.assertIsNone(err)
            self.assertEqual(kwargs["smiles"], "O")
            self.assertTrue(kwargs["output_sdf_path"].startswith(run_dir + os.sep))
            self.assertTrue(kwargs["output_sdf_path"].endswith(".sdf"))
            self.assertEqual(artifacts, [kwargs["output_sdf_path"]])

    def test_normalize_smiles_list_strips_quotes_and_descriptive_suffix(self):
        self.assertEqual(
            self.agent._normalize_smiles_list('"c1ccccc1" (benzene SMILES)'),
            ["c1ccccc1"],
        )

    def test_extract_paths_with_suffix_ignores_descriptive_prefix(self):
        paths = self.agent._extract_paths_with_suffix("Water molecule in SDF file: water_init.sdf", ".sdf")
        self.assertEqual(paths, ["water_init.sdf"])

    def test_normalize_route_section_strips_markdown_wrapper(self):
        raw = '# ** `#P B3LYP/6-31G(d) Opt=Tight SCF=Tight Integral=UltraFine`'
        self.assertEqual(
            self.agent._normalize_route_section(raw),
            "#P B3LYP/6-31G(d) Opt=Tight SCF=Tight Integral=UltraFine",
        )

    def test_normalize_route_section_strips_unmatched_trailing_parenthesis(self):
        self.assertEqual(
            self.agent._normalize_route_section("# HF/cc-pVTZ)"),
            "# HF/cc-pVTZ",
        )

    def test_normalize_route_section_prefers_first_route_in_multilink_output(self):
        raw = """# opt b3lyp/6-31g(d)

--link1--
%chk=benzene.chk
# b3lyp/6-31g(d) pop=full gfprint iop(3/33=1) geom=checkpoint guess=read
"""
        self.assertEqual(
            self.agent._normalize_route_section(raw),
            "# opt b3lyp/6-31g(d)",
        )

    def test_import_backend_allows_sibling_module_imports(self):
        with tempfile.TemporaryDirectory() as run_dir:
            log_path = os.path.join(run_dir, "water.log")
            with open(log_path, "w", encoding="utf-8") as f:
                f.write(" Normal termination of Gaussian 16\n")
                f.write(" Charge =  0 Multiplicity = 1\n")
            tool = self.agent.tools["get_gjf_from_log"]
            step = self._step(
                description="Extract the final optimized geometry from the B3LYP log file.",
                tool_name="get_gjf_from_log",
                expected_input="water.log",
                expected_output="water_opt.gjf",
            )
            step.working_directory = run_dir
            result = self.agent._execute_real_tool_via_import(
                tool=tool,
                input_data={"input_file_path": log_path, "output_gjf_path": os.path.join(run_dir, "water_opt.gjf")},
                step=step,
                script_path=str(PROJECT_ROOT / "src" / "chemistry_multiagent" / "tools" / "Get_gjf_from_log.py"),
            )
            self.assertNotEqual(result.get("error"), "No module named 'output_parser'")

    def test_plot_tools_accepts_run_local_json_input(self):
        with tempfile.TemporaryDirectory() as run_dir:
            json_path = os.path.join(run_dir, "co2_results.json")
            png_path = os.path.join(run_dir, "ir_spectrum.png")
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "frequencies": [667.4, 2349.1],
                        "ir_intensities": [42.0, 100.0],
                        "metadata": {"filename": "co2.log"},
                    },
                    f,
                )

            tool = self.agent.tools["plot_tools"]
            step = self._step(
                description="Plot the simulated IR spectrum using the plot_tools tool.",
                tool_name="plot_tools",
                expected_input="co2_results.json",
                expected_output="ir_spectrum.png",
            )
            step.working_directory = run_dir
            self.agent.work_dir = run_dir

            result = self.agent._execute_real_tool_via_import(
                tool=tool,
                input_data={},
                step=step,
                script_path=str(PROJECT_ROOT / "src" / "chemistry_multiagent" / "tools" / "plot_tools.py"),
            )

            self.assertTrue(result.get("success"), result)
            self.assertTrue(os.path.isfile(png_path))

    def test_plot_tools_uses_recent_json_when_step_only_mentions_parsed_data(self):
        with tempfile.TemporaryDirectory() as run_dir:
            json_path = os.path.join(run_dir, "water_results.json")
            png_path = os.path.join(run_dir, "ir_spectrum.png")
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "frequencies": [1600.0, 3650.0, 3750.0],
                        "ir_intensities": [12.0, 30.0, 25.0],
                    },
                    f,
                )

            tool = self.agent.tools["plot_tools"]
            step = self._step(
                description="Visualize the predicted IR spectrum using parsed frequencies and intensities.",
                tool_name="plot_tools",
                expected_input="Parsed data (frequencies and IR intensities) from Step 6",
                expected_output="ir_spectrum.png",
            )
            step.working_directory = run_dir
            self.agent.work_dir = run_dir
            self.agent._recent_gaussian_logs = []
            self.agent._recent_gaussian_jsons = [json_path]

            kwargs, artifacts, err = self.agent._build_real_tool_call_context(
                tool,
                {},
                step,
                str(PROJECT_ROOT / "src" / "chemistry_multiagent" / "tools" / "plot_tools.py"),
                "plot_tools",
            )

            self.assertIsNone(err)
            self.assertEqual(kwargs["input_file_path"], json_path)
            self.assertEqual(kwargs["output_image_path"], png_path)
            self.assertEqual(artifacts, [png_path])

    def test_single_point_validation_accepts_normal_termination_with_energy_when_scf_flag_missing(self):
        validation = self.agent.validate_single_point_result(
            {
                "job_type": "sp",
                "normal_termination": True,
                "scf_converged": None,
                "scf_energy": -232.24000229230253,
                "E_HOMO": -0.2455808035306677,
                "E_LUMO": 0.004190831193789268,
                "HOMO_LUMO_gap": 0.24977163472445696,
            }
        )

        self.assertEqual(validation["status"], "pass")
        self.assertEqual(validation["checks"], {"scf_convergence": True, "energy_extracted": True})

    def test_xyz_to_gjf_subprocess_backend_is_mapped(self):
        with tempfile.TemporaryDirectory() as run_dir:
            xyz_path = os.path.join(run_dir, "water.xyz")
            gjf_path = os.path.join(run_dir, "water.gjf")
            with open(xyz_path, "w", encoding="utf-8") as f:
                f.write("3\nwater\nO 0 0 0\nH 0 0 0.96\nH 0.75 0 -0.24\n")

            tool = self.agent.tools["xyz_to_gjf"]
            step = self._step(
                description="Create Gaussian input file for water optimization.",
                tool_name="xyz_to_gjf",
                expected_input="water.xyz",
                expected_output="water.gjf",
            )
            step.working_directory = run_dir
            self.agent.work_dir = run_dir

            result = self.agent._execute_real_tool_via_subprocess(
                tool=tool,
                input_data={"input_xyz_path": xyz_path, "output_gjf_path": gjf_path, "route_section": "# B3LYP/6-31G(d) opt"},
                step=step,
                script_path=str(PROJECT_ROOT / "src" / "chemistry_multiagent" / "tools" / "xyz2gjf.py"),
            )

            self.assertTrue(result.get("success"), result)
            self.assertTrue(os.path.isfile(gjf_path))

    def test_gaussian_api_retries_transient_502_and_recovers(self):
        class _FakeSuccessResponse:
            status_code = 200

            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "ok": True,
                    "job_id": "job-123",
                    "normal_termination": True,
                    "returncode": 0,
                    "timed_out": False,
                    "log": "Normal termination of Gaussian 16\n",
                }

        with tempfile.TemporaryDirectory() as run_dir:
            gjf_path = os.path.join(run_dir, "water.gjf")
            log_path = os.path.join(run_dir, "water.log")
            exit_code_path = os.path.join(run_dir, "water.exitcode")
            state_path = os.path.join(run_dir, "water.state.json")
            with open(gjf_path, "w", encoding="utf-8") as f:
                f.write("%mem=1GB\n# B3LYP/6-31G(d) opt\n\nwater\n\n0 1\nO 0 0 0\nH 0 0 1\nH 0 1 0\n\n")

            response = SimpleNamespace(status_code=502, text='{"error":"temporary upstream"}')
            error = requests.HTTPError("502 Server Error: Bad Gateway for url: http://fake/v1/gaussian/run")
            error.response = response

            self.agent.gaussian_api_base_url = "http://fake"
            self.agent.gaussian_api_max_retries = 1
            self.agent.gaussian_api_retry_backoff = 0.0
            self.agent.parse_gaussian_output = lambda raw: {"normal_termination": True, "converged": True}  # type: ignore[assignment]

            state = {
                "step_id": "1",
                "tool_name": "run_gaussian_deterministic",
                "execution_mode": "gaussian_job",
                "scheduler": "api",
                "work_dir": run_dir,
                "gjf_path": gjf_path,
                "log_path": log_path,
                "chk_path": os.path.join(run_dir, "water.chk"),
                "exit_code_path": exit_code_path,
                "state_path": state_path,
                "job_id": None,
                "status": "prepared",
                "submit_time": None,
                "last_check_time": None,
                "retry_count": 0,
                "expected_outputs": {"gjf": gjf_path, "log": log_path, "chk": os.path.join(run_dir, "water.chk"), "exit_code": exit_code_path},
                "resources": {},
                "error": None,
                "message": "作业已准备",
                "job_name": "gauss_test",
                "job_type": "small_opt",
            }

            with mock.patch("chemistry_multiagent.agents.execution_agent.requests.post", side_effect=[error, _FakeSuccessResponse()]) as post:
                result = self.agent._run_gaussian_via_api(state)

            self.assertEqual(post.call_count, 2)
            self.assertEqual(result.get("status"), "completed")
            self.assertTrue(os.path.isfile(log_path))
            with open(log_path, "r", encoding="utf-8") as f:
                self.assertIn("Normal termination", f.read())

    def test_gaussian_api_falls_back_to_local_pyscf_after_remote_502(self):
        response = SimpleNamespace(status_code=502, text='{"error":"temporary upstream"}')
        error = requests.HTTPError("502 Server Error: Bad Gateway for url: http://fake/v1/gaussian/run")
        error.response = response

        with tempfile.TemporaryDirectory() as run_dir:
            gjf_path = os.path.join(run_dir, "water.gjf")
            log_path = os.path.join(run_dir, "water.log")
            exit_code_path = os.path.join(run_dir, "water.exitcode")
            state_path = os.path.join(run_dir, "water.state.json")
            with open(gjf_path, "w", encoding="utf-8") as f:
                f.write("%mem=1GB\n# B3LYP/6-31G(d) opt\n\nwater\n\n0 1\nO 0 0 0\nH 0 0 1\nH 0 1 0\n\n")

            self.agent.gaussian_api_base_url = "http://fake"
            self.agent.gaussian_api_max_retries = 0
            self.agent.local_pyscf_available = True
            self.agent.enable_local_pyscf_fallback = True

            state = {
                "step_id": "1",
                "tool_name": "run_gaussian_deterministic",
                "execution_mode": "gaussian_job",
                "scheduler": "api",
                "work_dir": run_dir,
                "gjf_path": gjf_path,
                "log_path": log_path,
                "chk_path": os.path.join(run_dir, "water.chk"),
                "exit_code_path": exit_code_path,
                "state_path": state_path,
                "job_id": None,
                "status": "prepared",
                "submit_time": None,
                "last_check_time": None,
                "retry_count": 0,
                "expected_outputs": {"gjf": gjf_path, "log": log_path, "chk": os.path.join(run_dir, "water.chk"), "exit_code": exit_code_path},
                "resources": {},
                "error": None,
                "message": "作业已准备",
                "job_name": "gauss_test",
                "job_type": "small_opt",
            }

            fake_result = {
                "scf_energies": -76.4,
                "coordinates": [[[0.0, 0.0, 0.0], [0.0, 0.0, 0.96], [0.75, 0.0, -0.24]]],
                "elements": ["O", "H", "H"],
                "charge": 0,
                "mult": 1,
                "frequencies": [1600.0, 3650.0, 3750.0],
                "opt_done": True,
                "normal_termination": True,
                "metadata": {"backend": "pyscf_local", "success": True},
            }

            with mock.patch("chemistry_multiagent.agents.execution_agent.requests.post", side_effect=[error]):
                with mock.patch("chemistry_multiagent.agents.execution_agent.run_pyscf_job", return_value=fake_result):
                    result = self.agent._run_gaussian_via_api(state)

            self.assertEqual(result.get("status"), "completed")
            self.assertEqual(result.get("scheduler"), "local_pyscf")
            self.assertTrue(os.path.isfile(log_path))

    def test_output_parser_accepts_json_log_payload(self):
        from chemistry_multiagent.tools.output_parser import parse_gaussian_output

        with tempfile.TemporaryDirectory() as run_dir:
            json_log = os.path.join(run_dir, "water.log")
            with open(json_log, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "scf_energies": -76.4,
                        "coordinates": [[[0.0, 0.0, 0.0], [0.0, 0.0, 0.96], [0.75, 0.0, -0.24]]],
                        "elements": ["O", "H", "H"],
                        "charge": 0,
                        "mult": 1,
                        "frequencies": [1600.0, 3650.0, 3750.0],
                        "ir_intensities": [10.0, 20.0, 30.0],
                        "opt_done": True,
                        "metadata": {"backend": "pyscf_local", "success": True},
                    },
                    f,
                )
            result = parse_gaussian_output(json_log)
            self.assertTrue(result["success"])
            self.assertEqual(result["result"]["elements"], ["O", "H", "H"])
            self.assertEqual(result["result"]["ir_intensities"], [10.0, 20.0, 30.0])

    def test_manual_input_step_short_circuits_without_tool_lookup(self):
        step = self._step(
            description="Generate SMILES string for water monomer.",
            tool_name="None (manual input)",
            expected_output="SMILES: O",
        )
        result = self.agent.execute_tool_step(step, input_data=None, max_retries=0)
        self.assertEqual(result.status, "success")
        self.assertEqual(result.raw_output["execution_mode"], "manual_input")
        self.assertEqual(result.raw_output["provided_output"], "SMILES: O")

    def test_python_script_step_does_not_short_circuit_as_manual_success(self):
        step = self._step(
            description="Run a Python script using PySCF to perform CASSCF(6,6) and NEVPT2 on benzene.",
            tool_name="Other: Python script",
            expected_input="benzene.xyz",
            expected_output="CASSCF and NEVPT2 energies",
        )

        result = self.agent.execute_tool_step(step, input_data=None, max_retries=0)

        self.assertEqual(result.status, "failed")
        self.assertNotEqual((result.raw_output or {}).get("execution_mode"), "manual_input")
        self.assertTrue(result.error)

    def test_latest_geometry_search_does_not_read_current_working_directory(self):
        """A stray workspace SDF must not become geometry for an unrelated run."""
        with tempfile.TemporaryDirectory() as run_dir, tempfile.TemporaryDirectory() as outside_cwd:
            decoy = os.path.join(outside_cwd, "water_initial.sdf")
            with open(decoy, "w", encoding="utf-8") as f:
                f.write("water decoy from cwd\n")

            agent = ExecutionAgent(
                deepseek_api_key="",
                enable_expert_analysis=False,
                gaussian_job_root=os.path.join(run_dir, "gaussian_jobs"),
            )
            agent.work_dir = run_dir
            old_cwd = os.getcwd()
            try:
                os.chdir(outside_cwd)
                self.assertIsNone(agent._find_latest_geometry_file([".sdf"]))
            finally:
                os.chdir(old_cwd)

    def test_complex_mechanism_context_does_not_fallback_to_water(self):
        """H2O mentioned as a byproduct is not a valid target molecule for TS validation."""
        step = self._step(
            description=(
                "Generate initial transition state guess for enamine formation "
                "(catalyst + acetone -> enamine + H2O)."
            ),
            expected_input="SMILES of reactants and products as required by the tool.",
        )
        self.agent._run_question = (
            "Validate an asymmetric catalytic reaction between p-nitrobenzaldehyde "
            "O=Cc1ccc(cc1)[N+](=O)[O-] and acetone CC(=O)C with catalyst "
            "N1CCC[C@H]1C(N2CCCC2), producing CC(=O)C(O)c1ccc([N+](=O)[O-])cc1."
        )
        self.assertEqual(self.agent._resolve_smiles_from_context(step, {}, step.expected_input), [])

    # ---- per-step molecule binding in _deterministic_gaussian_calc ----
    _WATER_SDF = (
        "water\n  test\n\n"
        "  3  2  0  0  0  0  0  0  0  0999 V2000\n"
        "    0.0000    0.1156    0.0000 O   0  0  0  0  0  0  0  0  0  0  0  0\n"
        "    0.7989   -0.4526    0.0000 H   0  0  0  0  0  0  0  0  0  0  0  0\n"
        "   -0.7989   -0.4720    0.0000 H   0  0  0  0  0  0  0  0  0  0  0  0\n"
        "  1  2  1  0\n  1  3  1  0\nM  END\n$$$$\n"
    )
    _NH3_SDF = (
        "ammonia\n  test\n\n"
        "  4  3  0  0  0  0  0  0  0  0999 V2000\n"
        "    0.0000    0.0000    0.1000 N   0  0  0  0  0  0  0  0  0  0  0  0\n"
        "    0.9400    0.0000   -0.2500 H   0  0  0  0  0  0  0  0  0  0  0  0\n"
        "   -0.4700    0.8140   -0.2500 H   0  0  0  0  0  0  0  0  0  0  0  0\n"
        "   -0.4700   -0.8140   -0.2500 H   0  0  0  0  0  0  0  0  0  0  0  0\n"
        "  1  2  1  0\n  1  3  1  0\n  1  4  1  0\nM  END\n$$$$\n"
    )

    def _make_backend_capturing_agent(self, water_path):
        """Agent whose deterministic backend just records the .gjf it was asked to run."""
        agent = ExecutionAgent(
            deepseek_api_key="",
            enable_expert_analysis=False,
            gaussian_job_root=self._tmp.name,
        )
        agent.gaussian_api_base_url = "http://fake-backend"  # make deterministic path active
        captured = {}

        def fake_backend(tool_name, payload, step=None, tool=None):
            captured["gjf"] = open(payload["gjf_path"], encoding="utf-8").read()
            return {"execution_mode": "gaussian_job", "status": "completed",
                    "parsed_results": {"scf_energy": -1.0}}

        agent.execute_gaussian_related_tool = fake_backend  # type: ignore[assignment]
        # Force the only available stray geometry to be water.
        agent._scan_workdir_for_latest_artifact = lambda *a, **k: None  # type: ignore[assignment]
        agent._find_latest_geometry_file = lambda suffixes: water_path  # type: ignore[assignment]
        return agent, captured

    def test_deterministic_calc_rejects_mismatched_artifact_and_uses_target(self):
        """Target is benzene but the only stray geometry is water → must NOT compute water."""
        with tempfile.TemporaryDirectory() as d:
            water = os.path.join(d, "water_initial.sdf")
            with open(water, "w", encoding="utf-8") as f:
                f.write(self._WATER_SDF)
            agent, captured = self._make_backend_capturing_agent(water)
            agent._run_question = "Compute the HOMO-LUMO gap of benzene c1ccccc1."
            step = self._step(description="Optimize benzene c1ccccc1 and report HOMO-LUMO.")

            result = agent._deterministic_gaussian_calc(step, None, None)

            self.assertIsNotNone(result)  # benzene regenerated from target SMILES
            gjf = captured.get("gjf", "")
            self.assertEqual(gjf.count(" O "), 0, "water oxygen must not reach the .gjf")
            self.assertGreaterEqual(gjf.count("C"), 6, "benzene carbons should be present")
            prov = result["deterministic_provenance"]
            self.assertEqual(prov["geometry_source"], "context_smiles")
            self.assertNotIn("water", str(prov.get("mol_label", "")).lower())

    def test_deterministic_calc_refuses_water_artifact_for_mechanism_question(self):
        """Mechanism question with no single target molecule → honest None, backend never runs."""
        with tempfile.TemporaryDirectory() as d:
            water = os.path.join(d, "water_initial.sdf")
            with open(water, "w", encoding="utf-8") as f:
                f.write(self._WATER_SDF)
            agent, captured = self._make_backend_capturing_agent(water)
            agent._run_question = (
                "Validate the mechanism of the asymmetric reaction between "
                "p-nitrobenzaldehyde O=Cc1ccc(cc1)[N+](=O)[O-] and acetone CC(=O)C "
                "with catalyst N1CCC[C@H]1C(N2CCCC2)."
            )
            step = self._step(
                description="Run transition state optimization for enamine formation "
                            "(catalyst + acetone -> enamine + H2O).",
                expected_input="SMILES of reactants and products.",
            )

            result = agent._deterministic_gaussian_calc(step, None, None)

            self.assertIsNone(result)              # refuses to fabricate a molecule
            self.assertNotIn("gjf", captured)      # backend was never invoked

    def test_deterministic_calc_rejects_mismatched_nh3_artifact_for_n2_target(self):
        with tempfile.TemporaryDirectory() as d:
            ammonia = os.path.join(d, "NH3.sdf")
            with open(ammonia, "w", encoding="utf-8") as f:
                f.write(self._NH3_SDF)
            agent, captured = self._make_backend_capturing_agent(ammonia)
            agent._run_question = r"求反应 $\ce{N2 + 3H2 <=> 2NH3}$ 的反应焓"
            step = self._step(
                description="Run Gaussian on N2_SP_V5Z.gjf for CCSD(T)/cc-pV5Z single-point energy.",
                expected_input="N2_SP_V5Z.gjf",
            )

            result = agent._deterministic_gaussian_calc(step, None, None)

            self.assertIsNotNone(result)
            gjf = captured.get("gjf", "")
            self.assertEqual(gjf.count(" H "), 0, "ammonia hydrogens must not leak into the N2 job")
            self.assertGreaterEqual(gjf.count("N"), 2, "target N2 geometry should contain nitrogen atoms")
            prov = result["deterministic_provenance"]
            self.assertEqual(prov["geometry_source"], "context_smiles")
            self.assertNotIn("nh3", str(prov.get("mol_label", "")).lower())

    def test_pipeline_tool_is_resolvable_and_callable(self):
        tool = self.agent.tools["main"]
        eligible, script_path, reason = self.agent._is_real_tool_eligible(tool)
        self.assertTrue(eligible, reason)
        self.assertTrue(script_path and script_path.endswith("23_TSPipeline/run.py"))

        module_name = self.agent._resolve_tool_module_name(script_path)
        spec = __import__("importlib.util").util.spec_from_file_location(module_name, script_path)
        module = __import__("importlib.util").util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        self.assertTrue(callable(getattr(module, "run_tool", None)))


# ----------------------------------------------------------------------------- P3
try:
    import flask  # noqa: F401
    _HAS_FLASK = True
except Exception:  # pragma: no cover - env dependent
    _HAS_FLASK = False


@unittest.skipUnless(_HAS_FLASK, "Flask not installed; server harvest test skipped")
class LogHarvestTests(unittest.TestCase):
    """P3: Gaussian .log files referenced in result JSON are copied into the persisted dir."""

    def setUp(self):
        # server.py lives at the project root
        root = str(PROJECT_ROOT)
        if root not in sys.path:
            sys.path.insert(0, root)
        import server  # noqa: WPS433
        self.server = server
        self._artifacts = tempfile.TemporaryDirectory()
        self._orig_artifacts_dir = server.ARTIFACTS_DIR
        server.ARTIFACTS_DIR = self._artifacts.name
        self._work = tempfile.TemporaryDirectory()
        self.output_dir = os.path.join(self._work.name, "outputs", "multiagent")
        os.makedirs(self.output_dir, exist_ok=True)

    def tearDown(self):
        self.server.ARTIFACTS_DIR = self._orig_artifacts_dir
        self._artifacts.cleanup()
        self._work.cleanup()

    def _write_log(self, name: str, content: str = "Normal termination of Gaussian\n") -> str:
        path = os.path.join(self._work.name, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path

    def test_log_referenced_in_execution_result_is_harvested(self):
        log_path = self._write_log("det_abcd1234.log")
        result = {"results": [{"intermediate_artifacts": [{"type": "output_file", "path": log_path}]}]}
        with open(os.path.join(self.output_dir, "execution_result.json"), "w", encoding="utf-8") as f:
            json.dump(result, f)

        run_id = "a" * 32
        saved = self.server._harvest_log_artifacts(run_id, self.output_dir, [])
        names = {e["name"] for e in saved}
        self.assertIn("det_abcd1234.log", names)
        self.assertTrue(os.path.isfile(os.path.join(self._artifacts.name, run_id, "det_abcd1234.log")))

    def test_missing_log_path_is_skipped(self):
        result = {"results": [{"intermediate_artifacts": [{"path": "/no/such/file.log"}]}]}
        with open(os.path.join(self.output_dir, "execution_result.json"), "w", encoding="utf-8") as f:
            json.dump(result, f)
        saved = self.server._harvest_log_artifacts("b" * 32, self.output_dir, [])
        self.assertEqual(saved, [])

    def test_collision_safe_naming_against_existing_artifact(self):
        log_path = self._write_log("report.log")
        with open(os.path.join(self.output_dir, "bounded_closed_loop_result.json"), "w", encoding="utf-8") as f:
            json.dump({"final": {"log": log_path}}, f)
        # Pretend _persist_artifacts already saved a file named report.log.
        saved = self.server._harvest_log_artifacts("c" * 32, self.output_dir, [{"name": "report.log", "size": 1}])
        self.assertTrue(saved)
        self.assertNotEqual(saved[0]["name"], "report.log")  # renamed to avoid clobber


# ----------------------------------------------------------- P3: artifact path safety
@unittest.skipUnless(_HAS_FLASK, "Flask not installed; server path-boundary test skipped")
class LogArtifactPathBoundaryTests(unittest.TestCase):
    """The result/timeline JSON is untrusted (model output + user input). _harvest_log_artifacts
    must only copy .log/.out that resolve INSIDE this run's controlled roots (work_dir). Arbitrary
    absolute paths, symlinks, path-traversal escapes and other runs' job dirs must be rejected —
    they must never become downloadable artifacts."""

    # Clear env vars that widen the allow-list so tests see only this run's work_dir.
    _ROOT_ENV = ("GAUSSIAN_JOB_ROOT", "ARCHE_DETERMINISTIC_DIR", "ARCHE_LOG_HARVEST_ROOTS")

    def setUp(self):
        root = str(PROJECT_ROOT)
        if root not in sys.path:
            sys.path.insert(0, root)
        import server  # noqa: WPS433
        self.server = server

        self._saved_env = {k: os.environ.pop(k, None) for k in self._ROOT_ENV}

        self._artifacts = tempfile.TemporaryDirectory()
        self._orig_artifacts_dir = server.ARTIFACTS_DIR
        server.ARTIFACTS_DIR = self._artifacts.name

        self._work = tempfile.TemporaryDirectory(prefix="arche-run-")
        self.output_dir = os.path.join(self._work.name, "outputs", "multiagent")
        os.makedirs(self.output_dir, exist_ok=True)
        self._outside = tempfile.TemporaryDirectory(prefix="outside-")
        self._other_run = tempfile.TemporaryDirectory(prefix="arche-run-")

        self.run_id = "a1b2c3d4" * 4  # 32 hex chars

    def tearDown(self):
        self.server.ARTIFACTS_DIR = self._orig_artifacts_dir
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._artifacts.cleanup()
        self._work.cleanup()
        self._outside.cleanup()
        self._other_run.cleanup()

    @staticmethod
    def _write(path: str, content: str = "Normal termination of Gaussian\n") -> str:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path

    def _reference(self, path: str, json_name: str = "execution_result.json") -> None:
        """Make `path` appear as a recorded artifact in one of the scanned result JSONs."""
        payload = {"results": [{"intermediate_artifacts": [{"type": "output_file", "path": path}]}]}
        with open(os.path.join(self.output_dir, json_name), "w", encoding="utf-8") as f:
            json.dump(payload, f)

    def _harvest(self):
        return self.server._harvest_log_artifacts(self.run_id, self.output_dir, [])

    def _artifact_names(self):
        d = os.path.join(self._artifacts.name, self.run_id)
        return set(os.listdir(d)) if os.path.isdir(d) else set()

    # --- rejections -----------------------------------------------------------------
    def test_external_absolute_path_is_rejected(self):
        ext = self._write(os.path.join(self._outside.name, "secret.log"))
        self._reference(ext)
        self.assertEqual(self._harvest(), [])
        self.assertEqual(self._artifact_names(), set())

    def test_symlink_to_external_file_is_rejected(self):
        target = self._write(os.path.join(self._outside.name, "target.log"))
        link = os.path.join(self._work.name, "inside_link.log")  # symlink sits inside work_dir
        os.symlink(target, link)
        self._reference(link)
        self.assertEqual(self._harvest(), [])
        self.assertEqual(self._artifact_names(), set())

    def test_other_run_job_dir_is_rejected(self):
        other = self._write(os.path.join(self._other_run.name, "gaussian_jobs", "mol.log"))
        self._reference(other)
        self.assertEqual(self._harvest(), [])
        self.assertEqual(self._artifact_names(), set())

    def test_path_traversal_escape_is_rejected(self):
        ext = self._write(os.path.join(self._outside.name, "escape.out"))
        # A path that lexically starts under work_dir but normalizes outside it.
        traversal = os.path.join(self.output_dir, "..", "..", "..", os.path.basename(self._outside.name), "escape.out")
        self.assertTrue(os.path.isfile(os.path.realpath(traversal)))  # the escape really points at ext
        self._reference(traversal)
        self.assertEqual(self._harvest(), [])
        self.assertEqual(self._artifact_names(), set())

    def test_multiagent_log_json_paths_are_constrained_too(self):
        ext = self._write(os.path.join(self._outside.name, "from_timeline.log"))
        # Arbitrary paths injected through timeline JSON are constrained the same way.
        self._reference(ext, json_name="multiagent_log.json")
        self.assertEqual(self._harvest(), [])
        self.assertEqual(self._artifact_names(), set())

    def test_embedded_nul_path_is_rejected_without_crashing(self):
        # A NUL byte makes os.path.realpath raise ValueError; harvest must reject it.
        good = self._write(os.path.join(self._work.name, "gaussian_jobs", "ok.log"))
        payload = {"results": [
            {"intermediate_artifacts": [{"path": "/tmp/evil\x00.log"}]},
            {"intermediate_artifacts": [{"path": good}]},
        ]}
        with open(os.path.join(self.output_dir, "execution_result.json"), "w", encoding="utf-8") as f:
            json.dump(payload, f)
        saved = self._harvest()
        self.assertEqual({e["name"] for e in saved}, {"ok.log"})

    def test_deep_result_json_does_not_crash_or_skip_valid_sibling(self):
        good = self._write(os.path.join(self._work.name, "gaussian_jobs", "safe.log"))
        nested = {}
        cursor = nested
        for _ in range(self.server._LOG_SCAN_JSON_MAX_DEPTH + 20):
            child = {}
            cursor["next"] = child
            cursor = child
        payload = {"deep": nested, "valid": {"path": good}}
        with open(os.path.join(self.output_dir, "execution_result.json"), "w", encoding="utf-8") as f:
            json.dump(payload, f)
        saved = self._harvest()
        self.assertEqual({e["name"] for e in saved}, {"safe.log"})

    def test_operator_shared_job_root_is_scoped_to_this_run(self):
        # Shared/cluster roots trust only this run's per-run subdir.
        shared = tempfile.mkdtemp(prefix="shared-jobs-")
        self.addCleanup(shutil.rmtree, shared, ignore_errors=True)
        os.environ["GAUSSIAN_JOB_ROOT"] = shared

        mine = self._write(os.path.join(shared, self.run_id, "mine.log"))
        other_run_id = "f" * 32
        theirs = self._write(os.path.join(shared, other_run_id, "theirs.log"))

        payload = {"results": [{"intermediate_artifacts": [{"path": mine}, {"path": theirs}]}]}
        with open(os.path.join(self.output_dir, "execution_result.json"), "w", encoding="utf-8") as f:
            json.dump(payload, f)
        saved = self._harvest()
        self.assertEqual({e["name"] for e in saved}, {"mine.log"})
        self.assertNotIn("theirs.log", self._artifact_names())

    # --- positive control (the feature still works) ---------------------------------
    def test_log_inside_run_jobdir_is_harvested(self):
        good = self._write(os.path.join(self._work.name, "gaussian_jobs", "deterministic", "det_1234abcd.log"))
        self._reference(good)
        saved = self._harvest()
        self.assertEqual({e["name"] for e in saved}, {"det_1234abcd.log"})
        self.assertIn("det_1234abcd.log", self._artifact_names())


if __name__ == "__main__":
    unittest.main(verbosity=2)
