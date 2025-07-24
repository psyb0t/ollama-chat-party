"""
FastAPI web server with WebSocket support for real-time chat.
Handles web client connections and synchronization with CLI.
"""

import asyncio
import json
import os
import threading
import uuid
from queue import Empty
from typing import Any, Dict, List, Optional, Set

# Load environment variables from .env file
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass  # python-dotenv not installed, skip loading

import uvicorn
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from shared_state import shared_state

# Session management
active_sessions: Set[str] = set()
session_websockets: Dict[str, Set[WebSocket]] = {}  # Track WebSockets by session
password_required = os.getenv("OLLAMA_CHAT_PARTY_WEB_UI_PASSWORD") is not None
web_password = os.getenv("OLLAMA_CHAT_PARTY_WEB_UI_PASSWORD")

# Global broadcaster task
broadcaster_task = None


class PasswordRequest(BaseModel):
    password: str


class SessionResponse(BaseModel):
    session_id: str
    success: bool
    message: str


def strip_name_info_for_display(content: str, role: str) -> str:
    """Strip name info from user messages for clean UI display"""
    if role == "user" and "I am" in content:
        lines = content.split("\n")
        if len(lines) > 1 and lines[0].startswith("I am"):
            return "\n".join(lines[1:])
    return content


app = FastAPI(title="RAG Chat Server")

# Serve static files and templates
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


@app.get("/")
async def chat_page(request: Request):
    """Serve the main chat interface"""
    return templates.TemplateResponse(
        "chat.html", {"request": request, "password_required": password_required}
    )


@app.post("/auth/login")
async def login(password_request: PasswordRequest):
    """Authenticate user and create session"""
    if not password_required:
        raise HTTPException(
            status_code=400, detail="Password authentication not enabled"
        )

    if password_request.password == web_password:
        session_id = str(uuid.uuid4())
        active_sessions.add(session_id)

        # No login messages in CLI anymore - keep it clean

        return SessionResponse(
            session_id=session_id, success=True, message="Authentication successful"
        )
    else:
        raise HTTPException(status_code=401, detail="Invalid password")


@app.post("/auth/logout")
async def logout(request: Request) -> Dict[str, Any]:
    """Logout user and invalidate session"""
    session_id = request.headers.get("X-Session-ID")
    if session_id and session_id in active_sessions:
        active_sessions.remove(session_id)
        # Force close any WebSocket connections for this session
        if session_id in session_websockets:
            websockets_to_close = session_websockets[session_id].copy()
            for ws in websockets_to_close:
                try:
                    await ws.close(code=1000, reason="Session logged out")
                except Exception:
                    pass  # WebSocket might already be closed
            # Cleanup will happen in the WebSocket finally blocks

    return {"success": True, "message": "Logged out successfully"}


