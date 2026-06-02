"""System shutdown invocation (§7.8 of docs/reference.md).

A thin wrapper around `/sbin/shutdown -h now` so the App holds a
callable it can invoke (and tests can replace with a no-op or a
spy). The actual two-phase visual is handled by the App + the
main loop's exit `finally:` block (see `ui.shutdown_screen` and
`__main__.main`), not here — this module's only job is to ask
systemd nicely to halt the box.

The fp-lapse systemd unit runs as root so no `sudo` is needed.
On the Mac dev mock path nothing here is called (the chord still
works for UI exercising; the App's injected `shutdown_action` is a
no-op).
"""

from __future__ import annotations

import logging
import subprocess

logger = logging.getLogger(__name__)


SHUTDOWN_CMD: list[str] = ["/sbin/shutdown", "-h", "now"]


def do_shutdown() -> None:
    """Ask systemd to halt the box. Returns immediately.

    Failure (binary missing, EPERM, …) is logged but does NOT raise:
    the operator already saw `SHUTTING DOWN…` on the panel and the
    only useful next signal is the log, accessible via SSH if the
    network is up.
    """
    try:
        # `Popen` and not `run`: returns immediately. systemd starts
        # firing SIGTERM at our service in a few hundred ms; the main
        # loop catches it and paints phase 2 in its `finally:` block.
        subprocess.Popen(  # noqa: S603 — fixed argv, no shell
            SHUTDOWN_CMD,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
        logger.info("shutdown: spawned %s", " ".join(SHUTDOWN_CMD))
    except FileNotFoundError:
        logger.warning("shutdown: %s not found — is this a Pi?", SHUTDOWN_CMD[0])
    except PermissionError:
        logger.warning(
            "shutdown: EPERM running %s — service must run as root",
            SHUTDOWN_CMD[0],
        )
    except Exception:
        logger.exception("shutdown: unexpected error invoking %s", SHUTDOWN_CMD[0])
