import http.server
import socketserver
import os
import mimetypes
import urllib.parse
import json
import time
import threading
import hashlib
import base64
import requests
from datetime import datetime, timedelta

PORT = int(os.environ.get('PORT', 8080))

# Global storage
chatroom_messages = []
chatroom_lock = threading.Lock()
users_db = {}  # username -> {"password_hash": str, "created": datetime, "last_seen": datetime}
user_sessions = {}  # session_id -> {"username": str, "expires": datetime, "last_activity": datetime}
active_users = set()  # Currently online users
users_lock = threading.Lock()

# Persistence configuration
GITHUB_GIST_TOKEN = os.environ.get('GITHUB_GIST_TOKEN', '')
GITHUB_GIST_ID = os.environ.get('GITHUB_GIST_ID', '')
BACKUP_INTERVAL = 300  # 5 minutes
EXTERNAL_BACKUP_URL = os.environ.get('BACKUP_WEBHOOK_URL', '')

class DataPersistence:
    """Handles multiple backup strategies for data persistence"""
    
    @staticmethod
    def hash_password(password):
        """Hash password with salt"""
        salt = "chatroom_salt_2024"
        return hashlib.sha256((password + salt).encode()).hexdigest()
    
    @staticmethod
    def generate_session_id():
        """Generate secure session ID"""
        return base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip('=')
    
    @staticmethod
    def backup_to_github_gist():
        """Backup data to GitHub Gist (primary method)"""
        if not GITHUB_GIST_TOKEN or not GITHUB_GIST_ID:
            return False
        
        try:
            with chatroom_lock, users_lock:
                backup_data = {
                    "timestamp": datetime.now().isoformat(),
                    "users": {
                        username: {
                            "password_hash": data["password_hash"],
                            "created": data["created"].isoformat() if isinstance(data["created"], datetime) else data["created"],
                            "last_seen": data["last_seen"].isoformat() if isinstance(data["last_seen"], datetime) else data["last_seen"]
                        }
                        for username, data in users_db.items()
                    },
                    "messages": chatroom_messages[-100:],  # Keep last 100 messages
                    "stats": {
                        "total_users": len(users_db),
                        "total_messages": len(chatroom_messages)
                    }
                }
            
            headers = {
                'Authorization': f'token {GITHUB_GIST_TOKEN}',
                'Accept': 'application/vnd.github.v3+json'
            }
            
            data = {
                "files": {
                    "chatroom_backup.json": {
                        "content": json.dumps(backup_data, indent=2)
                    }
                }
            }
            
            response = requests.patch(
                f'https://api.github.com/gists/{GITHUB_GIST_ID}',
                headers=headers,
                json=data,
                timeout=10
            )
            
            if response.status_code == 200:
                print(f"✅ Data backed up to GitHub Gist at {datetime.now()}")
                return True
            else:
                print(f"❌ GitHub Gist backup failed: {response.status_code}")
                return False
                
        except Exception as e:
            print(f"❌ GitHub Gist backup error: {e}")
            return False
    
    @staticmethod
    def restore_from_github_gist():
        """Restore data from GitHub Gist"""
        if not GITHUB_GIST_TOKEN or not GITHUB_GIST_ID:
            return False
        
        try:
            headers = {
                'Authorization': f'token {GITHUB_GIST_TOKEN}',
                'Accept': 'application/vnd.github.v3+json'
            }
            
            response = requests.get(
                f'https://api.github.com/gists/{GITHUB_GIST_ID}',
                headers=headers,
                timeout=10
            )
            
            if response.status_code != 200:
                print(f"❌ GitHub Gist restore failed: {response.status_code}")
                return False
            
            gist_data = response.json()
            backup_content = gist_data['files']['chatroom_backup.json']['content']
            backup_data = json.loads(backup_content)
            
            with chatroom_lock, users_lock:
                # Restore users
                global users_db
                users_db = {}
                for username, user_data in backup_data.get("users", {}).items():
                    users_db[username] = {
                        "password_hash": user_data["password_hash"],
                        "created": datetime.fromisoformat(user_data["created"]),
                        "last_seen": datetime.fromisoformat(user_data["last_seen"])
                    }
                
                # Restore messages
                global chatroom_messages
                chatroom_messages = backup_data.get("messages", [])
                
                # Fix message IDs
                for i, msg in enumerate(chatroom_messages):
                    msg['id'] = i + 1
            
            print(f"✅ Data restored from GitHub Gist: {len(users_db)} users, {len(chatroom_messages)} messages")
            return True
            
        except Exception as e:
            print(f"❌ GitHub Gist restore error: {e}")
            return False

