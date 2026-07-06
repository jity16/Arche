import argparse
import os
import json
from typing import Any, Dict, List, Optional
from cclib.io import ccopen

ELEMENT_NAMES = {
    1: 'H', 2: 'He', 3: 'Li', 4: 'Be', 5: 'B', 6: 'C', 7: 'N', 8: 'O', 9: 'F', 10: 'Ne',
    11: 'Na', 12: 'Mg', 13: 'Al', 14: 'Si', 15: 'P', 16: 'S', 17: 'Cl', 18: 'Ar', 19: 'K', 20: 'Ca',
    21: 'Sc', 22: 'Ti', 23: 'V', 24: 'Cr', 25: 'Mn', 26: 'Fe', 27: 'Co', 28: 'Ni', 29: 'Cu', 30: 'Zn',
    31: 'Ga', 32: 'Ge', 33: 'As', 34: 'Se', 35: 'Br', 36: 'Kr', 37: 'Rb', 38: 'Sr', 39: 'Y', 40: 'Zr',
    41: 'Nb', 42: 'Mo', 43: 'Tc', 44: 'Ru', 45: 'Rh', 46: 'Pd', 47: 'Ag', 48: 'Cd', 49: 'In', 50: 'Sn',
    51: 'Sb', 52: 'Te', 53: 'I', 54: 'Xe', 55: 'Cs', 56: 'Ba', 57: 'La', 58: 'Ce', 59: 'Pr', 60: 'Nd',
    61: 'Pm', 62: 'Sm', 63: 'Eu', 64: 'Gd', 65: 'Tb', 66: 'Dy', 67: 'Ho', 68: 'Er', 69: 'Tm', 70: 'Yb',
    71: 'Lu', 72: 'Hf', 73: 'Ta', 74: 'W', 75: 'Re', 76: 'Os', 77: 'Ir', 78: 'Pt', 79: 'Au', 80: 'Hg',
    81: 'Tl', 82: 'Pb', 83: 'Bi', 84: 'Po', 85: 'At', 86: 'Rn', 87: 'Fr', 88: 'Ra', 89: 'Ac', 90: 'Th',
    91: 'Pa', 92: 'U', 93: 'Np', 94: 'Pu', 95: 'Am', 96: 'Cm', 97: 'Bk', 98: 'Cf', 99: 'Es', 100: 'Fm',
    101: 'Md', 102: 'No', 103: 'Lr', 104: 'Rf', 105: 'Db', 106: 'Sg', 107: 'Bh', 108: 'Hs', 109: 'Mt', 
    110: 'Ds', 111: 'Rg', 112: 'Cn', 113: 'Nh', 114: 'Fl', 115: 'Mc', 116: 'Lv', 117: 'Ts', 118: 'Og'
}

