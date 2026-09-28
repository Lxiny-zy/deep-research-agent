"""Validated text deliverables for planner-authored research steps.

The contract describes model output, never executable tool calls. Binary
deliverables belong to registered operations or the report export service.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import PurePosixPath
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .artifacts import ArtifactValidationError
from .planning import ArtifactSpec

Note = Annotated[str, Field(min_length=1, max_length=2000)]
CONTRACT_VERSION = "research-step-v1"
TEXT_FORMATS = {
    "md": "text/markdown",
    "markdown": "text/markdown",
    "json": "application/json",
    "txt": "text/plain",
    "text": "text/plain",
    "html": "text/html",
    "htm": "text/html",
    "csv": "text/csv",
    "tsv": "text/tab-separated-values",
    "tex": "text/plain",
    "bib": "text/plain",
    "py": "text/plain",
    "r": "text/plain",
    "sql": "text/plain",
    "yaml": "text/plain",
    "yml": "text/plain",
}


class TextArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    path: str = Field(min_length=1)
    content: str = Field(min_length=1)


class StepResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    contract_version: Literal[1]
    status: Literal["done", "partial"]
    summary: str = Field(min_length=1, max_length=4000)
    artifacts: list[TextArtifact] = Field(min_length=1, max_length=100)
    gaps: list[Note] = Field(default_factory=list, max_length=30)
    next_actions: list[Note] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def check_status(self) -> StepResult:
        if self.status == "partial" and not self.gaps:
            raise ValueError("partial results require explicit gaps")
        if self.status == "done" and self.gaps:
            raise ValueError("results with unresolved gaps must be partial")
        return self


def strict_json(text: str) -> Any:
    def invalid_constant(value: str) -> Any:
        raise ValueError(f"non-JSON constant: {value}")

    def unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    return json.loads(text, parse_constant=invalid_constant, object_pairs_hook=unique_keys)


def split_path(path: str, slug: str) -> tuple[str, str, str]:
    path = ArtifactSpec(path=path).path
    parts = path.split("/")
    if len(parts) < 4 or parts[0] not in {"work", "output"}:
        raise ArtifactValidationError("artifact path must use work|output/<slug>/<stage>/<name>")
    if parts[1] != slug:
        raise ArtifactValidationError("artifact path slug does not match the current run")
    return parts[0], parts[2], "/".join(parts[3:])


def text_mime(spec: ArtifactSpec) -> str:
    suffix = PurePosixPath(spec.path).suffix.lstrip(".").lower()
    declared = spec.format.lstrip(".").lower()
    kind = suffix or declared
    if kind not in TEXT_FORMATS:
        raise ArtifactValidationError(
            f"artifact {spec.path!r} is not a supported text format; "
            "use a registered operation or report export for binary files"
        )
    if declared and TEXT_FORMATS.get(declared) != TEXT_FORMATS[kind]:
        raise ArtifactValidationError(f"artifact format conflicts with path: {spec.path}")
    return TEXT_FORMATS[kind]


def output_specs(metadata: Mapping[str, Any], slug: str, step_id: str) -> list[ArtifactSpec]:
    raw = (
        metadata.get("expected_output_specs")
        or metadata.get("expected_outputs")
        or [f"work/{slug}/plan-{step_id}/response.md"]
    )
    if not isinstance(raw, list):
        raise ArtifactValidationError("expected artifact outputs must be a list")
    specs = [ArtifactSpec.model_validate(item) for item in raw]
    if len({spec.path for spec in specs}) != len(specs):
        raise ArtifactValidationError("duplicate artifact output paths")
    for spec in specs:
        _, stage, _ = split_path(spec.path, slug)
        if stage == "executor-journal":
            raise ArtifactValidationError("artifact stage executor-journal is reserved")
        text_mime(spec)
    return specs


def result_prompt(specs: list[ArtifactSpec], *, structured: bool) -> str:
    declarations = json.dumps([s.model_dump(mode="json") for s in specs], ensure_ascii=False)
    if not structured:
        return (
            "Return the content of the single declared text artifact directly. "
            "For JSON return strict JSON without code fences. To report partial work, "
            "you may instead use the research-step-v1 envelope described below.\n"
            + _envelope_prompt()
            + "\nDeclared artifacts: "
            + declarations
        )
    return _envelope_prompt() + "\nDeclared artifacts: " + declarations


def _envelope_prompt() -> str:
    return (
        "Return only a research-step-v1 JSON object: "
        '{"contract_version":1,"status":"done|partial","summary":"...",'
        '"artifacts":[{"path":"exact declared path","content":"file text"}],'
        '"gaps":[],"next_actions":[]}. '
        "Produce distinct content for each file. A done result must include every required "
        "artifact. A partial result must include useful artifacts and explicit gaps. "
        "Record supported findings, source URLs, failed approaches and uncertainties in "
        "the artifacts. Do not fabricate searches, experiments, citations or tool access."
    )


def parse_result(response: str, specs: list[ArtifactSpec], *, structured: bool) -> StepResult:
    try:
        value = strict_json(response)
    except ValueError:
        value = None
    envelope = (
        isinstance(value, dict) and {"contract_version", "status", "artifacts"} <= value.keys()
    )
    try:
        if structured or envelope:
            result = StepResult.model_validate(value)
        else:
            result = StepResult(
                contract_version=1,
                status="done",
                summary="Completed text artifact",
                artifacts=[TextArtifact(path=specs[0].path, content=response)],
            )
        by_path = {spec.path: spec for spec in specs}
        seen: set[str] = set()
        for artifact in result.artifacts:
            if artifact.path not in by_path or artifact.path in seen:
                raise ValueError(f"undeclared or duplicate artifact: {artifact.path}")
            seen.add(artifact.path)
            spec = by_path[artifact.path]
            if not artifact.content.strip():
                raise ValueError(f"empty artifact: {artifact.path}")
            if text_mime(spec) == "application/json":
                strict_json(artifact.content)
            if spec.sha256 and hashlib.sha256(artifact.content.encode()).hexdigest() != spec.sha256:
                raise ValueError(f"artifact SHA-256 mismatch: {artifact.path}")
        missing = [spec.path for spec in specs if spec.required and spec.path not in seen]
        if missing and result.status == "done":
            raise ValueError(f"missing required artifacts: {missing}")
        if missing:
            gap = "Missing required artifacts: " + ", ".join(missing)
            if gap not in result.gaps:
                result.gaps.append(gap)
        return result
    except ValueError as exc:
        raise ArtifactValidationError(f"artifact result contract failed: {exc}") from exc
