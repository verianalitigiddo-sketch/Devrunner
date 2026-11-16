"""Ayarlar, temalar, adlandırma, secret arındırma."""
from scripts import apps, config, theme
from scripts.config import ENV


def test_settings_roundtrip_and_types():
    changed = config.save({"HEALTH_INTERVAL_SEC": "30", "HEALTH_ENABLED": "0", "DEVOPS_PAT": "gizli-pat-123"})
    assert set(changed) == {"HEALTH_INTERVAL_SEC", "HEALTH_ENABLED", "DEVOPS_PAT"}
    assert ENV.HEALTH_INTERVAL_SEC == 30 and ENV.HEALTH_ENABLED is False
    config.load()   # .env'den yeniden oku
    assert ENV.DEVOPS_PAT == "gizli-pat-123"
    assert "DEVOPS_PAT='gizli-pat-123'" in config.ENV_FILE.read_text()


def test_settings_validation():
    import pytest
    with pytest.raises(ValueError):
        config.save({"HEALTH_INTERVAL_SEC": "altmış"})


def test_os_env_wins(monkeypatch):
    config.save({"DB_HOST": "dosyadan"})
    monkeypatch.setenv("DB_HOST", "ortamdan")
    config.load()
    assert ENV.DB_HOST == "ortamdan" and config.is_from_os("DB_HOST")


def test_readonly_user_id_not_overwritten():
    config.save({"USER_ID": "baska"})
    assert ENV.USER_ID == "00000000-0000-4000-8000-000000000001"


def test_redact():
    config.save({"DEVOPS_PAT": "abcd1234secret", "K8S_DEV_TOKEN": "eyJhbGciOi.token.value"})
    text = "hata: abcd1234secret ve eyJhbGciOi.token.value https://x/?token=zzz&a=1 Authorization: Basic OmFiY2QxMjM0"
    out = config.redact(text)
    assert "abcd1234secret" not in out and "eyJhbGciOi" not in out and "zzz" not in out and "OmFiY2Qx" not in out


def test_env_example_is_up_to_date():
    assert config.EXAMPLE_FILE.read_text(encoding="utf-8") == config.example_text(), \
        ".env.example eski: python -m scripts.config çalıştırın"


def test_every_theme_has_all_keys_and_builds():
    keys = set(theme.PALETTES["light"])
    for name, pal in theme.PALETTES.items():
        assert set(pal) == keys, name
        qss = theme.build_qss(name)
        assert "$" not in qss, f"{name}: doldurulmamış yer tutucu"
    assert [k for k, _ in theme.names()] == ["light", "dark", "cyberpunk", "dracula", "nineties"]


def test_naming_rules():
    repo = {"name": "DDDB.Iz.Anm.Musteri360", "id": "r1", "defaultBranch": "refs/heads/master"}
    a = apps.app_from_repo(repo)
    assert a.name == "Musteri360"
    assert a.ci_name == "DDDB.Iz.Anm.Musteri360_Container-CI"
    assert a.cd_name == "DDDB.Iz.Anm.Musteri360_Container-CD"
    assert a.urls() == {"dev": "https://musteri360.dk8s.gentest.hqtest.local/",
                        "test": "https://musteri360.tk8s.gentest.hqtest.local/",
                        "prod": "https://musteri360.pk8s.gen.halkbank.local/"}
    assert apps.app_from_repo({"name": "Baska.Repo"}) is None
    assert apps.app_from_repo({"name": "DDDB.Iz.Anm."}) is None


def test_override_wins():
    a = apps.AppRef("Ozel", "DDDB.Iz.Anm.Ozel")
    apps.set_overrides([{"app_name": "Ozel", "url_prod": "https://ozel.example/", "k8s_workload": "ozel-api",
                         "health_muted": "dev,prod"}])
    try:
        assert a.url("prod") == "https://ozel.example/"
        assert a.url("dev") == "https://ozel.dk8s.gentest.hqtest.local/"
        assert a.k8s_workload() == "ozel-api" and a.muted_envs() == {"dev", "prod"}
    finally:
        apps.set_overrides([])
