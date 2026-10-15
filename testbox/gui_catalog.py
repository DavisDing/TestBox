"""Desktop catalog preferences; contains no Qt or plugin execution logic."""
from __future__ import annotations

import json

from testbox.core.manifest import COMMAND_RE, NAME_RE


class CatalogPreferences:
    RECENT_LIMIT = 12

    def __init__(self, settings):
        self.settings = settings
        self.favorites: list[tuple[str, str]] = []
        self.recent: list[tuple[str, str]] = []
        self.warning = ""
        self._pending = set()
        self.reload()

    def reload(self):
        # Keep unsaved changes usable in this window after a write failure.
        if self._pending:
            return
        self.warning = ""
        self.settings.sync()
        for name in ("favorites", "recent"):
            values = []
            try:
                raw = json.loads(self.settings.value(f"catalog/{name}", "[]"))
                if not isinstance(raw, list):
                    raise ValueError("expected list")
                for item in raw:
                    if (not isinstance(item, dict) or set(item) != {"plugin_name", "command_name"}
                            or not isinstance(item["plugin_name"], str) or not NAME_RE.fullmatch(item["plugin_name"])
                            or not isinstance(item["command_name"], str) or not COMMAND_RE.fullmatch(item["command_name"])):
                        self.warning = "部分目录偏好无效，已忽略；仍可正常使用工具。"
                        continue
                    key = (item["plugin_name"], item["command_name"])
                    if key not in values:
                        values.append(key)
            except (ValueError, TypeError):
                self.warning = "部分目录偏好无法读取，已使用空列表；仍可正常使用工具。"
            setattr(self, name, values[:self.RECENT_LIMIT] if name == "recent" else values)
        if self._settings_failed():
            self.warning = "目录偏好无法读取；仍可正常使用工具。"

    def _settings_failed(self):
        status = self.settings.status()
        return getattr(status, "value", status) != 0

    def _save(self, name):
        self._pending.add(name)
        for pending in self._pending:
            values = [{"plugin_name": plugin, "command_name": command}
                      for plugin, command in getattr(self, pending)]
            self.settings.setValue(f"catalog/{pending}", json.dumps(values, ensure_ascii=False))
        self.settings.sync()
        if self._settings_failed():
            self.warning = "目录偏好未保存；本次操作已生效，下次启动可能无法保留。"
        else:
            self._pending.clear()
            self.warning = ""

    def toggle_favorite(self, key):
        if key in self.favorites:
            self.favorites.remove(key)
        else:
            self.favorites.append(key)
        self._save("favorites")

    def record_recent(self, key):
        self.recent = [key, *(item for item in self.recent if item != key)][:self.RECENT_LIMIT]
        self._save("recent")