# Initialize data persistence
data_persistence = DataPersistence()

def backup_data_periodically():
    """Background thread to backup data periodically"""
    while True:
        time.sleep(BACKUP_INTERVAL)
        print(f"🔄 Starting periodic backup...")
        data_persistence.backup_to_github_gist()

def cleanup_expired_sessions():
    """Background task to clean up expired sessions and update active users"""
    while True:
        time.sleep(60)  # Run every minute
        current_time = datetime.now()
        with users_lock:
            expired_sessions = []
            active_users.clear()
            
            for session_id, session_data in list(user_sessions.items()):
                if current_time > session_data['expires']:
                    expired_sessions.append(session_id)
                elif current_time - session_data.get('last_activity', session_data['expires']) < timedelta(minutes=5):
                    # User is active if they made a request in the last 5 minutes
                    active_users.add(session_data['username'])
            
            for session_id in expired_sessions:
                del user_sessions[session_id]
        
        if expired_sessions:
            print(f"🧹 Cleaned up {len(expired_sessions)} expired sessions")

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
            self.serve_login_page()
            return
        elif path == '/chat':
            self.serve_chatroom()
            return
        elif path.startswith('/api/'):
            self.handle_api(path)
            return
        
        if self.serve_static_file(path):
            return
        
        self.send_error(404, "File not found")
    
    def serve_login_page(self):
        """Serve the login/register page"""
        html_content = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>🎤💬 Chatroom Login</title>
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
            align-items: center;
            justify-content: center;
            padding: 20px;
        }
        
        .login-container {
            background: rgba(255, 255, 255, 0.95);
            backdrop-filter: blur(10px);
            border-radius: 20px;
            padding: 40px;
            box-shadow: 0 20px 40px rgba(0,0,0,0.1);
            width: 100%;
            max-width: 400px;
            text-align: center;
        }
        
        .logo {
            font-size: 3em;
            margin-bottom: 10px;
        }
        
        h1 {
            color: #333;
            margin-bottom: 30px;
            font-size: 1.8em;
        }
        
        .form-tabs {
            display: flex;
            margin-bottom: 30px;
            background: #f0f0f0;
            border-radius: 10px;
            padding: 5px;
        }
        
        .tab-btn {
            flex: 1;
            background: none;
            border: none;
            padding: 12px;
            border-radius: 8px;
            cursor: pointer;
            font-weight: bold;
            color: #666;
            transition: all 0.3s ease;
        }
        
        .tab-btn.active {
            background: #667eea;
            color: white;
            transform: scale(1.02);
        }
        
        .form-container {
            display: none;
            animation: fadeIn 0.3s ease-in;
        }
        
        .form-container.active {
            display: block;
        }
        
        @keyframes fadeIn {
            from { opacity: 0; transform: translateY(10px); }
            to { opacity: 1; transform: translateY(0); }
        }
        
        .form-group {
            margin-bottom: 20px;
            text-align: left;
        }
        
        label {
            display: block;
            margin-bottom: 5px;
            font-weight: bold;
            color: #333;
        }
        
        input[type="text"], input[type="password"] {
            width: 100%;
            padding: 12px 15px;
            border: 2px solid #ddd;
            border-radius: 10px;
            font-size: 16px;
            transition: border-color 0.3s ease;
        }
        
        input[type="text"]:focus, input[type="password"]:focus {
            outline: none;
            border-color: #667eea;
        }
        
        .submit-btn {
            width: 100%;
            background: #667eea;
            color: white;
            border: none;
            padding: 15px;
            border-radius: 10px;
            font-size: 16px;
            font-weight: bold;
            cursor: pointer;
            transition: all 0.3s ease;
            margin-top: 10px;
        }
        
        .submit-btn:hover {
            background: #5a6fd8;
            transform: translateY(-2px);
            box-shadow: 0 5px 15px rgba(102, 126, 234, 0.3);
        }
        
        .error-message {
            background: #ffebee;
            border: 1px solid #ffcdd2;
            color: #c62828;
            padding: 10px;
            border-radius: 8px;
            margin-bottom: 20px;
            display: none;
        }
        
        .success-message {
            background: #e8f5e8;
            border: 1px solid #c8e6c9;
            color: #2e7d32;
            padding: 10px;
            border-radius: 8px;
            margin-bottom: 20px;
            display: none;
        }
    </style>
