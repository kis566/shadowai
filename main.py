import json
import os
import re
import time
import asyncio
import base64
import threading
from io import BytesIO
from PIL import Image
from flask import Flask, request, jsonify, render_template, send_file, redirect, url_for
from flask_cors import CORS
from google import genai
from google.genai import types
import edge_tts
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from google.oauth2 import id_token
from google.auth.transport import requests as grequests

# Firebase Imports
import firebase_admin
from firebase_admin import credentials, firestore, auth

# Kivy Imports for Android UI
try:
    from kivy.app import App
    from kivy.uix.modalview import ModalView
    from kivy.clock import Clock
    from android.runnable import run_on_ui_thread
    from jnius import autoclass
    WebView = autoclass('android.webkit.WebView')
    WebViewClient = autoclass('android.webkit.WebViewClient')
    Activity = autoclass('org.kivy.android.PythonActivity').mActivity
    IS_ANDROID = True
except ImportError:
    # Desktop ya Server (Render) par run karne ke liye fallback
    class App: pass
    class ModalView: pass
    class Clock:
        @staticmethod
        def schedule_once(f, t): pass
    def run_on_ui_thread(f): return f
    WebView = None
    WebViewClient = None
    Activity = None
    IS_ANDROID = False

app = Flask(__name__)
CORS(app)

app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///shadow_ai.db'
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'default-key-for-dev-only')

db = SQLAlchemy(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# Web client ID
GOOGLE_CLIENT_ID = os.environ.get("google_client_id", "540700988873-dhgr67kpcpub99uvg9a68vts9e43intj.apps.googleusercontent.com")

# ==========================================
# FIREBASE INITIALIZATION (Via Environment Variable)
# ==========================================
firestore_db = None
try:
    fb_cred_env = os.environ.get("firebase_credentials")
    if fb_cred_env:
        cred_dict = json.loads(fb_cred_env)
        cred = credentials.Certificate(cred_dict)
        firebase_admin.initialize_app(cred)
        firestore_db = firestore.client()
        print("Firebase initialized successfully from environment variable!")
    elif os.path.exists("serviceAccountKey.json"):
        cred = credentials.Certificate("serviceAccountKey.json")
        firebase_admin.initialize_app(cred)
        firestore_db = firestore.client()
        print("Firebase initialized successfully from local JSON file!")
    else:
        print("Warning: Firebase credentials not found in env or local file.")
except Exception as e:
    print(f"Firebase initialization error: {e}")
    firestore_db = None

# ==========================================
# DATABASE MODELS
# ==========================================
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(150), nullable=True)

class ChatHistory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    user_query = db.Column(db.Text, nullable=False)
    ai_response = db.Column(db.Text, nullable=False)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# ==========================================
# GEMINI API INITIALIZATION (Env Variable / Config)
# ==========================================
CONFIG_FILE = "api_keys.json"
gemini_key = os.environ.get("gemini_api_key")

if not gemini_key and os.path.exists(CONFIG_FILE):
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)
            gemini_key = cfg.get("gemini_api_key")
    except Exception as e:
        print(f"Config load error: {e}")

client = genai.Client(api_key=gemini_key) if gemini_key else None

# ==========================================
# MAIN CHAT UI (Moved to templates/index.html)
# ==========================================

# ==========================================
# LOGIN & SIGNUP PAGE (Moved to templates/login.html)
# ==========================================

# ==========================================
# GOOGLE LOGIN HELPER
# ==========================================
def login_google_user(idinfo):
    """Google token verify hone ke baad user dhundo ya banao, phir login karo.
    Email se match karte hain (unique + secure), taaki kisi ka Google naam
    kisi existing password-user ke naam se match hoke account takeover na kar sake."""
    email = idinfo['email']

    user = User.query.filter_by(username=email).first()
    if not user:
        dummy_pw = generate_password_hash(f"google_oauth_{email}", method='pbkdf2:sha256')
        user = User(username=email, password=dummy_pw)
        db.session.add(user)
        db.session.commit()
    login_user(user, remember=True)

# ==========================================
# FLASK ROUTES
# ==========================================
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username').strip()
        password = request.form.get('password').strip()
        
        user = User.query.filter_by(username=username).first()
        if user:
            if user.password and check_password_hash(user.password, password):
                login_user(user, remember=True)
                return redirect(url_for('home'))
            else:
                return render_template('login.html', client_id=GOOGLE_CLIENT_ID, error="Incorrect password!")
        else:
            hashed_pw = generate_password_hash(password, method='pbkdf2:sha256')
            new_user = User(username=username, password=hashed_pw)
            db.session.add(new_user)
            db.session.commit()
            login_user(new_user, remember=True)
            return redirect(url_for('home'))
            
    return render_template('login.html', client_id=GOOGLE_CLIENT_ID)

