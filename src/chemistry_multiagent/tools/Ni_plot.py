import argparse
import json
from typing import Any, Dict, Optional

from plot_spectrum import generate_spectrum_plot


def plot_ni_spectrum(
    input_curve: str,
    output: str,
    input_line: Optional[str] = None,
    xlabel: str = "Wavenumber (cm^-1)",
    ylabel: str = "Intensity",
    xleft: Optional[float] = None,
    xright: Optional[float] = None,
    ybottom: Optional[float] = None,
    ytop: Optional[float] = None,
    x_reverse: bool = False,
    y_reverse: bool = False,
    dpi: int = 300,
    title: str = "Ni Spectrum",
) -> Dict[str, Any]:
    """
    Name: plot_ni_spectrum
    Description: Convenience wrapper for plotting Ni-related spectrum curves.
    Parameters:
    input_curve: str Path to curve file.
    output: str Output image path.
    input_line: Optional[str] Optional isolated line data file.
    Returns:
    dict Structured plotting result.
    """
    return generate_spectrum_plot(
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


def _build_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Plot Ni spectrum")
    parser.add_argument("--input_curve", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--input_line", default=None)
    parser.add_argument("--xlabel", default="Wavenumber (cm^-1)")
    parser.add_argument("--ylabel", default="Intensity")
    parser.add_argument("--xleft", type=float, default=None)
    parser.add_argument("--xright", type=float, default=None)
    parser.add_argument("--ybottom", type=float, default=None)
    parser.add_argument("--ytop", type=float, default=None)
    parser.add_argument("--x_reverse", action="store_true")
    parser.add_argument("--y_reverse", action="store_true")
    parser.add_argument("--dpi", type=int, default=300)
    parser.add_argument("--title", default="Ni Spectrum")
    return parser


def main() -> None:
    args = _build_cli_parser().parse_args()
    result = plot_ni_spectrum(
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
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
