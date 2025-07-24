// WebSocket connection and chat functionality
class RAGChat {
    constructor() {
        this.ws = null;
        this.reconnectAttempts = 0;
        this.maxReconnectAttempts = 5;
        this.reconnectDelay = 1000;
        this.currentMessage = null;
        this.sessionId = null;
        this.scrollPending = false;
        
        this.initializeElements();
        this.initializeAuth();
    }
    
    initializeElements() {
        this.chatMessages = document.getElementById('chat-messages');
        this.nameInput = document.getElementById('name-input');
        this.messageInput = document.getElementById('message-input');
        this.sendButton = document.getElementById('send-button');
        this.connectionStatus = document.getElementById('connection-status');
        
        // Auth elements
        this.authScreen = document.getElementById('auth-screen');
        this.chatScreen = document.getElementById('chat-screen');
        this.passwordInput = document.getElementById('password-input');
        this.loginButton = document.getElementById('login-button');
        this.logoutButton = document.getElementById('logout-button');
        this.authError = document.getElementById('auth-error');
        
        // Load saved username from localStorage
        const savedName = localStorage.getItem('userName');
        if (savedName) {
            this.nameInput.value = savedName;
        }
    }
    
    async initializeAuth() {
        if (!window.passwordRequired) {
            // No password required, go straight to chat
            this.showChatScreen();
            this.connectWebSocket();
            this.setupEventListeners();
            return;
        }
        
        // Check if we have a valid session
        const savedSessionId = localStorage.getItem('sessionId');
        if (savedSessionId) {
            const isValid = await this.validateSession(savedSessionId);
            if (isValid) {
                this.sessionId = savedSessionId;
                this.showChatScreen();
                this.connectWebSocket();
                this.setupEventListeners();
                return;
            } else {
                localStorage.removeItem('sessionId');
            }
        }
        
        // Show authentication screen
        this.showAuthScreen();
        this.setupAuthListeners();
    }
    
    async validateSession(sessionId) {
        try {
            const response = await fetch('/auth/validate', {
                headers: {
                    'X-Session-ID': sessionId
                }
            });
            const data = await response.json();
            return data.valid;
        } catch (error) {
            console.error('Session validation failed:', error);
            return false;
        }
    }
    
    showAuthScreen() {
        this.authScreen.style.display = 'flex';
        this.chatScreen.style.display = 'none';
        this.passwordInput.focus();
    }
    
    showChatScreen() {
        this.authScreen.style.display = 'none';
        this.chatScreen.style.display = 'flex';
        if (window.passwordRequired) {
            this.logoutButton.style.display = 'block';
        }
        // Focus message input when chat screen is shown
        setTimeout(() => this.messageInput.focus(), 100);
    }
    
