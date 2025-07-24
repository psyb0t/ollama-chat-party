"""
RAG (Retrieval-Augmented Generation) functionality for Ollama Chat Party
Handles document processing, embedding generation, FAISS indexing, and context selection
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

import faiss  # type: ignore
import numpy as np
import numpy.typing as npt
import requests
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table

from file_readers import READERS, read_txt

console = Console()

# ───────────────────────── EMBEDDING CALL ────────────────────────────


def ollama_embed(
    texts: List[str], model: str, url: str, debug=False
) -> npt.NDArray[np.float32]:
    """Generate embeddings using Ollama's embedding API"""
    # Always send as array to avoid batch processing issues
    payload: Dict[str, Any] = {"model": model, "input": texts}
    if debug:
        console.print(
            Panel.fit(
                f"🔮 Embedding [bold cyan]{len(texts)}[/] text(s) with "
                f"model [bold green]{model}[/]",
                title="[bold blue]EMBEDDING REQUEST[/]",
                border_style="blue",
            )
        )

    try:
        r = requests.post(f"{url}/api/embed", json=payload, timeout=600)
        if r.status_code == 404:
            raise RuntimeError("/api/embed 404 — embedding model not found")
        r.raise_for_status()
        data = r.json()
        vecs = data.get("embeddings") or [data["embedding"]]
    except requests.exceptions.RequestException as e:
        console.print(f"🚨 [red]Embedding request failed:[/] {e}")
        raise
    arr = np.asarray(vecs, dtype="float32")

    if debug:
        table = Table(
            title="🎯 Embedding Results", show_header=True, header_style="bold magenta"
        )
        table.add_column("Property", style="cyan")
        table.add_column("Value", style="green")
        table.add_row("Shape", f"{arr.shape}")
        table.add_row("Dimensions", f"{arr.shape[-1]} dims")
        table.add_row("Sample Values", f"{arr[0][:5].round(4)}")
        console.print(table)

    return arr


# ────────────────────────  INDEX BUILDING  ──────────────────────────
CHUNK_CHARS = 1000


def chunk_text(text: str, size: int = CHUNK_CHARS) -> List[str]:
    """Split text into chunks of specified size"""
    text = re.sub(r"\s+", " ", text)
    return [text[i:i + size] for i in range(0, len(text), size)]


def scan_docs(root: Path) -> List[Tuple[str, str]]:
    """Scan directory for supported documents and read their content"""
    # Find supported files directly (much faster!)
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as prog:
        count_task = prog.add_task("🔍 Counting files...")

        files: List[Path] = []

        # Only glob for supported file extensions
        for ext in READERS.keys():
            pattern = f"**/*{ext}"
            for p in root.rglob(pattern):
                if p.is_file() and p.name not in ["faiss_index.bin", "doc_store.json"]:
                    files.append(p)

        prog.update(count_task, completed=True)

    total_files = len(files)
    console.print(f"📊 [bold green]Found {total_files} supported documents[/]")

    docs: List[Tuple[str, str]] = []
    files_processed = 0

    with Progress(
        SpinnerColumn(),
        BarColumn(),
        TextColumn("[progress.description]{task.description}"),
        TextColumn("[dim]{task.fields[current_file]}"),
        TimeElapsedColumn(),
        console=console,
    ) as prog:
        task = prog.add_task(
            "📄 Scanning documents", total=total_files, current_file=""
        )

        for p in files:
            files_processed += 1

            # Update description to show scanned/total instead of documents found
            prog.update(
                task,
                description=f"📄 Scanning documents ({files_processed}/{total_files})",
                current_file=f"{p.name}",
            )

            reader = READERS.get(p.suffix.lower(), read_txt)
            try:
                txt = reader(p).strip()
                if txt:
                    docs.append((str(p), txt))
            except Exception as e:
                console.print(
                    f"⚠️  [yellow]Skipping[/] [bold red]{p.name}[/]: [dim]{e}[/]"
                )
            prog.advance(task)

        prog.update(
            task,
            current_file="✅ Complete!",
            description="📄 Document scanning complete",
        )
    return docs


