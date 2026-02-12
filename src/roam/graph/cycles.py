"""Tarjan SCC / cycle detection for the symbol graph."""

from __future__ import annotations

import sqlite3

import networkx as nx


def find_cycles(G: nx.DiGraph, min_size: int = 2) -> list[list[int]]:
    """Return strongly connected components with at least *min_size* members.

    Components are sorted by size descending, and each component's node list
    is sorted for deterministic output.
    """
    if len(G) == 0:
        return []

    sccs = [
        sorted(c)
        for c in nx.strongly_connected_components(G)
        if len(c) >= min_size
    ]
    sccs.sort(key=len, reverse=True)
    return sccs


def format_cycles(
    cycles: list[list[int]], conn: sqlite3.Connection
) -> list[dict]:
    """Annotate each cycle with symbol names and file paths.

    Returns a list of dicts::

        [
            {
                "symbols": [{"id": 1, "name": "foo", "kind": "function", "file_path": "..."}],
                "files": ["src/a.py", "src/b.py"],
                "size": 3,
            },
            ...
        ]
    """
    if not cycles:
        return []

    # Pre-fetch all symbol IDs we need in one query per cycle (batch)
    all_ids = {sid for cycle in cycles for sid in cycle}
    if not all_ids:
        return []

    # Batch in chunks of 500 to avoid exceeding SQLite's
    # SQLITE_MAX_VARIABLE_NUMBER limit on large repositories.
    all_ids_list = list(all_ids)
    lookup: dict[int, dict] = {}
    for i in range(0, len(all_ids_list), 500):
        batch = all_ids_list[i:i+500]
        placeholders = ",".join("?" for _ in batch)
        rows = conn.execute(
            f"SELECT s.id, s.name, s.kind, f.path AS file_path "
            f"FROM symbols s JOIN files f ON s.file_id = f.id "
            f"WHERE s.id IN ({placeholders})",
            batch,
        ).fetchall()
        for sid, name, kind, fpath in rows:
            lookup[sid] = {"id": sid, "name": name, "kind": kind, "file_path": fpath}

    result = []
    for cycle in cycles:
        symbols = [lookup[sid] for sid in cycle if sid in lookup]
        files = sorted({s["file_path"] for s in symbols})
        result.append({"symbols": symbols, "files": files, "size": len(cycle)})
    return result
