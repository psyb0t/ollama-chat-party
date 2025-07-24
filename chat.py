"""
Chat and LLM communication functionality for Ollama Chat Party
Handles token management, message formatting, and streaming responses
"""

from __future__ import annotations

import json
from typing import Any, Dict, Generator, List, Optional, TypedDict

import requests
import tiktoken
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

console = Console()

# Type definitions


class ChatMessage(TypedDict):
    role: str
    content: str


# Configuration
SYSTEM_PROMPT = (
    "You're a gothic dark raver cyberpunk AI assistant with fucking attitude - "
    "sharp, direct, and occasionally edgy. If CONTEXT is provided, use it to "
    "answer questions with precision - quote relevant snippets when they matter, "
    "but don't dump everything. Keep responses tight and real - no corporate "
    "bullshit or excessive politeness. If there's no context or it doesn't have "
    "the info, just answer based on your knowledge. Use some colorful language "
    "when it fits, but don't go overboard. Think hacker terminal meets "
    "street-smart AI."
)

# Switch between /api/chat (True) and /api/generate (False)
USE_CHAT_API = False

# Token counting with tiktoken
try:
    _tokenizer = tiktoken.get_encoding("cl100k_base")  # Used by GPT-4 and GPT-3.5-turbo
except Exception:
    _tokenizer = None  # type: ignore


def count_tokens(text: str) -> int:
    """Accurate token counting using tiktoken"""
    if _tokenizer is None:
        # Fallback to rough estimation if tiktoken fails
        return len(text) // 4
    return len(_tokenizer.encode(text))


def trim_messages_to_fit(
    messages: List[ChatMessage],
    context_size: int,
    reserve_tokens: int = 500,
    debug: bool = False,
) -> List[ChatMessage]:
    """Keep messages that fit within context size, ALWAYS preserving system
    messages and as many recent messages as possible. System messages are
    NEVER trimmed."""
    if not messages:
        return messages

    # Always keep system message if present
    system_msgs = [m for m in messages if m["role"] == "system"]
    other_msgs = [m for m in messages if m["role"] != "system"]

    # Calculate tokens used by system messages
    system_tokens = sum(count_tokens(m["content"]) for m in system_msgs)
    available_tokens = context_size - system_tokens - reserve_tokens

    if available_tokens <= 0:
        console.print(
            "⚠️  [yellow]Context size too small for system message - "
            "returning system message only[/]"
        )
        # Always return system messages even if they exceed context size
        # The LLM will handle truncation if needed
        return system_msgs

    # Keep as many recent messages as possible
    kept_msgs: List[ChatMessage] = []
    current_tokens = 0

    # Keep complete conversation turns (groups of context+question+response)
    # Go through messages in reverse order (most recent first)
    for msg in reversed(other_msgs):
        msg_tokens = count_tokens(msg["content"])
        if current_tokens + msg_tokens <= available_tokens:
            kept_msgs.insert(0, msg)  # Insert at beginning to maintain order
            current_tokens += msg_tokens
        else:
            # If we can't fit this message, stop adding more
            break

    total_tokens = system_tokens + current_tokens

    if debug:
        # Create a beautiful token usage display
        usage_percent = (total_tokens / context_size) * 100
        color = (
            "green" if usage_percent < 50 else "yellow" if usage_percent < 80 else "red"
        )

        table = Table(
            title="🎯 Token Usage Analysis",
            show_header=True,
            header_style="bold magenta",
        )
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style=color)
        table.add_row("Used Tokens", f"{total_tokens:,}")
        table.add_row("Context Size", f"{context_size:,}")
        table.add_row("Usage %", f"{usage_percent:.1f}%")
        table.add_row("Messages Kept", f"{len(system_msgs + kept_msgs)}")
        table.add_row("System Tokens", f"{system_tokens}")
        table.add_row("Content Tokens", f"{current_tokens}")
        console.print(table)

    return system_msgs + kept_msgs


def flatten_messages_to_prompt(messages: List[ChatMessage]) -> str:
    """Convert chat messages to a flat prompt format for /api/generate"""
    prompt_parts: List[str] = []

    for msg in messages:
        role = msg["role"].upper()
        content = msg["content"]
        prompt_parts.append(f"{role}: {content}")

    # Add ASSISTANT: at the end to prompt the model to continue
    prompt_parts.append("ASSISTANT:")

    return "\n\n".join(prompt_parts)


