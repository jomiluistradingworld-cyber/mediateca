"""CLI de mediateca, construida con Typer."""

from __future__ import annotations

from typing import Optional

import typer
from rich.console import Console
from rich.markup import escape as esc
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table

from . import library
from .config import ensure_dirs, load_config
from .downloader import DownloadError
from .formatting import fmt_duration as _fmt_duration
from .formatting import fmt_size as _fmt_size

app = typer.Typer(
    name="mediateca",
    help="Descargador multimedia universal, privado y local.",
    no_args_is_help=True,
)
console = Console()

# Los títulos, nombres de autor y rutas de archivo son datos externos (vienen
# de yt-dlp) y a menudo incluyen corchetes, p.ej. "Video [abc123].mp4". Rich
# interpreta "[...]" como marcado de estilo, así que TODO contenido dinámico
# debe pasar por esc() antes de ir a console.print()/Table para no corromper
# la salida ni lanzar errores de parseo.


@app.command()
def download(
    url: str = typer.Argument(..., help="URL del video, audio o playlist (un solo item)."),
    audio_only: bool = typer.Option(False, "--audio-only", "-a", help="Extraer solo audio."),
    quality: str = typer.Option(
        None, "--quality", "-q", help="best | 1080 | 720 | 480 (por defecto: el de config.toml)."
    ),
):
    """Descarga una URL y la añade a la biblioteca local."""
    config = load_config()
    ensure_dirs(config)
    conn = library.open_library(config)

    try:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("{task.percentage:>3.0f}%"),
            TimeElapsedColumn(),
            console=console,
        ) as progress:
            task_id = progress.add_task("Iniciando…", total=100)

            def on_progress(pct: Optional[float], message: str) -> None:
                if pct is not None:
                    progress.update(task_id, completed=pct, description=message or "Descargando…")
                else:
                    progress.update(task_id, description=message or "Descargando…")

            try:
                item_id = library.add_from_url(
                    conn, config, url, audio_only=audio_only, quality=quality, on_progress=on_progress
                )
            except DownloadError as e:
                progress.stop()
                console.print(f"[bold red]Error:[/bold red] {esc(str(e))}")
                raise typer.Exit(code=1) from None

        item = library.get_item(conn, item_id)
        console.print(
            f"[bold green]✓ Añadido a la biblioteca[/bold green] "
            f"(id {item_id}): {esc(item['title'])}"
        )
        console.print(f"  [dim]{esc(str(config.library_path / item['file_path']))}[/dim]")
    finally:
        conn.close()


@app.command(name="list")
def list_cmd(
    platform: Optional[str] = typer.Option(None, "--platform", "-p", help="Filtrar por plataforma (ej. youtube)."),
    limit: int = typer.Option(30, "--limit", "-n", help="Máximo de resultados."),
):
    """Lista los últimos elementos de la biblioteca."""
    config = load_config()
    conn = library.open_library(config)
    try:
        rows = library.list_library(conn, platform=platform, limit=limit)
        _print_table(rows)
    finally:
        conn.close()


@app.command()
def search(query: str = typer.Argument(..., help="Texto a buscar en título, autor, tags o descripción.")):
    """Busca en la biblioteca local."""
    config = load_config()
    conn = library.open_library(config)
    try:
        rows = library.search_library(conn, query)
        _print_table(rows)
    finally:
        conn.close()


@app.command()
def info(item_id: int = typer.Argument(..., help="ID del elemento (ver 'mediateca list').")):
    """Muestra el detalle completo de un elemento."""
    config = load_config()
    conn = library.open_library(config)
    try:
        row = library.get_item(conn, item_id)
        if row is None:
            console.print(f"[bold red]No existe el elemento con id {item_id}[/bold red]")
            raise typer.Exit(code=1)

        console.print(f"[bold]{esc(row['title'])}[/bold]  (id {row['id']})")
        console.print(f"  Plataforma:  {esc(row['extractor'])}")
        console.print(f"  Autor:       {esc(row['uploader'] or '—')}")
        console.print(f"  Duración:    {_fmt_duration(row['duration'])}")
        console.print(f"  Tamaño:      {_fmt_size(row['filesize'])}")
        console.print(f"  Tipo:        {esc(row['media_type'])}")
        console.print(f"  Añadido:     {row['added_at']}")
        console.print(f"  Origen:      {esc(row['source_url'])}")
        console.print(f"  Archivo:     {esc(str(config.library_path / row['file_path']))}")
    finally:
        conn.close()


@app.command()
def remove(
    item_id: int = typer.Argument(..., help="ID del elemento a eliminar."),
    delete_file: bool = typer.Option(False, "--delete-file", help="Borrar también el archivo del disco."),
):
    """Elimina un elemento de la biblioteca (y opcionalmente su archivo)."""
    config = load_config()
    conn = library.open_library(config)
    try:
        ok = library.remove_item(conn, item_id, delete_file=delete_file, config=config)
        if ok:
            console.print(f"[bold green]✓ Eliminado[/bold green] (id {item_id})")
        else:
            console.print(f"[bold red]No existe el elemento con id {item_id}[/bold red]")
            raise typer.Exit(code=1)
    finally:
        conn.close()


@app.command()
def serve(
    host: Optional[str] = typer.Option(None, help="Host (por defecto el de config.toml)."),
    port: Optional[int] = typer.Option(None, help="Puerto (por defecto el de config.toml)."),
    reload: bool = typer.Option(False, help="Recargar automáticamente en desarrollo."),
):
    """Levanta la interfaz web local."""
    import uvicorn

    config = load_config()
    ensure_dirs(config)
    console.print(
        f"[bold]mediateca[/bold] escuchando en "
        f"[cyan]http://{host or config.host}:{port or config.port}[/cyan] "
        f"(Ctrl+C para detener)"
    )
    uvicorn.run(
        "mediateca.web.app:create_app",
        factory=True,
        host=host or config.host,
        port=port or config.port,
        reload=reload,
    )


def _print_table(rows) -> None:
    if not rows:
        console.print("[dim]No se encontraron elementos.[/dim]")
        return
    table = Table(show_header=True, header_style="bold")
    table.add_column("ID", justify="right")
    table.add_column("Título")
    table.add_column("Autor")
    table.add_column("Plataforma")
    table.add_column("Duración", justify="right")
    table.add_column("Tamaño", justify="right")
    table.add_column("Añadido")
    for r in rows:
        table.add_row(
            str(r["id"]),
            esc(r["title"][:60]),
            esc((r["uploader"] or "—")[:25]),
            esc(r["extractor"]),
            _fmt_duration(r["duration"]),
            _fmt_size(r["filesize"]),
            r["added_at"][:10],
        )
    console.print(table)


if __name__ == "__main__":
    app()
