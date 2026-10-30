"""Desktop launcher for the local 40 CFR Part 1065 chatbot."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path
from urllib.request import urlopen

HOST = "127.0.0.1"
PORT = 8000
URL = f"http://{HOST}:{PORT}"
OLLAMA = "http://127.0.0.1:11434"
DEFAULT_ROOT = Path(r"C:\Cursor_Projects\1065 chat bot")
CREATE_NO_WINDOW = 0x08000000


def find_root() -> Path | None:
    candidates: list[Path] = []
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        candidates.extend([exe_dir, exe_dir.parent])
    else:
        candidates.append(Path(__file__).resolve().parent.parent)
    candidates.append(DEFAULT_ROOT)
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        python = resolved / ".venv" / "Scripts" / "python.exe"
        app = resolved / "app" / "main.py"
        if python.is_file() and app.is_file():
            return resolved
    return None


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.4)
        return sock.connect_ex((HOST, port)) == 0


def http_ok(url: str, timeout: float = 1.5) -> bool:
    try:
        with urlopen(url, timeout=timeout) as response:
            return 200 <= int(response.status) < 500
    except Exception:
        return False


class Launcher:
    def __init__(self) -> None:
        import tkinter as tk
        from tkinter import scrolledtext

        self.tk = tk
        self.root_dir = find_root()
        self.server: subprocess.Popen[str] | None = None
        self.opened_browser = False

        self.win = tk.Tk()
        self.win.title("40 CFR Part 1065 Assistant")
        self.win.geometry("560x420")
        self.win.minsize(480, 360)
        self.win.configure(bg="#12151c")
        self.win.protocol("WM_DELETE_WINDOW", self.quit)

        header = tk.Label(
            self.win,
            text="40 CFR Part 1065 Assistant",
            bg="#12151c",
            fg="#e8edf7",
            font=("Segoe UI", 16, "bold"),
        )
        header.pack(anchor="w", padx=18, pady=(16, 4))

        self.status = tk.Label(
            self.win,
            text="Starting…",
            bg="#12151c",
            fg="#e0b15a",
            font=("Segoe UI", 10),
            wraplength=520,
            justify="left",
        )
        self.status.pack(anchor="w", padx=18, pady=(0, 10))

        buttons = tk.Frame(self.win, bg="#12151c")
        buttons.pack(fill="x", padx=18, pady=(0, 10))

        self.open_btn = tk.Button(
            buttons,
            text="Open chatbot",
            command=self.open_browser,
            bg="#e0b15a",
            fg="#1a1408",
            activebackground="#f0c97a",
            relief="flat",
            font=("Segoe UI", 10, "bold"),
            padx=12,
            pady=6,
        )
        self.open_btn.pack(side="left")

        quit_btn = tk.Button(
            buttons,
            text="Quit",
            command=self.quit,
            bg="#2a3348",
            fg="#e8edf7",
            activebackground="#3a4660",
            relief="flat",
            font=("Segoe UI", 10),
            padx=12,
            pady=6,
        )
        quit_btn.pack(side="left", padx=(8, 0))

        self.log = scrolledtext.ScrolledText(
            self.win,
            height=14,
            bg="#1b2130",
            fg="#c5d0e0",
            insertbackground="#e8edf7",
            relief="flat",
            font=("Consolas", 9),
            wrap="word",
        )
        self.log.pack(fill="both", expand=True, padx=18, pady=(0, 16))
        self.log.configure(state="disabled")

        self.win.after(200, self.startup)

    def set_status(self, text: str) -> None:
        self.status.configure(text=text)

    def write(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text)
        if not text.endswith("\n"):
            self.log.insert("end", "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def open_browser(self) -> None:
        webbrowser.open(URL)

    def startup(self) -> None:
        if self.root_dir is None:
            self.set_status("Could not find the 1065 chatbot project folder.")
            self.write(
                "Place this exe in, or keep, C:\\Cursor_Projects\\1065 chat bot "
                "next to the .venv folder."
            )
            return

        os.chdir(self.root_dir)
        self.write(f"Project: {self.root_dir}")
        python = self.root_dir / ".venv" / "Scripts" / "python.exe"
        index = self.root_dir / "data" / "chroma" / "chunks.json"

        if not http_ok(f"{OLLAMA}/api/tags"):
            self.write("Ollama is not responding on 127.0.0.1:11434. Start Ollama, then ask a question.")
        else:
            self.write("Ollama is running.")

        if not index.is_file():
            self.write("Index not found. From this folder run: python -m app.ingest")

        if port_in_use(PORT) and http_ok(f"{URL}/api/status"):
            self.set_status(f"Already running at {URL}")
            self.write(f"Using existing server at {URL}")
            self.open_browser()
            self.opened_browser = True
            return

        if port_in_use(PORT):
            self.set_status(f"Port {PORT} is in use by another program.")
            self.write(f"Something else is using {HOST}:{PORT}. Close it, or open {URL} if the chatbot is already up.")
            return

        self.set_status("Starting local server…")
        self.write(f"Starting uvicorn with {python}")
        self.server = subprocess.Popen(
            [
                str(python),
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                HOST,
                "--port",
                str(PORT),
            ],
            cwd=str(self.root_dir),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        threading.Thread(target=self._pump_logs, daemon=True).start()
        threading.Thread(target=self._wait_ready, daemon=True).start()

    def _pump_logs(self) -> None:
        if not self.server or not self.server.stdout:
            return
        for line in self.server.stdout:
            self.win.after(0, self.write, line.rstrip())

    def _wait_ready(self) -> None:
        deadline = time.time() + 60
        while time.time() < deadline:
            if self.server and self.server.poll() is not None:
                self.win.after(0, self.set_status, "Server exited before it became ready.")
                return
            if http_ok(f"{URL}/api/status", timeout=2.0):
                self.win.after(0, self.set_status, f"Running at {URL}. Close this window to stop.")
                if not self.opened_browser:
                    self.opened_browser = True
                    self.win.after(0, self.open_browser)
                return
            time.sleep(0.4)
        self.win.after(0, self.set_status, "Timed out waiting for the server. Check the log.")

    def quit(self) -> None:
        if self.server and self.server.poll() is None:
            self.write("Stopping server…")
            self.server.terminate()
            try:
                self.server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.server.kill()
        self.win.destroy()

    def run(self) -> None:
        self.win.mainloop()


def main() -> int:
    Launcher().run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
