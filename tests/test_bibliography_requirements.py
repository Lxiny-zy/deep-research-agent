"""Q1.8 bibliography regressions; source versions keep their evidence identities."""

import hashlib

import pytest

from deep_research.bibliography import build_bibliography, document_identity, present_markdown
from deep_research.models import ScholarlyMetadata, Source
from tests.fakes import verified_finding


def test_unused_sources_do_not_appear_in_delivered_bibliography():
    urls = ["https://paper.test/used", "https://paper.test/unused"]
    findings = [verified_finding(source_url=url) for url in urls]
    for finding, title in zip(findings, ["Used study", "Unused study"], strict=True):
        finding.verification.source_reference = title
    catalog = build_bibliography("Result [1].", urls, findings)
    shown = present_markdown("Result [1].", catalog)
    assert "Used study" in shown and "Unused study" not in shown
    assert len(catalog.locations) == 2  # Preserve the original evidence inventory.


def test_arxiv_versions_share_bibliography_without_merging_source_snapshots():
    urls = ["https://arxiv.org/abs/2401.01234v1", "https://arxiv.org/html/2401.01234v2"]
    catalog = build_bibliography("Early [1]. Updated [2].", urls, [])
    assert len(catalog.documents) == 1
    assert [location.url for location in catalog.locations] == urls
    assert document_identity(urls[0])[0] != document_identity(urls[1])[0]


def test_arxiv_doi_alias_and_exact_scholarly_title_are_deduplicated():
    urls = ["https://arxiv.org/abs/2401.01234v1", "https://doi.org/10.48550/arXiv.2401.01234"]
    assert len(build_bibliography("Both [1,2].", urls, []).documents) == 1
    sources = [
        Source(
            url="https://journal.test/a",
            title="A Study of Spectral Reconstruction",
            content="a",
            scholarly=ScholarlyMetadata(authors=["Alice Smith"]),
        ),
        Source(
            url="https://archive.test/b",
            title="A STUDY OF SPECTRAL RECONSTRUCTION",
            content="b",
            scholarly=ScholarlyMetadata(authors=["Alice Smith"]),
        ),
    ]
    findings = [verified_finding(source_url=s.url) for s in sources]
    for finding, source in zip(findings, sources, strict=True):
        finding.verification.source_content_hash = hashlib.sha256(
            source.content.encode()
        ).hexdigest()
        finding.verification.source_title = source.title
    assert (
        len(
            build_bibliography("Both [1,2].", [s.url for s in sources], findings, sources).documents
        )
        == 1
    )


@pytest.mark.asyncio
async def test_uploaded_pdf_without_metadata_uses_first_page_bibliographic_text():
    import fitz

    from deep_research.workbench.attachments import parse_attachment

    document = fitz.open()
    page = document.new_page()
    page.insert_text((60, 80), "Deep Spectral Reconstruction", fontsize=20)
    page.insert_text((60, 115), "Alice Smith, Bob Jones", fontsize=12)
    page.insert_text((60, 137), "Example University", fontsize=10)
    page.insert_text((60, 157), "arXiv:2401.01234v2 [cs.CV] 12 Jan 2024", fontsize=9)
    page.insert_text((60, 185), "Abstract", fontsize=13)
    page.insert_text((60, 205), "We describe a reconstruction method.", fontsize=11)
    raw = document.tobytes()
    document.close()
    attachment = await parse_attachment(raw, "download.pdf")
    assert attachment.title == "Deep Spectral Reconstruction"
    assert attachment.authors == ["Alice Smith", "Bob Jones"]
    source = attachment.sources()[0]
    assert source.scholarly.work_id == "arxiv:2401.01234"
    assert source.scholarly.year == 2024
    assert source.scholarly.peer_reviewed is None


def test_missing_bibliographic_fields_are_explicit_without_invented_publication():
    source = Source(
        url="https://workspace.invalid/attachments/p?chunk=1",
        title="download.pdf",
        content="No identifiable title or authors.",
    )
    finding = verified_finding(source_url=source.url)
    finding.verification.source_content_hash = hashlib.sha256(source.content.encode()).hexdigest()
    finding.verification.source_title = source.title
    catalog = build_bibliography("Result [1].", [source.url], [finding], [source])
    assert "未识别" in catalog.documents[0].reference
    assert "2024" not in catalog.documents[0].reference