def build_or_load(
    root: Path,
    embed_model: str,
    url: str,
    rebuild: bool,
    debug: bool,
    batch_size: int = 32,
) -> Tuple[Any | None, Dict[str, Any]]:
    """Build or load FAISS index and document store"""
    # Save index files in the target directory
    db_path = root / "faiss_index.bin"
    doc_path = root / "doc_store.json"

    # Check if both files exist or handle orphaned files
    db_exists = db_path.exists()
    doc_exists = doc_path.exists()

    if db_exists and doc_exists and not rebuild:
        return faiss.read_index(str(db_path)), json.loads(  # type: ignore
            doc_path.read_text()
        )

    # If only one file exists, delete it and rebuild
    if db_exists and not doc_exists:
        console.print(
            "🗑️  [yellow]Found orphaned faiss_index.bin - deleting and rebuilding[/]"
        )
        db_path.unlink()
    elif doc_exists and not db_exists:
        console.print(
            "🗑️  [yellow]Found orphaned doc_store.json - deleting and rebuilding[/]"
        )
        doc_path.unlink()

    docs = scan_docs(root)

    if not docs:
        console.print("[bold yellow]⚠️  No documents found in directory![/bold yellow]")
        return None, {}

    chunks: List[str] = []
    meta: List[Dict[str, str]] = []
    for path, txt in docs:
        for ck in chunk_text(txt):
            chunks.append(ck)
            meta.append({"path": path})
    console.print(f"📊 [bold green]Total chunks:[/] [cyan]{len(chunks):,}[/]")

    dim = ollama_embed(["test"], embed_model, url)[0].shape[0]
    index = faiss.IndexFlatL2(dim)

    with Progress(
        SpinnerColumn(),
        BarColumn(),
        TextColumn("[progress.description]{task.description}"),
        TextColumn("[dim]{task.completed}/{task.total} chunks"),
        TimeElapsedColumn(),
        console=console,
    ) as prog:
        task = prog.add_task("🔮 Generating embeddings", total=len(chunks))
        for i in range(0, len(chunks), batch_size):
            current_batch_size = min(batch_size, len(chunks) - i)
            vecs = ollama_embed(chunks[i:i + current_batch_size], embed_model, url)
            index.add(vecs)  # type: ignore
            prog.advance(task, current_batch_size)

    faiss.write_index(index, str(db_path))  # type: ignore
    doc_path.write_text(json.dumps({"chunks": chunks, "meta": meta}))
    console.print("💾 [bold green]Index saved successfully![/] ✅")
    return index, {"chunks": chunks, "meta": meta}


# ─────────────────────── CONTEXT SELECTION ─────────────────────────


def pick_context(
    index: Any,
    store: Dict[str, Any],
    q_vec: npt.NDArray[np.float32],
    docs_k: int,
    chunks_k: int,
    debug: bool = False,
) -> str:
    """Select relevant document chunks based on similarity search"""
    # search many, then group by doc path
    distances, indices = index.search(q_vec, docs_k * chunks_k * 10)
    best_per_doc: Dict[str, float] = {}
    for dist, idx in zip(distances[0], indices[0]):
        path = store["meta"][idx]["path"]
        if path not in best_per_doc or dist < best_per_doc[path]:
            best_per_doc[path] = dist
    doc_order = [d for d, _ in sorted(best_per_doc.items(), key=lambda x: x[1])][
        :docs_k
    ]

    if debug:
        console.print(
            Panel.fit(
                f"🔍 Searching [bold cyan]{docs_k * chunks_k * 10}[/] candidates, "
                f"selecting [bold green]{chunks_k}[/] chunks from "
                f"[bold yellow]{docs_k}[/] docs",
                title="[bold magenta]CONTEXT SELECTION[/]",
                border_style="magenta",
            )
        )

        # Show best documents
        table = Table(
            title="📄 Best Documents by Distance",
            show_header=True,
            header_style="bold cyan",
        )
        table.add_column("Rank", style="yellow", width=6)
        table.add_column("Distance", style="green", width=10)
        table.add_column("Document", style="blue")

        for i, (doc, dist) in enumerate(
            sorted(best_per_doc.items(), key=lambda x: x[1])[:docs_k]
        ):
            table.add_row(f"#{i + 1}", f"{dist:.4f}", doc.split("/")[-1])
        console.print(table)

    chosen: List[str] = []
    seen: set[str] = set()
    for doc in doc_order:
        for idx in indices[0]:
            if store["meta"][idx]["path"] != doc:
                continue
            chunk = store["chunks"][idx]
            if chunk in seen:
                continue
            seen.add(chunk)
            chosen.append(chunk)
            if len(chosen) >= chunks_k:
                break
        if len(chosen) >= chunks_k:
            break

    if debug and chosen:
        console.print(
            Panel(
                f"Selected [bold green]{len(chosen)}[/] unique chunks\n"
                f"Total context length: "
                f"[bold cyan]{sum(len(c) for c in chosen):,}[/] characters",
                title="[bold green]✅ CONTEXT READY[/]",
                border_style="green",
            )
        )

    return "\n\n".join(chosen)
