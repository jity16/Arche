import json
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


try:
    from pyscf import cc, dft, gto, mp, scf
    from pyscf.geomopt.geometric_solver import optimize
    from pyscf.hessian import thermo

    PYSCF_AVAILABLE = True
except Exception:  # pragma: no cover - import depends on environment
    PYSCF_AVAILABLE = False


@dataclass
class PySCFJobSpec:
    route_line: str
    title: str
    charge: int
    multiplicity: int
    atom_lines: List[str]
    method: str
    basis: str
    do_opt: bool
    do_freq: bool
    raw_route: str


_METHOD_ALIASES = {
    "b3lyp": "b3lyp",
    "pbe0": "pbe0",
    "m06-2x": "m06-2x",
    "m062x": "m06-2x",
    "hf": "hf",
    "rhf": "hf",
    "uhf": "hf",
    "mp2": "mp2",
    "ccsd(t)": "ccsd(t)",
    "ccsdt": "ccsd(t)",
}


def pyscf_available() -> bool:
    return PYSCF_AVAILABLE


def _normalize_basis(text: str) -> str:
    basis = text.strip()
    while basis.endswith(")") and basis.count("(") < basis.count(")"):
        basis = basis[:-1].rstrip()
    basis = basis.rstrip(",;")
    basis = basis.replace("6-31g*", "6-31g(d)")
    basis = basis.replace("6-31G*", "6-31g(d)")
    basis = basis.replace("6-31+g*", "6-31+g(d)")
    basis = basis.replace("6-31+G*", "6-31+g(d)")
    basis = basis.replace("6-311+g*", "6-311+g(d)")
    basis = basis.replace("6-311+G*", "6-311+g(d)")
    basis = basis.replace("6-311++g**", "6-311++g(d,p)")
    basis = basis.replace("6-311++G**", "6-311++g(d,p)")
    return basis


def parse_gjf(gjf_path: str) -> PySCFJobSpec:
    with open(gjf_path, "r", encoding="utf-8") as f:
        lines = [line.rstrip("\n") for line in f]

    route_idx = next((i for i, line in enumerate(lines) if line.lstrip().startswith("#")), None)
    if route_idx is None:
        raise ValueError("GJF缺少route line")
    route_line = lines[route_idx].strip()
    body = lines[route_idx + 1 :]

    while body and not body[0].strip():
        body = body[1:]
    title = body[0].strip() if body else "PySCF job"
    body = body[1:] if body else []
    while body and not body[0].strip():
        body = body[1:]
    if not body:
        raise ValueError("GJF缺少电荷/多重度行")
    charge_mult = body[0].split()
    if len(charge_mult) < 2:
        raise ValueError("GJF电荷/多重度行无效")
    charge = int(charge_mult[0])
    multiplicity = int(charge_mult[1])
    atom_lines = []
    for line in body[1:]:
        if not line.strip():
            break
        atom_lines.append(line.strip())
    if not atom_lines:
        raise ValueError("GJF缺少原子坐标")

    route = route_line.lower().replace("#p", " ").replace("#", " ")
    mb = re.search(r"([a-z0-9+\-\(\)]+)\s*/\s*([a-z0-9+\-\(\),*]+)", route, re.I)
    if not mb:
        raise ValueError(f"无法从route解析方法/基组: {route_line}")
    raw_method = mb.group(1).lower()
    method = _METHOD_ALIASES.get(raw_method)
    if not method:
        raise ValueError(f"PySCF后端暂不支持该方法: {raw_method}")
    basis = _normalize_basis(mb.group(2))

    do_opt = bool(re.search(r"\bopt\b|optimization", route))
    do_freq = bool(re.search(r"\bfreq\b|frequency", route))
    return PySCFJobSpec(
        route_line=route_line,
        title=title,
        charge=charge,
        multiplicity=multiplicity,
        atom_lines=atom_lines,
        method=method,
        basis=basis,
        do_opt=do_opt,
        do_freq=do_freq,
        raw_route=route,
    )


def _build_molecule(spec: PySCFJobSpec):
    atom_block = "\n".join(spec.atom_lines)
    spin = max(0, spec.multiplicity - 1)
    return gto.M(atom=atom_block, basis=spec.basis, charge=spec.charge, spin=spin, unit="Angstrom", verbose=0)


