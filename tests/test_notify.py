import urllib.request

from population import notify


def test_off_without_a_topic(tmp_path, monkeypatch):
    monkeypatch.delenv("NTFY_TOPIC", raising=False)
    assert notify.send(str(tmp_path), "Hi", "there") is False


def test_sends_to_the_topic_from_the_env_file(tmp_path, monkeypatch):
    monkeypatch.delenv("NTFY_TOPIC", raising=False)
    (tmp_path / ".env").write_text("NTFY_TOPIC=secret-topic\nNTFY_NAME=Crownfall\n")
    sent = []

    class Reply:
        def close(self): pass

    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout: sent.append(req) or Reply())
    assert notify.send(str(tmp_path), "Updated", "abc -> def", "high") is True
    req = sent[0]
    assert req.full_url == "https://ntfy.sh/secret-topic" and req.data == b"abc -> def"
    assert req.get_header("Title") == "Crownfall: Updated" and req.get_header("Priority") == "high"


def test_a_failed_send_never_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("NTFY_TOPIC", "t")

    def boom(req, timeout):
        raise OSError("no network")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    assert notify.send(str(tmp_path), "x", "y") is False
