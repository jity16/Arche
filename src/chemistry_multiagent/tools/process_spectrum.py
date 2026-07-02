import os
import subprocess
import contextlib
from PIL import Image
from io import BytesIO, StringIO
import tempfile
from typing import Dict, Optional, Any, List

# Multiwfn 相关配置：路径从环境变量注入（替代硬编码的集群绝对路径 /mnt/...）。
# 未设置时留空 → 退化为按 PATH 解析 Multiwfn_noGUI；真实模式由部署环境提供该可执行文件。
MULTIWFN_PATH = os.environ.get("MULTIWFN_PATH", "")
MULTIWFN_EXE = os.path.join(MULTIWFN_PATH, "Multiwfn_noGUI") if MULTIWFN_PATH else "Multiwfn_noGUI"
# 绘图相关配置：脚本路径从环境变量注入（替代硬编码的集群绝对路径 /mnt/...）。
PLOT_SCRIPT = os.environ.get("ARCHE_PLOT_SCRIPT", "")  # 留空 → 退化为按 PATH/相对路径解析；真实模式由部署环境提供

# 1. 统一光谱配置字典（按类型分组管理参数）
SPECTRUM_CONFIGS: Dict[str, Dict[str, Any]] = {
    # 振动光谱通用配置（IR/Raman/ROA/VCD）
    "vibrational_base": {
        "param_types": ["vibrational"],  # 标识参数类型
        "common_params": {
            "FWHM": 20.0,
            "incident_wavelength": 532.0,  # nm
            "temperature": 298.15,  # K
            "scale_factor": 1.0  # 振动频率缩放因子
        }
    },

    # 红外光谱
    "IR": {
        "inherit": "vibrational_base",  # 继承振动光谱基础参数
        "plot_params": {
            "xlabel": "Wavenumber (cm⁻¹)",
            "ylabel": "Molar absorption cofficient ε (L · mol⁻¹ · cm⁻¹)",
            "xleft": 4000.0,
            "xright": 400.0,
            "ybottom": None,
            "ytop": None,
            "x_reverse": False,
            "y_reverse": True,
            "dpi": 300,
            "title": "IR Spectrum",
            "plot_lines": False
        },
        # 中间文件内容模板
        "intermediate_content": [
            "11",
            "1",
            "0",
            "8",
            "{FWHM}",
            "14",
            "\n",
            "{scale_factor}",
            "0",
            "2",
            "-3",
            "q\n"
        ],
        # 输出数据文件
        "data_files": {
            "curve": os.path.join(MULTIWFN_PATH, "spectrum_curve.txt"),
            "line": os.path.join(MULTIWFN_PATH, "spectrum_line.txt")
        }
    },
    
    # 拉曼光谱
    "Raman": {
        "inherit": "vibrational_base",
        "plot_params": {
            "xlabel": "Wavenumber (cm⁻¹)",
            "ylabel": "Relative Raman Intensity",
            "xleft": 4000.0,
            "xright": 0.0,
            "ybottom": None,
            "ytop": None,
            "x_reverse": False,
            "y_reverse": False,
            "dpi": 300,
            "title": "Raman Spectrum",
            "plot_lines": False
        },
        # 中间文件内容模板
        "intermediate_content": [
            "11",
            "2",
            "0",
            "19",
            "{incident_wavelength}nm",
            "{temperature}",
            "14",
            "\n",
            "{scale_factor}",
            "8",
            "{FWHM}",
            "0",
            "2",
            "-3",
            "q\n"
        ],
        # 输出数据文件 - 可能与IR不同
        "data_files": {
            "curve": os.path.join(MULTIWFN_PATH, "spectrum_curve.txt"),
            "line": os.path.join(MULTIWFN_PATH, "spectrum_line.txt")
        }
    },
    
    # ROA光谱
    "ROA": {
        "inherit": "vibrational_base",
        "common_params": {
            "FWHM": 8.0
        },
        "plot_params": {
            "xlabel": "Wavenumber (cm⁻¹)",
            "ylabel": "ROA intensity",
            "xleft": None,
            "xright": None,
            "ybottom": None,
            "ytop": None,
            "x_reverse": False,
            "y_reverse": False,
            "dpi": 300,
            "title": "ROA Spectrum",
            "plot_lines": True
        },
        # 中间文件内容模板
        "intermediate_content": [
            "11",
            "6",
            "2",
            "19",
            "{incident_wavelength}nm",
            "{temperature}",
            "14",
            "\n",
            "{scale_factor}",
            "8",
            "{FWHM}",
            "2",
            "-3",
            "q\n"
        ],
        # 输出数据文件
        "data_files": {
            "curve": os.path.join(MULTIWFN_PATH, "spectrum_curve.txt"),
            "line": os.path.join(MULTIWFN_PATH, "spectrum_line.txt")
        }
    },
    
    # VCD光谱
    "VCD": {
        "inherit": "vibrational_base",
        "common_params": {
            "FWHM": 8.0
        },
        "plot_params": {
            "xlabel": "Wavenumber (cm⁻¹)",
            "ylabel": "Δε (arb.)",
            "xleft": None,
            "xright": None,
            "ybottom": None,
            "ytop": None,
            "x_reverse": False,
            "y_reverse": False,
            "dpi": 300,
            "title": "VCD Spectrum",
            "plot_lines": True
        },
        "intermediate_content": [
            "11",
            "5",
            "14",
            "\n",
            "{scale_factor}",
            "8",
            "{FWHM}",
            "2",
            "-3",
            "q\n"
        ],
        "data_files": {
            "curve": os.path.join(MULTIWFN_PATH, "spectrum_curve.txt"),
            "line": os.path.join(MULTIWFN_PATH, "spectrum_line.txt")
        }
    },

    # UV-Vis光谱
    "UV-Vis": {
        "param_types": ["uv-vis"],  # 标识参数类型
        "plot_params": {
            "xlabel": "Wavelength (nm)",
            "ylabel": "Molar absorption cofficient ε (L · mol⁻¹ · cm⁻¹)",
            "xleft": None,
            "xright": None,
            "ybottom": None,
            "ytop": None,
            "x_reverse": False,
            "y_reverse": False,
            "dpi": 300,
            "title": "UV-Vis Spectrum",
            "plot_lines": True
        },
        "intermediate_content": [
            "11",
            "3",
            "2",
            "-3",
            "q\n"
        ],
        "data_files": {
            "curve": os.path.join(MULTIWFN_PATH, "spectrum_curve.txt"),
            "line": os.path.join(MULTIWFN_PATH, "spectrum_line.txt")
        }
    },

    # ECD光谱
    "ECD": {
        "param_types": ["ecd"],  # 标识参数类型
        "plot_params": {
            "xlabel": "Wavelength (nm)",
            "ylabel": "Δε (arb.)",
            "xleft": None,
            "xright": None,
            "ybottom": None,
            "ytop": None,
            "x_reverse": False,
            "y_reverse": False,
            "dpi": 300,
            "title": "ECD Spectrum",
            "plot_lines": True
        },
        "intermediate_content": [
            "11",
            "4",
            "2",
            "2",
            "-3",
            "q\n"
        ],
        "data_files": {
            "curve": os.path.join(MULTIWFN_PATH, "spectrum_curve.txt"),
            "line": os.path.join(MULTIWFN_PATH, "spectrum_line.txt")
        }
    },

    # 核磁光谱
    "NMR": {
        "param_types": ["nmr"],  # 标识参数类型
        "common_params": {
            "nucleus": "H",  # 核磁元素
            "tms_reference": 31.5139  # TMS标准化学位移
        },
        "plot_params": {
            "xlabel": "Chemical shift (ppm)",
            "ylabel": "Degeneracy",
            "xleft": None,
            "xright": None,
            "ybottom": 0.0,
            "ytop": 6.0,
            "x_reverse": False,
            "y_reverse": False,
            "dpi": 300,
            "title": "NMR Spectrum",
            "plot_lines": True
        },
        "intermediate_content": [
            "11",
            "7",
            "6",
            "{nucleus}",
            "7",
            "1",
            "{tms_reference}",
            "2",
            "-10",
            "q\n"
        ],
        "data_files": {
            "curve": os.path.join(MULTIWFN_PATH, "NMR_curve.txt"),
            "line": os.path.join(MULTIWFN_PATH, "NMR_line.txt")
        }
    }
}

