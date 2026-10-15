"""Structured telemetry on polars and hive-partitioned parquet: logs, run and artifact indexes, sweep progress."""

import lazy_loader


__getattr__, __dir__, __all__ = lazy_loader.attach_stub(__name__, __file__)
