"""Small, deterministic JSON Schema validator used by Core and GUI metadata."""
from __future__ import annotations

import copy
import json
import math
import re
from pathlib import Path
from typing import Any


class SchemaValidationError(ValueError):
    def __init__(self, message: str, *, code: str = "INVALID_PARAMETER", field: str | None = None):
        super().__init__(message)
        self.code = code
        self.field = field


class SchemaValidator:
    # format is UI metadata in TestBox (file/directory), not a format assertion.
    KEYWORDS = frozenset({"$schema", "$id", "title", "description", "default", "examples",
                          "readOnly", "writeOnly", "deprecated", "format", "type", "enum",
                          "properties", "required", "additionalProperties", "items",
                          "minimum", "maximum", "minLength", "maxLength", "pattern",
                          "minItems", "maxItems"})

    def _check_schema(self, schema: dict[str, Any], field: str = "参数") -> None:
        if not isinstance(schema, dict):
            raise SchemaValidationError(f"{field} Schema 必须是对象", code="SCHEMA_INVALID", field=field)
        unknown = {key for key in schema if key not in self.KEYWORDS and not key.startswith(("x-", "ui:"))}
        if unknown:
            raise SchemaValidationError(f"不支持的 Schema 关键字: {', '.join(sorted(unknown))}",
                                        code="SCHEMA_UNSUPPORTED", field=field)
        for key in ("minItems", "maxItems", "minLength", "maxLength"):
            if key in schema and (type(schema[key]) is not int or schema[key] < 0):
                raise SchemaValidationError(f"{key} 必须是非负整数", code="SCHEMA_INVALID", field=field)
        if "pattern" in schema:
            try:
                if not isinstance(schema["pattern"], str):
                    raise TypeError("pattern 必须是字符串")
                re.compile(schema["pattern"])
            except (TypeError, re.error) as error:
                raise SchemaValidationError("Schema pattern 无效", code="SCHEMA_INVALID", field=field) from error
        properties = schema.get("properties", {})
        if not isinstance(properties, dict):
            raise SchemaValidationError("Schema properties 必须是对象", code="SCHEMA_INVALID", field=field)
        for key, definition in properties.items():
            self._check_schema(definition, f"{field}.{key}")
        if "items" in schema:
            self._check_schema(schema["items"], f"{field}[]")
        additional = schema.get("additionalProperties", False)
        if isinstance(additional, dict):
            self._check_schema(additional, field)
        elif not isinstance(additional, bool):
            raise SchemaValidationError("Schema additionalProperties 无效", code="SCHEMA_INVALID", field=field)

    def load(self, path: Path) -> dict[str, Any]:
        if not path.is_file():
            raise SchemaValidationError("命令 Schema 不存在", code="SCHEMA_MISSING")
        try:
            schema = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise SchemaValidationError(f"命令 Schema JSON 无效: {error}", code="SCHEMA_INVALID") from error
        if not isinstance(schema, dict):
            raise SchemaValidationError("命令 Schema 根节点必须是对象", code="SCHEMA_INVALID")
        self._check_schema(schema)
        return schema

    def validate(self, schema: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(params, dict):
            raise SchemaValidationError("参数必须是对象")
        self._check_schema(schema)
        # Validation must not mutate caller-owned nested values, and default
        # objects/arrays must not share references with the schema definition.
        result = self._apply_defaults(schema, copy.deepcopy(params))
        self._validate_value(schema, result, "参数")
        # Keep established field feedback for declared schema properties;
        # inspect the remaining open values only after normal validation.
        self._validate_finite_numbers(result, "")
        return result

    def _validate_finite_numbers(self, value: Any, field: str) -> None:
        """Reject non-finite floats even inside intentionally open schema values.

        ``additionalProperties: true`` and an empty schema are useful for plugin
        extension payloads, but they must not provide a way to smuggle NaN or
        Infinity into task parameters, persisted JSON, or a Host request.
        """
        if isinstance(value, float) and not math.isfinite(value):
            raise SchemaValidationError(f"{field} 必须为有限数值", field=field)
        if isinstance(value, dict):
            for key, item in value.items():
                self._validate_finite_numbers(item, f"{field}.{key}" if field else str(key))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                self._validate_finite_numbers(item, f"{field}[{index}]")

    def _apply_defaults(self, schema: dict[str, Any], value: Any) -> Any:
        kind = schema.get("type")
        kinds = kind if isinstance(kind, list) else [kind]
        if "object" in kinds and isinstance(value, dict):
            properties = schema.get("properties", {})
            for key, definition in properties.items():
                if key not in value and isinstance(definition, dict) and "default" in definition:
                    value[key] = self._apply_defaults(definition, copy.deepcopy(definition["default"]))
                elif key in value and isinstance(definition, dict):
                    value[key] = self._apply_defaults(definition, value[key])
        elif "array" in kinds and isinstance(value, list) and isinstance(schema.get("items"), dict):
            return [self._apply_defaults(schema["items"], item) for item in value]
        return value

    def _validate_value(self, schema: dict[str, Any], value: Any, field: str) -> None:
        kind = schema.get("type")
        kinds = kind if isinstance(kind, list) else [kind]
        if value is None:
            if kind is not None and "null" not in kinds:
                raise SchemaValidationError(f"{field} 不能为空", field=field)
        else:
            valid = {
                "object": isinstance(value, dict),
                "array": isinstance(value, list),
                "string": isinstance(value, str),
                "integer": isinstance(value, int) and not isinstance(value, bool),
                "number": isinstance(value, (int, float)) and not isinstance(value, bool),
                "boolean": isinstance(value, bool),
                "null": value is None,
            }
            if kind is not None and not any(item in valid and valid[item] for item in kinds):
                expected = " or ".join(str(item) for item in kinds)
                raise SchemaValidationError(f"{field} 必须为 {expected}", field=field)
        if "enum" in schema and value not in schema["enum"]:
            raise SchemaValidationError(f"{field} 取值不受支持", field=field)
        if isinstance(value, float) and not math.isfinite(value):
            raise SchemaValidationError(f"{field} 必须为有限数值", field=field)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if "minimum" in schema and value < schema["minimum"]:
                raise SchemaValidationError(f"{field} 不能小于 {schema['minimum']}", field=field)
            if "maximum" in schema and value > schema["maximum"]:
                raise SchemaValidationError(f"{field} 不能大于 {schema['maximum']}", field=field)
        if isinstance(value, str):
            if "pattern" in schema and re.search(schema["pattern"], value) is None:
                raise SchemaValidationError(f"{field} 不符合 pattern", field=field)
            if "minLength" in schema and len(value) < schema["minLength"]:
                raise SchemaValidationError(f"{field} 长度不能小于 {schema['minLength']}", field=field)
            if "maxLength" in schema and len(value) > schema["maxLength"]:
                raise SchemaValidationError(f"{field} 长度不能大于 {schema['maxLength']}", field=field)
        if isinstance(value, dict):
            properties = schema.get("properties", {})
            unknown = set(value) - set(properties)
            additional = schema.get("additionalProperties", False)
            if additional is False and unknown:
                raise SchemaValidationError(f"未知参数: {', '.join(sorted(unknown))}", field=field)
            for required in schema.get("required", []):
                if required not in value:
                    raise SchemaValidationError(f"缺少必填参数: {required}", field=required)
            for key, item in value.items():
                if key in properties:
                    self._validate_value(properties[key], item, key)
                elif isinstance(additional, dict):
                    self._validate_value(additional, item, key)
        if isinstance(value, list):
            if "minItems" in schema and len(value) < schema["minItems"]:
                raise SchemaValidationError(f"{field} 项数不能小于 {schema['minItems']}", field=field)
            if "maxItems" in schema and len(value) > schema["maxItems"]:
                raise SchemaValidationError(f"{field} 项数不能大于 {schema['maxItems']}", field=field)
        if isinstance(value, list) and isinstance(schema.get("items"), dict):
            for index, item in enumerate(value):
                self._validate_value(schema["items"], item, f"{field}[{index}]")
