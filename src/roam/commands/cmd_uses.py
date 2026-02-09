"""Find all consumers of a symbol: callers, importers, inheritors."""

import click

from roam.db.connection import db_exists, open_db
from roam.output.formatter import abbrev_kind, loc, format_table


def _ensure_index():
    if not db_exists():
        from roam.index.indexer import Indexer
        Indexer().run()


@click.command()
@click.argument("name")
@click.option("--full", is_flag=True, help="Show all results without truncation")
def uses(name, full):
    """Show all consumers of a symbol: callers, importers, inheritors."""
    _ensure_index()

    with open_db(readonly=True) as conn:
        # Find the target symbol(s) by name
        targets = conn.execute(
            "SELECT id, name, kind, qualified_name FROM symbols WHERE name = ?",
            (name,),
        ).fetchall()

        if not targets:
            # Try LIKE search
            targets = conn.execute(
                "SELECT id, name, kind, qualified_name FROM symbols WHERE name LIKE ? LIMIT 50",
                (f"%{name}%",),
            ).fetchall()

        if not targets:
            click.echo(f"Symbol '{name}' not found.")
            raise SystemExit(1)

        target_ids = [t["id"] for t in targets]
        placeholders = ",".join("?" for _ in target_ids)

        # Find ALL edges pointing TO these targets
        rows = conn.execute(
            f"""SELECT s.name, s.qualified_name, s.kind, s.line_start,
                       f.path, e.kind as edge_kind, e.line as edge_line,
                       t.name as target_name
                FROM edges e
                JOIN symbols s ON e.source_id = s.id
                JOIN symbols t ON e.target_id = t.id
                JOIN files f ON s.file_id = f.id
                WHERE e.target_id IN ({placeholders})
                ORDER BY e.kind, f.path, s.line_start""",
            target_ids,
        ).fetchall()

        if not rows:
            click.echo(f"No consumers of '{name}' found.")
            return

        # Group by edge kind
        by_kind = {}
        for r in rows:
            by_kind.setdefault(r["edge_kind"], []).append(r)

        # Dedup within each group by (name, path)
        kind_labels = {
            "call": "Called by",
            "import": "Imported by",
            "inherits": "Extended by",
            "implements": "Implemented by",
            "uses_trait": "Used by (trait)",
        }

        total = 0
        click.echo(f"=== Consumers of '{name}' ===\n")

        # Show in a consistent order
        for kind in ("call", "import", "inherits", "implements", "uses_trait"):
            items = by_kind.get(kind)
            if not items:
                continue

            # Dedup by (qualified_name, path)
            seen = set()
            deduped = []
            for r in items:
                key = (r["qualified_name"], r["path"])
                if key not in seen:
                    seen.add(key)
                    deduped.append(r)

            label = kind_labels.get(kind, kind)
            total += len(deduped)

            table_rows = []
            for r in deduped:
                table_rows.append([
                    abbrev_kind(r["kind"]),
                    r["name"],
                    loc(r["path"], r["line_start"]),
                ])

            click.echo(f"-- {label} ({len(deduped)}) --")
            click.echo(format_table(
                ["Kind", "Name", "Location"],
                table_rows,
                budget=0 if full else 20,
            ))
            click.echo()

        # File summary: which files depend on this symbol
        files = set()
        for r in rows:
            files.add(r["path"])
        click.echo(f"Total: {total} consumers across {len(files)} files")
