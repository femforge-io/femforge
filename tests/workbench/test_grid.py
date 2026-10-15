"""Grouped rows on polars: the composite label the tables group by."""

import polars as pl

from femforge_workbench.grid import GROUP_COLUMN, group_rows


def _frame() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "series": ["alpha", "beta", "alpha", "beta"],
            "tag": ["a1", "b1", "a2", "b2"],
            "value": [1.23456, 2.5, 3.5, None],
            "count": [1, 2, 3, 4],
            "MAG.OD_mm": [10.0, 11.0, 12.0, 13.0],
        }
    )


def test_group_rows_labels_each_group_with_first_values_count_and_maxima() -> None:
    grouped = group_rows(_frame(), "series", first=("tag",), max_=("value",))

    labels = grouped.get_column(GROUP_COLUMN).to_list()

    assert grouped.columns == [*_frame().columns, GROUP_COLUMN]
    assert grouped.get_column("tag").to_list() == ["a1", "b1", "a2", "b2"]
    assert labels[0] == labels[2] == "alpha  ·  a1  ·  2 rows  ·  max value 3.5"
    assert labels[1] == labels[3] == "beta  ·  b1  ·  2 rows  ·  max value 2.5"
    assert group_rows(_frame(), "series").get_column(GROUP_COLUMN)[0] == "alpha  ·  2 rows"
