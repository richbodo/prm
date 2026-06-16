"""core/media.py — the content-addressed media store for contact images (v0.2 R7).

Bytes live on disk under the PRM home; ``relationships.db`` only references them by content hash (the
hash is stored as a ``field_values`` value — see ``docs/design-notes/contact-images.md``). The store is a
git-style 256-way shard:

    <home>/media/<hh>/<sha256>.<ext>          # hh = first two hex chars of the sha256

The filename **is** the content hash, which gives distinctive names, automatic dedup (same bytes → one
file), a cheap integrity re-hash, and immutability (files are only added or removed, never edited). A
leaf module: pure stdlib (``hashlib``/``pathlib``), no imports of ``cli``/``core`` siblings, so the
snapshot ring and the daemon can both touch the same files on disk.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

# mime ↔ extension for the formats a contact photo realistically uses. Unknown types fall back to
# ``.bin`` (still stored + served, just without a friendly extension).
_MIME_EXT = {
    "image/jpeg": "jpg", "image/png": "png", "image/gif": "gif",
    "image/webp": "webp", "image/heic": "heic", "image/bmp": "bmp", "image/svg+xml": "svg",
}
_EXT_MIME = {v: k for k, v in _MIME_EXT.items()}

# Magic-byte sniffers, used when an export's PHOTO carries no usable TYPE param.
_SNIFF = [
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"), (b"GIF89a", "image/gif"),
    (b"BM", "image/bmp"),
]


def sniff_mime(data: bytes) -> str | None:
    """Best-effort image mime from magic bytes (RIFF/WEBP handled explicitly). ``None`` if unrecognized."""
    for magic, mime in _SNIFF:
        if data.startswith(magic):
            return mime
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def ext_for_mime(mime: str | None) -> str:
    return _MIME_EXT.get((mime or "").lower(), "bin")


def mime_for_ext(ext: str) -> str | None:
    return _EXT_MIME.get(ext.lstrip(".").lower())


def content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _shard_dir(media_dir: Path, h: str) -> Path:
    return Path(media_dir) / h[:2]


# --------------------------------------------------------------------------- write
def put(home, data: bytes, *, mime: str | None = None) -> str:
    """Hash ``data``, write it under the shard if absent (idempotent — content-addressed), return the
    hash. The extension comes from ``mime`` (or a magic-byte sniff), so ``locate`` can find it by hash
    alone. Writing the same bytes twice is a no-op."""
    h = content_hash(data)
    ext = ext_for_mime(mime or sniff_mime(data))
    shard = _shard_dir(home.media_dir, h)
    shard.mkdir(parents=True, exist_ok=True)
    dest = shard / f"{h}.{ext}"
    if not dest.exists():
        dest.write_bytes(data)
    return h


# --------------------------------------------------------------------------- read
def locate(home, h: str) -> Path | None:
    """The on-disk path for a hash (any extension), or ``None`` if not stored."""
    if not h:
        return None
    shard = _shard_dir(home.media_dir, h)
    if not shard.is_dir():
        return None
    for p in shard.glob(f"{h}.*"):
        return p
    return None


def exists(home, h: str) -> bool:
    return locate(home, h) is not None


def read(home, h: str) -> tuple[bytes, str] | None:
    """``(bytes, mime)`` for a stored hash, or ``None``. Mime is recovered from the file extension,
    falling back to a magic-byte sniff, then ``application/octet-stream``."""
    p = locate(home, h)
    if p is None:
        return None
    data = p.read_bytes()
    mime = mime_for_ext(p.suffix) or sniff_mime(data) or "application/octet-stream"
    return data, mime


# --------------------------------------------------------------------------- maintenance
def _all_blobs(home) -> list[Path]:
    md = Path(home.media_dir)
    if not md.is_dir():
        return []
    return [p for shard in md.iterdir() if shard.is_dir() for p in shard.iterdir() if p.is_file()]


def _hash_of(path: Path) -> str:
    return path.stem   # filename stem is the content hash


def gc(home, referenced: set) -> list[str]:
    """Delete blobs whose hash is not in ``referenced`` (the live set — e.g. every ``image`` field
    value). Returns the hashes removed. Immutable hash-named files make this safe to run any time."""
    referenced = set(referenced or ())
    removed = []
    for p in _all_blobs(home):
        h = _hash_of(p)
        if h not in referenced:
            p.unlink(missing_ok=True)
            removed.append(h)
    return removed


def verify(home, referenced: set) -> dict:
    """A health check for ``prm doctor``: which stored blobs fail their own hash (``corrupt``), which are
    on disk but referenced by nobody (``orphans``), and which are referenced but missing from disk
    (``missing``). Pure read; never deletes."""
    referenced = set(referenced or ())
    on_disk, corrupt, orphans = set(), [], []
    for p in _all_blobs(home):
        h = _hash_of(p)
        on_disk.add(h)
        if content_hash(p.read_bytes()) != h:
            corrupt.append(h)
        elif h not in referenced:
            orphans.append(h)
    missing = sorted(referenced - on_disk)
    return {"stored": len(on_disk), "corrupt": sorted(corrupt),
            "orphans": sorted(orphans), "missing": missing}
