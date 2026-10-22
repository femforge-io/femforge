"""The docs source-links extension points each snippet block at its file on GitHub."""

import markdown
import pytest

REPO = "https://github.com/owner/name"


def render(text: str, ref: str | None = "abc123") -> str:
    md = markdown.Markdown(
        extensions=["femforge_docs.source_links", "attr_list", "pymdownx.details", "pymdownx.superfences"],
        extension_configs={"femforge_docs.source_links": {"repo": REPO, "ref": ref}},
    )
    return md.convert(text)


def test_details_header_gets_the_icon_link() -> None:
    html = render('??? example "main.py"\n\n    ```python\n    --8<-- "examples/a/main.py"\n    ```\n')

    assert '<summary>main.py <a aria-label="View on GitHub" class="ff-source"' in html
    assert f'href="{REPO}/blob/abc123/examples/a/main.py"' in html


def test_bare_block_gets_a_caption_after_the_fence() -> None:
    html = render('```python\n--8<-- "src/main.py"\n```\n\nNext.\n')

    assert f'<p><a class="ff-source-caption" href="{REPO}/blob/abc123/src/main.py">' in html
    assert html.index("ff-source-caption") > html.index("</code></pre>")


@pytest.mark.parametrize(
    ("spec", "anchor"),
    [("a.py:3", "#L3"), ("a.py:3:10", "#L3-L10")],
)
def test_line_ranges_become_anchors(spec: str, anchor: str) -> None:
    html = render(f'```python\n--8<-- "{spec}"\n```\n')

    assert f'href="{REPO}/blob/abc123/a.py{anchor}"' in html


def test_includes_outside_a_fence_are_left_alone() -> None:
    html = render('--8<-- "README.md:3"\n')

    assert "github.com" not in html


def test_ref_defaults_to_the_build_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_SHA", "deadbeef")

    html = render('```python\n--8<-- "a.py"\n```\n', ref=None)

    assert f"{REPO}/blob/deadbeef/a.py" in html


def test_a_block_after_a_paragraph_gets_a_caption() -> None:
    html = render('Intro text.\n\n```python\n--8<-- "src/main.py"\n```\n')

    assert f'<a class="ff-source-caption" href="{REPO}/blob/abc123/src/main.py">' in html
