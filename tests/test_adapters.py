"""Test deterministic post-processing in adapters."""

from src.adapters import capitalize_bullets


def test_capitalizes_lowercase_bullet() -> None:
    assert capitalize_bullets(r"\item designed and built") == r"\item Designed and built"


def test_preserves_already_capitalized() -> None:
    assert capitalize_bullets(r"\item Built something") == r"\item Built something"


def test_handles_multiple_items() -> None:
    tex = r"""\item designed systems
  \item built pipelines
  \item Engineered solutions"""
    result = capitalize_bullets(tex)
    assert r"\item Designed systems" in result
    assert r"\item Built pipelines" in result
    assert r"\item Engineered solutions" in result


def test_no_items_unchanged() -> None:
    tex = r"\section*{Skills}"
    assert capitalize_bullets(tex) == tex
