import argparse
import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple


HARTREE_TO_KJ_MOL = 2625.49962


def _load_payload(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    if isinstance(raw, dict) and isinstance(raw.get("result"), dict):
        return dict(raw["result"])
    if isinstance(raw, dict):
        return dict(raw)
    raise ValueError(f"JSON payload is not an object: {path}")


def _as_float(value: Any) -> Optional[float]:
    try:
        if isinstance(value, list):
            if not value:
                return None
            return float(value[-1])
        return float(value)
    except Exception:
        return None


def _basis_rank(text: str) -> int:
    raw = (text or "").lower()
    for token, rank in (
        ("v5z", 5),
        ("qz", 4),
        ("vqz", 4),
        ("aqz", 4),
        ("tz", 3),
        ("vtz", 3),
        ("atz", 3),
        ("dz", 2),
        ("vdz", 2),
        ("adz", 2),
    ):
        if token in raw:
            return rank
    return 0


def _infer_species_label(path: str, payload: Dict[str, Any]) -> str:
    metadata = payload.get("metadata", {}) if isinstance(payload.get("metadata"), dict) else {}
    for source in (
        metadata.get("label"),
        metadata.get("species"),
        metadata.get("filename"),
        os.path.basename(path),
    ):
        text = str(source or "")
        for candidate in re.findall(r"[A-Z][A-Za-z]?\d*(?:[A-Z][A-Za-z]?\d*)*", text):
            if candidate and candidate.lower() not in {"json", "parsed"}:
                return candidate
    raise ValueError(f"Cannot infer species label from {path}")


def _extract_energetics(payload: Dict[str, Any]) -> Tuple[Optional[float], Optional[float], int]:
    metadata = payload.get("metadata", {}) if isinstance(payload.get("metadata"), dict) else {}
    basis_text = " ".join(
        str(x or "")
        for x in (
            metadata.get("basis_set"),
            metadata.get("basis"),
            metadata.get("filename"),
        )
    )
    basis_rank = _basis_rank(basis_text)

    enthalpy = _as_float(payload.get("enthalpy"))
    if enthalpy is None:
        enthalpy = _as_float(payload.get("H_tot"))

    electronic = _as_float(payload.get("scf_energy"))
    if electronic is None:
        electronic = _as_float(payload.get("scf_energies"))
    if electronic is None:
        electronic = _as_float(payload.get("hf_energy"))

    return enthalpy, electronic, basis_rank


def _clean_reaction_text(text: str) -> str:
    raw = str(text or "")
    ce = re.search(r"\\ce\{([^}]*)\}", raw)
    if ce:
        raw = ce.group(1)
    raw = raw.replace("$", " ")
    raw = raw.replace("<=>", "->").replace("=>", "->").replace("→", "->")
    raw = re.sub(r"\\Delta\s*H(?:_\{[^}]+\})?", " ", raw)
    raw = re.sub(r"ΔH(?:_[A-Za-z{}]+)?", " ", raw)
    return re.sub(r"\s+", " ", raw).strip()


def _parse_stoichiometry(reaction_expression: str) -> Dict[str, int]:
    text = _clean_reaction_text(reaction_expression)
    if "->" not in text:
        raise ValueError(f"Cannot parse reaction expression: {reaction_expression}")
    left, right = [part.strip() for part in text.split("->", 1)]

    def parse_side(side: str, sign: int) -> Dict[str, int]:
        result: Dict[str, int] = {}
        for token in side.split("+"):
            chunk = token.strip()
            if not chunk:
                continue
            match = re.match(r"(?:(\d+)\s*)?([A-Z][A-Za-z]?\d*(?:[A-Z][A-Za-z]?\d*)*)", chunk)
            if not match:
                continue
            coeff = int(match.group(1) or "1")
            species = match.group(2)
            result[species] = result.get(species, 0) + sign * coeff
        return result

    stoich: Dict[str, int] = {}
    for partial in (parse_side(left, -1), parse_side(right, 1)):
        for species, coeff in partial.items():
            stoich[species] = stoich.get(species, 0) + coeff
    if not stoich:
        raise ValueError(f"Stoichiometry is empty for reaction: {reaction_expression}")
    return stoich


def _cbs_extrapolate(points: List[Tuple[int, float]]) -> Optional[float]:
    ranked = sorted((p for p in points if p[0] > 0), key=lambda item: item[0])
    if len(ranked) < 2:
        return None
    (low_n, low_e), (high_n, high_e) = ranked[-2], ranked[-1]
    power = 3
    denom = high_n**power - low_n**power
    if denom == 0:
        return None
    return (high_e * high_n**power - low_e * low_n**power) / denom


def compute_reaction_thermochemistry(
    input_file_paths: List[str],
    reaction_expression: str,
    output_json_path: Optional[str] = None,
) -> Dict[str, Any]:
    if not input_file_paths:
        raise ValueError("input_file_paths is required")

    stoichiometry = _parse_stoichiometry(reaction_expression)
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for path in input_file_paths:
        payload = _load_payload(path)
        species = _infer_species_label(path, payload)
        enthalpy, electronic, basis_rank = _extract_energetics(payload)
        grouped.setdefault(species, []).append(
            {
                "path": path,
                "enthalpy": enthalpy,
                "electronic_energy": electronic,
                "basis_rank": basis_rank,
            }
        )

    species_summary: Dict[str, Dict[str, Any]] = {}
    delta_h_hartree = 0.0
    for species, coeff in stoichiometry.items():
        entries = grouped.get(species, [])
        if not entries:
            raise ValueError(f"Missing parsed thermochemistry for species: {species}")

        enthalpy_entries = [entry for entry in entries if entry.get("enthalpy") is not None]
        enthalpy_entry = max(enthalpy_entries, key=lambda item: item.get("basis_rank", 0), default=None)
        enthalpy_value = enthalpy_entry.get("enthalpy") if enthalpy_entry else None

        cbs_points = [
            (entry["basis_rank"], entry["electronic_energy"])
            for entry in entries
            if entry.get("electronic_energy") is not None and entry.get("basis_rank", 0) > 0
        ]
        cbs_energy = _cbs_extrapolate(cbs_points)

        thermal_correction = None
        if enthalpy_entry and enthalpy_entry.get("electronic_energy") is not None and enthalpy_value is not None:
            thermal_correction = enthalpy_value - enthalpy_entry["electronic_energy"]

        if cbs_energy is not None and thermal_correction is not None:
            total_enthalpy = cbs_energy + thermal_correction
            method = "cbs_plus_thermal_correction"
        elif enthalpy_value is not None:
            total_enthalpy = enthalpy_value
            method = "direct_enthalpy"
        elif cbs_energy is not None:
            total_enthalpy = cbs_energy
            method = "cbs_electronic_only"
        else:
            direct_electronic = max(
                (entry for entry in entries if entry.get("electronic_energy") is not None),
                key=lambda item: item.get("basis_rank", 0),
                default=None,
            )
            if direct_electronic is None:
                raise ValueError(f"Missing usable energetics for species: {species}")
            total_enthalpy = direct_electronic["electronic_energy"]
            method = "direct_electronic_only"

        delta_h_hartree += coeff * total_enthalpy
        species_summary[species] = {
            "stoichiometric_coefficient": coeff,
            "total_enthalpy_hartree": total_enthalpy,
            "thermal_correction_hartree": thermal_correction,
            "cbs_electronic_energy_hartree": cbs_energy,
            "method": method,
            "input_files": [entry["path"] for entry in entries],
        }

    result = {
        "success": True,
        "reaction_expression": reaction_expression,
        "delta_h_rxn_hartree": delta_h_hartree,
        "delta_h_rxn_kj_mol": delta_h_hartree * HARTREE_TO_KJ_MOL,
        "species_summary": species_summary,
    }

    if output_json_path:
        os.makedirs(os.path.dirname(output_json_path), exist_ok=True)
        with open(output_json_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        result["output_json_path"] = output_json_path

    return result


run_tool = compute_reaction_thermochemistry


def _build_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compute reaction thermochemistry from parsed JSON files")
    parser.add_argument("--inputs", nargs="+", required=True, help="Parsed JSON input files")
    parser.add_argument("--reaction", required=True, help="Reaction expression, e.g. N2 + 3H2 -> 2NH3")
    parser.add_argument("--output", default=None, help="Optional output JSON file path")
    return parser


def main() -> None:
    args = _build_cli_parser().parse_args()
    result = compute_reaction_thermochemistry(
        input_file_paths=args.inputs,
        reaction_expression=args.reaction,
        output_json_path=args.output,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
