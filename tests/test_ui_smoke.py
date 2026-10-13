"""Headless UI smoke test: every page renders without exceptions (Streamlit AppTest)."""

from pathlib import Path

import pytest

AppTest = pytest.importorskip("streamlit.testing.v1").AppTest
APP = str(Path(__file__).resolve().parent.parent / "app.py")
PAGES = ["views/dashboard.py", "views/documents.py", "views/document_chat.py", "views/architecture_explorer.py",
         "views/revision_comparison.py", "views/findings.py", "views/reports.py", "views/settings_page.py"]


def _assert_clean(at, label):
    assert not at.exception, f"{label}: {[e.value for e in at.exception]}"


def test_pages_render_on_empty_installation(settings):
    at = AppTest.from_file(APP, default_timeout=120)
    at.run()
    _assert_clean(at, "app")
    for page in PAGES:
        at.switch_page(page)
        at.run()
        _assert_clean(at, page)


def test_pages_render_with_processed_documents(settings, processed):
    at = AppTest.from_file(APP, default_timeout=180)
    at.run()
    at.session_state["active_revision_id"] = processed["B"]["id"]
    for page in PAGES:
        at.switch_page(page)
        at.run()
        _assert_clean(at, page)

    at.switch_page("views/document_chat.py")
    at.run()
    at.session_state["pending_question"] = "What is the unit of Sig_VehicleSpeed?"
    at.run()
    _assert_clean(at, "chat question")

    at.switch_page("views/revision_comparison.py")
    at.run()
    next(b for b in at.button if b.label == "Compare").click().run()
    _assert_clean(at, "comparison")
    assert any("PedalInputHandler" in m.value for m in at.markdown)

    at.switch_page("views/reports.py")
    at.run()
    next(b for b in at.button if b.label == "Prepare report files").click().run()
    _assert_clean(at, "reports")