def _build_meanfield(mol, method: str):
    if method in {"b3lyp", "pbe0", "m06-2x"}:
        mf = dft.RKS(mol) if mol.spin == 0 else dft.UKS(mol)
        mf.xc = method
        return mf
    if method == "hf":
        return scf.RHF(mol) if mol.spin == 0 else scf.UHF(mol)
    raise ValueError(f"均场方法不支持: {method}")


def _extract_homo_lumo(mf, mol) -> tuple[Optional[float], Optional[float]]:
    try:
        nocc = mol.nelectron // 2
        if nocc <= 0 or nocc >= len(mf.mo_energy):
            return None, None
        return float(mf.mo_energy[nocc - 1]), float(mf.mo_energy[nocc])
    except Exception:
        return None, None


def _thermo_value(obj: Any) -> Optional[float]:
    if isinstance(obj, tuple) and obj:
        obj = obj[0]
    try:
        return float(obj)
    except Exception:
        return None


def run_pyscf_job(gjf_path: str) -> Dict[str, Any]:
    if not PYSCF_AVAILABLE:
        raise RuntimeError("PySCF不可用")

    spec = parse_gjf(gjf_path)
    mol = _build_molecule(spec)
    backend = spec.method

    if spec.method in {"b3lyp", "pbe0", "m06-2x", "hf"}:
        mf = _build_meanfield(mol, spec.method)
        if spec.do_opt:
            mol = optimize(mf, maxsteps=50)
            mf = _build_meanfield(mol, spec.method)
        energy = float(mf.kernel())
        homo, lumo = _extract_homo_lumo(mf, mol)
        freqs = []
        ir_intensities: list[float] = []
        enthalpy = None
        free_energy = None
        zpve = None
        if spec.do_freq:
            hess = mf.Hessian().kernel()
            freq_info = thermo.harmonic_analysis(mol, hess)
            freqs = [float(v) for v in freq_info["freq_wavenumber"]]
            thermo_info = thermo.thermo(mf, freq_info["freq_au"], 298.15, 101325)
            enthalpy = _thermo_value(thermo_info.get("H_tot"))
            free_energy = _thermo_value(thermo_info.get("G_tot"))
            zpve = _thermo_value(thermo_info.get("ZPE"))
        return {
            "backend": backend,
            "route_line": spec.route_line,
            "charge": spec.charge,
            "mult": spec.multiplicity,
            "scf_energies": energy,
            "enthalpy": enthalpy,
            "free_energy": free_energy,
            "zpve": zpve,
            "elements": [mol.atom_symbol(i) for i in range(mol.natm)],
            "coordinates": [mol.atom_coords(unit="Angstrom").tolist()],
            "frequencies": freqs,
            "ir_intensities": ir_intensities,
            "opt_done": True,
            "normal_termination": True,
            "job_type": "freq" if spec.do_freq else "opt" if spec.do_opt else "sp",
            "E_HOMO": homo,
            "E_LUMO": lumo,
            "HOMO_LUMO_gap": (lumo - homo) if homo is not None and lumo is not None else None,
            "metadata": {
                "backend": "pyscf_local",
                "method": spec.method,
                "basis_set": spec.basis,
                "success": True,
            },
        }

    mf = scf.RHF(mol) if mol.spin == 0 else scf.UHF(mol)
    ehf = float(mf.kernel())
    homo, lumo = _extract_homo_lumo(mf, mol)
    if spec.method == "mp2":
        corr = mp.MP2(mf).run()
        energy = float(corr.e_tot)
    elif spec.method == "ccsd(t)":
        mycc = cc.CCSD(mf).run()
        energy = float(mycc.e_tot + mycc.ccsd_t())
    else:  # pragma: no cover - guarded by parser
        raise ValueError(f"相关方法不支持: {spec.method}")
    return {
        "backend": backend,
        "route_line": spec.route_line,
        "charge": spec.charge,
        "mult": spec.multiplicity,
        "scf_energies": energy,
        "hf_energy": ehf,
        "elements": [mol.atom_symbol(i) for i in range(mol.natm)],
        "coordinates": [mol.atom_coords(unit="Angstrom").tolist()],
        "frequencies": [],
        "ir_intensities": [],
        "opt_done": True,
        "normal_termination": True,
        "job_type": "sp",
        "E_HOMO": homo,
        "E_LUMO": lumo,
        "HOMO_LUMO_gap": (lumo - homo) if homo is not None and lumo is not None else None,
        "metadata": {
            "backend": "pyscf_local",
            "method": spec.method,
            "basis_set": spec.basis,
            "success": True,
        },
    }


def dump_pyscf_log(result: Dict[str, Any], log_path: str) -> None:
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
