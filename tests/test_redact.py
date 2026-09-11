from clankops.redact import redact_url, sanitize_captured, sanitize_text

WEBHOOK = "https://discord.com/api/webhooks/000000000000000000/do-not-store-this"


def test_redact_basic_auth_in_remote() -> None:
    url = "https://user:ghp_secret@github.com/anil-ganti-nbc/oem-radar.git"
    redacted = redact_url(url)
    assert "ghp_secret" not in redacted
    assert "github.com/anil-ganti-nbc/oem-radar.git" in redacted
    assert redacted.startswith("https://***@")


def test_redact_url_secret_query_and_fragment() -> None:
    url = "https://example.test/hook?token=query-token-secret&ok=1#access_token=frag-secret"
    redacted = redact_url(url)
    assert "query-token-secret" not in redacted
    assert "frag-secret" not in redacted
    assert "ok=1" in redacted
    assert "example.test/hook" in redacted


def test_sanitize_text_bearer_github_and_webhook() -> None:
    text = (
        f"see {WEBHOOK} and Bearer super-bearer-secret-value "
        "and ghp_abcdefghijklmnopqrstuvwxyz012345"
    )
    cleaned = sanitize_text(text)
    assert "do-not-store-this" not in cleaned
    assert "discord.com/api/webhooks" not in cleaned
    assert "super-bearer-secret-value" not in cleaned
    assert "ghp_abcdefghijklmnopqrstuvwxyz012345" not in cleaned


def test_sanitize_captured_redacts_non_secret_keys() -> None:
    cleaned = sanitize_captured(
        {
            "url": WEBHOOK,
            "value": "ghp_abcdefghijklmnopqrstuvwxyz012345",
            "homepage": "https://github.com/anil-ganti-nbc/oem-radar",
            "webhook_configured": "yes",
            "nested": {"url": "https://example.test/?api_key=nested-api-secret"},
        }
    )
    assert cleaned["url"] == "[redacted]"
    assert cleaned["value"] == "[redacted]"
    assert cleaned["homepage"] == "https://github.com/anil-ganti-nbc/oem-radar"
    assert cleaned["webhook_configured"] == "yes"
    assert "nested-api-secret" not in cleaned["nested"]["url"]


def test_ordinary_host_and_path_survive() -> None:
    assert sanitize_text("ubuntu-4gb-hel1-1") == "ubuntu-4gb-hel1-1"
    assert sanitize_text("/home/deploy/staging/oem-radar") == "/home/deploy/staging/oem-radar"
    assert sanitize_text("20 * * * *") == "20 * * * *"


def test_secret_keys_are_redacted_even_for_flag_shaped_values() -> None:
    assert sanitize_captured({"token": "yes"})["token"] == "[redacted]"
    assert sanitize_captured({"password": True})["password"] == "[redacted]"
    assert sanitize_captured({"webhook_configured": "yes"})["webhook_configured"] == "yes"
