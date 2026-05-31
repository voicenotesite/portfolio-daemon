import json, os, time, threading, subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock, mock_open, PropertyMock

import pytest

from daemon.daemon import BackendProcess, TunnelManager, DaemonAPI, create_app

SAMPLE_CONFIG = {
    "api_port": 19876,
    "api_host": "127.0.0.1",
    "health_interval": 999,
    "git_check_interval": 999,
    "frontend_path": "/tmp/fake_frontend",
    "frontend_git": "",
    "backends": {
        "url-shortener": {
            "name": "URL Shortener",
            "path": "/tmp/url-shortener",
            "port": 8000,
            "git": "git@github.com:test/repo.git",
            "type": "fastapi",
            "entry": "app.main:app",
        },
        "task-queue": {
            "name": "Async Task Queue",
            "path": "/tmp/task-queue",
            "port": 8004,
            "git": None,
            "type": "stub",
            "entry": None,
        },
    },
}


class TestBackendProcess:
    def test_init_sets_attrs(self):
        bp = BackendProcess("test-key", {"name": "Test", "path": "/tmp/x", "port": 9999, "git": None, "type": "fastapi", "entry": "x:app"})
        assert bp.key == "test-key"
        assert bp.name == "Test"
        assert bp.port == 9999
        assert bp.status == "unknown"
        assert bp.git_behind == 0

    def test_to_dict_returns_correct_keys(self):
        bp = BackendProcess("test", {"name": "Test", "path": "/tmp/x", "port": 9999, "git": None, "type": "fastapi", "entry": "x:app"})
        d = bp.to_dict()
        assert d["key"] == "test"
        assert d["name"] == "Test"
        assert d["port"] == 9999
        assert d["status"] == "unknown"
        assert d["note"] is None

    def test_to_dict_stub(self):
        bp = BackendProcess("stub", {"name": "Stub", "path": "/tmp/x", "port": 8888, "git": None, "type": "stub", "entry": None})
        d = bp.to_dict()
        assert d["status"] == "unknown"
        assert d["note"] == "w implementacji"

    def test_find_venv_python_fallback(self):
        bp = BackendProcess("test", {"name": "Test", "path": "/tmp/does_not_exist", "port": 9999, "git": None, "type": "fastapi", "entry": "x:app"})
        py = bp.find_venv_python()
        assert py == "python3"

    @patch("daemon.daemon.os.path.exists", return_value=True)
    def test_find_venv_python_found(self, mock_exists):
        bp = BackendProcess("test", {"name": "Test", "path": "/tmp/x", "port": 9999, "git": None, "type": "fastapi", "entry": "x:app"})
        py = bp.find_venv_python()
        assert "venv/bin/python3" in py

    def test_stub_start_returns_true(self):
        bp = BackendProcess("stub", {"name": "Stub", "path": "/tmp/x", "port": 8888, "git": None, "type": "stub", "entry": None})
        assert bp.start() is True
        assert bp.status == "stub"

    def test_stub_stop_returns_true(self):
        bp = BackendProcess("stub", {"name": "Stub", "path": "/tmp/x", "port": 8888, "git": None, "type": "stub", "entry": None})
        assert bp.stop() is True
        assert bp.status == "stub"

    def test_log_adds_to_buffer(self):
        bp = BackendProcess("test", {"name": "Test", "path": "/tmp/x", "port": 9999, "git": None, "type": "fastapi", "entry": "x:app"})
        bp.log("hello")
        logs = bp.get_logs()
        assert any("hello" in l for l in logs)

    def test_check_git_no_git_url(self):
        bp = BackendProcess("test", {"name": "Test", "path": "/tmp/x", "port": 9999, "git": None, "type": "fastapi", "entry": "x:app"})
        assert bp.check_git() == 0

    def test_check_git_stub(self):
        bp = BackendProcess("stub", {"name": "Stub", "path": "/tmp/x", "port": 8888, "git": "git@x", "type": "stub", "entry": None})
        assert bp.check_git() == 0

    def test_git_pull_no_git_url(self):
        bp = BackendProcess("test", {"name": "Test", "path": "/tmp/x", "port": 9999, "git": None, "type": "fastapi", "entry": "x:app"})
        assert bp.git_pull() is True

    def test_health_check_stub(self):
        bp = BackendProcess("stub", {"name": "Stub", "path": "/tmp/x", "port": 8888, "git": None, "type": "stub", "entry": None})
        assert bp.health_check() == "stub"

    @patch("daemon.daemon.BackendProcess.is_port_open", return_value=False)
    def test_health_check_offline_no_started_at(self, mock_port):
        bp = BackendProcess("test", {"name": "Test", "path": "/tmp/x", "port": 9999, "git": None, "type": "fastapi", "entry": "x:app"})
        bp.started_at = None
        assert bp.health_check() == "offline"

    @patch("daemon.daemon.BackendProcess.is_port_open", return_value=False)
    def test_health_check_offline_triggers_restart(self, mock_port):
        bp = BackendProcess("test", {"name": "Test", "path": "/tmp/x", "port": 9999, "git": None, "type": "fastapi", "entry": "x:app"})
        bp.started_at = 100.0
        with patch.object(bp, "start", return_value=True):
            assert bp.health_check() == "offline"

    def test_get_logs_returns_list(self):
        bp = BackendProcess("test", {"name": "Test", "path": "/tmp/x", "port": 9999, "git": None, "type": "fastapi", "entry": "x:app"})
        bp.log_buffer.append("line1")
        bp.log_buffer.append("line2")
        assert bp.get_logs(10) == ["line1", "line2"]
        assert len(bp.get_logs(1)) == 1

    @patch("daemon.daemon.subprocess.run")
    def test_is_port_open_linux(self, mock_run):
        mock_run.return_value = MagicMock(stdout="12345\n", returncode=0)
        bp = BackendProcess("test", {"name": "Test", "path": "/tmp/x", "port": 9999, "git": None, "type": "fastapi", "entry": "x:app"})
        assert bp.is_port_open() is True


