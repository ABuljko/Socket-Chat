import argparse
import logging
import tkinter as tk
from pathlib import Path

from chat_client.app import ChatApp
from chat_client.network import ServerConnection
from chat_client.ui import MainWindow

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5557
DEFAULT_CAFILE = Path(__file__).with_name("cert.pem")


def main():
    parser = argparse.ArgumentParser(description="Run the chat client.")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--cafile", default=DEFAULT_CAFILE, help="server certificate to trust (default: cert.pem next to this script)"
    )
    args = parser.parse_args()
    logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s")

    root = tk.Tk()
    app = ChatApp(
        lambda app: MainWindow(root, app),
        lambda events: ServerConnection(args.host, args.port, args.cafile, events),
    )
    app.start()
    root.mainloop()


if __name__ == "__main__":
    main()
