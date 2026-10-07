from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from testbox.updater import apply_update, download_and_apply

RELEASE_DOWNLOAD_BASE_URL = "https://github.com/DavisDing/TestBox/releases/latest/download"


def updater_component() -> str | None:
    if not getattr(sys, "frozen", False):
        return None
    components = {"testbox-cli-updater": "cli", "testbox-gui-updater": "gui"}
    component = components.get(Path(sys.executable).stem.lower())
    if component is None:
        raise ValueError("无法识别更新器组件，请使用对应 CLI/GUI 完整安装器")
    return component


def default_manifest_url() -> str:
    """Return the release manifest matching the installed package channel."""
    channel = (updater_component() or "gui").upper()
    return f"{RELEASE_DOWNLOAD_BASE_URL}/TestBox-{channel}-update-manifest.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="TestBox incremental updater")
    parser.add_argument("--manifest-url", help="URL of the channel update manifest")
    parser.add_argument("--package", type=Path, help="local full or incremental update ZIP")
    default_install_dir = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent
    parser.add_argument("--install-dir", type=Path, default=default_install_dir)
    parser.add_argument("--wait-pid", type=int)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--result-file", type=Path, help="write UTF-8 diagnostics for the installer")
    args = parser.parse_args()
    exit_code = 0
    try:
        component = updater_component()
        result = (
            apply_update(args.install_dir, args.package, wait_for_pid=args.wait_pid, expected_component=component)
            if args.package
            else download_and_apply(args.install_dir, manifest_url=args.manifest_url or default_manifest_url(), wait_for_pid=args.wait_pid, force=args.force, expected_component=component)
        )
    except Exception as error:
        result = {"status": "failed", "message": str(error)}
        exit_code = 1
    text = json.dumps(result, ensure_ascii=False)
    if args.result_file:
        try:
            args.result_file.write_text(text + "\n", encoding="utf-8")
        except OSError as error:
            text = json.dumps({"status": "failed", "message": f"{text}; 无法写入更新诊断: {error}"}, ensure_ascii=False)
            exit_code = 1
    if sys.stdout is not None:
        print(text)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