# 2. 统一参数处理函数（获取合并后的完整参数）
def get_unified_params(spectrum_type: str, **user_params) -> Dict[str, Any]:
    config = SPECTRUM_CONFIGS[spectrum_type]
    
    # 处理继承关系
    if "inherit" in config:
        base_config = SPECTRUM_CONFIGS[config["inherit"]]
        base_params = base_config["common_params"].copy()
    else:
        base_params = config.get("common_params", {}).copy()
    
    # 合并当前光谱类型的特有参数（覆盖基础参数）
    if "common_params" in config:
        base_params.update(config["common_params"])
    
    # 合并用户的通用参数
    for key, value in user_params.items():
        if value is not None and key in base_params:
            base_params[key] = value
    
    # 处理绘图参数（合并默认和用户输入）
    plot_params = config["plot_params"].copy()
    for key, value in user_params.items():
        if value is not None and key in plot_params:
            plot_params[key] = value
    
    # 返回完整参数（包含data_files）
    return {
        "common": base_params,
        "plot": plot_params,
        "data_files": config["data_files"]
    }

# 3. 统一处理函数
def process_file(
    input_file_path: str, 
    output_image_path: str,
    spectrum_type: str = "IR",
    # 振动光谱通用参数
    FWHM: Optional[float] = None,
    incident_wavelength: Optional[float] = None,
    temperature: Optional[float] = None,
    scale_factor: Optional[float] = None,
    # 核磁光谱参数
    nucleus: Optional[str] = None,
    tms_reference: Optional[float] = None,
    # 绘图通用参数
    xlabel: Optional[str] = None,
    ylabel: Optional[str] = None,
    xleft: Optional[float] = None,
    xright: Optional[float] = None,
    ybottom: Optional[float] = None,
    ytop: Optional[float] = None,
    x_reverse: Optional[bool] = None,
    y_reverse: Optional[bool] = None,
    dpi: Optional[int] = None,
    title: Optional[str] = None,
    plot_lines: Optional[bool] = None,
) -> bool:
    """
    处理输入文件并生成指定类型的光谱图片
    
    参数:
        input_file_path: 输入文件路径
        output_image_path: 输出图片路径
        spectrum_type: 光谱类型，如"IR", "Raman", "ROA", "VCD"等
        **kwargs: 可选参数，用于覆盖默认配置
    """
    try:
        # 检查光谱类型是否支持
        if spectrum_type not in SPECTRUM_CONFIGS:
            print(f"不支持的光谱类型: {spectrum_type}")
            return False
            
        # 收集所有用户参数
        user_params = {
            # 振动参数
            "FWHM": FWHM,
            "incident_wavelength": incident_wavelength,
            "temperature": temperature,
            "scale_factor": scale_factor,
            # 核磁参数
            "nucleus": nucleus,
            "tms_reference": tms_reference,
            # 绘图参数
            "xlabel": xlabel, "ylabel": ylabel,
            "xleft": xleft, "xright": xright,
            "ybottom": ybottom, "ytop": ytop,
            "x_reverse": x_reverse, "y_reverse": y_reverse,
            "dpi": dpi, "title": title,
            "plot_lines": plot_lines
        }
        
        # 获取合并后的参数
        unified_params = get_unified_params(spectrum_type,** user_params)

        # 1. 生成中间文件
        intermediate_file = generate_intermediate_file(
            input_file_path=input_file_path,
            spectrum_type=spectrum_type,
            params=unified_params["common"]
        )
        if not intermediate_file:
            return False
            
        # 2. 运行Multiwfn处理中间文件
        multiwfn_success = run_multiwfn(
            input_file_path=input_file_path,
            tmp_file_path=intermediate_file
        )
        if not multiwfn_success:
            return False
            
        # 3. 检查数据文件是否生成
        data_files = unified_params["data_files"]
        curve_file = data_files["curve"]
        line_file = data_files["line"] 

        # 检查曲线文件
        if not os.path.exists(curve_file) or os.path.getsize(curve_file) == 0:
            print(f"Multiwfn未生成 {curve_file}")
            return False
    
        # 如果需要绘制线，检查线文件
        if unified_params["plot"]["plot_lines"] and (not line_file or not os.path.exists(line_file) or os.path.getsize(line_file) == 0):
            print(f"需要绘制光谱线，但未找到线文件 {line_file}")
            return False
            
        # 4. 运行绘图脚本生成图像
        plot_success = run_plot_script(
            curve_file=curve_file,
            line_file=line_file if unified_params["plot"]["plot_lines"] else None,
            output_image=output_image_path,
            xlabel=unified_params["plot"]["xlabel"],
            ylabel=unified_params["plot"]["ylabel"],
            xleft=unified_params["plot"]["xleft"],
            xright=unified_params["plot"]["xright"],
            ybottom=unified_params["plot"]["ybottom"],
            ytop=unified_params["plot"]["ytop"],
            x_reverse=unified_params["plot"]["x_reverse"],
            y_reverse=unified_params["plot"]["y_reverse"],
            dpi=unified_params["plot"]["dpi"],
            title=unified_params["plot"]["title"]
        )
        if not plot_success:
            return False
            
        return True
        
    except Exception as e:
        print(f"处理文件时出错: {str(e)}")
        return False
    finally:
        # 清理中间文件
        if 'intermediate_file' in locals() and os.path.exists(intermediate_file):
            try:
                os.remove(intermediate_file)
            except:
                pass

        # 清理数据文件
        if spectrum_type in SPECTRUM_CONFIGS:
            data_files = SPECTRUM_CONFIGS[spectrum_type]["data_files"]
            for file_path in data_files.values():
                if os.path.exists(file_path):
                    try:
                        os.remove(file_path)
                    except:
                        pass

