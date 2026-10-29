"""Synthetic fixtures stay in tests; the UI never generates demonstration history."""
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from dashboard.logic import (filter_history, latest_per_case, scored_cohort,
                             presentation_table, csv_bytes, quote_diagnostics)


@pytest.fixture
def history():
    rows = []
    for ident, case, status in [(1, "001", "legacy_unlinked"), (2, "001", "scored"),
                                (3, "002", "needs_review")]:
        row = dict(source_result_id=ident, case_id=case, source_recall_id=ident,
                   analysis_created_at=pd.Timestamp(f"2026-10-{ident:02}T12:00:00Z"),
                   company_name="Test company", product_name="Test product " + case,
                   brand="Test", analysis_status=status, evidence_verified=status == "scored",
                   toxicity_score=20.0 if status != "needs_review" else None,
                   completeness_score=.8 if status != "needs_review" else None,
                   verdict="toxique" if status != "needs_review" else None,
                   model_name="test-old" if ident == 1 else "test-new",
                   prompt_version="test-v1", scoring_version="legacy_unknown" if ident == 1 else "test-rules",
                   context_origin="legacy_current_context_unverified" if ident == 1 else "run_snapshot",
                   explanation=None, evidence={f"q{i}": ["Company quote"] if i <= 5 else [] for i in range(1, 8)},
                   **{f"q{i}": i <= 5 for i in range(1, 8)})
        rows.append(row)
    return pd.DataFrame(rows)


def test_latest_keeps_cases_and_deterministic_id_tiebreak(history):
    history.loc[1, "analysis_created_at"] = history.loc[0, "analysis_created_at"]
    assert latest_per_case(history).source_result_id.tolist() == [2, 3]
    assert latest_per_case(history.iloc[0:0]).empty


def test_reviews_and_legacy_are_excluded_and_null_scores_preserved(history):
    assert scored_cohort(history).source_result_id.tolist() == [2]
    table = presentation_table(history)
    assert pd.isna(table.loc[table.Analyse.eq(3), "Indice / 100"]).all()
    assert table.loc[table.Analyse.eq(2), "Complétude %"].iloc[0] == 80
    assert history.loc[2, "toxicity_score"] != 0


def test_filters_are_literal_and_applied_before_latest(history):
    assert filter_history(history, search="[.*]").empty
    assert filter_history(history, search="001").shape[0] == 2
    assert latest_per_case(filter_history(history, choices={"model_name": ["test-old"]})).source_result_id.tolist() == [1]
    date = datetime(2026, 10, 2).date()
    assert filter_history(history, date_range=(date, date)).source_result_id.tolist() == [2]


def test_csv_exports_all_history_and_neutralizes_formulas(history):
    frame = presentation_table(history)
    frame.loc[0, "Produit"] = " =HYPERLINK(\"evil\")"
    exported = csv_bytes(frame).decode("utf-8-sig")
    assert "' =HYPERLINK" in exported
    assert len(pd.read_csv(__import__("io").StringIO(exported), sep=";")) == 3


def test_lexical_diagnostic_requires_the_right_source_and_q7_pair(history):
    row = history.iloc[1].copy()
    docs = {"official": {"raw_text": "Official risk"}, "company": {"raw_text": "Company quote"}}
    assert quote_diagnostics(row, docs)[0] is True
    row.evidence = {**row.evidence, "q1": ["Official risk"]}
    valid, quotes, reasons = quote_diagnostics(row, docs)
    assert valid is False and quotes[0]["Retrouvée dans"] == "Officiel" and reasons
    row.evidence = {**row.evidence, "q1": ["Company quote"], "q7": ["Company quote"]}
    row.q7 = True
    assert quote_diagnostics(row, docs)[0] is False
    row.evidence = {**row.evidence, "q7": ["Official risk", "Company quote"]}
    assert quote_diagnostics(row, docs)[0] is True
    assert quote_diagnostics(row, {})[0] is None


