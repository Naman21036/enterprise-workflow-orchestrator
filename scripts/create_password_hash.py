"""Securely configure the initial APEX admin in the local ignored .env file."""

from getpass import getpass
from pathlib import Path
import os
import re
import tempfile

from backend.app.security.auth import hash_password


def main() -> None:
    username = input("Bootstrap administrator username [admin]: ").strip() or "admin"
    if not re.fullmatch(r"[A-Za-z0-9_.@-]{3,160}", username):
        raise SystemExit("Username must be 3-160 characters using letters, numbers, ., _, @, or -")

    first = getpass("Bootstrap administrator password (12+ characters; input hidden): ")
    second = getpass("Confirm password: ")
    if first != second:
        raise SystemExit("Passwords did not match")
    try:
        password_hash = hash_password(first)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    env_path = Path.cwd() / ".env"
    original = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    managed = {
        "APEX_BOOTSTRAP_ADMIN_USERNAME": username.lower(),
        "APEX_BOOTSTRAP_ADMIN_PASSWORD_HASH": password_hash,
    }
    output: list[str] = []
    written: set[str] = set()
    for line in original.splitlines():
        match = re.match(r"^\s*(APEX_BOOTSTRAP_ADMIN_USERNAME|APEX_BOOTSTRAP_ADMIN_PASSWORD_HASH)\s*=", line)
        if match:
            key = match.group(1)
            if key not in written:
                output.append(f"{key}={managed[key]}")
                written.add(key)
        else:
            output.append(line)
    for key, value in managed.items():
        if key not in written:
            output.append(f"{key}={value}")

    env_path.parent.mkdir(parents=True, exist_ok=True)
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n", dir=env_path.parent, delete=False) as temp:
            temp_name = temp.name
            temp.write("\n".join(output).rstrip("\n") + "\n")
            temp.flush()
            os.fsync(temp.fileno())
        os.replace(temp_name, env_path)
    finally:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)
    print("Bootstrap administrator settings were written to .env. Password and hash were not displayed.")


if __name__ == "__main__":
    main()
