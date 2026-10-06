"""Fault injection runs in a real renderer process, never in application code."""

import os
import runpy
import subprocess
import sys
import time
from pathlib import Path

from deep_research import render_process, report


def main():
    mode, directory = sys.argv[1:]
    marker = Path(directory)
    original = report.render_markdown

    def mark(name):
        with (marker / (name + ".txt")).open("a", encoding="utf-8") as output:
            output.write(str(os.getpid()) + "\n")

    def render(document, **kwargs):
        mark("entered")
        if mode == "diagnostic":
            def native_failure():
                private_value = "PRIVATE-LOCAL-NOT-FOR-LOGS"
                try:
                    raise ValueError("PRIVATE-CAUSE-NOT-FOR-LOGS")
                except ValueError as cause:
                    raise RuntimeError("PRIVATE-MESSAGE-NOT-FOR-LOGS " + private_value) from cause

            native_failure()
        if mode == "exit":
            os._exit(33)
        if mode == "noise":
            os.write(1, b"native diagnostic output\n")
            time.sleep(3600)
        if mode == "flood":
            os.write(1, b"x" * 1048576)
            time.sleep(3600)
        if mode == "nonce":
            os.write(1, b'{"nonce":"wrong", "result":{}}\n')
            time.sleep(3600)
        if mode == "tree":
            options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
            child = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(3600)"], **options,
            )
            (marker / "descendant.txt").write_text(str(child.pid), encoding="utf-8")
        if mode in {"hang", "tree"}:
            time.sleep(3600)
        elif mode == "cpu":
            while True:
                pass
        elif mode == "gate":
            while not (marker / "release").exists():
                time.sleep(0.01)
        return original(document, **kwargs)

    report.render_markdown = render
    if mode == "bundle":
        from deep_research.workbench import publish
        from deep_research.workbench.delivery import docx, html, pdf

        publish.build_bundle = runpy.run_path("tests/test_render_progress.py")["build"]
        for name, module, method in [
            ("html", html, "render_html"), ("docx", docx, "render_docx"),
            ("pdf", pdf, "render_pdf"),
        ]:
            original_format = getattr(module, method)

            def format_render(*args, _name=name, _original=original_format, **kwargs):
                mark(_name)
                if _name == "pdf" and not (marker / "release").exists():
                    time.sleep(3600)
                return _original(*args, **kwargs)

            setattr(module, method, format_render)
    render_process.main()


if __name__ == "__main__":
    main()
