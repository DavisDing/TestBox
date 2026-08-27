from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from testbox.updater import download_and_apply

DEFAULT_MANIFEST_URL = "https://github.com/DavisDing/TestBox/releases/latest/download/update-manifest.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="TestBox incremental updater")
    parser.add_argument("--manifest-url", default=DEFAULT_MANIFEST_URL, help="URL of update-manifest.json")
    default_install_dir = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent
    parser.add_argument("--install-dir", type=Path, default=default_install_dir)
    parser.add_argument("--wait-pid", type=int)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    try:
        result = download_and_apply(args.install_dir, manifest_url=args.manifest_url, wait_for_pid=args.wait_pid, force=args.force)
    except Exception as error:
        print(json.dumps({"status": "failed", "message": str(error)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
