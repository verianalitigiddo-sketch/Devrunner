"""Sohbet: el sıkışma kriptosu ve tek süreçte iki eşin loopback üzerinden mesajlaşması."""
import time

import pytest
from nacl.public import PrivateKey
from PySide6.QtNetwork import QHostAddress

from scripts import ChatAPI as C


def test_handshake_roundtrip_and_tamper():
    a, b = PrivateKey.generate(), PrivateKey.generate()
    hello, a_eph = C.make_hello(a, "A", b.public_key, "B", "init")
    uid, a_eph_pub = C.open_hello(hello, b, "B", a.public_key, "init")
    assert uid == "A" and bytes(a_eph_pub) == bytes(a_eph.public_key)
    # yanlış rol / yanlış alıcı / başka anahtar reddedilir
    with pytest.raises(C.HandshakeError):
        C.open_hello(hello, b, "B", a.public_key, "resp")
    with pytest.raises(C.HandshakeError):
        C.open_hello(hello, b, "X", a.public_key, "init")
    with pytest.raises(C.HandshakeError):
        C.open_hello(hello, b, "B", PrivateKey.generate().public_key, "init")


def test_session_rejects_replay():
    a, b = PrivateKey.generate(), PrivateKey.generate()
    sa, sb = C.Session(a, b.public_key), C.Session(b, a.public_key)
    m1 = sa.seal({"type": "msg", "text": "merhaba"})
    assert b"merhaba" not in m1                                  # düz metin yok
    assert sb.open(m1)["text"] == "merhaba"
    with pytest.raises(C.HandshakeError):
        sb.open(m1)


def test_known_keys_pinning(tmp_path):
    k = C.KnownKeys(tmp_path)
    assert k.check("u", "pk1") == "new"
    k.accept("u", "pk1")
    assert C.KnownKeys(tmp_path).check("u", "pk1") == "same"
    assert C.KnownKeys(tmp_path).check("u", "pk2") == "changed"


def wait_until(qtbot, cond, timeout=8000):
    qtbot.waitUntil(lambda: bool(cond()), timeout=timeout)


@pytest.fixture
def make_node(tmp_path):
    """Düğümler test sonunda arayüz iş parçacığında silinir (çöp toplayıcı başka iş parçacığında silmesin)."""
    import shiboken6
    nodes = []

    def make(uid, name):
        node = _make_node(tmp_path, uid, name)
        nodes.append(node)
        return node

    yield make
    for n in nodes:
        n.stop()
        shiboken6.delete(n)


def _make_node(tmp_path, uid, name):
    d = tmp_path / uid
    node = C.ChatNode(uid, name, C.load_identity(d), C.KnownKeys(d))
    node.GRACE_SEC = 0
    assert node.start(0, QHostAddress.LocalHost)
    return node


def row(node, name):
    return {"user_id": node.user_id, "name": name, "ip": "127.0.0.1", "port": node.port, "pubkey": node.pubkey}


def test_two_peers_exchange_encrypted_messages(qtbot, make_node):
    a = make_node("aaaa", "Ayşe")
    b = make_node("bbbb", "Mehmet")
    got_a, got_b = [], []
    a.message_received.connect(got_a.append)
    b.message_received.connect(got_b.append)
    a.set_peers([row(b, "Mehmet")])
    b.set_peers([row(a, "Ayşe")])
    wait_until(qtbot, lambda: a.is_online("bbbb") and b.is_online("aaaa"))
    assert a.online_count() == 1 and b.online_count() == 1     # tek bağlantıda birleşir

    assert a.send("bbbb", "selam Mehmet") == []
    assert b.send(C.GENERAL, "herkese merhaba") == []
    wait_until(qtbot, lambda: got_b and got_a)
    assert got_b[0]["text"] == "selam Mehmet" and got_b[0]["room"] == "aaaa" and got_b[0]["name"] == "Ayşe"
    assert got_a[0]["room"] == C.GENERAL and got_a[0]["text"] == "herkese merhaba"
    a.stop()
    wait_until(qtbot, lambda: not b.is_online("aaaa"))
    b.stop()


def test_changed_key_blocks_connection(qtbot, make_node):
    a = make_node("aaaa", "Ayşe")
    b = make_node("bbbb", "Mehmet")
    a.known.accept("bbbb", C.b64(bytes(PrivateKey.generate().public_key)))   # önceden başka anahtar görülmüş
    warnings = []
    a.key_warning.connect(warnings.append)
    a.set_peers([row(b, "Mehmet")])
    b.set_peers([row(a, "Ayşe")])
    qtbot.wait(1500)
    assert warnings == ["bbbb"]
    assert not a.is_online("bbbb") and not b.is_online("aaaa")
    a.accept_new_key("bbbb")
    wait_until(qtbot, lambda: a.is_online("bbbb"))
    a.stop()
    b.stop()


def test_fingerprint_format():
    fp = C.fingerprint(C.b64(bytes(PrivateKey.generate().public_key)))
    assert len(fp.split()) == 5 and all(len(p) == 4 for p in fp.split())
    time.sleep(0)
