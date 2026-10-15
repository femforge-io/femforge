"""The bundle filename vocabulary classifies every name exactly once, and the file primitives behave on a real filesystem."""

import fnmatch
import hashlib
import os
import stat
from pathlib import Path

import pytest

from femforge.common import files
from femforge.common.files import BundleFile, BundleGlob, RunFile, link_or_copy, partition_dir_name, remove_path, sha256_file, tree_size, write_atomic


def _classes_matching(name: str) -> list[RunFile]:
    return [file_class for file_class in RunFile if any(fnmatch.fnmatch(name, pattern) for pattern in file_class.patterns)]


def test_every_fixed_filename_belongs_to_exactly_one_class() -> None:
    for name in BundleFile:
        assert len(_classes_matching(name.value)) == 1, name


def test_every_glob_belongs_to_exactly_one_class() -> None:
    for pattern in BundleGlob:
        owners = [file_class for file_class in RunFile if pattern in file_class.patterns]
        assert len(owners) == 1, pattern


def test_no_pattern_is_shared_between_classes() -> None:
    seen: set[str] = set()
    for file_class in RunFile:
        for pattern in file_class.patterns:
            assert pattern not in seen, pattern
            seen.add(pattern)


def test_run_file_members_carry_patterns_and_round_trip_by_value() -> None:
    assert RunFile("fields") is RunFile.FIELDS
    assert RunFile.FIELDS.value == "fields"
    assert RunFile.FIELDS.patterns == (BundleGlob.PVTU, BundleGlob.VTU)
    assert RunFile.RUN_DIR.patterns == ()
    assert RunFile.BUNDLE.patterns == (BundleFile.MANIFEST, BundleFile.RESULT)


def test_partition_dir_name_matches_the_partitioning_glob() -> None:
    assert partition_dir_name(4) == "partitioning.4"
    assert _classes_matching(partition_dir_name(4)) == [RunFile.PARTITIONING]


def test_rank_scalars_match_scalars_but_not_the_names_sidecar() -> None:
    assert _classes_matching("scalars.dat.3") == [RunFile.SCALARS]
    assert _classes_matching("scalars.dat.names") == [RunFile.SCALARS]
    assert _classes_matching("case_t0001.vtu") == [RunFile.FIELDS]
    assert _classes_matching("geometry.msh") == [RunFile.GMSH_MESH]


def test_write_atomic_leaves_no_temp_file(tmp_path: Path) -> None:
    target = tmp_path / "out.json"
    write_atomic(target, "{}")
    assert target.read_text(encoding="utf-8") == "{}"
    assert not list(tmp_path.glob("*.tmp"))


def test_link_or_copy_links_on_the_same_volume(tmp_path: Path) -> None:
    src = tmp_path / "src.bin"
    _ = src.write_bytes(b"payload")
    dst = tmp_path / "dst.bin"
    linked = link_or_copy(src, dst)
    assert dst.read_bytes() == b"payload"
    assert linked == (os.stat(src).st_ino == os.stat(dst).st_ino)


def test_link_or_copy_falls_back_to_a_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def no_links(_src: object, _dst: object) -> None:
        raise OSError("links unsupported")

    monkeypatch.setattr(files.os, "link", no_links)
    src = tmp_path / "src.bin"
    _ = src.write_bytes(b"payload")
    dst = tmp_path / "dst.bin"
    assert link_or_copy(src, dst) is False
    assert dst.read_bytes() == b"payload"


def test_sha256_file_hashes_the_bytes(tmp_path: Path) -> None:
    path = tmp_path / "blob"
    _ = path.write_bytes(b"abc" * 1000)
    assert sha256_file(path) == hashlib.sha256(b"abc" * 1000).hexdigest()


def test_remove_path_clears_read_only_files_and_trees(tmp_path: Path) -> None:
    locked = tmp_path / "locked.txt"
    _ = locked.write_text("x", encoding="utf-8")
    os.chmod(locked, stat.S_IREAD)
    remove_path(locked)
    assert not locked.exists()

    tree = tmp_path / "partitioning.2"
    tree.mkdir()
    inner = tree / "part.1.nodes"
    _ = inner.write_text("y", encoding="utf-8")
    os.chmod(inner, stat.S_IREAD)

    remove_path(tree)
    assert not tree.exists()


def test_tree_size_sums_files_below_a_directory(tmp_path: Path) -> None:
    single = tmp_path / "one.bin"
    _ = single.write_bytes(b"12345")

    tree = tmp_path / "tree"
    (tree / "deep").mkdir(parents=True)
    _ = (tree / "a.bin").write_bytes(b"12")
    _ = (tree / "deep" / "b.bin").write_bytes(b"345")

    assert tree_size(single) == 5
    assert tree_size(tree) == 5
