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
online_users = {}  # session_id -> {"username": str, "last_ping": datetime}
typing_users = {}  # username -> timestamp
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
                    "messages": chatroom_messages[-50:],
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
                global users_db
                users_db = {}
                for username, user_data in backup_data.get("users", {}).items():
                    users_db[username] = {
                        "password_hash": user_data["password_hash"],
                        "created": datetime.fromisoformat(user_data["created"]),
                        "last_seen": datetime.fromisoformat(user_data["last_seen"])
                    }
                
                global chatroom_messages
                chatroom_messages = backup_data.get("messages", [])
                
                for i, msg in enumerate(chatroom_messages):
                    msg['id'] = i + 1
            
            print(f"✅ Data restored from GitHub Gist: {len(users_db)} users, {len(chatroom_messages)} messages")
            return True
            
        except Exception as e:
            print(f"❌ GitHub Gist restore error: {e}")
            return False
    
    @staticmethod
    def backup_to_webhook():
        """Secondary backup to external webhook"""
        if not EXTERNAL_BACKUP_URL:
            return False
        
        try:
            with chatroom_lock, users_lock:
                backup_data = {
                    "timestamp": datetime.now().isoformat(),
                    "users_count": len(users_db),
                    "messages_count": len(chatroom_messages),
                    "last_messages": chatroom_messages[-10:] if chatroom_messages else []
                }
            
            response = requests.post(
                EXTERNAL_BACKUP_URL,
                json=backup_data,
                timeout=5
            )
            
            if response.status_code == 200:
                print(f"✅ Webhook backup successful")
                return True
            else:
                print(f"⚠️ Webhook backup failed: {response.status_code}")
                return False
                
        except Exception as e:
            print(f"⚠️ Webhook backup error: {e}")
            return False

data_persistence = DataPersistence()

def backup_data_periodically():
    """Background thread to backup data periodically"""
    while True:
        time.sleep(BACKUP_INTERVAL)
        print(f"🔄 Starting periodic backup...")
        
        if data_persistence.backup_to_github_gist():
            print("✅ Primary backup (GitHub Gist) successful")
        else:
            print("⚠️ Primary backup failed, trying webhook...")
            data_persistence.backup_to_webhook()

