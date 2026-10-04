"""Load one consistent report projection for every HTTP export format."""

from __future__ import annotations

from collections.abc import Callable

from ..blocking import run_blocking
from ..checkpoints import RUN_SETTINGS_KEY
from ..persistence.repository import ResearchRepository, RunDetail
from ..reproducibility import RUN_MANIFEST_CHECKPOINT_KEY
from .assemble import assemble_document, table_citations
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
        if detail.report is not None:
            from ..bibliography import build_bibliography
            from ..workbench.reader import paper_sources

            document.bibliography = build_bibliography(
                detail.report.markdown,
                detail.report.citations,
                [finding for result in detail.results for finding in result.findings],
                [*detail.sources, *paper_sources(detail)],
                extra_citations=table_citations(document.blocks),
            )
        validation = scratch.get("_report_validation") if isinstance(scratch, dict) else None
        if isinstance(validation, dict):
            document.final_validation = FinalReportValidation.model_validate(validation)
        if detail.report is not None and isinstance(scratch, dict):
            from ..workbench.prose_review import reviewer_for_report, stored_review

            checker = reviewer_for_report(
                None,
                detail.query,
                detail.results,
                detail.report.citations,
                scratch,
                0,
                corroboration=requires_corroboration(detail),
                sources=detail.sources,
            )
            record = stored_review(scratch)
            if document.bibliography is not None:
                from ..workbench.citation_binding import bind_review

                bind_review(document.bibliography, checker, detail.report.markdown, record)
            if checker is not None and record is not None:
                bound, issues = checker.check(detail.report.markdown, record)
                document.final_validation = FinalReportValidation(
                    scope="citation_and_numbers"
                    if record.get("model_review_skipped")
                    else "model_assessed_final_prose_support",
                    issues=issues,
                    fallback=bool(record.get("body_replaced")),
                    semantic_verification=not bool(record.get("model_review_skipped")),
                    support_status="fail" if issues or not bound else "pass",
                )
            workbench = scratch.get("workbench", {})
            if workbench.get("template") in {
                "paperRead",
                "peerReview",
                "autoResearch",
                "litReview",
            }:
                from ..workbench.contract import contract_from_scratch
                from ..workbench.templates import get_template
                from ..workbench.titles import paper_report_title

                contract = contract_from_scratch(scratch)
                template = get_template(workbench["template"])
                assert template is not None
                documents = document.bibliography.documents if document.bibliography else []
                document.title = paper_report_title(
                    detail.report.markdown,
                    (contract.title if contract else "")
                    or f"{template.title}：{detail.query[:40]}",
                    detail.query,
                    label=template.title,
                    reference_title=documents[0].title
                    if template.key == "peerReview" and len(documents) == 1
                    else "",
                )
            if workbench.get("template") == "dataAnalysis" and isinstance(
                scratch.get("analysis"), dict
            ):
                from ..workbench.titles import analysis_title

                document.title = analysis_title(scratch["analysis"])
            if workbench.get("template") == "mindmap":
                from ..workbench.mindmap_contract import checked_review
                from ..workbench.titles import mindmap_title

                extras = workbench.get("extras", {})
                if isinstance(extras.get("mindmap"), dict):
                    document.title = mindmap_title(extras["mindmap"])
                if extras.get("node_review") is not None and extras.get("mindmap"):
                    bound, issues = checked_review(
                        extras["mindmap"],
                        detail.report.citations,
                        detail.results,
                        extras["node_review"],
                        detail.report.markdown,
                    )
                    document.final_validation = FinalReportValidation(
                        scope="model_assessed_node_evidence_and_relations",
                        issues=issues,
                        semantic_verification=True,
                        support_status="fail" if issues or not bound else "pass",
                    )
        from ..workbench.extraction import processing_failures

        if failures := processing_failures(detail.results):
            document.final_validation = FinalReportValidation(
                scope="source_processing",
                issues=failures,
                fallback=True,
                semantic_verification=False,
                support_status="fail",
            )
        return document
