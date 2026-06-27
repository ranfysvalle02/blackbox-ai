"""Console entry point: ``python -m blackbox_ai`` / ``blackbox-ai``.

Delegates to the argparse dispatcher in :mod:`blackbox_ai.cli` (``serve`` is
the default subcommand, preserving the original ``blackbox-ai`` behaviour).
"""

from __future__ import annotations

from blackbox_ai.cli import main

__all__ = ["main"]


if __name__ == "__main__":
    main()
