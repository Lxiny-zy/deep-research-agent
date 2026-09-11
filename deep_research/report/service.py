"""Load one consistent report projection for every HTTP export format."""

from __future__ import annotations

from collections.abc import Callable

from ..blocking import run_blocking
from ..checkpoints import RUN_SETTINGS_KEY
from ..persistence.repository import ResearchRepository, RunDetail
from ..reproducibility import RUN_MANIFEST_CHECKPOINT_KEY
from .assemble import assemble_document
from .document import FinalReportValidation, ReportDocument


class ReportNotFoundError(LookupError):
    pass


def requires_corroboration(detail: RunDetail) -> bool:
    if detail.manifest is not None and "require_corroboration" in detail.manifest.settings:
        return detail.manifest.settings.get("require_corroboration") is True
    execution = detail.orchestration
    checkpoint = execution.checkpoint if execution is not None else None
    scratch = checkpoint.get("scratch") if isinstance(checkpoint, dict) else None
    if not isinstance(scratch, dict):
        return False
    raw_manifest = scratch.get(RUN_MANIFEST_CHECKPOINT_KEY)
    if isinstance(raw_manifest, dict):
        settings = raw_manifest.get("settings")
        if isinstance(settings, dict) and settings.get("require_corroboration") is True:
            return True
    raw_settings = scratch.get(RUN_SETTINGS_KEY)
    return isinstance(raw_settings, dict) and raw_settings.get("require_corroboration") is True


class ReportService:
    def __init__(
        self,
        repo: ResearchRepository,
        *,
        assembler: Callable[..., ReportDocument] = assemble_document,
        event_limit: int = 20_000,
    ) -> None:
        self.repo, self.assembler, self.event_limit = repo, assembler, event_limit

    async def document(self, run_id: str, *, include_hsi_tables: bool = False) -> ReportDocument:
        detail = await self.repo.get_run(run_id)
        if detail is None:
            raise ReportNotFoundError(run_id)
        events = await self.repo.get_events(run_id, limit=self.event_limit)
        document = await run_blocking(
            self.assembler,
            detail.report,
            detail.results,
            events=events,
            query=detail.query,
            require_corroboration=requires_corroboration(detail),
            include_hsi_tables=include_hsi_tables,
        )
        scratch = detail.orchestration.checkpoint.get("scratch", {}) if detail.orchestration else {}
        validation = scratch.get("_report_validation") if isinstance(scratch, dict) else None
        if isinstance(validation, dict):
            document.final_validation = FinalReportValidation.model_validate(validation)
        return document