@app.get("/auth/validate")
async def validate_session(request: Request) -> Dict[str, Any]:
    """Validate if session is still active"""
    if not password_required:
        return {"valid": True, "password_required": False}

    session_id = request.headers.get("X-Session-ID")
    is_valid = session_id in active_sessions if session_id else False
    return {"valid": is_valid, "password_required": True}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """Handle WebSocket connections for real-time chat"""
    global broadcaster_task

    await websocket.accept()

    # Check session if password is required
    session_id = None
    if password_required:
        # Get session ID from query parameters
        session_id = websocket.query_params.get("session_id")
        if not session_id or session_id not in active_sessions:
            await websocket.close(code=1008, reason="Invalid or missing session")
            return

        # Track this WebSocket for the session
        if session_id not in session_websockets:
            session_websockets[session_id] = set()
        session_websockets[session_id].add(websocket)

    # Start global broadcaster on first client connection
    if broadcaster_task is None or broadcaster_task.done():
        broadcaster_task = asyncio.create_task(broadcast_cli_messages())

    shared_state.add_web_client(websocket)

    # Create broadcast queue and ACK tracking for this client
    setattr(websocket, "_broadcast_queue", asyncio.Queue())
    setattr(websocket, "_ack_event", asyncio.Event())
    setattr(websocket, "_next_seq_id", 1)
    setattr(websocket, "_last_ack_seq", 0)
    # broadcast_task = None  - not used locally

    # Initialize client_task
    client_task: Optional[asyncio.Task[Any]] = None

    try:
        # Send existing messages to new client
        display_messages = shared_state.get_display_messages()
        for msg in display_messages:
            message_data = {
                "type": "message",
                "role": msg["role"],
                "content": strip_name_info_for_display(msg["content"], msg["role"]),
                "source": msg["source"],
            }
            # Include user_name if present
            user_name = msg.get("user_name")
            if user_name is not None:
                message_data["user_name"] = user_name

            await websocket.send_text(json.dumps(message_data))

        # Start background task to handle per-client message processing
        client_task = asyncio.create_task(handle_cli_to_web_messages(websocket))

        # Handle incoming messages from web client
        async for data in websocket.iter_text():
            try:
                message = json.loads(data)
                if message["type"] == "user_message":
                    content = message["content"]
                    user_name = message.get("user_name")
                    await handle_web_user_message(content, websocket, user_name)
                elif message["type"] == "chunk_ack":
                    # Handle chunk acknowledgment for flow control
                    seq_id = message.get("seq_id")
                    if seq_id and hasattr(websocket, "_ack_event"):
                        websocket._last_ack_seq = seq_id  # type: ignore
                        websocket._ack_event.set()  # type: ignore
            except json.JSONDecodeError:
                await websocket.send_text(
                    json.dumps({"type": "error", "content": "Invalid message format"})
                )

    except WebSocketDisconnect:
        pass
    except Exception:
        # Silent web errors - don't clutter CLI
        pass
    finally:
        shared_state.remove_web_client(websocket)
        if client_task is not None:
            client_task.cancel()

        # Clean up session tracking
        if session_id and session_id in session_websockets:
            session_websockets[session_id].discard(websocket)
            if not session_websockets[session_id]:  # Remove empty sets
                del session_websockets[session_id]


async def broadcast_cli_messages():
    """Broadcast messages from CLI to all connected web clients"""
    try:
        while not shared_state.shutdown_event.is_set():
            try:
                # Get message from shared CLI-to-web queue
                msg_type, content = shared_state.cli_to_web_queue.get_nowait()

                # Broadcast to all connected clients' individual queues
                clients_to_remove: List[WebSocket] = []
                for websocket in shared_state.web_clients.copy():
                    try:
                        if hasattr(websocket, "_broadcast_queue"):
                            await websocket._broadcast_queue.put(  # type: ignore
                                (msg_type, content)
                            )
                    except Exception:
                        # Client disconnected, mark for removal
                        clients_to_remove.append(websocket)

                # Clean up disconnected clients
                for client in clients_to_remove:
                    shared_state.web_clients.discard(client)

            except Empty:
                # No messages in shared queue
                pass

            await asyncio.sleep(0.001)  # Check every 1ms for broadcasting

    except asyncio.CancelledError:
        pass


