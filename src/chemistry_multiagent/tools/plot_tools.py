import json
import os
import shutil
import tempfile
from typing import Any, Dict, Optional

import matplotlib.pyplot as plt
import numpy as np
import process_spectrum
from process_spectrum import SPECTRUM_CONFIGS


def get_supported_spectrum_types() -> list:
    """Return supported spectrum types defined by process_spectrum."""
    return list(SPECTRUM_CONFIGS.keys())


def _coerce_input_paths(input_file_path: Any) -> list[str]:
    if isinstance(input_file_path, (list, tuple, set)):
        items = [str(item).strip() for item in input_file_path if str(item).strip()]
    else:
        text = str(input_file_path or "").strip()
        if not text:
            return []
        items = [part.strip() for part in text.replace("\n", ",").split(",") if part.strip()]
    return [os.path.abspath(os.path.expanduser(item)) for item in items]


def _extract_ir_payload(raw: Dict[str, Any]) -> Dict[str, Any]:
    payload = raw.get("result") if isinstance(raw.get("result"), dict) else raw
    return payload if isinstance(payload, dict) else {}


def _load_ir_series_from_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    payload = _extract_ir_payload(raw if isinstance(raw, dict) else {})
    freqs = payload.get("frequencies")
    intensities = payload.get("ir_intensities")
    if not isinstance(freqs, list) or not isinstance(intensities, list):
        raise ValueError(f"JSON缺少 frequencies / ir_intensities: {os.path.basename(path)}")
    points = []
    for freq, inten in zip(freqs, intensities):
        try:
            fval = float(freq)
            ival = float(inten)
        except (TypeError, ValueError):
            continue
        if fval > 0 and ival >= 0:
            points.append((fval, ival))
    if not points:
        raise ValueError(f"JSON缺少可用IR峰数据: {os.path.basename(path)}")
    return {
        "label": os.path.splitext(os.path.basename(path))[0],
        "frequencies": [p[0] for p in points],
        "intensities": [p[1] for p in points],
    }


def _render_ir_plot_from_json(
    series: list[Dict[str, Any]],
    output_image_path: str,
    title: Optional[str] = None,
    xlabel: Optional[str] = None,
    ylabel: Optional[str] = None,
    xleft: Optional[float] = None,
    xright: Optional[float] = None,
    ybottom: Optional[float] = None,
    ytop: Optional[float] = None,
    x_reverse: Optional[bool] = None,
    y_reverse: Optional[bool] = None,
    dpi: Optional[int] = None,
    fwhm: float = 20.0,
) -> None:
    all_freqs = [freq for item in series for freq in item["frequencies"]]
    if not all_freqs:
        raise ValueError("没有可绘制的IR频率数据")
    x_min = min(all_freqs) - 200.0
    x_max = max(all_freqs) + 200.0
    x = np.linspace(x_min, x_max, 4000)
    sigma = max(fwhm, 1e-6) / 2.354820045

    fig, ax = plt.subplots(figsize=(10, 6))
    cmap = plt.get_cmap("tab10")
    for idx, item in enumerate(series):
        y = np.zeros_like(x)
        for freq, inten in zip(item["frequencies"], item["intensities"]):
            y += float(inten) * np.exp(-0.5 * ((x - float(freq)) / sigma) ** 2)
        color = cmap(idx % 10)
        ax.plot(x, y, linewidth=1.4, color=color, label=item["label"])
        ax.vlines(item["frequencies"], 0, item["intensities"], colors=[color], alpha=0.3, linewidth=0.8)

    ax.set_xlabel(xlabel or "Wavenumber (cm⁻¹)", fontsize=12)
    ax.set_ylabel(ylabel or "IR intensity (arb. units)", fontsize=12)
    ax.set_title(title or ("IR Spectrum Comparison" if len(series) > 1 else "IR Spectrum"), fontsize=14)
    ax.grid(True, linestyle="--", alpha=0.5)
    if len(series) > 1:
        ax.legend()
    if xleft is not None or xright is not None:
        left = xleft if xleft is not None else ax.get_xlim()[0]
        right = xright if xright is not None else ax.get_xlim()[1]
        ax.set_xlim(left=left, right=right)
    if ybottom is not None or ytop is not None:
        bottom = ybottom if ybottom is not None else ax.get_ylim()[0]
        top = ytop if ytop is not None else ax.get_ylim()[1]
        ax.set_ylim(bottom=bottom, top=top)
    if x_reverse is True or (x_reverse is None and (xleft is not None and xright is not None and xleft > xright)):
        ax.invert_xaxis()
    if y_reverse:
        ax.invert_yaxis()
    plt.tight_layout()
    try:
        plt.savefig(output_image_path, dpi=dpi or 300, bbox_inches="tight")
    finally:
        plt.close(fig)