def _debug_display_request(
    payload: Dict[str, Any],
    messages: List[ChatMessage],
    model: str,
    title: str,
    endpoint: str,
):
    """Shared debug display logic for both API types"""
    console.print()  # Add newline before debug panel
    console.print(
        Panel.fit(
            f"🚀 Sending [bold cyan]{len(messages)}[/] messages to "
            f"[bold green]{model}[/] via [bold yellow]{endpoint}[/]",
            title=f"[bold yellow]{title}[/]",
            border_style="yellow",
        )
    )

    # Pretty print the JSON payload
    payload_json = json.dumps(payload, indent=2, ensure_ascii=False)
    syntax = Syntax(payload_json, "json", theme="monokai", line_numbers=False)
    console.print(
        Panel(
            syntax,
            title="[bold cyan]📤 OLLAMA PAYLOAD[/]",
            border_style="cyan",
        )
    )

    # Show conversation context
    table = Table(
        title="💬 Recent Messages", show_header=True, header_style="bold magenta"
    )
    table.add_column("Role", style="cyan", width=12)
    table.add_column("Content Preview", style="white")

    # Always show system message first, then most recent messages
    display_messages: List[ChatMessage] = []
    system_msg: Optional[ChatMessage] = next(
        (m for m in messages if m["role"] == "system"), None
    )
    if system_msg is not None:
        display_messages.append(system_msg)

    # Show all non-system messages (they're already trimmed to fit context)
    non_system_msgs: List[ChatMessage] = [m for m in messages if m["role"] != "system"]
    display_messages.extend(non_system_msgs)

    for msg in display_messages:
        content_preview = (
            msg["content"][:100] + "..."
            if len(msg["content"]) > 100
            else msg["content"]
        )
        role_emoji = {"user": "👤", "assistant": "🤖", "system": "⚙️"}.get(
            msg["role"], "💬"
        )
        table.add_row(f"{role_emoji} {msg['role']}", content_preview)
    console.print(table)
    console.print("[bold green]🤖 [/]", end="")


def stream_chat_api(
    messages: List[ChatMessage], model: str, url: str, context_size: int, debug=False
) -> Generator[str, None, None]:
    """Stream chat using Ollama's /api/chat endpoint"""
    payload: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": True,
        "options": {
            "num_ctx": context_size,
            "stop": [
                "USER:",
                "ASSISTANT:",
                "SYSTEM:",
                "User:",
                "Assistant:",
                "System:",
                "Human:",
                "AI:",
                "\nUSER:",
                "\nASSISTANT:",
                "\nSYSTEM:",
            ],
        },
    }

    if debug:
        _debug_display_request(
            payload, messages, model, "🤖 LLM CHAT REQUEST", "/api/chat"
        )
    else:
        console.print("[bold green]🤖 [/]", end="")

    with requests.post(
        f"{url}/api/chat",
        json=payload,
        stream=True,
        timeout=3600,
    ) as r:
        if r.status_code == 404:
            raise RuntimeError("/api/chat 404 — LLM name incorrect")
        r.raise_for_status()
        for line in r.iter_lines():
            if not line:
                continue
            chunk = json.loads(line.decode())
            if chunk.get("done"):
                break
            if "message" in chunk and "content" in chunk["message"]:
                yield chunk["message"]["content"]


def stream_generate_api(
    messages: List[ChatMessage], model: str, url: str, context_size: int, debug=False
) -> Generator[str, None, None]:
    """Stream chat using Ollama's /api/generate endpoint with flattened prompt"""
    prompt = flatten_messages_to_prompt(messages)

    payload: Dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "stream": True,
        "options": {
            "num_ctx": context_size,
            "stop": [
                "USER:",
                "ASSISTANT:",
                "SYSTEM:",
                "User:",
                "Assistant:",
                "System:",
                "Human:",
                "AI:",
                "\nUSER:",
                "\nASSISTANT:",
                "\nSYSTEM:",
            ],
        },
    }

    if debug:
        _debug_display_request(
            payload, messages, model, "📝 LLM GENERATE REQUEST", "/api/generate"
        )
    else:
        console.print("[bold green]🤖 [/]", end="")

    with requests.post(
        f"{url}/api/generate",
        json=payload,
        stream=True,
        timeout=3600,
    ) as r:
        if r.status_code == 404:
            raise RuntimeError("/api/generate 404 — LLM name incorrect")
        r.raise_for_status()
        for line in r.iter_lines():
            if not line:
                continue
            chunk = json.loads(line.decode())
            if chunk.get("done"):
                break
            if "response" in chunk:
                yield chunk["response"]


def stream_chat(
    messages: List[ChatMessage], model: str, url: str, context_size: int, debug=False
) -> Generator[str, None, None]:
    """Main chat streaming function that delegates to appropriate API based on
    USE_CHAT_API setting"""
    if USE_CHAT_API:
        yield from stream_chat_api(messages, model, url, context_size, debug)
    else:
        yield from stream_generate_api(messages, model, url, context_size, debug)