    setupAuthListeners() {
        this.loginButton.addEventListener('click', () => this.handleLogin());
        this.passwordInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') {
                this.handleLogin();
            }
        });
    }
    
    async handleLogin() {
        const password = this.passwordInput.value.trim();
        if (!password) {
            this.showAuthError('Please enter a password');
            return;
        }
        
        this.loginButton.disabled = true;
        this.loginButton.textContent = 'Logging in...';
        this.hideAuthError();
        
        try {
            const response = await fetch('/auth/login', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ password })
            });
            
            if (response.ok) {
                const data = await response.json();
                this.sessionId = data.session_id;
                localStorage.setItem('sessionId', this.sessionId);
                
                this.showChatScreen();
                this.connectWebSocket();
                this.setupEventListeners();
            } else {
                const error = await response.json();
                this.showAuthError(error.detail || 'Authentication failed');
            }
        } catch (error) {
            this.showAuthError('Network error. Please try again.');
        } finally {
            this.loginButton.disabled = false;
            this.loginButton.textContent = 'Login';
            this.passwordInput.value = '';
        }
    }
    
    async handleLogout() {
        try {
            await fetch('/auth/logout', {
                method: 'POST',
                headers: {
                    'X-Session-ID': this.sessionId
                }
            });
        } catch (error) {
            console.error('Logout request failed:', error);
        }
        
        // Clean up regardless of server response
        localStorage.removeItem('sessionId');
        this.sessionId = null;
        
        if (this.ws) {
            this.ws.close();
            // Give server time to process WebSocket cleanup
            await new Promise(resolve => setTimeout(resolve, 100));
        }
        
        this.showAuthScreen();
    }
    
    showAuthError(message) {
        this.authError.textContent = message;
        this.authError.style.display = 'block';
    }
    
    hideAuthError() {
        this.authError.style.display = 'none';
    }
    
    connectWebSocket() {
        const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        let wsUrl = `${wsProtocol}//${window.location.host}/ws`;
        
        // Add session ID if password is required
        if (window.passwordRequired && this.sessionId) {
            wsUrl += `?session_id=${this.sessionId}`;
        }
        
        try {
            this.ws = new WebSocket(wsUrl);
            this.setupWebSocketHandlers();
        } catch (error) {
            console.error('WebSocket connection failed:', error);
            this.handleConnectionError();
        }
    }
    
    setupWebSocketHandlers() {
        this.ws.onopen = () => {
            console.log('WebSocket connected');
            this.connectionStatus.textContent = '🟢 Connected';
            this.connectionStatus.classList.add('connected');
            this.nameInput.disabled = false;
            this.messageInput.disabled = false;
            this.sendButton.disabled = false;
            this.reconnectAttempts = 0;
        };
        
        this.ws.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                this.handleWebSocketMessage(data);
            } catch (error) {
                console.error('Failed to parse WebSocket message:', error);
            }
        };
        
        this.ws.onclose = (event) => {
            console.log('WebSocket closed:', event.code, event.reason);
            this.handleConnectionError();
        };
        
        this.ws.onerror = (error) => {
            console.error('WebSocket error:', error);
            this.handleConnectionError();
        };
    }
    
    handleWebSocketMessage(data) {
        switch (data.type) {
            case 'message':
                // Finish any current streaming message before adding new one
                this.finishCurrentMessage();
                this.addMessage(data.role, data.content, data.source, data.user_name);
                break;
                
            case 'chunk':
                this.appendToCurrentMessage(data.content);
                // Send ACK back to server for flow control
                if (data.seq_id && this.ws && this.ws.readyState === WebSocket.OPEN) {
                    this.ws.send(JSON.stringify({
                        type: 'chunk_ack',
                        seq_id: data.seq_id
                    }));
                }
                break;
                
            case 'stream_complete':
                // Just finish the current streaming message
                this.finishCurrentMessage();
                // Ensure final scroll after stream completion
                this.scrollToBottom();
                break;
                
            case 'error':
                this.addErrorMessage(data.content);
                break;
                
            default:
                console.log('Unknown message type:', data.type);
        }
    }
    
    handleConnectionError() {
        this.connectionStatus.textContent = '🔴 Disconnected';
        this.connectionStatus.classList.remove('connected');
        this.nameInput.disabled = true;
        this.messageInput.disabled = true;
        this.sendButton.disabled = true;
        
        if (this.reconnectAttempts < this.maxReconnectAttempts) {
            this.reconnectAttempts++;
            console.log(`Reconnecting... (${this.reconnectAttempts}/${this.maxReconnectAttempts})`);
            setTimeout(() => this.connectWebSocket(), this.reconnectDelay);
            this.reconnectDelay = Math.min(this.reconnectDelay * 1.5, 10000);
        } else {
            this.connectionStatus.textContent = '🔴 Connection failed';
        }
    }
    
    setupEventListeners() {
        this.sendButton.addEventListener('click', () => this.sendMessage());
        
        this.messageInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                this.sendMessage();
            }
        });
        
        // Auto-resize input
        this.messageInput.addEventListener('input', (e) => {
            e.target.style.height = 'auto';
            e.target.style.height = Math.min(e.target.scrollHeight, 120) + 'px';
        });
        
        // Save username to localStorage when changed or unfocused
        this.nameInput.addEventListener('input', () => {
            this.saveUserName();
        });
        
        this.nameInput.addEventListener('blur', () => {
            this.saveUserName();
        });
        
        // Logout button (always set up, even if not visible initially)
        this.logoutButton.addEventListener('click', () => this.handleLogout());
    }
    
    saveUserName() {
        const userName = this.nameInput.value.trim();
        if (userName) {
            localStorage.setItem('userName', userName);
        } else {
            // If empty, remove from localStorage and reset to default
            localStorage.removeItem('userName');
            this.nameInput.value = 'Anonymous';
        }
    }
    
    sendMessage() {
        const content = this.messageInput.value.trim();
        const userName = this.nameInput.value.trim();
        
        if (!content || !this.ws || this.ws.readyState !== WebSocket.OPEN) {
            return;
        }
        
        // Finish any current streaming message before sending new one
        this.finishCurrentMessage();
        
        // Send message to server with user name (always include, even if "Anonymous")
        const messageData = {
            type: 'user_message',
            content: content,
            user_name: userName || "Anonymous"
        };
        
        this.ws.send(JSON.stringify(messageData));
        
        // Clear message input
        this.messageInput.value = '';
        this.messageInput.style.height = 'auto';
        
        // Disable inputs temporarily
        this.nameInput.disabled = true;
        this.messageInput.disabled = true;
        this.sendButton.disabled = true;
        
        // Re-enable after short delay
        setTimeout(() => {
            if (this.ws && this.ws.readyState === WebSocket.OPEN) {
                this.nameInput.disabled = false;
                this.messageInput.disabled = false;
                this.sendButton.disabled = false;
                this.messageInput.focus();
            }
        }, 500);
    }
    
    addMessage(role, content, source = 'web', userName = null) {
        // Clear any existing current message when starting a new user message
        if (role === 'user') {
            this.finishCurrentMessage();
        }
        
        const messageDiv = document.createElement('div');
        messageDiv.className = `message ${role}-message`;
        
        if (source === 'cli') {
            messageDiv.classList.add('from-cli');
        }
        
        const headerDiv = document.createElement('div');
        headerDiv.className = 'message-header';
        
        let emoji, label;
        switch (role) {
            case 'user':
                emoji = source === 'cli' ? '💻' : '🗨️';
                if (source === 'cli') {
                    label = userName || 'CLI User';
                } else {
                    // Check if this message is from the current user
                    const currentUserName = this.nameInput.value.trim() || 'Anonymous';
                    if (userName && userName === currentUserName) {
                        label = 'You';
                    } else if (userName) {
                        label = userName;
                    } else {
                        label = 'You';  // Default for backward compatibility
                    }
                }
                break;
            case 'assistant':
                emoji = '🤖';
                label = 'Assistant';
                break;
            case 'system':
                emoji = '⚙️';
                label = 'System';
                break;
            default:
                emoji = '💬';
                label = role;
        }
        
        headerDiv.innerHTML = `${emoji} <strong>${label}</strong>`;
        
        const contentDiv = document.createElement('div');
        contentDiv.className = 'message-content';
        contentDiv.textContent = content;
        
        messageDiv.appendChild(headerDiv);
        messageDiv.appendChild(contentDiv);
        
        this.chatMessages.appendChild(messageDiv);
        this.scrollToBottom();
        
        // Store reference if this is an assistant message for streaming
        if (role === 'assistant') {
            this.currentMessage = contentDiv;
            messageDiv.classList.add('streaming');
        }
        
        return messageDiv;
    }
    
    appendToCurrentMessage(chunk) {
        if (this.currentMessage) {
            this.currentMessage.textContent += chunk;
            // Batch scroll updates to reduce DOM reflow
            if (!this.scrollPending) {
                this.scrollPending = true;
                requestAnimationFrame(() => {
                    this.scrollToBottom();
                    this.scrollPending = false;
                });
            }
        } else {
            // Start new assistant message if none exists
            this.finishCurrentMessage(); // Make sure no old messages are lingering
            const messageDiv = this.addMessage('assistant', chunk);
            this.currentMessage = messageDiv.querySelector('.message-content');
            messageDiv.classList.add('streaming');
        }
    }
    
    finishCurrentMessage() {
        if (this.currentMessage) {
            const messageDiv = this.currentMessage.parentElement;
            messageDiv.classList.remove('streaming');
            // Force reflow to ensure the streaming cursor is immediately removed
            messageDiv.offsetHeight;
            this.currentMessage = null;
        }
    }
    
    addErrorMessage(content) {
        const errorDiv = document.createElement('div');
        errorDiv.className = 'error-message';
        errorDiv.innerHTML = `<strong>Error:</strong> ${content}`;
        this.chatMessages.appendChild(errorDiv);
        this.scrollToBottom();
    }
    
    scrollToBottom() {
        this.chatMessages.scrollTop = this.chatMessages.scrollHeight;
    }
}

// Initialize chat when page loads
document.addEventListener('DOMContentLoaded', () => {
    window.ragChat = new RAGChat();
    
    // Focus input field
    document.getElementById('message-input').focus();
});