def draw_ir_spectrum_from_json(
    input_file_path: Any,
    output_image_path: str,
    title: Optional[str] = None,
    xlabel: Optional[str] = None,
    ylabel: Optional[str] = None,
    xleft: Optional[float] = None,
    xright: Optional[float] = None,
    ybottom: Optional[float] = None,
    ytop: Optional[float] = None,
    x_reverse: Optional[bool] = None,
    y_reverse: Optional[bool] = None,
    dpi: Optional[int] = None,
    FWHM: Optional[float] = None,
) -> Dict[str, Any]:
    paths = _coerce_input_paths(input_file_path)
    if not paths:
        return {"success": False, "message": "缺少可用的JSON输入文件", "output_image_path": output_image_path}
    series = [_load_ir_series_from_json(path) for path in paths]
    _render_ir_plot_from_json(
        series,
        output_image_path=output_image_path,
        title=title,
        xlabel=xlabel,
        ylabel=ylabel,
        xleft=xleft,
        xright=xright,
        ybottom=ybottom,
        ytop=ytop,
        x_reverse=x_reverse,
        y_reverse=y_reverse,
        dpi=dpi,
        fwhm=FWHM or 20.0,
    )
    return {
        "success": True,
        "message": "IR spectrum plotted from JSON data",
        "output_image_path": output_image_path,
        "series_count": len(series),
        "inputs": paths,
    }


def draw_spectrum_from_file(
    input_file_path: str,
    output_image_path: str,
    spectrum_type: str = "IR",
    FWHM: Optional[float] = None,
    incident_wavelength: Optional[float] = None,
    temperature: Optional[float] = None,
    scale_factor: Optional[float] = None,
    nucleus: Optional[str] = None,
    tms_reference: Optional[float] = None,
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
    include_image_bytes: bool = False,
) -> Dict[str, Any]:
    """
    Name: draw_spectrum_from_file
    Description: Generate a spectrum image from an input file using process_spectrum.
    Parameters:
    input_file_path: str Path to input quantum-chemistry output file.
    output_image_path: str Output image path.
    spectrum_type: str Spectrum type key supported by process_spectrum.
    include_image_bytes: bool Whether to include PNG bytes in result.
    Returns:
    dict Structured processing result.
    """
    result: Dict[str, Any] = {
        "success": False,
        "spectrum_type": spectrum_type,
        "input_file_path": input_file_path,
        "output_image_path": output_image_path,
        "supported_types": get_supported_spectrum_types(),
        "image_bytes": None,
        "message": "",
    }

    if spectrum_type not in SPECTRUM_CONFIGS:
        result["message"] = f"Unsupported spectrum_type: {spectrum_type}"
        return result

    success = process_spectrum.process_file(
        input_file_path=input_file_path,
        output_image_path=output_image_path,
        spectrum_type=spectrum_type,
        FWHM=FWHM,
        incident_wavelength=incident_wavelength,
        temperature=temperature,
        scale_factor=scale_factor,
        nucleus=nucleus,
        tms_reference=tms_reference,
        xlabel=xlabel,
        ylabel=ylabel,
        xleft=xleft,
        xright=xright,
        ybottom=ybottom,
        ytop=ytop,
        x_reverse=x_reverse,
        y_reverse=y_reverse,
        dpi=dpi,
        title=title,
        plot_lines=plot_lines,
    )

    if not success:
        result["message"] = f"{spectrum_type} spectrum processing failed"
        return result

    if include_image_bytes:
        image_buf = process_spectrum.get_image_bytes(output_image_path)
        if image_buf is None:
            result["message"] = "Image generated but failed to read image bytes"
            return result
        result["image_bytes"] = image_buf.getvalue()

    result["success"] = True
    result["message"] = "Spectrum generated successfully"
    return result


