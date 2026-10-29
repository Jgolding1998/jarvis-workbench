"""Start the local setup wizard in safe demo mode and open it in the browser.

Run with: python -m jarvis.launcher
Keep this file free of newer syntax so an old Python can still print the help message.
"""
from __future__ import annotations

import argparse
import sys
import webbrowser

PYTHON_URL = "https://www.python.org/downloads/"
MINIMUM = (3, 11)


def python_ok(version=None):
    return tuple((version or sys.version_info)[:2]) >= MINIMUM


def python_help():
    return ("Jarvis needs Python 3.11 or newer. This is Python %d.%d.\n"
            "Download the official installer from %s and run this again.\n"
            "Nothing was installed or changed." % (sys.version_info[0], sys.version_info[1], PYTHON_URL))


def main(argv=None, opener=webbrowser.open):
    parser = argparse.ArgumentParser(description="Open the Jarvis setup wizard on this computer (safe demo mode).")
    parser.add_argument("--port", type=int, default=0, help="Local port. Default: any free port")
    parser.add_argument("--no-browser", action="store_true", help="Print the address instead of opening a browser")
    parser.add_argument("--check", action="store_true", help="Check prerequisites and exit without starting anything")
    args = parser.parse_args(argv)
    if not python_ok():
        print(python_help())
        return 1
    from . import planner, server
    planner.load_catalog()
    if args.check:
        print("Jarvis prerequisites look ready: Python " + ".".join(map(str, sys.version_info[:3])) + ", setup catalog valid.")
        return 0
    if not 0 <= args.port <= 65535:
        parser.error("Port must be 0 to 65535")
    print("Opening the setup wizard in safe demo mode. No camera, microphone, mouse, or Windows action is used.")
    print("To stop Jarvis, choose Exit in the wizard or press Ctrl+C here.")
    server.serve(server.build_parser().parse_args(["--demo", "--port", str(args.port)]), open_path="/setup",
                 opener=None if args.no_browser else opener)
    return 0


if __name__ == "__main__":
    sys.exit(main())