def cleanup_inactive_users():
    """Remove inactive users from online list"""
    while True:
        time.sleep(30)  # Check every 30 seconds
        current_time = datetime.now()
        
        with users_lock:
            # Remove users inactive for more than 2 minutes
            inactive_sessions = [
                session_id for session_id, data in online_users.items()
                if (current_time - data['last_ping']).total_seconds() > 120
            ]
            
            for session_id in inactive_sessions:
                del online_users[session_id]
            
            # Clean up typing users (remove if inactive for 10 seconds)
            inactive_typing = [
                username for username, timestamp in typing_users.items()
                if (current_time - timestamp).total_seconds() > 10
            ]
            
            for username in inactive_typing:
                del typing_users[username]

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
    <title>💬 Chatroom Login</title>
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
        
        .password-match {
            font-size: 12px;
            margin-top: 5px;
            color: #666;
        }
        
        .password-match.valid {
            color: #4CAF50;
        }
        
        .password-match.invalid {
            color: #f44336;
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
        
        .submit-btn:disabled {
            background: #ccc;
            cursor: not-allowed;
            transform: none;
            box-shadow: none;
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
        
        .server-info {
            margin-top: 30px;
            padding: 15px;
            background: rgba(0,0,0,0.05);
            border-radius: 10px;
            font-size: 14px;
            color: #666;
        }
        
        .server-info h3 {
            color: #333;
            margin-bottom: 10px;
        }
        
        @media (max-width: 500px) {
            .login-container {
                padding: 30px 20px;
                margin: 10px;
            }
        }
    </style>
</head>
<body>
    <div class="login-container">
        <div class="logo">💬</div>
        <h1>Enhanced Chatroom</h1>
        
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
                           placeholder="Choose a username" onkeyup="checkUsername()">
                    <div class="password-match" id="usernameCheck"></div>
                </div>
                
                <div class="form-group">
                    <label for="regPassword">Password</label>
                    <input type="password" id="regPassword" required minlength="4"
                           placeholder="Choose a password" onkeyup="checkPasswords()">
                </div>
                
                <div class="form-group">
                    <label for="regPasswordConfirm">Confirm Password</label>
                    <input type="password" id="regPasswordConfirm" required 
                           placeholder="Confirm your password" onkeyup="checkPasswords()">
                    <div class="password-match" id="passwordMatch"></div>
                </div>
                
                <button type="submit" class="submit-btn" id="registerBtn" disabled>
                    ✨ Create Account & Join
                </button>
            </form>
        </div>
        
        <div class="server-info">
            <h3>🔐 Enhanced Features</h3>
            <p>• See who's online in real-time</p>
            <p>• Reply to specific messages</p>
            <p>• Typing indicators</p>
            <p>• Smart scrolling behavior</p>
            <p>• Secure & persistent storage</p>
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
            
            const successDiv = document.getElementById('successMessage');
            successDiv.style.display = 'none';
        }
        
        function showSuccess(message) {
            const successDiv = document.getElementById('successMessage');
            successDiv.textContent = message;
            successDiv.style.display = 'block';
            
            const errorDiv = document.getElementById('errorMessage');
            errorDiv.style.display = 'none';
        }
        
        function hideMessages() {
            document.getElementById('errorMessage').style.display = 'none';
            document.getElementById('successMessage').style.display = 'none';
        }
        
        function checkUsername() {
            const username = document.getElementById('regUsername').value;
            const checkDiv = document.getElementById('usernameCheck');
            
            if (username.length < 3) {
                checkDiv.textContent = 'Username must be at least 3 characters';
                checkDiv.className = 'password-match invalid';
            } else if (!/^[a-zA-Z0-9_]+$/.test(username)) {
                checkDiv.textContent = 'Only letters, numbers, and underscores allowed';
                checkDiv.className = 'password-match invalid';
            } else {
                checkDiv.textContent = 'Username looks good! ✓';
                checkDiv.className = 'password-match valid';
            }
            
            checkRegisterButton();
        }
        
        function checkPasswords() {
            const password = document.getElementById('regPassword').value;
            const confirmPassword = document.getElementById('regPasswordConfirm').value;
            const matchDiv = document.getElementById('passwordMatch');
            
            if (password.length < 4) {
                matchDiv.textContent = 'Password must be at least 4 characters';
                matchDiv.className = 'password-match invalid';
            } else if (confirmPassword && password !== confirmPassword) {
                matchDiv.textContent = 'Passwords do not match';
                matchDiv.className = 'password-match invalid';
            } else if (confirmPassword && password === confirmPassword) {
                matchDiv.textContent = 'Passwords match! ✓';
                matchDiv.className = 'password-match valid';
            } else {
                matchDiv.textContent = '';
                matchDiv.className = 'password-match';
            }
            
            checkRegisterButton();
        }
        
        function checkRegisterButton() {
            const username = document.getElementById('regUsername').value;
            const password = document.getElementById('regPassword').value;
            const confirmPassword = document.getElementById('regPasswordConfirm').value;
            const registerBtn = document.getElementById('registerBtn');
            
            const isValid = username.length >= 3 && 
                           /^[a-zA-Z0-9_]+$/.test(username) &&
                           password.length >= 4 && 
                           password === confirmPassword;
            
            registerBtn.disabled = !isValid;
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
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({ username, password })
                });
                
                const data = await response.json();
                
                if (data.success) {
                    document.cookie = `session_id=${data.session_id}; path=/; max-age=345600`; // 96 hours
                    showSuccess('Login successful! Redirecting to chatroom...');
                    setTimeout(() => {
                        window.location.href = '/chat';
                    }, 1000);
                } else {
                    showError(data.error || 'Login failed');
                }
            } catch (error) {
                showError('Network error. Please try again.');
                console.error('Login error:', error);
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
            
            if (password.length < 4) {
                showError('Password must be at least 4 characters');
                return;
            }
            
            try {
                const response = await fetch('/api/auth/register', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({ username, password })
                });
                
                const data = await response.json();
                
                if (data.success) {
                    showSuccess('Account created successfully! You can now login.');
                    setTimeout(() => {
                        switchForm('login');
                        document.getElementById('loginUsername').value = username;
                        document.getElementById('regUsername').value = '';
                        document.getElementById('regPassword').value = '';
                        document.getElementById('regPasswordConfirm').value = '';
                    }, 1500);
                } else {
                    showError(data.error || 'Registration failed');
                }
            } catch (error) {
                showError('Network error. Please try again.');
                console.error('Register error:', error);
            }
        }
        
        document.addEventListener('DOMContentLoaded', async function() {
            try {
                const response = await fetch('/api/auth/check');
                const data = await response.json();
                
                if (data.authenticated) {
                    showSuccess(`Welcome back, ${data.username}! Redirecting...`);
                    setTimeout(() => {
                        window.location.href = '/chat';
                    }, 1000);
                }
            } catch (error) {
                console.log('Not logged in or session expired');
            }
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
    
    def serve_chatroom(self):
        """Serve the chatroom with enhanced features"""
        session_id = self.get_session_from_cookies()
        if not session_id or not self.is_valid_session(session_id):
            self.send_response(302)
            self.send_header("Location", "/")
            self.end_headers()
            return
        
        # Update user activity
        with users_lock:
            username = user_sessions.get(session_id, {}).get('username', 'Anonymous')
            user_sessions[session_id]['last_activity'] = datetime.now()
            online_users[session_id] = {
                'username': username,
                'last_ping': datetime.now()
            }
        
        html_content = f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Enhanced Chatroom 💬</title>
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
            backdrop-filter: blur(5px);
        }}
        
        .tab-btn.active {{
            background: rgba(255, 255, 255, 0.3);
            transform: scale(1.05);
        }}
        
        .tab-btn:hover {{
            background: rgba(255, 255, 255, 0.25);
        }}
        
        .stats-container {{
            background: rgba(76, 175, 80, 0.8);
            padding: 5px 15px;
            border-radius: 20px;
            display: inline-block;
            font-size: 0.9em;
            margin-top: 10px;
        }}
        
        .main-container {{
            flex: 1;
            display: flex;
            max-width: 1200px;
            margin: 0 auto;
            width: 100%;
            padding: 20px;
            gap: 20px;
        }}
        
        .sidebar {{
            width: 300px;
            display: flex;
            flex-direction: column;
            gap: 20px;
        }}
        
        .online-users {{
            background: rgba(255, 255, 255, 0.95);
            border-radius: 15px;
            padding: 20px;
            max-height: 300px;
            overflow-y: auto;
        }}
        
        .online-users h3 {{
            margin-bottom: 15px;
            color: #333;
            display: flex;
            align-items: center;
            gap: 10px;
        }}
        
        .user-item {{
            background: #f0f8ff;
            padding: 8px 12px;
            border-radius: 8px;
            margin-bottom: 8px;
            display: flex;
            align-items: center;
            gap: 8px;
            border-left: 3px solid #667eea;
        }}
        
        .user-item.current-user {{
            background: #e3f2fd;
            border-left-color: #2196F3;
            font-weight: bold;
        }}
        
        .user-status {{
            width: 8px;
            height: 8px;
            background: #4CAF50;
            border-radius: 50%;
            animation: pulse 2s infinite;
        }}
        
        @keyframes pulse {{
            0% {{ opacity: 1; }}
            50% {{ opacity: 0.5; }}
            100% {{ opacity: 1; }}
        }}
        
        .typing-indicators {{
            background: rgba(255, 255, 255, 0.95);
            border-radius: 15px;
            padding: 15px;
            min-height: 100px;
        }}
        
        .typing-indicators h3 {{
            margin-bottom: 15px;
            color: #333;
            display: flex;
            align-items: center;
            gap: 10px;
        }}
        
        .typing-item {{
            background: #fff3e0;
            padding: 8px 12px;
            border-radius: 8px;
            margin-bottom: 8px;
            color: #333;
            border-left: 3px solid #ff9800;
            animation: fadeIn 0.3s ease-in;
        }}
        
        .typing-dots {{
            display: inline-block;
            margin-left: 5px;
        }}
        
        .typing-dots::after {{
            content: '';
            animation: typing 1.5s infinite;
        }}
        
        @keyframes typing {{
            0% {{ content: ''; }}
            25% {{ content: '.'; }}
            50% {{ content: '..'; }}
            75% {{ content: '...'; }}
            100% {{ content: ''; }}
        }}
        
        .chat-area {{
            flex: 1;
            display: flex;
            flex-direction: column;
        }}
        
        .tab-content {{
            display: none;
            flex: 1;
            flex-direction: column;
        }}
        
        .tab-content.active {{
            display: flex;
        }}
        
        .messages-container {{
            flex: 1;
            background: rgba(255, 255, 255, 0.95);
            border-radius: 15px 15px 0 0;
            padding: 20px;
            overflow-y: auto;
            max-height: 400px;
            position: relative;
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
            background: #f3e5f5;
            border-left-color: #9c27b0;
        }}
        
        .message-header {{
            display: flex;
            justify-content: between;
            align-items: center;
            margin-bottom: 5px;
            font-size: 0.9em;
        }}
        
        .username {{
            font-weight: bold;
            color: #667eea;
        }}
        
        .timestamp {{
            color: #666;
            font-size: 0.8em;
        }}
        
        .message-actions {{
            position: absolute;
            top: 5px;
            right: 10px;
            opacity: 0;
            transition: opacity 0.3s ease;
        }}
        
        .message:hover .message-actions {{
            opacity: 1;
        }}
        
        .reply-btn {{
            background: none;
            border: none;
            cursor: pointer;
            padding: 4px 8px;
            border-radius: 4px;
            font-size: 12px;
            color: #666;
            transition: background 0.2s;
        }}
        
        .reply-btn:hover {{
            background: rgba(0,0,0,0.1);
        }}
        
        .reply-reference {{
            background: rgba(0,0,0,0.05);
            padding: 8px 12px;
            border-radius: 8px;
            margin-bottom: 8px;
            font-size: 0.85em;
            color: #666;
            border-left: 3px solid #9c27b0;
        }}
        
        .reply-username {{
            font-weight: bold;
            color: #9c27b0;
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
            background: rgba(156, 39, 176, 0.1);
            padding: 10px;
            border-radius: 8px;
            border-left: 3px solid #9c27b0;
            display: none;
        }}
        
        .reply-preview-header {{
            display: flex;
            justify-content: between;
            align-items: center;
            margin-bottom: 5px;
        }}
        
        .reply-preview-username {{
            font-weight: bold;
            color: #9c27b0;
            font-size: 0.9em;
        }}
        
        .cancel-reply {{
            background: none;
            border: none;
            cursor: pointer;
            color: #666;
            font-size: 16px;
            padding: 2px;
        }}
        
        .reply-preview-text {{
            font-size: 0.85em;
            color: #666;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
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
            min-height: 42px;
            max-height: 120px;
        }}
        
        #messageInput:focus {{
            border-color: #667eea;
            outline: none;
        }}
        
        .emoji-buttons {{
            display: flex;
            gap: 5px;
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
            white-space: nowrap;
        }}
        
        #sendButton:hover {{
            background: #5a6fd8;
            transform: translateY(-1px);
        }}
        
        .no-messages {{
            text-align: center;
            color: #666;
            font-style: italic;
            padding: 40px;
        }}
        
        .voice-maintenance {{
            background: rgba(255, 255, 255, 0.95);
            border-radius: 15px;
            padding: 40px;
            text-align: center;
            display: flex;
            flex-direction: column;
            justify-content: center;
            align-items: center;
            gap: 20px;
        }}
        
        .maintenance-icon {{
            font-size: 4em;
            color: #ff9800;
        }}
        
        .maintenance-title {{
            font-size: 1.5em;
            color: #333;
            font-weight: bold;
        }}
        
        .maintenance-message {{
            color: #666;
            line-height: 1.6;
            max-width: 400px;
        }}
        
        @keyframes slideIn {{
            from {{
                opacity: 0;
                transform: translateY(20px);
            }}
            to {{
                opacity: 1;
                transform: translateY(0);
            }}
        }}
        
        @keyframes fadeIn {{
            from {{ opacity: 0; }}
            to {{ opacity: 1; }}
        }}
        
        .scroll-to-bottom {{
            position: absolute;
            bottom: 20px;
            right: 20px;
            background: #667eea;
            color: white;
            border: none;
            width: 40px;
            height: 40px;
            border-radius: 50%;
            cursor: pointer;
            display: none;
            align-items: center;
            justify-content: center;
            box-shadow: 0 2px 10px rgba(0,0,0,0.2);
            transition: all 0.3s ease;
        }}
        
        .scroll-to-bottom:hover {{
            background: #5a6fd8;
            transform: scale(1.1);
        }}
        
        @media (max-width: 768px) {{
            .main-container {{
                flex-direction: column;
                padding: 10px;
            }}
            
            .sidebar {{
                width: 100%;
                flex-direction: row;
                gap: 10px;
            }}
            
            .online-users, .typing-indicators {{
                flex: 1;
                max-height: 200px;
            }}
            
            .input-row {{
                flex-direction: column;
                align-items: stretch;
            }}
            
            .emoji-buttons {{
                justify-content: center;
            }}
            
            .user-info {{
                position: static;
                justify-content: center;
                margin-bottom: 10px;
            }}
        }}
    </style>
