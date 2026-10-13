"""Machine-readable JSON exports."""

from __future__ import annotations

import json


def _strip(obj):
    if isinstance(obj, dict):
        return {k: _strip(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_strip(v) for v in obj]
    return obj


def to_json_bytes(data, indent: int = 2) -> bytes:
    return json.dumps(_strip(data), indent=indent, ensure_ascii=False, default=str).encode("utf-8")


def report_json(report: dict) -> bytes:
    payload = dict(report)
    payload["schema"] = "autodoc-ai/report/v1"
    return to_json_bytes(payload)


def comparison_json(result_dict: dict) -> bytes:
    payload = dict(result_dict)
    payload["schema"] = "autodoc-ai/comparison/v1"
    return to_json_bytes(payload)
