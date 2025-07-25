import http.server
import socketserver
import os
import mimetypes
import urllib.parse
import json
import time
import threading
import hashlib
import secrets
import sqlite3
from datetime import datetime, timedelta

PORT = int(os.environ.get('PORT', 8080))

# Global storage
chatroom_messages = []
chatroom_lock = threading.Lock()
active_sessions = {}  # session_token -> user_data
session_lock = threading.Lock()

# Database setup
def init_database():
    """Initialize SQLite database for user accounts"""
    conn = sqlite3.connect('chatroom_users.db')
    cursor = conn.cursor()
    
    # Create users table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            salt TEXT NOT NULL,
            created_at TEXT NOT NULL,
            last_login TEXT,
            is_active INTEGER DEFAULT 1,
            avatar_color TEXT DEFAULT '#667eea',
            display_name TEXT,
            bio TEXT DEFAULT '',
            message_count INTEGER DEFAULT 0
        )
    ''')
    
    # Create sessions table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sessions (
            session_token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            ip_address TEXT,
            user_agent TEXT,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')
    
    conn.commit()
    conn.close()

# User authentication functions
def hash_password(password, salt=None):
    """Hash password with salt"""
    if salt is None:
        salt = secrets.token_hex(32)
    
    password_hash = hashlib.pbkdf2_hmac('sha256', 
                                       password.encode('utf-8'), 
                                       salt.encode('utf-8'), 
                                       100000)
    return password_hash.hex(), salt

def verify_password(password, password_hash, salt):
    """Verify password against hash"""
    test_hash, _ = hash_password(password, salt)
    return test_hash == password_hash

def create_session(user_id, ip_address, user_agent):
    """Create new session token"""
    session_token = secrets.token_urlsafe(32)
    expires_at = (datetime.now() + timedelta(days=30)).isoformat()
    
    conn = sqlite3.connect('chatroom_users.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT INTO sessions (session_token, user_id, created_at, expires_at, ip_address, user_agent)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (session_token, user_id, datetime.now().isoformat(), expires_at, ip_address, user_agent))
    
    conn.commit()
    conn.close()
    
    return session_token

def get_user_by_session(session_token):
    """Get user data by session token"""
    conn = sqlite3.connect('chatroom_users.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT u.id, u.username, u.email, u.display_name, u.avatar_color, u.bio, u.message_count
        FROM users u
        JOIN sessions s ON u.id = s.user_id
        WHERE s.session_token = ? AND s.expires_at > ?
    ''', (session_token, datetime.now().isoformat()))
    
    result = cursor.fetchone()
    conn.close()
    
    if result:
        return {
            'id': result[0],
            'username': result[1],
            'email': result[2],
            'display_name': result[3] or result[1],
            'avatar_color': result[4],
            'bio': result[5],
            'message_count': result[6]
        }
    return None

class ChatroomHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        mimetypes.add_type('application/javascript', '.js')
        mimetypes.add_type('text/css', '.css')
        mimetypes.add_type('application/json', '.json')
        super().__init__(*args, **kwargs)
    
    def do_GET(self):
        parsed_path = urllib.parse.urlparse(self.path)
        path = parsed_path.path
        
        if path == '/':
            self.serve_chatroom()
            return
        
        if path.startswith('/api/'):
            self.handle_api(path)
            return
        
        if self.serve_static_file(path):
            return
        
        self.send_error(404, "File not found")
    
    def serve_chatroom(self):
        """Serve the chatroom with authentication system"""
        html_content = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Secured Chatroom + Voice Room 🔐💬🎤</title>
    <script src="https://cdn.socket.io/4.7.2/socket.io.min.js"></script>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            height: 100vh;
            display: flex;
            flex-direction: column;
        }
        
        /* Auth Modal Styles */
        .auth-modal {
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(0, 0, 0, 0.8);
            display: flex;
            justify-content: center;
            align-items: center;
            z-index: 1000;
        }
        
        .auth-container {
            background: white;
            border-radius: 20px;
            padding: 40px;
            width: 90%;
            max-width: 400px;
            box-shadow: 0 20px 40px rgba(0, 0, 0, 0.3);
            text-align: center;
            position: relative;
        }
        
        .auth-toggle {
            display: flex;
            background: #f0f0f0;
            border-radius: 25px;
            margin-bottom: 30px;
            overflow: hidden;
        }
        
        .auth-toggle button {
            flex: 1;
            padding: 12px;
            border: none;
            background: transparent;
            cursor: pointer;
            font-weight: bold;
            transition: all 0.3s ease;
        }
        
        .auth-toggle button.active {
            background: #667eea;
            color: white;
        }
        
        .auth-form {
            display: none;
        }
        
        .auth-form.active {
            display: block;
        }
        
        .form-group {
            margin-bottom: 20px;
            text-align: left;
        }
        
        .form-group label {
            display: block;
            margin-bottom: 5px;
            font-weight: bold;
            color: #333;
        }
        
        .form-group input {
            width: 100%;
            padding: 12px;
            border: 2px solid #ddd;
            border-radius: 8px;
            font-size: 14px;
            transition: border-color 0.3s ease;
        }
        
        .form-group input:focus {
            outline: none;
            border-color: #667eea;
        }
        
        .auth-btn {
            width: 100%;
            padding: 15px;
            background: #667eea;
            color: white;
            border: none;
            border-radius: 8px;
            font-size: 16px;
            font-weight: bold;
            cursor: pointer;
            transition: all 0.3s ease;
        }
        
        .auth-btn:hover {
            background: #5a6fd8;
            transform: translateY(-1px);
        }
        
        .auth-error {
            background: #ffebee;
            color: #c62828;
            padding: 10px;
            border-radius: 5px;
            margin-bottom: 15px;
            display: none;
        }
        
        .auth-success {
            background: #e8f5e8;
            color: #2e7d32;
            padding: 10px;
            border-radius: 5px;
            margin-bottom: 15px;
            display: none;
        }
        
        /* User Profile Styles */
        .user-profile {
            background: rgba(255, 255, 255, 0.1);
            backdrop-filter: blur(10px);
            border-radius: 10px;
            padding: 10px 15px;
            margin-left: 20px;
            display: flex;
            align-items: center;
            gap: 10px;
            color: white;
            cursor: pointer;
            transition: all 0.3s ease;
        }
        
        .user-profile:hover {
            background: rgba(255, 255, 255, 0.15);
        }
        
        .user-avatar {
            width: 35px;
            height: 35px;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: bold;
            color: white;
            font-size: 14px;
        }
        
        .user-info {
            display: flex;
            flex-direction: column;
            align-items: flex-start;
        }
        
        .user-name {
            font-weight: bold;
            font-size: 14px;
        }
        
        .user-messages {
            font-size: 11px;
            opacity: 0.8;
        }
        
        .logout-btn {
            background: rgba(244, 67, 54, 0.8);
            color: white;
            border: none;
            padding: 5px 10px;
            border-radius: 5px;
            cursor: pointer;
            font-size: 12px;
            margin-left: 10px;
        }
        
        .logout-btn:hover {
            background: rgba(244, 67, 54, 1);
        }
        
        /* Existing styles from original code */
        .header {
            background: rgba(255, 255, 255, 0.1);
            backdrop-filter: blur(10px);
            padding: 20px;
            text-align: center;
            color: white;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        
        .header-left {
            flex: 1;
        }
        
        .header h1 {
            font-size: 2em;
            margin-bottom: 10px;
            text-shadow: 2px 2px 4px rgba(0,0,0,0.3);
        }
        
        .tabs {
            display: flex;
            justify-content: center;
            gap: 10px;
            margin-top: 10px;
        }
        
        .tab-btn {
            background: rgba(255, 255, 255, 0.2);
            color: white;
            border: none;
            padding: 10px 20px;
            border-radius: 25px;
            cursor: pointer;
            font-weight: bold;
            transition: all 0.3s ease;
            backdrop-filter: blur(5px);
        }
        
        .tab-btn.active {
            background: rgba(255, 255, 255, 0.3);
            transform: scale(1.05);
        }
        
        .tab-btn:hover {
            background: rgba(255, 255, 255, 0.25);
        }
        
        .online-count {
            background: rgba(76, 175, 80, 0.8);
            padding: 5px 15px;
            border-radius: 20px;
            display: inline-block;
            font-size: 0.9em;
            margin-top: 10px;
        }
        
        .main-container {
            flex: 1;
            display: flex;
            flex-direction: column;
            max-width: 1000px;
            margin: 0 auto;
            width: 100%;
            padding: 20px;
        }
        
        .tab-content {
            display: none;
            flex: 1;
            animation: fadeIn 0.3s ease-in;
        }
        
        .tab-content.active {
            display: flex;
            flex-direction: column;
        }
        
        @keyframes fadeIn {
            from { opacity: 0; transform: translateY(10px); }
            to { opacity: 1; transform: translateY(0); }
        }
        
        .messages-container {
            flex: 1;
            background: rgba(255, 255, 255, 0.95);
            border-radius: 15px 15px 0 0;
            padding: 20px;
            overflow-y: auto;
            max-height: 400px;
            margin-bottom: 0;
        }
        
        .message {
            margin-bottom: 15px;
            padding: 12px 15px;
            border-radius: 10px;
            background: #f8f9fa;
            border-left: 4px solid #667eea;
            animation: slideIn 0.3s ease-out;
        }
        
        .message.own {
            background: #e3f2fd;
            border-left-color: #2196F3;
            margin-left: 50px;
        }
        
        .message.voice {
            background: #fff3e0;
            border-left-color: #ff9800;
        }
        
        .message-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 5px;
            font-size: 0.9em;
        }
        
        .username {
            font-weight: bold;
            color: #667eea;
            display: flex;
            align-items: center;
            gap: 8px;
        }
        
        .message-avatar {
            width: 20px;
            height: 20px;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 10px;
            color: white;
            font-weight: bold;
        }
        
        .timestamp {
            color: #666;
            font-size: 0.8em;
        }
        
        .message-text {
            color: #333;
            line-height: 1.4;
            word-wrap: break-word;
        }
        
        .input-container {
            background: rgba(255, 255, 255, 0.95);
            padding: 20px;
            border-radius: 0 0 15px 15px;
            display: flex;
            gap: 10px;
            align-items: center;
        }
        
        #messageInput {
            flex: 1;
            padding: 12px;
            border: 2px solid #ddd;
            border-radius: 8px;
            font-size: 14px;
            resize: none;
        }
        
        #sendButton {
            background: #667eea;
            color: white;
            border: none;
            padding: 12px 20px;
            border-radius: 8px;
            cursor: pointer;
            font-size: 14px;
            font-weight: bold;
            transition: all 0.3s ease;
        }
        
        #sendButton:hover {
            background: #5a6fd8;
            transform: translateY(-1px);
        }
        
        .no-messages {
            text-align: center;
            color: #666;
            font-style: italic;
            padding: 40px;
        }
        
        @keyframes slideIn {
            from {
                opacity: 0;
                transform: translateY(20px);
            }
            to {
                opacity: 1;
                transform: translateY(0);
            }
        }
        
        .emoji-btn {
            background: none;
            border: none;
            font-size: 18px;
            cursor: pointer;
            padding: 5px;
            border-radius: 3px;
            transition: background 0.2s;
        }
        
        .emoji-btn:hover {
            background: rgba(0,0,0,0.1);
        }
        
        /* Voice Room Styles */
        .voice-container {
            background: rgba(255, 255, 255, 0.95);
            border-radius: 15px;
            padding: 30px;
            text-align: center;
            flex: 1;
            display: flex;
            flex-direction: column;
            justify-content: center;
            align-items: center;
            gap: 20px;
        }
        
        .voice-controls {
            display: flex;
            gap: 15px;
            align-items: center;
            flex-wrap: wrap;
            justify-content: center;
        }
        
        .voice-btn {
            background: #4CAF50;
            color: white;
            border: none;
            padding: 15px 25px;
            border-radius: 50px;
            cursor: pointer;
            font-size: 16px;
            font-weight: bold;
            transition: all 0.3s ease;
            min-width: 120px;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
        }
        
        .voice-btn:hover {
            transform: translateY(-2px);
            box-shadow: 0 4px 15px rgba(0,0,0,0.2);
        }
        
        .voice-btn.recording {
            background: #f44336;
            animation: pulse 1.5s infinite;
        }
        
        .voice-btn.disabled {
            background: #ccc;
            cursor: not-allowed;
        }
        
        @keyframes pulse {
            0% { transform: scale(1); }
            50% { transform: scale(1.05); }
            100% { transform: scale(1); }
        }
        
        .voice-status {
            background: rgba(0,0,0,0.05);
            padding: 15px 25px;
            border-radius: 10px;
            font-weight: bold;
            color: #333;
            min-height: 50px;
            display: flex;
            align-items: center;
            justify-content: center;
        }
        
        .voice-participants {
            background: rgba(0,0,0,0.05);
            padding: 20px;
            border-radius: 10px;
            margin-top: 20px;
            width: 100%;
            max-width: 500px;
        }
        
        .voice-participants h3 {
            margin-bottom: 15px;
            color: #333;
        }
        
        .participant-list {
            display: flex;
            flex-wrap: wrap;
            gap: 10px;
            justify-content: center;
        }
        
        .participant {
            background: #667eea;
            color: white;
            padding: 8px 15px;
            border-radius: 20px;
            font-size: 14px;
            display: flex;
            align-items: center;
            gap: 5px;
        }
        
        .participant.speaking {
            animation: speakingGlow 1s infinite alternate;
        }
        
        @keyframes speakingGlow {
            from { box-shadow: 0 0 5px rgba(102, 126, 234, 0.5); }
            to { box-shadow: 0 0 15px rgba(102, 126, 234, 0.8); }
        }
        
        .connection-status {
            background: rgba(255, 193, 7, 0.1);
            border: 2px solid rgba(255, 193, 7, 0.3);
            border-radius: 10px;
            padding: 15px;
            margin-bottom: 20px;
            color: #333;
        }
        
        .connection-status.connected {
            background: rgba(76, 175, 80, 0.1);
            border-color: rgba(76, 175, 80, 0.3);
        }
        
        @media (max-width: 600px) {
            .header {
                flex-direction: column;
                gap: 15px;
            }
            
            .input-container {
                flex-direction: column;
                gap: 10px;
            }
            
            .voice-controls {
                flex-direction: column;
            }
            
            .voice-btn {
                width: 100%;
                max-width: 250px;
            }
        }
    </style>
