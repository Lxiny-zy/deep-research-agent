"""Generate synthetic N7 visual fixtures and bounded local resource observations."""

from __future__ import annotations

import argparse
import io
import json
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image  # noqa: E402

from deep_research.workbench.delivery.mindmap import render_mindmap_html  # noqa: E402
from deep_research.workbench.mindmap_delivery import (  # noqa: E402
    branch_png_pages,
    delivery_index,
    overview_pages,
)


def fixture() -> dict:
    branches = []
    for index, title in enumerate(("方法结构", "实验条件", "比较关系", "后续问题")):
        branches.append(
            {
                "label": title,
                "details": "受控显示样例，只用于工程排版检查，不代表论文结论。",
                "children": [
                    {
                        "label": f"观察 {index + 1}.{child + 1}",
                        "details": (
                            f"这是分支 {index + 1} 的合成说明。保留输入条件、适用范围和不确定性，"
                            "不把显示样例作为研究发现。"
                        )
                        * 4
                        + (r" 公式样例 $x=\frac{1}{2}y$ 保留系数。" if child == 0 else ""),
                        "kind": "question",
                        "citations": [],
                    }
                    for child in range(6)
                ],
            }
        )
    return {
        "schema_version": 2,
        "root": "思维导图工程验收 · 合成样例",
        "branches": branches,
        "links": [{"source": "0.0", "target": "2.0", "relation": "对比", "citations": []}],
        "sources": [
            {
                "index": 1,
                "url": "https://example.invalid/fixture",
                "title": "受控合成来源",
                "quotes": ["仅用于浏览器引用定位测试，不作科研证据。"],
            }
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="docs/validation/n7-readability")
    args = parser.parse_args()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    raw = fixture()
    raw["branches"][0]["children"][0]["citations"] = [1]
    started = time.monotonic()
    peak = [0]
    stopped = threading.Event()

    def observe() -> None:
        try:
            import psutil

            process = psutil.Process()
            while not stopped.wait(0.02):
                peak[0] = max(peak[0], process.memory_info().rss)
        except ImportError:
            return

    observer = threading.Thread(target=observe, daemon=True)
    observer.start()
    assets = []
    try:
        (output / "mindmap.html").write_text(
            render_mindmap_html(raw, title="N7 工程检查（合成样例，未作科研核验）"),
            encoding="utf-8",
        )
        index = delivery_index(raw)
        (output / "mindmap-index.json").write_text(
            json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        groups = [
            overview_pages(raw),
            *(branch_png_pages(raw, i) for i in range(len(raw["branches"]))),
        ]
        for group in groups:
            for suffix, data in group:
                name = "sample" + suffix
                (output / name).write_bytes(data)
                with Image.open(io.BytesIO(data)) as image:
                    assets.append(
                        {
                            "name": name,
                            "width": image.width,
                            "height": image.height,
                            "bytes": len(data),
                            "metadata": json.loads(image.info["deep-research-mindmap"]),
                        }
                    )
    finally:
        stopped.set()
        observer.join(1)
    result = {
        "fixture": "synthetic_only",
        "paid_model_calls": 0,
        "node_count": index["node_count"],
        "png_files": len(assets),
        "assets": assets,
        "elapsed_seconds": round(time.monotonic() - started, 2),
        "peak_process_rss_bytes": peak[0] or None,
        "resource_scope": "one local Python process; not a production 1 GiB container test",
    }
    (output / "render-results.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {key: value for key, value in result.items() if key != "assets"}, ensure_ascii=False
        )
    )


if __name__ == "__main__":
    main()
