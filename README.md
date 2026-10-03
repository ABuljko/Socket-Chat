# Socket Chat

[![CI](https://github.com/ABuljko/Socket-Chat/actions/workflows/ci.yml/badge.svg)](https://github.com/ABuljko/Socket-Chat/actions/workflows/ci.yml)

A simple chat app written in Python. Users sign up, add friends and send each other messages.

## Requirements

- Python 3.12 or newer
- OpenSSL, to make the certificate (Git for Windows includes it)

## How to run

Make a certificate (only once):

```
python make_cert.py
```

Start the server:

```
python server.py
```

Start the app (once for each user):

```
python main.py
```

## How to use

1. **File > Sign up** to create an account.
2. **Friends > Find User** to send a friend request.
3. Your friend accepts it in **Friends > Pending Requests**.
4. Click your friend's name on the left and start chatting.

## Good to know

- To start over with an empty database, run `python initialize_db.py`.
- After 5 failed logins, each retry waits longer, up to a minute.
- The connection is encrypted with TLS. The app only talks to a server whose certificate matches `cert.pem`.
- To chat across machines, run `python make_cert.py --host <server address>`, start the server with `--host 0.0.0.0`, and give each user a copy of `cert.pem` (never `key.pem`). They start the app with `--host <server address>`.

## For developers

Install [uv](https://docs.astral.sh/uv/), then run the checks:

```
uv sync
uv run ruff check .
uv run pytest
```

## Contributors

Amin Niaziardekani, Swapnaneel Sarkhel, Ajdin Buljko
