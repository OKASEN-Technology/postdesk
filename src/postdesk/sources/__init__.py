"""
Sources: what to post about (docs/sources.md). A source answers two
questions, what is new enough to post about (`candidates`) and what the
facts about one thing are (`facts`). The maker writes only from `Facts`;
it never sees the source itself.

Built in: `markdown` (a file or a folder of files with front matter) and
`manual` (a title and pasted text, from the web form or the CLI). Another
package adds one through the `postdesk.sources` entry point.
"""

from __future__ import annotations

from importlib.metadata import entry_points

from postdesk.sources.base import Candidate, Facts, Image, Source, SourceError, Subject

__all__ = ["Candidate", "Facts", "Image", "Source", "SourceError", "Subject", "available", "source_class"]


def available() -> dict[str, type]:
    """key -> source class, from the entry points (the built-ins are
    registered the same way)."""
    out: dict[str, type] = {}
    for ep in entry_points(group="postdesk.sources"):
        try:
            out[ep.name] = ep.load()
        except Exception as e:  # a broken plugin should not take the app down
            raise SourceError(f"source plugin {ep.name!r} failed to load: {e}") from e
    return out


def source_class(key: str) -> type:
    try:
        return available()[key]
    except KeyError:
        raise SourceError(f"no source {key!r}; installed: {', '.join(sorted(available())) or 'none'}") from None
