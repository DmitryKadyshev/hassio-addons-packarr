"""Read the single release version/image used by CI and Home Assistant."""

import argparse
import ast
import json
import os
from pathlib import Path
import re
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def release_metadata(root=ROOT):
    manifest = (root / "packarr/config.yaml").read_text()
    versions = re.findall(r'^version:\s*[\"\']?(\d+\.\d+\.\d+)[\"\']?\s*$', manifest, re.M)
    images = re.findall(r'^image:\s*(ghcr\.io/[a-z0-9][a-z0-9._/-]+)\s*$', manifest, re.M)
    if len(versions) != 1 or len(images) != 1:
        raise ValueError("Home Assistant must declare exactly one release version and untagged GHCR image")
    version, image = versions[0], images[0]
    if len(image.split("/")) != 3:
        raise ValueError("Unexpected container registry path")
    project_version = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
    module = ast.parse((root / "packarr/src/packarr/__init__.py").read_text())
    runtime_version = next(ast.literal_eval(n.value) for n in module.body if isinstance(n, ast.Assign)
                           and any(isinstance(t, ast.Name) and t.id == "__version__" for t in n.targets))
    if version != project_version or version != runtime_version:
        raise ValueError("App, package, and runtime release versions disagree")
    arch = re.search(r'^arch:\s*\n((?:[ \t]+[^\n]*\n)+)', manifest, re.M)
    if arch is None or set(re.findall(r'^\s*-\s*(\w+)\s*$', arch[1], re.M)) != {"amd64", "aarch64"}:
        raise ValueError("Home Assistant architectures must match the published amd64/arm64 images")
    return {"image": image, "version": version}


def verify_manifest(data):
    platforms = {(entry.get("platform", {}).get("os"), entry.get("platform", {}).get("architecture"))
                 for entry in data.get("manifests", [])}
    if platforms != {("linux", "amd64"), ("linux", "arm64")}:
        raise ValueError("Published manifest must contain exactly linux/amd64 and linux/arm64")
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--github-output", action="store_true")
    parser.add_argument("--verify-manifest", type=Path)
    args = parser.parse_args()
    metadata = release_metadata()
    if args.github_output:
        with open(os.environ["GITHUB_OUTPUT"], "a") as output:
            for key, value in metadata.items():
                output.write(f"{key}={value}\n")
    if args.verify_manifest:
        verify_manifest(json.loads(args.verify_manifest.read_text()))
    print(json.dumps(metadata))


if __name__ == "__main__":
    main()
