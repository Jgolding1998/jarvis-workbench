"""Launch the selected Windows features with every device action disarmed."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--feature", choices=("camera", "hand"), required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    if sys.platform != "win32":
        print("Desktop control requires Windows.")
        return 2
    try:
        import cv2
        if args.feature == "hand":
            import mediapipe
            from .install_tools import MODEL_SHA256
            import hashlib
            model = Path(__file__).resolve().parents[1] / "models" / "hand_landmarker.task"
            if not model.is_file() or model.stat().st_size > 12 * 1024 * 1024 or hashlib.sha256(model.read_bytes()).hexdigest() != MODEL_SHA256:
                raise ValueError("Hand model unavailable")
    except (ImportError, OSError, ValueError):
        print("The selected desktop tools are incomplete. Run setup again.")
        return 2
    if args.check:
        print("Desktop prerequisites are ready. No devices were opened.")
        return 0
    from . import server
    flags = ["--port", "0"] + (["--camera", "--allow-lock", "--allow-spreadsheet"] if args.feature == "camera" else ["--allow-hand-control"])
    options = server.build_parser().parse_args(flags)
    server.check_args(parser, options)
    server.serve(options)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
