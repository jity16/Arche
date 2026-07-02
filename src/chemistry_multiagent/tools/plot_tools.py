import os
import shutil
import tempfile
from typing import Any, Dict, Optional

import process_spectrum
from process_spectrum import SPECTRUM_CONFIGS


def get_supported_spectrum_types() -> list:
    """Return supported spectrum types defined by process_spectrum."""
    return list(SPECTRUM_CONFIGS.keys())


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
