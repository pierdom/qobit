"""Whether lyrics belong to the track that is playing.

A player that takes the first search result serves, sooner or later, another
song's lyrics (or another recording's: timed for a live take, they drift a
minute off the studio one). The guards, in order:

1. The title and the artist must be the same after folding: case, accents,
   punctuation, "feat." and what is in brackets ("(Remastered 2011)") don't
   make another song. One shared artist is enough ("A & B" is "A"). But a
   bracket that names another recording does: "(English Ver.)" has other
   words, "(Live)" and "(Acoustic)" another performance (``variant``).
2. Timed lyrics must fit the track's length (``SYNC_TOLERANCE`` seconds: the
   source's own rule, plus Qobuz's whole-second lengths). Lyrics that fit
   only as text (``PLAIN_TOLERANCE``) are shown untimed.
3. Of what is left, the best timed one wins, then the closest in length, then
   the one from the same album.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from .lrc import parse_lrc, parse_plain
from .model import Lyrics, Query

SYNC_TOLERANCE = 2.5
PLAIN_TOLERANCE = 10.0

_BRACKETS = re.compile(r"[(\[{].*?[)\]}]")
_VERSION_SUFFIX = re.compile(
    r"\s[-–—]\s.*\b(remaster\w*|remix\w*|mix|live|edit|version|mono|stereo|demo|deluxe|bonus)\b.*$"
)
_FEAT = re.compile(r"\b(feat|ft|featuring)\b\.?.*$")
_ARTIST_SPLIT = re.compile(r",|&|;|/|\bfeat\b\.?|\bft\b\.?|\bfeaturing\b|\bwith\b|\band\b| x ")


def fold(text: str) -> str:
    """``text`` as a comparison key."""
    text = unicodedata.normalize("NFKD", text).casefold()
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = _BRACKETS.sub(" ", text)
    text = _VERSION_SUFFIX.sub(" ", text)
    text = _FEAT.sub(" ", text.replace("&", " and "))
    text = re.sub(r"[^\w\s]", " ", text)
    return " ".join(text.split())


# What a bracket or " - " suffix can say that makes it another recording (the
# same title, the same length, other words or another take). Anything else in
# it ("Remastered 2011", "Deluxe", "Mono") is noise.
_VARIANT_WORDS = frozenset(
    {
        *("live", "acoustic", "instrumental", "karaoke", "remix", "demo", "cover"),
        *("unplugged", "orchestral", "piano", "reprise"),
        *("english", "japanese", "korean", "chinese", "mandarin", "cantonese", "spanish"),
        *("french", "german", "italian", "portuguese", "russian", "arabic", "hindi", "turkish"),
    }
)
_SUFFIX = re.compile(r"\s[-–—]\s(.*)$")
_PART = re.compile(r"\b(?:part|pt)\.?\s*(\d+|[ivx]+)\b")  # "Part 2" is another song


def variant(title: str) -> frozenset[str]:
    """The variant words in the brackets and the " - " suffix of ``title``."""
    text = unicodedata.normalize("NFKD", title).casefold()
    parts = _BRACKETS.findall(text)
    if m := _SUFFIX.search(text):
        parts.append(m.group(1))
    words = {w for part in parts for w in re.findall(r"[^\W\d_]+", part)}
    numbered = {f"part {m.group(1)}" for part in parts for m in _PART.finditer(part)}
    return frozenset(words & _VARIANT_WORDS) | numbered


def _artists(name: str) -> set[str]:
    return {f for part in _ARTIST_SPLIT.split(name.casefold()) if (f := fold(part))}


@dataclass
class Candidate:
    """What a provider answered for a search, before it is judged."""

    title: str
    artist: str
    album: str = ""
    duration: float | None = None
    synced: str | None = None  # LRC text
    plain: str | None = None
    instrumental: bool = False


def _delta(q: Query, c: Candidate) -> float | None:
    return abs(c.duration - q.duration) if c.duration and q.duration else None


def _usable(q: Query, c: Candidate) -> tuple[int, Lyrics] | None:
    """The best lyrics this candidate can give for ``q`` and their quality
    (2 timed, 1 text, 0 instrumental), or None."""
    delta = _delta(q, c)
    if c.synced and (delta is None or delta <= SYNC_TOLERANCE):
        lyrics = parse_lrc(c.synced)
        if lyrics.synced:
            return 2, lyrics
    if c.plain and (delta is None or delta <= PLAIN_TOLERANCE):
        lyrics = parse_plain(c.plain)
        if lyrics.lines:
            return 1, lyrics
    if c.instrumental and (delta is None or delta <= SYNC_TOLERANCE):
        return 0, Lyrics(instrumental=True)
    return None


def pick(q: Query, candidates: list[Candidate]) -> Lyrics | None:
    """The lyrics of the candidate that is ``q``'s song, if any is."""
    title = fold(q.title) or q.title.casefold()
    wanted = variant(q.title) | variant(f"({q.version})") if q.version else variant(q.title)
    artists = _artists(q.artist)
    album = fold(q.album)
    best: tuple[tuple, Lyrics] | None = None
    for c in candidates:
        if (fold(c.title) or c.title.casefold()) != title or not artists & _artists(c.artist):
            continue
        if variant(c.title) != wanted:
            continue  # another recording: another language, a live or acoustic take
        usable = _usable(q, c)
        if usable is None:
            continue
        quality, lyrics = usable
        delta = _delta(q, c)
        key = (
            quality,
            -(delta if delta is not None else 0.0),
            bool(album) and fold(c.album) == album,
        )
        if best is None or key > best[0]:
            best = (key, lyrics)
    return best[1] if best else None
