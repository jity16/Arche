import argparse
import os
from typing import Dict, Optional

from rdkit import Chem


def sdf_to_xyz(
    input_sdf_path: str,
    output_xyz_path: str,
    title: str = "Converted from SDF file",
) -> Dict:
    """
    Convert a single SDF file into an XYZ coordinate file.
    """
    result = {
        "success": False,
        "input_sdf_path": input_sdf_path,
        "output_xyz_path": output_xyz_path,
        "atom_count": 0,
        "message": "",
    }

    try:
        supplier = Chem.SDMolSupplier(input_sdf_path, removeHs=False)
        mol = supplier[0] if supplier else None
        if mol is None:
            raise ValueError("failed to read SDF")
        if mol.GetNumConformers() == 0:
            raise ValueError("SDF does not contain 3D coordinates")

        conf = mol.GetConformer()
        os.makedirs(os.path.dirname(output_xyz_path) or ".", exist_ok=True)

        lines = [str(mol.GetNumAtoms()), title]
        for atom in mol.GetAtoms():
            pos = conf.GetAtomPosition(atom.GetIdx())
            lines.append(f"{atom.GetSymbol()} {pos.x:.6f} {pos.y:.6f} {pos.z:.6f}")

        with open(output_xyz_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

        result["success"] = True
        result["atom_count"] = mol.GetNumAtoms()
        result["message"] = "Converted SDF to XYZ successfully"
        return result
    except Exception as exc:
        result["message"] = str(exc)
        return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert an SDF file into an XYZ file.")
    parser.add_argument("--input", "--input_sdf_path", dest="input_sdf_path", required=True)
    parser.add_argument("--output", "--output_xyz_path", dest="output_xyz_path", required=True)
    parser.add_argument("--title", default="Converted from SDF file")
    args = parser.parse_args()

    result = sdf_to_xyz(
        input_sdf_path=args.input_sdf_path,
        output_xyz_path=args.output_xyz_path,
        title=args.title,
    )
    print(result)
    return 0 if result.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
