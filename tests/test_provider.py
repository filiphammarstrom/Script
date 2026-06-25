"""Tester för OpenAI (GPT) som alternativ AI-motor – SDK:t stubbas, inget nätverk."""
from app import analyze as analyze_mod
from app.models import AnalyzeResult, GlobalSettings, Project, ReviseResult, ScreenplayElement


class _FakeFn:
    def __init__(self, arguments):
        self.arguments = arguments


class _FakeToolCall:
    def __init__(self, arguments):
        self.function = _FakeFn(arguments)


class _FakeMsg:
    def __init__(self, arguments):
        self.tool_calls = [_FakeToolCall(arguments)]


class _FakeResp:
    def __init__(self, arguments):
        self.choices = [type("C", (), {"message": _FakeMsg(arguments)})()]


class _FakeClient:
    """Härmar OpenAI-klientens client.chat.completions.create(...)."""

    def __init__(self, arguments):
        create = lambda **kw: _FakeResp(arguments)  # noqa: E731
        self.chat = type("Chat", (), {"completions": type("Comp", (), {"create": staticmethod(create)})()})()


def test_analyze_openai_parses_function_call(monkeypatch):
    canned = AnalyzeResult(new_elements=[ScreenplayElement(id=0, type="action", text="Hon går.")])
    monkeypatch.setattr(analyze_mod, "_openai_client", lambda key: _FakeClient(canned.model_dump_json()))
    res = analyze_mod.analyze(Project(id="x"), "hon går", GlobalSettings(), provider="openai")
    assert res.new_elements[0].text == "Hon går."


def test_revise_openai_parses_function_call(monkeypatch):
    canned = ReviseResult(summary="ett förslag")
    monkeypatch.setattr(analyze_mod, "_openai_client", lambda key: _FakeClient(canned.model_dump_json()))
    res = analyze_mod.revise(Project(id="x"), "ändra något", GlobalSettings(), provider="openai")
    assert res.summary == "ett förslag"
