from copy import deepcopy

import pytest

from src.database.upgrade import verify_source_preservation


def historical_row():
    return {"llm_results": {5: {"id":5,"score":.8,"verdict":"non_toxique",
        "evidence":{"q1":["original quotation"]},
        **{f"q{i}":i<=5 and i!=2 for i in range(1,8)}}}}


def test_original_history_and_new_columns():
    before=historical_row()
    after=deepcopy(before)
    after["llm_results"][5]["scoring_version"]=None
    assert verify_source_preservation(before,after)==0


@pytest.mark.parametrize("mutation",["verdict","score","evidence","missing"])
def test_history_changes_are_detected(mutation):
    before=historical_row()
    after=deepcopy(before)
    if mutation=="missing": after["llm_results"].pop(5)
    else: after["llm_results"][5][mutation]="different"
    with pytest.raises(RuntimeError,match="historical|Historical"):
        verify_source_preservation(before,after)


def test_derived_percentage_is_explicit_and_checked():
    before=historical_row()
    before["llm_results"][5].update(toxicity_score=None,toxicity_score_origin=None)
    after=deepcopy(before)
    after["llm_results"][5].update(toxicity_score=14.29,
        toxicity_score_origin="derived_on_migration:seven-equal-weights-v1")
    assert verify_source_preservation(before,after)==1
    after["llm_results"][5]["toxicity_score"]=99
    with pytest.raises(RuntimeError,match="Historical"):
        verify_source_preservation(before,after)
def test_upgrade_cli_handles_unicode_backup_paths(monkeypatch, capsys):
    import json
    import src.database.upgrade as upgrade
    expected = {"backup": "C:/MySpace\u202f/backups/test.dump", "status": "verified"}
    monkeypatch.setattr(upgrade, "upgrade_database", lambda **kwargs: expected)
    monkeypatch.setattr("sys.argv", ["upgrade"])
    assert upgrade.main() == 0
    output = capsys.readouterr().out
    assert output.isascii()
    assert json.loads(output) == expected

