"""Ortak veri: notlar, Bilge talepleri, freeze, ezmeler, geçmiş, şema sürümü (yerel PostgreSQL gerekir)."""
from datetime import date

import pytest

from scripts import BilgeAPI, FreezeAPI, NotesAPI, apps


def test_schema_version_and_user(db):
    assert db.read_sql("SELECT value FROM dr_meta WHERE key = 'schema_version'")[0]["value"] == str(db.SCHEMA_VERSION)
    assert db.read_sql("SELECT display_name FROM dr_users")[0]["display_name"] == "Test Kişi"
    assert db.init_db() == ""          # ikinci kez: idempotent


def test_newer_schema_warns(db):
    db.execute("UPDATE dr_meta SET value = '99' WHERE key = 'schema_version'")
    assert "yeni" in db.init_db()


def test_notes_crud_soft_delete_and_audit(db):
    nid = NotesAPI.add_note("Deploy notu", "**dikkat**", "Musteri360", "prod", pinned=True)
    NotesAPI.update_note(nid, "Deploy notu", "güncel", "Musteri360", "prod", True)
    assert NotesAPI.list_notes("Musteri360")[0]["body"] == "güncel"
    assert NotesAPI.list_notes(search="güncel")
    assert NotesAPI.counts_by_app() == {"Musteri360": 1}
    NotesAPI.delete_note(nid)
    assert NotesAPI.list_notes() == []
    assert db.read_sql("SELECT deleted_at FROM dr_notes WHERE id = :id", {"id": nid})[0]["deleted_at"]
    actions = [h["action"] for h in db.history(entity="note", entity_id=nid)]
    assert actions == ["delete", "update", "create"]
    assert db.history(app_name="Musteri360")[0]["user_name"] == "Test Kişi"
    with pytest.raises(ValueError):
        NotesAPI.add_note("  ", "x")


def test_bilge_requests(db):
    rid = BilgeAPI.add_request("Port açılması", "https://bilge.local/talep/123", "KrediSkor")
    with pytest.raises(ValueError):
        BilgeAPI.add_request("Kötü", "javascript:alert(1)")
    BilgeAPI.update_request(rid, "Port açılması", "https://bilge.local/talep/123", "KrediSkor", "Kapandı")
    assert BilgeAPI.list_requests(status="Kapandı")[0]["id"] == rid
    assert BilgeAPI.counts_by_app() == {}       # kapanan sayılmaz
    detail = db.history(entity="request", entity_id=rid)[0]["detail"]
    assert detail["status"] == "Açık → Kapandı"


def test_freezes(db):
    FreezeAPI.add_freeze(date(2020, 1, 1), date(2020, 1, 3), "Eski")
    fid = FreezeAPI.add_freeze(date(2099, 12, 25), date(2099, 12, 31), "Gelecek")
    with pytest.raises(ValueError):
        FreezeAPI.add_freeze(date(2099, 2, 2), date(2099, 2, 1), "ters")
    assert [f.title for f in FreezeAPI.refresh()] == ["Gelecek"]            # geçmiş önbelleğe girmez
    assert len(FreezeAPI.list_freezes()) == 2
    FreezeAPI.delete_freeze(fid)
    assert FreezeAPI.refresh() == []


def test_overrides(db):
    apps.save_override("Ozel", {"url_prod": "https://ozel/", "health_muted": "dev"})
    apps.set_overrides([])
    apps.load_overrides()
    a = apps.AppRef("Ozel", "DDDB.Iz.Anm.Ozel")
    assert a.url("prod") == "https://ozel/" and a.muted_envs() == {"dev"}
    apps.save_override("Ozel", {"url_prod": ""})                            # kurala dön
    assert a.url("prod") == "https://ozel.pk8s.gen.halkbank.local/"
    apps.set_overrides([])


def test_presence(db):
    from scripts import ChatAPI
    ChatAPI.sync_presence("00000000-0000-4000-8000-000000000002", "10.0.0.2", 47800, "pk")
    others = ChatAPI.sync_presence("00000000-0000-4000-8000-000000000001", "10.0.0.1", 47800, "pk1")
    assert [o["ip"] for o in others] == ["10.0.0.2"]
    ChatAPI.remove_presence("00000000-0000-4000-8000-000000000002")
    assert ChatAPI.sync_presence("00000000-0000-4000-8000-000000000001", "10.0.0.1", 47800, "pk1") == []


def test_health_events_deduplicated(db):
    from scripts.HealthAPI import Change, HealthState, events, record_changes
    st = HealthState("A", "dev", "https://a/", state="down", status_code=503)
    record_changes([Change("A", "dev", "up", "down", st)], "Biri")
    record_changes([Change("A", "dev", "up", "down", st)], "Başkası")   # ikinci istemci aynı değişimi gördü
    assert len(events("A")) == 1
