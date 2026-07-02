import argparse
import json
import math
import os
from typing import Dict, Iterable, List, Optional, Tuple

from PIL import Image, ImageDraw
from rdkit import Chem
from rdkit.Chem import AllChem, Draw
from rdkit.Chem.rdMolAlign import AlignMol


def make_error_image(msg: str, size: Tuple[int, int] = (600, 200)) -> Image.Image:
    img = Image.new("RGB", size, color="white")
    drawer = ImageDraw.Draw(img)
    try:
        drawer.text((10, 10), msg, fill="black")
    except Exception:
        pass
    return img


def _optimize_conformers(
    mol,
    num_conformers: int,
    max_iter: int,
    random_seed: int,
) -> Tuple[List[Tuple[int, float]], int]:
    params = AllChem.ETKDGv3()
    params.randomSeed = random_seed
    cids = list(AllChem.EmbedMultipleConfs(mol, numConfs=num_conformers, params=params))
    if not cids:
        return [], 0

    energies: List[Tuple[int, float]] = []
    failed_optimizations = 0
    for cid in cids:
        try:
            AllChem.MMFFOptimizeMolecule(mol, confId=cid, maxIters=max_iter)
            mmff_props = AllChem.MMFFGetMoleculeProperties(mol)
            ff = AllChem.MMFFGetMoleculeForceField(mol, mmff_props, confId=cid)
            energies.append((cid, float(ff.CalcEnergy())))
        except Exception:
            failed_optimizations += 1
            energies.append((cid, float("inf")))

    energies.sort(key=lambda item: item[1])
    return energies, failed_optimizations


def _save_top_conformer_sdfs(
    mol,
    selected: List[Tuple[int, float]],
    output_dir: str,
    sdf_prefix: str,
) -> List[str]:
    os.makedirs(output_dir, exist_ok=True)
    saved_paths: List[str] = []

    for index, (cid, energy) in enumerate(selected, start=1):
        sdf_path = os.path.join(output_dir, f"{sdf_prefix}{index}.sdf")
        writer = Chem.SDWriter(sdf_path)
        mol.SetProp("Conformer_ID", f"{sdf_prefix}{index}")
        mol.SetProp("Energy_kcal_per_mol", f"{energy:.6f}")
        writer.write(mol, confId=cid)
        writer.close()
        saved_paths.append(sdf_path)

    return saved_paths


def _save_grid_image(
    mol,
    selected: List[Tuple[int, float]],
    image_output_path: str,
) -> str:
    top_cids = [cid for cid, _ in selected]
    try:
        img = Draw.MolsToGridImage(
            [mol] * len(top_cids),
            molsPerRow=min(3, len(top_cids)),
            subImgSize=(300, 300),
            legends=[f"conf {idx}: {energy:.2f} kcal/mol" for idx, (_, energy) in enumerate(selected, start=1)],
            confIds=top_cids,
        )
        if img is None:
            img = make_error_image("Failed to render conformer image")
    except Exception:
        img = make_error_image("Failed to render conformer image")

    os.makedirs(os.path.dirname(image_output_path) or ".", exist_ok=True)
    img.save(image_output_path)
    return image_output_path


def generate_conformations(
    smiles: str,
    num_conformers: int = 50,
    max_iter: int = 200,
    top_n: int = 5,
    output_dir: Optional[str] = None,
    sdf_prefix: str = "conf_",
    random_seed: int = 42,
    save_image: bool = False,
    image_output_path: Optional[str] = None,
) -> Dict:
    """
    Name: generate_conformations
    Description: Generate, optimize, rank, and optionally save top conformers for one SMILES string.
    Parameters:
    smiles: str Input SMILES.
    num_conformers: int Number of conformers to generate.
    max_iter: int Maximum MMFF optimization iterations.
    top_n: int Number of best conformers to keep.
    output_dir: Optional[str] Directory to save top conformer SDF files.
    sdf_prefix: str Prefix for saved SDF filenames.
    random_seed: int Random seed for conformer embedding.
    save_image: bool Whether to save a conformer grid image.
    image_output_path: Optional[str] Output path for conformer image. If None and save_image=True, auto-generated under output_dir.
    Returns:
    dict Structured generation result.
    """
    result: Dict = {
        "success": False,
        "smiles": smiles,
        "num_conformers_requested": num_conformers,
        "max_iter": max_iter,
        "top_n_requested": top_n,
        "top_n_returned": 0,
        "failed_optimizations": 0,
        "selected_conformers": [],
        "saved_sdf_paths": [],
        "image_output_path": None,
        "message": "",
    }

    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        result["message"] = "Failed to parse SMILES"
        return result

    if top_n <= 0:
        top_n = 1

    mol = Chem.AddHs(mol)
    energies, failed_optimizations = _optimize_conformers(
        mol=mol,
        num_conformers=num_conformers,
        max_iter=max_iter,
        random_seed=random_seed,
    )
    result["failed_optimizations"] = failed_optimizations

    if not energies:
        result["message"] = "Conformer embedding failed"
        return result

    valid_energies = [item for item in energies if math.isfinite(item[1])]
    if not valid_energies:
        result["message"] = "No valid conformers after optimization"
        return result

    selected = valid_energies[:top_n]
    top_cids = [cid for cid, _ in selected]

    if len(top_cids) > 1:
        ref_cid = top_cids[0]
        for cid in top_cids[1:]:
            AlignMol(mol, mol, prbCid=cid, refCid=ref_cid)

    result["top_n_returned"] = len(selected)
    result["selected_conformers"] = [
        {
            "conformer_id": cid,
            "energy_kcal_per_mol": energy,
        }
        for cid, energy in selected
    ]

    if output_dir:
        result["saved_sdf_paths"] = _save_top_conformer_sdfs(
            mol=mol,
            selected=selected,
            output_dir=output_dir,
            sdf_prefix=sdf_prefix,
        )

    if save_image:
        resolved_image_path = image_output_path
        if resolved_image_path is None:
            base_dir = output_dir if output_dir else "."
            resolved_image_path = os.path.join(base_dir, "conformers.png")
        result["image_output_path"] = _save_grid_image(
            mol=mol,
            selected=selected,
            image_output_path=resolved_image_path,
        )

    result["success"] = True
    result["message"] = "Conformer generation completed"
    return result