# Website ke liye (browser me Google button)
@app.route('/google-login', methods=['POST'])
def google_login():
    data = request.get_json() or {}
    token = data.get('token')
    try:
        idinfo = id_token.verify_oauth2_token(token, grequests.Request(), GOOGLE_CLIENT_ID)
        login_google_user(idinfo)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400

# Android app ke liye (native account picker se aaya token)
@app.route('/auth/google-app', methods=['POST'])
def google_app_login():
    token = request.form.get('id_token')
    try:
        # Firebase token verification support
        try:
            decoded_token = auth.verify_id_token(token)
            email = decoded_token.get('email')
            idinfo = {'email': email}
        except:
            # Fallback to Google ID Token
            idinfo = id_token.verify_oauth2_token(token, grequests.Request(), GOOGLE_CLIENT_ID)

        login_google_user(idinfo)
        return redirect(url_for('home'))
    except Exception as e:
        return render_template(
            'login.html', client_id=GOOGLE_CLIENT_ID,
            error=f"Login failed: {e}"
        ), 401

@app.route('/auth/firebase', methods=['POST'])
def firebase_auth():
    data = request.get_json() or {}
    token = data.get('token')
    try:
        decoded_token = auth.verify_id_token(token)
        email = decoded_token.get('email')

        user = User.query.filter_by(username=email).first()
        if not user:
            user = User(username=email)
            db.session.add(user)
            db.session.commit()

        login_user(user, remember=True)
        return jsonify({"success": True, "username": email})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 401

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/')
@login_required
def home():
    return render_template('index.html')

@app.route('/logo')
def serve_logo():
    try:
        return send_file('shadow_logo.png', mimetype='image/png')
    except Exception as e:
        return f"Logo not found: {e}", 404

@app.route('/tts', methods=['POST'])
@login_required
def handle_tts():
    data = request.get_json()
    text = data.get("text", "")
    voice = data.get("voice", "hi-IN-MadhurNeural")
    if not text:
        return jsonify({"error": "Empty text"}), 400
    
    clean_text = re.sub(r'[*#_`~]', '', text)
    emoji_pattern = re.compile(
        "["
        "\U0001f600-\U0001f64f"
        "\U0001f300-\U0001f5ff"
        "\U0001f680-\U0001f6ff"
        "\U0001f1e0-\U0001f1ff"
        "\U00002702-\U000027b0"
        "\U000024c2-\U0001f251"
        "]+", flags=re.UNICODE
    )
    clean_text = emoji_pattern.sub(r'', clean_text)
    clean_text = re.sub(r'\s+', ' ', clean_text).strip()
    
    async def get_audio_stream():
        communicate = edge_tts.Communicate(clean_text, voice)
        audio_data = b""
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_data += chunk["data"]
        return audio_data

    try:
        audio_bytes = asyncio.run(get_audio_stream())
        return send_file(BytesIO(audio_bytes), mimetype="audio/mpeg")
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/history', methods=['GET'])
@login_required
def get_history():
    # Pehle Firestore se history uthane ki koshish karein
    if firestore_db:
        try:
            chats_ref = firestore_db.collection('chats').where('user_id', '==', str(current_user.id)).order_by('timestamp', direction=firestore.Query.ASCENDING)
            docs = chats_ref.stream()
            history = [{"query": doc.to_dict().get('user_query'), "response": doc.to_dict().get('ai_response')} for doc in docs]
            if history:
                return jsonify({"history": history})
        except Exception as e:
            print(f"Firestore history error: {e}")

    # Fallback to SQLite agar Firestore fail ho ya empty ho
    chats = ChatHistory.query.filter_by(user_id=current_user.id).all()
    history = [{"query": c.user_query, "response": c.ai_response} for c in chats]
    return jsonify({"history": history})

