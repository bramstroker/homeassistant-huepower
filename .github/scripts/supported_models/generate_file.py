"""Update the manifest file."""
import glob
import json
import os
import sys
from pathlib import Path

sys.path.insert(1, os.path.abspath(os.path.join(Path(__file__), "../../../../custom_components/powercalc")))

from aliases import MODEL_DIRECTORY_MAPPING, MANUFACTURER_DIRECTORY_MAPPING

from pytablewriter import MarkdownTableWriter


def generate_supported_model_list():
    writer = MarkdownTableWriter()
    writer.header_list = [
        "manufacturer",
        "model id",
        "name",
        "calculation modes",
        "color modes",
        "aliases"
    ]

    """Generate static file containing the supported models."""
    project_root = os.path.realpath(
        os.path.join(os.path.abspath(__file__), "../../../../")
    )
    with open(os.path.join(project_root, "docs/supported_models.md"), "w") as md_file:

        rows = []
        for json_path in glob.glob(
            f"{project_root}/custom_components/powercalc/data/*/*/model.json",
            recursive=True,
        ):
            with open(json_path) as json_file:
                model_directory = os.path.dirname(json_path)
                model_data = json.load(json_file)
                model = os.path.basename(model_directory)
                manufacturer = os.path.basename(os.path.dirname(model_directory))
                supported_modes = model_data["supported_modes"]
                name = model_data["name"]
                color_modes = get_color_modes(model_directory)
                aliases = get_aliases(manufacturer, model)
                rows.append(
                    [
                        manufacturer,
                        model,
                        name,
                        ",".join(supported_modes),
                        ",".join(color_modes),
                        ",".join(aliases)
                    ]
                )

        rows = sorted(rows, key=lambda x: (x[0], x[1]))
        writer.value_matrix = rows
        writer.table_name = f"Supported models ({len(rows)} total)"
        writer.dump(md_file)
    print("Generated supported_models.md")


def get_color_modes(model_directory: str) -> list:
    color_modes = set()
    for path in glob.glob(f"{model_directory}/**/*.csv.gz", recursive=True):
        filename = os.path.basename(path)
        index = filename.index(".")
        color_mode = filename[:index]
        color_modes.add(color_mode)
    return color_modes

def get_aliases(manufacturer_dir: str, model: str) -> list:
    manufacturer = get_manufacturer_by_directory_name(manufacturer_dir)
    if manufacturer is None:
        return []

    model_aliases = MODEL_DIRECTORY_MAPPING.get(manufacturer)
    if model_aliases is None:
        return []
    
    aliases = list()
    for alias, model_id in model_aliases.items():
        if model == model_id:
            aliases.append(alias)
    
    return aliases

def get_manufacturer_by_directory_name(search_directory: str) -> str|None:
    for manufacturer, directory in MANUFACTURER_DIRECTORY_MAPPING.items():
        if search_directory == directory:
            return manufacturer
    
    return None

generate_supported_model_list()