def _cleanup_directory(path: str) -> None:
    try:
        shutil.rmtree(path, ignore_errors=True)
    except Exception:
        pass


async def draw_spectrum(
    file: Any,
    background_tasks: Optional[Any] = None,
    spectrum_type: str = "IR",
    FWHM: Optional[float] = None,
    incident_wavelength: Optional[float] = None,
    temperature: Optional[float] = None,
    scale_factor: Optional[float] = None,
    nucleus: Optional[str] = None,
    tms_reference: Optional[float] = None,
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
) -> Dict[str, Any]:
    """
    Async upload-style helper.

    This keeps the original endpoint-like behavior, but returns a structured dict instead
    of framework-specific response objects.
    """
    tmpdir = tempfile.mkdtemp(prefix=f"{spectrum_type.lower()}_spectrum_")

    try:
        filename = getattr(file, "filename", "input.dat")
        input_file_path = os.path.join(tmpdir, filename)

        content = await file.read()
        with open(input_file_path, "wb") as out_file:
            out_file.write(content)

        output_image = os.path.join(tmpdir, f"{spectrum_type.lower()}_spectrum.png")

        result = draw_spectrum_from_file(
            input_file_path=input_file_path,
            output_image_path=output_image,
            spectrum_type=spectrum_type,
            FWHM=FWHM,
            incident_wavelength=incident_wavelength,
            temperature=temperature,
            scale_factor=scale_factor,
            nucleus=nucleus,
            tms_reference=tms_reference,
            xlabel=xlabel,
            ylabel=ylabel,
            xleft=xleft,
            xright=xright,
            ybottom=ybottom,
            ytop=ytop,
            x_reverse=x_reverse,
            y_reverse=y_reverse,
            dpi=dpi,
            title=title,
            plot_lines=plot_lines,
            include_image_bytes=True,
        )

        result["tmpdir"] = tmpdir
        if background_tasks is not None and hasattr(background_tasks, "add_task"):
            background_tasks.add_task(_cleanup_directory, tmpdir)
            result["cleanup_scheduled"] = True
        else:
            result["cleanup_scheduled"] = False

        return result
    except Exception as exc:
        _cleanup_directory(tmpdir)
        return {
            "success": False,
            "spectrum_type": spectrum_type,
            "tmpdir": tmpdir,
            "message": str(exc),
        }


def plot_tools(
    input_file_path: Any,
    output_image_path: str,
    spectrum_type: str = "IR",
    FWHM: Optional[float] = None,
    incident_wavelength: Optional[float] = None,
    temperature: Optional[float] = None,
    scale_factor: Optional[float] = None,
    nucleus: Optional[str] = None,
    tms_reference: Optional[float] = None,
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
) -> Dict[str, Any]:
    paths = _coerce_input_paths(input_file_path)
    if not paths:
        return {"success": False, "message": "缺少可用输入文件", "output_image_path": output_image_path}
    if all(path.lower().endswith(".json") for path in paths):
        return draw_ir_spectrum_from_json(
            input_file_path=paths,
            output_image_path=output_image_path,
            title=title,
            xlabel=xlabel,
            ylabel=ylabel,
            xleft=xleft,
            xright=xright,
            ybottom=ybottom,
            ytop=ytop,
            x_reverse=x_reverse,
            y_reverse=y_reverse,
            dpi=dpi,
            FWHM=FWHM,
        )
    if len(paths) != 1:
        return {"success": False, "message": "非JSON输入仅支持单个文件", "output_image_path": output_image_path}
    return draw_spectrum_from_file(
        input_file_path=paths[0],
        output_image_path=output_image_path,
        spectrum_type=spectrum_type,
        FWHM=FWHM,
        incident_wavelength=incident_wavelength,
        temperature=temperature,
        scale_factor=scale_factor,
        nucleus=nucleus,
        tms_reference=tms_reference,
        xlabel=xlabel,
        ylabel=ylabel,
        xleft=xleft,
        xright=xright,
        ybottom=ybottom,
        ytop=ytop,
        x_reverse=x_reverse,
        y_reverse=y_reverse,
        dpi=dpi,
        title=title,
        plot_lines=plot_lines,
    )


run_tool = plot_tools
