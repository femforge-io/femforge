"""Where the example writes its run bundles and its run index."""

from pathlib import Path


EXAMPLE = Path(__file__).resolve().parent
RUNS_DIR = EXAMPLE / "runs"
TELEMETRY_DIR = EXAMPLE / "runs-index"
