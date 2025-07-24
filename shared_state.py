"""
Shared state management between CLI and web interfaces.
Handles message synchronization and RAG components.
"""

import threading
from dataclasses import dataclass
from queue import Queue
from typing import Any, Dict, List, Optional, Set, Tuple, TypedDict

from fastapi import WebSocket

from main import Args
from chat import ChatMessage, trim_messages_to_fit


class DisplayMessage(TypedDict):
    role: str
    content: str
    source: str  # 'web', 'cli', 'internal'
    user_name: Optional[str]  # User name if available


@dataclass
class SharedRAGState:
    """Thread-safe shared state for RAG system"""

    def __init__(self):
        self.messages: List[DisplayMessage] = []
        self.web_clients: Set[WebSocket] = set()
        self.index: Optional[Any] = None
        self.store: Optional[Dict[str, Any]] = None
        self.args: Optional[Args] = None  # Store CLI args for web server

        # Thread synchronization
        self.lock = threading.RLock()
        self.shutdown_event = threading.Event()

        # Inter-thread communication queues (flow control prevents overflow)
        self.web_to_cli_queue: Queue[Tuple[str, Any]] = Queue(maxsize=500)
        self.cli_to_web_queue: Queue[Tuple[str, Any]] = Queue(maxsize=1000)

    def set_rag_components(
        self,
        index: Optional[Any],
        store: Optional[Dict[str, Any]],
        args: Optional[Args] = None,
    ) -> None:
        """Set the RAG index, document store, and CLI args"""
        with self.lock:
            self.index = index
            self.store = store
            if args:
                self.args = args

    def add_message(
        self,
        message: Dict[str, str],
        source: str = "web",
        user_name: Optional[str] = None,
    ):
        """Add a message to the conversation history"""
        with self.lock:
            display_msg: DisplayMessage = {
                "role": message["role"],
                "content": message["content"],
                "source": source,
                "user_name": user_name,
            }
            self.messages.append(display_msg)

    def get_messages_for_context(
        self, context_size: int, debug: bool = False
    ) -> List[ChatMessage]:
        """Get messages formatted for LLM context, with token limiting"""
        with self.lock:
            # Convert to chat format and filter out display-only messages
            chat_messages: List[ChatMessage] = []
            for msg in self.messages:
                if msg["source"] != "display_only":  # Include internal messages
                    chat_messages.append(
                        ChatMessage(role=msg["role"], content=msg["content"])
                    )

            # Trim to fit context window
            return trim_messages_to_fit(chat_messages, context_size, debug=debug)

    def get_display_messages(self) -> List[DisplayMessage]:
        """Get messages for display (excludes internal context messages)"""
        with self.lock:
            return [msg for msg in self.messages if msg["source"] != "internal"]

    def add_web_client(self, websocket: WebSocket) -> None:
        """Add a web client connection"""
        with self.lock:
            self.web_clients.add(websocket)

    def remove_web_client(self, websocket: WebSocket) -> None:
        """Remove a web client connection"""
        with self.lock:
            self.web_clients.discard(websocket)

    def get_web_client_count(self) -> int:
        """Get number of connected web clients"""
        with self.lock:
            return len(self.web_clients)

    def shutdown(self):
        """Signal shutdown to all threads"""
        self.shutdown_event.set()


# Global shared state instance
shared_state = SharedRAGState()
