#!/usr/bin/env python3
"""Read-only Step 1 inventory tool for the standard host source.

This tool parses source with ``ast``. It does not import or start the standard
runtime, open serial ports, mutate its registry, or send firmware commands.
"""

import argparse
import ast
import json
from pathlib import Path


TARGET_CLASSES = {
    "CommsRuntime",
    "BaseModule",
    "TractionModule",
    "ColorSensorModule",
    "LineSensorModule",
    "DistanceSensorModule",
    "Motors",
}


def signature(node: ast.FunctionDef) -> dict:
    positional = [arg.arg for arg in (*node.args.posonlyargs, *node.args.args)]
    keyword_only = [arg.arg for arg in node.args.kwonlyargs]
    return {
        "name": node.name,
        "positional": positional,
        "keyword_only": keyword_only,
        "is_async": isinstance(node, ast.AsyncFunctionDef),
    }


def inspect_file(path: Path) -> dict:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    classes = {}
    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or node.name not in TARGET_CLASSES:
            continue
        properties = set()
        methods = []
        for child in node.body:
            if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if child.name.startswith("_"):
                continue
            if any(
                isinstance(dec, ast.Name) and dec.id == "property"
                for dec in child.decorator_list
            ):
                properties.add(child.name)
            methods.append(signature(child))
        classes[node.name] = {
            "properties": sorted(properties),
            "methods": methods,
        }
    return classes


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="Open-RDK repository root",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    source = args.repo / "host" / "main" / "src" / "openrdk"
    result = {
        "schema_version": 1,
        "source": "standard-host-read-only",
        "files": {
            "ordk_runtime.py": inspect_file(source / "ordk_runtime.py"),
            "modules.py": inspect_file(source / "modules.py"),
        },
    }
    encoded = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(encoded, encoding="utf-8")
    else:
        print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

