import argparse
from types import SimpleNamespace
from typing import Any, Dict, Optional

import matplotlib.pyplot as plt
import numpy as np


def parse_args():
    parser = argparse.ArgumentParser(description="Plot spectrum data")
    parser.add_argument("--input_curve", required=True, help="Path to broadened spectrum curve data")
    parser.add_argument("--input_line", help="Path to theoretical isolated line data (optional)")
    parser.add_argument("--output", required=True, help="Output image path")
    parser.add_argument("--xlabel", default="X Label", help="X-axis label")
    parser.add_argument("--ylabel", default="Y Label", help="Y-axis label")
    parser.add_argument("--xleft", type=float, help="X-axis left limit")
    parser.add_argument("--xright", type=float, help="X-axis right limit")
    parser.add_argument("--ybottom", type=float, help="Y-axis bottom limit")
    parser.add_argument("--ytop", type=float, help="Y-axis top limit")
    parser.add_argument("--x_reverse", action="store_true", help="Reverse X-axis")
    parser.add_argument("--y_reverse", action="store_true", help="Reverse Y-axis")
    parser.add_argument("--dpi", type=int, default=300, help="Image DPI")
    parser.add_argument("--title", default="Title", help="Plot title")
    return parser.parse_args()


def load_data(curve_path):
    data = np.loadtxt(curve_path, comments="#", delimiter=None)
    if data.ndim != 2 or data.shape[1] < 2:
        raise ValueError("Curve file must contain at least two columns")
    return data[:, 0], data[:, 1]


def load_line_data(line_path):
    line_data = np.loadtxt(line_path, comments="#", delimiter=None)
    if line_data.ndim != 2 or line_data.shape[1] < 2:
        raise ValueError("Line file must contain at least two columns")
    return line_data


def plot_spectrum(x, y, line_data, args):
    plt.style.use("default")
    fig, ax = plt.subplots(figsize=(10, 6))

    ax.plot(x, y, color="red", linewidth=1.2)

    if line_data is not None:
        num_lines = len(line_data) // 3
        for i in range(num_lines):
            start_idx = i * 3
            end_idx = start_idx + 3
            line_points = line_data[start_idx:end_idx]
            line_x = line_points[:, 0]
            line_y = line_points[:, 1]
            ax.plot(line_x, line_y, color="black", linewidth=0.8)

    ax.set_xlabel(args.xlabel, fontsize=12)
    ax.set_ylabel(args.ylabel, fontsize=12)
    ax.set_title(args.title, fontsize=14)

    if args.xleft is not None:
        ax.set_xlim(left=args.xleft)
    if args.xright is not None:
        ax.set_xlim(right=args.xright)
    if args.ybottom is not None:
        ax.set_ylim(bottom=args.ybottom)
    if args.ytop is not None:
        ax.set_ylim(top=args.ytop)

    if args.x_reverse:
        ax.invert_xaxis()
    if args.y_reverse:
        ax.invert_yaxis()

    ax.grid(True, linestyle="--", alpha=0.7)
    plt.tight_layout()

    try:
        plt.savefig(args.output, dpi=args.dpi, bbox_inches="tight")
    finally:
        plt.close(fig)


def generate_spectrum_plot(
    input_curve: str,
    output: str,
    input_line: Optional[str] = None,
    xlabel: str = "X Label",
    ylabel: str = "Y Label",
    xleft: Optional[float] = None,
    xright: Optional[float] = None,
    ybottom: Optional[float] = None,
    ytop: Optional[float] = None,
    x_reverse: bool = False,
    y_reverse: bool = False,
    dpi: int = 300,
    title: str = "Title",
) -> Dict[str, Any]:
    """
    Name: generate_spectrum_plot
    Description: Generate a spectrum image from curve data and optional isolated line data.
    Parameters:
    input_curve: str Path to curve data file.
    output: str Output image path.
    input_line: Optional[str] Path to line data file.
    Returns:
    dict Structured plot result.
    """
    result: Dict[str, Any] = {
        "success": False,
        "input_curve": input_curve,
        "input_line": input_line,
        "output": output,
        "curve_points": 0,
        "line_points": 0,
        "message": "",
    }

    try:
        args = SimpleNamespace(
            input_curve=input_curve,
            input_line=input_line,
            output=output,
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
        )

        x, y = load_data(args.input_curve)
        line_data = load_line_data(args.input_line) if args.input_line else None
        plot_spectrum(x, y, line_data, args)

        result["curve_points"] = len(x)
        result["line_points"] = 0 if line_data is None else len(line_data)
        result["success"] = True
        result["message"] = "Spectrum plot generated"
        return result
    except Exception as exc:
        result["message"] = str(exc)
        return result


def plot_spectrum_from_files(
    input_curve,
    output,
    input_line=None,
    xlabel="X Label",
    ylabel="Y Label",
    xleft=None,
    xright=None,
    ybottom=None,
    ytop=None,
    x_reverse=False,
    y_reverse=False,
    dpi=300,
    title="Title",
):
    """Compatibility wrapper returning only output path."""
    result = generate_spectrum_plot(
        input_curve=input_curve,
        output=output,
        input_line=input_line,
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
    )
    if not result["success"]:
        raise RuntimeError(result["message"])
    return output


def main():
    args = parse_args()
    result = generate_spectrum_plot(
        input_curve=args.input_curve,
        output=args.output,
        input_line=args.input_line,
        xlabel=args.xlabel,
        ylabel=args.ylabel,
        xleft=args.xleft,
        xright=args.xright,
        ybottom=args.ybottom,
        ytop=args.ytop,
        x_reverse=args.x_reverse,
        y_reverse=args.y_reverse,
        dpi=args.dpi,
        title=args.title,
    )
    if not result["success"]:
        raise SystemExit(result["message"])


if __name__ == "__main__":
    main()
