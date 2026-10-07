"""Shared, non-mutating redaction for task diagnostics and persistence."""
from __future__ import annotations

import json
import re
from typing import Any


class Redactor:
    """Learn secrets only from sensitive keys in the original params/config.

    ``text`` masks known values in diagnostics. ``value`` additionally masks
    sensitive dictionary entries, returning new dictionaries/lists. Ordinary
    numeric/bool/null values retain their types: a secret PIN must not erase an
    unrelated count. Numeric secrets are masked in text on token boundaries;
    bool/null/empty values are not learned as global text replacements. Short
    word-like string secrets also use boundaries to avoid erasing ordinary
    words (e.g. password "a" must not turn "failed" into "f***iled").

    Dictionary keys are structural identities and are never rewritten. This
    protects SDK diagnostics, not arbitrary files written by a plugin. Callers
    must keep protocol/status fields and output paths outside text redaction.
    """

    MASK = "***"
    SENSITIVE_KEYS = (
        "password", "passwd", "secret", "token", "api_key", "apikey",
        "credential", "connection", "dsn",
    )

    def __init__(self, *sources: dict[str, Any]):
        replacements: dict[str, bool] = {}
        for source in sources:
            self._collect(source, replacements)
        patterns = []
        for secret, bounded in sorted(replacements.items(), key=lambda item: (-len(item[0]), item[0])):
            escaped = re.escape(secret)
            patterns.append(r"(?<!\w)" + escaped + r"(?!\w)" if bounded else escaped)
        # Persistence may redact an already-redacted Host result again.
        # Preserve the marker even when a source secret itself is "*"/"**".
        self._pattern = re.compile(re.escape(self.MASK) + "|" + "|".join(patterns)) if patterns else None

    @classmethod
    def _sensitive(cls, key: Any) -> bool:
        return any(marker in str(key).lower() for marker in cls.SENSITIVE_KEYS)

    @classmethod
    def _collect(cls, value: Any, replacements: dict[str, bool], *, sensitive: bool = False) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                cls._collect(item, replacements, sensitive=sensitive or cls._sensitive(key))
        elif isinstance(value, (list, tuple)):
            for item in value:
                cls._collect(item, replacements, sensitive=sensitive)
        elif sensitive and isinstance(value, (str, int, float)) and not isinstance(value, bool):
            rendered = str(value)
            if not rendered or rendered == cls.MASK:
                return
            bounded = not isinstance(value, str) or (len(rendered) <= 3 and rendered.isalnum())
            # Diagnostics may use JSON/repr instead of emitting the raw string.
            variants = {rendered}
            if isinstance(value, str):
                variants.update((json.dumps(value)[1:-1], json.dumps(value, ensure_ascii=False)[1:-1], repr(value)[1:-1]))
            for variant in variants:
                # Prefer unrestricted matching if two sources supply the same
                # token both as a string secret and as a numeric secret.
                replacements[variant] = replacements.get(variant, True) and bounded

    def text(self, value: str) -> str:
        """Replace known secrets without modifying the supplied string."""
        return self._pattern.sub(lambda match: self.MASK, value) if self._pattern else value

    def value(self, value: Any) -> Any:
        """Recursively mask sensitive keys and known secrets in text values."""
        if isinstance(value, dict):
            return {
                key: self.MASK if self._sensitive(key) else self.value(item)
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [self.value(item) for item in value]
        if isinstance(value, str):
            return self.text(value)
        return value
