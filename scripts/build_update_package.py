from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from testbox.updater import create_update_package


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a TestBox full or incremental Windows update package")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--previous-manifest", type=Path)
    parser.add_argument("--package-base-url")
    args = parser.parse_args()
    manifest = create_update_package(
        args.root,
        version=args.version,
        output=args.output,
        manifest_output=args.manifest,
        previous_manifest=args.previous_manifest,
        package_base_url=args.package_base_url,
    )
    print(f"Created update package {args.output} ({len(manifest['changed_files'])} changed, {len(manifest['deleted_files'])} deleted)")


if __name__ == "__main__":
    main()
