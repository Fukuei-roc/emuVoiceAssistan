from pathlib import Path

from app.config import ROOT_DIR, settings
from app.knowledge import MarkdownKnowledgeBase, load_markdown


MARKDOWN_PATH = ROOT_DIR / "knowledge" / "EMU800" / "EMU800_故障處理流程_AI整理版.md"


def test_markdown_can_be_read():
    assert MARKDOWN_PATH.exists()
    assert MARKDOWN_PATH.read_text(encoding="utf-8").strip()


def test_markdown_parses_multiple_sections():
    sections = load_markdown(MARKDOWN_PATH, "EMU800")
    assert len(sections) > 10
    assert any("VCB" in section.heading for section in sections)


def test_search_finds_expected_sections(tmp_path: Path):
    kb = MarkdownKnowledgeBase(tmp_path / "knowledge.db", [MARKDOWN_PATH], settings.active_vehicle)
    kb.reload()

    vcb = kb.search("VCB不閉合")
    assert vcb
    assert any("VCB" in result.heading and "不閉合" in result.heading for result in vcb)

    siv = kb.search("SIV")
    assert siv
    assert any("SIV" in result.heading or "SIV" in result.content for result in siv)

    brake = kb.search("不鬆軔")
    assert brake
    assert any("軔" in result.heading or "軔" in result.content for result in brake)
