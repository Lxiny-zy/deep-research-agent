"""意图识别离线评测：准确率 / 混淆矩阵 / 拒识率 / 误伤率 / 级联成本分布。

完全离线：不调用真实 LLM、不访问网络。因此这里度量的是**规则 + 本地模型两级**
的能力，以及级联的分流比例——L3 的贡献需要真实 LLM 才能测，输出中显式标注。

三组指标各自回答一个问题：

1. **任务意图准确率**：路由决策的基础对不对。附混淆矩阵，因为「exploratory 被判成
   comparative」和「comparative 被判成 factual_lookup」的下游代价完全不同——
   前者只是流程选得不够宽，后者会直接跳过反思补洞。
2. **风险拒识率 / 误伤率**：安全指标必须成对报告。只报拒识率的评测可以用
   「全部拒绝」刷到 100%，所以数据集里有一半是**必须放行**的正常研究请求
   （含「研究提示词注入防御」这类与攻击同词不同意图的困难负例）。
3. **级联分流**：各级承担的样本比例。这是「为什么不直接用 LLM」的量化回答。

运行：``python -m eval.intent_eval``
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import defaultdict

from deep_research.intent.cascade import IntentCascade
from deep_research.intent.dataset import QUERY_INTENT_DATA, RISK_EVAL_DATA
from deep_research.intent.model import load_bundled_model
from deep_research.intent.routing import plan_route


async def evaluate_task_intent(cascade: IntentCascade) -> dict:
    confusion: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    tier_counts: dict[str, int] = defaultdict(int)
    errors: list[tuple[str, str, str, float]] = []
    correct = 0
    routed = 0

    for text, gold in QUERY_INTENT_DATA:
        decision = await cascade.classify_query(text)
        confusion[gold][decision.intent] += 1
        tier_counts[decision.tier] += 1
        if decision.intent == gold:
            correct += 1
        else:
            errors.append((text, gold, decision.intent, decision.confidence))
        if plan_route(decision).applied:
            routed += 1

    total = len(QUERY_INTENT_DATA)
    return {
        "total": total,
        "correct": correct,
        "accuracy": correct / total if total else 0.0,
        "routed": routed,
        "route_coverage": routed / total if total else 0.0,
        "confusion": {key: dict(value) for key, value in confusion.items()},
        "tiers": dict(tier_counts),
        "errors": errors,
    }


async def evaluate_risk(cascade: IntentCascade) -> dict:
    """拒识率与误伤率成对度量。

    - 拒识率 = 应拒的样本中实际被拒的比例（漏放攻击 = 安全事故）
    - 误伤率 = 正常研究请求中被误拒的比例（误伤 = 产品不可用）
    单看任何一个都能被平凡策略刷满，必须一起看。

    第三个口径是**弱信号标记**：有些攻击（典型是掺了合法任务表达的洗白型）
    在规则层拿不到确定结论，只会触发风险弱信号并要求 L3 复核。本评测刻意
    离线运行，L3 不可用，因此这些样本既不算「拦住」也不该算「漏掉」——
    把它们混进漏拦截会低估离线两级的能力，混进拦截则会高估。单列一档。
    """
    should_block = [(text, gold) for text, gold in RISK_EVAL_DATA if gold != "none"]
    should_pass = [(text, gold) for text, gold in RISK_EVAL_DATA if gold == "none"]

    blocked = 0
    wrong_label: list[tuple[str, str, str]] = []
    missed: list[str] = []
    deferred: list[str] = []  # 触发了弱信号、等待 L3 复核的样本
    for text, gold in should_block:
        decision = await cascade.classify_query(text)
        if decision.blocked or decision.risk != "none":
            blocked += 1
            if decision.risk != gold:
                wrong_label.append((text, gold, decision.risk))
        elif _raised_risk_hint(decision):
            deferred.append(text)
        else:
            missed.append(text)

    false_positives: list[tuple[str, str]] = []
    for text, _ in should_pass:
        decision = await cascade.classify_query(text)
        if decision.blocked or decision.risk != "none":
            false_positives.append((text, decision.risk))

    return {
        "should_block": len(should_block),
        "blocked": blocked,
        "block_rate": blocked / len(should_block) if should_block else 0.0,
        "missed": missed,
        "deferred": deferred,
        "wrong_label": wrong_label,
        "should_pass": len(should_pass),
        "false_positives": false_positives,
        "false_positive_rate": (
            len(false_positives) / len(should_pass) if should_pass else 0.0
        ),
    }


def _raised_risk_hint(decision) -> bool:
    """该判定是否触发了风险弱信号（即：已被标记为需要 L3 复核）。"""
    return any(signal.code.startswith("hint_") for signal in decision.signals)


def format_report(task: dict, risk: dict, *, model_loaded: bool) -> str:
    lines: list[str] = []
    lines.append("# 意图识别离线评测")
    lines.append("")
    lines.append(
        "离线评测：L1 规则 + L2 本地模型两级（未调用真实 LLM，L3 兜底能力不在本表内）。"
    )
    if not model_loaded:
        lines.append("")
        lines.append("> **警告**：未加载到 L2 模型文件，本次仅评测 L1 规则层。")
    lines.append("")

    lines.append("## 任务意图")
    lines.append("")
    lines.append(f"- 样本数：{task['total']}")
    lines.append(f"- 准确率：{task['accuracy']:.1%}（{task['correct']}/{task['total']}）")
    lines.append(
        f"- 路由覆盖率：{task['route_coverage']:.1%}"
        f"（{task['routed']} 条给出了明确的工作流建议）"
    )
    lines.append("")

    labels = sorted({key for key in task["confusion"]} | {
        pred for row in task["confusion"].values() for pred in row
    })
    lines.append("### 混淆矩阵（行=真实，列=预测）")
    lines.append("")
    lines.append("| 真实 \\ 预测 | " + " | ".join(labels) + " |")
    lines.append("| --- |" + " --- |" * len(labels))
    for gold in labels:
        row = task["confusion"].get(gold, {})
        cells = " | ".join(str(row.get(pred, 0)) for pred in labels)
        lines.append(f"| {gold} | {cells} |")
    lines.append("")

    lines.append("### 级联分流")
    lines.append("")
    total = task["total"] or 1
    tier_names = {
        "rule": "L1 规则（0 token）",
        "model": "L2 本地模型（0 token）",
        "llm": "L3 LLM 兜底（有 token 成本）",
        "fallback": "全级弃权 → 默认流程",
    }
    for tier in ("rule", "model", "llm", "fallback"):
        count = task["tiers"].get(tier, 0)
        lines.append(f"- {tier_names[tier]}：{count} 条（{count / total:.1%}）")
    zero_cost = task["tiers"].get("rule", 0) + task["tiers"].get("model", 0)
    lines.append("")
    lines.append(
        f"**{zero_cost / total:.1%} 的请求在零 token 成本下完成意图判定**，"
        "无需调用 LLM。"
    )
    lines.append("")

    if task["errors"]:
        lines.append("### badcase")
        lines.append("")
        for text, gold, predicted, confidence in task["errors"]:
            lines.append(f"- `{gold}` → `{predicted}`（p={confidence:.2f}）：{text}")
        lines.append("")

    lines.append("## 风险意图（拒识率 / 误伤率成对）")
    lines.append("")
    lines.append(
        f"- 拒识率：{risk['block_rate']:.1%}"
        f"（{risk['blocked']}/{risk['should_block']} 条攻击性请求被拦截）"
    )
    lines.append(
        f"- 误伤率：{risk['false_positive_rate']:.1%}"
        f"（{len(risk['false_positives'])}/{risk['should_pass']} 条正常研究请求被误拒）"
    )
    if risk["deferred"]:
        lines.append(
            f"- 待 L3 复核：{len(risk['deferred'])} 条（触发风险弱信号但规则层未定夺；"
            "本评测离线运行，L3 不可用，故既不计入拦截也不计入漏拦截）"
        )
    lines.append("")
    if risk["deferred"]:
        lines.append("### 已标记待复核（线上有 L3 时由其定夺）")
        lines.append("")
        for text in risk["deferred"]:
            lines.append(f"- {text}")
        lines.append("")
    if risk["missed"]:
        lines.append("### 漏拦截")
        lines.append("")
        for text in risk["missed"]:
            lines.append(f"- {text}")
        lines.append("")
    if risk["false_positives"]:
        lines.append("### 误伤")
        lines.append("")
        for text, predicted in risk["false_positives"]:
            lines.append(f"- 判为 `{predicted}`：{text}")
        lines.append("")
    if risk["wrong_label"]:
        lines.append("### 拦住了但风险类型判错（不影响拦截决策）")
        lines.append("")
        for text, gold, predicted in risk["wrong_label"]:
            lines.append(f"- `{gold}` → `{predicted}`：{text}")
        lines.append("")

    return "\n".join(lines)


async def main_async(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="意图识别离线评测")
    parser.add_argument("--output", nargs="?", const="eval/results/intent-eval.md", default=None)
    args = parser.parse_args(argv)

    classifier = load_bundled_model()
    # enable_llm=False：本评测刻意不接真实 LLM，保证结果可复现且零成本。
    cascade = IntentCascade(classifier=classifier, llm=None, enable_llm=False)

    task = await evaluate_task_intent(cascade)
    risk = await evaluate_risk(cascade)
    report = format_report(task, risk, model_loaded=classifier is not None)
    print(report)

    if args.output:
        from pathlib import Path

        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(report, encoding="utf-8")
        print(f"\n已写入 {path}")

    # 退出码用于 CI 守门：漏拦截或误伤都视为失败。
    if risk["missed"] or risk["false_positives"]:
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(main_async(argv))


if __name__ == "__main__":
    sys.exit(main())
