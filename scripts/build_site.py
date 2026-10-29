"""Build the hosted planner without copying desktop code or local configuration."""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
PAGES = ("index.html", "site.css", "site.js", "privacy.html", "dashboard-preview.png", "favicon.svg")
SETUP = ("setup.html", "setup.js", "setup.css", "setup-catalog.json", "provider-catalog.json")


def build(destination: Path):
    if destination.is_symlink():
        raise ValueError("Output directory must not be a symbolic link")
    destination = destination.resolve()
    if destination == ROOT or ROOT.is_relative_to(destination) or destination in {(ROOT / "web").resolve(), (ROOT / "website").resolve()}:
        raise ValueError("Choose a separate output directory")
    if destination.exists():
        allowed = set(PAGES + SETUP)
        if any(path.name not in allowed or not path.is_file() or path.is_symlink() for path in destination.iterdir()):
            raise ValueError("Output contains unexpected files; choose an empty output directory")
    destination.mkdir(parents=True, exist_ok=True)
    for name in PAGES:
        shutil.copyfile(ROOT / "website" / name, destination / name)
    for name in SETUP:
        source = ROOT / "web" / name
        if name == "setup.html":
            content = source.read_text(encoding="utf-8")
            marker = 'name="jarvis-mode" content="auto"'
            if content.count(marker) != 1:
                raise ValueError("The local setup mode marker is missing or ambiguous")
            content = content.replace(marker, 'name="jarvis-mode" content="hosted"')
            destination.joinpath(name).write_text(content, encoding="utf-8")
        else:
            shutil.copyfile(source, destination / name)
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "site-dist")
    result = build(parser.parse_args().output)
    print("Built static planner: " + str(result))


if __name__ == "__main__":
    main()