# 4. 统一中间文件生成函数
def generate_intermediate_file(
    input_file_path: str,
    spectrum_type: str,
    params: Dict[str, Any]  # 统一接收参数字典
) -> Optional[str]:
    """根据光谱类型生成特定格式的中间文件"""
    try:
        config = SPECTRUM_CONFIGS[spectrum_type]
        temp_dir = tempfile.gettempdir()
        filename = os.path.basename(input_file_path)
        name, _ = os.path.splitext(filename)
        intermediate_path = os.path.join(temp_dir, f"{name}_{spectrum_type}_intermediate.txt")
        
        # 动态替换模板中的所有参数占位符
        content = [line.format(**params) for line in config["intermediate_content"]]
        
        # 写入中间文件
        with open(intermediate_path, 'w') as f_out:
            f_out.write("\n".join(content) + "\n")
        
        return intermediate_path
    except Exception as e:
        print(f"生成中间文件失败: {str(e)}")
        return None


def run_multiwfn(
    input_file_path: str, 
    tmp_file_path: str
) -> bool:
    """
    运行Multiwfn_noGUI，通过输入重定向传递临时文件内容
    
    参数:
        input_file_path: 用户上传的输入文件路径（如分子结构文件）
        tmp_file_path: 中间指令文件路径（包含Multiwfn的操作指令）
    """
    try:
        # 1. 读取临时指令文件的内容（作为标准输入）
        with open(tmp_file_path, 'r') as f:
            stdin_content = f.read()  # 读取指令内容
        
        # 2. 构造命令：Multiwfn_noGUI + 输入文件路径
        command = [
            MULTIWFN_EXE,  # 可执行文件
            input_file_path  # 第一个位置参数：用户输入文件
        ]
        
        # 3. 执行命令，通过stdin传递临时文件内容（模拟<重定向）
        result = subprocess.run(
            command,
            cwd=MULTIWFN_PATH,  # 在Multiwfn目录执行
            input=stdin_content,  # 关键：将临时文件内容作为标准输入
            capture_output=True,
            text=True,
            timeout=10  # 超时时间（根据实际情况调整）
        )
        
        # 4. 检查执行结果
        if result.returncode != 0:
            print(f"Multiwfn执行失败，返回码: {result.returncode}")
            print(f"错误输出: {result.stderr}")
            return False
            
        print("Multiwfn_noGUI执行成功")
        return True
        
    except FileNotFoundError:
        print(f"临时指令文件不存在: {tmp_file_path}")
        return False
    except subprocess.TimeoutExpired:
        print("Multiwfn执行超时")
        return False
    except Exception as e:
        print(f"运行Multiwfn时出错: {str(e)}")
        return False

