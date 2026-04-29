from pathlib import Path
import yaml
from coinfiliate.config import Settings, load_settings


def test_load_settings_from_yaml_and_env(tmp_path, monkeypatch):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(yaml.safe_dump({
        "networks": ["flexoffers", "awin"],
        "sync": {"page": 2, "page_size": 50, "selectable_fields": "all"},
        "runner": {"max_shops_per_batch": 10, "max_concurrency": 2, "inter_shop_jitter_ms": [100, 500]},
        "harvest": {"networkidle_timeout_seconds": 10, "consent_wait_ms": 1000, "review_threshold": 0.5},
        "writeback": {"verify_after_save": False},
        "llm": {"provider": "gemini", "model": "gemini-2.5-flash", "max_retries": 2, "timeout_seconds": 20},
        "logging": {"level": "DEBUG", "debug_log_retention_days": 3},
    }))
    monkeypatch.setenv("COINFILIATE_EMAIL", "a@b.com")
    monkeypatch.setenv("COINFILIATE_PASS", "x")
    monkeypatch.setenv("GEMINI_API_KEY", "gk")

    s = load_settings(cfg)

    assert s.networks == ["flexoffers", "awin"]
    assert s.harvest.review_threshold == 0.5
    assert s.llm.provider == "gemini"
    assert s.coinfiliate_email == "a@b.com"
    assert s.gemini_api_key == "gk"


def test_missing_credentials_raises(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(yaml.safe_dump({"networks": ["flexoffers"]}))
    try:
        load_settings(cfg)
    except Exception as e:
        assert "COINFILIATE_EMAIL" in str(e) or "coinfiliate_email" in str(e).lower()
    else:
        raise AssertionError("expected exception")
