#!/usr/bin/env python3
"""Studio launcher: relaunch Bambu Studio on a file under /exchange (stdlib only).

GET /health -> {"ok", "studio_running", "mode": "relaunch"}
POST /open {"path"} -> 202 {"pid"} | 400 | 401 | 409 (relaunch in progress)
POST /stop -> kill Studio
Native single-instance handoff is broken in Studio (D-Bus abort), so every open
kills the running instance (unsaved work is lost) and starts a fresh one.
"""
import json
import os
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BIN = "/opt/bambustudio/bin/bambu-studio"
PATTERN = "^" + BIN
ROOT = "/exchange"
TOKEN = os.environ.get("STUDIO_LAUNCHER_TOKEN", "")
ENV = {
    "PATH": "/usr/local/bin:/usr/bin:/bin", "DISPLAY": ":0", "WAYLAND_DISPLAY": "wayland-0",
    "HOME": "/config", "XDG_RUNTIME_DIR": "/config/.XDG",
    "LD_LIBRARY_PATH": "/opt/bambustudio/bin", "LC_ALL": "C",
}
lock = threading.Lock()


def running() -> bool:
    return subprocess.run(["pgrep", "-f", PATTERN], capture_output=True).returncode == 0


def kill(grace: float = 10.0) -> None:
    subprocess.run(["pkill", "-f", PATTERN])
    end = time.time() + grace
    while running() and time.time() < end:
        time.sleep(0.2)
    if running():
        subprocess.run(["pkill", "-9", "-f", PATTERN])
        end = time.time() + 15
        while running() and time.time() < end:
            time.sleep(0.2)
        if running():
            raise RuntimeError("studio did not exit")


CONF = "/config/.config/BambuStudioBeta/BambuStudio.conf"


def prepare_session(path: str) -> None:
    """Chown the session dir (parent of in/) to abc; point Studio's last dirs at it."""
    d = os.path.dirname(path)
    sess = os.path.dirname(d) if os.path.basename(d) == "in" else d
    if sess == ROOT:
        sess = d
    subprocess.run(["chown", "-R", "abc:abc", sess])
    try:
        with open(CONF) as f:
            c = json.load(f)
        a = c.setdefault("app", {})
        a["last_opened_folder"] = a["last_export_path"] = sess
        with open(CONF, "w") as f:
            json.dump(c, f, indent=4)
        subprocess.run(["chown", "abc:abc", CONF])
    except (OSError, ValueError) as e:
        print(f"conf rewrite skipped: {e}", flush=True)


def relaunch(path: str) -> int:
    kill()  # Studio must be dead first: it rewrites the conf on exit
    prepare_session(path)
    p = subprocess.Popen(
        ["s6-setuidgid", "abc", "nohup", BIN, path], env=ENV, cwd="/config",
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    print(f"launched studio pid={p.pid} path={path}", flush=True)
    return p.pid


class H(BaseHTTPRequestHandler):
    def log_message(self, fmt, *a):
        print(f"{self.address_string()} {fmt % a}", flush=True)

    def _send(self, code: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/health":
            self._send(200, {"ok": True, "studio_running": running(), "mode": "relaunch"})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        if TOKEN and self.headers.get("X-Launcher-Token") != TOKEN:
            return self._send(401, {"error": "bad token"})
        if self.path == "/stop":
            if not lock.acquire(blocking=False):
                return self._send(409, {"error": "relaunch in progress"})
            try:
                kill()
            finally:
                lock.release()
            return self._send(200, {"ok": True})
        if self.path != "/open":
            return self._send(404, {"error": "not found"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            path = json.loads(self.rfile.read(n) or b"{}").get("path")
        except (ValueError, AttributeError):
            return self._send(400, {"error": "bad json"})
        real = os.path.realpath(path) if isinstance(path, str) else ""
        if not real.startswith(ROOT + "/") or not os.path.isfile(real):
            return self._send(400, {"error": "path must be an existing file under /exchange"})
        if not lock.acquire(blocking=False):
            return self._send(409, {"error": "relaunch in progress"})
        out: dict = {}

        def work():
            try:
                out["pid"] = relaunch(real)
            except Exception as e:  # noqa: BLE001
                out["error"] = str(e)
                print(f"relaunch failed: {e}", flush=True)
            finally:
                lock.release()

        t = threading.Thread(target=work, daemon=True)
        t.start()
        t.join(timeout=1.0)  # report the pid when the old instance dies quickly
        if "error" in out:
            return self._send(500, {"error": out["error"]})
        self._send(202, {"pid": out.get("pid")})


if __name__ == "__main__":
    print("launcher listening on :3010", flush=True)
    ThreadingHTTPServer(("0.0.0.0", 3010), H).serve_forever()
