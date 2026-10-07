"""Setuptools build hook: ship official plugin resources without copying caches."""
from __future__ import annotations

from pathlib import Path
import shutil

from setuptools.command.build_py import build_py


class BundledBuildPy(build_py):
    """Keep repository plugins authoritative; assemble resources only in build/."""

    def run(self):
        super().run()
        source = Path(__file__).resolve().parent / "plugins"
        destination = Path(self.build_lib) / "testbox" / "_bundled_plugins"
        if destination.exists():
            # This is generated build output, never a user installation.
            shutil.rmtree(destination)
        for plugin in sorted(source.iterdir()):
            if not plugin.is_dir() or not (plugin / "manifest.yaml").is_file():
                continue
            for item in sorted(plugin.rglob("*")):
                relative = item.relative_to(plugin)
                if "__pycache__" in relative.parts or "tests" in relative.parts:
                    continue
                if item.suffix in {".pyc", ".pyo"}:
                    continue
                if relative.parts[0] not in {"src", "schemas", "config", "data", "manifest.yaml", "README.md", "requirements.txt"}:
                    continue
                if item.is_symlink():
                    raise ValueError(f"Bundled plugin resources cannot be links: {item}")
                if item.is_file():
                    target = destination / plugin.name / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(item, target)
