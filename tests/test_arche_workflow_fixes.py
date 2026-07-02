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

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


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
        self.assertEqual(steps[2]["Tool"], "generate_gaussian_code")
        self.assertEqual(steps[3]["Tool"], "generate_gaussian_code")

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
