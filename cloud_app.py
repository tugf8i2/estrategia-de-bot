#!/usr/bin/env python3
"""Ejecuta el bot y el panel HTTPS de Hostless dentro de una sola app."""

import os
import threading

os.environ.setdefault("DASHBOARD_HOST", "0.0.0.0")
os.environ.setdefault("SIGNAL_DATA_DIR", "/tmp/coffee-fly-data")

from signal_bot_core import run_main
from tv_xauusd_signals import cfg
from dashboard import Handler, ThreadingHTTPServer
import dashboard


def bot_worker():
    try:
        run_main(cfg)
    except Exception:
        cfg.log.exception("El proceso principal del bot terminó inesperadamente")
        raise


if __name__ == "__main__":
    thread = threading.Thread(target=bot_worker, name="signal-bot", daemon=True)
    thread.start()
    port = int(os.environ.get("PORT", os.environ.get("DASHBOARD_PORT", "8080")))
    dashboard.PORT = port
    print(f"Coffee Fly activo en 0.0.0.0:{port}")
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
