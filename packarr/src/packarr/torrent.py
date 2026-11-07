"""Bounded v1 metainfo reader. Hash the original info bytes, never a re-encoding."""

from dataclasses import dataclass
from hashlib import sha1
import math
import re

from .settings import Held

MAX_METAINFO = 8 * 1024**2
SAFE_EXTRA = {".srt", ".ass", ".ssa", ".vtt", ".nfo", ".txt", ".jpg", ".jpeg", ".png"}
VIDEO = {".mkv", ".mp4", ".m4v", ".avi", ".ts"}


def component(raw: bytes) -> str:
    try:
        value = raw.decode("utf-8")
    except (AttributeError, UnicodeError) as exc:
        raise Held("invalid_torrent_path_encoding") from exc
    if not value or value in {".", ".."} or any(c in value for c in "/\\:\x00") or any(ord(c) < 32 for c in value):
        raise Held("unsafe_torrent_path")
    return value


@dataclass(frozen=True)
class TorrentFile:
    index: int
    name: str
    length: int

    @property
    def extension(self):
        return "." + self.name.rsplit(".", 1)[-1].lower()


@dataclass(frozen=True)
class Torrent:
    info_hash: str
    files: tuple[TorrentFile, ...]
    total_bytes: int


def read_metainfo(raw: bytes) -> Torrent:
    if not raw or len(raw) > MAX_METAINFO:
        raise Held("metainfo_size_limit")
    pos = 0
    nodes = 0
    info_span = None

    def read(depth=0):
        nonlocal pos, nodes, info_span
        nodes += 1
        if depth > 32 or nodes > 100_000 or pos >= len(raw):
            raise Held("invalid_bencode_limits")
        token = raw[pos:pos + 1]
        if token == b"i":
            end = raw.find(b"e", pos + 1)
            value = raw[pos + 1:end] if end != -1 else b""
            if len(value) > 20 or not re.fullmatch(rb"0|-?[1-9][0-9]*", value):
                raise Held("invalid_bencode_integer")
            pos = end + 1
            return int(value)
        if token in {b"l", b"d"}:
            pos += 1
            result = [] if token == b"l" else {}
            previous = None
            while pos < len(raw) and raw[pos:pos + 1] != b"e":
                if token == b"l":
                    result.append(read(depth + 1))
                else:
                    key = read(depth + 1)
                    if not isinstance(key, bytes) or previous is not None and key <= previous:
                        raise Held("invalid_bencode_dictionary")
                    previous = key
                    start = pos
                    result[key] = read(depth + 1)
                    if depth == 0 and key == b"info":
                        info_span = (start, pos)
            if pos >= len(raw):
                raise Held("truncated_bencode")
            pos += 1
            return result
        colon = raw.find(b":", pos, pos + 12)
        prefix = raw[pos:colon] if colon != -1 else b""
        if not re.fullmatch(rb"0|[1-9][0-9]*", prefix):
            raise Held("invalid_bencode_string")
        size = int(prefix)
        pos = colon + 1
        if size > len(raw) - pos:
            raise Held("truncated_bencode_string")
        value = raw[pos:pos + size]
        pos += size
        return value

    try:
        top = read()
        if pos != len(raw) or not isinstance(top, dict) or info_span is None:
            raise Held("invalid_metainfo")
        info = top[b"info"]
        if not isinstance(info, dict) or b"meta version" in info:
            raise Held("only_v1_torrents_supported")
        name = component(info[b"name.utf-8"] if b"name.utf-8" in info else info[b"name"])
        items = info.get(b"files")
        if items is None:
            items = [{b"length": info[b"length"], b"path": []}]
        elif b"length" in info or not isinstance(items, list) or not items:
            raise Held("invalid_torrent_file_list")
        if len(items) > 512:
            raise Held("too_many_torrent_files")
        files = []
        names = set()
        for index, item in enumerate(items):
            if not isinstance(item, dict) or b"l" in item.get(b"attr", b"") or b"symlink path" in item:
                raise Held("unsafe_torrent_file")
            path = item.get(b"path.utf-8", item[b"path"])
            if not isinstance(path, list):
                raise Held("invalid_torrent_file_path")
            length = item[b"length"]
            if type(length) is not int or length < 0:
                raise Held("invalid_torrent_file_length")
            full_name = "/".join([name, *(component(c) for c in path)])
            if full_name.casefold() in names:
                raise Held("duplicate_torrent_file_path")
            names.add(full_name.casefold())
            files.append(TorrentFile(index, full_name, length))
        total = sum(f.length for f in files)
        piece_length = info[b"piece length"]
        pieces = info[b"pieces"]
        if type(piece_length) is not int or piece_length < 1 or not isinstance(pieces, bytes):
            raise Held("invalid_torrent_pieces")
        if total < 1 or len(pieces) != math.ceil(total / piece_length) * 20:
            raise Held("invalid_torrent_piece_count")
        return Torrent(sha1(raw[info_span[0]:info_span[1]]).hexdigest(), tuple(files), total)
    except (KeyError, TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise Held("invalid_metainfo_structure") from exc