def generate_visualize_and_save_conformers(
    smiles: str,
    num_conformers: int = 50,
    max_iter: int = 200,
    top_n: int = 5,
    individual_sdf_dir: Optional[str] = None,
    individual_sdf_prefix: str = "conf_",
) -> Dict:
    """Backward-compatible wrapper around generate_conformations with old parameter names."""
    return generate_conformations(
        smiles=smiles,
        num_conformers=num_conformers,
        max_iter=max_iter,
        top_n=top_n,
        output_dir=individual_sdf_dir,
        sdf_prefix=individual_sdf_prefix,
        random_seed=42,
        save_image=False,
        image_output_path=None,
    )


def batch_generate_conformations(
    smiles_list: Iterable[str],
    output_root_dir: Optional[str] = None,
    num_conformers: int = 50,
    max_iter: int = 200,
    top_n: int = 5,
    sdf_prefix: str = "conf_",
    random_seed: int = 42,
    save_image: bool = False,
) -> Dict:
    """
    Name: batch_generate_conformations
    Description: Optional batch helper that runs conformer generation for multiple SMILES strings.
    Parameters:
    smiles_list: Iterable[str] Input SMILES list.
    output_root_dir: Optional[str] Root output directory for per-item folders.
    num_conformers: int Number of conformers to generate per molecule.
    max_iter: int Maximum MMFF optimization iterations.
    top_n: int Number of best conformers to keep.
    sdf_prefix: str Prefix for saved SDF filenames.
    random_seed: int Random seed for conformer embedding.
    save_image: bool Whether to save per-item conformer images.
    Returns:
    dict Batch summary and per-item results.
    """
    results: List[Dict] = []
    success_count = 0

    if output_root_dir:
        os.makedirs(output_root_dir, exist_ok=True)

    for index, smiles in enumerate(smiles_list, start=1):
        item_output_dir = None
        item_image_path = None
        if output_root_dir:
            item_output_dir = os.path.join(output_root_dir, f"molecule_{index}")
            item_image_path = os.path.join(item_output_dir, "conformers.png")

        item_result = generate_conformations(
            smiles=smiles,
            num_conformers=num_conformers,
            max_iter=max_iter,
            top_n=top_n,
            output_dir=item_output_dir,
            sdf_prefix=sdf_prefix,
            random_seed=random_seed,
            save_image=save_image,
            image_output_path=item_image_path,
        )
        item_result["index"] = index
        results.append(item_result)

        if item_result.get("success"):
            success_count += 1

    return {
        "success": success_count == len(results),
        "total": len(results),
        "success_count": success_count,
        "failure_count": len(results) - success_count,
        "results": results,
    }


def _read_smiles_from_file(smiles_file: str) -> List[str]:
    smiles_values: List[str] = []
    with open(smiles_file, "r", encoding="utf-8") as file_obj:
        for line in file_obj:
            text = line.strip()
            if text:
                smiles_values.append(text)
    return smiles_values


def _build_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate molecular conformations from SMILES")
    subparsers = parser.add_subparsers(dest="mode", required=True)

    single_parser = subparsers.add_parser("single", help="Generate conformers for one SMILES")
    single_parser.add_argument("--smiles", required=True, help="Input SMILES")

    batch_parser = subparsers.add_parser("batch", help="Generate conformers for multiple SMILES")
    batch_group = batch_parser.add_mutually_exclusive_group(required=True)
    batch_group.add_argument("--smiles_file", help="Text file with one SMILES per line")
    batch_group.add_argument("--smiles", nargs="+", help="SMILES values")
    batch_parser.add_argument("--output_root_dir", default=None, help="Root directory for batch outputs")

    for subparser in (single_parser, batch_parser):
        subparser.add_argument("--num_conformers", type=int, default=50)
        subparser.add_argument("--max_iter", type=int, default=200)
        subparser.add_argument("--top_n", type=int, default=5)
        subparser.add_argument("--sdf_prefix", default="conf_")
        subparser.add_argument("--random_seed", type=int, default=42)
        subparser.add_argument("--save_image", action="store_true")

    single_parser.add_argument("--output_dir", default=None, help="Directory to save top conformer SDF files")
    single_parser.add_argument("--image_output_path", default=None, help="Path to save conformer grid image")

    return parser


def main() -> None:
    args = _build_cli_parser().parse_args()

    if args.mode == "single":
        output = generate_conformations(
            smiles=args.smiles,
            num_conformers=args.num_conformers,
            max_iter=args.max_iter,
            top_n=args.top_n,
            output_dir=args.output_dir,
            sdf_prefix=args.sdf_prefix,
            random_seed=args.random_seed,
            save_image=args.save_image,
            image_output_path=args.image_output_path,
        )
    else:
        if args.smiles_file:
            smiles_values = _read_smiles_from_file(args.smiles_file)
        else:
            smiles_values = list(args.smiles)

        output = batch_generate_conformations(
            smiles_list=smiles_values,
            output_root_dir=args.output_root_dir,
            num_conformers=args.num_conformers,
            max_iter=args.max_iter,
            top_n=args.top_n,
            sdf_prefix=args.sdf_prefix,
            random_seed=args.random_seed,
            save_image=args.save_image,
        )

    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
