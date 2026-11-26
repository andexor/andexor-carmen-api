# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Andexor Network, Inc.
# Author: Ed Jenkins

"""Check recorded calls against the OpenAPI specification.

preview-test.sh records every call it makes and the answer it got. This compares each successful
one with the specification the same Worker serves: the status must be documented, the answer must
have exactly the documented fields, and the body that was sent must fit the documented body. So a
model that disagrees with the API fails the smoke test. See specs/032-openapi-models/spec.md.

    python3 tools/contract_check.py <specification.json> <calls.jsonl>
"""

import json
import pathlib
import re
import sys
from typing import Any

from jsonschema import Draft202012Validator


def problems_of(spec: dict[str, Any], calls: list[dict[str, Any]]) -> tuple[list[str], int, set[str]]:
    """The problems found, how many calls were checked, and the operations that were exercised."""
    patterns = [(re.compile("^" + re.sub(r"\{[^}]+\}", "[^/]+", path) + "$"), path) for path in spec["paths"]]
    components = {"components": spec.get("components", {})}
    found: dict[str, None] = {}
    seen: set[str] = set()
    checked = 0

    def fits(value: Any, schema: dict[str, Any]) -> str | None:
        validator: Any = Draft202012Validator({**schema, **components})
        errors: list[Any] = sorted(validator.iter_errors(value), key=lambda e: [str(part) for part in e.absolute_path])
        if not errors:
            return None
        where = "/".join(str(part) for part in errors[0].absolute_path if not isinstance(part, int)) or "the top"
        return f"at {where}: {errors[0].message[:200]}"

    for call in calls:
        if not 200 <= int(call["status"]) < 300:
            continue
        path = urlpath(str(call["url"]))
        template = next((template for pattern, template in patterns if pattern.match(path)), None)
        operation: dict[str, Any] | None = spec["paths"].get(template, {}).get(str(call["method"]).lower()) if template else None
        if operation is None:
            continue  # the specification itself and the Swagger UI are not part of the contract
        name = f"{call['method']} {template}"
        seen.add(name)
        checked += 1
        answer: dict[str, Any] | None = operation["responses"].get(str(call["status"]))
        if answer is None:
            found[f"{name}: answered {call['status']}, which the specification does not list"] = None
            continue
        schema = answer.get("content", {}).get("application/json", {}).get("schema")
        if schema is not None:
            try:
                wrong = fits(json.loads(call["response"]), schema)
            except ValueError:
                wrong = "the answer is not JSON"
            if wrong:
                found[f"{name}: the {call['status']} answer does not fit, {wrong}"] = None
        sent = operation.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema")
        if sent is not None and call.get("request"):
            try:
                wrong = fits(json.loads(call["request"]), sent)
            except ValueError:
                wrong = None  # a body that is a file, not JSON
            if wrong:
                found[f"{name}: a body the API accepted does not fit, {wrong}"] = None
    return list(found), checked, seen


def urlpath(url: str) -> str:
    return "/" + url.split("://", 1)[-1].split("/", 1)[-1].split("?", 1)[0]


if __name__ == "__main__":
    try:
        specification = json.loads(pathlib.Path(sys.argv[1]).read_text())
        recorded = [json.loads(line) for line in pathlib.Path(sys.argv[2]).read_text().splitlines() if line.strip()]
    except (OSError, ValueError, IndexError) as e:
        raise SystemExit(f"FAILED: the specification or the recorded calls could not be read: {e}") from e
    wrong_ones, count, exercised = problems_of(specification, recorded)
    for line in wrong_ones:
        print(f"FAILED: {line}")
    if wrong_ones:
        raise SystemExit(1)
    print(f"ok: {count} answers of {len(exercised)} operations fit the specification")
