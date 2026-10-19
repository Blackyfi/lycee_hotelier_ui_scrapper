"""One-shot migration: copy a plaintext SQLite DB into a new SQLCipher-encrypted file.

    python scripts/encrypt_db.py <plain.sqlite> <encrypted.sqlite> <key_file>

The source is never modified. The output is verified (cipher integrity check plus
identical row counts for every table) before the script reports success.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import sqlcipher3.dbapi2 as sqlcipher


def _tables(conn: sqlcipher.Connection, schema: str = "main") -> list[str]:
    rows = conn.execute(f"SELECT name FROM {schema}.sqlite_master WHERE type='table' ORDER BY name").fetchall()
    return [r[0] for r in rows]


def _counts(conn: sqlcipher.Connection) -> dict[str, int]:
    return {t: conn.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0] for t in _tables(conn)}


def main(src: Path, dst: Path, key_file: Path) -> int:
    key = key_file.read_text().strip()
    if not re.fullmatch(r"[0-9a-fA-F]{64}", key):
        print("key file must contain 64 hex chars", file=sys.stderr)
        return 1
    if dst.exists():
        print(f"refusing to overwrite {dst}", file=sys.stderr)
        return 1

    plain = sqlcipher.connect(str(src))
    if plain.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        print("source integrity_check failed", file=sys.stderr)
        return 1
    before = _counts(plain)
    plain.execute(f"ATTACH DATABASE '{dst}' AS enc KEY \"x'{key}'\"")
    plain.execute("SELECT sqlcipher_export('enc')")
    plain.execute("DETACH DATABASE enc")
    plain.close()

    enc = sqlcipher.connect(str(dst))
    enc.execute(f"PRAGMA key = \"x'{key}'\"")
    problems = enc.execute("PRAGMA cipher_integrity_check").fetchall()
    after = _counts(enc)
    enc.close()

    if problems or before != after:
        print(f"VERIFY FAILED: cipher problems={problems} before={before} after={after}", file=sys.stderr)
        return 1
    if dst.read_bytes()[:16] == b"SQLite format 3\x00":
        print("VERIFY FAILED: output has a plaintext header", file=sys.stderr)
        return 1
    print(f"OK {src} -> {dst}: {after}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print(__doc__, file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(main(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])))
