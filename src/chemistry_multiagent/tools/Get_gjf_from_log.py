import argparse

from output_parser import GaussianParser


class GjfGenerator:
    """Generate .gjf files from Gaussian output files."""

    @classmethod
    def generate_gjf(
        cls,
        input_file_path,
        output_gjf_path,
        route_line="# opt freq b3lyp/6-31g(d)",
        title="Generated from output file",
        chk_file=None,
    ):
        """
        Name: generate_gjf
        Description: Generate a Gaussian .gjf input file from a Gaussian output file by extracting the final optimized geometry.
        Parameters:
        input_file_path: str Path to Gaussian output (.log/.out).
        output_gjf_path: str Path to write generated .gjf file.
        route_line: str Gaussian route line (starts with #).
        title: str Title line for the .gjf file.
        chk_file: Optional[str] Checkpoint filename to include as %chk.
        Returns:
        str Generated .gjf file path.
        """
        properties_to_extract = ["elements", "coordinates", "charge", "mult", "opt_done"]
        parsed_data = GaussianParser.parse_file(
            input_file_path,
            properties=properties_to_extract,
            include_metadata=False,
        )

        if not parsed_data.get("opt_done", False):
            raise ValueError("Optimization was not completed (opt_done=False).")

        elements = parsed_data.get("elements")
        coordinates = parsed_data.get("coordinates")
        charge = parsed_data.get("charge")
        mult = parsed_data.get("mult")

        if not all([elements, coordinates, charge is not None, mult is not None]):
            raise ValueError("Missing required data: elements/coordinates/charge/multiplicity.")

        if isinstance(coordinates, list) and len(coordinates) > 0:
            final_coords = coordinates[-1]
        else:
            raise ValueError("Invalid coordinates: could not get final optimized geometry.")

        if len(elements) != len(final_coords):
            raise ValueError("Element count does not match coordinate count.")

        gjf_content = []
        if chk_file:
            gjf_content.append(f"%chk={chk_file}")

        route_line = route_line.strip()
        if not route_line.startswith("#"):
            route_line = f"# {route_line}"
        gjf_content.append(route_line)

        gjf_content.append("")
        gjf_content.append(title)
        gjf_content.append("")
        gjf_content.append(f"{charge} {mult}")

        for elem, coord in zip(elements, final_coords):
            try:
                x, y, z = float(coord[0]), float(coord[1]), float(coord[2])
            except (ValueError, TypeError) as exc:
                raise ValueError(f"Invalid coordinate value: {coord}") from exc
            gjf_content.append(f"{elem:<2} {x:>14.8f} {y:>14.8f} {z:>14.8f}")

        gjf_content.append("")
        gjf_content.append("")

        with open(output_gjf_path, "w", encoding="utf-8") as file_obj:
            file_obj.write("\n".join(gjf_content))

        return output_gjf_path


def get_gjf_from_log(
    input_file_path,
    output_gjf_path,
    route_line="# opt freq b3lyp/6-31g(d)",
    title="Generated from output file",
    chk_file=None,
):
    """
    Name: get_gjf_from_log
    Description: Tool wrapper that creates a Gaussian .gjf from an optimized Gaussian log/out file.
    Parameters:
    input_file_path: str Path to Gaussian output file.
    output_gjf_path: str Path to output .gjf file.
    route_line: str Gaussian route section.
    title: str Gaussian input title line.
    chk_file: Optional[str] Checkpoint filename.
    Returns:
    str Path to generated .gjf file.
    """
    return GjfGenerator.generate_gjf(
        input_file_path=input_file_path,
        output_gjf_path=output_gjf_path,
        route_line=route_line,
        title=title,
        chk_file=chk_file,
    )


def _build_cli_parser():
    parser = argparse.ArgumentParser(description="Generate .gjf from Gaussian output file")
    parser.add_argument("input_file_path", help="Path to Gaussian output (.log/.out)")
    parser.add_argument("output_gjf_path", help="Output .gjf path")
    parser.add_argument("--route_line", default="# opt freq b3lyp/6-31g(d)")
    parser.add_argument("--title", default="Generated from output file")
    parser.add_argument("--chk_file", default=None)
    return parser


if __name__ == "__main__":
    cli_args = _build_cli_parser().parse_args()
    generated_file = get_gjf_from_log(
        input_file_path=cli_args.input_file_path,
        output_gjf_path=cli_args.output_gjf_path,
        route_line=cli_args.route_line,
        title=cli_args.title,
        chk_file=cli_args.chk_file,
    )
    print(generated_file)