async def handle_cli_to_web_messages(websocket: WebSocket):
    """Handle messages from CLI to web clients"""
    try:
        while not shared_state.shutdown_event.is_set():
            try:
                # Check for messages from CLI (non-blocking) - using per-client queue
                try:
                    # Get message from per-client broadcast queue
                    msg_type: str
                    content: Any
                    msg_type, content = await asyncio.wait_for(
                        websocket._broadcast_queue.get(), timeout=0.01  # type: ignore
                    )

                    if msg_type == "chunk":
                        try:
                            # Send chunk with sequence ID for flow control
                            seq_id = websocket._next_seq_id  # type: ignore
                            websocket._next_seq_id += 1  # type: ignore

                            await websocket.send_text(
                                json.dumps(
                                    {
                                        "type": "chunk",
                                        "content": content,
                                        "seq_id": seq_id,
                                    }
                                )
                            )

                            # Wait for ACK before sending next chunk
                            websocket._ack_event.clear()  # type: ignore
                            try:
                                await asyncio.wait_for(
                                    websocket._ack_event.wait(),  # type: ignore
                                    timeout=5.0,
                                )
                                # Verify we got the right ACK
                                if websocket._last_ack_seq != seq_id:  # type: ignore
                                    # ACK sequence mismatch - connection issue
                                    break
                            except asyncio.TimeoutError:
                                # ACK timeout - client not responding
                                break

                        except Exception:
                            # Failed to send chunk - connection issue
                            break
                    elif msg_type == "assistant_complete":
                        # Signal end of streaming - finalize current message
                        await websocket.send_text(
                            json.dumps(
                                {
                                    "type": "stream_complete",
                                    "role": content["role"],
                                    "content": content["content"],
                                    "source": content["source"],
                                }
                            )
                        )
                    elif msg_type == "user_message":
                        # Debug line removed
                        content_dict: Dict[str, Any] = (
                            dict(content)  # type: ignore
                            if isinstance(content, dict)
                            else {}
                        )
                        message_data: Dict[str, Any] = {
                            "type": "message",
                            "role": str(content_dict.get("role", "")),
                            "content": strip_name_info_for_display(
                                str(content_dict.get("content", "")),
                                str(content_dict.get("role", "")),
                            ),
                            "source": str(content_dict.get("source", "")),
                        }
                        # Include user_name if present
                        if "user_name" in content_dict:
                            message_data["user_name"] = content_dict["user_name"]

                        await websocket.send_text(json.dumps(message_data))
                    elif msg_type == "error":
                        # Just send error to web clients - state already managed by CLI
                        await websocket.send_text(
                            json.dumps({"type": "error", "content": content["content"]})
                        )

                except asyncio.TimeoutError:
                    # No messages in per-client queue, continue
                    pass

            except Exception:
                # WebSocket error - exit loop
                break

            await asyncio.sleep(0.005)  # Check every 5ms for per-client processing

    except asyncio.CancelledError:
        pass


async def handle_web_user_message(
    content: str, websocket: WebSocket, user_name: Optional[str] = None
):
    """Process user message from web interface"""
    try:
        # Don't add to shared_state here - let CLI handle it as single source of truth

        # Send user message back to sender and broadcast to all web clients
        user_message_data = {
            "type": "message",
            "role": "user",
            "content": content,
            "source": "web",
        }

        # Include username if provided
        if user_name:
            user_message_data["user_name"] = user_name

        # Send to current client
        await websocket.send_text(json.dumps(user_message_data))

        # Broadcast to other web clients
        await broadcast_to_other_clients(websocket, user_message_data)

        # Send message to CLI for processing with user name
        if user_name:
            shared_state.web_to_cli_queue.put(
                ("user", {"content": content, "user_name": user_name})
            )
        else:
            shared_state.web_to_cli_queue.put(("user", content))

    except Exception:
        # Silent web errors - don't clutter CLI
        pass


async def broadcast_to_other_clients(sender_ws: WebSocket, message: Dict[str, Any]):
    """Broadcast message to all web clients except sender"""
    disconnected: Set[WebSocket] = set()
    for ws in shared_state.web_clients:
        if ws != sender_ws:
            try:
                await ws.send_text(json.dumps(message))
            except Exception:
                disconnected.add(ws)

    # Clean up disconnected clients
    for ws in disconnected:
        shared_state.remove_web_client(ws)


def run_web_server(host: str = "0.0.0.0", port: int = 8000):
    """Run the web server in a background thread"""

    def start_server():
        config = uvicorn.Config(
            app,
            host=host,
            port=port,
            log_level="warning",  # Reduce noise
        )
        server = uvicorn.Server(config)
        asyncio.run(server.serve())

    thread = threading.Thread(target=start_server, daemon=True)
    thread.start()
    return thread