class GaussianParser:
    """Gaussian输出文件解析工具类，按新要求优化了数据处理逻辑"""

    # 定义支持的性质及其对应的cclib属性
    PROPERTY_MAP = {
        'charge': 'charge',              # 分子电荷
        'mult': 'mult',                  # 分子自旋多重度
        'scf_energies': 'scfenergies',   # SCF能量
        'mp_energies': 'mpenergies',     # MP能量
        'cc_energies': 'ccenergies',     # CC能量
        'temperature': 'temperature',    # 热力学计算时的温度 K
        'pressure': 'pressure',          # 热力学计算时的压强 atm
        'zpve': 'zpve',                  # 零点振动能
        'enthalpy': 'enthalpy',          # 焓
        'entropy': 'entropy',            # 熵
        'free_energy': 'freeenergy',     # 自由能
        'atom_num': 'natom',             # 原子个数
        'elements': 'atomnos',           # 原子序数
        'coordinates': 'atomcoords',     # 原子坐标
        'atom_charges': 'atomcharges',   # 原子电荷
        'moments': 'moments',            # 偶/四/八/十六极矩
        'polarizabilities': 'polarizabilities',  # 极化率
        'rotational_consts': 'rotconsts',   # 转动常数
        'frequencies': 'vibfreqs',       # 频率
        'ir_intensities': 'vibirs',      # IR 强度
        'opt_done': 'optdone',           # 是否 opt 成功
        'basis_num': 'nbasis',           # 基组数量
    }

    @classmethod
    def parse_file(cls, input_file_path, properties=None, include_metadata=True):
        """
        解析Gaussian输出文件

        参数:
            input_file_path: 输入文件路径
            properties: 需要提取的性质列表，None表示提取所有可用性质
            include_metadata: 是否包含元数据

        返回:
            解析结果字典
        """
        try:
            # 使用cclib解析文件
            data = ccopen(input_file_path).parse()
        except Exception as e:
            raise Exception(f"解析文件失败: {str(e)}")

        # 验证请求的性质是否有效
        if properties:
            invalid_props = [p for p in properties if p not in cls.PROPERTY_MAP.keys()]
            if invalid_props:
                raise ValueError(
                    f"无效的性质: {', '.join(invalid_props)}. "
                    f"支持的性质: {', '.join(cls.PROPERTY_MAP.keys())}"
                )

        # 如果未指定性质，则提取所有可用性质
        if properties is None:
            properties = []
            for prop_name, attr_name in cls.PROPERTY_MAP.items():
                # 1. 先检查data是否有该属性
                if not hasattr(data, attr_name):
                    continue
                # 2. 检查属性值是否为None（排除空值）
                attr_value = getattr(data, attr_name)
                if attr_value is None:
                    continue
                # 3. 最终加入默认提取列表
                properties.append(prop_name)

        result = {}

        # 提取每个性质
        for prop in properties:
            attr_name = cls.PROPERTY_MAP.get(prop)
            if not attr_name or not hasattr(data, attr_name):
                continue  # 跳过不存在的性质

            value = getattr(data, attr_name)

            # 转换单位和格式
            result[prop] = cls._process_property(prop, value)

        # 添加元数据
        if include_metadata:
            result['metadata'] = {
                'filename': os.path.basename(input_file_path),
                'package': data.metadata.get('package', 'Gaussian'),
                'version': data.metadata.get('legacy_package_version', 'Unknown'),
                'functional': data.metadata.get('functional', 'Unknown'),
                'basis_set': data.metadata.get('basis_set', 'Unknown'),
                'success': data.metadata.get('success', False)
            }

        return result

    @classmethod
    def _process_property(cls, prop_name, value):
        """处理不同性质的格式转换，按新要求调整"""
        if value is None:
            return None

        # 处理numpy数组，转换为列表
        if hasattr(value, 'tolist'):
            value = value.tolist()

        # 1. 处理需要取最后一帧的属性
        if prop_name in ['scf_energies', 'rotational_consts']:
            # 如果是列表/数组，取最后一个元素
            if isinstance(value, (list, tuple)) and len(value) > 0:
                value = value[-1]

        # 2. 处理单位转换 - 仅SCF能量需要从eV转换为hartree
        if prop_name == 'scf_energies':
            # 按要求: eV -> hartree，使用转换因子1/27.21138505
            if isinstance(value, (list, tuple)):
                value = [v / 27.21138505 for v in value]
            else:
                value = value / 27.21138505

        # 3. 原子序数转换为元素符号
        elif prop_name == 'elements':
            return [ELEMENT_NAMES[num] for num in value] if isinstance(value, (list, tuple)) else ELEMENT_NAMES[value]

        # 4. 原子电荷特殊处理
        elif prop_name == 'atom_charges':
            if isinstance(value, dict):
                return {k: v.tolist() if hasattr(v, 'tolist') else v for k, v in value.items()}
            return value

        # 关键修改：调用原生类型清洗函数，确保输出是Python原生类型
        cleaned_value = cls._clean_native_type(value)
        return cleaned_value

    @classmethod
    def _clean_native_type(cls, value):
        """
        递归清洗值，确保所有类型都是Python原生类型（支持嵌套结构）
        处理：cclib自定义类型、numpy类型、其他非原生类型
        """
        import numpy as np
        # 1. 处理numpy数值类型（如np.float64→float，np.int64→int）
        if isinstance(value, (np.integer, np.int8, np.int16, np.int32, np.int64)):
            return int(value)
        elif isinstance(value, (np.floating, np.float16, np.float32, np.float64)):
            return float(value)
        elif isinstance(value, np.ndarray):
            # numpy数组→原生列表，并递归清洗每个元素
            return [cls._clean_native_type(item) for item in value.tolist()]
        
        # 2. 处理cclib自定义类型（如ccDataArray、ccData）
        # 检查是否为cclib的ccDataArray（通常有'tolist'方法）
        if hasattr(value, 'tolist') and callable(value.tolist):
            try:
                cclib_list = value.tolist()
                # 递归清洗cclib列表中的元素
                return [cls._clean_native_type(item) for item in cclib_list]
            except Exception:
                pass
        
        # 3. 处理列表/元组（递归清洗每个元素）
        if isinstance(value, (list, tuple)):
            return [cls._clean_native_type(item) for item in value]
        
        # 4. 处理字典（递归清洗每个值）
        if isinstance(value, dict):
            return {k: cls._clean_native_type(v) for k, v in value.items()}
        
        # 5. 处理布尔值、None（直接返回）
        if isinstance(value, (bool, type(None), str)):
            return value
        
        # 6. 其他未知类型（强制转换为字符串，避免序列化失败）
        try:
            return str(value)
        except Exception:
            return "Unknown type"
    
    @classmethod
    def save_to_json(cls, data, output_filename):
        """将解析结果保存为JSON文件"""
        if not data:
            return False

        try:
            with open(output_filename, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
            return True
        except Exception as e:
            raise Exception(f"保存JSON失败: {str(e)}")


def parse_gaussian_output(
    input_file_path: str,
    properties: Optional[List[str]] = None,
    include_metadata: bool = True,
    save_json_path: Optional[str] = None
) -> Dict[str, Any]:
    """
    Name: parse_gaussian_output
    Description: Parse a Gaussian output file and optionally save parsed result to JSON.
    Parameters:
    input_file_path: str Path to Gaussian output file.
    properties: Optional[List[str]] Requested properties to parse.
    include_metadata: bool Whether to include metadata in parse result.
    save_json_path: Optional[str] Optional JSON output path.
    Returns:
    dict Structured parsing result.
    """
    response: Dict[str, Any] = {
        "success": False,
        "message": "",
        "input_path": input_file_path,
        "output_path": None,
        "result": {},
        "metadata": {},
        "error": None,
    }

    try:
        parsed_result = GaussianParser.parse_file(
            input_file_path=input_file_path,
            properties=properties,
            include_metadata=include_metadata
        )

        response["result"] = parsed_result
        if include_metadata:
            response["metadata"] = parsed_result.get("metadata", {})

        if save_json_path:
            GaussianParser.save_to_json(parsed_result, save_json_path)
            response["output_path"] = save_json_path

        response["success"] = True
        response["message"] = "Gaussian output parsed successfully"
        return response
    except Exception as exc:
        response["message"] = "Failed to parse Gaussian output"
        response["error"] = str(exc)
        return response


def _build_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Parse Gaussian output files")
    parser.add_argument("--input", required=True, help="Input Gaussian output file path")
    parser.add_argument("--output", default=None, help="Optional output JSON file path")
    parser.add_argument(
        "--properties",
        nargs="*",
        default=None,
        help="Optional list of properties to extract",
    )
    parser.add_argument(
        "--no-metadata",
        action="store_true",
        help="Disable metadata in parsed output",
    )
    return parser


def main() -> None:
    args = _build_cli_parser().parse_args()
    result = parse_gaussian_output(
        input_file_path=args.input,
        properties=args.properties,
        include_metadata=not args.no_metadata,
        save_json_path=args.output,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["success"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
