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
user_sessions = {}  # session_id -> {"username": str, "expires": datetime}
users_lock = threading.Lock()

# Persistence configuration
GITHUB_GIST_TOKEN = os.environ.get('GITHUB_GIST_TOKEN', '')  # Set this in Render environment
GITHUB_GIST_ID = os.environ.get('GITHUB_GIST_ID', '')  # Set this after first run
BACKUP_INTERVAL = 300  # 5 minutes
EXTERNAL_BACKUP_URL = os.environ.get('BACKUP_WEBHOOK_URL', '')  # Optional webhook backup

class DataPersistence:
    """Handles multiple backup strategies for data persistence"""
    
    @staticmethod
    def hash_password(password):
        """Hash password with salt"""
        salt = "chatroom_salt_2024"  # In production, use random salt per user
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
                    "messages": chatroom_messages[-50:],  # Keep last 50 messages
                    "stats": {
                        "total_users": len(users_db),
                        "total_messages": len(chatroom_messages)
                    }
                }
            
            # Update GitHub Gist
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

# Initialize data persistence
data_persistence = DataPersistence()

def backup_data_periodically():
    """Background thread to backup data periodically"""
    while True:
        time.sleep(BACKUP_INTERVAL)
        print(f"🔄 Starting periodic backup...")
        
        # Try GitHub Gist first
        if data_persistence.backup_to_github_gist():
            print("✅ Primary backup (GitHub Gist) successful")
        else:
            print("⚠️ Primary backup failed, trying webhook...")
            data_persistence.backup_to_webhook()

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
            <h3>🔐 Secure & Persistent</h3>
            <p>• Your data is backed up automatically</p>
            <p>• Accounts persist across server restarts</p>
            <p>• Voice + text chat with friends</p>
            <p>• Mobile-friendly interface</p>
        </div>
    </div>

    <script>
        function switchForm(formType) {
            // Update tabs
            document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
            event.target.classList.add('active');
            
            // Update forms
            document.querySelectorAll('.form-container').forEach(form => form.classList.remove('active'));
            document.getElementById(formType + 'Form').classList.add('active');
            
            // Clear messages
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
                    // Store session and redirect
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
                    // Switch to login form
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
        
        // Check if already logged in
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
        """Serve the chatroom - but check authentication first"""
        # Check if user is authenticated
        session_id = self.get_session_from_cookies()
        if not session_id or not self.is_valid_session(session_id):
            # Redirect to login
            self.send_response(302)
            self.send_header("Location", "/")
            self.end_headers()
            return
        
        # Get username from session
        username = user_sessions.get(session_id, {}).get('username', 'Anonymous')
        
        """Serve the public chatroom interface with voice room connected to Render server"""
        html_content = f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Chatroom + Voice Room 🎤💬</title>
    <script src="https://cdn.socket.io/4.7.2/socket.io.min.js"></script>
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
        
        .online-count {{
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
        .messages-container {{
            flex: 1;
            background: rgba(255, 255, 255, 0.95);
            border-radius: 15px 15px 0 0;
            padding: 20px;
            overflow-y: auto;
            max-height: 400px;
            margin-bottom: 0;
            position: relative;
        }}
        
        .scroll-down-btn {{
            position: absolute;
            bottom: 20px;
            right: 20px;
            background: #667eea;
            color: white;
            border: none;
            border-radius: 50%;
            width: 50px;
            height: 50px;
            font-size: 20px;
            cursor: pointer;
            box-shadow: 0 4px 15px rgba(0,0,0,0.2);
            transition: all 0.3s ease;
            z-index: 100;
            display: none;
        }}
        
        .scroll-down-btn:hover {{
            background: #5a6fd8;
            transform: scale(1.1);
        }}
        
        .scroll-down-btn.show {{
            display: block;
            animation: bounceIn 0.3s ease-out;
        }}
        
        @keyframes bounceIn {{
            0% {{ transform: scale(0); opacity: 0; }}
            50% {{ transform: scale(1.2); opacity: 1; }}
            100% {{ transform: scale(1); opacity: 1; }}
        }}
        
        .message {{
            margin-bottom: 15px;
            padding: 12px 15px;
            border-radius: 10px;
            background: #f8f9fa;
            border-left: 4px solid #667eea;
            animation: slideIn 0.3s ease-out;
        }}
        
        .message.own {{
            background: #e3f2fd;
            border-left-color: #2196F3;
            margin-left: 50px;
        }}
        
        .message.voice {{
            background: #fff3e0;
            border-left-color: #ff9800;
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
        }}
        
        .timestamp {{
            color: #666;
            font-size: 0.8em;
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
            gap: 10px;
            align-items: center;
        }}
        
        #messageInput {{
            flex: 1;
            padding: 12px;
            border: 2px solid #ddd;
            border-radius: 8px;
            font-size: 14px;
            resize: none;
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
        
        .no-messages {{
            text-align: center;
            color: #666;
            font-style: italic;
            padding: 40px;
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
        
        .emoji-btn {{
            background: none;
            border: none;
            font-size: 18px;
            cursor: pointer;
            padding: 5px;
            border-radius: 3px;
            transition: background 0.2s;
        }}
        
        .emoji-btn:hover {{
            background: rgba(0,0,0,0.1);
        }}
        
        /* Voice Room Styles */
        .voice-container {{
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
        }}
        
        .voice-controls {{
            display: flex;
            gap: 15px;
            align-items: center;
            flex-wrap: wrap;
            justify-content: center;
        }}
        
        .voice-btn {{
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
        }}
        
        .voice-btn:hover {{
            transform: translateY(-2px);
            box-shadow: 0 4px 15px rgba(0,0,0,0.2);
        }}
        
        .voice-btn.recording {{
            background: #f44336;
            animation: pulse 1.5s infinite;
        }}
        
        .voice-btn.disabled {{
            background: #ccc;
            cursor: not-allowed;
        }}
        
        @keyframes pulse {{
            0% {{ transform: scale(1); }}
            50% {{ transform: scale(1.05); }}
            100% {{ transform: scale(1); }}
        }}
        
        .voice-status {{
            background: rgba(0,0,0,0.05);
            padding: 15px 25px;
            border-radius: 10px;
            font-weight: bold;
            color: #333;
            min-height: 50px;
            display: flex;
            align-items: center;
            justify-content: center;
        }}
        
        .voice-participants {{
            background: rgba(0,0,0,0.05);
            padding: 20px;
            border-radius: 10px;
            margin-top: 20px;
            width: 100%;
            max-width: 500px;
        }}
        
        .voice-participants h3 {{
            margin-bottom: 15px;
            color: #333;
        }}
        
        .participant-list {{
            display: flex;
            flex-wrap: wrap;
            gap: 10px;
            justify-content: center;
        }}
        
        .participant {{
            background: #667eea;
            color: white;
            padding: 8px 15px;
            border-radius: 20px;
            font-size: 14px;
            display: flex;
            align-items: center;
            gap: 5px;
        }}
        
        .participant.speaking {{
            animation: speakingGlow 1s infinite alternate;
        }}
        
        @keyframes speakingGlow {{
            from {{ box-shadow: 0 0 5px rgba(102, 126, 234, 0.5); }}
            to {{ box-shadow: 0 0 15px rgba(102, 126, 234, 0.8); }}
        }}
        
        .connection-status {{
            background: rgba(255, 193, 7, 0.1);
            border: 2px solid rgba(255, 193, 7, 0.3);
            border-radius: 10px;
            padding: 15px;
            margin-bottom: 20px;
            color: #333;
        }}
        
        .connection-status.connected {{
            background: rgba(76, 175, 80, 0.1);
            border-color: rgba(76, 175, 80, 0.3);
        }}
        
        @media (max-width: 600px) {{
            .input-container {{
                flex-direction: column;
                gap: 10px;
            }}
            
            .voice-controls {{
                flex-direction: column;
            }}
            
            .voice-btn {{
                width: 100%;
                max-width: 250px;
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
        <h1>🎤💬 Chatroom + Voice Room</h1>
        <div class="tabs">
            <button class="tab-btn active" onclick="switchTab('chat')">💬 Text Chat</button>
            <button class="tab-btn" onclick="switchTab('voice')">🎤 Voice Room</button>
        </div>
        <div class="online-count" id="onlineCount">🟢 Loading...</div>
    </div>
    
    <div class="main-container">
        <!-- Text Chat Tab -->
        <div id="chatTab" class="tab-content active">
            <div class="messages-container" id="messagesContainer">
                <div class="no-messages">Welcome to the chatroom! Send a message to get started 🚀</div>
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
        let currentUser = '{username}';
        let lastMessageId = 0;
        
        // Voice variables
        let socket = null;
        let localStream = null;
        let peerConnections = new Map();
        let isInVoiceRoom = false;
        let isMuted = false;
        let isTalking = false;
        let roomId = 'main-voice-room';
        
        // Connect to your Render signaling server
        const SIGNALING_SERVER = 'https://repo1-ejq1.onrender.com';
        
        // Authentication function
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
        
        // Tab switching
        function switchTab(tabName) {{
            document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
            event.target.classList.add('active');
            
            document.querySelectorAll('.tab-content').forEach(content => content.classList.remove('active'));
            document.getElementById(tabName + 'Tab').classList.add('active');
        }}
        
        // Initialize Socket.IO connection
        function initializeVoiceConnection() {{
            socket = io(SIGNALING_SERVER);
            
            socket.on('connect', () => {{
                console.log('Connected to voice server!');
                document.getElementById('connectionStatus').innerHTML = '🟢 Connected to voice server';
                document.getElementById('connectionStatus').classList.add('connected');
            }});
            
            socket.on('disconnect', () => {{
                console.log('Disconnected from voice server');
                document.getElementById('connectionStatus').innerHTML = '🔴 Disconnected from voice server';
                document.getElementById('connectionStatus').classList.remove('connected');
            }});
            
            socket.on('user-joined', (data) => {{
                console.log('User joined:', data.username);
                createPeerConnection(data.userId);
                updateVoiceNotification(`🎤 ${{data.username}} joined voice room`);
            }});
            
            socket.on('user-left', (data) => {{
                console.log('User left:', data.username);
                closePeerConnection(data.userId);
                updateVoiceNotification(`📞 ${{data.username}} left voice room`);
            }});
            
            socket.on('offer', async (data) => {{
                console.log('Received offer from:', data.from);
                await handleOffer(data.offer, data.from);
            }});
            
            socket.on('answer', async (data) => {{
                console.log('Received answer from:', data.from);
                await handleAnswer(data.answer, data.from);
            }});
            
            socket.on('ice-candidate', async (data) => {{
                console.log('Received ICE candidate from:', data.from);
                await handleIceCandidate(data.candidate, data.from);
            }});
            
            socket.on('room-stats', (data) => {{
                document.getElementById('participantCount').textContent = data.userCount;
                updateParticipantsList();
            }});
            
            socket.on('user-voice-activity', (data) => {{
                updateUserVoiceActivity(data.userId, data.isActive);
            }});
        }}
        
        // Chat functionality
        const messageInput = document.getElementById('messageInput');
        messageInput.addEventListener('input', function() {{
            this.style.height = 'auto';
            this.style.height = Math.min(this.scrollHeight, 100) + 'px';
        }});
        
        messageInput.addEventListener('keydown', function(e) {{
            if (e.key === 'Enter' && !e.shiftKey) {{
                e.preventDefault();
                sendMessage();
            }}
        }});
        
        function addEmoji(emoji) {{
            const input = document.getElementById('messageInput');
            input.value += emoji;
            input.focus();
        }}
        
        function sendMessage() {{
            const messageText = messageInput.value.trim();
            if (!messageText) return;
            
            const message = {{
                text: messageText,
                timestamp: new Date().toISOString()
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
                    messageInput.value = '';
                    messageInput.style.height = 'auto';
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
                    updateOnlineCount(data.messageCount || 0);
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
                if (message.text.includes('🎤') || message.text.includes('🗣️') || message.text.includes('📞')) messageClass += ' voice';
                
                messageDiv.className = messageClass;
                messageDiv.id = `message-${{message.id}}`;
                
                const timestamp = new Date(message.timestamp).toLocaleTimeString();
                
                messageDiv.innerHTML = `
                    <div class="message-header">
                        <span class="username">${{escapeHtml(message.username)}}</span>
                        <span class="timestamp">${{timestamp}}</span>
                    </div>
                    <div class="message-text">${{escapeHtml(message.text)}}</div>
                `;
                
                container.appendChild(messageDiv);
            }});
            
            container.scrollTop = container.scrollHeight;
        }}
        
        function updateOnlineCount(messageCount) {{
            const onlineCount = document.getElementById('onlineCount');
            onlineCount.textContent = `💬 ${{messageCount}} messages`;
        }}
        
        function escapeHtml(text) {{
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }}
        
        function updateVoiceNotification(text) {{
            const message = {{
                text: text,
                timestamp: new Date().toISOString()
            }};
            
            fetch('/api/chat/send', {{
                method: 'POST',
                headers: {{
                    'Content-Type': 'application/json',
                }},
                body: JSON.stringify(message)
            }});
        }}
        
        // Voice Room functionality (same as before)
        async function joinVoiceRoom() {{
            try {{
                localStream = await navigator.mediaDevices.getUserMedia({{
                    audio: {{
                        echoCancellation: true,
                        noiseSuppression: true,
                        sampleRate: 44100
                    }}
                }});
                
                localStream.getAudioTracks().forEach(track => {{
                    track.enabled = false;
                }});
                
                isInVoiceRoom = true;
                
                socket.emit('join-room', {{
                    roomId: roomId,
                    username: currentUser
                }});
                
                document.getElementById('joinVoiceBtn').style.display = 'none';
                document.getElementById('talkBtn').classList.remove('disabled');
                document.getElementById('muteBtn').style.display = 'inline-flex';
                document.getElementById('leaveVoiceBtn').style.display = 'inline-flex';
                document.getElementById('voiceStatus').innerHTML = '🎤 In voice room - Hold "Talk" to speak!';
                
                updateVoiceNotification(`🎤 ${{currentUser}} joined the voice room`);
                
            }} catch (error) {{
                console.error('Error accessing microphone:', error);
                document.getElementById('voiceStatus').innerHTML = '❌ Microphone access denied. Please allow microphone and try again.';
            }}
        }}
        
        function leaveVoiceRoom() {{
            if (localStream) {{
                localStream.getTracks().forEach(track => track.stop());
                localStream = null;
            }}
            
            peerConnections.forEach((pc, userId) => {{
                pc.close();
            }});
            peerConnections.clear();
            
            isInVoiceRoom = false;
            isTalking = false;
            isMuted = false;
            
            document.getElementById('joinVoiceBtn').style.display = 'inline-flex';
            document.getElementById('talkBtn').classList.add('disabled');
            document.getElementById('muteBtn').style.display = 'none';
            document.getElementById('leaveVoiceBtn').style.display = 'none';
            document.getElementById('voiceStatus').innerHTML = '🎤 Click "Join Voice Room" to start talking with others!';
            
            updateVoiceNotification(`📞 ${{currentUser}} left the voice room`);
            updateParticipantsList();
        }}
        
        async function createPeerConnection(userId) {{
            const peerConnection = new RTCPeerConnection({{
                iceServers: [
                    {{ urls: 'stun:stun.l.google.com:19302' }},
                    {{ urls: 'stun:global.stun.twilio.com:3478' }}
                ]
            }});
            
            if (localStream) {{
                localStream.getTracks().forEach(track => {{
                    peerConnection.addTrack(track, localStream);
                }});
            }}
            
            peerConnection.ontrack = (event) => {{
                const remoteStream = event.streams[0];
                playRemoteAudio(remoteStream, userId);
            }};
            
            peerConnection.onicecandidate = (event) => {{
                if (event.candidate) {{
                    socket.emit('ice-candidate', {{
                        target: userId,
                        candidate: event.candidate
                    }});
                }}
            }};
            
            peerConnections.set(userId, peerConnection);
            
            const offer = await peerConnection.createOffer();
            await peerConnection.setLocalDescription(offer);
            
            socket.emit('offer', {{
                target: userId,
                offer: offer
            }});
        }}
        
        async function handleOffer(offer, fromUserId) {{
            const peerConnection = new RTCPeerConnection({{
                iceServers: [
                    {{ urls: 'stun:stun.l.google.com:19302' }},
                    {{ urls: 'stun:global.stun.twilio.com:3478' }}
                ]
            }});
            
            if (localStream) {{
                localStream.getTracks().forEach(track => {{
                    peerConnection.addTrack(track, localStream);
                }});
            }}
            
            peerConnection.ontrack = (event) => {{
                const remoteStream = event.streams[0];
                playRemoteAudio(remoteStream, fromUserId);
            }};
            
            peerConnection.onicecandidate = (event) => {{
                if (event.candidate) {{
                    socket.emit('ice-candidate', {{
                        target: fromUserId,
                        candidate: event.candidate
                    }});
                }}
            }};
            
            peerConnections.set(fromUserId, peerConnection);
            
            await peerConnection.setRemoteDescription(offer);
            const answer = await peerConnection.createAnswer();
            await peerConnection.setLocalDescription(answer);
            
            socket.emit('answer', {{
                target: fromUserId,
                answer: answer
            }});
        }}
        
        async function handleAnswer(answer, fromUserId) {{
            const peerConnection = peerConnections.get(fromUserId);
            if (peerConnection) {{
                await peerConnection.setRemoteDescription(answer);
            }}
        }}
        
        async function handleIceCandidate(candidate, fromUserId) {{
            const peerConnection = peerConnections.get(fromUserId);
            if (peerConnection) {{
                await peerConnection.addIceCandidate(candidate);
            }}
        }}
        
        function closePeerConnection(userId) {{
            const peerConnection = peerConnections.get(userId);
            if (peerConnection) {{
                peerConnection.close();
                peerConnections.delete(userId);
            }}
            
            const audioElement = document.getElementById(`audio-${{userId}}`);
            if (audioElement) {{
                audioElement.remove();
            }}
        }}
        
        function playRemoteAudio(stream, userId) {{
            const audio = document.createElement('audio');
            audio.srcObject = stream;
            audio.autoplay = true;
            audio.id = `audio-${{userId}}`;
            audio.volume = 1.0;
            
            document.body.appendChild(audio);
            console.log(`Playing audio from user: ${{userId}}`);
        }}
        
        function startTalking() {{
            if (!isInVoiceRoom || isMuted || isTalking || !localStream) return;
            
            isTalking = true;
            
            localStream.getAudioTracks().forEach(track => {{
                track.enabled = true;
            }});
            
            document.getElementById('talkBtn').classList.add('recording');
            document.getElementById('voiceStatus').innerHTML = '🔴 Talking... Release button to stop';
            
            socket.emit('voice-activity', {{ isActive: true }});
        }}
        
        function stopTalking() {{
            if (!isTalking || !localStream) return;
            
            isTalking = false;
            
            localStream.getAudioTracks().forEach(track => {{
                track.enabled = false;
            }});
            
            document.getElementById('talkBtn').classList.remove('recording');
            document.getElementById('voiceStatus').innerHTML = '🎤 In voice room - Hold "Talk" to speak!';
            
            socket.emit('voice-activity', {{ isActive: false }});
        }}
        
        function toggleMute() {{
            isMuted = !isMuted;
            const muteBtn = document.getElementById('muteBtn');
            
            if (isMuted) {{
                muteBtn.innerHTML = '🔇 Unmute';
                muteBtn.style.background = '#f44336';
                document.getElementById('voiceStatus').innerHTML = '🔇 Microphone muted';
                
                if (localStream) {{
                    localStream.getAudioTracks().forEach(track => track.enabled = false);
                }}
            }} else {{
                muteBtn.innerHTML = '🔊 Mute';
                muteBtn.style.background = '#ff9800';
                document.getElementById('voiceStatus').innerHTML = '🎤 In voice room - Hold "Talk" to speak!';
                
                if (localStream && !isTalking) {{
                    localStream.getAudioTracks().forEach(track => track.enabled = false);
                }}
            }}
        }}
        
        function updateParticipantsList() {{
            const participantList = document.getElementById('participantList');
            
            if (!isInVoiceRoom) {{
                participantList.innerHTML = `
                    <div class="participant">
                        <span>💤</span>
                        <span>No one in voice yet</span>
                    </div>
                `;
                return;
            }}
            
            participantList.innerHTML = `
                <div class="participant" id="myParticipant">
                    <span>🎤</span>
                    <span>You (${{currentUser}})</span>
                </div>
            `;
            
            peerConnections.forEach((pc, userId) => {{
                const participant = document.createElement('div');
                participant.className = 'participant';
                participant.id = `participant-${{userId}}`;
                participant.innerHTML = `
                    <span>🔊</span>
                    <span>User ${{userId.substring(0, 8)}}...</span>
                `;
                participantList.appendChild(participant);
            }});
        }}
        
        function updateUserVoiceActivity(userId, isActive) {{
            const participant = document.getElementById(`participant-${{userId}}`);
            if (participant) {{
                if (isActive) {{
                    participant.classList.add('speaking');
                }} else {{
                    participant.classList.remove('speaking');
                }}
            }}
        }}
        
        document.getElementById('talkBtn').addEventListener('contextmenu', e => e.preventDefault());
        
        // Initialize everything when page loads
        document.addEventListener('DOMContentLoaded', function() {{
            initializeVoiceConnection();
            setInterval(loadMessages, 2000);
            loadMessages();
            
            console.log('🎉 Authenticated Chatroom + Voice Room loaded!');
            console.log('👤 Logged in as:', currentUser);
            console.log('💬 Text chat ready');
            console.log('🎤 Voice room connected');
        }});
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
                # Session expired, remove it
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
            
            # Validation
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
                # Check if username already exists
                if username.lower() in [u.lower() for u in users_db.keys()]:
                    self.send_json_response({"success": False, "error": "Username already taken"})
                    return
                
                # Create new user
                users_db[username] = {
                    "password_hash": data_persistence.hash_password(password),
                    "created": datetime.now(),
                    "last_seen": datetime.now()
                }
            
            # Trigger backup after registration
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
                # Check if user exists
                user_data = users_db.get(username)
                if not user_data:
                    self.send_json_response({"success": False, "error": "Invalid username or password"})
                    return
                
                # Verify password
                password_hash = data_persistence.hash_password(password)
                if password_hash != user_data["password_hash"]:
                    self.send_json_response({"success": False, "error": "Invalid username or password"})
                    return
                
                # Update last seen
                user_data["last_seen"] = datetime.now()
                
                # Create session
                session_id = data_persistence.generate_session_id()
                user_sessions[session_id] = {
                    "username": username,
                    "expires": datetime.now() + timedelta(hours=24)  # 24 hour session
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
        """Handle sending a new chat message (authenticated users only)"""
        # Check authentication
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
            
            text = message_data.get('text', '')[:500]  # Limit message length
            
            if not text.strip():
                self.send_json_response({"success": False, "error": "Empty message"})
                return
            
            # Add message to global storage
            with chatroom_lock:
                new_id = max([msg['id'] for msg in chatroom_messages], default=0) + 1
                
                message = {
                    'id': new_id,
                    'username': username,
                    'text': text.strip(),
                    'timestamp': datetime.now().isoformat(),
                    'ip': self.client_address[0]
                }
                chatroom_messages.append(message)
                
                # Keep only last 100 messages
                if len(chatroom_messages) > 100:
                    chatroom_messages.pop(0)
                    for i, msg in enumerate(chatroom_messages):
                        msg['id'] = i + 1
            
            self.send_json_response({"success": True, "message": "Message sent", "messageId": new_id})
            
        except json.JSONDecodeError:
            self.send_json_response({"success": False, "error": "Invalid JSON"})
        except Exception as e:
            self.send_json_response({"success": False, "error": str(e)})
    
    def handle_chat_messages(self, path):
        """Handle retrieving chat messages (authenticated users only)"""
        # Check authentication
        session_id = self.get_session_from_cookies()
        if not session_id or not self.is_valid_session(session_id):
            self.send_json_response({"error": "Not authenticated"})
            return
        
        # Parse query parameters
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
    
    def handle_status(self):
        """Handle server status"""
        with chatroom_lock, users_lock:
            message_count = len(chatroom_messages)
            user_count = len(users_db)
            active_sessions = len([s for s in user_sessions.values() if datetime.now() < s['expires']])
        
        data = {
            "status": "online",
            "server": "Authenticated Chatroom + Voice Room Server",
            "version": "6.0",
            "timestamp": time.time(),
            "total_messages": message_count,
            "total_users": user_count,
            "active_sessions": active_sessions,
            "signaling_server": "https://repo1-ejq1.onrender.com",
            "features": [
                "user_authentication", 
                "persistent_storage", 
                "github_gist_backup",
                "text_chat", 
                "voice_room", 
                "webrtc_voice", 
                "push_to_talk", 
                "render_signaling"
            ],
            "backup_status": {
                "github_gist_configured": bool(GITHUB_GIST_TOKEN and GITHUB_GIST_ID),
                "webhook_configured": bool(EXTERNAL_BACKUP_URL)
            },
            "uptime": "Running with authentication & persistent storage! 🔐💬🎤"
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
    """Background task to clean up expired sessions"""
    while True:
        time.sleep(3600)  # Run every hour
        current_time = datetime.now()
        with users_lock:
            expired_sessions = [
                session_id for session_id, session_data in user_sessions.items()
                if current_time > session_data['expires']
            ]
            for session_id in expired_sessions:
                del user_sessions[session_id]
        
        if expired_sessions:
            print(f"🧹 Cleaned up {len(expired_sessions)} expired sessions")

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
    
    try:
        with socketserver.TCPServer(("0.0.0.0", PORT), ChatroomHandler) as httpd:
            print("🚀" * 60)
            print(f"🔐💬🎤 AUTHENTICATED CHATROOM + VOICE ROOM SERVER STARTED!")
            print("🚀" * 60)
            print(f"🌐 Server URL: http://localhost:{PORT}")
            print(f"📡 Voice Signaling: https://repo1-ejq1.onrender.com")
            print(f"📂 Directory: {os.getcwd()}")
            print(f"🗄️ Loaded Users: {len(users_db)}")
            print(f"💬 Loaded Messages: {len(chatroom_messages)}")
            
            print("\n🔐 AUTHENTICATION FEATURES:")
            print("   👤 User registration with username + password")
            print("   🔑 Secure login system with sessions")
            print("   🕒 24-hour session expiration")
            print("   🚪 Logout functionality")
            print("   ⚡ Session validation on all requests")
            
            print("\n💾 PERSISTENCE FEATURES:")
            print("   📦 GitHub Gist backup (primary)")
            print("   🔄 Auto-backup every 5 minutes")
            print("   📤 Webhook backup (secondary)")
            print("   🔧 Data restoration on server restart")
            print("   🧹 Automatic session cleanup")
            
            print("\n🎯 RENDER.COM SETUP INSTRUCTIONS:")
            print("   1. Set environment variables in Render dashboard:")
            print("      GITHUB_GIST_TOKEN=your_github_token_here")
            print("      GITHUB_GIST_ID=your_gist_id_here")
            print("      BACKUP_WEBHOOK_URL=optional_webhook_url")
            print("   2. Create a GitHub Personal Access Token with 'gist' scope")
            print("   3. Create a new private gist on GitHub")
            print("   4. Copy the gist ID from the URL")
            print("   5. Your data will persist across restarts! 🎉")
            
            print("\n✨ FEATURES:")
            print("   🔐 Secure user authentication")
            print("   💬 Real-time text chatroom")
            print("   🎤 Voice room with WebRTC")
            print("   📱 Mobile-friendly interface")
            print("   😊 Emoji support")
            print("   🔄 Auto-refresh every 2 seconds")
            print("   💾 Persistent data storage")
            print("   🔇 Mute/unmute functionality")
            print("   📊 Voice activity indicators")
            print("   👥 User session management")
            
            print("\n🌐 API ENDPOINTS:")
            print("   🏠 GET / (Login/Register page)")
            print("   💬 GET /chat (Chatroom - requires auth)")
            print("   📝 POST /api/auth/register (Create account)")
            print("   🔑 POST /api/auth/login (Login)")
            print("   🚪 POST /api/auth/logout (Logout)")
            print("   ✅ GET /api/auth/check (Check auth)")
            print("   📤 POST /api/chat/send (Send message)")
            print("   📥 GET /api/chat/messages (Get messages)")
            print("   📊 GET /api/status (Server status)")
            
            backup_status = "✅ Configured" if GITHUB_GIST_TOKEN and GITHUB_GIST_ID else "❌ Not configured"
            webhook_status = "✅ Configured" if EXTERNAL_BACKUP_URL else "⚠️ Optional"
            
            print(f"\n💾 BACKUP STATUS:")
            print(f"   GitHub Gist: {backup_status}")
            print(f"   Webhook URL: {webhook_status}")
            
            if not GITHUB_GIST_TOKEN or not GITHUB_GIST_ID:
                print("\n⚠️  WARNING: No backup configured!")
                print("   Your data will be lost when Render restarts the service.")
                print("   Please set GITHUB_GIST_TOKEN and GITHUB_GIST_ID environment variables.")
            
            print("\n🛑 Press Ctrl+C to stop the server")
            print("=" * 60)
            
            httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n🛑 Server stopped by user")
        print("💾 Performing final backup...")
        data_persistence.backup_to_github_gist()
        print("👋 Thanks for using the authenticated chatroom!")
    except Exception as e:
        print(f"❌ Server error: {e}")

if __name__ == "__main__":
    main()
