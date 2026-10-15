"""Grouped rows on polars frames: a composite label per group for the tables to group by."""

from collections.abc import Sequence

import polars as pl


GROUP_COLUMN = "group"
"""The column group_rows adds and the tables group by; reserved in every frame a table shows."""

DECIMALS = 3
LABEL_SEPARATOR = "  ·  "

type Row = dict[str, object]
"""A table row: each column's value by name."""


def group_rows(frame: pl.DataFrame, by: str, first: Sequence[str] = (), max_: Sequence[str] = ()) -> pl.DataFrame:
    """The frame with a group label per `by` value: the value, the first of each `first` column, the row count and the maximum of each `max_` column.

    Args:
        frame: The rows to label.
        by: The column whose values form the groups.
        first: Columns whose first value per group joins the label.
        max_: Columns whose maximum per group joins the label.
    """
    firsts = [pl.first(name).alias(f"first {name}") for name in first]
    maxima = [pl.max(name).alias(f"max {name}") for name in max_]
    groups = frame.group_by(by, maintain_order=True).agg(*firsts, *maxima, pl.len().alias("count"))

    parts: list[pl.Expr] = [pl.col(by).cast(pl.String)]
    parts.extend(pl.col(f"first {name}").cast(pl.String) for name in first)
    parts.append(pl.format("{} rows", pl.col("count")))
    parts.extend(pl.format("max " + name + " {}", pl.col(f"max {name}").round(DECIMALS)) for name in max_)
    labelled = groups.select(pl.col(by), pl.concat_str(parts, separator=LABEL_SEPARATOR).alias(GROUP_COLUMN))

    return frame.join(labelled, on=by, how="left", maintain_order="left")
