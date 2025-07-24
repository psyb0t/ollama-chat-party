"""
Simple CLI handler without Rich Live display interference.
Handles CLI input and display while syncing with web interface.
"""

import threading
import time
from queue import Empty
from typing import Any, Dict, Optional, cast

import numpy as np
import numpy.typing as npt
from rich.console import Console

from main import Args
from chat import SYSTEM_PROMPT, stream_chat
from rag import ollama_embed, pick_context
from shared_state import shared_state

console = Console()


class SimpleCLIHandler:
    """Simple CLI handler without Rich Live interference"""

    def __init__(self, args: Args, web_enabled: bool = True):
        self.args = args
        self.web_enabled = web_enabled
        self.web_monitor_thread = None

    def start_cli(self):
        """Start simple CLI interface"""
        console.print("🚀 [bold green]Starting Ollama Chat Party...[/]")
        if self.web_enabled:
            console.print(
                "🌐 [bold blue]Web interface available at http://localhost:8000[/]"
            )

            # Check if password protection is enabled
            import os

            if os.getenv("OLLAMA_CHAT_PARTY_WEB_UI_PASSWORD"):
                console.print("🔐 [bold yellow]Web UI is password protected[/]")
        else:
            console.print("🖥️  [bold yellow]Running in CLI-only mode[/]")

        console.print()

        # Start background web message monitor only if web is enabled
        if self.web_enabled:
            self.web_monitor_thread = threading.Thread(
                target=self._monitor_web_messages, daemon=True
            )
            self.web_monitor_thread.start()

        # Run CLI loop in main thread
        self._run_cli_loop()

    def _run_cli_loop(self):
        """Main CLI interaction loop"""
        while not shared_state.shutdown_event.is_set():
            try:
                # Show web connection status only if web is enabled
                status = ""
                if self.web_enabled:
                    web_count = shared_state.get_web_client_count()
                    status = (
                        f"(🌐 {web_count} web client"
                        f"{'s' if web_count != 1 else ''} connected)"
                        if web_count > 0
                        else ""
                    )

                # Get user input with proper prompt
                if status:
                    console.print(f"\n[dim]{status}[/]")

                # Ensure we have a clean line for input
                console.print("[bold cyan]🗨️  > [/]", end="")
                message = input().strip()

                if message:
                    self._process_cli_message(message)
                    # Add a small delay to let any async processes finish
                    import time

                    time.sleep(0.1)

            except (KeyboardInterrupt, EOFError):
                console.print("\n[bold red]🛑 Shutting down...[/]")
                shared_state.shutdown()
                break
            except Exception as e:
                console.print(f"[bold red]CLI Error: {e}[/]")
                break

    def _monitor_web_messages(self):
        """Monitor web messages and display them in CLI"""
        while not shared_state.shutdown_event.is_set():
            try:
                self._process_web_messages()
                time.sleep(0.1)  # Check every 100ms
            except Exception as e:
                console.print(f"[bold red]Web monitor error: {e}[/]")
                time.sleep(1)

    def _process_message(
        self, message: str, source: str, user_name: Optional[str] = None
    ):
        """Process message through RAG pipeline (shared by CLI and web)"""
        try:
            # Check if RAG is enabled
            if shared_state.index is not None and shared_state.store is not None:
                # RAG is enabled - get document context
                q_vec: npt.NDArray[np.float32] = ollama_embed(
                    [message],
                    self.args.embed_model,
                    self.args.ollama_url,
                    self.args.debug,
                )
                ctx = pick_context(
                    shared_state.index,
                    shared_state.store,
                    q_vec,
                    self.args.max_ctx_docs,
                    self.args.chunks,
                    debug=self.args.debug,
                )

                # Add context message
                context_msg = {"role": "user", "content": f"CONTEXT:\n{ctx}"}
                shared_state.add_message(context_msg, source="internal")

            display_name = user_name if user_name else "Anonymous"
            question_msg = {
                "role": "user",
                "content": f"I am {display_name}:\n{message}",
            }
            shared_state.add_message(question_msg, source=source, user_name=user_name)

            # Get trimmed messages for context
            messages = shared_state.get_messages_for_context(
                self.args.context_size, debug=self.args.debug
            )

            # Stream response
            response_content = ""

            try:
                # For web responses, let stream_chat function handle robot emoji

                for chunk in stream_chat(
                    messages,
                    self.args.model,
                    self.args.ollama_url,
                    self.args.context_size,
                    self.args.debug,
                ):
                    # Always show in CLI
                    console.print(chunk, end="")

                    # Always send chunks to web clients for real-time streaming
                    shared_state.cli_to_web_queue.put(("chunk", chunk))

                    response_content += chunk

                # Always add newline after streaming
                console.print()  # New line after response

                # Add complete response to shared state (single source of truth)
                assistant_msg = {"role": "assistant", "content": response_content}
                shared_state.add_message(assistant_msg, source=source)

                # Notify web clients about complete response
                shared_state.cli_to_web_queue.put(
                    (
                        "assistant_complete",
                        {
                            "role": "assistant",
                            "content": response_content,
                            "source": source,
                        },
                    )
                )

            except Exception as e:
                error_msg = f"Error generating response: {str(e)}"
                if source == "cli":
                    console.print(f"\n[bold red]❌ {error_msg}[/]")

                # Add error to shared state (single source of truth)
                error_chat_msg = {"role": "assistant", "content": error_msg}
                shared_state.add_message(error_chat_msg, source=source)

                # Notify web clients about error
                shared_state.cli_to_web_queue.put(
                    (
                        "error",
                        {
                            "role": "assistant",
                            "content": error_msg,
                            "source": source,
                        },
                    )
                )

        except Exception as e:
            console.print(f"[bold red]Error processing {source} message: {e}[/]")

    def _process_cli_message(self, message: str):
        """Process message from CLI"""
        # Get CLI user name
        cli_name = getattr(self.args, "name", None)

        # Notify web clients of CLI user message with name
        shared_state.cli_to_web_queue.put(
            (
                "user_message",
                {
                    "role": "user",
                    "content": message,
                    "source": "cli",
                    "user_name": cli_name or "Anonymous",
                },
            )
        )

        # Process through shared pipeline with CLI user name
        self._process_message(message, "cli", user_name=cli_name)

    def _process_web_message(self, message: str, user_name: Optional[str] = None):
        """Process message from web interface"""
        # Process through shared pipeline with web user name
        self._process_message(message, "web", user_name=user_name)

        # Show CLI prompt after processing web message
        web_count = shared_state.get_web_client_count()
        if web_count > 0:
            console.print(
                f"\n[dim](🌐 {web_count} web client"
                f"{'s' if web_count != 1 else ''} connected)[/]"
            )
        console.print("[bold cyan]🗨️  > [/]", end="")

    def _process_web_messages(self):
        """Process messages from web interface"""
        try:
            while True:
                try:
                    msg_type, content = shared_state.web_to_cli_queue.get_nowait()

                    if msg_type == "user":
                        # Handle old format (content) and new format (dict)
                        if isinstance(content, dict):
                            content_dict = cast(Dict[str, Any], content)
                            msg_content: str = str(content_dict.get("content", ""))
                            user_name: Optional[str] = content_dict.get("user_name")
                            if isinstance(user_name, str):
                                pass  # Already correct type
                            else:
                                user_name = None
                        else:
                            msg_content: str = str(content)
                            user_name: Optional[str] = None

                        # Clear current line and show web user message
                        console.print(
                            f"\r[bold blue]🌐 [{user_name or 'Anonymous'}]:[/] "
                            f"[white]{msg_content}[/]"
                        )

                        # Process web message through same RAG pipeline
                        self._process_web_message(msg_content, user_name=user_name)

                except Empty:
                    break

        except Exception as e:
            console.print(f"[bold red]Error processing web messages: {e}[/]")


def run_simple_cli_interface(
    args: Args, index: Any, store: Dict[str, Any], web_enabled: bool = True
) -> None:
    """Run simple CLI interface with optional web sync"""
    # Set RAG components in shared state
    shared_state.set_rag_components(index, store, args)

    # Initialize conversation with system message (internal - don't show in web)
    system_prompt = args.system_prompt if args.system_prompt else SYSTEM_PROMPT
    system_msg: Dict[str, str] = {"role": "system", "content": system_prompt}
    shared_state.add_message(system_msg, source="internal")

    # Create and start CLI handler
    cli_handler = SimpleCLIHandler(args, web_enabled)
    cli_handler.start_cli()