</head>
<body>
    <div class="header">
        <div class="user-info">
            <div class="username-display">👤 {username}</div>
            <button class="logout-btn" onclick="logout()">🚪 Logout</button>
        </div>
        <h1>💬 Enhanced Chatroom</h1>
        <div class="tabs">
            <button class="tab-btn active" onclick="switchTab('chat')">💬 Chat</button>
            <button class="tab-btn" onclick="switchTab('voice')">🎤 Voice</button>
        </div>
        <div class="stats-container" id="statsContainer">🔄 Loading...</div>
    </div>
    
    <div class="main-container">
        <div class="sidebar">
            <div class="online-users">
                <h3>🟢 Online Users (<span id="onlineCount">0</span>)</h3>
                <div id="onlineUsersList">
                    <div style="color: #666; text-align: center; padding: 20px;">Loading...</div>
                </div>
            </div>
            
            <div class="typing-indicators">
                <h3>⌨️ Typing</h3>
                <div id="typingList">
                    <div style="color: #666; text-align: center; padding: 20px;">No one is typing</div>
                </div>
            </div>
        </div>
        
        <div class="chat-area">
            <!-- Chat Tab -->
            <div id="chatTab" class="tab-content active">
                <div class="messages-container" id="messagesContainer">
                    <div class="no-messages">Welcome to the enhanced chatroom! 🚀</div>
                    <button class="scroll-to-bottom" id="scrollToBottom" onclick="scrollToBottom()">↓</button>
                </div>
                
                <div class="input-container">
                    <div class="reply-preview" id="replyPreview">
                        <div class="reply-preview-header">
                            <span class="reply-preview-username" id="replyUsername"></span>
                            <button class="cancel-reply" onclick="cancelReply()">✕</button>
                        </div>
                        <div class="reply-preview-text" id="replyText"></div>
                    </div>
                    
                    <div class="input-row">
                        <textarea id="messageInput" placeholder="Type your message..." rows="1" maxlength="500"></textarea>
                        <div class="emoji-buttons">
                            <button class="emoji-btn" onclick="addEmoji('😊')">😊</button>
                            <button class="emoji-btn" onclick="addEmoji('👍')">👍</button>
                            <button class="emoji-btn" onclick="addEmoji('❤️')">❤️</button>
                            <button class="emoji-btn" onclick="addEmoji('😂')">😂</button>
                            <button class="emoji-btn" onclick="addEmoji('🎉')">🎉</button>
                        </div>
                        <button id="sendButton" onclick="sendMessage()">Send 📤</button>
                    </div>
                </div>
            </div>
            
            <!-- Voice Tab -->
            <div id="voiceTab" class="tab-content">
                <div class="voice-maintenance">
                    <div class="maintenance-icon">🔧</div>
                    <div class="maintenance-title">Voice Feature Under Maintenance</div>
                    <div class="maintenance-message">
                        We're working hard to improve the voice chat experience! 
                        The voice feature is temporarily unavailable while we implement 
                        new enhancements and fix some technical issues.
                        <br><br>
                        Please check back soon. In the meantime, enjoy our enhanced 
                        text chat with reply features, typing indicators, and real-time 
                        online user tracking!
                    </div>
                </div>
            </div>
        </div>
    </div>

    <script>
        let currentUser = '{username}';
        let lastMessageId = 0;
        let replyToMessage = null;
        let typingTimeout = null;
        let isUserScrolling = false;
        let shouldAutoScroll = true;
        
        // Initialize
        document.addEventListener('DOMContentLoaded', function() {{
            initializeChat();
            console.log('🎉 Enhanced Chatroom loaded!');
            console.log('👤 Logged in as:', currentUser);
        }});
        
        function initializeChat() {{
            setupMessageInput();
            setupScrollDetection();
            startPeriodicUpdates();
            loadMessages();
            updateOnlineUsers();
        }}
        
        function setupMessageInput() {{
            const messageInput = document.getElementById('messageInput');
            
            // Auto-resize textarea
            messageInput.addEventListener('input', function() {{
                this.style.height = 'auto';
                this.style.height = Math.min(this.scrollHeight, 120) + 'px';
                
                // Handle typing indicator
                handleTyping();
            }});
            
            // Send on Enter
            messageInput.addEventListener('keydown', function(e) {{
                if (e.key === 'Enter' && !e.shiftKey) {{
                    e.preventDefault();
                    sendMessage();
                }}
            }});
        }}
        
        function setupScrollDetection() {{
            const container = document.getElementById('messagesContainer');
            
            container.addEventListener('scroll', function() {{
                const isAtBottom = container.scrollTop + container.clientHeight >= container.scrollHeight - 50;
                shouldAutoScroll = isAtBottom;
                
                // Show/hide scroll to bottom button
                const scrollBtn = document.getElementById('scrollToBottom');
                if (isAtBottom) {{
                    scrollBtn.style.display = 'none';
                }} else {{
                    scrollBtn.style.display = 'flex';
                }}
            }});
        }}
        
        function startPeriodicUpdates() {{
            // Load messages every 2 seconds
            setInterval(loadMessages, 2000);
            
            // Update online users every 5 seconds
            setInterval(updateOnlineUsers, 5000);
            
            // Update typing indicators every 1 second
            setInterval(updateTypingIndicators, 1000);
            
            // Send heartbeat every 30 seconds
            setInterval(sendHeartbeat, 30000);
        }}
        
        async function logout() {{
            try {{
                await fetch('/api/auth/logout', {{ method: 'POST' }});
                document.cookie = 'session_id=; path=/; expires=Thu, 01 Jan 1970 00:00:01 GMT;';
                window.location.href = '/';
            }} catch (error) {{
                console.error('Logout error:', error);
                window.location.href = '/';
            }}
        }}
        
        function switchTab(tabName) {{
            document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
            event.target.classList.add('active');
            
            document.querySelectorAll('.tab-content').forEach(content => content.classList.remove('active'));
            document.getElementById(tabName + 'Tab').classList.add('active');
        }}
        
        function addEmoji(emoji) {{
            const input = document.getElementById('messageInput');
            const start = input.selectionStart;
            const end = input.selectionEnd;
            const text = input.value;
            
            input.value = text.substring(0, start) + emoji + text.substring(end);
            input.selectionStart = input.selectionEnd = start + emoji.length;
            input.focus();
            
            // Trigger input event for auto-resize
            input.dispatchEvent(new Event('input'));
        }}
        
        function replyToMsg(messageId, username, text) {{
            replyToMessage = {{
                id: messageId,
                username: username,
                text: text.length > 50 ? text.substring(0, 50) + '...' : text
            }};
            
            document.getElementById('replyUsername').textContent = username;
            document.getElementById('replyText').textContent = replyToMessage.text;
            document.getElementById('replyPreview').style.display = 'block';
            document.getElementById('messageInput').focus();
        }}
        
        function cancelReply() {{
            replyToMessage = null;
            document.getElementById('replyPreview').style.display = 'none';
        }}
        
        function sendMessage() {{
            const messageText = document.getElementById('messageInput').value.trim();
            if (!messageText) return;
            
            const message = {{
                text: messageText,
                timestamp: new Date().toISOString(),
                replyTo: replyToMessage ? replyToMessage.id : null
            }};
            
            fetch('/api/chat/send', {{
                method: 'POST',
                headers: {{
                    'Content-Type': 'application/json',
                }},
                body: JSON.stringify(message)
            }})
            .then(response => response.json())
            .then(data => {{
                if (data.success) {{
                    document.getElementById('messageInput').value = '';
                    document.getElementById('messageInput').style.height = 'auto';
                    cancelReply();
                    shouldAutoScroll = true;
                    if (data.messageId) {{
                        lastMessageId = data.messageId;
                    }}
                }} else if (data.error === 'Not authenticated') {{
                    alert('Session expired. Please login again.');
                    window.location.href = '/';
                }}
            }})
            .catch(error => {{
                console.error('Error sending message:', error);
            }});
        }}
        
        function loadMessages() {{
            fetch(`/api/chat/messages?since=${{lastMessageId}}`)
                .then(response => response.json())
                .then(data => {{
                    if (data.error === 'Not authenticated') {{
                        window.location.href = '/';
                        return;
                    }}
                    
                    if (data.messages && data.messages.length > 0) {{
                        displayMessages(data.messages);
                        lastMessageId = data.lastId;
                    }}
                    
                    updateStats(data);
                }})
                .catch(error => {{
                    console.error('Error loading messages:', error);
                }});
        }}
        
        function displayMessages(messages) {{
            const container = document.getElementById('messagesContainer');
            const noMessages = container.querySelector('.no-messages');
            
            if (noMessages && messages.length > 0) {{
                noMessages.remove();
            }}
            
            messages.forEach(message => {{
                const existingMessage = document.getElementById(`message-${{message.id}}`);
                if (existingMessage) {{
                    return;
                }}
                
                const messageDiv = document.createElement('div');
                let messageClass = 'message';
                if (message.username === currentUser) messageClass += ' own';
                if (message.replyTo) messageClass += ' reply';
                
                messageDiv.className = messageClass;
                messageDiv.id = `message-${{message.id}}`;
                
                const timestamp = new Date(message.timestamp).toLocaleTimeString();
                
                let replyHtml = '';
                if (message.replyTo) {{
                    const replyMsg = findMessageById(message.replyTo);
                    if (replyMsg) {{
                        replyHtml = `
                            <div class="reply-reference">
                                <span class="reply-username">${{escapeHtml(replyMsg.username)}}</span>
                                <div>${{escapeHtml(replyMsg.text.length > 50 ? replyMsg.text.substring(0, 50) + '...' : replyMsg.text)}}</div>
                            </div>
                        `;
                    }}
                }}
                
                messageDiv.innerHTML = `
                    <div class="message-actions">
                        <button class="reply-btn" onclick="replyToMsg(${{message.id}}, '${{escapeHtml(message.username)}}', '${{escapeHtml(message.text)}}')">↩️</button>
                    </div>
                    <div class="message-header">
                        <span class="username">${{escapeHtml(message.username)}}</span>
                        <span class="timestamp">${{timestamp}}</span>
                    </div>
                    ${{replyHtml}}
                    <div class="message-text">${{escapeHtml(message.text)}}</div>
                `;
                
                container.appendChild(messageDiv);
            }});
            
            if (shouldAutoScroll) {{
                scrollToBottom();
            }}
        }}
        
        function findMessageById(id) {{
            const messageElement = document.getElementById(`message-${{id}}`);
            if (!messageElement) return null;
            
            const username = messageElement.querySelector('.username').textContent;
            const text = messageElement.querySelector('.message-text').textContent;
            return {{ username, text }};
        }}
        
        function scrollToBottom() {{
            const container = document.getElementById('messagesContainer');
            container.scrollTop = container.scrollHeight;
            shouldAutoScroll = true;
        }}
        
        function updateStats(data) {{
            const stats = document.getElementById('statsContainer');
            stats.textContent = `💬 ${{data.messageCount || 0}} messages`;
        }}
        
        function updateOnlineUsers() {{
            fetch('/api/users/online')
                .then(response => response.json())
                .then(data => {{
                    if (data.error === 'Not authenticated') {{
                        window.location.href = '/';
                        return;
                    }}
                    
                    const usersList = document.getElementById('onlineUsersList');
                    const count = document.getElementById('onlineCount');
                    
                    count.textContent = data.users.length;
                    
                    if (data.users.length === 0) {{
                        usersList.innerHTML = '<div style="color: #666; text-align: center; padding: 20px;">No users online</div>';
                        return;
                    }}
                    
                    usersList.innerHTML = data.users.map(user => `
                        <div class="user-item ${{user.username === currentUser ? 'current-user' : ''}}">
                            <div class="user-status"></div>
                            <span>${{escapeHtml(user.username)}}</span>
                        </div>
                    `).join('');
                }})
                .catch(error => {{
                    console.error('Error updating online users:', error);
                }});
        }}
        
        function updateTypingIndicators() {{
            fetch('/api/users/typing')
                .then(response => response.json())
                .then(data => {{
                    if (data.error === 'Not authenticated') {{
                        return;
                    }}
                    
                    const typingList = document.getElementById('typingList');
                    
                    if (data.typing.length === 0) {{
                        typingList.innerHTML = '<div style="color: #666; text-align: center; padding: 20px;">No one is typing</div>';
                        return;
                    }}
                    
                    typingList.innerHTML = data.typing.map(username => `
                        <div class="typing-item">
                            ${{escapeHtml(username)}} is typing<span class="typing-dots"></span>
                        </div>
                    `).join('');
                }})
                .catch(error => {{
                    console.error('Error updating typing indicators:', error);
                }});
        }}
        
        function handleTyping() {{
            // Send typing indicator
            fetch('/api/users/typing', {{
                method: 'POST',
                headers: {{
                    'Content-Type': 'application/json',
                }},
                body: JSON.stringify({{ typing: true }})
            }});
            
            // Clear existing timeout
            if (typingTimeout) {{
                clearTimeout(typingTimeout);
            }}
            
            // Stop typing after 3 seconds of inactivity
            typingTimeout = setTimeout(() => {{
                fetch('/api/users/typing', {{
                    method: 'POST',
                    headers: {{
                        'Content-Type': 'application/json',
                    }},
                    body: JSON.stringify({{ typing: false }})
                }});
            }}, 3000);
        }}
        
        function sendHeartbeat() {{
            fetch('/api/users/heartbeat', {{ method: 'POST' }})
                .catch(error => {{
                    console.error('Heartbeat failed:', error);
                }});
        }}
        
        function escapeHtml(text) {{
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }}
    </script>
