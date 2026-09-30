# Socket Chat

[![CI](https://github.com/ABuljko/Socket-Chat/actions/workflows/ci.yml/badge.svg)](https://github.com/ABuljko/Socket-Chat/actions/workflows/ci.yml)

A simple chat app written in Python. Users sign up, add friends and send each other messages.

## Requirements

- Python 3.12 or newer

## How to run

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
- Messages are not encrypted. Use it only on a network you trust.

## For developers

Install [uv](https://docs.astral.sh/uv/), then run the checks:

```
uv sync
uv run ruff check .
uv run pytest
```

## Contributors

Amin Niaziardekani, Swapnaneel Sarkhel, Ajdin Buljko
