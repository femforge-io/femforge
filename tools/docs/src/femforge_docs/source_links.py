"""Markdown extension that links each code snippet to its source on GitHub."""

import os
import re
from typing import Final, override

from markdown import Markdown
from markdown.extensions import Extension
from markdown.preprocessors import Preprocessor


__all__ = ["SourceLinks", "makeExtension"]

SNIPPET: Final = re.compile(r'^\s*--8<-- "(?P<path>[^":]+)(?::(?P<start>\d+)(?::(?P<end>\d+))?)?"\s*$')
FENCE: Final = re.compile(r"^(?P<indent>\s*)(?P<fence>`{3,}|~{3,})")
HEADER: Final = re.compile(r'^(?P<head>(?P<indent>\s*)(?:\?{3}\+?|!{3})[^"]*)"(?P<title>.*)"\s*$')
ICON: Final = ":octicons-mark-github-16:"
LABEL: Final = "View on GitHub"


class SourceLinkPreprocessor(Preprocessor):
    """Adds a GitHub link to every fenced block whose body is a single snippet include."""

    def __init__(self, md: Markdown, repo: str, ref: str) -> None:
        super().__init__(md)
        self.repo = repo.rstrip("/")
        self.ref = ref

    @override
    def run(self, lines: list[str]) -> list[str]:
        def url(snippet: re.Match[str]) -> str:
            start, end = snippet["start"], snippet["end"]
            anchor = ""
            if start is not None:
                anchor = f"#L{start}-L{end}" if end is not None else f"#L{start}"

            return f"{self.repo}/blob/{self.ref}/{snippet['path']}{anchor}"

        def header(fence: int, indent: int) -> tuple[int, re.Match[str]] | None:
            for index in reversed(range(fence)):
                if not out[index].strip():
                    continue

                head = HEADER.match(out[index])
                if head is None or len(head["indent"]) >= indent:
                    return None

                return index, head

            return None

        out = list(lines)
        for index in reversed(range(1, len(lines) - 1)):
            snippet = SNIPPET.match(lines[index])
            opening = FENCE.match(lines[index - 1])
            closing = FENCE.match(lines[index + 1])
            if snippet is None or opening is None or closing is None or closing["fence"] != opening["fence"]:
                continue

            target = url(snippet)
            found = header(index - 1, len(opening["indent"]))
            if found is not None:
                at, head = found
                out[at] = f"{head['head']}\"{head['title']} [{ICON}]({target}){{ .ff-source title='{LABEL}' aria-label='{LABEL}' }}\""
                continue

            caption = f"{opening['indent']}[{ICON} {LABEL}]({target}){{ .ff-source-caption }}"
            out[index + 2 : index + 2] = ["", caption]

        return out


class SourceLinks(Extension):
    """Links fenced snippet includes to the file on GitHub at the commit being built."""

    def __init__(self, repo: str, ref: str | None = None) -> None:
        super().__init__()
        self.repo = repo
        self.ref = ref or os.environ.get("GITHUB_SHA", "main")

    @override
    def extendMarkdown(self, md: Markdown) -> None:
        preprocessor = SourceLinkPreprocessor(md, self.repo, self.ref)
        md.preprocessors.register(preprocessor, "femforge_source_links", 33)


def makeExtension(repo: str, ref: str | None = None) -> SourceLinks:
    """Entry point Python-Markdown calls to load the extension by module name."""
    return SourceLinks(repo, ref)
