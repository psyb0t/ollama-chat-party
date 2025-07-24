#!/usr/bin/env python3
"""
Offline RAG with Ollama
"""

from __future__ import annotations

import argparse
import signal
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# Load environment variables from .env file
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass  # python-dotenv not installed, skip loading

from rich.console import Console
from rich.table import Table

from rag import build_or_load


@dataclass
class Args:
    rag_dir: Optional[str] = None
    model: str = "dolphin-mistral:7b"
    embed_model: str = "nomic-embed-text:v1.5"
    ollama_url: str = "http://localhost:11434"
    max_ctx_docs: int = 1
    chunks: int = 4
    context_size: int = 4096
    embed_batch_size: int = 32
    debug: bool = False
    rebuild: bool = False
    no_web: bool = False
    system_prompt: Optional[str] = None
    name: Optional[str] = None
    listen: str = "localhost:8000"


console = Console()

signal.signal(signal.SIGINT, lambda *_: sys.exit(0))  # type: ignore

# ───────────────────────────── CLI ─────────────────────────────────


def print_banner():
    """Print a compact banner"""
    banner = (
        "[bold magenta]╔═════════════════════════════════╗[/]\n"
        "[bold magenta]║[/]      [bold cyan]OLLAMA[/] [bold red]•[/] "
        "[bold yellow]CHAT[/] [bold red]•[/] [bold green]PARTY[/]      "
        "[bold magenta]║[/]\n"
        "[bold magenta]╚═════════════════════════════════╝[/]"
    )

    console.print(banner)
    console.print()


def main():
    print_banner()

    ap = argparse.ArgumentParser(
        description="Chat with Ollama, optionally with RAG using local documents"
    )
    ap.add_argument("--rag-dir", help="Directory with documents for RAG (optional)")
    ap.add_argument(
        "--model", default="dolphin-mistral:7b", help="LLM to use for chat responses"
    )
    ap.add_argument(
        "--embed-model",
        default="nomic-embed-text:v1.5",
        help="Embedding model for document vectorization",
    )
    ap.add_argument(
        "--ollama-url", default="http://localhost:11434", help="Ollama server URL"
    )
    ap.add_argument(
        "--max-ctx-docs",
        type=int,
        default=1,
        help="Maximum documents to include in context",
    )
    ap.add_argument("--chunks", type=int, default=4, help="Maximum chunks per query")
    ap.add_argument(
        "--context-size",
        type=int,
        default=4096,
        help="LLM context window size in tokens",
    )
    ap.add_argument(
        "--embed-batch-size",
        type=int,
        default=32,
        help="Batch size for embedding generation",
    )
    ap.add_argument(
        "--debug", action="store_true", help="Enable debug output and detailed logging"
    )
    ap.add_argument(
        "--rebuild", action="store_true", help="Force rebuild of document index"
    )
    ap.add_argument(
        "--no-web", action="store_true", help="Disable web interface (CLI only)"
    )
    ap.add_argument(
        "--system-prompt",
        help="Custom system prompt for the LLM",
    )
    ap.add_argument(
        "--name",
        help="Your name (will be included in context messages)",
    )
    ap.add_argument(
        "--listen",
        default="localhost:8000",
        help="Listen address and port in format host:port (default: localhost:8000)",
    )
    parsed_args = ap.parse_args()
    args = Args(
        rag_dir=parsed_args.rag_dir,
        model=parsed_args.model,
        embed_model=parsed_args.embed_model,
        ollama_url=parsed_args.ollama_url,
        max_ctx_docs=parsed_args.max_ctx_docs,
        chunks=parsed_args.chunks,
        context_size=parsed_args.context_size,
        embed_batch_size=parsed_args.embed_batch_size,
        debug=parsed_args.debug,
        rebuild=parsed_args.rebuild,
        no_web=parsed_args.no_web,
        system_prompt=parsed_args.system_prompt,
        name=parsed_args.name,
        listen=parsed_args.listen,
    )

    # Show all settings in debug mode
    if args.debug:
        settings_table = Table(
            title="🔧 Configuration Settings",
            show_header=True,
            header_style="bold magenta",
        )
        settings_table.add_column("Setting", style="cyan", width=20)
        settings_table.add_column("Value", style="green")

        settings_table.add_row(
            "📁 RAG directory",
            str(args.rag_dir) if args.rag_dir else "None (RAG disabled)",
        )
        settings_table.add_row("🤖 LLM name", args.model)
        settings_table.add_row("🔮 Embedding model", args.embed_model)
        settings_table.add_row("🌐 Ollama URL", args.ollama_url)
        settings_table.add_row("📄 Max context docs", str(args.max_ctx_docs))
        settings_table.add_row("📦 Max chunks", str(args.chunks))
        settings_table.add_row("🧠 Context size", f"{args.context_size:,} tokens")
        settings_table.add_row("⚡ Embed batch size", str(args.embed_batch_size))
        settings_table.add_row("🔄 Rebuild index", "Yes" if args.rebuild else "No")
        settings_table.add_row(
            "🌐 Web interface", "Disabled" if args.no_web else "Enabled"
        )
        system_prompt_display = (
            "Custom" if args.system_prompt else "Default (Cyberpunk)"
        )
        settings_table.add_row("💬 System prompt", system_prompt_display)
        user_name_display = args.name if args.name else "Anonymous"
        settings_table.add_row("👤 User name", user_name_display)
        settings_table.add_row("🐛 Debug mode", "Enabled")

        console.print(settings_table)
        console.print()

    # Build or load index only if RAG directory is provided
    if args.rag_dir:
        # Validate RAG directory exists first
        rag_path = Path(args.rag_dir).expanduser()
        if not rag_path.exists():
            console.print(f"[bold red]Error: RAG directory does not exist: {args.rag_dir}[/]")
            sys.exit(1)
        if not rag_path.is_dir():
            console.print(f"[bold red]Error: RAG path is not a directory: {args.rag_dir}[/]")
            sys.exit(1)

        try:
            index, store = build_or_load(
                rag_path,
                args.embed_model,
                args.ollama_url,
                args.rebuild,
                args.debug,
                args.embed_batch_size,
            )
            # If no documents found, switch to non-RAG mode
            if index is None:
                console.print(
                    "[yellow]📄 Switching to non-RAG mode due to no documents found.[/]"
                )
                index, store = None, {}
        except RuntimeError as e:
            console.print(f"[bold red]Error: {e}[/]")
            sys.exit(1)
    else:
        # No RAG directory provided, disable RAG functionality
        index, store = None, None
        if args.debug:
            console.print(
                "[yellow]⚠️  No RAG directory provided. RAG functionality disabled.[/]"
            )

    # Import CLI and web components
    from cli_handler_simple import run_simple_cli_interface
    from shared_state import shared_state

    # Start web server if enabled
    web_enabled = not args.no_web
    if web_enabled:
        from web_server import run_web_server

        # Parse listen address
        host, port = args.listen.split(":")
        port = int(port)

        # Start web server in background thread (silent startup)
        run_web_server(host=host, port=port)

    # Run CLI interface (same logic whether web is enabled or not)
    try:
        run_simple_cli_interface(args, index, store or {}, web_enabled)
    except KeyboardInterrupt:
        console.print("\n[bold red]🛑 Shutting down gracefully...[/]")
        if web_enabled:
            shared_state.shutdown()
    except Exception as e:
        console.print(f"[bold red]Error: {e}[/]")
        if web_enabled:
            shared_state.shutdown()


if __name__ == "__main__":
    main()
