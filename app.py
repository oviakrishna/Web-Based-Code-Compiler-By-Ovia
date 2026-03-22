from flask import Flask, request, jsonify, send_from_directory, session, redirect
from flask_cors import CORS
from pymongo import MongoClient
import requests, bcrypt, os, certifi
from datetime import datetime, timezone
from flask import make_response

from dotenv import load_dotenv
load_dotenv()

app = Flask(__name__, static_folder=".")
app.secret_key = "oviasree123"
IS_PROD = os.environ.get("RENDER")  # Render sets this automatically
app.config["SESSION_COOKIE_SECURE"] = bool(IS_PROD)
app.config["SESSION_COOKIE_SAMESITE"] = "None" if IS_PROD else "Lax"
app.config["SESSION_COOKIE_HTTPONLY"] = True

# ── FIX 2: Allow credentials from your deployed frontend origin ───────────────
# Replace "https://your-app.onrender.com" with your actual Render URL
FRONTEND_ORIGIN = os.environ.get("FRONTEND_ORIGIN", "http://localhost:5000")
CORS(app, supports_credentials=True, origins=[FRONTEND_ORIGIN])

client = MongoClient(
    "mongodb+srv://ovia_krishna:oviasree123@cluster0.vvqdgc4.mongodb.net/?retryWrites=true&w=majority",
    tlsCAFile=certifi.where()
)
db       = client["codeforge"]
users    = db["users"]
run_logs = db["run_logs"]

@app.route("/")
def home():
    return redirect("/compiler") if "user" in session else send_from_directory(".", "login.html")

@app.route("/me")
def me():
    return jsonify({"user": session.get("user", "NOT SET")})

@app.route("/health")
def health():
    return "OK", 200

@app.route("/compiler")
def compiler():
    return redirect("/") if "user" not in session else send_from_directory(".", "index.html")

@app.route("/signup")
def signup_page():
    return send_from_directory(".", "signup.html")

@app.route("/signup", methods=["POST"])
def signup():
    d = request.get_json()
    if users.find_one({"username": d["username"]}):
        return jsonify({"status": "exists"})
    users.insert_one({
        "username": d["username"],
        "password": bcrypt.hashpw(d["password"].encode(), bcrypt.gensalt())
    })
    session["user"] = d["username"]
    session.permanent = True          # ← make session persist properly
    return jsonify({"status": "success", "redirect": "/compiler"})

@app.route("/login", methods=["POST"])
def login():
    d = request.get_json()
    u = users.find_one({"username": d["username"]})
    if not u or not bcrypt.checkpw(d["password"].encode(), u["password"]):
        return jsonify({"status": "error"})
    session["user"] = d["username"]
    session.permanent = True          # ← make session persist properly
    return jsonify({"status": "success", "redirect": "/compiler"})

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

# ─── Token routes ─────────────────────────────────────────────────────────────

@app.route("/glot-token")
def glot_token():
    return jsonify({"token": "ea3e8183-7350-4d16-9d96-577b15bbab47"})

@app.route("/save-token", methods=["POST"])
def save_token():
    if "user" not in session:
        return jsonify({"status": "error"}), 401
    d = request.get_json()
    users.update_one(
        {"username": session["user"]},
        {"$set": {"glot_token": d["token"]}}
    )
    return jsonify({"status": "success"})

@app.route("/get-token")
def get_token():
    if "user" not in session:
        return jsonify({"token": ""})
    u = users.find_one({"username": session["user"]})
    return jsonify({"token": u.get("glot_token", "") if u else ""})

# ─────────────────────────────────────────────────────────────────────────────

@app.route("/run", methods=["POST"])
def run_code():
    d = request.get_json()

    # ── FIX 2: Get username from session OR from request body as fallback ─────
    username = session.get("user")
    if not username:
        # If session is missing (cookie issue), client can send it explicitly
        username = d.get("username", "anonymous")

    run_logs.insert_one({
        "username": username,
        "language": d["language"],
        "timestamp": datetime.now(timezone.utc)
    })

    try:
        res = requests.post(
            f"https://glot.io/api/run/{d['language']}/latest",
            headers={
                "Authorization": f"Token {d['token']}",
                "Content-Type": "application/json"
            },
            json={
                "files": [{"name": d["filename"], "content": d["code"]}],
                "stdin": d.get("stdin", "")
            },
            timeout=10
        )

        if res.status_code != 200:
            return make_response(jsonify({"error": "Glot API failed", "stderr": res.text}), 500)

        data = res.json()

        # ── FIX 1: Normalize Glot response — null fields cause "No output" ───
        return jsonify({
            "stdout": data.get("stdout") or "",
            "stderr": data.get("stderr") or "",
            "error":  data.get("error")  or "",
        })

    except requests.exceptions.Timeout:
        return make_response(jsonify({"error": "Execution timed out", "stdout": "", "stderr": ""}), 500)

    except requests.exceptions.RequestException as e:
        return make_response(jsonify({"error": str(e), "stdout": "", "stderr": ""}), 500)


# ─── Report page ──────────────────────────────────────────────────────────────
@app.route("/report")
def report_page():
    return send_from_directory(".", "report.html")


# ─── Report API ───────────────────────────────────────────────────────────────
@app.route("/api/report")
def report():
    try:
        pipeline = [
            {
                "$group": {
                    "_id": {
                        "date":     {"$dateToString": {"format": "%Y-%m-%d", "date": "$timestamp"}},
                        "language": "$language"
                    },
                    "count": {"$sum": 1}
                }
            },
            {"$sort": {"_id.date": 1}}
        ]
        rows = list(run_logs.aggregate(pipeline))
        result = [{"date": r["_id"]["date"], "language": r["_id"]["language"], "count": r["count"]} for r in rows]
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─── User Report API ──────────────────────────────────────────────────────────
@app.route("/api/user-report")
def user_report():
    try:
        pipeline = [
            {
                "$group": {
                    "_id": {
                        "username": "$username",
                        "language": "$language",
                        "date":     {"$dateToString": {"format": "%Y-%m-%d", "date": "$timestamp"}}
                    },
                    "count": {"$sum": 1}
                }
            },
            {"$sort": {"_id.username": 1, "_id.date": 1}}
        ]
        rows = list(run_logs.aggregate(pipeline))
        result = [{"username": r["_id"]["username"], "language": r["_id"]["language"],
                   "date": r["_id"]["date"], "count": r["count"]} for r in rows]
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run()