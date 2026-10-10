"""Use exactly the same adapters as comparison/checks; preview is a real task."""
import json

from testbox.sdk import Result, normalize_dataset, read_dataset, run_dataset_batch


def display_value(value):
    text = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value
    if isinstance(text, str) and len(text) > 120:
        return text[:120] + "…〈显示截断；完整值见预览JSON〉"
    return value


class Plugin:
    def init(self, context):
        self.context = context

    def execute(self, command, params):
        batch_result = run_dataset_batch(self, command, params)
        if batch_result is not None:
            return batch_result
        return self.execute_one(command, {k: v for k, v in params.items()
                                         if k not in {"inputs", "left_inputs", "right_inputs", "batch"}})

    def execute_one(self, command, params):
        if not params.get("input"):
            from testbox.sdk import PluginError
            raise PluginError("INVALID_PARAMS", "请选择单文件或批量文件")
        dataset = read_dataset(params["input"], params.get("options"))
        normalized = normalize_dataset(dataset, params.get("normalize"))
        sample = params.get("sample_rows", 20)
        data = {key: value for key, value in normalized.items() if key not in {"rows", "locations"}}
        data.update({"rows": normalized["rows"][:sample], "raw_rows": dataset["rows"][:sample],
                     "locations": dataset["locations"][:sample], "row_count": len(dataset["rows"]),
                     "sample_truncated": len(dataset["rows"]) > sample,
                     "options": params.get("options", {}), "preview_only": True})
        name = f"{self.context.task.id}-preview.json"
        self.context.files.write_text(name, json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False))
        # Display values are bounded separately from the full sampled artifact.
        display = dict(data)
        display["rows"] = [{key: display_value(value) for key, value in row.items()} for row in data["rows"]]
        display["raw_rows"] = [{key: display_value(value) for key, value in row.items()} for row in data["raw_rows"]]
        display["display_bounded"] = True
        display["display_cell_limit"] = 120
        return Result("success", f"已完整解析 {len(dataset['rows'])} 行，预览前 {sample} 行",
                      display, [name], dataset["warnings"])

    def destroy(self):
        pass