@app.route('/ask', methods=['POST'])
@login_required
def ask_ai():
    request_start = time.perf_counter()
    data = request.get_json() or {}
    user_query = data.get("query", "").strip()
    image_data = data.get("image", None)

    if not user_query and not image_data:
        return jsonify({"error": "Query or Image is empty"}), 400

    if not client:
        return jsonify({"answer": "Gemini API key configure nahi hai."}), 500

    # History loading (Firestore preferred)
    past_chats_data = []
    if firestore_db:
        try:
            docs = firestore_db.collection('chats').where('user_id', '==', str(current_user.id)).order_by('timestamp', direction=firestore.Query.DESCENDING).limit(5).stream()
            past_chats_data = [{"user_query": d.to_dict().get('user_query'), "ai_response": d.to_dict().get('ai_response')} for d in docs]
            past_chats_data.reverse()
        except:
            pass

    if not past_chats_data:
        past_chats = ChatHistory.query.filter_by(user_id=current_user.id).order_by(ChatHistory.id.desc()).limit(5).all()
        past_chats.reverse()
        past_chats_data = [{"user_query": c.user_query, "ai_response": c.ai_response} for c in past_chats]
    
    contents = []
    for chat in past_chats_data:
        contents.append(f"User ({current_user.username}): {chat['user_query']}")
        contents.append(f"Shadow: {chat['ai_response']}")

    if image_data:
        try:
            header, encoded = image_data.split(",", 1)
            binary_data = base64.b64decode(encoded)
            img = Image.open(BytesIO(binary_data))
            contents.append(img)
        except Exception as e:
            print(f"Image processing error: {e}")

    if user_query:
        contents.append(user_query)
    elif image_data and not user_query:
        contents.append("Is image mein kya hai? Iske baare mein professional aur mazedaar style mein batao.")

    system_prompt = (
        f"Aapka naam 'Shadow AI' hai. Aapko Deepak ne banaya hai, lekin abhi aapka current user '{current_user.username}' hai.\n\n"
        f"USER IDENTITY: Is user ka naam '{current_user.username}' hai. Inse hamesha inke naam ya 'Boss' keh kar baat karein. Kisi doosre user ya purane user (jaise Deepak) ki baat mat karein jab tak yeh khud na puchein.\n"
        "STYLE & TONE: Aap ek smart, professional aur thode mazedaar (witty) AI assistant ho. Apne har jawab me relevant aur acche **Emojis (jaise 😎, 🚀, 💡, 🔥)** ka use karein taaki conversation boring na lage aur engaging bane. "
        "Jawab seedha, accurate, aur professional hona chahiye lekin sath hi conversational aur fun tone me bhi hona chahiye."
    )

    try:
        response = client.models.generate_content(
            model="gemini-3.5-flash-lite",
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=0.4,
                max_output_tokens=4096
            )
        )
        answer_text = response.text
    except Exception as e:
        return jsonify({"answer": f"Boss, Gemini API me error aa raha hai: {e}"}), 500

    stored_query = user_query if user_query else "[Image Attachment]"
    if stored_query and answer_text:
        # Save to SQLite
        new_chat = ChatHistory(user_id=current_user.id, user_query=stored_query, ai_response=answer_text)
        db.session.add(new_chat)
        db.session.commit()

        # Save to Firestore
        if firestore_db:
            try:
                firestore_db.collection('chats').add({
                    'user_id': str(current_user.id),
                    'user_query': stored_query,
                    'ai_response': answer_text,
                    'timestamp': firestore.SERVER_TIMESTAMP
                })
            except Exception as e:
                print(f"Firestore save error: {e}")

    return jsonify({"answer": answer_text, "response_time_seconds": round(time.perf_counter() - request_start, 2)})

# Render pe gunicorn use hota hai, isliye tables yahan banate hain (import par)
with app.app_context():
    db.create_all()

# ==========================================
# CONFIGURATION
# ==========================================
# Render URL updated
REMOTE_URL = "https://shadowai-w5r4.onrender.com"

# Android Kivy Wrapper
class ShadowAIApp(App):
    def build(self):
        self.webview = None
        # Mobile screen ko full black background dena
        from kivy.core.window import Window
        Window.clearcolor = (0, 0, 0, 1)
        Clock.schedule_once(self.create_webview, 0)
        return ModalView()

    @run_on_ui_thread
    def create_webview(self, *args):
        webview = WebView(Activity)
        webview.getSettings().setJavaScriptEnabled(True)
        webview.getSettings().setDomStorageEnabled(True)
        webview.getSettings().setAllowFileAccess(True)
        webview.getSettings().setAllowContentAccess(True)
        webview.getSettings().setUserAgentString("Mozilla/5.0 (Linux; Android 10; Mobile) ShadowAI/1.0")
        webview.setWebViewClient(WebViewClient())
        Activity.setContentView(webview)

        # Load the Remote Render URL
        webview.loadUrl(REMOTE_URL)
        self.webview = webview

def run_flask():
    # Android par local server tabhi chalayein jab remote URL na ho
    if REMOTE_URL == "http://127.0.0.1:5000":
        app.run(host='127.0.0.1', port=5000, debug=False)

if __name__ == '__main__':
    if IS_ANDROID:
        # Android App logic: Flask ko background mein aur Kivy ko foreground mein chalayein
        if REMOTE_URL == "http://127.0.0.1:5000":
            threading.Thread(target=run_flask, daemon=True).start()
        ShadowAIApp().run()
    else:
        # Render/Server ya Desktop mode
        port = int(os.environ.get("PORT", 5000))
        app.run(host='0.0.0.0', port=port)
