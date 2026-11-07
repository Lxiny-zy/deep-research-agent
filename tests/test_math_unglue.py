from deep_research.workbench.delivery.math_residual import unglue_digit_math
from deep_research.workbench.gates import markdown_gate


def test_digit_glued_math_becomes_renderable_without_model_revision() -> None:
    text = (
        r"经一个 1$\times$1 卷积核得到；场景 S1$\sim$S10；"
        r"初始学习率为 4$\times$10$^{-4}$；窗口 $M\times M$ 不变。"
    )
    assert markdown_gate(text).status == "fail"
    fixed = unglue_digit_math(text)
    assert "1×1" in fixed and "S1～S10" in fixed and "4×$10^{-4}$" in fixed
    assert r"$M\times M$" in fixed
    assert markdown_gate(fixed).status != "fail"


def test_real_formulas_untouched() -> None:
    text = r"退化模型 $\mathbf{y} = \mathbf{\Phi}\mathbf{x} + \mathbf{n}$ 成立，窗口 $M\times M$。"
    assert unglue_digit_math(text) == text
