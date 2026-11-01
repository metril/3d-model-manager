#!/usr/bin/env python3
"""OrcaSlicer launcher: relaunch OrcaSlicer on a file under /exchange (stdlib only).

GET /health -> {"ok", "orcaslicer_running", "mode": "relaunch", "last_exit", "crashed"}
POST /open {"path"} -> 202 {"pid"} | 400 | 401 | 409 (relaunch in progress)
POST /stop -> kill OrcaSlicer
We do not rely on native single-instance handoff, so every open
kills the running instance (unsaved work is lost) and starts a fresh one.
"""
import json
import os
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

APPRUN = "/opt/orcaslicer/AppRun"  # sets GDK_BACKEND=x11 + runtime libs, then execs BIN
BIN = "/opt/orcaslicer/bin/orca-slicer"
PATTERN = "^" + BIN
ROOT = "/exchange"
TOKEN = os.environ.get("ORCASLICER_LAUNCHER_TOKEN", "")
ENV = {
    "PATH": "/usr/local/bin:/usr/bin:/bin", "DISPLAY": ":0", "WAYLAND_DISPLAY": "wayland-0",
    "HOME": "/config", "XDG_RUNTIME_DIR": "/config/.XDG",
    "LC_ALL": "C",
}
lock = threading.Lock()
# Exit tracking: `last` = {"rc", "at", "stopped"} of the most recent child. kill() records the
# child pid in `stopped_pids` first, so an intentional stop (/stop or relaunch) is never a crash.
state_lock = threading.Lock()
last: dict | None = None
child_pid: int | None = None
stopped_pids: set[int] = set()


def running() -> bool:
    return subprocess.run(["pgrep", "-f", PATTERN], capture_output=True).returncode == 0


def kill(grace: float = 10.0) -> None:
    with state_lock:
        if child_pid is not None:
            stopped_pids.add(child_pid)
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
            raise RuntimeError("orcaslicer did not exit")


CONF = "/config/.config/OrcaSlicer/OrcaSlicer.conf"


def prepare_session(path: str) -> None:
    """Chown the session dir (parent of in/) to abc; point OrcaSlicer's last dirs at it."""
    d = os.path.dirname(path)
    sess = os.path.dirname(d) if os.path.basename(d) == "in" else d
    if sess == ROOT:
        sess = d
    subprocess.run(["chown", "-R", "abc:abc", sess])
    try:
        with open(CONF) as f:
            c = json.load(f)
        for section in ("app", "recent"):
            sec = c.setdefault(section, {})
            sec["last_opened_folder"] = sec["last_export_path"] = sess
        with open(CONF, "w") as f:
            json.dump(c, f, indent=4)
        subprocess.run(["chown", "abc:abc", CONF])
    except (OSError, ValueError) as e:
        print(f"conf rewrite skipped: {e}", flush=True)


def relaunch(path: str) -> int:
    global child_pid
    kill()  # OrcaSlicer must be dead first: it rewrites the conf on exit
    prepare_session(path)
    p = subprocess.Popen(
        ["s6-setuidgid", "abc", "nohup", APPRUN, path], env=ENV, cwd="/config",
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    with state_lock:
        child_pid = p.pid
    threading.Thread(target=watch, args=(p,), daemon=True).start()
    print(f"launched orcaslicer pid={p.pid} path={path}", flush=True)
    return p.pid


def watch(p) -> None:
    """Record how the spawned child exited (a stop by us is flagged via `stopped_pids`)."""
    global last
    rc = p.wait()
    with state_lock:
        last = {"rc": rc, "at": time.time(), "stopped": p.pid in stopped_pids}


def health() -> dict:
    with state_lock:
        snap = dict(last) if last else None
    rc = snap["rc"] if snap else None
    crashed = bool(snap) and rc != 0 and not snap["stopped"]
    return {"ok": True, "orcaslicer_running": running(), "mode": "relaunch", "last_exit": rc, "crashed": crashed}


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
            self._send(200, health())
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