def run_plot_script(
    curve_file: str,
    line_file: str,
    output_image: str,
    xlabel: str,
    ylabel: str,
    xleft: float,
    xright: float,
    ybottom: float,
    ytop: float,
    x_reverse: bool,
    y_reverse: bool,
    dpi: int,
    title: str
) -> bool:
    """运行绘图脚本生成图像"""
    try:
        # 构建绘图命令
        command = [
            "python", 
            PLOT_SCRIPT,
            "--input_curve", curve_file,
            "--output", output_image,
            "--xlabel", xlabel,
            "--ylabel", ylabel,
            "--dpi", str(dpi),
            "--title", title
        ]

        # 如果需要绘制线文件，添加线文件参数
        if line_file is not None:
            command.extend(["--input_line", line_file])

        # 添加可选的轴范围参数
        if xleft is not None:
            command.extend(["--xleft", str(xleft)])
        if xright is not None:
            command.extend(["--xright", str(xright)])
        if ybottom is not None:
            command.extend(["--ybottom", str(ybottom)])
        if ytop is not None:
            command.extend(["--ytop", str(ytop)])

        # 处理标志位参数
        if x_reverse:
            command.append("--x_reverse")
        if y_reverse:
            command.append("--y_reverse")

        # 执行命令
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=True
        )
        
        if result.returncode != 0:
            print(f"绘图脚本执行失败: {result.stderr}")
            return False
            
        return True
    except Exception as e:
        print(f"运行绘图脚本失败: {str(e)}")
        return False


