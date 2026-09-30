import json


def friends(server, first="alice", second="bob"):
    a = server().register(first)
    b = server().register(second)
    a.send("friend_request", username=second)
    a.expect("info")
    b.expect("request_received")
    b.send("respond_request", username=first, accept=True)
    b.expect("friend_added")
    b.expect("pending")
    a.expect("friend_added")
    return a, b


def test_signup_then_login_on_same_connection(server):
    c = server()
    c.send("signup", username="alice", password="password1")
    assert c.expect("signup_result") == {"type": "signup_result", "ok": True}
    c.send("signup", username="alice", password="password1")
    assert c.expect("signup_result")["ok"] is False
    c.send("login", username="alice", password="wrong-password")
    assert c.expect("login_result")["ok"] is False
    c.send("login", username="alice", password="password1")
    assert c.expect("login_result") == {"type": "login_result", "ok": True, "username": "alice"}
    assert c.expect("friends")["users"] == []
    assert c.expect("pending")["users"] == []


def test_signup_validation(server):
    c = server()
    for username, password in [
        ("alice", "short"),
        ("bad name", "password1"),
        ("x" * 33, "password1"),
        ("", "password1"),
    ]:
        c.send("signup", username=username, password=password)
        c.expect("error")
    c.send("signup", username="alice")
    assert "password" in c.expect("error")["text"]


def test_guests_cannot_use_chat_commands(server):
    c = server()
    for type_ in ["message", "history", "friend_request", "pending"]:
        c.send(type_, username="bob", to="bob", text="hi")
        assert c.expect("error")["text"] == "Invalid request."


def test_friend_request_accept_and_chat(server):
    a, b = friends(server)
    text = "hello | with pipes\nand a newline"
    a.send("message", to="bob", text=text)
    echoed = a.expect("message")
    delivered = b.expect("message")
    assert echoed == delivered
    assert delivered["sender"] == "alice" and delivered["text"] == text

    b.send("history", username="alice")
    history = b.expect("history")
    assert history["username"] == "alice"
    assert [m["text"] for m in history["messages"]] == [text]


def test_many_messages_arrive_separately_and_in_order(server):
    a, b = friends(server)
    for i in range(50):
        a.send("message", to="bob", text=f"m{i}")
    assert [b.expect("message")["text"] for _ in range(50)] == [f"m{i}" for i in range(50)]


def test_friend_request_rules(server):
    a = server().register("alice")
    b = server().register("bob")
    a.send("friend_request", username="alice")
    a.expect("error")
    a.send("friend_request", username="nobody")
    a.expect("error")
    a.send("respond_request", username="bob", accept=True)
    a.expect("error")
    b.quiet()

    a.send("friend_request", username="bob")
    a.expect("info")
    b.expect("request_received")
    a.send("friend_request", username="bob")
    assert "already" in a.expect("info")["text"]


def test_rejection_is_reported_to_sender(server):
    a = server().register("alice")
    b = server().register("bob")
    a.send("friend_request", username="bob")
    a.expect("info")
    b.expect("request_received")
    b.send("respond_request", username="alice", accept=False)
    assert b.expect("pending")["users"] == []
    assert a.expect("request_rejected")["username"] == "bob"


def test_only_friends_can_chat(server):
    a = server().register("alice")
    server().register("bob")
    a.send("message", to="bob", text="hi")
    a.expect("error")
    a.send("history", username="bob")
    a.expect("error")


def test_message_validation(server):
    a, b = friends(server)
    for text in ["   ", "x" * 2001, 5, None, "bad \ud800"]:
        a.send("message", to="bob", text=text)
        a.expect("error")
    b.quiet()


def test_second_login_replaces_first(server):
    a, b = friends(server)
    again = server()
    again.send("login", username="alice", password="password1")
    again.expect("login_result")
    assert again.expect("friends")["users"] == ["bob"]
    again.expect("pending")
    frames = a.wait_closed()
    assert frames and frames[0]["type"] == "error"

    b.send("message", to="alice", text="still there?")
    b.expect("message")
    assert again.expect("message")["text"] == "still there?"


def test_invalid_json_closes_connection_with_error(server):
    for payload in [
        b"not json\n",
        b"[1, 2]\n",
        b"\xff\xfe{}\n",
        b'{"n": ' + b"9" * 5000 + b"}\n",
        b"[" * 20000 + b"]" * 20000 + b"\n",
    ]:
        c = server()
        c.send_raw(payload)
        frames = c.wait_closed()
        assert frames == [] or frames[0]["type"] == "error", frames


def test_odd_but_valid_input_keeps_connection(server):
    c = server()
    c.send_raw(b'{"type": ["login"]}\n')
    c.expect("error")
    c.send_raw(b"\n\n")
    c.send_raw((json.dumps({"type": "login", "username": None, "password": []}) + "\n").encode())
    c.expect("error")
    c.send("signup", username="alice", password="password1")
    assert c.expect("signup_result")["ok"]