</head>
<body>
    <div class="login-container">
        <div class="logo">🎤💬</div>
        <h1>Chatroom + Voice Room</h1>
        
        <div class="error-message" id="errorMessage"></div>
        <div class="success-message" id="successMessage"></div>
        
        <div class="form-tabs">
            <button class="tab-btn active" onclick="switchForm('login')">Login</button>
            <button class="tab-btn" onclick="switchForm('register')">Register</button>
        </div>
        
        <!-- Login Form -->
        <div id="loginForm" class="form-container active">
            <form onsubmit="handleLogin(event)">
                <div class="form-group">
                    <label for="loginUsername">Username</label>
                    <input type="text" id="loginUsername" required maxlength="20" 
                           placeholder="Enter your username">
                </div>
                
                <div class="form-group">
                    <label for="loginPassword">Password</label>
                    <input type="password" id="loginPassword" required 
                           placeholder="Enter your password">
                </div>
                
                <button type="submit" class="submit-btn">
                    🚀 Login & Enter Chatroom
                </button>
            </form>
        </div>
        
        <!-- Register Form -->
        <div id="registerForm" class="form-container">
            <form onsubmit="handleRegister(event)">
                <div class="form-group">
                    <label for="regUsername">Username</label>
                    <input type="text" id="regUsername" required maxlength="20" 
                           placeholder="Choose a username">
                </div>
                
                <div class="form-group">
                    <label for="regPassword">Password</label>
                    <input type="password" id="regPassword" required minlength="4"
                           placeholder="Choose a password">
                </div>
                
                <div class="form-group">
                    <label for="regPasswordConfirm">Confirm Password</label>
                    <input type="password" id="regPasswordConfirm" required 
                           placeholder="Confirm your password">
                </div>
                
                <button type="submit" class="submit-btn">
                    ✨ Create Account & Join
                </button>
            </form>
        </div>
    </div>

    <script>
        function switchForm(formType) {
            document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
            event.target.classList.add('active');
            
            document.querySelectorAll('.form-container').forEach(form => form.classList.remove('active'));
            document.getElementById(formType + 'Form').classList.add('active');
            
            hideMessages();
        }
        
        function showError(message) {
            const errorDiv = document.getElementById('errorMessage');
            errorDiv.textContent = message;
            errorDiv.style.display = 'block';
            document.getElementById('successMessage').style.display = 'none';
        }
        
        function showSuccess(message) {
            const successDiv = document.getElementById('successMessage');
            successDiv.textContent = message;
            successDiv.style.display = 'block';
            document.getElementById('errorMessage').style.display = 'none';
        }
        
        function hideMessages() {
            document.getElementById('errorMessage').style.display = 'none';
            document.getElementById('successMessage').style.display = 'none';
        }
        
        async function handleLogin(event) {
            event.preventDefault();
            hideMessages();
            
            const username = document.getElementById('loginUsername').value.trim();
            const password = document.getElementById('loginPassword').value;
            
            if (!username || !password) {
                showError('Please fill in all fields');
                return;
            }
            
            try {
                const response = await fetch('/api/auth/login', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ username, password })
                });
                
                const data = await response.json();
                
                if (data.success) {
                    document.cookie = `session_id=${data.session_id}; path=/; max-age=86400`; // 24 hours
                    showSuccess('Login successful! Redirecting...');
                    setTimeout(() => window.location.href = '/chat', 1000);
                } else {
                    showError(data.error || 'Login failed');
                }
            } catch (error) {
                showError('Network error. Please try again.');
            }
        }
        
        async function handleRegister(event) {
            event.preventDefault();
            hideMessages();
            
            const username = document.getElementById('regUsername').value.trim();
            const password = document.getElementById('regPassword').value;
            const confirmPassword = document.getElementById('regPasswordConfirm').value;
            
            if (!username || !password || !confirmPassword) {
                showError('Please fill in all fields');
                return;
            }
            
            if (password !== confirmPassword) {
                showError('Passwords do not match');
                return;
            }
            
            try {
                const response = await fetch('/api/auth/register', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ username, password })
                });
                
                const data = await response.json();
                
                if (data.success) {
                    showSuccess('Account created! You can now login.');
                    setTimeout(() => {
                        switchForm('login');
                        document.getElementById('loginUsername').value = username;
                    }, 1500);
                } else {
                    showError(data.error || 'Registration failed');
                }
            } catch (error) {
                showError('Network error. Please try again.');
            }
        }
    </script>
