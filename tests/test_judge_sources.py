import json

from deep_research.models import Source
from eval.judge import EvalScore, Judge


class CapturingModel:
    async def parse(self, system, user, schema, **kwargs):
        self.user = user
        return EvalScore(coverage=3, groundedness=5, depth=3, coherence=3)


def judge():
    instance = Judge.__new__(Judge)
    instance.llm = CapturingModel()
    instance.max_source_chars = 2000
    return instance


async def test_judge_receives_frozen_snapshot_content_and_hash():
    instance = judge()
    result = await instance.score(
        "q",
        "claim [1]",
        sources=[
            Source(
                title="Frozen source", url="https://example.test/a", content="Measured result: 42"
            )
        ],
    )
    snapshot = json.loads(instance.llm.user.split("来源快照（JSON 数据）：\n")[1])[0]
    assert snapshot["content"] == "Measured result: 42" and len(snapshot["sha256"]) == 64
    assert result.source_count == 1 and result.source_scope == "snapshots"
    assert result.groundedness == 5


async def test_missing_sources_never_produce_a_groundedness_score():
    result = await judge().score("q", "claim [1]")
    assert result.groundedness is None and result.source_scope == "unavailable"
    assert result.average == 3  # The unavailable dimension cannot inflate the average.


async def test_truncated_sources_are_recorded_in_the_result():
    result = await judge().score(
        "q",
        "claim",
        sources=[Source(title="Large", url="https://example.test/a", content="x" * 10_000)],
    )
    assert result.sources_truncated and result.source_count == 1
