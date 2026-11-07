import argparse
import fcntl
import json
import logging
import os
from pathlib import Path
import signal
import threading
import time

from . import __version__
from .engine import Engine, event
from .settings import Held, Settings


def main():
    parser = argparse.ArgumentParser(description="Packarr selective rolling-pack helper")
    parser.add_argument("--config", default="/data/options.json")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--inspect", action="store_true", help="Legacy dry-run discovery alias; complete seasons are not searched")
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    os.umask(0o077)
    try:
        settings = Settings.load(json.loads(Path(args.config).read_text()))
        if args.inspect and not settings.dry_run:
            raise Held("inspection_requires_dry_run")
        directory = Path(settings.state_dir)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        with (directory / "worker.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise Held("another_packarr_worker_is_running") from exc
            engine = Engine(settings)
            stop = threading.Event()
            signal.signal(signal.SIGTERM, lambda *_: stop.set())
            signal.signal(signal.SIGINT, lambda *_: stop.set())
            event("started", version=__version__, dry_run=settings.dry_run, discovery="automatic")
            next_search = 0
            while not stop.is_set():
                engine.reconcile()
                if time.monotonic() >= next_search:
                    engine.discover(inspect=args.inspect)
                    next_search = time.monotonic() + settings.poll_interval
                temporary = directory / "status.tmp"
                temporary.write_text(json.dumps(engine.status(), ensure_ascii=False))
                temporary.replace(directory / "status.json")
                if args.once:
                    break
                stop.wait(30)
            engine.state.close()
    except (Held, OSError, ValueError) as exc:
        event("startup_or_runtime_error", reason=str(exc) if isinstance(exc, Held) else type(exc).__name__)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
