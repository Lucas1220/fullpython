import http.server
import socketserver
import os
import mimetypes
import urllib.parse
import json
import time
import threading
from datetime import datetime

PORT = 8080

# Global chatroom storage
chatroom_messages = []
chatroom_lock = threading.Lock()

class ChatroomHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        # Set up MIME types for different file extensions
        mimetypes.add_type('application/javascript', '.js')
        mimetypes.add_type('text/css', '.css')
        mimetypes.add_type('application/json', '.json')
        super().__init__(*args, **kwargs)
    
    def do_GET(self):
        # Parse the URL path
        parsed_path = urllib.parse.urlparse(self.path)
        path = parsed_path.path
        
        # Handle root path - serve chatroom
        if path == '/':
            self.serve_chatroom()
            return
        
        # Handle API endpoints
        if path.startswith('/api/'):
            self.handle_api(path)
            return
        
        # Try to serve static files
        if self.serve_static_file(path):
            return
        
        # If file not found, serve 404
        self.send_error(404, "File not found")
    
    def serve_chatroom(self):
        """Serve the public chatroom interface with voice room connected to Render server"""
        html_content = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Chatroom + Voice Room 🎤💬</title>
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
        
        .header {
            background: rgba(255, 255, 255, 0.1);
            backdrop-filter: blur(10px);
            padding: 20px;
            text-align: center;
            color: white;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
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
        
        /* Chat Room Styles */
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
        
        #usernameInput {
            padding: 12px;
            border: 2px solid #ddd;
            border-radius: 8px;
            width: 150px;
            font-size: 14px;
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
            .input-container {
                flex-direction: column;
                gap: 10px;
            }
            
            #usernameInput {
                width: 100%;
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
    <div class="header">
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
                <input type="text" id="usernameInput" placeholder="Your name" maxlength="20" value="Anonymous">
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
        let currentUser = 'Anonymous';
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
        
        document.getElementById('usernameInput').addEventListener('input', function() {
            currentUser = this.value.trim() || 'Anonymous';
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
        
        function sendMessage() {
            const messageText = messageInput.value.trim();
            if (!messageText) return;
            
            const message = {
                username: currentUser,
                text: messageText,
                timestamp: new Date().toISOString()
            };
            
            fetch('/api/chat/send', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify(message)
            })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    messageInput.value = '';
                    messageInput.style.height = 'auto';
                    if (data.messageId) {
                        lastMessageId = data.messageId;
                    }
                }
            })
            .catch(error => {
                console.error('Error sending message:', error);
            });
        }
        
        function loadMessages() {
            fetch(`/api/chat/messages?since=${lastMessageId}`)
                .then(response => response.json())
                .then(data => {
                    if (data.messages && data.messages.length > 0) {
                        displayMessages(data.messages);
                        lastMessageId = data.lastId;
                    }
                    updateOnlineCount(data.messageCount || 0);
                })
                .catch(error => {
                    console.error('Error loading messages:', error);
                });
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
                if (message.username === currentUser) messageClass += ' own';
                if (message.text.includes('🎤') || message.text.includes('🗣️') || message.text.includes('📞')) messageClass += ' voice';
                
                messageDiv.className = messageClass;
                messageDiv.id = `message-${message.id}`;
                
                const timestamp = new Date(message.timestamp).toLocaleTimeString();
                
                messageDiv.innerHTML = `
                    <div class="message-header">
                        <span class="username">${escapeHtml(message.username)}</span>
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
            const message = {
                username: 'Voice System',
                text: text,
                timestamp: new Date().toISOString()
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
            try {
                localStream = await navigator.mediaDevices.getUserMedia({
                    audio: {
                        echoCancellation: true,
                        noiseSuppression: true,
                        sampleRate: 44100
                    }
                });
                
                // Mute by default (push-to-talk)
                localStream.getAudioTracks().forEach(track => {
                    track.enabled = false;
                });
                
                isInVoiceRoom = true;
                
                // Join the voice room via socket
                socket.emit('join-room', {
                    roomId: roomId,
                    username: currentUser
                });
                
                // Update UI
                document.getElementById('joinVoiceBtn').style.display = 'none';
                document.getElementById('talkBtn').classList.remove('disabled');
                document.getElementById('muteBtn').style.display = 'inline-flex';
                document.getElementById('leaveVoiceBtn').style.display = 'inline-flex';
                document.getElementById('voiceStatus').innerHTML = '🎤 In voice room - Hold "Talk" to speak!';
                
                updateVoiceNotification(`🎤 ${currentUser} joined the voice room`);
                
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
            
            // Close all peer connections
            peerConnections.forEach((pc, userId) => {
                pc.close();
            });
            peerConnections.clear();
            
            isInVoiceRoom = false;
            isTalking = false;
            isMuted = false;
            
            // Update UI
            document.getElementById('joinVoiceBtn').style.display = 'inline-flex';
            document.getElementById('talkBtn').classList.add('disabled');
            document.getElementById('muteBtn').style.display = 'none';
            document.getElementById('leaveVoiceBtn').style.display = 'none';
            document.getElementById('voiceStatus').innerHTML = '🎤 Click "Join Voice Room" to start talking with others!';
            
            updateVoiceNotification(`📞 ${currentUser} left the voice room`);
            updateParticipantsList();
        }
        
        async function createPeerConnection(userId) {
            const peerConnection = new RTCPeerConnection({
                iceServers: [
                    { urls: 'stun:stun.l.google.com:19302' },
                    { urls: 'stun:global.stun.twilio.com:3478' }
                ]
            });
            
            // Add local stream
            if (localStream) {
                localStream.getTracks().forEach(track => {
                    peerConnection.addTrack(track, localStream);
                });
            }
            
            // Handle remote stream
            peerConnection.ontrack = (event) => {
                const remoteStream = event.streams[0];
                playRemoteAudio(remoteStream, userId);
            };
            
            // Handle ICE candidates
            peerConnection.onicecandidate = (event) => {
                if (event.candidate) {
                    socket.emit('ice-candidate', {
                        target: userId,
                        candidate: event.candidate
                    });
                }
            };
            
            peerConnections.set(userId, peerConnection);
            
            // Create offer for new user
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
            
            // Add local stream
            if (localStream) {
                localStream.getTracks().forEach(track => {
                    peerConnection.addTrack(track, localStream);
                });
            }
            
            // Handle remote stream
            peerConnection.ontrack = (event) => {
                const remoteStream = event.streams[0];
                playRemoteAudio(remoteStream, fromUserId);
            };
            
            // Handle ICE candidates
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
            
            // Remove audio element
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
            
            // Notify server about voice activity
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
            
            // Notify server about voice activity
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
                    <span>You (${currentUser})</span>
                </div>
            `;
            
            // Add connected users
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
        
        // Prevent context menu on talk button
        document.getElementById('talkBtn').addEventListener('contextmenu', e => e.preventDefault());
        
        // Initialize everything when page loads
        document.addEventListener('DOMContentLoaded', function() {
            // Initialize voice connection
            initializeVoiceConnection();
            
            // Auto-refresh messages every 2 seconds
            setInterval(loadMessages, 2000);
            
            // Load initial messages
            loadMessages();
            
            console.log('🎉 Chatroom + Voice Room loaded!');
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
        if path == '/api/chat/send':
            self.handle_chat_send()
        elif path.startswith('/api/chat/messages'):
            self.handle_chat_messages(path)
        elif path == '/api/status':
            self.handle_status()
        else:
            self.send_error(404, "API endpoint not found")
    
    def handle_chat_send(self):
        """Handle sending a new chat message"""
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length)
            message_data = json.loads(post_data.decode('utf-8'))
            
            # Validate message data
            username = message_data.get('username', 'Anonymous')[:20]  # Limit username length
            text = message_data.get('text', '')[:500]  # Limit message length
            
            if not text.strip():
                self.send_json_response({"success": False, "error": "Empty message"})
                return
            
            # Add message to global storage with proper ID generation
            with chatroom_lock:
                # Generate unique ID based on current max ID + 1
                new_id = max([msg['id'] for msg in chatroom_messages], default=0) + 1
                
                message = {
                    'id': new_id,
                    'username': username,
                    'text': text.strip(),
                    'timestamp': datetime.now().isoformat(),
                    'ip': self.client_address[0]  # For potential moderation
                }
                chatroom_messages.append(message)
                
                # Keep only last 100 messages to prevent memory issues
                if len(chatroom_messages) > 100:
                    chatroom_messages.pop(0)
                    # Reassign IDs after removing old messages to maintain sequence
                    for i, msg in enumerate(chatroom_messages):
                        msg['id'] = i + 1
            
            self.send_json_response({"success": True, "message": "Message sent", "messageId": new_id})
            
        except json.JSONDecodeError:
            self.send_json_response({"success": False, "error": "Invalid JSON"})
        except Exception as e:
            self.send_json_response({"success": False, "error": str(e)})
    
    def handle_chat_messages(self, path):
        """Handle retrieving chat messages"""
        # Parse query parameters
        query_params = urllib.parse.parse_qs(urllib.parse.urlparse(path).query)
        since_id = int(query_params.get('since', [0])[0])
        
        with chatroom_lock:
            # Get messages since the specified ID
            new_messages = [msg for msg in chatroom_messages if msg['id'] > since_id]
            
            response_data = {
                "messages": new_messages,
                "lastId": chatroom_messages[-1]['id'] if chatroom_messages else 0,
                "messageCount": len(chatroom_messages)
            }
        
        self.send_json_response(response_data)
    
    def handle_status(self):
        """Handle server status"""
        with chatroom_lock:
            message_count = len(chatroom_messages)
        
        data = {
            "status": "online",
            "server": "Chatroom + Voice Room Server",
            "version": "5.0",
            "timestamp": time.time(),
            "total_messages": message_count,
            "signaling_server": "https://repo1-ejq1.onrender.com",
            "features": ["text_chat", "voice_room", "webrtc_voice", "push_to_talk", "render_signaling"],
            "uptime": "Running with Render.com signaling! 💬🎤"
        }
        
        self.send_json_response(data)
    
    def send_json_response(self, data):
        """Helper method to send JSON responses"""
        response = json.dumps(data, indent=2)
        self.send_response(200)
        self.send_header("Content-type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(response.encode('utf-8'))
    
    def serve_static_file(self, path):
        """Try to serve static files from current directory"""
        # Remove leading slash and prevent directory traversal
        file_path = path.lstrip('/')
        if '..' in file_path:
            return False
        
        if os.path.exists(file_path) and os.path.isfile(file_path):
            # Get MIME type
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
            # Default POST handler
            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length)
            
            response_data = {
                "message": "POST request received",
                "content_length": content_length,
                "data_preview": post_data.decode('utf-8', errors='ignore')[:200]
            }
            
            self.send_json_response(response_data)

def main():
    try:
        with socketserver.TCPServer(("0.0.0.0", PORT), ChatroomHandler) as httpd:
            print("🚀" * 50)
            print(f"💬🎤 CHATROOM + VOICE ROOM SERVER STARTED!")
            print("🚀" * 50)
            print(f"🌐 Local URL: http://localhost:{PORT}")
            print(f"📡 Signaling Server: https://repo1-ejq1.onrender.com")
            print(f"📂 Directory: {os.getcwd()}")
            print("\n✨ FEATURES:")
            print("   💬 Real-time text chatroom")
            print("   🎤 Voice room with WebRTC")
            print("   📡 Connected to your Render signaling server")
            print("   👥 Multiple users can chat and talk together")
            print("   📱 Mobile-friendly tabbed interface")
            print("   🎨 Beautiful UI with animations")
            print("   😊 Emoji support")
            print("   🔄 Auto-refresh every 2 seconds")
            print("   📝 Message history (last 100 messages)")
            print("   🔇 Mute/unmute functionality")
            print("   📊 Voice activity indicators")
            print("\n🎤 VOICE FEATURES:")
            print("   🗣️ Push-to-talk (hold button to speak)")
            print("   📡 Uses your Render.com signaling server")
            print("   🔗 WebRTC peer-to-peer voice connections")
            print("   👥 Real-time participant management")
            print("   🔊 Mute/unmute controls")
            print("   📞 Join/leave voice room")
            print("   🎯 Browser-based (no downloads needed)")
            print("\n🎯 API ENDPOINTS:")
            print(f"   📤 POST /api/chat/send (Send message)")
            print(f"   📥 GET /api/chat/messages (Get messages)")
            print(f"   📊 GET /api/status (Server status)")
            print("\n💡 USAGE:")
            print("   1. Run this Python script locally")
            print("   2. Use tunneling to make it public:")
            print("      npx localtunnel --port 8080 --subdomain myvoicechat")
            print("   3. Share the tunnel URL with friends")
            print("   4. Switch between Text Chat and Voice Room tabs")
            print("   5. Click 'Join Voice Room' and allow microphone")
            print("   6. Hold 'Talk' button to speak with others!")
            print("\n⚠️  VOICE ROOM NOTES:")
            print("   • Voice signaling goes through your Render server")
            print("   • Audio is peer-to-peer (no audio through server)")
            print("   • Works best in Chrome/Edge browsers")
            print("   • Allow microphone permissions when prompted")
            print("   • Voice notifications appear in text chat")
            print("\n🛑 Press Ctrl+C to stop the server")
            print("=" * 50)
            
            httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n🛑 Chatroom + Voice Room server stopped by user")
        print("👋 Thanks for using the chatroom with Render voice signaling!")
    except Exception as e:
        print(f"❌ Server error: {e}")

if __name__ == "__main__":
    main()