def get_image_bytes(
    image_path: str
) -> Optional[BytesIO]:
    """将图片转换为BytesIO对象"""
    try:
        img = Image.open(image_path)
        buf = BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        return buf
    except Exception as e:
        print(f"转换图片为BytesIO失败: {str(e)}")
        return None


def _snapshot_runtime_paths() -> Dict[str, Any]:
    data_files_snapshot: Dict[str, Dict[str, str]] = {}
    for spectrum_name, config in SPECTRUM_CONFIGS.items():
        if "data_files" in config:
            data_files_snapshot[spectrum_name] = dict(config["data_files"])

    return {
        "MULTIWFN_PATH": MULTIWFN_PATH,
        "MULTIWFN_EXE": MULTIWFN_EXE,
        "PLOT_SCRIPT": PLOT_SCRIPT,
        "data_files_snapshot": data_files_snapshot,
    }


def _apply_runtime_path_overrides(
    multiwfn_path: Optional[str] = None,
    plot_script_path: Optional[str] = None,
) -> None:
    global MULTIWFN_PATH, MULTIWFN_EXE, PLOT_SCRIPT

    if multiwfn_path:
        MULTIWFN_PATH = multiwfn_path
        MULTIWFN_EXE = os.path.join(MULTIWFN_PATH, "Multiwfn_noGUI")

        for config in SPECTRUM_CONFIGS.values():
            if "data_files" in config:
                config["data_files"] = {
                    key: os.path.join(MULTIWFN_PATH, os.path.basename(path))
                    for key, path in config["data_files"].items()
                }

    if plot_script_path:
        PLOT_SCRIPT = plot_script_path


def _restore_runtime_paths(snapshot: Dict[str, Any]) -> None:
    global MULTIWFN_PATH, MULTIWFN_EXE, PLOT_SCRIPT

    MULTIWFN_PATH = snapshot["MULTIWFN_PATH"]
    MULTIWFN_EXE = snapshot["MULTIWFN_EXE"]
    PLOT_SCRIPT = snapshot["PLOT_SCRIPT"]

    data_files_snapshot = snapshot.get("data_files_snapshot", {})
    for spectrum_name, saved_data_files in data_files_snapshot.items():
        if spectrum_name in SPECTRUM_CONFIGS and "data_files" in SPECTRUM_CONFIGS[spectrum_name]:
            SPECTRUM_CONFIGS[spectrum_name]["data_files"] = dict(saved_data_files)


def run_spectrum_pipeline(
    input_file_path: str,
    output_image_path: str,
    spectrum_type: str = "IR",
    multiwfn_path: Optional[str] = None,
    plot_script_path: Optional[str] = None,
    **kwargs
) -> Dict[str, Any]:
    """
    Name: run_spectrum_pipeline
    Description: Safe wrapper for running the spectrum generation pipeline.
    Parameters:
    input_file_path: str Input file path for spectrum processing.
    output_image_path: str Output image path.
    spectrum_type: str Spectrum type, default IR.
    multiwfn_path: Optional[str] Override Multiwfn working path.
    plot_script_path: Optional[str] Override plot script path.
    kwargs: Additional parameters forwarded to process_file.
    Returns:
    dict Structured pipeline result.
    """
    response: Dict[str, Any] = {
        "success": False,
        "message": "",
        "input_path": input_file_path,
        "output_path": output_image_path,
        "spectrum_type": spectrum_type,
        "error": None,
    }

    snapshot = _snapshot_runtime_paths()
    stdout_buffer = StringIO()
    stderr_buffer = StringIO()

    try:
        _apply_runtime_path_overrides(
            multiwfn_path=multiwfn_path,
            plot_script_path=plot_script_path,
        )

        with contextlib.redirect_stdout(stdout_buffer), contextlib.redirect_stderr(stderr_buffer):
            success = process_file(
                input_file_path=input_file_path,
                output_image_path=output_image_path,
                spectrum_type=spectrum_type,
                **kwargs,
            )

        captured_logs = "\n".join(
            part for part in [stdout_buffer.getvalue().strip(), stderr_buffer.getvalue().strip()] if part
        )

        if success:
            response["success"] = True
            response["message"] = "Spectrum pipeline completed successfully"
        else:
            response["message"] = "Spectrum pipeline failed"
            response["error"] = captured_logs if captured_logs else "process_file returned False"

        return response
    except Exception as exc:
        response["message"] = "Spectrum pipeline failed with exception"
        response["error"] = str(exc)
        return response
    finally:
        _restore_runtime_paths(snapshot)
