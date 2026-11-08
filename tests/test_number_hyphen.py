from deep_research.report.validation import _NUMBER


def test_hyphen_after_math_is_not_a_minus_sign() -> None:
    assert _NUMBER.findall("RDLUF-MixS$^2$-9stg 58%") == ["2", "9", "58"]


def test_real_negative_numbers_keep_their_sign() -> None:
    assert _NUMBER.findall("温度为 -9 摄氏度，变化 +3") == ["-9", "+3"]
