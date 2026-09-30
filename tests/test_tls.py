import json
import socket
import ssl
import threading
import time

import pytest
from conftest import HOST, Client

import make_cert
import server as chat_server
import tls


def frame(type_, /, **fields):
    return (json.dumps({"type": type_, **fields}) + "\n").encode()


def connect_locked(server):
    sock = socket.create_connection((HOST, server.port), timeout=5)
    return tls.LockedSocket(server.context.wrap_socket(sock, server_hostname=HOST))


def test_plain_tcp_client_is_dropped(server):
    with socket.create_connection((HOST, server.port), timeout=15) as raw:
        raw.sendall(frame("signup", username="alice", password="password1"))
        received = b""
        try:
            while data := raw.recv(4096):
                received += data
        except ConnectionError:
            pass
    assert b"signup_result" not in received
    server().register("alice")


def test_client_that_never_handshakes_does_not_block_others(server):
    with socket.create_connection((HOST, server.port)):
        start = time.monotonic()
        server().register("alice")
        assert time.monotonic() - start < tls.HANDSHAKE_TIMEOUT / 2


def test_client_rejects_unknown_certificate(server, tmp_path):
    other = tmp_path / "other.pem"
    make_cert.make_cert(other, tmp_path / "other-key.pem")
    with pytest.raises(ssl.SSLCertVerificationError):
        Client(server.port, tls.client_context(other))


def test_client_rejects_wrong_hostname(server):
    sock = socket.create_connection((HOST, server.port), timeout=5)
    with pytest.raises(ssl.SSLCertVerificationError):
        server.context.wrap_socket(sock, server_hostname="example.com")


def test_shutdown_wakes_waiting_reader(server):
    sock = connect_locked(server)
    reader = sock.makefile()
    result = []
    thread = threading.Thread(target=lambda: result.append(reader.readline()))
    thread.start()
    time.sleep(0.2)
    sock.shutdown()
    thread.join(tls.POLL_INTERVAL + 5)
    assert not thread.is_alive() and result == [""]
    sock.close()


def test_connection_sends_while_receiving(cert, monkeypatch):
    clients, count = 16, 3000
    text = "x" * 1500
    monkeypatch.setattr(chat_server, "MAX_QUEUED", 3 * count)
    server_context = tls.server_context(*cert)
    client_context = tls.client_context(cert[0])
    listener = socket.create_server((HOST, 0))
    port = listener.getsockname()[1]
    errors = []

    def serve(raw):
        try:
            conn = chat_server.Connection(tls.LockedSocket(server_context.wrap_socket(raw, server_side=True)))
        except OSError as e:
            errors.append(f"server handshake: {e!r}")
            return
        pusher = threading.Thread(target=lambda: [conn.send("push", seq=i, text=text) for i in range(count)])
        pusher.start()
        try:
            while (message := conn.receive()) is not None:
                conn.send("echo", seq=message["seq"], text=message["text"])
        except Exception as e:
            errors.append(f"server: {e!r}")
        pusher.join()
        conn.close()

    def accept():
        for _ in range(clients):
            threading.Thread(target=serve, args=(listener.accept()[0],), daemon=True).start()

    def client():
        sock = tls.LockedSocket(
            client_context.wrap_socket(socket.create_connection((HOST, port), timeout=5), server_hostname=HOST)
        )
        got = {"echo": [], "push": []}

        def read():
            try:
                with sock.makefile() as reader:
                    while len(got["echo"]) + len(got["push"]) < 2 * count:
                        message = json.loads(reader.readline())
                        assert message["text"] == text
                        got[message["type"]].append(message["seq"])
            except Exception as e:
                errors.append(f"client read: {e!r}")

        reader = threading.Thread(target=read)
        reader.start()
        try:
            for i in range(count):
                sock.sendall(frame("message", seq=i, text=text))
        except Exception as e:
            errors.append(f"client write: {e!r}")
        reader.join(60)
        sock.close()
        if got != {"echo": list(range(count)), "push": list(range(count))}:
            errors.append("frames missing or out of order")

    threading.Thread(target=accept, daemon=True).start()
    threads = [threading.Thread(target=client) for _ in range(clients)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(90)
    listener.close()
    assert not any(thread.is_alive() for thread in threads), "a client hung"
    assert errors == []


def test_sending_while_receiving(server):
    pairs, count = 4, 150
    padding = "x" * 1900
    names = [(f"a{i}", f"b{i}") for i in range(pairs)]
    for first, second in names:
        a = server().register(first)
        b = server().register(second)
        a.send("friend_request", username=second)
        a.expect("info")
        b.expect("request_received")
        b.send("respond_request", username=first, accept=True)
        b.expect("friend_added")
        a.close()
        b.close()

    users = []
    for first, second in names:
        for name, peer in [(first, second), (second, first)]:
            sock = connect_locked(server)
            reader = sock.makefile()
            sock.sendall(frame("login", username=name, password="password1"))
            while (message := json.loads(reader.readline()))["type"] != "pending":
                if message["type"] == "login_result":
                    assert message["ok"]
            users.append({"name": name, "peer": peer, "sock": sock, "reader": reader, "got": [], "errors": []})

    def read(user):
        try:
            while len(user["got"]) < 2 * count:
                message = json.loads(user["reader"].readline())
                if message["type"] == "message":
                    user["got"].append((message["sender"], message["text"]))
        except Exception as e:
            user["errors"].append(repr(e))

    def write(user):
        try:
            for i in range(count):
                user["sock"].sendall(frame("message", to=user["peer"], text=f"{user['name']} {i} {padding}"))
        except Exception as e:
            user["errors"].append(repr(e))

    threads = [threading.Thread(target=f, args=(user,)) for user in users for f in (read, write)]
    for thread in threads:
        thread.start()
    deadline = time.monotonic() + 120
    for thread in threads:
        thread.join(max(0, deadline - time.monotonic()))
    hung = any(thread.is_alive() for thread in threads)
    for user in users:
        user["sock"].close()
    assert not hung, "a reader or writer hung"

    for user in users:
        assert user["errors"] == []
        for name in (user["name"], user["peer"]):
            texts = [text for sender, text in user["got"] if sender == name]
            assert texts == [f"{name} {i} {padding}" for i in range(count)], (user["name"], name)


@pytest.mark.parametrize("host", ["", "a,DNS:evil.com", "has space", pytest.param("x" * 254, id="too-long"), "a..b"])
def test_make_cert_rejects_bad_hosts(host):
    with pytest.raises(ValueError):
        make_cert.san_entry(host)


def test_make_cert_host_entries():
    assert [make_cert.san_entry(h) for h in ["::1", "10.0.0.5", "chat.example"]] == [
        "IP:::1",
        "IP:10.0.0.5",
        "DNS:chat.example",
    ]