def test_conflicting_authors_or_doi_do_not_bridge_unrelated_works():
    from deep_research.models import SourceIdentity

    urls = ["https://one.test/a", "https://two.test/b", "https://three.test/c"]
    findings = [verified_finding(source_url=url) for url in urls]
    title = "An Exact Title Shared by Different Studies"
    for finding, author in zip(findings, ["Alice Smith", "Bob Jones", "Chris Brown"], strict=True):
        finding.verification.source_identity = SourceIdentity(title=title, authors=[author])
    assert len(build_bibliography("[1,2,3]", urls, findings).documents) == 3
    conflict = findings[0].model_copy(deep=True)
    findings[0].verification.source_identity.doi = "10.1234/a"
    conflict.verification.source_identity.doi = "10.1234/b"
    findings[1].verification.source_identity.doi = "10.1234/a"
    findings[2].verification.source_identity.doi = "10.1234/b"
    assert len(build_bibliography("[1,2,3]", urls, [*findings, conflict]).documents) == 3


def test_later_metadata_and_table_only_citations_reach_the_reference_list():
    sources = [
        Source(
            url="https://arxiv.org/abs/2401.01234v1",
            title="A Study of Spectral Reconstruction",
            content="a",
        ),
        Source(
            url="https://arxiv.org/abs/2401.01234v2",
            title="A Study of Spectral Reconstruction",
            content="b",
            scholarly=ScholarlyMetadata(
                authors=["Alice Smith"],
                year=2024,
                venue="arXiv",
                peer_reviewed=False,
                version="v2",
                retracted=True,
            ),
        ),
        Source(url="https://unused.test/p", content="c"),
    ]
    findings = [verified_finding(source_url=source.url) for source in sources]
    for finding, source in zip(findings, sources, strict=True):
        finding.verification.source_content_hash = hashlib.sha256(
            source.content.encode()
        ).hexdigest()
        finding.verification.source_title = source.title
    catalog = build_bibliography(
        "See the result table.", [s.url for s in sources], findings, sources, extra_citations=[1]
    )
    shown = present_markdown("See the result table.", catalog)
    assert "Alice Smith" in shown and "2024" in shown
    assert "预印本v2" in shown.replace(" ", "") and "已撤稿" in shown
    assert "unused.test" not in shown
    assert len(catalog.locations) == 3


def test_pdf_body_references_and_template_dates_are_not_publication_metadata():
    import pymupdf

    from deep_research.tools.oa_pdf_fulltext import parse_oa_pdf

    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((60, 80), "A Reliable Reconstruction Study", fontsize=20)
    page.insert_text((60, 115), "Alice Smith", fontsize=12)
    page.insert_text((60, 150), "Abstract", fontsize=13)
    page.insert_text((60, 170), "We compare with arXiv:2401.01234v2.", fontsize=11)
    page.insert_text((60, 200), "References\nhttps://doi.org/10.1234/other-paper", fontsize=11)
    document.set_metadata({"creationDate": "D:20260101000000", "subject": "Imaginary Journal"})
    parsed = parse_oa_pdf(document.tobytes())
    document.close()
    assert parsed.scholarly is None
    assert parsed.title == "A Reliable Reconstruction Study"
    assert parsed.authors == ("Alice Smith",)


def test_pdf_author_superscripts_and_explicit_conference_footer():
    import pymupdf

    from deep_research.tools.oa_pdf_fulltext import parse_oa_pdf

    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((60, 80), "A Reliable Reconstruction Study", fontsize=20)
    page.insert_text((60, 115), "Alice Smith", fontsize=12)
    page.insert_text((123, 110), "1,*", fontsize=7)
    page.insert_text((143, 115), ", Bob Jones,", fontsize=12)
    page.insert_text((60, 140), "Example University", fontsize=10)
    page.insert_text((60, 160), "New York", fontsize=10)
    page.insert_text((60, 190), "Abstract", fontsize=13)
    page.insert_text((60, 210), "We describe the method.", fontsize=11)
    page.insert_text(
        (60, 760),
        "36th Conference on Neural Information Processing Systems (NeurIPS 2022).",
        fontsize=9,
    )
    parsed = parse_oa_pdf(document.tobytes())
    document.close()
    assert parsed.authors == ("Alice Smith", "Bob Jones")
    assert parsed.scholarly.year == 2022
    assert "NeurIPS 2022" in parsed.scholarly.venue
