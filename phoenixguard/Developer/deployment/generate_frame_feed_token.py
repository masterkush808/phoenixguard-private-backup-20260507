from __future__ import annotations

import argparse
import hashlib
import secrets


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a PhoenixGuard frame-ingest token and optional registry hash.")
    parser.add_argument("--name", default="feed-token", help="Friendly token name for operator notes.")
    parser.add_argument("--hash-only", action="store_true", help="Print only the sha256 hash.")
    args = parser.parse_args()

    token = secrets.token_urlsafe(36)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    if args.hash_only:
        print(token_hash)
        return 0
    print(f"name={args.name}")
    print(f"token={token}")
    print(f"token_sha256={token_hash}")
    print("Store the token in a secret manager or VPS env file. Commit only token_sha256 or token_env references.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
