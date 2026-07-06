import glob
import json
import os
import random
import shutil
import sys
from typing import Any, Dict, Tuple

import numpy as np


THIS_DIR = os.path.dirname(os.path.abspath(__file__))
TOOLS_DIR = os.path.join(THIS_DIR, "tools")
for candidate in (THIS_DIR, TOOLS_DIR):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)


def _load_pipeline_modules() -> Tuple[Any, Any, Any, Any, Any]:
    from MapReaction import map_reaction
    from ParseReactionMapIndex import parse_reaction_map_index
    from GenConformersAndOptComplex import gen_conformers_and_opt_complex
    from PreOptComplex import batch_optimize_xyz
    from RunCINEB_ash import run_cineb

    return (
        map_reaction,
        parse_reaction_map_index,
        gen_conformers_and_opt_complex,
        batch_optimize_xyz,
        run_cineb,
    )


def load_config_from_json(json_path: str) -> Dict[str, Any]:
    """Load and validate the TS pipeline configuration."""
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            config = json.load(f)

        required_fields = [
            "reactants", "products", "seed", "direction",
            "base_results_dir", "main_force_field", "target_force_field",
            "charge", "multiplicity", "num_images",
            "conformer_rmsd_threshold", "max_conformers",
            "conformer_max_attempts", "conformer_max_iter",
            "complex_max_attempts", "complex_initial_distance",
            "rmsd_threshold", "equilibrium_ratio", "min_equilibrium_ratio",
            "max_equilibrium_ratio", "force_constant", "max_allowed_ratio",
            "filter_rmsd_threshold", "reorder", "opt_fmax", "opt_steps", "task_name",
            "climb", "neb_fmax", "neb_steps",
        ]
        for field in required_fields:
            if field not in config:
                raise ValueError(f"配置文件缺少必要字段: {field}")

        if not isinstance(config["reactants"], list) or not isinstance(config["products"], list):
            raise ValueError("'reactants' 和 'products' 必须是列表")
        if not isinstance(config["seed"], int):
            raise ValueError("'seed' 必须是整数")

        return config
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON解析错误: {str(exc)}") from exc
    except Exception as exc:
        raise ValueError(f"加载配置失败: {str(exc)}") from exc


