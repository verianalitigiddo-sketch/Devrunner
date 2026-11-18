"""
Sohbet -> relations.chat_service() (scripts/ChatAPI.py). Tüm erişim qt.call ile ana iş parçacığında:
ChatService ve ChatNode QObject'tir. Yeni mesaj SSE "chat_message" olayıyla gelir; sayfa mesaj listesini
sunucudan (kaçışlanmış) yeniden ister. Mesajlar sunucuda saklanmaz; geçmiş yalnızca oturum boyunca.
"""
from __future__ import annotations

from datetime import datetime

from flask import Blueprint, render_template, request

from scripts import relations
from scripts.config import ENV
from web import ui_state
from web.common import qt, toast

bp = Blueprint("chat", __name__)
GENERAL = "general"


def _snapshot() -> dict:
    """Ana iş parçacığında çalışır; Qt nesnelerinden düz veri kopyası çıkarır."""
    from scripts.ChatAPI import fingerprint
    chat = relations.chat_service()
    if chat is None or not chat.running:
        return {"running": False, "error": chat.error if chat else ""}
    node = chat.node
    peers = [{"uid": p.user_id, "name": p.name, "online": node.is_online(p.user_id), "key_state": p.key_state,
              "fingerprint": p.fingerprint, "ip": p.ip, "port": p.port, "version": p.app_version,
              "unread": chat.unread.get(p.user_id, 0)} for p in node.peers()]
    return {"running": True, "error": chat.error, "name": chat.name, "ip": chat.ip, "port": node.port,
            "fingerprint": fingerprint(node.pubkey), "peers": peers, "online": node.online_count(),
            "unread_general": chat.unread.get(GENERAL, 0)}


def _messages(room: str) -> list[dict]:
    chat = relations.chat_service()
    if chat is None:
        return []
    chat.mark_read(room)
    return [dict(m) for m in chat.history.get(room, [])[-500:]]


def _groups(msgs: list[dict]) -> list[dict]:
    """Discord gibi: aynı kişinin 5 dk içindeki art arda mesajları tek başlık altında."""
    groups: list[dict] = []
    for msg in msgs:
        last = groups[-1] if groups else None
        if last and last["from"] == msg["from"] and msg["ts"] - last["last_ts"] < 300:
            last["texts"].append(msg["text"])
            last["last_ts"] = msg["ts"]
        else:
            groups.append({"from": msg["from"], "name": msg["name"], "mine": msg["mine"], "texts": [msg["text"]],
                           "when": datetime.fromtimestamp(msg["ts"]).strftime("%H:%M"), "last_ts": msg["ts"],
                           "color": sum(map(ord, msg["from"])) % 6})
    return groups


def _room() -> str:
    return request.values.get("room") or GENERAL


@bp.get("/")
def page():
    ui_state.update(last_page="chat")
    snap = qt.call(_snapshot)
    return render_template("chat/page.html", snap=snap, room=_room(), enabled=ENV.CHAT_ENABLED)


@bp.get("/rooms")
def rooms():
    return render_template("chat/_rooms.html", snap=qt.call(_snapshot), room=_room())


@bp.get("/header")
def header():
    snap = qt.call(_snapshot)
    room = _room()
    peer = next((p for p in snap.get("peers", []) if p["uid"] == room), None)
    return render_template("chat/_header.html", snap=snap, room=room, peer=peer)


@bp.get("/messages")
def messages():
    room = _room()
    return render_template("chat/_messages.html", groups=_groups(qt.call(_messages, room)), room=room)


@bp.post("/send")
def send():
    room, text = _room(), request.form.get("text", "").strip()
    if not text:
        return render_template("chat/_composer.html", room=room)

    def do_send():
        chat = relations.chat_service()
        if chat is None or not chat.running:
            return None, "Sohbet çalışmıyor."
        if room == GENERAL and chat.node.online_count() == 0:
            return None, "Şu an çevrimiçi kimse yok; mesaj gönderilmedi."
        return chat.send(room, text), ""

    failed, error = qt.call(do_send)
    if error:
        return render_template("chat/_composer.html", room=room, text=text), 200, toast(error, "warning")
    if failed and room != GENERAL:
        return render_template("chat/_composer.html", room=room, text=text), 200, \
            toast("Kişi çevrimdışı veya bağlantı kurulamadı; mesaj gönderilmedi.", "warning")
    headers = toast(f"Ulaşmadı: {', '.join(failed)}", "warning") if failed else {}
    return render_template("chat/_composer.html", room=room), 200, headers


@bp.post("/accept/<uid>")
def accept(uid):
    def do():
        chat = relations.chat_service()
        if chat:
            chat.node.accept_new_key(uid)
    qt.call(do)
    return "", 204, toast("Yeni anahtar kabul edildi.", "info")
