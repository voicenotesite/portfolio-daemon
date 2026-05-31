#!/usr/bin/env python3
"""
Portfolio Daemon – manages 5 backend projects.
Health checks, git auto-update, subprocess control, status API.
Cross-platform: Linux (pkill/lsof), Windows (taskkill/netstat), macOS (same as Linux).
"""

import os, sys, json, time, subprocess, threading, signal, platform, logging, uuid
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, Dict, List
from collections import deque

HOME = Path.home()
IS_LINUX = platform.system() == "Linux"
IS_WINDOWS = platform.system() == "Windows"
IS_MACOS = platform.system() == "Darwin"

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("daemon")

CONFIG_PATH = Path(__file__).parent.parent / "config.json"

# ─── Backend Manager ─────────────────────────────────────────────────────────

class BackendProcess:
    def __init__(self, key: str, cfg: dict) -> None:
        self.key = key
        self.name = cfg["name"]
        self.path = Path(cfg["path"])
        self.port = cfg["port"]
        self.git_url = cfg.get("git")
        self.type = cfg.get("type", "fastapi")
        self.entry = cfg.get("entry")
        self.process: Optional[subprocess.Popen] = None
        self.log_buffer: deque = deque(maxlen=200)
        self.status = "unknown"
        self.uptime = 0.0
        self.started_at: Optional[float] = None
        self.git_behind = 0
        self.last_check = 0.0
        self._reader_thread: Optional[threading.Thread] = None

    def is_port_open(self) -> bool:
        if IS_WINDOWS:
            r = subprocess.run(f"netstat -ano | findstr :{self.port}", shell=True, capture_output=True, text=True, timeout=5)
            return bool(r.stdout.strip())
        else:
            r = subprocess.run(["lsof", "-ti", f":{self.port}"], capture_output=True, text=True, timeout=5)
            return bool(r.stdout.strip())

    def pid(self) -> str:
        if IS_WINDOWS:
            r = subprocess.run(f"netstat -ano | findstr :{self.port}", shell=True, capture_output=True, text=True, timeout=5)
            if r.stdout.strip():
                parts = r.stdout.strip().split()
                return parts[-1] if parts else ""
            return ""
        else:
            r = subprocess.run(["lsof", "-ti", f":{self.port}"], capture_output=True, text=True, timeout=5)
            return r.stdout.strip().split("\n")[0] if r.stdout.strip() else ""

    def find_venv_python(self) -> str:
        candidates = [
            f"{self.path}/venv/bin/python3",
            f"{self.path}/.venv/bin/python3",
            f"{self.path}/venv/Scripts/python.exe",
            f"{self.path}/.venv/Scripts/python.exe",
        ]
        for c in candidates:
            if os.path.exists(c):
                return c
        return "python3"

    def start(self) -> bool:
        if self.type == "stub":
            self.status = "stub"
            self.log(f"stub – w implementacji")
            return True

        if self.is_port_open():
            pid = self.pid()
            self.status = "online"
            self.started_at = time.time()
            self.log(f"already running on :{self.port} (PID {pid})")
            return True

        path = self.path
        if not path.exists():
            self.log(f"path not found: {path}")
            self.status = "error"
            return False

        venv_python = self.find_venv_python()
        cmd = f"cd \"{path}\" && \"{venv_python}\" -m uvicorn {self.entry} --host 0.0.0.0 --port {self.port}"

        try:
            self.process = subprocess.Popen(
                cmd, shell=True,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                preexec_fn=os.setsid if not IS_WINDOWS else None,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if IS_WINDOWS else 0,
                bufsize=1, text=True
            )
            self.status = "starting"
            self.started_at = time.time()
            self._reader_thread = threading.Thread(target=self._read_output, daemon=True)
            self._reader_thread.start()
            self.log(f"started on :{self.port}")
            return True
        except Exception as e:
            self.log(f"start failed: {e}")
            self.status = "error"
            return False

    def stop(self) -> bool:
        if self.type == "stub":
            self.status = "stub"
            return True

        pid = self.pid()
        if not pid:
            self.status = "offline"
            self.log("not running")
            return True

        try:
            if IS_WINDOWS:
                subprocess.run(f"taskkill /F /PID {pid}", shell=True, timeout=5)
            else:
                os.kill(int(pid), signal.SIGTERM)
                time.sleep(1)
                if self.is_port_open():
                    os.kill(int(pid), signal.SIGKILL)
        except Exception:
            pass

        if not IS_WINDOWS:
            subprocess.run(["pkill", "-f", f"uvicorn.*app.*:{self.port}"], capture_output=True, timeout=5)

        self.status = "offline"
        self.started_at = None
        self.log(f"stopped (PID {pid})")
        return True

    def restart(self) -> bool:
        self.stop()
        time.sleep(1)
        return self.start()

    def check_git(self) -> int:
        if not self.git_url or self.type == "stub":
            return 0
        path = self.path
        if not (path / ".git").exists():
            if IS_WINDOWS:
                subprocess.run(f"cd /d \"{path}\" && git init && git remote add origin {self.git_url} && git fetch && git checkout -f main", shell=True, capture_output=True, timeout=30)
            else:
                subprocess.run(f"cd \"{path}\" && git init && git remote add origin {self.git_url} && git fetch && git checkout -f main", shell=True, capture_output=True, timeout=30)
            self.log("initialized git repo")
            return 0

        r = subprocess.run(
            f"cd \"{path}\" && git fetch origin main 2>/dev/null; git rev-list --count HEAD..origin/main",
            shell=True, capture_output=True, text=True, timeout=30
        )
        try:
            behind = int(r.stdout.strip())
            self.git_behind = behind
            return behind
        except ValueError:
            return 0

    def git_pull(self) -> bool:
        if not self.git_url or self.type == "stub":
            return True
        path = self.path
        r = subprocess.run(f"cd \"{path}\" && git pull origin main", shell=True, capture_output=True, text=True, timeout=60)
        if r.returncode == 0:
            self.log(f"git pull ok: {r.stdout.strip()[-80:]}")
            return True
        self.log(f"git pull failed: {r.stderr.strip()[-80:]}")
        return False

    def _read_output(self) -> None:
        try:
            for line in iter(self.process.stdout.readline, ""):
                line = line.rstrip()
                self.log_buffer.append(line)
        except Exception:
            pass

    def log(self, msg: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_buffer.append(f"[daemon] {msg}")
        logging.info(f"[{self.key}] {msg}")

    def get_logs(self, n: int = 50) -> List[str]:
        return list(self.log_buffer)[-n:]

    def health_check(self) -> str:
        self.last_check = time.time()
        if self.type == "stub":
            self.status = "stub"
            return self.status

        if not self.is_port_open():
            self.status = "offline"
            if self.started_at:
                self.log(f"went offline, attempting restart...")
                self.start()
            return self.status

        try:
            import urllib.request
            r = urllib.request.urlopen(f"http://localhost:{self.port}/health", timeout=3)
            if r.status == 200:
                self.status = "online"
                if self.started_at:
                    self.uptime = time.time() - self.started_at
            else:
                self.status = "degraded"
        except Exception:
            self.status = "offline"
            if self.started_at:
                self.log(f"health check failed, restarting...")
                self.start()

        return self.status

    def to_dict(self) -> dict:
        now = time.time()
        return {
            "key": self.key,
            "name": self.name,
            "port": self.port,
            "status": self.status,
            "uptime": round(self.uptime, 1) if self.status == "online" else 0,
            "git_behind": self.git_behind,
            "last_check": round(now - self.last_check, 1) if self.last_check else 0,
            "type": self.type,
            "note": "w implementacji" if self.type == "stub" else None
        }

# ─── Tunnel Manager ──────────────────────────────────────────────────────────

CLOUDFLARED = str(Path.home() / ".local" / "bin" / "cloudflared")

class TunnelManager:
    def __init__(self, target_host: str, target_port: int, on_url=None) -> None:
        self.target_host = target_host
        self.target_port = target_port
        self.process: Optional[subprocess.Popen] = None
        self.url: Optional[str] = None
        self._reader_thread: Optional[threading.Thread] = None
        self._on_url = on_url

    def is_running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def start(self) -> bool:
        if self.is_running():
            return True
        if not os.path.exists(CLOUDFLARED):
            log.warning(f"cloudflared not found at {CLOUDFLARED}")
            return False
        try:
            cmd = f"{CLOUDFLARED} tunnel --url http://{self.target_host}:{self.target_port}"
            self.process = subprocess.Popen(
                cmd, shell=True,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                preexec_fn=os.setsid if not IS_WINDOWS else None,
                bufsize=1, text=True
            )
            self._reader_thread = threading.Thread(target=self._read_output, daemon=True)
            self._reader_thread.start()
            log.info("tunnel starting...")
            return True
        except Exception as e:
            log.error(f"tunnel start failed: {e}")
            return False

    def stop(self) -> None:
        if self.is_running():
            pid = self.process.pid
            os.killpg(os.getpgid(pid), signal.SIGTERM) if not IS_WINDOWS else self.process.terminate()
            self.process.wait(timeout=5)
        subprocess.run(["pkill", "-f", "cloudflared"], capture_output=True, timeout=5)
        self.url = None
        log.info("tunnel stopped")

    def _read_output(self) -> None:
        import re
        try:
            for line in iter(self.process.stdout.readline, ""):
                line = line.rstrip()
                log.info(f"[tunnel] {line}")
                m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
                if m:
                    self.url = m.group(0)
                    log.info(f"tunnel URL: {self.url}")
                    if self._on_url:
                        self._on_url(self.url)
        except Exception:
            pass

# ─── Daemon API ──────────────────────────────────────────────────────────────

class DaemonAPI:
    def __init__(self, config: dict) -> None:
        self.config = config
        self.backends: Dict[str, BackendProcess] = {}
        self.start_time = time.time()
        self.push_progress = {"step": "idle", "progress": 0, "status": "", "uptime": 0.0}
        self.backend_tunnels: Dict[str, TunnelManager] = {}
        self.tunnel = TunnelManager(
            config.get("api_host", "127.0.0.1"),
            config.get("api_port", 19876),
            on_url=self.frontend_autoupdate
        )
        self._init_backends()
        self._start_health_loop()
        self._start_git_loop()
        self._lock = threading.Lock()

    def _init_backends(self) -> None:
        for key, cfg in self.config["backends"].items():
            self.backends[key] = BackendProcess(key, cfg)

    def _health_loop(self) -> None:
        while True:
            time.sleep(self.config.get("health_interval", 30))
            for key, bp in self.backends.items():
                try:
                    bp.health_check()
                except Exception as e:
                    log.error(f"health check {key}: {e}")

    def _git_loop(self) -> None:
        while True:
            time.sleep(self.config.get("git_check_interval", 60))
            for key, bp in self.backends.items():
                if bp.git_url and bp.type != "stub":
                    try:
                        behind = bp.check_git()
                        if behind > 0:
                            log.info(f"[{key}] {behind} commit(s) behind, auto-updating...")
                            if bp.git_pull():
                                bp.restart()
                    except Exception as e:
                        log.error(f"git check {key}: {e}")

    def _start_health_loop(self) -> None:
        t = threading.Thread(target=self._health_loop, daemon=True)
        t.start()

    def _start_git_loop(self) -> None:
        t = threading.Thread(target=self._git_loop, daemon=True)
        t.start()

    def get_status(self) -> dict:
        self.push_progress["uptime"] = round(time.time() - self.start_time, 1)
        return {
            "daemon": {
                "uptime": round(time.time() - self.start_time, 1),
                "version": "1.0.0",
                "api_port": self.config.get("api_port", 19876),
                "tunnel_url": self.tunnel.url,
                "tunnel_running": self.tunnel.is_running(),
                "push_progress": dict(self.push_progress)
            },
            "backends": [bp.to_dict() for bp in self.backends.values()],
            "backend_tunnels": {
                k: {"url": t.url, "running": t.is_running()}
                for k, t in self.backend_tunnels.items()
            }
        }

    def start_backend(self, key: str) -> dict:
        bp = self.backends.get(key)
        if not bp:
            return {"error": f"unknown backend: {key}"}
        ok = bp.start()
        return {"status": bp.status, "port": bp.port, "result": "ok" if ok else "failed"}

    def stop_backend(self, key: str) -> dict:
        bp = self.backends.get(key)
        if not bp:
            return {"error": f"unknown backend: {key}"}
        ok = bp.stop()
        return {"status": bp.status, "result": "ok" if ok else "failed"}

    def restart_backend(self, key: str) -> dict:
        bp = self.backends.get(key)
        if not bp:
            return {"error": f"unknown backend: {key}"}
        ok = bp.restart()
        return {"status": bp.status, "result": "ok" if ok else "failed"}

    def get_logs(self, key: str, n: int = 50) -> dict:
        bp = self.backends.get(key)
        if not bp:
            return {"error": f"unknown backend: {key}"}
        return {"name": bp.name, "logs": bp.get_logs(n)}

    def update_backend(self, key: str) -> dict:
        bp = self.backends.get(key)
        if not bp:
            return {"error": f"unknown backend: {key}"}
        if not bp.git_url:
            return {"error": "no git configured"}
        ok = bp.git_pull()
        if ok:
            bp.restart()
        return {"result": "ok" if ok else "failed", "git_behind": bp.git_behind}

    def update_all(self) -> dict:
        results = {}
        for key, bp in self.backends.items():
            if bp.git_url and bp.type != "stub":
                ok = bp.git_pull()
                bp.restart()
                results[key] = "ok" if ok else "failed"
        return {"results": results}

    def _on_backend_tunnel_url(self, key: str, url: str) -> None:
        log.info(f"backend tunnel [{key}]: {url}")
        # Re-run frontend update with new backend URL
        if self.tunnel.url:
            self.frontend_autoupdate(self.tunnel.url)

    def frontend_autoupdate(self, tunnel_url: str) -> None:
        self.push_progress["step"] = "frontend_path"
        self.push_progress["progress"] = 0
        self.push_progress["status"] = "Sprawdzanie ścieżki frontendu..."

        frontend_path = Path(self.config.get("frontend_path", ""))
        if not frontend_path.exists():
            log.warning("frontend_autoupdate: frontend_path not found")
            self.push_progress["status"] = "Błąd: brak frontend_path"
            return
        index_file = frontend_path / "index.html"
        if not index_file.exists():
            log.warning("frontend_autoupdate: index.html not found")
            self.push_progress["status"] = "Błąd: brak index.html"
            return

        self.push_progress["step"] = "update_url"
        self.push_progress["progress"] = 20
        self.push_progress["status"] = "Aktualizacja DAEMON_URL w index.html..."

        import re
        content = index_file.read_text(encoding="utf-8")
        # Replace DAEMON_URL assignment
        pattern = r"(const DAEMON_URL = ')[^']*(' || '';)"
        new_content = re.sub(pattern, r"\1" + tunnel_url + r"\2", content)
        # Replace backend tunnel placeholders
        for key, tm in self.backend_tunnels.items():
            if tm.url:
                new_content = new_content.replace(f"__BACKEND_{key}__", tm.url)

        if new_content == content:
            log.info("frontend_autoupdate: nothing to update")
            self.push_progress["status"] = "URL już aktualny, pomijam..."
            return

        content = new_content
        index_file.write_text(content, encoding="utf-8")
        log.info(f"frontend_autoupdate: updated DAEMON_URL → {tunnel_url}")

        self.push_progress["step"] = "git_commit"
        self.push_progress["progress"] = 40
        self.push_progress["status"] = "Commit zmian do gita..."

        try:
            subprocess.run(["git", "add", "index.html"], cwd=frontend_path, capture_output=True, timeout=10)
            r = subprocess.run(
                ["git", "commit", "-m", f"auto-update tunnel URL: {tunnel_url}"],
                cwd=frontend_path, capture_output=True, timeout=10
            )
            if r.returncode != 0 and "nothing to commit" not in r.stdout.lower() + r.stderr.lower():
                log.warning(f"frontend_autoupdate: commit issue: {r.stderr.strip()[-200:]}")
        except Exception as e:
            log.error(f"frontend_autoupdate: git commit error: {e}")

        self.push_progress["step"] = "git_push"
        self.push_progress["progress"] = 60
        self.push_progress["status"] = "Push na GitHub..."

        try:
            r = subprocess.run(["git", "push", "origin", "master"], cwd=frontend_path, capture_output=True, text=True, timeout=30)
            if r.returncode == 0:
                self.push_progress["step"] = "deploy"
                self.push_progress["progress"] = 85
                self.push_progress["status"] = "Push OK – GitHub Pages deployuje..."
                log.info("frontend_autoupdate: pushed to GitHub → Pages deploys")
            else:
                self.push_progress["step"] = "push_failed"
                self.push_progress["progress"] = 0
                self.push_progress["status"] = f"Push failed: {r.stderr.strip()[-100:]}"
                log.warning(f"frontend_autoupdate: push failed: {r.stderr.strip()[-200:]}")
                return
        except Exception as e:
            self.push_progress["step"] = "push_error"
            self.push_progress["progress"] = 0
            self.push_progress["status"] = f"Błąd push: {e}"
            log.error(f"frontend_autoupdate: git push error: {e}")
            return

        self.push_progress["step"] = "done"
        self.push_progress["progress"] = 100
        self.push_progress["status"] = "Gotowe! Strona zaktualizowana."

# ─── FastAPI App ──────────────────────────────────────────────────────────────

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, FileResponse, PlainTextResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import uvicorn

def create_app(daemon: DaemonAPI) -> FastAPI:
    app = FastAPI(title="Portfolio Daemon", version="1.0.0")

    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

    # Serve frontend (WebBartosz) at root
    frontend_path = Path(daemon.config.get("frontend_path", ""))
    frontend_git = daemon.config.get("frontend_git", "")
    if not frontend_path.exists() and frontend_git:
        log.info(f"cloning frontend from {frontend_git}...")
        subprocess.run(["git", "clone", frontend_git, str(frontend_path)], capture_output=True, timeout=60)

    if frontend_path.exists() and (frontend_path / "index.html").exists():
        @app.get("/", response_class=HTMLResponse)
        def root():
            return (frontend_path / "index.html").read_text(encoding="utf-8")
        screenshots_path = frontend_path / "screenshots"
        if screenshots_path.exists():
            app.mount("/screenshots", StaticFiles(directory=str(screenshots_path)), name="screenshots")
        log.info(f"frontend mounted from {frontend_path}")

    # Serve downloadable files
    gui_dir = Path(__file__).parent.parent / "gui"
    run_sh = Path(__file__).parent.parent / "run.sh"
    manager_py = gui_dir / "manager.py"
    if manager_py.exists():
        @app.get("/manager.py")
        def download_manager():
            return FileResponse(str(manager_py), media_type="text/plain", filename="manager.py")
        log.info("manager.py served at /manager.py")
    if run_sh.exists():
        @app.get("/run.sh")
        def download_run():
            return FileResponse(str(run_sh), media_type="text/plain", filename="run.sh")
        log.info("run.sh served at /run.sh")



    @app.get("/health")
    def health():
        return {"status": "ok", "uptime": round(time.time() - daemon.start_time, 1)}

    @app.get("/status")
    def status():
        return daemon.get_status()

    @app.post("/start/{key}")
    def start_backend(key: str):
        return daemon.start_backend(key)

    @app.post("/stop/{key}")
    def stop_backend(key: str):
        return daemon.stop_backend(key)

    @app.post("/restart/{key}")
    def restart_backend(key: str):
        return daemon.restart_backend(key)

    @app.get("/logs/{key}")
    def get_logs(key: str, n: int = 50):
        return daemon.get_logs(key, n)

    @app.post("/start-all")
    def start_all():
        results = {}
        for key in daemon.backends:
            results[key] = daemon.start_backend(key)
        return {"results": results}

    @app.post("/stop-all")
    def stop_all():
        results = {}
        for key in daemon.backends:
            results[key] = daemon.stop_backend(key)
        return {"results": results}

    @app.post("/restart-all")
    def restart_all():
        results = {}
        for key in daemon.backends:
            results[key] = daemon.restart_backend(key)
        return {"results": results}

    @app.post("/update/{key}")
    def update_backend(key: str):
        return daemon.update_backend(key)

    @app.post("/update-all")
    def update_all():
        return daemon.update_all()

    @app.post("/tunnel/start")
    def tunnel_start():
        ok = daemon.tunnel.start()
        return {"result": "ok" if ok else "failed", "url": daemon.tunnel.url}

    @app.post("/tunnel/stop")
    def tunnel_stop():
        daemon.tunnel.stop()
        return {"result": "ok"}

    @app.get("/tunnel/status")
    def tunnel_status():
        return {
            "running": daemon.tunnel.is_running(),
            "url": daemon.tunnel.url
        }

    return app

# ─── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    if not CONFIG_PATH.exists():
        log.error(f"config not found: {CONFIG_PATH}")
        sys.exit(1)

    with open(CONFIG_PATH) as f:
        config = json.load(f)

    daemon = DaemonAPI(config)
    app = create_app(daemon)
    api_host = config.get("api_host", "127.0.0.1")
    api_port = config.get("api_port", 19876)

    log.info(f"starting daemon on {api_host}:{api_port}")
    log.info(f"managing {len(daemon.backends)} backends")

    # Start all backends that have type != stub
    for key, bp in daemon.backends.items():
        if bp.type != "stub":
            threading.Thread(target=bp.start, daemon=True).start()

    # Start Cloudflare tunnel (daemon API + frontend)
    threading.Thread(target=daemon.tunnel.start, daemon=True).start()

    # Start backend tunnels for live demos
    for key, bp in daemon.backends.items():
        if bp.type != "stub" and bp.port:
            tm = TunnelManager("127.0.0.1", bp.port, on_url=lambda url, k=key: daemon._on_backend_tunnel_url(k, url))
            daemon.backend_tunnels[key] = tm
            threading.Thread(target=tm.start, daemon=True).start()

    uvicorn.run(app, host=api_host, port=api_port, log_level="warning")

if __name__ == "__main__":
    main()
