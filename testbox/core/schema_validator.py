"""Small, deterministic JSON Schema validator used by Core and GUI metadata."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class SchemaValidationError(ValueError):
    def __init__(self, message: str, *, code: str = "INVALID_PARAMETER", field: str | None = None):
        super().__init__(message)
        self.code = code
        self.field = field


class SchemaValidator:
    def load(self, path: Path) -> dict[str, Any]:
        if not path.is_file():
            raise SchemaValidationError("命令 Schema 不存在", code="SCHEMA_MISSING")
        try:
            schema = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise SchemaValidationError(f"命令 Schema JSON 无效: {error}", code="SCHEMA_INVALID") from error
        if not isinstance(schema, dict):
            raise SchemaValidationError("命令 Schema 根节点必须是对象", code="SCHEMA_INVALID")
        return schema

    def validate(self, schema: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(params, dict):
            raise SchemaValidationError("参数必须是对象")
        result = self._apply_defaults(schema, dict(params))
        self._validate_value(schema, result, "参数")
        return result

    def _apply_defaults(self, schema: dict[str, Any], value: Any) -> Any:
        if schema.get("type") == "object" and isinstance(value, dict):
            properties = schema.get("properties", {})
            for key, definition in properties.items():
                if key not in value and isinstance(definition, dict) and "default" in definition:
                    value[key] = definition["default"]
                elif key in value and isinstance(definition, dict):
                    value[key] = self._apply_defaults(definition, value[key])
        elif schema.get("type") == "array" and isinstance(value, list) and isinstance(schema.get("items"), dict):
            return [self._apply_defaults(schema["items"], item) for item in value]
        return value

    def _validate_value(self, schema: dict[str, Any], value: Any, field: str) -> None:
        if value is None:
            if schema.get("type") not in (None, "null"):
                raise SchemaValidationError(f"{field} 不能为空", field=field)
            return
        kind = schema.get("type")
        valid = {
            "object": isinstance(value, dict),
            "array": isinstance(value, list),
            "string": isinstance(value, str),
            "integer": isinstance(value, int) and not isinstance(value, bool),
            "number": isinstance(value, (int, float)) and not isinstance(value, bool),
            "boolean": isinstance(value, bool),
            "null": value is None,
        }
        if kind in valid and not valid[kind]:
            raise SchemaValidationError(f"{field} 必须为 {kind}", field=field)
        if "enum" in schema and value not in schema["enum"]:
            raise SchemaValidationError(f"{field} 取值不受支持", field=field)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if "minimum" in schema and value < schema["minimum"]:
                raise SchemaValidationError(f"{field} 不能小于 {schema['minimum']}", field=field)
            if "maximum" in schema and value > schema["maximum"]:
                raise SchemaValidationError(f"{field} 不能大于 {schema['maximum']}", field=field)
        if isinstance(value, str):
            if "minLength" in schema and len(value) < schema["minLength"]:
                raise SchemaValidationError(f"{field} 长度不能小于 {schema['minLength']}", field=field)
            if "maxLength" in schema and len(value) > schema["maxLength"]:
                raise SchemaValidationError(f"{field} 长度不能大于 {schema['maxLength']}", field=field)
        if isinstance(value, dict):
            properties = schema.get("properties", {})
            unknown = set(value) - set(properties)
            if schema.get("additionalProperties", False) is not True and unknown:
                raise SchemaValidationError(f"未知参数: {', '.join(sorted(unknown))}", field=field)
            for required in schema.get("required", []):
                if required not in value:
                    raise SchemaValidationError(f"缺少必填参数: {required}", field=required)
            for key, item in value.items():
                if key in properties:
                    self._validate_value(properties[key], item, key)
        if isinstance(value, list) and isinstance(schema.get("items"), dict):
            for index, item in enumerate(value):
                self._validate_value(schema["items"], item, f"{field}[{index}]")
