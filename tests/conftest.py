import json
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

import chatroom_db as db

REPO = Path(__file__).resolve().parent.parent


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


@pytest.fixture
def server(tmp_path):
    """Start server.py on a free port with its own database."""
    port = free_port()
    proc = subprocess.Popen(
        [sys.executable, "-u", str(REPO / "server.py"), "--port", str(port), "--db", str(tmp_path / "server.db")],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    deadline = time.monotonic() + 10
    while True:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=1).close()
            break
        except OSError:
            if time.monotonic() > deadline or proc.poll() is not None:
                proc.kill()
                pytest.fail(f"server did not start: {proc.communicate()[0]}")
            time.sleep(0.05)

    clients = []

    def connect():
        client = Client(port)
        clients.append(client)
        return client

    yield connect
    for client in clients:
        client.close()
    proc.terminate()
    log = proc.communicate(timeout=10)[0]
    assert "Error handling" not in log and "Traceback" not in log, log


class Client:
    def __init__(self, port):
        self.sock = socket.create_connection(("127.0.0.1", port))
        self.buffer = b""

    def send(self, type_, /, **fields):
        self.send_raw((json.dumps({"type": type_, **fields}) + "\n").encode())

    def send_raw(self, data):
        self.sock.sendall(data)

    def _next(self, timeout):
        while b"\n" not in self.buffer:
            self.sock.settimeout(timeout)
            data = self.sock.recv(65536)
            if not data:
                return None
            self.buffer += data
        line, self.buffer = self.buffer.split(b"\n", 1)
        return json.loads(line)

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