def run_pipeline(config_path: str) -> Dict[str, Any]:
    config = load_config_from_json(config_path)
    (
        map_reaction,
        parse_reaction_map_index,
        gen_conformers_and_opt_complex,
        batch_optimize_xyz,
        run_cineb,
    ) = _load_pipeline_modules()

    print("成功加载配置参数")
    seed = config["seed"]
    random.seed(seed)
    np.random.seed(seed)
    print(f"已设置随机数种子: {seed}")

    reactants = config["reactants"]
    products = config["products"]
    direction = config["direction"]
    base_results_dir = config["base_results_dir"]
    main_force_field = config["main_force_field"]
    target_force_field = config["target_force_field"]
    charge = config["charge"]
    mult = config["multiplicity"]
    num_images = config["num_images"]

    conformer_rmsd_threshold = config["conformer_rmsd_threshold"]
    max_conformers = config["max_conformers"]
    conformer_max_attempts = config["conformer_max_attempts"]
    conformer_max_iter = config["conformer_max_iter"]
    complex_max_attempts = config["complex_max_attempts"]
    complex_initial_distance = config["complex_initial_distance"]
    rmsd_threshold = config["rmsd_threshold"]
    equilibrium_ratio = config["equilibrium_ratio"]
    min_equilibrium_ratio = config["min_equilibrium_ratio"]
    max_equilibrium_ratio = config["max_equilibrium_ratio"]
    force_constant = config["force_constant"]
    max_allowed_ratio = config["max_allowed_ratio"]
    filter_rmsd_threshold = config["filter_rmsd_threshold"]
    reorder = config["reorder"]

    opt_fmax = config["opt_fmax"]
    opt_steps = config["opt_steps"]
    task_name = config["task_name"]

    climb = config["climb"]
    neb_fmax = config["neb_fmax"]
    neb_steps = config["neb_steps"]

    print(f"Reactants: {reactants}")
    print(f"Products: {products}")
    print(f"反应方向: {direction}")
    print(f"主力场: {main_force_field}, 目标力场: {target_force_field}")

    results = map_reaction(reactants, products)
    print("成功完成反应的原子映射")

    mapped_rxn = results[0]["mapped_rxn"]
    rxn_site_data = parse_reaction_map_index(mapped_rxn)
    print("成功完成反应位点解析")

    mapped_rxn_update = rxn_site_data[0]["updated_smiles"]
    if direction == "forward":
        smiles_list = mapped_rxn_update.split(">>")[0].split(".")
    else:
        smiles_list = mapped_rxn_update.split(">>")[1].split(".")
    rxn_pairs = rxn_site_data[0].get(direction, [])

    output_dir = os.path.join(base_results_dir, direction, main_force_field)
    base_output_file = "reactant_complex" if direction == "forward" else "product_complex"

    print(f"SMILES列表包含 {len(smiles_list)} 个分子")
    print(f"{direction} 方向包含 {len(rxn_pairs)} 个反应对")
    print(f"输出根目录: {output_dir}")

    gen_conformers_and_opt_complex(
        smiles_list=smiles_list,
        rxn_pairs=rxn_pairs,
        conformer_rmsd_threshold=conformer_rmsd_threshold,
        max_conformers=max_conformers,
        conformer_max_attempts=conformer_max_attempts,
        conformer_max_iter=conformer_max_iter,
        output_dir=output_dir,
        base_output_file=base_output_file,
        complex_max_attempts=complex_max_attempts,
        complex_initial_distance=complex_initial_distance,
        charge=charge,
        multiplicity=mult,
        rmsd_threshold=rmsd_threshold,
        equilibrium_ratio=equilibrium_ratio,
        min_equilibrium_ratio=min_equilibrium_ratio,
        max_equilibrium_ratio=max_equilibrium_ratio,
        force_constant=force_constant,
        max_allowed_ratio=max_allowed_ratio,
        filter_rmsd_threshold=filter_rmsd_threshold,
        reorder=reorder,
    )

    reaction_pairs_dir = os.path.join(output_dir, "reaction_pairs")
    rxn_pair_dirs = glob.glob(os.path.join(reaction_pairs_dir, "rxn_pair*"))
    processed_pairs = []

    for dir_path in rxn_pair_dirs:
        if not os.path.isdir(dir_path):
            continue

        target_dir_path = dir_path.replace(main_force_field, target_force_field)
        os.makedirs(target_dir_path, exist_ok=True)
        print(f"\n处理反应对目录: {dir_path}")
        print(f"目标力场目录: {target_dir_path}")

        batch_optimize_xyz(
            input_dir=dir_path,
            output_dir=target_dir_path,
            charge=charge,
            mult=mult,
            fmax=opt_fmax,
            steps=opt_steps,
            task_name=task_name,
        )

        reactant_file = os.path.join(target_dir_path, "reactant.xyz")
        product_file = os.path.join(target_dir_path, "product.xyz")

        pair_record = {
            "source_dir": dir_path,
            "target_dir": target_dir_path,
            "reactant_file": reactant_file,
            "product_file": product_file,
            "ts_guess": None,
            "status": "skipped",
        }

        if os.path.exists(reactant_file) and os.path.exists(product_file):
            ts_guess = run_cineb(
                reactant_file=reactant_file,
                product_file=product_file,
                output_file=target_dir_path,
                num_images=num_images,
                charge=charge,
                mult=mult,
                climb=climb,
                fmax=neb_fmax,
                steps=neb_steps,
                task_name=task_name,
            )
            print(f"已完成 {os.path.basename(dir_path)} 的CINEB计算")

            current_dir = os.getcwd()
            for item in os.listdir(current_dir):
                item_path = os.path.join(current_dir, item)
                if os.path.isdir(item_path) and item.startswith("image"):
                    try:
                        shutil.rmtree(item_path)
                        print(f"已删除目录: {item_path}")
                    except Exception as exc:
                        print(f"删除目录 {item_path} 失败: {exc}")

            os.makedirs(target_dir_path, exist_ok=True)
            for pattern in ("*.result", "*.xyz", "*.interp", "*.energy"):
                for file_path in glob.glob(os.path.join(current_dir, pattern)):
                    target_file = os.path.join(target_dir_path, os.path.basename(file_path))
                    try:
                        shutil.move(file_path, target_file)
                        print(f"已移动文件: {file_path} -> {target_file}")
                    except Exception as exc:
                        print(f"移动文件 {file_path} 失败: {exc}")

            pair_record["ts_guess"] = ts_guess
            pair_record["status"] = "completed"
        else:
            print(f"警告: {target_dir_path} 中缺少反应物或产物文件，跳过CINEB计算")

        processed_pairs.append(pair_record)

    print("\n所有任务完成")
    return {
        "success": True,
        "config_path": os.path.abspath(config_path),
        "base_results_dir": base_results_dir,
        "output_dir": output_dir,
        "processed_pairs": processed_pairs,
        "message": "23_TSPipeline completed",
    }


def run_tool(config_path: str, **_: Any) -> Dict[str, Any]:
    try:
        return run_pipeline(config_path)
    except Exception as exc:
        return {
            "success": False,
            "config_path": os.path.abspath(config_path) if config_path else None,
            "message": str(exc),
        }


def main(config_path: str | None = None) -> int:
    config_path = config_path or (sys.argv[1] if len(sys.argv) > 1 else os.path.join(THIS_DIR, "config.json"))
    result = run_tool(config_path=config_path)
    if not result.get("success"):
        print(result.get("message", "23_TSPipeline failed"))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
