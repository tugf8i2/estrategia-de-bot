#!/usr/bin/env python3
"""Ejecuta el bot y el panel HTTPS de Hostless dentro de una sola app."""

import os
import threading

os.environ.setdefault("DASHBOARD_HOST", "0.0.0.0")
os.environ.setdefault("SIGNAL_DATA_DIR", "/tmp/coffee-fly-data")

from signal_bot_core import run_main
from tv_nas100_signals import cfg as nas100_cfg
from tv_xauusd_signals import cfg as xauusd_cfg
from dashboard import Handler, ThreadingHTTPServer
import dashboard


def bot_worker(cfg):
    try:
        run_main(cfg)
    except Exception:
        cfg.log.exception("El proceso principal del bot terminó inesperadamente")
        raise


def start_bot_workers():
    configs = (xauusd_cfg, nas100_cfg)
    threads = []
    for cfg in configs:
        thread = threading.Thread(
            target=bot_worker,
            args=(cfg,),
            name=f"signal-bot-{cfg.label.lower()}",
            daemon=True,
        )
        thread.start()
        threads.append(thread)
    return threads


if __name__ == "__main__":
    start_bot_workers()
    port = int(os.environ.get("PORT", os.environ.get("DASHBOARD_PORT", "8080")))
    dashboard.PORT = port
    print(f"Coffee Fly XAUUSD + NAS100 activo en 0.0.0.0:{port}")
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
