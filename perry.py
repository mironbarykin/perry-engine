"""A temporary terminal communicator for Perry Engine.

Run with ``poetry run python perry.py``.  It starts a local Engine if one is
not already listening, connects the primary user to Calendar on demand, and
forwards terminal messages through the same HTTP API a Telegram bot will use.
"""

import json
import os
import threading
import time
import urllib.error
import urllib.request
import webbrowser

import uvicorn

from app.config import settings
from app.main import app

HOST = "127.0.0.1"
PORT = int(os.environ.get("PERRY_PORT", "8000"))
BASE_URL = f"http://{HOST}:{PORT}"


def api_request(path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=data,
        method="POST" if body is not None else "GET",
        headers={
            "Content-Type": "application/json",
            "X-API-Key": settings.access_api_key,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise RuntimeError(f"Engine returned {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Cannot reach Perry Engine at {BASE_URL}: {exc.reason}") from exc


def start_engine_if_needed() -> uvicorn.Server | None:
    try:
        api_request("/health")
        return None
    except RuntimeError:
        pass

    server = uvicorn.Server(
        uvicorn.Config(app, host=HOST, port=PORT, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(50):
        try:
            api_request("/health")
            return server
        except RuntimeError:
            time.sleep(0.1)
    raise RuntimeError("Perry Engine did not start within five seconds")


def connect_calendar() -> None:
    response = api_request(
        "/integrations/google/calendar/authorize", {"user_id": "p1"}
    )
    url = response["authorization_url"]
    print("\nOpening Google consent in your browser for p1…")
    print("If it does not open, visit this URL:\n", url)
    webbrowser.open(url)
    input("\nApprove Calendar access, wait for the connected page, then press Enter here.")


def confirm_actions(confirmation_ids: list[str]) -> None:
    for confirmation_id in confirmation_ids:
        response = api_request(f"/confirmations/{confirmation_id}", {"user_id": "p1"})
        print(f"Confirmed: Calendar event {response.get('event_id', '')} was created or updated.")


def chat_loop() -> None:
    print("Perry terminal communicator. Type /connect-calendar, /confirm <id>, or /quit.\n")
    while True:
        try:
            message = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not message:
            continue
        if message == "/quit":
            return
        if message == "/connect-calendar":
            try:
                connect_calendar()
            except RuntimeError as exc:
                print(f"Calendar connection failed: {exc}")
            continue
        if message.startswith("/confirm "):
            try:
                confirm_actions(message.removeprefix("/confirm ").split())
            except RuntimeError as exc:
                print(f"Confirmation failed: {exc}\n")
            continue
        try:
            response = api_request("/chat", {"user_id": "p1", "message": message})
            print(f"Perry: {response['reply']}\n")
            confirmations = response.get("pending_confirmations", [])
            if confirmations:
                ids = [item["id"] for item in confirmations]
                answer = input(f"Confirm {len(ids)} proposed Calendar action(s)? [y/N]: ").strip().lower()
                if answer in {"y", "yes"}:
                    confirm_actions(ids)
                else:
                    print("No Calendar changes were made. They expire after ten minutes.\n")
        except RuntimeError as exc:
            print(f"Perry error: {exc}\n")


def main() -> None:
    server = start_engine_if_needed()
    try:
        chat_loop()
    finally:
        if server is not None:
            server.should_exit = True


if __name__ == "__main__":
    main()
