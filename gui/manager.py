#!/usr/bin/env python3
import os, sys, json, threading, time, platform
from pathlib import Path
from datetime import datetime
import tkinter as tk
from tkinter import ttk

try:
    import urllib.request
    import urllib.error
except ImportError:
    pass

HOME = Path.home()
IS_LINUX = platform.system() == "Linux"
DAEMON_URL = "http://127.0.0.1:19876"

class DaemonClient:
    def get(self, path: str, timeout: int = 5) -> dict:
        try:
            r = urllib.request.urlopen(f"{DAEMON_URL}{path}", timeout=timeout)
            return json.loads(r.read())
        except Exception:
            return {"error": "connection failed"}

    def post(self, path: str, timeout: int = 10) -> dict:
        try:
            req = urllib.request.Request(f"{DAEMON_URL}{path}", method="POST", data=b"")
            r = urllib.request.urlopen(req, timeout=timeout)
            return json.loads(r.read())
        except Exception:
            return {"error": "connection failed"}

class App:
    def __init__(self):
        self.client = DaemonClient()
        self.root = tk.Tk()
        self.root.title("Portfolio Manager")
        self.root.geometry("980x640")
        self.root.minsize(800, 480)
        self.root.configure(bg="#0a0a0f")
        self.selected = None
        self.status_data = {"daemon": {"uptime": 0, "version": "?"}, "backends": []}
        self.tunnel_was_connected = False

        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TProgressbar", background="#6366f1", troughcolor="#1a1a2e",
                        bordercolor="#0a0a0f", lightcolor="#6366f1", darkcolor="#6366f1")
        self._build_ui()
        self._poll_status()

    def _build_ui(self):
        # ── Splash header ──
        self.splash = tk.Frame(self.root, bg="#12121e", height=80)
        self.splash.pack(fill=tk.X)
        self.splash.pack_propagate(False)

        inner = tk.Frame(self.splash, bg="#12121e")
        inner.pack(expand=True)

        self.splash_text = tk.Label(inner, text="",
            font=("Segoe UI", 20, "bold"), fg="#6366f1", bg="#12121e")
        self.splash_text.pack()
        self.splash_sub = tk.Label(inner, text="",
            font=("Segoe UI", 10), fg="#8888aa", bg="#12121e")
        self.splash_sub.pack()

        self._splash_sequence()

        # ── Tunnel panel ──
        tunnel_frame = tk.Frame(self.root, bg="#1a1a2e", height=40)
        tunnel_frame.pack(fill=tk.X)
        tunnel_frame.pack_propagate(False)

        tk.Label(tunnel_frame, text="🔗", font=("Segoe UI", 12),
                 bg="#1a1a2e", fg="#6366f1").pack(side=tk.LEFT, padx=(12, 4))

        self.tunnel_var = tk.StringVar(value=" Tunnel: łączenie...")
        self.tunnel_label = tk.Label(tunnel_frame, textvariable=self.tunnel_var,
            font=("Segoe UI", 9, "bold"), bg="#1a1a2e", fg="#8888aa")
        self.tunnel_label.pack(side=tk.LEFT, padx=4)

        self.tunnel_status_dot = tk.Canvas(tunnel_frame, width=12, height=12,
            bg="#1a1a2e", highlightthickness=0)
        self.tunnel_status_dot.pack(side=tk.RIGHT, padx=(0, 12))
        self.tunnel_dot_id = self.tunnel_status_dot.create_oval(1, 1, 11, 11, fill="#8888aa", outline="")

        # ── Header ──
        header = tk.Frame(self.root, bg="#12121e", height=48)
        header.pack(fill=tk.X)
        header.pack_propagate(False)

        tk.Label(header, text="⚡ Portfolio Manager", font=("Segoe UI", 14, "bold"),
                 fg="#6366f1", bg="#12121e").pack(side=tk.LEFT, padx=16, pady=10)

        self.daemon_status_label = tk.Label(header, text="daemon: ?", font=("Segoe UI", 9),
                                            fg="#8888aa", bg="#12121e")
        self.daemon_status_label.pack(side=tk.RIGHT, padx=16, pady=10)

        # ── Toolbar ──
        toolbar = tk.Frame(self.root, bg="#0a0a0f")
        toolbar.pack(fill=tk.X, padx=12, pady=6)

        for text, cmd, color in [
            ("▶ All", self._start_all, "#22c55e"),
            ("■ All", self._stop_all, "#ef4444"),
            ("⟳ Restart All", self._restart_all, "#f59e0b"),
            ("⬇ Update All", self._update_all, "#6366f1"),
        ]:
            b = tk.Button(toolbar, text=text, font=("Segoe UI", 9, "bold"),
                          bg=color, fg="white", relief="flat", padx=14, pady=4,
                          cursor="hand2", border=0, command=cmd)
            b.pack(side=tk.LEFT, padx=3)

        # ── Main area: sidebar + content ──
        main = tk.Frame(self.root, bg="#0a0a0f")
        main.pack(fill=tk.BOTH, expand=True, padx=12, pady=4)

        sidebar = tk.Frame(main, bg="#12121e", width=220, highlightbackground="#2a2a44",
                           highlightthickness=1)
        sidebar.pack(side=tk.LEFT, fill=tk.Y)
        sidebar.pack_propagate(False)

        tk.Label(sidebar, text="Projekty", font=("Segoe UI", 10, "bold"),
                 fg="#e2e2f0", bg="#12121e").pack(pady=(12, 8))

        self.sidebar_buttons = {}
        projects = [
            ("url-shortener", "URL Shortener"),
            ("graphql-blog", "GraphQL Blog"),
            ("chat-proxy", "AI Chat Proxy"),
            ("task-queue", "Async Task Queue"),
            ("rag-qa", "RAG PDF Q&A"),
        ]

        for key, label in projects:
            frame = tk.Frame(sidebar, bg="#1a1a2e", cursor="hand2", highlightbackground="#2a2a44",
                             highlightthickness=1, padx=10, pady=8)
            frame.pack(fill=tk.X, padx=6, pady=2)

            dot = tk.Canvas(frame, width=10, height=10, bg="#1a1a2e", highlightthickness=0)
            dot.create_oval(1, 1, 9, 9, fill="#8888aa", outline="")
            dot.pack(side=tk.LEFT, padx=(0, 8))

            lbl = tk.Label(frame, text=label, font=("Segoe UI", 10),
                           fg="#e2e2f0", bg="#1a1a2e", anchor=tk.W)
            lbl.pack(side=tk.LEFT, fill=tk.X, expand=True)

            port_lbl = tk.Label(frame, text="", font=("Segoe UI", 7),
                                fg="#8888aa", bg="#1a1a2e")
            port_lbl.pack(side=tk.RIGHT, padx=(4, 0))

            frame.bind("<Button-1>", lambda e, k=key: self._select_backend(k))
            lbl.bind("<Button-1>", lambda e, k=key: self._select_backend(k))

            btn_frame = tk.Frame(frame, bg="#1a1a2e")
            btn_frame.pack(fill=tk.X, pady=(4, 0))

            for text, cmd, color in [
                ("▶", lambda k=key: self._start_backend(k), "#22c55e"),
                ("■", lambda k=key: self._stop_backend(k), "#ef4444"),
                ("⟳", lambda k=key: self._restart_backend(k), "#f59e0b"),
            ]:
                b = tk.Button(btn_frame, text=text, font=("Segoe UI", 7),
                              bg=color, fg="white", relief="flat", width=3, pady=0,
                              cursor="hand2", border=0, command=cmd)
                b.pack(side=tk.LEFT, padx=1)

            self.sidebar_buttons[key] = {"frame": frame, "dot": dot, "label": lbl,
                                         "port_lbl": port_lbl, "btn_frame": btn_frame}

        # ── Content panel ──
        content = tk.Frame(main, bg="#0a0a0f")
        content.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(8, 0))

        self.project_title = tk.Label(content, text="Wybierz projekt z sidebaru",
                                      font=("Segoe UI", 12, "bold"),
                                      fg="#e2e2f0", bg="#0a0a0f")
        self.project_title.pack(anchor=tk.W, pady=(0, 4))

        self.project_info = tk.Label(content, text="", font=("Segoe UI", 9),
                                     fg="#8888aa", bg="#0a0a0f")
        self.project_info.pack(anchor=tk.W, pady=(0, 4))

        log_label = tk.Label(content, text="Logi", font=("Segoe UI", 9, "bold"),
                             fg="#8888aa", bg="#0a0a0f")
        log_label.pack(anchor=tk.W)

        self.log_text = tk.Text(content, font=("Consolas", 9), bg="#0a0a0f",
                                fg="#22c55e", insertbackground="#e2e2f0",
                                relief="flat", border=0, state=tk.DISABLED,
                                wrap=tk.WORD)
        self.log_text.pack(fill=tk.BOTH, expand=True)

        # ── Deploy Progress ──
        deploy_frame = tk.Frame(self.root, bg="#12121e", height=36)
        deploy_frame.pack(fill=tk.X, side=tk.BOTTOM)
        deploy_frame.pack_propagate(False)

        self.deploy_status_var = tk.StringVar(value="")
        tk.Label(deploy_frame, textvariable=self.deploy_status_var,
                 font=("Segoe UI", 8), fg="#8888aa", bg="#12121e").pack(side=tk.LEFT, padx=12)

        self.deploy_bar = ttk.Progressbar(deploy_frame, mode="determinate",
                                           length=200, style="TProgressbar")
        self.deploy_bar.pack(side=tk.RIGHT, padx=12, pady=8)

        # ── Status bar ──
        status = tk.Frame(self.root, bg="#12121e", height=28)
        status.pack(fill=tk.X, side=tk.BOTTOM)
        self.status_var = tk.StringVar(value="Gotowy")
        tk.Label(status, textvariable=self.status_var, font=("Segoe UI", 8),
                 fg="#8888aa", bg="#12121e").pack(side=tk.LEFT, padx=12)

        self.version_var = tk.StringVar(value="")
        tk.Label(status, textvariable=self.version_var, font=("Segoe UI", 8),
                 fg="#555", bg="#12121e").pack(side=tk.RIGHT, padx=12)

    def _splash_sequence(self):
        messages = [
            ("Portfolio Daemon", "v1.0 · inicjalizacja..."),
            ("Portfolio Daemon", "ładowanie backendów..."),
            ("Portfolio Daemon", "uruchamianie tunelu..."),
            ("⚡ Portfolio Daemon", "gotowy!"),
        ]
        def animate(i=0):
            if i < len(messages):
                self.splash_text.config(text=messages[i][0])
                self.splash_sub.config(text=messages[i][1])
                self.root.after(600, lambda: animate(i + 1))
            else:
                self.root.after(300, self._fade_splash)
        animate()

    def _fade_splash(self):
        self.splash.destroy()
        self.root.configure(bg="#0a0a0f")

    def _pulse_tunnel_dot(self, color="#f59e0b", count=0):
        if count > 6:
            self.tunnel_status_dot.itemconfig(self.tunnel_dot_id, fill="#22c55e")
            return
        colors = [color, "#8888aa"]
        self.tunnel_status_dot.itemconfig(self.tunnel_dot_id, fill=colors[count % 2])
        self.root.after(300, lambda: self._pulse_tunnel_dot(color, count + 1))

    # ── API calls ──
    def _call_api(self, path: str, method: str = "get", callback=None):
        def go():
            try:
                if method == "post":
                    result = self.client.post(path)
                else:
                    result = self.client.get(path)
                if callback:
                    self.root.after(0, callback, result)
            except Exception as e:
                self.root.after(0, lambda: self._set_status(f"Błąd: {e}"))
        threading.Thread(target=go, daemon=True).start()

    def _select_backend(self, key: str):
        self.selected = key
        self._refresh_logs(key)
        self._refresh_project_info(key)

    def _refresh_project_info(self, key: str):
        for bp in self.status_data.get("backends", []):
            if bp["key"] == key:
                note = f" · {bp['note']}" if bp.get("note") else ""
                behind = f" · {bp['git_behind']} commitów za" if bp.get("git_behind", 0) > 0 else ""
                uptime = f" · online {bp['uptime']}s" if bp.get("uptime", 0) > 0 else ""
                self.project_info.config(
                    text=f"Port: {bp['port']} · Status: {bp['status']}{note}{behind}{uptime}"
                )
                return
        self.project_info.config(text="")

    def _refresh_logs(self, key: str):
        def update(result):
            if "logs" in result:
                self.log_text.config(state=tk.NORMAL)
                self.log_text.delete("1.0", tk.END)
                for line in result["logs"]:
                    self.log_text.insert(tk.END, line + "\n")
                self.log_text.see(tk.END)
                self.log_text.config(state=tk.DISABLED)
                self.project_title.config(text=result.get("name", key))
        self._call_api(f"/logs/{key}", callback=update)

    def _start_backend(self, key: str):
        if key in self.sidebar_buttons:
            self.sidebar_buttons[key]["dot"].itemconfig(1, fill="#f59e0b")
            self.sidebar_buttons[key]["label"].config(fg="#f59e0b")
        self._set_status(f"Startuję {key}...")
        self._call_api(f"/start/{key}", "post", callback=lambda r: self._set_status(
            f"{key}: {r.get('status', 'ok')}" if "error" not in r else f"Błąd: {r['error']}"))

    def _stop_backend(self, key: str):
        if key in self.sidebar_buttons:
            self.sidebar_buttons[key]["dot"].itemconfig(1, fill="#ef4444")
        self._set_status(f"Zatrzymuję {key}...")
        self._call_api(f"/stop/{key}", "post", callback=lambda r: self._set_status(
            f"{key}: zatrzymany" if "error" not in r else f"Błąd: {r['error']}"))

    def _restart_backend(self, key: str):
        if key in self.sidebar_buttons:
            self.sidebar_buttons[key]["dot"].itemconfig(1, fill="#f59e0b")
        self._set_status(f"Restartuję {key}...")
        self._call_api(f"/restart/{key}", "post", callback=lambda r: self._set_status(
            f"{key}: restart" if "error" not in r else f"Błąd: {r['error']}"))

    def _start_all(self):
        for btn in self.sidebar_buttons.values():
            btn["dot"].itemconfig(1, fill="#f59e0b")
        self._set_status("Uruchamianie wszystkich backendów...")
        self._call_api("/start-all", "post", callback=lambda r: self._set_status("Backendy uruchomione"))

    def _stop_all(self):
        for btn in self.sidebar_buttons.values():
            btn["dot"].itemconfig(1, fill="#ef4444")
        self._set_status("Zatrzymywanie wszystkich backendów...")
        self._call_api("/stop-all", "post", callback=lambda r: self._set_status("Backendy zatrzymane"))

    def _restart_all(self):
        for btn in self.sidebar_buttons.values():
            btn["dot"].itemconfig(1, fill="#f59e0b")
        self._set_status("Restartowanie wszystkich backendów...")
        self._call_api("/restart-all", "post", callback=lambda r: self._set_status("Backendy zrestartowane"))

    def _update_all(self):
        for btn in self.sidebar_buttons.values():
            btn["dot"].itemconfig(1, fill="#f59e0b")
        self._set_status("Aktualizacja wszystkich backendów...")
        self._call_api("/update-all", "post", callback=lambda r: self._set_status("Backendy zaktualizowane"))

    def _set_status(self, msg: str):
        self.status_var.set(msg)

    # ── Polling ──
    def _poll_status(self):
        def update(result):
            if "error" not in result:
                self.status_data = result
                daemon_info = result.get("daemon", {})
                uptime = daemon_info.get("uptime", 0)
                self.daemon_status_label.config(
                    text=f"daemon: online ({uptime:.0f}s)"
                )
                self.version_var.set(f"v{daemon_info.get('version', '?')}")

                # Tunnel status
                tunnel_url = daemon_info.get("tunnel_url")
                tunnel_running = daemon_info.get("tunnel_running", False)
                if tunnel_running and tunnel_url:
                    self.tunnel_var.set(f" 🔗 {tunnel_url}")
                    self.tunnel_label.config(fg="#22c55e")
                    if not self.tunnel_was_connected:
                        self.tunnel_was_connected = True
                        self.tunnel_status_dot.itemconfig(self.tunnel_dot_id, fill="#f59e0b")
                        self._pulse_tunnel_dot("#22c55e")
                elif tunnel_running:
                    self.tunnel_var.set(" Tunnel: łączenie...")
                    self.tunnel_label.config(fg="#f59e0b")
                else:
                    self.tunnel_var.set(" Tunnel: offline")
                    self.tunnel_label.config(fg="#ef4444")
                    self.tunnel_status_dot.itemconfig(self.tunnel_dot_id, fill="#ef4444")
                    self.tunnel_was_connected = False

                for bp in result.get("backends", []):
                    key = bp["key"]
                    if key in self.sidebar_buttons:
                        btn = self.sidebar_buttons[key]
                        color_map = {
                            "online": "#22c55e",
                            "starting": "#f59e0b",
                            "offline": "#ef4444",
                            "error": "#ef4444",
                            "stub": "#8888aa",
                            "degraded": "#f59e0b",
                            "unknown": "#555",
                        }
                        color = color_map.get(bp["status"], "#555")
                        btn["dot"].itemconfig(1, fill=color)
                        btn["port_lbl"].config(text=f":{bp['port']}")

                if self.selected:
                    for bp in result.get("backends", []):
                        if bp["key"] == self.selected:
                            self.project_info.config(
                                text=f"Port: {bp['port']} · Status: {bp['status']}"
                                + (f" · {bp['note']}" if bp.get("note") else "")
                                + (f" · {bp['git_behind']} commits behind" if bp.get("git_behind", 0) > 0 else "")
                                + (f" · online {bp['uptime']}s" if bp.get("uptime", 0) > 0 else "")
                            )
                            break

                push = daemon_info.get("push_progress", {})
                step = push.get("step", "") if push else ""
                prog = push.get("progress", 0) if push else 0
                status_text = push.get("status", "") if push else ""
                if step and step != "idle":
                    self.deploy_status_var.set(f"Deploy: {status_text}")
                    self.deploy_bar["value"] = prog
                else:
                    self.deploy_status_var.set("")
                    self.deploy_bar["value"] = 0
            else:
                self.daemon_status_label.config(text="daemon: offline")

        self._call_api("/status", callback=update)
        self.root.after(5000, self._poll_status)

    def run(self):
        self.root.mainloop()

if __name__ == "__main__":
    App().run()
