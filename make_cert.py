import argparse
import ipaddress
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOSTNAME_RE = re.compile(r"[A-Za-z0-9-]{1,63}(\.[A-Za-z0-9-]{1,63})*")
GIT_OPENSSL = [
    Path(r"C:\Program Files\Git\mingw64\bin\openssl.exe"),
    Path(r"C:\Program Files\Git\usr\bin\openssl.exe"),
]


def find_openssl():
    found = shutil.which("openssl")
    if found:
        return found
    for path in GIT_OPENSSL:
        if path.is_file():
            return str(path)
    raise FileNotFoundError("openssl not found. Install OpenSSL, or Git for Windows which includes it.")


def san_entry(host):
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if len(host) > 253 or not HOSTNAME_RE.fullmatch(host):
            raise ValueError(f"Not a hostname or IP address: {host!r}") from None
        return f"DNS:{host}"
    return f"IP:{host}"


def make_cert(cert, key, hosts=(), days=365):
    hosts = ["localhost", "127.0.0.1", *hosts]
    san = ",".join(san_entry(host) for host in dict.fromkeys(hosts))
    subprocess.run(
        [
            find_openssl(),
            "req",
            "-x509",
            "-newkey",
            "ec",
            "-pkeyopt",
            "ec_paramgen_curve:P-256",
            "-nodes",
            "-days",
            str(days),
            "-subj",
            "/CN=localhost",
            "-addext",
            f"subjectAltName={san}",
            "-keyout",
            str(key),
            "-out",
            str(cert),
        ],
        check=True,
        capture_output=True,
    )


def main():
    parser = argparse.ArgumentParser(description="Make cert.pem and key.pem for the chat server.")
    parser.add_argument(
        "--host",
        action="append",
        default=[],
        help="extra hostname or IP address the server is reached at (repeatable)",
    )
    parser.add_argument("--days", type=int, default=365)
    args = parser.parse_args()
    cert, key = HERE / "cert.pem", HERE / "key.pem"
    try:
        make_cert(cert, key, args.host, args.days)
    except (FileNotFoundError, ValueError) as e:
        sys.exit(str(e))
    except subprocess.CalledProcessError as e:
        sys.exit(f"openssl failed:\n{e.stderr.decode(errors='replace')}")
    print(f"Wrote {cert} and {key}.")
    print("Clients on other machines need a copy of cert.pem. Keep key.pem private.")


if __name__ == "__main__":
    main()