</body>
</html>
        """
        
        self.send_response(200)
        self.send_header("Content-type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(html_content.encode('utf-8'))
    
    def serve_chatroom(self):
        """Serve the chatroom with improved functionality"""
        # Check authentication
        session_id = self.get_session_from_cookies()
        if not session_id or not self.is_valid_session(session_id):
            self.send_response(302)
            self.send_header("Location", "/")
            self.end_headers()
            return
        
        # Update user activity
        self.update_user_activity(session_id)
        username = user_sessions.get(session_id, {}).get('username', 'Anonymous')
        
        html_content = f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Chatroom + Voice Room 🎤💬</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        
        body {{
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            height: 100vh;
            display: flex;
            flex-direction: column;
        }}
        
        .header {{
            background: rgba(255, 255, 255, 0.1);
            backdrop-filter: blur(10px);
            padding: 20px;
            text-align: center;
            color: white;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
            position: relative;
        }}
        
        .header h1 {{
            font-size: 2em;
            margin-bottom: 10px;
            text-shadow: 2px 2px 4px rgba(0,0,0,0.3);
        }}
        
        .user-info {{
            position: absolute;
            top: 20px;
            right: 20px;
            display: flex;
            align-items: center;
            gap: 10px;
        }}
        
        .username-display {{
            background: rgba(255, 255, 255, 0.2);
            padding: 8px 15px;
            border-radius: 20px;
            font-weight: bold;
        }}
        
        .logout-btn {{
            background: rgba(255, 255, 255, 0.2);
            color: white;
            border: none;
            padding: 8px 15px;
            border-radius: 20px;
            cursor: pointer;
            font-weight: bold;
            transition: all 0.3s ease;
        }}
        
        .logout-btn:hover {{
            background: rgba(255, 255, 255, 0.3);
        }}
        
        .tabs {{
            display: flex;
            justify-content: center;
            gap: 10px;
            margin-top: 10px;
        }}
        
        .tab-btn {{
            background: rgba(255, 255, 255, 0.2);
            color: white;
            border: none;
            padding: 10px 20px;
            border-radius: 25px;
            cursor: pointer;
            font-weight: bold;
            transition: all 0.3s ease;
        }}
        
        .tab-btn.active {{
            background: rgba(255, 255, 255, 0.3);
            transform: scale(1.05);
        }}
        
        .status-bar {{
            display: flex;
            justify-content: center;
            gap: 20px;
            margin-top: 10px;
            flex-wrap: wrap;
        }}
        
        .status-item {{
            background: rgba(255, 255, 255, 0.2);
            padding: 5px 15px;
            border-radius: 20px;
            font-size: 0.9em;
        }}
        
        .main-container {{
            flex: 1;
            display: flex;
            flex-direction: column;
            max-width: 1000px;
            margin: 0 auto;
            width: 100%;
            padding: 20px;
        }}
        
        .tab-content {{
            display: none;
            flex: 1;
            animation: fadeIn 0.3s ease-in;
        }}
        
        .tab-content.active {{
            display: flex;
            flex-direction: column;
        }}
        
        @keyframes fadeIn {{
            from {{ opacity: 0; transform: translateY(10px); }}
            to {{ opacity: 1; transform: translateY(0); }}
        }}
        
        /* Chat Room Styles */
        .chat-layout {{
            display: flex;
            gap: 20px;
            flex: 1;
        }}
        
        .chat-main {{
            flex: 1;
            display: flex;
            flex-direction: column;
        }}
        
        .online-users {{
            width: 200px;
            background: rgba(255, 255, 255, 0.95);
            border-radius: 15px;
            padding: 20px;
            max-height: 500px;
            overflow-y: auto;
        }}
        
        .online-users h3 {{
            margin-bottom: 15px;
            color: #333;
            text-align: center;
        }}
        
        .user-list {{
            display: flex;
            flex-direction: column;
            gap: 8px;
        }}
        
        .user-item {{
            background: #f0f0f0;
            padding: 8px 12px;
            border-radius: 20px;
            font-size: 14px;
            display: flex;
            align-items: center;
            gap: 8px;
        }}
        
        .user-item.current {{
            background: #667eea;
            color: white;
        }}
        
        .user-item.typing {{
            background: #ffeb3b;
            animation: pulse 1s infinite;
        }}
        
        .messages-container {{
            flex: 1;
            background: rgba(255, 255, 255, 0.95);
            border-radius: 15px 15px 0 0;
            padding: 20px;
            overflow-y: auto;
            max-height: 400px;
            margin-bottom: 0;
            scroll-behavior: smooth;
        }}
        
        .message {{
            margin-bottom: 15px;
            padding: 12px 15px;
            border-radius: 10px;
            background: #f8f9fa;
            border-left: 4px solid #667eea;
            animation: slideIn 0.3s ease-out;
            position: relative;
        }}
        
        .message.own {{
            background: #e3f2fd;
            border-left-color: #2196F3;
            margin-left: 50px;
        }}
        
        .message.reply {{
            border-left-color: #4CAF50;
            background: #f1f8e9;
        }}
        
        .message-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 5px;
            font-size: 0.9em;
        }}
        
        .username {{
            font-weight: bold;
            color: #667eea;
            cursor: pointer;
        }}
        
        .username:hover {{
            text-decoration: underline;
        }}
        
        .timestamp {{
            color: #666;
            font-size: 0.8em;
        }}
        
        .message-actions {{
            position: absolute;
            right: 10px;
            top: 8px;
            opacity: 0;
            transition: opacity 0.3s;
        }}
        
        .message:hover .message-actions {{
            opacity: 1;
        }}
        
        .reply-btn {{
            background: none;
            border: none;
            cursor: pointer;
            font-size: 14px;
            padding: 2px 6px;
            border-radius: 3px;
            transition: background 0.2s;
        }}
        
        .reply-btn:hover {{
            background: rgba(0,0,0,0.1);
        }}
        
        .reply-to {{
            background: rgba(0,0,0,0.05);
            border-left: 3px solid #4CAF50;
            padding: 8px 12px;
            margin-bottom: 8px;
            border-radius: 5px;
            font-size: 0.9em;
            color: #666;
        }}
        
        .reply-to .reply-username {{
            font-weight: bold;
            color: #4CAF50;
        }}
        
        .message-text {{
            color: #333;
            line-height: 1.4;
            word-wrap: break-word;
        }}
        
        .input-container {{
            background: rgba(255, 255, 255, 0.95);
            padding: 20px;
            border-radius: 0 0 15px 15px;
            display: flex;
            flex-direction: column;
            gap: 10px;
        }}
        
        .reply-preview {{
            background: rgba(76, 175, 80, 0.1);
            border-left: 3px solid #4CAF50;
            padding: 8px 12px;
            border-radius: 5px;
            display: none;
            position: relative;
        }}
        
        .reply-preview.show {{
            display: block;
        }}
        
        .cancel-reply {{
            position: absolute;
            right: 8px;
            top: 8px;
            background: none;
            border: none;
            cursor: pointer;
            font-size: 16px;
            color: #666;
        }}
        
        .input-row {{
            display: flex;
            gap: 10px;
            align-items: flex-end;
        }}
        
        #messageInput {{
            flex: 1;
            padding: 12px;
            border: 2px solid #ddd;
            border-radius: 8px;
            font-size: 14px;
            resize: none;
            max-height: 100px;
        }}
        
        .typing-indicator {{
            font-size: 12px;
            color: #666;
            font-style: italic;
            min-height: 16px;
            margin-bottom: 5px;
        }}
        
        #sendButton {{
            background: #667eea;
            color: white;
            border: none;
            padding: 12px 20px;
            border-radius: 8px;
            cursor: pointer;
            font-size: 14px;
            font-weight: bold;
            transition: all 0.3s ease;
        }}
        
        #sendButton:hover {{
            background: #5a6fd8;
            transform: translateY(-1px);
        }}
        
        .emoji-btn {{
            background: none;
            border: none;
            font-size: 18px;
            cursor: pointer;
            padding: 8px;
            border-radius: 6px;
            transition: background 0.2s;
        }}
        
        .emoji-btn:hover {{
            background: rgba(0,0,0,0.1);
        }}
        
        /* Voice Room Styles - Disabled */
        .voice-container {{
            background: rgba(255, 255, 255, 0.95);
            border-radius: 15px;
            padding: 40px;
            text-align: center;
            flex: 1;
            display: flex;
            flex-direction: column;
            justify-content: center;
            align-items: center;
            gap: 20px;
            position: relative;
        }}
        
        .police-tape {{
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: repeating-linear-gradient(
                45deg,
                #ffeb3b 0px,
                #ffeb3b 20px,
                #000 20