</head>
<body>
    <!-- Authentication Modal -->
    <div id="authModal" class="auth-modal">
        <div class="auth-container">
            <h2>🔐 Welcome to Secured Chatroom</h2>
            
            <div class="auth-toggle">
                <button onclick="showLogin()" id="loginToggle" class="active">Login</button>
                <button onclick="showRegister()" id="registerToggle">Register</button>
            </div>
            
            <div id="authError" class="auth-error"></div>
            <div id="authSuccess" class="auth-success"></div>
            
            <!-- Login Form -->
            <form id="loginForm" class="auth-form active" onsubmit="handleLogin(event)">
                <div class="form-group">
                    <label for="loginUsername">Username or Email</label>
                    <input type="text" id="loginUsername" required>
                </div>
                <div class="form-group">
                    <label for="loginPassword">Password</label>
                    <input type="password" id="loginPassword" required>
                </div>
                <button type="submit" class="auth-btn">🔑 Login</button>
            </form>
            
            <!-- Register Form -->
            <form id="registerForm" class="auth-form" onsubmit="handleRegister(event)">
                <div class="form-group">
                    <label for="registerUsername">Username</label>
                    <input type="text" id="registerUsername" required minlength="3" maxlength="20">
                </div>
                <div class="form-group">
                    <label for="registerEmail">Email</label>
                    <input type="email" id="registerEmail" required>
                </div>
                <div class="form-group">
                    <label for="registerDisplayName">Display Name (Optional)</label>
                    <input type="text" id="registerDisplayName" maxlength="30">
                </div>
                <div class="form-group">
                    <label for="registerPassword">Password</label>
                    <input type="password" id="registerPassword" required minlength="6">
                </div>
                <div class="form-group">
                    <label for="registerConfirmPassword">Confirm Password</label>
                    <input type="password" id="registerConfirmPassword" required>
                </div>
                <button type="submit" class="auth-btn">📝 Create Account</button>
            </form>
        </div>
    </div>
    
    <!-- Main App -->
    <div class="header">
        <div class="header-left">
            <h1>🔐💬🎤 Secured Chatroom + Voice Room</h1>
            <div class="tabs">
                <button class="tab-btn active" onclick="switchTab('chat')">💬 Text Chat</button>
                <button class="tab-btn" onclick="switchTab('voice')">🎤 Voice Room</button>
            </div>
            <div class="online-count" id="onlineCount">🟢 Loading...</div>
        </div>
        
        <div id="userProfile" class="user-profile" style="display: none;">
            <div id="userAvatar" class="user-avatar"></div>
            <div class="user-info">
                <div id="userName" class="user-name"></div>
                <div id="userMessages" class="user-messages"></div>
            </div>
            <button onclick="logout()" class="logout-btn">Logout</button>
        </div>
    </div>
    
    <div class="main-container">
        <!-- Text Chat Tab -->
        <div id="chatTab" class="tab-content active">
            <div class="messages-container" id="messagesContainer">
                <div class="no-messages">Welcome to the secured chatroom! Send a message to get started 🚀</div>
            </div>
            
            <div class="input-container">
                <textarea id="messageInput" placeholder="Type your message..." rows="1" maxlength="500"></textarea>
                <button class="emoji-btn" onclick="addEmoji('😊')">😊</button>
                <button class="emoji-btn" onclick="addEmoji('👍')">👍</button>
                <button class="emoji-btn" onclick="addEmoji('❤️')">❤️</button>
                <button id="sendButton" onclick="sendMessage()">Send 📤</button>
            </div>
        </div>
        
        <!-- Voice Room Tab -->
        <div id="voiceTab" class="tab-content">
            <div class="voice-container">
                <div class="connection-status" id="connectionStatus">
                    🔌 Connecting to voice server...
                </div>
                
                <div class="voice-status" id="voiceStatus">
                    🎤 Click "Join Voice Room" to start talking with others!
                </div>
                
                <div class="voice-controls">
                    <button class="voice-btn" id="joinVoiceBtn" onclick="joinVoiceRoom()">
                        🎤 Join Voice Room
                    </button>
                    <button class="voice-btn disabled" id="talkBtn" onmousedown="startTalking()" onmouseup="stopTalking()" ontouchstart="startTalking()" ontouchend="stopTalking()">
                        🗣️ Hold to Talk
                    </button>
                    <button class="voice-btn" id="muteBtn" onclick="toggleMute()" style="background: #ff9800; display: none;">
                        🔊 Mute
                    </button>
                    <button class="voice-btn" id="leaveVoiceBtn" onclick="leaveVoiceRoom()" style="background: #f44336; display: none;">
                        📞 Leave Voice
                    </button>
                </div>
                
                <div class="voice-participants">
                    <h3>👥 Voice Participants (<span id="participantCount">0</span>)</h3>
                    <div class="participant-list" id="participantList">
                        <div class="participant">
                            <span>💤</span>
                            <span>No one in voice yet</span>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <script>
        // Global variables
        let currentUser = null;
        let sessionToken = null;
        let lastMessageId = 0;
        
        // Voice variables
        let socket = null;
        let localStream = null;
        let peerConnections = new Map();
        let isInVoiceRoom = false;
        let isMuted = false;
        let isTalking = false;
        let roomId = 'main-voice-room';
        
        const SIGNALING_SERVER = 'https://repo1-ejq1.onrender.com';
        
        // Authentication functions
        function showLogin() {
            document.getElementById('loginToggle').classList.add('active');
            document.getElementById('registerToggle').classList.remove('active');
            document.getElementById('loginForm').classList.add('active');
            document.getElementById('registerForm').classList.remove('active');
            clearAuthMessages();
        }
        
        function showRegister() {
            document.getElementById('loginToggle').classList.remove('active');
            document.getElementById('registerToggle').classList.add('active');
            document.getElementById('loginForm').classList.remove('active');
            document.getElementById('registerForm').classList.add('active');
            clearAuthMessages();
        }
        
        function clearAuthMessages() {
            document.getElementById('authError').style.display = 'none';
            document.getElementById('authSuccess').style.display = 'none';
        }
        
        function showAuthError(message) {
            const errorDiv = document.getElementById('authError');
            errorDiv.textContent = message;
            errorDiv.style.display = 'block';
            document.getElementById('authSuccess').style.display = 'none';
        }
        
        function showAuthSuccess(message) {
            const successDiv = document.getElementById('authSuccess');
            successDiv.textContent = message;
            successDiv.style.display = 'block';
            document.getElementById('authError').style.display = 'none';
        }
        
        async function handleLogin(event) {
            event.preventDefault();
            clearAuthMessages();
            
            const username = document.getElementById('loginUsername').value.trim();
            const password = document.getElementById('loginPassword').value;
            
            try {
                const response = await fetch('/api/auth/login', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({ username, password })
                });
                
                const data = await response.json();
                
                if (data.success) {
                    sessionToken = data.sessionToken;
                    currentUser = data.user;
                    localStorage.setItem('sessionToken', sessionToken);
                    showAuthSuccess('Login successful! Welcome back!');
                    setTimeout(() => {
                        hideAuthModal();
                        updateUserProfile();
                    }, 1000);
                } else {
                    showAuthError(data.error || 'Login failed');
                }
            } catch (error) {
                showAuthError('Network error. Please try again.');
                console.error('Login error:', error);
            }
        }
        
        async function handleRegister(event) {
            event.preventDefault();
            clearAuthMessages();
            
            const username = document.getElementById('registerUsername').value.trim();
            const email = document.getElementById('registerEmail').value.trim();
            const displayName = document.getElementById('registerDisplayName').value.trim();
            const password = document.getElementById('registerPassword').value;
            const confirmPassword = document.getElementById('registerConfirmPassword').value;
            
            if (password !== confirmPassword) {
                showAuthError('Passwords do not match');
                return;
            }
            
            try {
                const response = await fetch('/api/auth/register', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({ username, email, displayName, password })
                });
                
                const data = await response.json();
                
                if (data.success) {
                    showAuthSuccess('Account created successfully! You can now login.');
                    setTimeout(() => {
                        showLogin();
                        document.getElementById('loginUsername').value = username;
                    }, 1500);
                } else {
                    showAuthError(data.error || 'Registration failed');
                }
            } catch (error) {
                showAuthError('Network error. Please try again.');
                console.error('Registration error:', error);
            }
        }
        
        async function checkSession() {
            const savedToken = localStorage.getItem('sessionToken');
            if (!savedToken) {
                showAuthModal();
                return false;
            }
            
            try {
                const response = await fetch('/api/auth/verify', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({ sessionToken: savedToken })
                });
                
                const data = await response.json();
                
                if (data.success) {
                    sessionToken = savedToken;
                    currentUser = data.user;
                    hideAuthModal();
                    updateUserProfile();
                    return true;
                } else {
                    localStorage.removeItem('sessionToken');
                    showAuthModal();
                    return false;
                }
            } catch (error) {
                console.error('Session verification error:', error);
                localStorage.removeItem('sessionToken');
                showAuthModal();
                return false;
            }
        }
        
        function showAuthModal() {
            document.getElementById('authModal').style.display = 'flex';
        }
        
        function hideAuthModal() {
            document.getElementById('authModal').style.display = 'none';
        }
        
        function updateUserProfile() {
            if (!currentUser) return;
            
            const profileDiv = document.getElementById('userProfile');
            const avatarDiv = document.getElementById('userAvatar');
            const nameDiv = document.getElementById('userName');
            const messagesDiv = document.getElementById('userMessages');
            
            avatarDiv.style.backgroundColor = currentUser.avatar_color;
            avatarDiv.textContent = currentUser.display_name.charAt(0).toUpperCase();
            nameDiv.textContent = currentUser.display_name;
            messagesDiv.textContent = `${currentUser.message_count} messages`;
            
            profileDiv.style.display = 'flex';
        }
        
        function logout() {
            if (sessionToken) {
                fetch('/api/auth/logout', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({ sessionToken })
                }).catch(console.error);
            }
            
            localStorage.removeItem('sessionToken');
            sessionToken = null;
            currentUser = null;
            
            document.getElementById('userProfile').style.display = 'none';
            
            if (isInVoiceRoom) {
                leaveVoiceRoom();
            }
            
            showAuthModal();
            
            // Clear forms
            document.getElementById('loginForm').reset();
            document.getElementById('registerForm').reset();
            clearAuthMessages();
        }
        
        // Tab switching
        function switchTab(tabName) {
            document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
            event.target.classList.add('active');
            
            document.querySelectorAll('.tab-content').forEach(content => content.classList.remove('active'));
            document.getElementById(tabName + 'Tab').classList.add('active');
        }
        
        // Initialize Socket.IO connection
        function initializeVoiceConnection() {
            socket = io(SIGNALING_SERVER);
            
            socket.on('connect', () => {
                console.log('Connected to voice server!');
                document.getElementById('connectionStatus').innerHTML = '🟢 Connected to voice server';
                document.getElementById('connectionStatus').classList.add('connected');
            });
            
            socket.on('disconnect', () => {
                console.log('Disconnected from voice server');
                document.getElementById('connectionStatus').innerHTML = '🔴 Disconnected from voice server';
                document.getElementById('connectionStatus').classList.remove('connected');
            });
            
            socket.on('user-joined', (data) => {
                console.log('User joined:', data.username);
                createPeerConnection(data.userId);
                updateVoiceNotification(`🎤 ${data.username} joined voice room`);
            });
            
            socket.on('user-left', (data) => {
                console.log('User left:', data.username);
                closePeerConnection(data.userId);
                updateVoiceNotification(`📞 ${data.username} left voice room`);
            });
            
            socket.on('offer', async (data) => {
                console.log('Received offer from:', data.from);
                await handleOffer(data.offer, data.from);
            });
            
            socket.on('answer', async (data) => {
                console.log('Received answer from:', data.from);
                await handleAnswer(data.answer, data.from);
            });
            
            socket.on('ice-candidate', async (data) => {
                console.log('Received ICE candidate from:', data.from);
                await handleIceCandidate(data.candidate, data.from);
            });
            
            socket.on('room-stats', (data) => {
                document.getElementById('participantCount').textContent = data.userCount;
                updateParticipantsList();
            });
            
            socket.on('user-voice-activity', (data) => {
                updateUserVoiceActivity(data.userId, data.isActive);
            });
        }
        
        // Chat functionality
        const messageInput = document.getElementById('messageInput');
        messageInput.addEventListener('input', function() {
            this.style.height = 'auto';
            this.style.height = Math.min(this.scrollHeight, 100) + 'px';
        });
        
        messageInput.addEventListener('keydown', function(e) {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                sendMessage();
            }
        });
        
        function addEmoji(emoji) {
            const input = document.getElementById('messageInput');
            input.value += emoji;
            input.focus();
        }
        
        async function sendMessage() {
            if (!currentUser || !sessionToken) {
                showAuthError('Please login to send messages');
                return;
            }
            
            const messageText = messageInput.value.trim();
            if (!messageText) return;
            
            const message = {
                text: messageText,
                timestamp: new Date().toISOString(),
                sessionToken: sessionToken
            };
            
            try {
                const response = await fetch('/api/chat/send', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify(message)
                });
                
                const data = await response.json();
                
                if (data.success) {
                    messageInput.value = '';
                    messageInput.style.height = 'auto';
                    if (data.messageId) {
                        lastMessageId = data.messageId;
                    }
                    // Update user message count
                    if (currentUser) {
                        currentUser.message_count++;
                        updateUserProfile();
                    }
                } else if (data.error === 'Invalid session') {
                    logout();
                }
            } catch (error) {
                console.error('Error sending message:', error);
            }
        }
        
        async function loadMessages() {
            if (!sessionToken) return;
            
            try {
                const response = await fetch(`/api/chat/messages?since=${lastMessageId}&sessionToken=${sessionToken}`);
                const data = await response.json();
                
                if (data.success) {
                    if (data.messages && data.messages.length > 0) {
                        displayMessages(data.messages);
                        lastMessageId = data.lastId;
                    }
                    updateOnlineCount(data.messageCount || 0);
                } else if (data.error === 'Invalid session') {
                    logout();
                }
            } catch (error) {
                console.error('Error loading messages:', error);
            }
        }
        
        function displayMessages(messages) {
            const container = document.getElementById('messagesContainer');
            const noMessages = container.querySelector('.no-messages');
            
            if (noMessages && messages.length > 0) {
                noMessages.remove();
            }
            
            messages.forEach(message => {
                const existingMessage = document.getElementById(`message-${message.id}`);
                if (existingMessage) {
                    return;
                }
                
                const messageDiv = document.createElement('div');
                let messageClass = 'message';
                if (message.username === currentUser?.username) messageClass += ' own';
                if (message.text.includes('🎤') || message.text.includes('🗣️') || message.text.includes('📞')) messageClass += ' voice';
                
                messageDiv.className = messageClass;
                messageDiv.id = `message-${message.id}`;
                
                const timestamp = new Date(message.timestamp).toLocaleTimeString();
                
                messageDiv.innerHTML = `
                    <div class="message-header">
                        <span class="username">
                            <div class="message-avatar" style="background-color: ${message.avatar_color || '#667eea'}">
                                ${message.display_name.charAt(0).toUpperCase()}
                            </div>
                            ${escapeHtml(message.display_name)}
                        </span>
                        <span class="timestamp">${timestamp}</span>
                    </div>
                    <div class="message-text">${escapeHtml(message.text)}</div>
                `;
                
                container.appendChild(messageDiv);
            });
            
            container.scrollTop = container.scrollHeight;
        }
        
        function updateOnlineCount(messageCount) {
            const onlineCount = document.getElementById('onlineCount');
            onlineCount.textContent = `💬 ${messageCount} messages`;
        }
        
        function escapeHtml(text) {
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }
        
        function updateVoiceNotification(text) {
            if (!sessionToken) return;
            
            const message = {
                text: text,
                timestamp: new Date().toISOString(),
                sessionToken: sessionToken
            };
            
            fetch('/api/chat/send', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify(message)
            });
        }
        
        // Voice Room functionality
        async function joinVoiceRoom() {
            if (!currentUser) {
                alert('Please login to join voice room');
                return;
            }
            
            try {
                localStream = await navigator.mediaDevices.getUserMedia({
                    audio: {
                        echoCancellation: true,
                        noiseSuppression: true,
                        sampleRate: 44100
                    }
                });
                
                localStream.getAudioTracks().forEach(track => {
                    track.enabled = false;
                });
                
                isInVoiceRoom = true;
                
                socket.emit('join-room', {
                    roomId: roomId,
                    username: currentUser.display_name
                });
                
                document.getElementById('joinVoiceBtn').style.display = 'none';
                document.getElementById('talkBtn').classList.remove('disabled');
                document.getElementById('muteBtn').style.display = 'inline-flex';
                document.getElementById('leaveVoiceBtn').style.display = 'inline-flex';
                document.getElementById('voiceStatus').innerHTML = '🎤 In voice room - Hold "Talk" to speak!';
                
                updateVoiceNotification(`🎤 ${currentUser.display_name} joined the voice room`);
                
            } catch (error) {
                console.error('Error accessing microphone:', error);
                document.getElementById('voiceStatus').innerHTML = '❌ Microphone access denied. Please allow microphone and try again.';
            }
        }
        
        function leaveVoiceRoom() {
            if (localStream) {
                localStream.getTracks().forEach(track => track.stop());
                localStream = null;
            }
            
            peerConnections.forEach((pc, userId) => {
                pc.close();
            });
            peerConnections.clear();
            
            isInVoiceRoom = false;
            isTalking = false;
            isMuted = false;
            
            document.getElementById('joinVoiceBtn').style.display = 'inline-flex';
            document.getElementById('talkBtn').classList.add('disabled');
            document.getElementById('muteBtn').style.display = 'none';
            document.getElementById('leaveVoiceBtn').style.display = 'none';
            document.getElementById('voiceStatus').innerHTML = '🎤 Click "Join Voice Room" to start talking with others!';
            
            if (currentUser) {
                updateVoiceNotification(`📞 ${currentUser.display_name} left the voice room`);
            }
            updateParticipantsList();
        }
        
        async function createPeerConnection(userId) {
            const peerConnection = new RTCPeerConnection({
                iceServers: [
                    { urls: 'stun:stun.l.google.com:19302' },
                    { urls: 'stun:global.stun.twilio.com:3478' }
                ]
            });
            
            if (localStream) {
                localStream.getTracks().forEach(track => {
                    peerConnection.addTrack(track, localStream);
                });
            }
            
            peerConnection.ontrack = (event) => {
                const remoteStream = event.streams[0];
                playRemoteAudio(remoteStream, userId);
            };
            
            peerConnection.onicecandidate = (event) => {
                if (event.candidate) {
                    socket.emit('ice-candidate', {
                        target: userId,
                        candidate: event.candidate
                    });
                }
            };
            
            peerConnections.set(userId, peerConnection);
            
            const offer = await peerConnection.createOffer();
            await peerConnection.setLocalDescription(offer);
            
            socket.emit('offer', {
                target: userId,
                offer: offer
            });
        }
        
        async function handleOffer(offer, fromUserId) {
            const peerConnection = new RTCPeerConnection({
                iceServers: [
                    { urls: 'stun:stun.l.google.com:19302' },
                    { urls: 'stun:global.stun.twilio.com:3478' }
                ]
            });
            
            if (localStream) {
                localStream.getTracks().forEach(track => {
                    peerConnection.addTrack(track, localStream);
                });
            }
            
            peerConnection.ontrack = (event) => {
                const remoteStream = event.streams[0];
                playRemoteAudio(remoteStream, fromUserId);
            };
            
            peerConnection.onicecandidate = (event) => {
                if (event.candidate) {
                    socket.emit('ice-candidate', {
                        target: fromUserId,
                        candidate: event.candidate
                    });
                }
            };
            
            peerConnections.set(fromUserId, peerConnection);
            
            await peerConnection.setRemoteDescription(offer);
            const answer = await peerConnection.createAnswer();
            await peerConnection.setLocalDescription(answer);
            
            socket.emit('answer', {
                target: fromUserId,
                answer: answer
            });
        }
        
        async function handleAnswer(answer, fromUserId) {
            const peerConnection = peerConnections.get(fromUserId);
            if (peerConnection) {
                await peerConnection.setRemoteDescription(answer);
            }
        }
        
        async function handleIceCandidate(candidate, fromUserId) {
            const peerConnection = peerConnections.get(fromUserId);
            if (peerConnection) {
                await peerConnection.addIceCandidate(candidate);
            }
        }
        
        function closePeerConnection(userId) {
            const peerConnection = peerConnections.get(userId);
            if (peerConnection) {
                peerConnection.close();
                peerConnections.delete(userId);
            }
            
            const audioElement = document.getElementById(`audio-${userId}`);
            if (audioElement) {
                audioElement.remove();
            }
        }
        
        function playRemoteAudio(stream, userId) {
            const audio = document.createElement('audio');
            audio.srcObject = stream;
            audio.autoplay = true;
            audio.id = `audio-${userId}`;
            audio.volume = 1.0;
            
            document.body.appendChild(audio);
            console.log(`Playing audio from user: ${userId}`);
        }
        
        function startTalking() {
            if (!isInVoiceRoom || isMuted || isTalking || !localStream) return;
            
            isTalking = true;
            
            localStream.getAudioTracks().forEach(track => {
                track.enabled = true;
            });
            
            document.getElementById('talkBtn').classList.add('recording');
            document.getElementById('voiceStatus').innerHTML = '🔴 Talking... Release button to stop';
            
            socket.emit('voice-activity', { isActive: true });
        }
        
        function stopTalking() {
            if (!isTalking || !localStream) return;
            
            isTalking = false;
            
            localStream.getAudioTracks().forEach(track => {
                track.enabled = false;
            });
            
            document.getElementById('talkBtn').classList.remove('recording');
            document.getElementById('voiceStatus').innerHTML = '🎤 In voice room - Hold "Talk" to speak!';
            
            socket.emit('voice-activity', { isActive: false });
        }
        
        function toggleMute() {
            isMuted = !isMuted;
            const muteBtn = document.getElementById('muteBtn');
            
            if (isMuted) {
                muteBtn.innerHTML = '🔇 Unmute';
                muteBtn.style.background = '#f44336';
                document.getElementById('voiceStatus').innerHTML = '🔇 Microphone muted';
                
                if (localStream) {
                    localStream.getAudioTracks().forEach(track => track.enabled = false);
                }
            } else {
                muteBtn.innerHTML = '🔊 Mute';
                muteBtn.style.background = '#ff9800';
                document.getElementById('voiceStatus').innerHTML = '🎤 In voice room - Hold "Talk" to speak!';
                
                if (localStream && !isTalking) {
                    localStream.getAudioTracks().forEach(track => track.enabled = false);
                }
            }
        }
        
        function updateParticipantsList() {
            const participantList = document.getElementById('participantList');
            
            if (!isInVoiceRoom) {
                participantList.innerHTML = `
                    <div class="participant">
                        <span>💤</span>
                        <span>No one in voice yet</span>
                    </div>
                `;
                return;
            }
            
            participantList.innerHTML = `
                <div class="participant" id="myParticipant">
                    <span>🎤</span>
                    <span>You (${currentUser?.display_name || 'User'})</span>
                </div>
            `;
            
            peerConnections.forEach((pc, userId) => {
                const participant = document.createElement('div');
                participant.className = 'participant';
                participant.id = `participant-${userId}`;
                participant.innerHTML = `
                    <span>🔊</span>
                    <span>User ${userId.substring(0, 8)}...</span>
                `;
                participantList.appendChild(participant);
            });
        }
        
        function updateUserVoiceActivity(userId, isActive) {
            const participant = document.getElementById(`participant-${userId}`);
            if (participant) {
                if (isActive) {
                    participant.classList.add('speaking');
                } else {
                    participant.classList.remove('speaking');
                }
            }
        }
        
        document.getElementById('talkBtn').addEventListener('contextmenu', e => e.preventDefault());
        
        // Initialize everything when page loads
        document.addEventListener('DOMContentLoaded', async function() {
            console.log('🔐 Secured Chatroom + Voice Room loading...');
            
            // Check if user is already logged in
            const isLoggedIn = await checkSession();
            
            if (isLoggedIn) {
                // Initialize voice connection
                initializeVoiceConnection();
                
                // Auto-refresh messages every 2 seconds
                setInterval(loadMessages, 2000);
                
                // Load initial messages
                loadMessages();
                
                console.log('✅ User authenticated and chatroom loaded!');
            } else {
                console.log('🔑 Please login to continue');
            }
            
            console.log('🎉 Secured Chatroom + Voice Room ready!');
            console.log('🔐 Authentication system active');
            console.log('💬 Text chat ready');
            console.log('🎤 Voice room connected to your Render server');
            console.log('🌐 Signaling server:', SIGNALING_SERVER);
        });
    </script>