class TestTunnelManager:
    def test_init(self):
        tm = TunnelManager("127.0.0.1", 8888)
        assert tm.target_host == "127.0.0.1"
        assert tm.target_port == 8888
        assert tm.url is None

    def test_is_running_no_process(self):
        tm = TunnelManager("127.0.0.1", 8888)
        assert tm.is_running() is False

    @patch("daemon.daemon.os.path.exists", return_value=False)
    def test_start_no_cloudflared(self, mock_exists):
        tm = TunnelManager("127.0.0.1", 8888)
        assert tm.start() is False

    @patch("daemon.daemon.os.path.exists", return_value=True)
    @patch("daemon.daemon.subprocess.Popen")
    def test_start_success(self, mock_popen, mock_exists):
        mock_popen.return_value.poll.return_value = None
        mock_popen.return_value.pid = 12345
        tm = TunnelManager("127.0.0.1", 8888)
        assert tm.start() is True


class TestDaemonAPI:
    def test_init_creates_backends(self):
        api = DaemonAPI(SAMPLE_CONFIG)
        assert len(api.backends) == 2
        assert "url-shortener" in api.backends
        assert "task-queue" in api.backends

    def test_init_starts_loops(self):
        api = DaemonAPI(SAMPLE_CONFIG)
        assert api.backends["task-queue"].key == "task-queue"

    def test_get_status_returns_daemon_keys(self):
        api = DaemonAPI(SAMPLE_CONFIG)
        status = api.get_status()
        assert "daemon" in status
        assert "backends" in status
        assert "backend_tunnels" in status
        assert status["daemon"]["version"] == "1.0.0"
        assert len(status["backends"]) == 2

    def test_start_backend_unknown_key(self):
        api = DaemonAPI(SAMPLE_CONFIG)
        result = api.start_backend("nope")
        assert "error" in result

    def test_stop_backend_unknown_key(self):
        api = DaemonAPI(SAMPLE_CONFIG)
        result = api.stop_backend("nope")
        assert "error" in result

    def test_restart_backend_unknown_key(self):
        api = DaemonAPI(SAMPLE_CONFIG)
        result = api.restart_backend("nope")
        assert "error" in result

    def test_get_logs_unknown_key(self):
        api = DaemonAPI(SAMPLE_CONFIG)
        result = api.get_logs("nope")
        assert "error" in result

    def test_update_backend_no_git(self):
        api = DaemonAPI(SAMPLE_CONFIG)
        result = api.update_backend("task-queue")
        assert "error" in result

    def test_get_logs_returns_logs_for_backend(self):
        api = DaemonAPI(SAMPLE_CONFIG)
        bp = api.backends["url-shortener"]
        bp.log_buffer.append("test log line")
        result = api.get_logs("url-shortener")
        assert result["name"] == "URL Shortener"
        assert any("test log line" in l for l in result["logs"])

    def test_frontend_autoupdate_no_frontend_path(self):
        api = DaemonAPI(SAMPLE_CONFIG)
        api.frontend_autoupdate("https://test.trycloudflare.com")
        assert api.push_progress["status"] == "Błąd: brak frontend_path"

    def test_update_all(self):
        api = DaemonAPI(SAMPLE_CONFIG)
        result = api.update_all()
        assert "results" in result


class TestFastAPIEndpoints:
    @pytest.fixture
    def client(self):
        api = DaemonAPI(SAMPLE_CONFIG)
        app = create_app(api)
        from fastapi.testclient import TestClient
        return TestClient(app)

    def test_health_endpoint(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"

    def test_status_endpoint(self, client):
        r = client.get("/status")
        assert r.status_code == 200
        data = r.json()
        assert "daemon" in data
        assert "backends" in data

    def test_start_unknown_backend(self, client):
        r = client.post("/start/nope")
        assert r.status_code == 200
        assert "error" in r.json()

    def test_stop_unknown_backend(self, client):
        r = client.post("/stop/nope")
        assert r.status_code == 200
        assert "error" in r.json()

    def test_restart_unknown_backend(self, client):
        r = client.post("/restart/nope")
        assert r.status_code == 200
        assert "error" in r.json()

    def test_logs_unknown_backend(self, client):
        r = client.get("/logs/nope")
        assert r.status_code == 200
        assert "error" in r.json()

    def test_tunnel_status(self, client):
        r = client.get("/tunnel/status")
        assert r.status_code == 200
        data = r.json()
        assert "running" in data
        assert "url" in data

    def test_tunnel_stop(self, client):
        r = client.post("/tunnel/stop")
        assert r.status_code == 200
        assert r.json()["result"] == "ok"

    def test_update_unknown_backend(self, client):
        r = client.post("/update/nope")
        assert r.status_code == 200
        assert "error" in r.json()

    def test_start_all(self, client):
        r = client.post("/start-all")
        assert r.status_code == 200
        data = r.json()
        assert "results" in data
        assert len(data["results"]) == 2
