"""Run the full pipeline on a file from the terminal.

    python -m server.cli recording.mp4 --mode clean
    python -m server.cli podcast.mp4 --mode clips --vertical
    python -m server.cli recording.mp4 --no-ai      # fillers and silences only
"""

import argparse
import shutil
import sys
from pathlib import Path

from .pipeline.run import process
from .project import Project


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("file", type=Path)
    ap.add_argument("--mode", choices=["clean", "clips"], default="clean")
    ap.add_argument("--no-ai", action="store_true", help="skip Claude; only remove fillers and dead air")
    ap.add_argument("--vertical", action="store_true", help="crop clips to 9:16")
    args = ap.parse_args()

    p = Project.create(args.file.stem)
    shutil.copy(args.file, p.dir / f"raw{args.file.suffix.lower()}")
    print(f"project {p.id}")
    process(p, args.mode, use_ai=not args.no_ai, opts={"vertical": args.vertical})
    s = p.state
    if s["status"] == "error":
        sys.exit(s["error"])
    src = s["media"]["duration"]
    for o in s["outputs"]:
        print(f"{o['name']}: {p.dir / o['file']}  ({src:.1f}s -> {o['duration']:.1f}s, {o['cuts']} pieces)")
    edits = p.read("edits.json")
    if edits.get("summary"):
        print("\n" + edits["summary"])


if __name__ == "__main__":
    main()
