"""DevOps: demo verisiyle ayrıştırma ve gerçek istemcinin hata davranışı (responses ile)."""
import responses

from scripts import DevopsAPI as D
from scripts import config

BASE = "https://devops.halkbank.com.tr/DefaultCollection/Halkbank/_apis"


def test_overview_from_demo():
    ov = D.fetch_overview()
    assert ov.source == "demo"
    names = [a.name for a in ov.apps]
    assert "Musteri360" in names and "Baska.Ekip.Uygulama" not in names   # önek filtresi
    p = ov.pipelines
    assert p["KrediSkor"].ci.state == "failed"
    assert p["SubeAnaliz"].ci is None and p["SubeAnaliz"].cd is None           # pipeline yok
    assert p["VeriKalite"].ci is not None and p["VeriKalite"].cd is None
    assert p["KampanyaTakip"].cd.kind == "release"                              # klasik release'e düşer
    assert p["Musteri360"].cd.kind == "build"


def test_history_timeline_and_log():
    ov = D.fetch_overview()
    ci = ov.pipelines["KrediSkor"].ci
    builds = D.history(ci)
    assert builds[0].state == "failed" and builds[0].duration_sec > 0
    steps = D.timeline(builds[0].id)
    failed = [s for s in steps if s.state == "failed"]
    assert failed and failed[0].errors
    assert "pyarrow" in D.build_log(builds[0].id, failed[0].log_id - 1 if failed[0].log_id == 8 else 7)


def test_states():
    assert D.build_state("inProgress", None) == "running"
    assert D.build_state("completed", "partiallySucceeded") == "partial"
    assert D.build_state("notStarted", None) == "queued"
    assert D.deployment_state("notDeployed") == "queued"


@responses.activate
def test_real_client_paginates_and_maps_auth_errors():
    config.save({"DEVOPS_PAT": "pat-xyz", "DEVOPS_VERIFY_TLS": "0"})
    responses.get(f"{BASE}/git/repositories", json={"value": [{"name": "DDDB.Iz.Anm.A", "id": "1"}]},
                  headers={"x-ms-continuationtoken": "t2"})
    responses.get(f"{BASE}/git/repositories", json={"value": [{"name": "DDDB.Iz.Anm.B", "id": "2"}]})
    c = D.Client()
    assert [r["name"] for r in c.get_all("git/repositories")] == ["DDDB.Iz.Anm.A", "DDDB.Iz.Anm.B"]
    assert responses.calls[0].request.headers["Authorization"].startswith("Basic ")

    responses.get(f"{BASE}/build/definitions", body="<html>giriş</html>", status=203,
                  content_type="text/html")
    import pytest
    with pytest.raises(D.DevopsAuthError):
        c.get("build/definitions")


@responses.activate
def test_release_permission_missing_is_warning_not_error():
    config.save({"DEVOPS_PAT": "pat-xyz", "DEVOPS_VERIFY_TLS": "0"})
    responses.get(f"{BASE}/git/repositories", json={"value": [{"name": "DDDB.Iz.Anm.A", "id": "1"}]})
    responses.get(f"{BASE}/build/definitions", json={"value": [
        {"id": 5, "name": "DDDB.Iz.Anm.A_Container-CI",
         "latestBuild": {"id": 9, "buildNumber": "1", "status": "completed", "result": "succeeded"}}]})
    responses.get(f"{BASE}/release/definitions", status=401)
    ov = D.fetch_overview()
    assert ov.pipelines["A"].ci.state == "succeeded" and ov.pipelines["A"].cd is None
    assert ov.warnings and "Release" in ov.warnings[0]
