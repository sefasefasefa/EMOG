"""Kosu iptal bayraklari.

Tarayıcı kapanınca (ve kullanıcının arka plan izni yoksa) çalışan build
görevinin bir sonraki kontrol noktasında durması için süreç-içi işaretler.
"""
import threading

_lock = threading.Lock()
_cancelled_runs = set()


def cancel_run(run_id: str) -> None:
    if not run_id:
        return
    with _lock:
        _cancelled_runs.add(str(run_id))


def is_cancelled(run_id: str) -> bool:
    if not run_id:
        return False
    with _lock:
        return str(run_id) in _cancelled_runs


def clear_run(run_id: str) -> None:
    if not run_id:
        return
    with _lock:
        _cancelled_runs.discard(str(run_id))