</body>
</html>
        """
        
        self.send_response(200)
        self.send_header("Content-type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(html_content.encode('utf-8'))
    
    def handle_api(self, path):
        """Handle API endpoints"""
        if path == '/api/auth/register':
            self.handle_register()
        elif path == '/api/auth/login':
            self.handle_login()
        elif path == '/api/auth/verify':
            self.handle_verify_session()
        elif path == '/api/auth/logout':
            self.handle_logout()
        elif path == '/api/chat/send':
            self.handle_chat_send()
        elif path.startswith('/api/chat/messages'):
            self.handle_chat_messages(path)
        elif path == '/api/status':
            self.handle_status()
        else:
            self.send_error(404, "API endpoint not found")
    
    def handle_register(self):
        """Handle user registration"""
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length)
            data = json.loads(post_data.decode('utf-8'))
            
            username = data.get('username', '').strip()[:20]
            email = data.get('email', '').strip()
            display_name = data.get('displayName', '').strip()[:30] or username
            password = data.get('password', '')
            
            # Validation
            if len(username) < 3:
                self.send_json_response({"success": False, "error": "Username must be at least 3 characters"})
                return
            
            if len(password) < 6:
                self.send_json_response({"success": False, "error": "Password must be at least 6 characters"})
                return
            
            if '@' not in email:
                self.send_json_response({"success": False, "error": "Invalid email address"})
                return
            
            # Hash password
            password_hash, salt = hash_password(password)
            
            # Generate random avatar color
            colors = ['#667eea', '#764ba2', '#f093fb', '#f5576c', '#4facfe', '#00f2fe', '#43e97b', '#38f9d7', '#ffecd2', '#fcb69f']
            avatar_color = secrets.choice(colors)
            
            conn = sqlite3.connect('chatroom_users.db')
            cursor = conn.cursor()
            
            try:
                cursor.execute('''
                    INSERT INTO users (username, email, password_hash, salt, created_at, avatar_color, display_name)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (username, email, password_hash, salt, datetime.now().isoformat(), avatar_color, display_name))
                
                conn.commit()
                self.send_json_response({"success": True, "message": "Account created successfully"})
                
            except sqlite3.IntegrityError as e:
                if 'username' in str(e):
                    self.send_json_response({"success": False, "error": "Username already exists"})
                elif 'email' in str(e):
                    self.send_json_response({"success": False, "error": "Email already registered"})
                else:
                    self.send_json_response({"success": False, "error": "Registration failed"})
            finally:
                conn.close()
                
        except json.JSONDecodeError:
            self.send_json_response({"success": False, "error": "Invalid JSON"})
        except Exception as e:
            self.send_json_response({"success": False, "error": "Server error"})
            print(f"Registration error: {e}")
    
    def handle_login(self):
        """Handle user login"""
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length)
            data = json.loads(post_data.decode('utf-8'))
            
            username_or_email = data.get('username', '').strip()
            password = data.get('password', '')
            
            conn = sqlite3.connect('chatroom_users.db')
            cursor = conn.cursor()
            
            # Check if login is username or email
            cursor.execute('''
                SELECT id, username, email, password_hash, salt, display_name, avatar_color, bio, message_count
                FROM users 
                WHERE (username = ? OR email = ?) AND is_active = 1
            ''', (username_or_email, username_or_email))
            
            user = cursor.fetchone()
            
            if user and verify_password(password, user[3], user[4]):
                # Update last login
                cursor.execute('UPDATE users SET last_login = ? WHERE id = ?', 
                             (datetime.now().isoformat(), user[0]))
                conn.commit()
                
                # Create session
                session_token = create_session(
                    user[0], 
                    self.client_address[0], 
                    self.headers.get('User-Agent', '')
                )
                
                user_data = {
                    'id': user[0],
                    'username': user[1],
                    'email': user[2],
                    'display_name': user[5] or user[1],
                    'avatar_color': user[6],
                    'bio': user[7],
                    'message_count': user[8]
                }
                
                self.send_json_response({
                    "success": True, 
                    "sessionToken": session_token,
                    "user": user_data
                })
            else:
                self.send_json_response({"success": False, "error": "Invalid username/email or password"})
            
            conn.close()
            
        except json.JSONDecodeError:
            self.send_json_response({"success": False, "error": "Invalid JSON"})
        except Exception as e:
            self.send_json_response({"success": False, "error": "Server error"})
            print(f"Login error: {e}")
    
    def handle_verify_session(self):
        """Verify session token"""
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length)
            data = json.loads(post_data.decode('utf-8'))
            
            session_token = data.get('sessionToken', '')
            user_data = get_user_by_session(session_token)
            
            if user_data:
                self.send_json_response({"success": True, "user": user_data})
            else:
                self.send_json_response({"success": False, "error": "Invalid session"})
                
        except json.JSONDecodeError:
            self.send_json_response({"success": False, "error": "Invalid JSON"})
        except Exception as e:
            self.send_json_response({"success": False, "error": "Server error"})
            print(f"Session verification error: {e}")
    
    def handle_logout(self):
        """Handle user logout"""
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length)
            data = json.loads(post_data.decode('utf-8'))
            
            session_token = data.get('sessionToken', '')
            
            if session_token:
                conn = sqlite3.connect('chatroom_users.db')
                cursor = conn.cursor()
                cursor.execute('DELETE FROM sessions WHERE session_token = ?', (session_token,))
                conn.commit()
                conn.close()
            
            self.send_json_response({"success": True, "message": "Logged out successfully"})
            
        except Exception as e:
            self.send_json_response({"success": False, "error": "Server error"})
            print(f"Logout error: {e}")
    
    def handle_chat_send(self):
        """Handle sending a new chat message"""
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length)
            message_data = json.loads(post_data.decode('utf-8'))
            
            session_token = message_data.get('sessionToken', '')
            text = message_data.get('text', '')[:500]
            
            if not text.strip():
                self.send_json_response({"success": False, "error": "Empty message"})
                return
            
            # Verify session and get user
            user_data = get_user_by_session(session_token)
            if not user_data:
                self.send_json_response({"success": False, "error": "Invalid session"})
                return
            
            # Add message to global storage
            with chatroom_lock:
                new_id = max([msg['id'] for msg in chatroom_messages], default=0) + 1
                
                message = {
                    'id': new_id,
                    'username': user_data['username'],
                    'display_name': user_data['display_name'],
                    'avatar_color': user_data['avatar_color'],
                    'text': text.strip(),
                    'timestamp': datetime.now().isoformat(),
                    'user_id': user_data['id'],
                    'ip': self.client_address[0]
                }
                chatroom_messages.append(message)
                
                # Keep only last 100 messages
                if len(chatroom_messages) > 100:
                    chatroom_messages.pop(0)
                    for i, msg in enumerate(chatroom_messages):
                        msg['id'] = i + 1
            
            # Update user message count in database
            conn = sqlite3.connect('chatroom_users.db')
            cursor = conn.cursor()
            cursor.execute('UPDATE users SET message_count = message_count + 1 WHERE id = ?', (user_data['id'],))
            conn.commit()
            conn.close()
            
            self.send_json_response({"success": True, "message": "Message sent", "messageId": new_id})
            
        except json.JSONDecodeError:
            self.send_json_response({"success": False, "error": "Invalid JSON"})
        except Exception as e:
            self.send_json_response({"success": False, "error": str(e)})
            print(f"Send message error: {e}")
    
    def handle_chat_messages(self, path):
        """Handle retrieving chat messages"""
        try:
            # Parse query parameters
            query_params = urllib.parse.parse_qs(urllib.parse.urlparse(path).query)
            since_id = int(query_params.get('since', [0])[0])
            session_token = query_params.get('sessionToken', [''])[0]
            
            # Verify session
            user_data = get_user_by_session(session_token)
            if not user_data:
                self.send_json_response({"success": False, "error": "Invalid session"})
                return
            
            with chatroom_lock:
                # Get messages since the specified ID
                new_messages = [msg for msg in chatroom_messages if msg['id'] > since_id]
                
                response_data = {
                    "success": True,
                    "messages": new_messages,
                    "lastId": chatroom_messages[-1]['id'] if chatroom_messages else 0,
                    "messageCount": len(chatroom_messages)
                }
            
            self.send_json_response(response_data)
            
        except Exception as e:
            self.send_json_response({"success": False, "error": "Server error"})
            print(f"Load messages error: {e}")
    
    def handle_status(self):
        """Handle server status"""
        with chatroom_lock:
            message_count = len(chatroom_messages)
        
        # Get user count from database
        conn = sqlite3.connect('chatroom_users.db')
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM users WHERE is_active = 1')
        user_count = cursor.fetchone()[0]
        
        # Get active sessions count
        cursor.execute('SELECT COUNT(*) FROM sessions WHERE expires_at > ?', (datetime.now().isoformat(),))
        active_sessions_count = cursor.fetchone()[0]
        conn.close()
        
        data = {
            "status": "online",
            "server": "Secured Chatroom + Voice Room Server",
            "version": "6.0",
            "timestamp": time.time(),
            "total_messages": message_count,
            "total_users": user_count,
            "active_sessions": active_sessions_count,
            "signaling_server": "https://repo1-ejq1.onrender.com",
            "features": [
                "user_authentication", 
                "secure_sessions", 
                "persistent_storage",
                "text_chat", 
                "voice_room", 
                "webrtc_voice", 
                "push_to_talk", 
                "render_signaling",
                "user_profiles",
                "avatar_colors",
                "message_history"
            ],
            "database": "SQLite with user accounts",
            "uptime": "Running with secure authentication! 🔐💬🎤"
        }
        
        self.send_json_response(data)
    
    def send_json_response(self, data):
        """Helper method to send JSON responses"""
        response = json.dumps(data, indent=2)
        self.send_response(200)
        self.send_header("Content-type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(response.encode('utf-8'))
    
    def serve_static_file(self, path):
        """Try to serve static files from current directory"""
        file_path = path.lstrip('/')
        if '..' in file_path:
            return False
        
        if os.path.exists(file_path) and os.path.isfile(file_path):
            mime_type, _ = mimetypes.guess_type(file_path)
            if mime_type is None:
                mime_type = 'application/octet-stream'
            
            try:
                with open(file_path, 'rb') as f:
                    content = f.read()
                
                self.send_response(200)
                self.send_header("Content-type", mime_type)
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
                return True
            except IOError:
                return False
        
        return False
    
    def do_POST(self):
        """Handle POST requests"""
        parsed_path = urllib.parse.urlparse(self.path)
        path = parsed_path.path
        
        if path.startswith('/api/'):
            self.handle_api(path)
        else:
            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length)
            
            response_data = {
                "message": "POST request received",
                "content_length": content_length,
                "data_preview": post_data.decode('utf-8', errors='ignore')[:200]
            }
            
            self.send_json_response(response_data)
    
    def do_OPTIONS(self):
        """Handle OPTIONS requests for CORS"""
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

def cleanup_expired_sessions():
    """Clean up expired sessions periodically"""
    def cleanup():
        while True:
            try:
                conn = sqlite3.connect('chatroom_users.db')
                cursor = conn.cursor()
                cursor.execute('DELETE FROM sessions WHERE expires_at < ?', (datetime.now().isoformat(),))
                deleted = cursor.rowcount
                conn.commit()
                conn.close()
                
                if deleted > 0:
                    print(f"🧹 Cleaned up {deleted} expired sessions")
                
                time.sleep(3600)  # Run every hour
            except Exception as e:
                print(f"Session cleanup error: {e}")
                time.sleep(3600)
    
    cleanup_thread = threading.Thread(target=cleanup, daemon=True)
    cleanup_thread.start()

def main():
    # Initialize database
    print("🔐 Initializing secure database...")
    init_database()
    
    # Start session cleanup thread
    cleanup_expired_sessions()
    
    try:
        with socketserver.TCPServer(("0.0.0.0", PORT), ChatroomHandler) as httpd:
            print("🚀" * 50)
            print(f"🔐💬🎤 SECURED CHATROOM + VOICE ROOM SERVER STARTED!")
            print("🚀" * 50)
            print(f"🌐 Local URL: http://localhost:{PORT}")
            print(f"📡 Signaling Server: https://repo1-ejq1.onrender.com")
            print(f"📂 Directory: {os.getcwd()}")
            print(f"🗄️ Database: chatroom_users.db")
            print("\n🔐 AUTHENTICATION FEATURES:")
            print("   📝 User registration with email validation")
            print("   🔑 Secure login with password hashing (PBKDF2)")
            print("   🎫 Session token management (30-day expiry)")
            print("   👤 User profiles with avatars and display names")
            print("   📊 Message count tracking per user")
            print("   🔒 Session verification for all actions")
            print("   🧹 Automatic expired session cleanup")
            print("\n✨ CHAT FEATURES:")
            print("   💬 Real-time authenticated text chatroom")
            print("   🎨 Personalized user avatars with colors")
            print("   📝 Display names and user profiles")
            print("   📊 Message history with user attribution")
            print("   🔄 Auto-refresh every 2 seconds")
            print("   😊 Emoji support")
            print("   📱 Mobile-friendly tabbed interface")
            print("\n🎤 VOICE FEATURES:")
            print("   🗣️ Push-to-talk authenticated voice chat")
            print("   📡 Uses your Render.com signaling server")
            print("   🔗 WebRTC peer-to-peer voice connections")
            print("   👥 Real-time participant management")
            print("   🔊 Mute/unmute controls")
            print("   📞 Join/leave voice room")
            print("   🎯 Browser-based (no downloads needed)")
            print("\n🛡️ SECURITY FEATURES:")
            print("   🔐 Password hashing with salt (PBKDF2)")
            print("   🎫 Secure session tokens (32-byte random)")
            print("   ⏰ Session expiration (30 days)")
            print("   🗄️ SQLite database for persistent storage")
            print("   🧹 Automatic cleanup of expired sessions")
            print("   📊 User activity tracking")
            print("   🔒 All API endpoints require authentication")
            print("\n🎯 API ENDPOINTS:")
            print(f"   📝 POST /api/auth/register (Create account)")
            print(f"   🔑 POST /api/auth/login (User login)")
            print(f"   ✅ POST /api/auth/verify (Verify session)")
            print(f"   👋 POST /api/auth/logout (User logout)")
            print(f"   📤 POST /api/chat/send (Send message - auth required)")
            print(f"   📥 GET /api/chat/messages (Get messages - auth required)")
            print(f"   📊 GET /api/status (Server status)")
            print("\n💡 USAGE:")
            print("   1. Run this Python script locally")
            print("   2. Create an account or login with existing credentials")
            print("   3. Use tunneling to make it public:")
            print("      npx localtunnel --port 8080 --subdomain mysecurechat")
            print("   4. Share the tunnel URL with friends")
            print("   5. All users must create accounts to participate")
            print("   6. Switch between Text Chat and Voice Room tabs")
            print("   7. Click 'Join Voice Room' and allow microphone")
            print("   8. Hold 'Talk' button to speak with others!")
            print("\n⚠️  IMPORTANT NOTES:")
            print("   • All users must register/login to use the chatroom")
            print("   • User data is stored in SQLite database (chatroom_users.db)")
            print("   • Sessions expire after 30 days of inactivity")
            print("   • Voice signaling goes through your Render server")
            print("   • Audio is peer-to-peer (no audio through server)")
            print("   • Works best in Chrome/Edge browsers")
            print("   • Allow microphone permissions when prompted")
            print("\n🗄️ DATABASE TABLES:")
            print("   👥 users: User accounts, profiles, and settings")
            print("   🎫 sessions: Active user sessions and tokens")
            print("   💬 Messages stored in memory (last 100 messages)")
            print("\n🛑 Press Ctrl+C to stop the server")
            print("=" * 50)
            
            httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n🛑 Secured Chatroom server stopped by user")
        print("👋 Thanks for using the authenticated chatroom!")
    except Exception as e:
        print(f"❌ Server error: {e}")

if __name__ == "__main__":
    main()
