#!/usr/bin/env python3
"""Refuse market-shard writers that try to replace the card EV bundle.

Root ``data.json`` / ``data.js`` is the Box EV card snapshot (Grade A, rates,
pity). Scheduled jobs each own a directory under ``data/``:

- ``data/sealed-boxes/`` — JP sealed landed prices
- ``data/market-jp-psa10-bench/`` — JP PSA10 broad benchmark
- ``data/market-en/``, ``data/market-vintage/``, ``data/market-midera/`` — EN pulse
- ``data/market-vintage-pc/`` — EN Base PriceCharting monthly indexes

A path is allowed when a ``data`` directory is one of its parents
(``data/<shard>/...``). The repo-root bundle has no ``data`` parent.
"""
from __future__ import annotations

import sys
from pathlib import Path


class CardBundleWriteError(RuntimeError):
    """A shard writer tried to replace root data.json or data.js."""


def assert_not_card_bundle(path: str | Path) -> None:
    raw = Path(path)
    resolved = raw if raw.is_absolute() else (Path.cwd() / raw)
    parts = resolved.parts
    if not parts or parts[-1] not in {"data.json", "data.js"}:
        return
    if "data" in parts[:-1]:
        return
    raise CardBundleWriteError(
        f"refusing to write {resolved}: root data.json / data.js is the card EV bundle. "
        "Write this job's shard under data/ instead."
    )


def self_test() -> None:
    assert_not_card_bundle("/tmp/repo/data/market-en/latest.json")
    assert_not_card_bundle("/tmp/repo/data/market-vintage/history.js")
    assert_not_card_bundle("/tmp/repo/data/market-midera/latest.json")
    assert_not_card_bundle("/tmp/repo/data/market-vintage-pc/latest.js")
    assert_not_card_bundle("/tmp/repo/data/market-jp-psa10-bench/latest.json")
    assert_not_card_bundle("/tmp/repo/data/sealed-boxes/latest.json")
    assert_not_card_bundle("/tmp/repo/data/sealed-boxes/latest.js")
    for rel in ("/tmp/repo/data.json", "/tmp/repo/data.js"):
        try:
            assert_not_card_bundle(rel)
        except CardBundleWriteError:
            continue
        raise AssertionError(f"should have refused {rel}")
    print("bundle_guard self-test ok")


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        self_test()
    else:
        print("usage: python3 tools/bundle_guard.py --self-test", file=sys.stderr)
        sys.exit(2)
