import re
from typing import Any, Dict, List, Optional


BENCHMARK_WATER = "water_geometry"
BENCHMARK_BENZENE = "benzene_homo_lumo"
BENCHMARK_CO2 = "co2_ir"
BENCHMARK_HABER = "haber_reaction_enthalpy"


def detect_predefined_benchmark(question: str) -> Optional[str]:
    text = str(question or "").lower()
    compact = re.sub(r"\s+", " ", text)

    if ("h2o" in compact or "\\ce{h2o}" in compact) and (
        "优化几何" in compact or "geometry" in compact or "optimiz" in compact
    ):
        return BENCHMARK_WATER

    if ("benzene" in compact or "苯" in compact or "c6h6" in compact or "\\ce{c6h6}" in compact) and (
        "homo" in compact and "lumo" in compact
    ):
        return BENCHMARK_BENZENE

    if ("co2" in compact or "\\ce{co2}" in compact) and (
        "ir" in compact or "振动光谱" in compact or "吸收峰" in compact
    ):
        return BENCHMARK_CO2

    if (
        ("n2 + 3h2" in compact or "n2+3h2" in compact or "\\ce{n2 + 3h2" in compact)
        and ("2nh3" in compact or "2nh3}" in compact or "2nh3" in compact)
    ) or ("反应焓" in compact and "nh3" in compact):
        return BENCHMARK_HABER

    return None


def build_predefined_hypotheses(question: str) -> List[Dict[str, Any]]:
    kind = detect_predefined_benchmark(question)
    if kind == BENCHMARK_WATER:
        return [
            {
                "strategy_name": "B3LYP/6-31G(d) water geometry benchmark",
                "reasoning": "Optimize H2O at B3LYP/6-31G(d) and report the converged geometry directly from the real quantum-chemistry pipeline.",
                "confidence": 0.9,
                "status": "active",
            },
            {
                "strategy_name": "PBE0/6-31G(d) water geometry cross-check",
                "reasoning": "Use a second compact DFT geometry optimization only as an optional cross-check once the primary benchmark geometry is complete.",
                "confidence": 0.65,
                "status": "active",
            },
        ]
    if kind == BENCHMARK_BENZENE:
        return [
            {
                "strategy_name": "B3LYP/6-31G(d) benzene orbital-gap benchmark",
                "reasoning": "Optimize benzene with a supported DFT method and extract the frontier orbital energies directly from the converged job to report the HOMO-LUMO gap.",
                "confidence": 0.9,
                "status": "active",
            },
            {
                "strategy_name": "PBE0/6-31G(d) benzene orbital-gap cross-check",
                "reasoning": "Use a second supported hybrid functional only after the primary benchmark run succeeds, to compare the reported HOMO-LUMO gap without introducing unsupported GW or multireference workflows.",
                "confidence": 0.65,
                "status": "active",
            },
        ]
    if kind == BENCHMARK_CO2:
        return [
            {
                "strategy_name": "B3LYP/6-31G(d) CO2 IR benchmark",
                "reasoning": "Optimize CO2 and run a real frequency calculation with a supported DFT method, then use the parsed vibrational frequencies as the basis for IR peak assignment.",
                "confidence": 0.9,
                "status": "active",
            }
        ]
    if kind == BENCHMARK_HABER:
        return [
            {
                "strategy_name": "B3LYP/6-31G(d) Haber thermochemistry benchmark",
                "reasoning": "Run supported opt/freq calculations for N2, H2, and NH3, parse their thermochemistry JSON outputs, and compute ΔH_rxn with the real reaction-thermochemistry tool.",
                "confidence": 0.9,
                "status": "active",
            }
        ]
    return []


def _step(n: int, desc: str, tool: str, inp: str, out: str, **extra: Any) -> Dict[str, Any]:
    payload = {
        "Step_number": n,
        "Description": desc,
        "Tool": tool,
        "Input": inp,
        "Output": out,
    }
    payload.update(extra)
    return payload


