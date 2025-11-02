from flask import Flask, render_template, request, jsonify, session, redirect, url_for
import sqlite3, os
from datetime import datetime, timedelta
from transformers import pipeline
from werkzeug.security import generate_password_hash, check_password_hash
from flask_wtf import FlaskForm, CSRFProtect
from wtforms import StringField, PasswordField, SubmitField
from wtforms.validators import DataRequired

# ===== Flask app setup =====
app = Flask(__name__)
app.secret_key = "rahul123"
app.config['VERSION'] = '3.0'
app.permanent_session_lifetime = timedelta(minutes=15)
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SECURE'] = False

csrf = CSRFProtect(app)

# ===== Admin credentials =====
ADMIN_USER = "admin"
ADMIN_PASS_HASH = generate_password_hash("admin123", method="pbkdf2:sha256")

# ===== Chatbot setup =====
chatbot = pipeline(
    "text-generation",
    model="microsoft/DialoGPT-medium",
    tokenizer="microsoft/DialoGPT-medium"
)

# ===== Database setup =====
DB_PATH = "database/restaurant.db"
os.makedirs("database", exist_ok=True)

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        items TEXT,
        order_type TEXT,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS reservations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        contact TEXT,
        date TEXT,
        time TEXT,
        guests INTEGER,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS feedback (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        message TEXT,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")
    conn.commit()
    conn.close()

init_db()

# ===== Flask-WTF Form =====
class AdminLoginForm(FlaskForm):
    username = StringField("Username", validators=[DataRequired()])
    password = PasswordField("Password", validators=[DataRequired()])
    submit = SubmitField("Login")

# ===== Session helpers =====
def ensure_session():
    if "orders" not in session: session["orders"] = []
    if "state" not in session: session["state"] = {}
    if "last_food" not in session: session["last_food"] = None

def clear_orders_session():
    session["orders"] = []
    session["state"] = {}
    session["last_food"] = None

def reset_state_only():
    session["state"] = {}
    session["last_food"] = None

# ===== Database save functions =====
def save_order(items, order_type):
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("INSERT INTO orders (items, order_type) VALUES (?, ?)", (items, order_type))
        conn.commit()
        conn.close()
    except Exception as e: print("DB Error (save_order):", e)

def save_reservation_from_string(msg):
    try:
        parts = msg.split(",")
        if len(parts) < 5: return False
        name, contact, date, time, guests = [p.strip() for p in parts[:5]]
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            "INSERT INTO reservations (name, contact, date, time, guests) VALUES (?, ?, ?, ?, ?)",
            (name, contact, date, time, guests)
        )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print("DB Error (save_reservation):", e)
        return False

def save_feedback(msg):
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("INSERT INTO feedback (message) VALUES (?)", (msg,))
        conn.commit()
        conn.close()
    except Exception as e:
        print("DB Error (save_feedback):", e)

# ===== Restaurant chatbot logic =====
def handle_restaurant_intents(msg):
    ensure_session()
    msg_lower = (msg or "").lower().strip()
    state = session.get("state", {})
    orders = session.get("orders", [])
    menu_items = ["pizza", "burger", "pasta", "salad", "coffee", "dessert"]
    mentioned_items = [item for item in menu_items if item in msg_lower]

    if msg_lower in ["clear orders", "reset orders"]:
        clear_orders_session()
        return "🗑️ Your orders have been cleared."
    if msg_lower in ["clear reservations", "reset reservations"]:
        reset_state_only()
        return "🗑️ Reservation process has been reset."
    if msg_lower.startswith("feedback:"):
        save_feedback(msg[len("feedback:"):].strip())
        return "✅ Thank you for your feedback!"
    if "menu" in msg_lower:
        return "📜 Our menu: Pizza 🍕, Pasta 🍝, Burger 🍔, Salad 🥗, Coffee ☕, Dessert 🍰."

    if state.get("expecting") == "upsell":
        last_food = session.get("last_food")
        if any(word in msg_lower for word in ["fries", "drink", "coffee", "combo"]):
            upsell_added = ""
            if "fries" in msg_lower: upsell_added = "with fries"
            elif "drink" in msg_lower: upsell_added = "with a drink"
            elif "coffee" in msg_lower: upsell_added = "with coffee"
            elif "combo" in msg_lower: upsell_added = "combo"
            updated = False
            for i, o in enumerate(orders):
                if last_food and (o == last_food or o.startswith(last_food)):
                    orders[i] = f"{last_food} {upsell_added}".strip()
                    updated = True
                    break
            if not updated and last_food:
                orders.append(f"{last_food} {upsell_added}".strip())
            session["orders"] = orders
            session["state"] = {"expecting": "delivery_or_table"}
            session["last_food"] = None
            return f"Noted! Your {last_food if last_food else 'item'} {upsell_added} is added. Delivery or table?"
        return "Please choose between fries, drink, coffee, or combo for your meal."

    if mentioned_items or any(k in msg_lower for k in ["order", "add", "i want", "i'd like"]):
        added_items = []
        for item in mentioned_items:
            if item not in orders:
                orders.append(item)
                added_items.append(item)
        session["orders"] = orders
        if added_items:
            last_added = added_items[-1]
            session["last_food"] = last_added
            session["state"] = {"expecting": "upsell"}
            return f"Great choice! Would you like fries or a drink with your {last_added}?"
        else:
            if mentioned_items:
                return "You already added these items. Anything else?"
            return f"⚠️ Sorry, we don’t have that item. Our menu: {', '.join(menu_items).title()}."

    if state.get("expecting") == "delivery_or_table":
        session["state"] = {}
        if "delivery" in msg_lower:
            delivered_items = ', '.join(orders) if orders else "No items"
            save_order(delivered_items, "Delivery")
            session["orders"] = []
            session["last_food"] = None
            return f"✅ Your order ({delivered_items}) will be delivered soon!"
        elif "table" in msg_lower or "reservation" in msg_lower:
            session["state"] = {"expecting": "reservation"}
            return "Sure! Please provide your name, contact, date, time, and number of guests (comma separated)."
        else:
            return "Please specify delivery or table reservation."

    if state.get("expecting") == "reservation":
        success = save_reservation_from_string(msg)
        session["state"] = {}
        session["orders"] = []
        session["last_food"] = None
        if success:
            return f"✅ Reservation confirmed. Details: {msg}"
        else:
            return "⚠️ Reservation format not recognized. Please provide: name, contact, date, time, guests (comma separated)."

    if msg_lower in ["my orders", "orders"]:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT items, order_type, timestamp FROM orders ORDER BY id DESC LIMIT 5")
        rows = c.fetchall()
        conn.close()
        if not rows: return "You have no past orders."
        reply = "🛒 Your last 5 orders:\n" + "\n".join(f"- {r[0]} ({r[1]}) at {r[2]}" for r in rows)
        return reply

    if msg_lower in ["my reservations", "reservations"]:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT name, contact, date, time, guests FROM reservations ORDER BY id DESC LIMIT 5")
        rows = c.fetchall()
        conn.close()
        if not rows: return "You have no past reservations."
        reply = "🍽️ Your last 5 reservations:\n" + "\n".join(f"- {r[0]}, {r[4]} guests on {r[2]} at {r[3]}" for r in rows)
        return reply

    if "how are you" in msg_lower: return "I'm doing great, thanks! Hungry today?"
    if "your name" in msg_lower or "who are you" in msg_lower: return "I'm the Spice Villa assistant 🤖 — here to help with orders and reservations!"
    if any(g in msg_lower for g in ["hi", "hello", "hey", "welcome"]): return "👋 Hello! Welcome to Spice Villa. You can order food 🍕 or book a table 🪑."
    return None

