"""Show code ownership: who owns a file or directory."""

from datetime import datetime, timezone

import click

from roam.db.connection import open_db, db_exists, find_project_root
from roam.index.git_stats import get_blame_for_file
from roam.output.formatter import format_table


def _ensure_index():
    if not db_exists():
        click.echo("No index found. Building...")
        from roam.index.indexer import Indexer
        Indexer().run()


def _format_date(epoch: int) -> str:
    """Format a unix timestamp as YYYY-MM-DD."""
    if not epoch:
        return "?"
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%d")


def _ownership_for_file(project_root, file_path):
    """Compute ownership breakdown for a single file."""
    blame = get_blame_for_file(project_root, file_path)
    if not blame:
        return None

    author_lines = {}
    last_active = {}
    for entry in blame:
        author = entry["author"]
        author_lines[author] = author_lines.get(author, 0) + 1
        ts = entry.get("timestamp", 0)
        if ts and (author not in last_active or ts > last_active[author]):
            last_active[author] = ts

    total = sum(author_lines.values())
    if total == 0:
        return None

    # Sort by lines desc
    sorted_authors = sorted(author_lines.items(), key=lambda x: x[1], reverse=True)

    # Compute fragmentation: 1 - sum(p_i^2) (Herfindahl index complement)
    fragmentation = 1.0 - sum((n / total) ** 2 for _, n in sorted_authors)

    return {
        "authors": sorted_authors,
        "total": total,
        "fragmentation": round(fragmentation, 3),
        "main_dev": sorted_authors[0][0] if sorted_authors else "?",
        "last_active": last_active,
    }


@click.command()
@click.argument('path')
def owner(path):
    """Show code ownership: who owns a file or directory."""
    _ensure_index()
    project_root = find_project_root()
    path = path.replace("\\", "/")

    with open_db(readonly=True) as conn:
        # Check if path is a directory (matches multiple files)
        dir_files = conn.execute(
            "SELECT id, path FROM files WHERE path LIKE ? ORDER BY path",
            (f"{path}%",),
        ).fetchall()

        if not dir_files:
            # Try exact match
            frow = conn.execute(
                "SELECT id, path FROM files WHERE path = ?", (path,)
            ).fetchone()
            if frow is None:
                frow = conn.execute(
                    "SELECT id, path FROM files WHERE path LIKE ? LIMIT 1",
                    (f"%{path}",),
                ).fetchone()
            if frow is None:
                click.echo(f"Path not found in index: {path}")
                raise SystemExit(1)
            dir_files = [frow]

        if len(dir_files) == 1:
            _show_file_owner(conn, project_root, dir_files[0])
        else:
            _show_dir_owner(conn, project_root, path, dir_files)


def _show_file_owner(conn, project_root, file_row):
    """Show ownership for a single file."""
    file_path = file_row["path"]
    file_id = file_row["id"]
    click.echo(f"{file_path}")
    click.echo()

    info = _ownership_for_file(project_root, file_path)
    if info is None:
        click.echo("  (no blame data available)")
        return

    click.echo(f"Main developer: {info['main_dev']}")
    click.echo(f"Fragmentation:  {info['fragmentation']} (0=one owner, 1=many)")
    click.echo()

    rows = []
    for author, lines in info["authors"]:
        pct = f"{lines * 100 / info['total']:.0f}%"
        last = _format_date(info["last_active"].get(author, 0))
        rows.append([author, str(lines), pct, last])
    click.echo(format_table(["Author", "Lines", "Pct", "Last active"], rows))

    # Recent commits touching this file
    recent = conn.execute(
        """SELECT gc.author, gc.message, gc.timestamp
           FROM git_file_changes gfc
           JOIN git_commits gc ON gfc.commit_id = gc.id
           WHERE gfc.file_id = ?
           ORDER BY gc.timestamp DESC LIMIT 5""",
        (file_id,),
    ).fetchall()

    if recent:
        click.echo(f"\nRecent commits:")
        for r in recent:
            date = _format_date(r["timestamp"])
            msg = r["message"][:60]
            click.echo(f"  {date}  {r['author']}  {msg}")


def _show_dir_owner(conn, project_root, path, dir_files):
    """Show ownership for a directory using stored git data (fast)."""
    file_ids = [f["id"] for f in dir_files]
    click.echo(f"{path}/ ({len(dir_files)} files)")
    click.echo()

    # Use stored git data for fast aggregation
    placeholders = ",".join("?" for _ in file_ids)
    rows = conn.execute(
        f"""SELECT gc.author,
                   COUNT(DISTINCT gfc.commit_id) as commits,
                   SUM(gfc.lines_added + gfc.lines_removed) as churn,
                   MAX(gc.timestamp) as last_active,
                   COUNT(DISTINCT gfc.file_id) as files_touched
            FROM git_file_changes gfc
            JOIN git_commits gc ON gfc.commit_id = gc.id
            WHERE gfc.file_id IN ({placeholders})
            GROUP BY gc.author
            ORDER BY churn DESC""",
        file_ids,
    ).fetchall()

    if not rows:
        click.echo("  (no git data available)")
        return

    total_churn = sum(r["churn"] or 0 for r in rows)
    main_dev = rows[0]["author"] if rows else "?"
    click.echo(f"Main developer: {main_dev}")
    click.echo()

    table_rows = []
    for r in rows:
        churn = r["churn"] or 0
        pct = f"{churn * 100 / total_churn:.0f}%" if total_churn else "0%"
        table_rows.append([
            r["author"],
            str(r["commits"]),
            str(r["files_touched"]),
            str(churn),
            pct,
            _format_date(r["last_active"]),
        ])
    click.echo(format_table(
        ["Author", "Commits", "Files", "Churn", "Pct", "Last active"],
        table_rows,
        budget=15,
    ))

    # Top churned files in this directory
    churn_rows = conn.execute(
        f"""SELECT f.path, fs.commit_count, fs.total_churn, fs.distinct_authors
            FROM file_stats fs
            JOIN files f ON fs.file_id = f.id
            WHERE fs.file_id IN ({placeholders})
            ORDER BY fs.total_churn DESC LIMIT 10""",
        file_ids,
    ).fetchall()

    if churn_rows:
        click.echo(f"\nTop churned files:")
        tr = []
        for r in churn_rows:
            tr.append([
                r["path"],
                str(r["commit_count"]),
                str(r["total_churn"]),
                str(r["distinct_authors"]),
            ])
        click.echo(format_table(
            ["File", "Commits", "Churn", "Authors"],
            tr,
            budget=10,
        ))
