"""PLAN_V2 Faz C: Aura — kişi eşleme, puanlama, etiketler, eşitleme, depolar, sayfa ve rozetler."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from scripts import AuraAPI as A
from scripts.apps import AppRef
from scripts.DevopsAPI import AppPipelines, PipelineInfo
from tests.test_web import TOKEN, make_app, post

T0 = datetime(2026, 10, 5, 9, 0, tzinfo=timezone.utc)      # Pazartesi, İstanbul 12:00
BURAK = "Burak ALTINIŞIK (STRATEJİ DİJ. DÖNÜŞÜM VE Y.ZEKA DB)"


def F(kind, who=BURAK, app="Musteri360", at=T0, key=None, **attrs):
    F.n = getattr(F, "n", 0) + 1
    return A.Fact(key or f"t:{F.n}", kind, who, app, at, attrs)


# ---------------------------------------------------------------------------
# Kişi eşleme
# ---------------------------------------------------------------------------
def test_person_key_turkish_and_units():
    assert A.person_key(BURAK) == A.person_key("burak altınışık") == A.person_key("BURAK  ALTINIŞIK") == "burak altinişik"
    assert A.person_key("İsmail IŞIK (X)") == A.person_key("ismail ışık") == A.person_key("ISMAIL IŞIK")
    assert A.strip_unit("HALKBANK\\Ayşe Yılmaz") == "Ayşe Yılmaz"


@pytest.mark.parametrize("name,ok", [
    (BURAK, True), ("Ayşe Yılmaz", True), ("1234567", False), ("U1234567", False), ("Ali 1234567", False),
    ("Burak", False), ("Project Collection Build Service (DefaultCollection)", False), ("Azure Pipelines Bot", False),
    ("Build Service (Halkbank)", False), ("ayse.yilmaz@halkbank.com.tr", False), ("", False), ("Can Bot", False),
])
def test_is_person(name, ok):
    assert A.is_person(name) is ok


def test_user_codes_never_score():
    cards = A.score([F("commit", "1234567"), F("commit", "7654321 (X DB)"), F("commit")])
    assert [c.key for c in cards] == ["burak altinişik"] and A.count_ignored([F("commit", "1234567")]) == 1


def test_same_person_merges_and_prefers_unit_name():
    cards = A.score([F("commit", "burak altınışık"), F("commit", BURAK), F("commit", "BURAK ALTINIŞIK")])
    assert len(cards) == 1 and cards[0].name == BURAK and cards[0].aura == 30


# ---------------------------------------------------------------------------
# Kurallar
# ---------------------------------------------------------------------------
def test_points_table():
    facts = [F("commit"), F("commit", merge=True), F("revert"), F("ci_ok"), F("ci_partial"), F("ci_fail"),
             F("ci_canceled"), F("cd_ok"), F("cd_fail"), F("cd_ok", prod=True), F("cd_fail", prod=True),
             F("pr_open"), F("pr_merged"), F("pr_abandoned"), F("review")]
    pts = [f.base_points() for f in facts]
    assert pts == [10, 2, -15, 5, 1, -5, 0, 15, -10, 25, -20, 5, 20, -5, 8]
    assert A.score(facts)[0].aura == sum(pts)


def test_daily_commit_cap_and_rules_rescore(monkeypatch):
    facts = [F("commit", at=T0 + timedelta(minutes=i)) for i in range(20)]
    facts.append(F("commit", at=T0 + timedelta(days=1)))
    c = A.score(facts)[0]
    assert c.aura == (A.DAILY_COMMIT_CAP + 1) * 10 and c.totals.commits == 21
    monkeypatch.setitem(A.RULES, "commit", 4)                # kural değişince geçmiş yeniden puanlanır
    assert A.score(facts)[0].aura == (A.DAILY_COMMIT_CAP + 1) * 4


def test_levels_and_titles():
    assert [A.level_for(x) for x in (-50, 0, 24, 25, 99, 100, 2025, 9025, 21025)] == [1, 1, 1, 2, 2, 3, 10, 20, 30]
    assert A.aura_for_level(10) == 2025
    assert A.title_for(1, -5) == "Build Kıran" and A.title_for(1, 0) == "Git Noob" and A.title_for(4, 300) == "Noob Coder"
    assert A.title_for(20, 9100) == "Programming Chad" and A.title_for(33, 26000) == "DevOps Killer"
    assert A.title_for(55, 80000) == "10x Efsane"


def test_stats_are_bounded_and_smoothed():
    empty = A.stats_for(A.Totals())
    assert all(1 <= v <= 99 for v in empty.values()) and empty["DEX"] < 60
    one_ok = A.stats_for(A.Totals(ci_ok=1, ci_total=1))
    many_ok = A.stats_for(A.Totals(ci_ok=300, ci_total=300))
    assert one_ok["DEX"] < many_ok["DEX"] <= 99                # tek başarı uç değer vermez
    assert A.stats_for(A.Totals(commits=5000))["STR"] == 99


def test_badges():
    night = [F("commit", at=datetime(2026, 10, 6, 0, 30, tzinfo=timezone.utc) + timedelta(days=i)) for i in range(10)]  # 03:30 İst.
    green = [F("ci_ok", at=T0 + timedelta(hours=i)) for i in range(10)]
    fire = [F("cd_fail", at=T0, app="A1"), F("cd_ok", at=T0 + timedelta(minutes=30), app="A1")]
    friday = [F("cd_ok", at=datetime(2026, 10, 9, 14, 30, tzinfo=timezone.utc), prod=True)]   # Cuma 17:30 İst.
    phoenix = [F("revert", at=T0 - timedelta(days=3)), F("commit", at=T0 - timedelta(days=2)),
               F("commit", at=T0 - timedelta(days=1))]
    c = A.score(night + green + fire + friday + phoenix)[0]
    for bid in ("first_blood", "night_owl", "green_streak", "firefighter", "friday_courage", "phoenix"):
        assert bid in c.earned, bid
    assert "flawless" not in c.earned
    done, frac, text = A.BADGE_BY_ID["flawless"].progress(c.totals)
    assert not done and 0 < frac < 1 and text == "10/25"


def test_freeze_badge_uses_freezes():
    from scripts.FreezeAPI import Freeze
    fr = [Freeze(1, date(2026, 10, 5), date(2026, 10, 7), "Yıl sonu")]
    assert "freeze_runner" in A.score([F("cd_ok")], freezes=fr)[0].earned
    assert "freeze_runner" not in A.score([F("cd_ok")])[0].earned


def test_streak_badges_and_equip_label():
    days = [F("commit", at=T0 + timedelta(days=i)) for i in range(30)]
    c = A.score(days, profiles={"burak altinişik": {"equipped": "marathon"}})[0]
    assert c.totals.longest_streak == 30 and "marathon" in c.earned and c.label.endswith("Maratoncu")
    c2 = A.score(days, profiles={"burak altinişik": {"equipped": "club_1000"}})[0]   # kazanılmamış: unvan
    assert c2.label == c2.title


def test_period_and_app_filters():
    facts = [F("commit", at=T0 - timedelta(days=60), app="Eski"), F("commit", at=T0, app="Yeni")]
    assert A.score(facts)[0].aura == 20
    assert A.score(facts, since=T0 - timedelta(days=30))[0].aura == 10
    assert A.score(facts, app="Eski")[0].aura == 10


# ---------------------------------------------------------------------------
# Eşitleme: sahte DevOps istemcisi
# ---------------------------------------------------------------------------
class FakeDevops:
    def __init__(self):
        self.calls = []
        now = datetime.now(timezone.utc)
        iso = lambda d: d.strftime("%Y-%m-%dT%H:%M:%S.1234567Z")   # noqa: E731
        self.commits = [
            {"commitId": "a" * 40, "comment": "Özellik", "author": {"name": BURAK, "date": iso(now - timedelta(days=2))}},
            {"commitId": "b" * 40, "comment": "Revert \"Özellik\"", "author": {"name": "Ayşe Yılmaz", "date": iso(now - timedelta(days=1))}},
            {"commitId": "c" * 40, "comment": "x", "author": {"name": "1234567", "date": iso(now - timedelta(days=1))}},
            {"commitId": "d" * 40, "comment": "eski", "author": {"name": BURAK, "date": iso(now - timedelta(days=400))}},
        ]
        self.prs = [{"pullRequestId": 7, "status": "completed", "createdBy": {"displayName": BURAK},
                     "creationDate": iso(now - timedelta(days=3)), "closedDate": iso(now - timedelta(days=2)),
                     "reviewers": [{"id": "r1", "displayName": "Ayşe Yılmaz", "vote": 10},
                                   {"id": "r2", "displayName": "Can Kaya", "vote": 0},
                                   {"id": "g", "displayName": "[Proje]\\Takım", "vote": 10, "isContainer": True}]}]
        self.builds = {11: [{"id": 501, "status": "completed", "result": "succeeded", "finishTime": iso(now - timedelta(hours=5)),
                             "requestedFor": {"displayName": BURAK}},
                            {"id": 502, "status": "completed", "result": "failed", "finishTime": iso(now - timedelta(hours=4)),
                             "requestedFor": {"displayName": "Ayşe Yılmaz"}},
                            {"id": 503, "status": "inProgress", "requestedFor": {"displayName": BURAK}}]}
        self.deps = {21: [{"id": 9001, "deploymentStatus": "succeeded", "completedOn": iso(now - timedelta(hours=2)),
                           "releaseEnvironment": {"name": "PROD"}, "requestedFor": {"displayName": BURAK}},
                          {"id": 9002, "deploymentStatus": "failed", "completedOn": iso(now - timedelta(hours=3)),
                           "releaseEnvironment": {"name": "Test"}, "requestedFor": {"displayName": "Ayşe Yılmaz"}}]}

    def get(self, path, params=None):
        self.calls.append((path, dict(params or {})))
        if path.endswith("/commits"):
            return {"value": self.commits if not params.get("$skip") else []}
        if path.endswith("/pullrequests"):
            return {"value": self.prs if not params.get("$skip") else []}
        if path == "build/builds":
            return {"value": self.builds.get(params["definitions"], [])}
        if path == "release/deployments":
            return {"value": self.deps.get(params["definitionId"], [])}
        raise AssertionError(path)


APPS = [AppRef("Musteri360", "DDDB.Iz.Anm.Musteri360", "repo-1")]
PIPES = {"Musteri360": AppPipelines(PipelineInfo("build", 11, "DDDB.Iz.Anm.Musteri360_Container-CI", ""),
                                    PipelineInfo("release", 21, "DDDB.Iz.Anm.Musteri360_Container-CD", ""))}


def _check_sync(store):
    client = FakeDevops()
    r1 = A.collect(store, client, APPS, PIPES, since_days=180, by="Test")
    assert not r1.errors and r1.ignored == 1 and r1.scopes == 4
    n = store.count()
    r2 = A.collect(store, client, APPS, PIPES, since_days=180, by="Test")      # yinelenebilir
    assert r2.new == 0 and store.count() == n
    kinds = sorted(f.kind for f in store.facts())
    assert kinds == sorted(["commit", "revert", "commit", "pr_open", "pr_merged", "review", "ci_ok", "ci_fail",
                            "cd_ok", "cd_fail"]), kinds
    prod = [f for f in store.facts() if f.kind == "cd_ok"][0]
    assert prod.attrs["prod"] is True
    commit_call = [c for c in client.calls if c[0].endswith("/commits")][-1][1]
    assert "searchCriteria.fromDate" in commit_call                    # ikinci turda filigrandan
    cards = {c.key: c for c in A.score(store.facts())}
    b = cards["burak altinişik"]
    assert b.aura == 10 + 5 + 20 + 5 + 25 and cards["ayşe yilmaz"].aura == -15 + 8 - 5 - 10
    return store


def test_collect_memory_store_is_idempotent():
    s = _check_sync(A.MemoryStore())
    assert s.last_sync()["by"] == "Test"


def test_collect_db_store_matches_memory(db):
    mem = _check_sync(A.MemoryStore())
    dbs = _check_sync(A.DbStore())
    key = lambda c: (c.key, c.aura, c.level, tuple(c.earned))           # noqa: E731
    assert [key(c) for c in A.score(dbs.facts())] == [key(c) for c in A.score(mem.facts())]
    dbs.set_profile("burak altinişik", "Test", equipped="first_blood")
    dbs.set_profile("burak altinişik", "Test", hidden=True)
    assert dbs.profiles()["burak altinişik"] == {"equipped": "first_blood", "hidden": True}
    from scripts.database import read_sql
    assert read_sql("SELECT value FROM dr_meta WHERE key = 'aura_schema'")[0]["value"] == "1"
    with dbs.try_lock() as l1:                                          # ikinci istemci eşitlemez
        assert l1.acquired
        assert A.collect(dbs, FakeDevops(), APPS, PIPES, 180).skipped


def test_collect_survives_scope_errors():
    class Broken(FakeDevops):
        def get(self, path, params=None):
            if path == "build/builds":
                raise RuntimeError("403")
            return super().get(path, params)
    r = A.collect(A.MemoryStore(), Broken(), APPS, PIPES, 180)
    assert len(r.errors) == 1 and r.scopes == 3


def test_demo_facts_are_deterministic_and_include_ignored():
    a = A.demo_facts(["X", "Y"], me="Deneme Kişi", now=T0)
    b = A.demo_facts(["X", "Y"], me="Deneme Kişi", now=T0)
    assert A.fingerprint(a) == A.fingerprint(b) and A.count_ignored(a) >= 20
    cards = A.score(a)
    assert any(c.key == "deneme kişi" for c in cards) and any(c.aura < 0 for c in cards)


# ---------------------------------------------------------------------------
# Web: sayfa, rozetler
# ---------------------------------------------------------------------------
@pytest.fixture
def client():
    from scripts import DevopsAPI
    from scripts import apps as catalog
    from scripts import config
    from web import aura_service
    config.save({"USER_NAME": "Burak Altınışık"})
    config.load()
    ov = DevopsAPI.fetch_overview()
    catalog.set_apps(ov.apps, ov.source, ov.fetched_at)
    aura_service.reset()
    c = make_app().test_client()
    c.get(f"/auth?t={TOKEN}")
    yield c
    aura_service.reset()
    catalog.set_apps([], "", "")


def test_aura_menu_between_chat_and_settings():
    from web.app import PAGES
    ids = [p.id for p in PAGES]
    assert ids.index("aura") == ids.index("chat") + 1 and ids.index("settings") == ids.index("aura") + 1


def test_aura_page_and_parts(client):
    body = client.get("/aura/").get_data(as_text=True)
    assert "Örnek veri" in body and 'id="aura-board"' in body and "i-trophy" in body
    me = client.get("/aura/me").get_data(as_text=True)
    assert "Burak ALTINIŞIK (STRATEJİ" in me and "aura" in me and "STR" in me
    board = client.get("/aura/board?period=30d").get_data(as_text=True)
    assert "Kaan TUNÇ" in board and "Build Kıran" in board and "1234567" not in board
    assert "selected" in board
    status = client.get("/aura/status").get_data(as_text=True)
    assert "kayıt yok sayıldı" in status
    badges = client.get("/aura/badges").get_data(as_text=True)
    assert "İlk Kan" in badges and "Tak" in badges


def test_header_and_chat_show_badge(client):
    home = client.get("/home/").get_data(as_text=True)
    assert 'class="aura-chip' in home and "Sv " in home
    from web import aura_service
    b = aura_service.badge_for("burak altınışık")
    assert b and b["level"] >= 1 and aura_service.badge_for("1234567") is None


def test_badge_html_is_escaped(client, monkeypatch):
    from web import aura_service
    monkeypatch.setattr(aura_service, "badge_for", lambda n: {"level": 3, "label": "<script>x</script>", "tip": "\"><img>",
                                                             "negative": False})
    part = client.get("/api/header?part=aura").get_data(as_text=True)
    assert "<script>x" not in part and "&lt;script&gt;" in part and '"><img>' not in part


def test_equip_and_hide(client):
    from web import aura_service
    me = aura_service.me()
    bid = me.earned[0]
    assert post(client, "/aura/equip", data={"badge": bid}).status_code == 204
    assert aura_service.me().equipped == bid and aura_service.badge_for("Burak Altınışık")["label"].endswith(
        AuraAPI_name(bid))
    assert post(client, "/aura/equip", data={"badge": "club_1000"}).status_code in (204, 400)
    assert post(client, "/aura/equip", data={"badge": "yok"}).status_code == 400
    assert post(client, "/aura/hide", data={"hidden": "1"}).status_code == 204
    assert aura_service.badge_for("Burak Altınışık") is None                     # gizli: rozet yok
    assert "gizli" in client.get("/aura/board").get_data(as_text=True)          # kendisi görür
    post(client, "/aura/hide", data={"hidden": "0"})


def AuraAPI_name(bid):
    return A.BADGE_BY_ID[bid].name


def test_unmatched_name_gets_suggestions(client):
    from scripts import config
    from web import aura_service
    client.get("/aura/me")                       # örnek olgular eski adla kuruldu (demo kendini ekler)
    config.save({"USER_NAME": "Burak Altinisik"})
    config.load()
    aura_service.invalidate()
    body = client.get("/aura/me").get_data(as_text=True)
    assert "eşleşmedi" in body and "ALTINIŞIK" in body


def test_sync_in_demo_does_not_hit_devops(client):
    from web import aura_service
    assert post(client, "/aura/sync").status_code == 200
    import time
    for _ in range(50):
        if not aura_service.sync_state.running:
            break
        time.sleep(0.05)
    assert aura_service.sync_state.error == "" and aura_service.mode() == "demo"


def test_chat_templates_show_escaped_badge(client, monkeypatch):
    from flask import render_template

    from web import aura_service
    monkeypatch.setattr(aura_service, "badge_for", lambda n: {"level": 7, "label": "<b>x</b>", "tip": "t", "negative": False}
                        if n == "Ayşe Yılmaz" else None)
    app = client.application
    with app.test_request_context("/chat/", base_url="http://127.0.0.1:5999"):
        msgs = render_template("chat/_messages.html", room="general", groups=[
            {"name": "Ayşe Yılmaz", "mine": False, "color": 0, "when": "10:00", "texts": ["selam"]},
            {"name": "1234567", "mine": False, "color": 1, "when": "10:01", "texts": ["x"]}])
        rooms = render_template("chat/_rooms.html", room="general", snap={
            "running": True, "unread_general": 0, "online": 1, "name": "Ben", "ip": "1.2.3.4", "port": 1, "fingerprint": "f",
            "peers": [{"uid": "u1", "name": "Ayşe Yılmaz", "online": True, "ip": "1.2.3.5", "port": 2, "key_state": "ok",
                       "unread": 0}]})
    assert msgs.count('class="aura-chip') == 1 and "Sv 7" in msgs and "&lt;b&gt;x" in msgs and "<b>x</b>" not in msgs
    assert 'class="aura-chip' in rooms
