from clankops.redact import redact_url


def test_redact_basic_auth_in_remote() -> None:
    url = "https://user:ghp_secret@github.com/anil-ganti-nbc/oem-radar.git"
    redacted = redact_url(url)
    assert "ghp_secret" not in redacted
    assert "github.com/anil-ganti-nbc/oem-radar.git" in redacted
    assert redacted.startswith("https://***@")