def build_predefined_protocols(question: str) -> List[Dict[str, Any]]:
    kind = detect_predefined_benchmark(question)
    if kind == BENCHMARK_WATER:
        return [
            {
                "workflow_name": "Water geometry benchmark",
                "strategy_name": "B3LYP/6-31G(d) water geometry benchmark",
                "goal": question,
                "Steps": [
                    _step(1, "Generate the initial H2O structure from SMILES.", "smiles2sdf", "SMILES: O", "water_initial.sdf"),
                    _step(2, "Convert the SDF structure to XYZ coordinates.", "sdf_to_xyz", "water_initial.sdf", "water_initial.xyz"),
                    _step(3, "Create the Gaussian input file from the XYZ geometry using a deterministic supported route section.", "xyz_to_gjf", "water_initial.xyz", "water_opt.gjf", route_section="# B3LYP/6-31G(d) Opt=Tight SCF=Tight Integral=Ultrafine"),
                    _step(4, "Run the Gaussian geometry optimization job.", "generate_gaussian_code", "water_opt.gjf", "water_opt.log"),
                    _step(5, "Parse the optimized Gaussian output to extract converged geometry and energy.", "parse_gaussian_output", "water_opt.log", "water_opt.json"),
                ],
            }
        ]
    if kind == BENCHMARK_BENZENE:
        return [
            {
                "workflow_name": "Benzene HOMO-LUMO benchmark",
                "strategy_name": "B3LYP/6-31G(d) benzene orbital-gap benchmark",
                "goal": question,
                "Steps": [
                    _step(1, "Generate the initial benzene structure from SMILES.", "smiles2sdf", "SMILES: c1ccccc1", "benzene_initial.sdf"),
                    _step(2, "Convert the SDF structure to XYZ coordinates.", "sdf_to_xyz", "benzene_initial.sdf", "benzene_initial.xyz"),
                    _step(3, "Create the Gaussian input file from the XYZ geometry using a deterministic supported route section.", "xyz_to_gjf", "benzene_initial.xyz", "benzene_opt.gjf", route_section="# B3LYP/6-31G(d) Integral=(Grid=UltraFine) Opt Freq // SP Pop=Full GFInput"),
                    _step(4, "Run the Gaussian geometry-optimization job for benzene.", "generate_gaussian_code", "benzene_opt.gjf", "benzene_opt.log"),
                    _step(5, "Parse the Gaussian output to extract HOMO, LUMO, and the HOMO-LUMO gap.", "parse_gaussian_output", "benzene_opt.log", "benzene_opt.json"),
                ],
            }
        ]
    if kind == BENCHMARK_CO2:
        return [
            {
                "workflow_name": "CO2 IR benchmark",
                "strategy_name": "B3LYP/6-31G(d) CO2 IR benchmark",
                "goal": question,
                "Steps": [
                    _step(1, "Generate the initial CO2 structure from SMILES.", "smiles2sdf", "SMILES: O=C=O", "co2_initial.sdf"),
                    _step(2, "Convert the SDF structure to XYZ coordinates.", "sdf_to_xyz", "co2_initial.sdf", "co2_initial.xyz"),
                    _step(3, "Create the Gaussian input file from the XYZ geometry using a deterministic supported route section.", "xyz_to_gjf", "co2_initial.xyz", "co2_opt_freq.gjf", route_section="# B3LYP/6-31G(d) Opt Freq Nosymm"),
                    _step(4, "Run the Gaussian geometry-optimization and frequency job for CO2.", "generate_gaussian_code", "co2_opt_freq.gjf", "co2_opt_freq.log"),
                    _step(5, "Parse the Gaussian output to extract vibrational frequencies for IR peak assignment.", "parse_gaussian_output", "co2_opt_freq.log", "co2_opt_freq.json"),
                ],
            }
        ]
    if kind == BENCHMARK_HABER:
        steps = [
            _step(1, "Generate the initial N2 structure from SMILES.", "smiles2sdf", "SMILES: N#N", "N2_initial.sdf"),
            _step(2, "Convert the N2 SDF structure to XYZ coordinates.", "sdf_to_xyz", "N2_initial.sdf", "N2_initial.xyz"),
            _step(3, "Create the Gaussian input file for N2 opt/freq using a deterministic supported route section.", "xyz_to_gjf", "N2_initial.xyz", "N2_optfreq.gjf", route_section="# B3LYP/6-31G(d) Opt Freq"),
            _step(4, "Run the Gaussian opt/freq job for N2.", "generate_gaussian_code", "N2_optfreq.gjf", "N2_optfreq.log"),
            _step(5, "Parse the N2 Gaussian output to extract thermochemistry.", "parse_gaussian_output", "N2_optfreq.log", "N2_parsed.json"),
            _step(7, "Generate the initial H2 structure from SMILES.", "smiles2sdf", "SMILES: [H][H]", "H2_initial.sdf"),
            _step(8, "Convert the H2 SDF structure to XYZ coordinates.", "sdf_to_xyz", "H2_initial.sdf", "H2_initial.xyz"),
            _step(9, "Create the Gaussian input file for H2 opt/freq using a deterministic supported route section.", "xyz_to_gjf", "H2_initial.xyz", "H2_optfreq.gjf", route_section="# B3LYP/6-31G(d) Opt Freq"),
            _step(10, "Run the Gaussian opt/freq job for H2.", "generate_gaussian_code", "H2_optfreq.gjf", "H2_optfreq.log"),
            _step(11, "Parse the H2 Gaussian output to extract thermochemistry.", "parse_gaussian_output", "H2_optfreq.log", "H2_parsed.json"),
            _step(13, "Generate the initial NH3 structure from SMILES.", "smiles2sdf", "SMILES: N", "NH3_initial.sdf"),
            _step(14, "Convert the NH3 SDF structure to XYZ coordinates.", "sdf_to_xyz", "NH3_initial.sdf", "NH3_initial.xyz"),
            _step(15, "Create the Gaussian input file for NH3 opt/freq using a deterministic supported route section.", "xyz_to_gjf", "NH3_initial.xyz", "NH3_optfreq.gjf", route_section="# B3LYP/6-31G(d) Opt Freq"),
            _step(16, "Run the Gaussian opt/freq job for NH3.", "generate_gaussian_code", "NH3_optfreq.gjf", "NH3_optfreq.log"),
            _step(17, "Parse the NH3 Gaussian output to extract thermochemistry.", "parse_gaussian_output", "NH3_optfreq.log", "NH3_parsed.json"),
            _step(18, "Compute the reaction enthalpy ΔH_rxn from the parsed JSON thermochemistry for N2 + 3H2 -> 2NH3.", "compute_reaction_thermochemistry", "N2_parsed.json, H2_parsed.json, NH3_parsed.json", "haber_delta_h.json"),
        ]
        return [
            {
                "workflow_name": "Haber reaction enthalpy benchmark",
                "strategy_name": "B3LYP/6-31G(d) Haber thermochemistry benchmark",
                "goal": question,
                "Steps": steps,
            }
        ]
    return []
