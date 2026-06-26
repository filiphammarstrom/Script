"""Tål-tester: modeller som ibland returnerar nästlade fält som JSON-strängar."""
from app.models import AnalyzeResult, ReviseResult


def test_analyze_result_coerces_stringified_clarifications():
    # Reproducerar det rapporterade felet: clarifications kom som en sträng.
    r = AnalyzeResult.model_validate({
        "new_elements": '[{"id": 0, "type": "action", "text": "Bobo tittar på sin son."}]',
        "story_bible_updates": '{"characters": [{"name": "BOBO"}], "locations": [], "notes": []}',
        "clarifications": '[{"element_id": 0, "question": "Vem talar?", "options": ["BOBO", "SONEN"]}]',
    })
    assert r.new_elements[0].text == "Bobo tittar på sin son."
    assert r.story_bible_updates.characters[0].name == "BOBO"
    assert r.clarifications[0].question == "Vem talar?"
    assert r.clarifications[0].options == ["BOBO", "SONEN"]


def test_analyze_result_handles_empty_and_invalid_strings():
    r = AnalyzeResult.model_validate({
        "new_elements": "[]",
        "clarifications": "",          # tom sträng -> tom lista
        "story_bible_updates": "",     # tom sträng -> tomt objekt (defaults)
    })
    assert r.new_elements == []
    assert r.clarifications == []
    assert r.story_bible_updates.characters == []


def test_analyze_result_still_accepts_real_lists():
    r = AnalyzeResult.model_validate({
        "new_elements": [{"id": 0, "type": "dialogue", "text": "Hej."}],
        "clarifications": [],
    })
    assert r.new_elements[0].type == "dialogue"


def test_revise_result_coerces_stringified_operations():
    r = ReviseResult.model_validate({
        "operations": '[{"op": "delete", "target_id": 2, "reason": "dubblett"}]',
        "summary": "Tog bort dubbletten.",
    })
    assert r.operations[0].op == "delete"
    assert r.operations[0].target_id == 2
