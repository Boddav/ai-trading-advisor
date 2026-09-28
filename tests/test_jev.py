import io
import json

from advisor import jev


def test_system_one_request_shape(monkeypatch):
    captured = {}

    class Res(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout):
        captured["url"], captured["headers"] = req.full_url, dict(req.header_items())
        captured["body"] = json.loads(req.data)
        return Res(json.dumps({"model": "jev-latest", "answers": {"x": {"type": "choice", "choice": "a"}},
                               "usage": {"input_tokens": 1, "output_tokens": 0}}).encode())

    monkeypatch.setattr(jev.urllib.request, "urlopen", fake_urlopen)
    q = {"x": jev.choice("pick", {"a": None, "b": "desc"})}
    out = jev.JevClient("KEY").system_one({"s": 1}, q)
    assert out["answers"]["x"]["choice"] == "a"
    assert captured["url"] == "https://api.typesafe.ai/v1/systemone"
    assert captured["headers"]["Authorization"] == "Bearer KEY"
    assert captured["body"] == {"model": "jev-latest", "state": {"s": 1},
                                "questions": {"x": {"type": "choice", "instructions": "pick",
                                                    "criteria": {"a": None, "b": "desc"}}}}
