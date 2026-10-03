import json
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

import chatroom_db as db
import make_cert
import tls

REPO = Path(__file__).resolve().parent.parent
HOST = "127.0.0.1"


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "chat.db"
    monkeypatch.setattr(db, "DB_PATH", str(path))
    db.init_db()
    return path


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def cert(tmp_path_factory):
    folder = tmp_path_factory.mktemp("cert")
    cert, key = folder / "cert.pem", folder / "key.pem"
    make_cert.make_cert(cert, key)
    return cert, key


@pytest.fixture
def server(tmp_path, cert, request):
    """Start server.py on a free port with its own database.

    Extra command line arguments come from @pytest.mark.server_args(...).
    """
    marker = request.node.get_closest_marker("server_args")
    port = free_port()
    proc = subprocess.Popen(
        [sys.executable, "-u", str(REPO / "server.py"), "--port", str(port), "--db", str(tmp_path / "server.db")]
        + ["--cert", str(cert[0]), "--key", str(cert[1])]
        + (list(marker.args) if marker else []),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    context = tls.client_context(cert[0])
    deadline = time.monotonic() + 10
    while True:
        try:
            Client(port, context).close()
            break
        except OSError:
            if time.monotonic() > deadline or proc.poll() is not None:
                proc.kill()
                pytest.fail(f"server did not start: {proc.communicate()[0]}")
            time.sleep(0.05)

    clients = []

    def connect():
        client = Client(port, context)
        clients.append(client)
        return client

    connect.port = port
    connect.context = context
    yield connect
    for client in clients:
        client.close()
    proc.terminate()
    log = proc.communicate(timeout=10)[0]
    assert "Error handling" not in log and "Traceback" not in log, log


class Client:
    def __init__(self, port, context):
        self.sock = context.wrap_socket(socket.create_connection(("127.0.0.1", port), timeout=5), server_hostname=HOST)
        self.buffer = b""
        self.answer_pings = True

    def send(self, type_, /, **fields):
        self.send_raw((json.dumps({"type": type_, **fields}) + "\n").encode())

    def send_raw(self, data):
        self.sock.sendall(data)

    def _next(self, timeout):
        deadline = time.monotonic() + timeout
        while True:
            while b"\n" not in self.buffer:
                left = deadline - time.monotonic()
                if left <= 0:
                    raise TimeoutError
                self.sock.settimeout(left)
                data = self.sock.recv(65536)
                if not data:
                    return None
                self.buffer += data
            line, self.buffer = self.buffer.split(b"\n", 1)
            frame = json.loads(line)
            if not (self.answer_pings and frame["type"] == "ping"):
                return frame
            self.send("pong")

    def expect(self, type_, timeout=15):
        """Return the next frame and check its type."""
        frame = self._next(timeout)
        assert frame is not None, f"connection closed while waiting for {type_}"
        assert frame["type"] == type_, frame
        return frame

    def quiet(self, timeout=0.5):
        """Check that nothing arrives."""
        try:
            frame = self._next(timeout)
        except TimeoutError:
            return
        raise AssertionError(f"unexpected frame: {frame}")

    def wait_closed(self, timeout=15):
        """Read frames until the server closes the connection."""
        frames = []
        try:
            while (frame := self._next(timeout)) is not None:
                frames.append(frame)
        except ConnectionError:
            pass
        return frames

    def register(self, username, password="password1"):
        self.send("signup", username=username, password=password)
        assert self.expect("signup_result")["ok"]
        self.send("login", username=username, password=password)
        assert self.expect("login_result")["ok"]
        self.expect("friends")
        self.expect("pending")
        return self

    def close(self):
        self.sock.close()