# ===== Routes =====
@app.route("/admin-login", methods=["GET", "POST"])
def admin_login():
    form = AdminLoginForm()
    if form.validate_on_submit():
        username = form.username.data
        password = form.password.data
        if username == ADMIN_USER and check_password_hash(ADMIN_PASS_HASH, password):
            session["admin_logged_in"] = True
            session.permanent = True
            return redirect(url_for("admin_dashboard"))
        return render_template("admin_login.html", form=form, error="Invalid credentials")
    return render_template("admin_login.html", form=form)

@app.route("/admin")
def admin_dashboard():
    if not session.get("admin_logged_in"):
        return redirect(url_for("admin_login"))
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT items, order_type, timestamp FROM orders ORDER BY id DESC LIMIT 10")
    orders = c.fetchall()
    c.execute("SELECT name, contact, date, time, guests FROM reservations ORDER BY id DESC LIMIT 10")
    reservations = c.fetchall()
    c.execute("SELECT message, timestamp FROM feedback ORDER BY id DESC LIMIT 10")
    feedbacks = c.fetchall()
    conn.close()
    return render_template("admin.html", orders=orders, reservations=reservations, feedback=feedbacks)

@app.route("/dashboard")
def dashboard_stats():
    if not session.get("admin_logged_in"): return jsonify({"error": "Unauthorized"}), 401
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM orders"); total_orders = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM reservations"); total_reservations = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM feedback"); total_feedback = c.fetchone()[0]
    conn.close()
    return jsonify({
        "total_orders": total_orders,
        "total_reservations": total_reservations,
        "total_feedback": total_feedback,
        "model_accuracy": 95,
        "data_storage": "SQLite DB"
    })

@app.route("/logout")
def logout():
    session.pop("admin_logged_in", None)
    return redirect(url_for("admin_login"))

@app.route("/")
def home():
    ensure_session()
    is_admin = session.get("admin_logged_in", False)
    return render_template("index.html", is_admin=is_admin)

@csrf.exempt
@app.route("/get", methods=["POST"])
def get_bot_response():
    ensure_session()
    msg = request.form.get("msg", "")
    response = handle_restaurant_intents(msg)
    if not response:
        try:
            prompt = f"The user and a polite restaurant assistant chat.\nUser: {msg}\nAssistant:"
            result = chatbot(prompt, max_length=80, do_sample=True, top_p=0.9, temperature=0.7)
            generated = result[0].get("generated_text", "") if isinstance(result, list) else str(result)
            if isinstance(generated, str) and generated.startswith(prompt):
                response = generated[len(prompt):].strip()
            else:
                response = generated.strip()
            if not response or len(response) < 2:
                response = "🙂 Sorry, I didn’t quite catch that. Could you rephrase?"
        except Exception as e:
            print("DialoGPT error:", e)
            response = "⚠️ Sorry, I couldn't understand. Please try again."
    return jsonify({"response": response})

# ===== Run app =====
if __name__ == "__main__":
    app.run(debug=True)
