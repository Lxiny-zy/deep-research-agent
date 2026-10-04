from deep_research.document_corpus import FullTextCorpus, mark_complete_sources
from deep_research.models import Source


def parts():
    return [
        Source(
            url=f"https://example.org/paper.pdf#chunk-{index}",
            title="Alpha",
            content=text,
            locator=f"page {index + 1}",
        )
        for index, text in enumerate(["Alpha's method.", "T = 4 in implementation."])
    ]


def test_only_a_complete_unchanged_manifest_establishes_full_text():
    raw = parts()
    assert not next(iter(FullTextCorpus(raw, {}).documents.values())).complete
    marked = mark_complete_sources(raw)
    assert next(iter(FullTextCorpus(marked, {}).documents.values())).complete
    assert not next(iter(FullTextCorpus(marked[:1], {}).documents.values())).complete
    changed = [marked[0], marked[1].model_copy(update={"content": "Changed evidence"})]
    assert not next(iter(FullTextCorpus(changed, {}).documents.values())).complete
    duplicate = [marked[0], marked[1].model_copy(update={"document_part_index": 0})]
    assert not next(iter(FullTextCorpus(duplicate, {}).documents.values())).complete


def test_cited_chunk_selects_the_entire_document_and_excludes_other_papers():
    raw = mark_complete_sources(parts())
    other = mark_complete_sources([Source(url="https://example.org/b.pdf", content="Beta")])
    corpus = FullTextCorpus(raw + other, {raw[0].url: 1, other[0].url: 2})
    catalog = corpus.catalog([1])
    assert len(catalog) == 1
    assert {source["url"] for source in catalog[0]["sources"]} == {s.url for s in raw}
    assert catalog[0]["complete"]


def test_changed_unselected_fulltext_invalidates_the_review_fingerprint():
    raw = mark_complete_sources(parts())
    original = FullTextCorpus(raw, {raw[0].url: 1})
    changed = FullTextCorpus(
        [raw[0], raw[1].model_copy(update={"content": "T = 8"})], {raw[0].url: 1}
    )
    assert original.fingerprint != changed.fingerprint