@pytest.fixture
def app(monkeypatch, history):
    import streamlit as st
    from streamlit.testing.v1 import AppTest
    import dashboard.data as data
    st.cache_data.clear()
    st.cache_resource.clear()
    counts = dict(recalls=2, source_results=3, facts=3, document_versions=4,
                  executions=2, read_at=datetime.now(timezone.utc))
    monkeypatch.setattr(data, "make_engine", lambda: object())
    monkeypatch.setattr(data, "read_store", lambda engine: (history.copy(), counts, pd.DataFrame()))
    def detail(engine, result_id):
        metadata = dict.fromkeys(["run_id", "model_revision", "git_commit", "git_dirty", "prompt_hash",
                                  "started_at", "finished_at", "snapshot_observed_at"])
        return metadata, {}
    monkeypatch.setattr(data, "read_detail", detail)
    monkeypatch.setattr(data, "read_observations", lambda *a: pd.DataFrame())
    monkeypatch.setattr(data, "read_dimensions", lambda *a: (pd.DataFrame(), pd.DataFrame()))
    monkeypatch.setattr(data, "read_live_signature", lambda *a: (3, 3, None))
    at = AppTest.from_file(Path(__file__).resolve().parents[1] / "dashboard" / "app.py", default_timeout=30).run()
    yield at
    st.cache_data.clear()
    st.cache_resource.clear()


def test_ui_overview_filters_and_empty_selection(app):
    assert not app.exception
    assert [m.value for m in app.metric] == ["2", "1", "1", "20.0"]
    app.text_input(key="search").set_value("no-such-product").run()
    assert not app.exception
    assert any("Aucune analyse" in item.value for item in app.info)
    app.text_input(key="search").set_value("").run()
    app.toggle(key="all_analyses").set_value(True).run()
    assert not app.exception
    assert len(app.dataframe[0].value) == 3


def test_ui_multiple_protocols_are_not_averaged_together(app, history):
    import streamlit as st
    history.loc[0, "case_id"] = "003"
    history.loc[0, "analysis_status"] = "scored"
    history.loc[0, "evidence_verified"] = True
    st.cache_data.clear()
    app.run()
    assert not app.exception
    assert app.metric[3].value == "—"
    assert any("aucune moyenne globale" in i.value for i in app.info)


def test_ui_new_recall_form_requires_both_documents(app, monkeypatch):
    import dashboard.intake as intake
    monkeypatch.setattr(intake, "read_jobs", lambda *a: (pd.DataFrame(), None))
    app.radio(key="page").set_value("Nouveau rappel").run()
    assert not app.exception
    app.button(key="FormSubmitter:new_recall-Envoyer et analyser").click().run()
    assert not app.exception
    assert any("Ajoutez les deux documents" in item.value for item in app.error)


@pytest.mark.parametrize("page", ["Rappels", "À revoir", "Historique", "Traçabilité"])
def test_ui_navigation_and_honest_unlinked_documents(app, page):
    app.radio(key="page").set_value(page).run()
    assert not app.exception
    if page in ("Rappels", "À revoir", "Historique"):
        assert any("Aucun document lié" in item.value for item in app.info)
    if page == "À revoir":
        assert any("aucun score ni verdict" in item.value for item in app.warning)
        assert app.metric[1].value == "—"


def test_ui_connection_failure_does_not_expose_connection_string(monkeypatch):
    import streamlit as st
    from streamlit.testing.v1 import AppTest
    import dashboard.data as data
    st.cache_data.clear()
    st.cache_resource.clear()
    def fail():
        raise RuntimeError("postgresql://sensitive-user:secret@host/db")
    monkeypatch.setattr(data, "make_engine", fail)
    at = AppTest.from_file(Path(__file__).resolve().parents[1] / "dashboard" / "app.py").run()
    assert not at.exception
    assert "PostgreSQL" in at.error[0].value
    assert "secret" not in at.error[0].value
    st.cache_data.clear()
    st.cache_resource.clear()
