"""
Çevrimiçi geliştiriciler arasında doğrudan (P2P), uçtan uca şifreli sohbet.

BULUŞMA     Eşler birbirini ortak PostgreSQL'deki dr_presence tablosundan bulur: her istemci IP, port
            ve açık anahtarını yazar, 20 sn'de bir last_seen'i günceller, kapanışta satırını siler.
            (UDP yayını yalnızca aynı alt ağda çalışır; VPN / farklı VLAN'da çalışmaz.)
ÇEVRİMİÇİ   last_seen 60 sn'den yeni VE P2P el sıkışma başarılı.
TAŞIMA      Mesajlar veritabanından geçmez; eşler arasında doğrudan TCP (QTcpServer / QTcpSocket).
            Çerçeve: 4 bayt uzunluk + 1 bayt tür + gövde, en çok MAX_FRAME bayt.
ŞİFRELEME   PyNaCl (libsodium). Her istemcinin kalıcı bir X25519 anahtar çifti vardır (.local/chat/).
            El sıkışma: taraflar geçici (oturumluk) açık anahtarlarını, kalıcı anahtarlarla kurulan
            Box(statik_ben, statik_o) içinde değiş tokuş eder -> karşı taraf kalıcı anahtarın sahibidir.
            Oturum Box(geçici_ben, geçici_o) ile şifrelenir (ileriye dönük gizlilik). Mesajlarda artan
            sıra numarası vardır (tekrar oynatma reddedilir). Kendi şifreleme şeması tasarlanmadı.
ANAHTAR     Açık anahtarlar ortak veritabanından gelir; veritabanına yazabilen biri anahtar değiştirebilir.
GÜVENİ      İlk görülen anahtar yerelde sabitlenir (known_keys.json); değişirse o kişiyle bağlantı
            kurulmaz ve kullanıcı uyarılır, kişi ayrıntısında parmak izi gösterilir.
KAPSAM      Birebir sohbet + "Genel" oda (her çevrimiçi eşe ayrı şifrelenip gönderilir). Çevrimdışı
            kişiye mesaj gitmez. Geçmiş yalnızca oturum boyunca bellekte tutulur.

Taşıma katmanı (PeerLink) soyuttur: doğrudan TCP banka ağında engellenirse aynı şifreli çerçeveler
PostgreSQL LISTEN/NOTIFY üzerinden taşınabilir (PLAN.md 5.9).
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import socket
import struct
import sys
import time
from dataclasses import dataclass, field

from nacl.exceptions import CryptoError
from nacl.public import Box, PrivateKey, PublicKey
from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtNetwork import QAbstractSocket, QHostAddress, QTcpServer, QTcpSocket

from scripts import __version__
from scripts.config import ENV, LOCAL_DIR

log = logging.getLogger(__name__)

PROTOCOL = 1
MAX_FRAME = 256 * 1024
MAX_TEXT = 4000
HANDSHAKE_TIMEOUT_MS = 10_000
ONLINE_WINDOW_SEC = 60
KEY_DIR = LOCAL_DIR / "chat"
GENERAL = "general"


class HandshakeError(Exception):
    pass


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def unb64(text: str) -> bytes:
    return base64.b64decode(text.encode())


def fingerprint(pubkey_b64: str) -> str:
    digest = hashlib.blake2b(unb64(pubkey_b64), digest_size=10).hexdigest().upper()
    return " ".join(digest[i:i + 4] for i in range(0, len(digest), 4))


# ---------------------------------------------------------------------------
# Kalıcı kimlik anahtarı ve anahtar sabitleme
# ---------------------------------------------------------------------------
def load_identity(directory=None) -> PrivateKey:
    directory = directory or KEY_DIR
    path = directory / "identity.key"
    if path.exists():
        return PrivateKey(unb64(path.read_text().strip()))
    directory.mkdir(parents=True, exist_ok=True)
    key = PrivateKey.generate()
    path.write_text(b64(bytes(key)))
    if sys.platform != "win32":
        path.chmod(0o600)
    return key


class KnownKeys:
    def __init__(self, directory=None):
        self.path = (directory or KEY_DIR) / "known_keys.json"
        try:
            self.keys: dict[str, str] = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.keys = {}

    def check(self, uid: str, pubkey: str) -> str:
        """'new' | 'same' | 'changed'"""
        known = self.keys.get(uid)
        if known is None:
            return "new"
        return "same" if known == pubkey else "changed"

    def accept(self, uid: str, pubkey: str) -> None:
        self.keys[uid] = pubkey
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.keys, indent=1), encoding="utf-8")


# ---------------------------------------------------------------------------
# El sıkışma ve oturum (saf fonksiyonlar)
# ---------------------------------------------------------------------------
def make_hello(my_static: PrivateKey, my_uid: str, peer_static: PublicKey, peer_uid: str,
               role: str) -> tuple[bytes, PrivateKey]:
    eph = PrivateKey.generate()
    inner = json.dumps({"v": PROTOCOL, "role": role, "from": my_uid, "to": peer_uid,
                        "eph": b64(bytes(eph.public_key))}).encode()
    sealed = Box(my_static, peer_static).encrypt(inner)
    body = json.dumps({"v": PROTOCOL, "uid": my_uid, "box": b64(bytes(sealed))}).encode()
    return body, eph


def peek_uid(body: bytes) -> str:
    try:
        return str(json.loads(body)["uid"])
    except (ValueError, KeyError, TypeError):
        raise HandshakeError("Geçersiz el sıkışma") from None


def open_hello(body: bytes, my_static: PrivateKey, my_uid: str, peer_static: PublicKey,
               expect_role: str) -> tuple[str, PublicKey]:
    try:
        outer = json.loads(body)
        inner = json.loads(Box(my_static, peer_static).decrypt(unb64(outer["box"])))
    except (ValueError, KeyError, TypeError, CryptoError):
        raise HandshakeError("El sıkışma doğrulanamadı (anahtar uyuşmuyor)") from None
    if inner.get("v") != PROTOCOL:
        raise HandshakeError("Desteklenmeyen sürüm")
    if inner.get("role") != expect_role or inner.get("to") != my_uid or inner.get("from") != outer.get("uid"):
        raise HandshakeError("El sıkışma bilgileri tutarsız")
    return inner["from"], PublicKey(unb64(inner["eph"]))


class Session:
    def __init__(self, my_eph: PrivateKey, peer_eph: PublicKey):
        self.box = Box(my_eph, peer_eph)
        self.send_seq = 0
        self.recv_seq = 0

    def seal(self, payload: dict) -> bytes:
        self.send_seq += 1
        return bytes(self.box.encrypt(json.dumps({**payload, "seq": self.send_seq}).encode()))

    def open(self, data: bytes) -> dict:
        try:
            msg = json.loads(self.box.decrypt(data))
        except (CryptoError, ValueError):
            raise HandshakeError("Mesaj çözülemedi") from None
        if int(msg.get("seq", 0)) <= self.recv_seq:
            raise HandshakeError("Tekrarlanan mesaj reddedildi")
        self.recv_seq = int(msg["seq"])
        return msg


def frame(kind: bytes, body: bytes) -> bytes:
    return struct.pack(">I", len(body) + 1) + kind + body


# ---------------------------------------------------------------------------
# Çevrimiçi kaydı (dr_presence)
# ---------------------------------------------------------------------------
@dataclass
class Peer:
    user_id: str
    name: str
    ip: str
    port: int
    pubkey: str
    app_version: str = ""
    first_seen: float = field(default_factory=time.time)
    key_state: str = "new"          # new | same | changed

    @property
    def fingerprint(self) -> str:
        return fingerprint(self.pubkey)


def advertise_ip() -> str:
    if ENV.CHAT_ADVERTISE_IP:
        return ENV.CHAT_ADVERTISE_IP
    target = ENV.DB_HOST or "10.255.255.255"
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect((socket.gethostbyname(target), ENV.DB_PORT or 5432))   # paket gönderilmez, yalnızca rota
            return s.getsockname()[0]
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return "127.0.0.1"


def sync_presence(user_id: str, ip: str, port: int, pubkey: str) -> list[dict]:
    """Kendi kaydını günceller, diğer çevrimiçi kayıtları döner (arka planda çağrılır)."""
    from scripts import database
    with database.transaction() as conn:
        conn.execute(database.text("""
            INSERT INTO dr_presence (user_id, ip, port, pubkey, app_version, last_seen)
            VALUES (:uid, :ip, :port, :pk, :ver, now())
            ON CONFLICT (user_id) DO UPDATE SET ip = :ip, port = :port, pubkey = :pk, app_version = :ver, last_seen = now()
        """), {"uid": user_id, "ip": ip, "port": port, "pk": pubkey, "ver": __version__})
        return database.rows(conn, f"""
            SELECT p.user_id, COALESCE(u.display_name, '?') AS name, p.ip, p.port, p.pubkey, p.app_version
            FROM dr_presence p LEFT JOIN dr_users u ON u.id = p.user_id
            WHERE p.user_id <> :uid AND p.last_seen > now() - interval '{ONLINE_WINDOW_SEC} seconds'
            ORDER BY name""", {"uid": user_id})


def remove_presence(user_id: str) -> None:
    from scripts import database
    if database.is_ready():
        database.execute("DELETE FROM dr_presence WHERE user_id = :uid", {"uid": user_id})


# ---------------------------------------------------------------------------
# Taşıma (Qt)
# ---------------------------------------------------------------------------
class PeerLink(QObject):
    established = Signal(object)
    message = Signal(object, dict)
    closed = Signal(object)

    def __init__(self, node: "ChatNode", sock: QTcpSocket, initiator: bool, peer_uid: str = ""):
        super().__init__(node)
        self.node, self.sock, self.initiator = node, sock, initiator
        self.peer_uid = peer_uid
        self.state = "handshake"
        self.session: Session | None = None
        self._eph: PrivateKey | None = None
        self._buf = b""
        sock.setParent(self)
        sock.readyRead.connect(self._read)
        sock.disconnected.connect(self.close)
        sock.errorOccurred.connect(self._socket_error)
        # Zamanlayıcı bağlantının çocuğu: bağlantı silinirse zamanlayıcı da silinir
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._handshake_timeout)
        self._timer.start(HANDSHAKE_TIMEOUT_MS)

    def _socket_error(self, _err) -> None:
        self.close()

    def _handshake_timeout(self):
        if self.state == "handshake":
            log.info("Sohbet el sıkışması zaman aşımı (%s)", self.peer_uid or "gelen bağlantı")
            self.close()

    def start_handshake(self, peer: Peer) -> None:
        body, self._eph = make_hello(self.node.identity, self.node.user_id, PublicKey(unb64(peer.pubkey)),
                                     peer.user_id, "init")
        self.sock.write(frame(b"H", body))

    def _read(self):
        self._buf += bytes(self.sock.readAll())
        while len(self._buf) >= 4:
            (length,) = struct.unpack(">I", self._buf[:4])
            if length < 1 or length > MAX_FRAME:
                self.close()
                return
            if len(self._buf) < 4 + length:
                return
            kind, body = self._buf[4:5], self._buf[5:4 + length]
            self._buf = self._buf[4 + length:]
            try:
                self._handle(kind, body)
            except HandshakeError as e:
                log.warning("Sohbet bağlantısı kapatıldı (%s): %s", self.peer_uid or "?", e)
                self.close()
                return

    def _handle(self, kind: bytes, body: bytes) -> None:
        n = self.node
        if self.state == "handshake" and not self.initiator and kind == b"H":
            uid = peek_uid(body)
            peer = n.peer(uid)
            if peer is None:
                n.peer_unknown.emit()          # yeni gelmiş olabilir: çevrimiçi listesini hemen yenile
                raise HandshakeError("Tanınmayan kişi (çevrimiçi listesinde henüz yok)")
            if peer.key_state == "changed":
                raise HandshakeError("Anahtarı değişmiş kişi")
            _, peer_eph = open_hello(body, n.identity, n.user_id, PublicKey(unb64(peer.pubkey)), "init")
            reply, self._eph = make_hello(n.identity, n.user_id, PublicKey(unb64(peer.pubkey)), uid, "resp")
            self.sock.write(frame(b"R", reply))
            self._open(uid, peer_eph)
        elif self.state == "handshake" and self.initiator and kind == b"R":
            peer = n.peer(self.peer_uid)
            if peer is None or peek_uid(body) != self.peer_uid:
                raise HandshakeError("Beklenmeyen yanıt")
            _, peer_eph = open_hello(body, n.identity, n.user_id, PublicKey(unb64(peer.pubkey)), "resp")
            self._open(self.peer_uid, peer_eph)
        elif self.state == "open" and kind == b"M":
            msg = self.session.open(body)
            if msg.get("type") == "msg":
                self.message.emit(self, msg)
        else:
            raise HandshakeError("Beklenmeyen çerçeve")

    def _open(self, uid: str, peer_eph: PublicKey) -> None:
        self.peer_uid = uid
        self.session = Session(self._eph, peer_eph)
        self._eph = None
        self.state = "open"
        self.established.emit(self)

    def send(self, payload: dict) -> bool:
        if self.state != "open":
            return False
        self.sock.write(frame(b"M", self.session.seal(payload)))
        return True

    def close(self) -> None:
        if self.state == "closed":
            return
        self.state = "closed"
        self._timer.stop()
        for sig, slot in ((self.sock.readyRead, self._read), (self.sock.disconnected, self.close),
                          (self.sock.errorOccurred, self._socket_error)):
            try:
                sig.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        self.sock.abort()
        self.closed.emit(self)
        self.deleteLater()


class ChatNode(QObject):
    """Dinleyen sunucu + eş bağlantıları. Presence bilgisini set_peers ile alır."""
    peers_changed = Signal()
    message_received = Signal(dict)       # {"room", "from", "name", "text", "ts", "mine"}
    key_warning = Signal(str)             # uid
    peer_unknown = Signal()               # listede olmayan biri bağlanmaya çalıştı

    RETRY_SEC = 10
    GRACE_SEC = 12   # büyük kimlikli taraf, küçük kimlikli tarafın bağlanmasını bu kadar bekler

    def __init__(self, user_id: str, name: str, identity: PrivateKey, known: KnownKeys, parent=None):
        super().__init__(parent)
        self.user_id, self.name, self.identity, self.known = user_id, name, identity, known
        self.pubkey = b64(bytes(identity.public_key))
        self.server = QTcpServer(self)
        self.server.newConnection.connect(self._accept)
        self.port = 0
        self._peers: dict[str, Peer] = {}
        self.links: dict[str, PeerLink] = {}
        self._pending: dict[str, PeerLink] = {}
        self._all: set[PeerLink] = set()       # el sıkışması bitmemiş gelen bağlantılar dahil
        self._last_attempt: dict[str, float] = {}
        self.listen_error = ""
        # Eksik bağlantıları presence turunu beklemeden dene (bekleme süresi dolan eşler için)
        self._retry = QTimer(self)
        self._retry.setInterval(4000)
        self._retry.timeout.connect(self._connect_missing)

    # ---- yaşam döngüsü
    def start(self, port: int, address=QHostAddress.AnyIPv4) -> int:
        for p in [port + i for i in range(10)] + [0]:
            if self.server.listen(QHostAddress(address), p):
                self.port = self.server.serverPort()
                self._retry.start()
                return self.port
        self.listen_error = self.server.errorString()
        log.warning("Sohbet portu açılamadı: %s", self.listen_error)
        return 0

    def stop(self) -> None:
        self._retry.stop()
        for link in list(self._all):
            link.close()
        self.links.clear()
        self._pending.clear()
        self.server.close()

    # ---- eşler
    def peer(self, uid: str) -> Peer | None:
        return self._peers.get(uid)

    def peers(self) -> list[Peer]:
        return sorted(self._peers.values(), key=lambda p: p.name.lower())

    def is_online(self, uid: str) -> bool:
        return uid in self.links

    def online_count(self) -> int:
        return len(self.links)

    def set_peers(self, rows: list[dict]) -> None:
        seen = set()
        for r in rows:
            uid = r["user_id"]
            seen.add(uid)
            old = self._peers.get(uid)
            peer = Peer(uid, r["name"], r["ip"], int(r["port"]), r["pubkey"], r.get("app_version") or "",
                        old.first_seen if old else time.time())
            peer.key_state = self.known.check(uid, peer.pubkey)
            if peer.key_state == "new":
                self.known.accept(uid, peer.pubkey)
                peer.key_state = "same"
            elif peer.key_state == "changed" and (not old or old.key_state != "changed"):
                self.key_warning.emit(uid)
                if uid in self.links:
                    self.links[uid].close()
            self._peers[uid] = peer
        for uid in list(self._peers):
            if uid not in seen:
                del self._peers[uid]
                if uid in self.links:
                    self.links[uid].close()
        self._connect_missing()
        self.peers_changed.emit()

    def accept_new_key(self, uid: str) -> None:
        peer = self._peers.get(uid)
        if peer:
            self.known.accept(uid, peer.pubkey)
            peer.key_state = "same"
            self._last_attempt.pop(uid, None)
            self._connect_missing()
            self.peers_changed.emit()

    def _connect_missing(self) -> None:
        now_ = time.time()
        for uid, peer in self._peers.items():
            if uid in self.links or uid in self._pending or peer.key_state == "changed":
                continue
            if now_ - self._last_attempt.get(uid, 0) < self.RETRY_SEC:
                continue
            if self.user_id > uid and now_ - peer.first_seen < self.GRACE_SEC:
                continue
            self._last_attempt[uid] = now_
            self._dial(peer)

    def _dial(self, peer: Peer) -> None:
        sock = QTcpSocket()
        link = PeerLink(self, sock, initiator=True, peer_uid=peer.user_id)
        self._all.add(link)
        self._pending[peer.user_id] = link
        link.established.connect(self._on_established)
        link.closed.connect(self._on_closed)
        link.message.connect(self._on_message)
        sock.connected.connect(lambda: link.state == "handshake" and link.start_handshake(peer))
        sock.connectToHost(peer.ip, peer.port)

    def _accept(self) -> None:
        while self.server.hasPendingConnections():
            sock = self.server.nextPendingConnection()
            link = PeerLink(self, sock, initiator=False)
            self._all.add(link)
            link.established.connect(self._on_established)
            link.closed.connect(self._on_closed)
            link.message.connect(self._on_message)

    def _on_established(self, link: PeerLink) -> None:
        uid = link.peer_uid
        self._pending.pop(uid, None)
        existing = self.links.get(uid)
        if existing and existing is not link:
            # Aynı kişiye iki bağlantı: iki taraf da küçük kimlikli tarafın başlattığını tutar
            preferred_initiator = min(self.user_id, uid)
            link_initiator = self.user_id if link.initiator else uid
            if link_initiator != preferred_initiator:
                link.close()
                return
            existing.closed.disconnect(self._on_closed)
            self._all.discard(existing)
            existing.close()
        self.links[uid] = link
        self.peers_changed.emit()

    def _on_closed(self, link: PeerLink) -> None:
        self._all.discard(link)
        uid = link.peer_uid
        if self._pending.get(uid) is link:
            del self._pending[uid]
        if self.links.get(uid) is link:
            del self.links[uid]
            self._last_attempt.pop(uid, None)
            self.peers_changed.emit()

    def _on_message(self, link: PeerLink, msg: dict) -> None:
        peer = self._peers.get(link.peer_uid)
        room = GENERAL if msg.get("room") == GENERAL else link.peer_uid
        self.message_received.emit({
            "room": room, "from": link.peer_uid, "name": peer.name if peer else msg.get("name", "?"),
            "text": str(msg.get("text", ""))[:MAX_TEXT], "ts": time.time(), "mine": False})

    # ---- gönderme
    def send(self, room: str, text: str) -> list[str]:
        """Mesajı gönderir; ulaştırılamayan kişilerin adlarını döner."""
        text = text.strip()[:MAX_TEXT]
        if not text:
            return []
        payload = {"type": "msg", "room": GENERAL if room == GENERAL else "direct", "text": text, "name": self.name}
        failed = []
        targets = list(self.links.keys()) if room == GENERAL else [room]
        for uid in targets:
            link = self.links.get(uid)
            if not link or not link.send(payload):
                peer = self._peers.get(uid)
                failed.append(peer.name if peer else uid)
        return failed


# ---------------------------------------------------------------------------
# Servis: presence döngüsü + düğüm + oturum geçmişi
# ---------------------------------------------------------------------------
class ChatService(QObject):
    changed = Signal()                     # kişi listesi / durum
    message = Signal(dict)                 # yeni mesaj (gelen veya giden)
    key_warning = Signal(str, str)         # uid, ad

    PRESENCE_MS = 20_000

    def __init__(self, user_id: str, name: str, parent=None):
        super().__init__(parent)
        from scripts.worker import Poller
        self.user_id, self.name = user_id, name
        self.node = ChatNode(user_id, name, load_identity(), KnownKeys(), self)
        self.node.peers_changed.connect(self.changed)
        self.node.peer_unknown.connect(self.refresh)
        self.node.message_received.connect(self._incoming)
        self.node.key_warning.connect(lambda uid: self.key_warning.emit(uid, (self.node.peer(uid) or Peer(uid, uid, "", 0, "")).name))
        self.history: dict[str, list[dict]] = {GENERAL: []}
        self.unread: dict[str, int] = {}
        self.ip = ""
        self.error = ""
        self.running = False
        self._poller = Poller(self._sync, self.PRESENCE_MS, on_done=self._synced, on_error=self._sync_failed, parent=self)
        self._early = QTimer(self)          # açılıştan kısa süre sonra bir tur daha: eşler hızlı buluşsun
        self._early.setSingleShot(True)
        self._early.setInterval(5000)
        self._early.timeout.connect(self.refresh)

    def start(self) -> None:
        from scripts import database
        if self.running:
            return
        if not database.is_ready():
            self.error = "Veritabanı bağlantısı yok: çevrimiçi kişiler bulunamıyor."
            self.changed.emit()
            return
        if not self.node.start(ENV.CHAT_PORT):
            self.error = f"Dinleme portu açılamadı: {self.node.listen_error}"
            self.changed.emit()
            return
        self.ip = advertise_ip()
        self.running = True
        self.error = ""
        self._poller.start()
        self._early.start()
        self.changed.emit()

    def stop(self) -> None:
        if not self.running:
            return
        self.running = False
        self._poller.stop()
        self.node.stop()
        try:
            remove_presence(self.user_id)
        except Exception:   # noqa: BLE001
            log.warning("Çevrimiçi kaydı silinemedi", exc_info=True)

    def _sync(self):
        return sync_presence(self.user_id, self.ip, self.node.port, self.node.pubkey)

    def _synced(self, rows):
        if self.error.startswith("Çevrimiçi"):
            self.error = ""
        self.node.set_peers(rows)

    def _sync_failed(self, e):
        self.error = f"Çevrimiçi listesi alınamadı: {type(e).__name__}"
        self.node.set_peers([])

    def refresh(self) -> None:
        if self.running:
            self._poller.trigger()

    def _incoming(self, msg: dict) -> None:
        self.history.setdefault(msg["room"], []).append(msg)
        self.unread[msg["room"]] = self.unread.get(msg["room"], 0) + 1
        self.message.emit(msg)

    def send(self, room: str, text: str) -> list[str]:
        failed = self.node.send(room, text)
        targets = len(self.node.links) if room == GENERAL else 1
        if targets and len(failed) < targets:
            msg = {"room": room, "from": self.user_id, "name": self.name, "text": text.strip()[:MAX_TEXT],
                   "ts": time.time(), "mine": True}
            self.history.setdefault(room, []).append(msg)
            self.message.emit(msg)
        return failed

    def mark_read(self, room: str) -> None:
        self.unread.pop(room, None)