</body>
</html>
        """
        
        self.send_response(200)
        self.send_header("Content-type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(html_content.encode('utf-8'))
    
    def get_session_from_cookies(self):
        """Extract session ID from cookies"""
        cookie_header = self.headers.get('Cookie', '')
        for part in cookie_header.split(';'):
            part = part.strip()
            if part.startswith('session_id='):
                return part.split('=', 1)[1]
        return None
    
    def is_valid_session(self, session_id):
        """Check if session is valid and not expired"""
        with users_lock:
            session = user_sessions.get(session_id)
            if not session:
                return False
            
            if datetime.now() > session['expires']:
                del user_sessions[session_id]
                return False
            
            return True
    
    def get_username_from_session(self, session_id):
        """Get username from valid session"""
        with users_lock:
            session = user_sessions.get(session_id)
            return session['username'] if session else None
    
    def handle_api(self, path):
        """Handle API endpoints"""
        if path == '/api/auth/register':
            self.handle_register()
        elif path == '/api/auth/login':
            self.handle_login()
        elif path == '/api/auth/logout':
            self.handle_logout()
        elif path == '/api/auth/check':
            self.handle_auth_check()
        elif path == '/api/chat/send':
            self.handle_chat_send()
        elif path.startswith('/api/chat/messages'):
            self.handle_chat_messages(path)
        elif path == '/api/users/online':
            self.handle_online_users()
        elif path == '/api/users/typing':
            if self.command == 'POST':
                self.handle_typing_post()
            else:
                self.handle_typing_get()
        elif path == '/api/users/heartbeat':
            self.handle_heartbeat()
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
            
            username = data.get('username', '').strip()
            password = data.get('password', '')
            
            if not username or not password:
                self.send_json_response({"success": False, "error": "Username and password are required"})
                return
            
            if len(username) < 3 or len(username) > 20:
                self.send_json_response({"success": False, "error": "Username must be 3-20 characters"})
                return
            
            if not username.replace('_', '').isalnum():
                self.send_json_response({"success": False, "error": "Username can only contain letters, numbers, and underscores"})
                return
            
            if len(password) < 4:
                self.send_json_response({"success": False, "error": "Password must be at least 4 characters"})
                return
            
            with users_lock:
                if username.lower() in [u.lower() for u in users_db.keys()]:
                    self.send_json_response({"success": False, "error": "Username already taken"})
                    return
                
                users_db[username] = {
                    "password_hash": data_persistence.hash_password(password),
                    "created": datetime.now(),
                    "last_seen": datetime.now()
                }
            
            threading.Thread(target=data_persistence.backup_to_github_gist, daemon=True).start()
            
            self.send_json_response({"success": True, "message": "Account created successfully"})
            
        except json.JSONDecodeError:
            self.send_json_response({"success": False, "error": "Invalid JSON"})
        except Exception as e:
            self.send_json_response({"success": False, "error": f"Registration failed: {str(e)}"})
    
    def handle_login(self):
        """Handle user login"""
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length)
            data = json.loads(post_data.decode('utf-8'))
            
            username = data.get('username', '').strip()
            password = data.get('password', '')
            
            if not username or not password:
                self.send_json_response({"success": False, "error": "Username and password are required"})
                return
            
            with users_lock:
                user_data = users_db.get(username)
                if not user_data:
                    self.send_json_response({"success": False, "error": "Invalid username or password"})
                    return
                
                password_hash = data_persistence.hash_password(password)
                if password_hash != user_data["password_hash"]:
                    self.send_json_response({"success": False, "error": "Invalid username or password"})
                    return
                
                user_data["last_seen"] = datetime.now()
                
                session_id = data_persistence.generate_session_id()
                user_sessions[session_id] = {
                    "username": username,
                    "expires": datetime.now() + timedelta(hours=24),
                    "last_activity": datetime.now()
                }
            
            self.send_json_response({
                "success": True, 
                "message": "Login successful",
                "session_id": session_id,
                "username": username
            })
            
        except json.JSONDecodeError:
            self.send_json_response({"success": False, "error": "Invalid JSON"})
        except Exception as e:
            self.send_json_response({"success": False, "error": f"Login failed: {str(e)}"})
    
    def handle_logout(self):
        """Handle user logout"""
        session_id = self.get_session_from_cookies()
        if session_id:
            with users_lock:
                user_sessions.pop(session_id, None)
                online_users.pop(session_id, None)
        
        self.send_json_response({"success": True, "message": "Logged out successfully"})
    
    def handle_auth_check(self):
        """Check if user is authenticated"""
        session_id = self.get_session_from_cookies()
        if session_id and self.is_valid_session(session_id):
            username = self.get_username_from_session(session_id)
            self.send_json_response({
                "authenticated": True,
                "username": username
            })
        else:
            self.send_json_response({"authenticated": False})
    
    def handle_chat_send(self):
        """Handle sending a new chat message"""
        session_id = self.get_session_from_cookies()
        if not session_id or not self.is_valid_session(session_id):
            self.send_json_response({"success": False, "error": "Not authenticated"})
            return
        
        username = self.get_username_from_session(session_id)
        if not username:
            self.send_json_response({"success": False, "error": "Not authenticated"})
            return
        
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length)
            message_data = json.loads(post_data.decode('utf-8'))
            
            text = message_data.get('text', '')[:500]
            reply_to = message_data.get('replyTo')
            
            if not text.strip():
                self.send_json_response({"success": False, "error": "Empty message"})
                return
            
            with chatroom_lock:
                new_id = max([msg['id'] for msg in chatroom_messages], default=0) + 1
                
                message = {
                    'id': new_id,
                    'username': username,
                    'text': text.strip(),
                    'timestamp': datetime.now().isoformat(),
                    'ip': self.client_address[0],
                    'replyTo': reply_to
                }
                chatroom_messages.append(message)
                
                if len(chatroom_messages) > 100:
                    chatroom_messages.pop(0)
                    for i, msg in enumerate(chatroom_messages):
                        msg['id'] = i + 1
            
            # Remove user from typing list
            with users_lock:
                typing_users.pop(username, None)
            
            self.send_json_response({"success": True, "message": "Message sent", "messageId": new_id})
            
        except json.JSONDecodeError:
            self.send_json_response({"success": False, "error": "Invalid JSON"})
        except Exception as e:
            self.send_json_response({"success": False, "error": str(e)})
    
    def handle_chat_messages(self, path):
        """Handle retrieving chat messages"""
        session_id = self.get_session_from_cookies()
        if not session_id or not self.is_valid_session(session_id):
            self.send_json_response({"error": "Not authenticated"})
            return
        
        query_params = urllib.parse.parse_qs(urllib.parse.urlparse(path).query)
        since_id = int(query_params.get('since', [0])[0])
        
        with chatroom_lock:
            new_messages = [msg for msg in chatroom_messages if msg['id'] > since_id]
            
            response_data = {
                "messages": new_messages,
                "lastId": chatroom_messages[-1]['id'] if chatroom_messages else 0,
                "messageCount": len(chatroom_messages)
            }
        
        self.send_json_response(response_data)
    
    def handle_online_users(self):
        """Handle getting online users"""
        session_id = self.get_session_from_cookies()
        if not session_id or not self.is_valid_session(session_id):
            self.send_json_response({"error": "Not authenticated"})
            return
        
        # Update current user's presence
        with users_lock:
            username = self.get_username_from_session(session_id)
            if username:
                online_users[session_id] = {
                    'username': username,
                    'last_ping': datetime.now()
                }
            
            # Get all online users
            users = [{"username": data['username']} for data in online_users.values()]
            # Sort users alphabetically
            users.sort(key=lambda x: x['username'].lower())
        
        self.send_json_response({"users": users})
    
    def handle_typing_post(self):
        """Handle typing indicator updates"""
        session_id = self.get_session_from_cookies()
        if not session_id or not self.is_valid_session(session_id):
            self.send_json_response({"error": "Not authenticated"})
            return
        
        username = self.get_username_from_session(session_id)
        if not username:
            self.send_json_response({"error": "Not authenticated"})
            return
        
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length)
            data = json.loads(post_data.decode('utf-8'))
            
            is_typing = data.get('typing', False)
            
            with users_lock:
                if is_typing:
                    typing_users[username] = datetime.now()
                else:
                    typing_users.pop(username, None)
            
            self.send_json_response({"success": True})
            
        except json.JSONDecodeError:
            self.send_json_response({"success": False, "error": "Invalid JSON"})
        except Exception as e:
            self.send_json_response({"success": False, "error": str(e)})
    
    def handle_typing_get(self):
        """Handle getting typing indicators"""
        session_id = self.get_session_from_cookies()
        if not session_id or not self.is_valid_session(session_id):
            self.send_json_response({"error": "Not authenticated"})
            return
        
        current_username = self.get_username_from_session(session_id)
        current_time = datetime.now()
        
        with users_lock:
            # Remove expired typing indicators (older than 5 seconds)
            expired_users = [
                username for username, timestamp in typing_users.items()
                if (current_time - timestamp).total_seconds() > 5
            ]
            
            for username in expired_users:
                typing_users.pop(username, None)
            
            # Get current typing users (excluding current user)
            typing = [
                username for username in typing_users.keys()
                if username != current_username
            ]
            typing.sort()
        
        self.send_json_response({"typing": typing})
    
    def handle_heartbeat(self):
        """Handle user heartbeat to maintain online status"""
        session_id = self.get_session_from_cookies()
        if not session_id or not self.is_valid_session(session_id):
            self.send_json_response({"error": "Not authenticated"})
            return
        
        username = self.get_username_from_session(session_id)
        if username:
            with users_lock:
                online_users[session_id] = {
                    'username': username,
                    'last_ping': datetime.now()
                }
                
                # Update session activity
                if session_id in user_sessions:
                    user_sessions[session_id]['last_activity'] = datetime.now()
        
        self.send_json_response({"success": True})
    
    def handle_status(self):
        """Handle server status"""
        with chatroom_lock, users_lock:
            message_count = len(chatroom_messages)
            user_count = len(users_db)
            active_sessions = len([s for s in user_sessions.values() if datetime.now() < s['expires']])
            online_count = len(online_users)
            typing_count = len(typing_users)
        
        data = {
            "status": "online",
            "server": "Enhanced Chatroom Server",
            "version": "7.0",
            "timestamp": time.time(),
            "total_messages": message_count,
            "total_users": user_count,
            "active_sessions": active_sessions,
            "online_users": online_count,
            "typing_users": typing_count,
            "features": [
                "user_authentication", 
                "persistent_storage", 
                "github_gist_backup",
                "text_chat", 
                "reply_system",
                "typing_indicators",
                "online_user_tracking",
                "smart_scrolling",
                "emoji_support"
            ],
            "backup_status": {
                "github_gist_configured": bool(GITHUB_GIST_TOKEN and GITHUB_GIST_ID),
                "webhook_configured": bool(EXTERNAL_BACKUP_URL)
            },
            "uptime": "Running with enhanced features! 🚀💬✨"
        }
        
        self.send_json_response(data)
    
    def send_json_response(self, data):
        """Helper method to send JSON responses"""
        response = json.dumps(data, indent=2)
        self.send_response(200)
        self.send_header("Content-type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
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
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

def cleanup_expired_sessions():
    """Background task to clean up expired sessions and inactive users"""
    while True:
        time.sleep(3600)  # Run every hour
        current_time = datetime.now()
        
        with users_lock:
            # Clean expired sessions
            expired_sessions = [
                session_id for session_id, session_data in user_sessions.items()
                if current_time > session_data['expires']
            ]
            for session_id in expired_sessions:
                del user_sessions[session_id]
                online_users.pop(session_id, None)
            
            # Clean inactive online users (inactive for more than 5 minutes)
            inactive_online = [
                session_id for session_id, data in online_users.items()
                if (current_time - data['last_ping']).total_seconds() > 300
            ]
            for session_id in inactive_online:
                del online_users[session_id]
            
            # Clean old typing indicators
            inactive_typing = [
                username for username, timestamp in typing_users.items()
                if (current_time - timestamp).total_seconds() > 30
            ]
            for username in inactive_typing:
                del typing_users[username]
        
        if expired_sessions or inactive_online or inactive_typing:
            print(f"🧹 Cleaned up {len(expired_sessions)} expired sessions, {len(inactive_online)} inactive users, {len(inactive_typing)} stale typing indicators")

def main():
    # Restore data from backup on startup
    print("🔄 Restoring data from backups...")
    if data_persistence.restore_from_github_gist():
        print("✅ Data restored from GitHub Gist backup")
    else:
        print("⚠️ No backup found or failed to restore - starting fresh")
    
    # Start background tasks
    backup_thread = threading.Thread(target=backup_data_periodically, daemon=True)
    backup_thread.start()
    
    cleanup_thread = threading.Thread(target=cleanup_expired_sessions, daemon=True)
    cleanup_thread.start()
    
    inactive_cleanup_thread = threading.Thread(target=cleanup_inactive_users, daemon=True)
    inactive_cleanup_thread.start()
    
    try:
        with socketserver.TCPServer(("0.0.0.0", PORT), ChatroomHandler) as httpd:
            print("🚀" * 60)
            print(f"✨💬 ENHANCED CHATROOM SERVER STARTED!")
            print("🚀" * 60)
            print(f"🌐 Server URL: http://localhost:{PORT}")
            print(f"📂 Directory: {os.getcwd()}")
            print(f"🗄️ Loaded Users: {len(users_db)}")
            print(f"💬 Loaded Messages: {len(chatroom_messages)}")
            
            print("\n🔐 AUTHENTICATION FEATURES:")
            print("   👤 User registration with username + password")
            print("   🔑 Secure login system with sessions")
            print("   🕒 24-hour session expiration")
            print("   🚪 Logout functionality")
            print("   ⚡ Session validation on all requests")
            
            print("\n✨ NEW ENHANCED FEATURES:")
            print("   🟢 Real-time online user tracking")
            print("   ⌨️ Live typing indicators")
            print("   ↩️ Reply to specific messages")
            print("   📜 Smart scrolling behavior")
            print("   📱 Mobile-responsive design")
            print("   💔 Voice feature removed (maintenance mode)")
            
            print("\n💾 PERSISTENCE FEATURES:")
            print("   📦 GitHub Gist backup (primary)")
            print("   🔄 Auto-backup every 5 minutes")
            print("   📤 Webhook backup (secondary)")
            print("   🔧 Data restoration on server restart")
            print("   🧹 Automatic cleanup tasks")
            
            print("\n🌐 API ENDPOINTS:")
            print("   🏠 GET / (Login/Register page)")
            print("   💬 GET /chat (Enhanced chatroom)")
            print("   📝 POST /api/auth/register")
            print("   🔑 POST /api/auth/login")
            print("   🚪 POST /api/auth/logout")
            print("   ✅ GET /api/auth/check")
            print("   📤 POST /api/chat/send")
            print("   📥 GET /api/chat/messages")
            print("   🟢 GET /api/users/online")
            print("   ⌨️ GET/POST /api/users/typing")
            print("   💓 POST /api/users/heartbeat")
            print("   📊 GET /api/status")
            
            backup_status = "✅ Configured" if GITHUB_GIST_TOKEN and GITHUB_GIST_ID else "❌ Not configured"
            webhook_status = "✅ Configured" if EXTERNAL_BACKUP_URL else "⚠️ Optional"
            
            print(f"\n💾 BACKUP STATUS:")
            print(f"   GitHub Gist: {backup_status}")
            print(f"   Webhook URL: {webhook_status}")
            
            if not GITHUB_GIST_TOKEN or not GITHUB_GIST_ID:
                print("\n⚠️  WARNING: No backup configured!")
                print("   Your data will be lost when server restarts.")
                print("   Please set GITHUB_GIST_TOKEN and GITHUB_GIST_ID environment variables.")
            
            print("\n🛑 Press Ctrl+C to stop the server")
            print("=" * 60)
            
            httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n🛑 Server stopped by user")
        print("💾 Performing final backup...")
        data_persistence.backup_to_github_gist()
        print("👋 Thanks for using the enhanced chatroom!")
    except Exception as e:
        print(f"❌ Server error: {e}")

if __name__ == "__main__":
    main()
