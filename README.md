# Portfolio Daemon

Service manager do lokalnych backendów. Auto-start, health monitoring, logi, tunel Cloudflare.

## Funkcje

- Uruchamianie / zatrzymywanie backendów (FastAPI/uvicorn)
- Health check co 5s z kolorowym statusem
- Podgląd logów na żywo
- Tunel Cloudflare jednym kliknięciem
- REST API na porcie 19876
- GUI w tkinter

## Uruchomienie

```bash
./run.sh
```

Lub przez GUI:
```bash
python3 gui/manager.py
```

## API

| Metoda | Endpoint | Opis |
|--------|----------|------|
| GET | `/health` | Status daemona |
| GET | `/api/services` | Lista serwisów |
| POST | `/api/services/{name}/start` | Start serwisu |
| POST | `/api/services/{name}/stop` | Stop serwisu |
| GET | `/api/services/{name}/logs` | Logi serwisu |
| POST | `/api/tunnel/start` | Start tunelu |
| POST | `/api/tunnel/stop` | Stop tunelu |

## Wymagania

- Python 3.12+
- FastAPI
- uvicorn
- cloudflared (opcjonalnie, do tunelu)
