from flask import Flask, render_template, request, jsonify, session
import sqlite3
import os
from datetime import datetime
from transformers import pipeline

app = Flask(__name__)
app.secret_key = "rahul123"
app.config['VERSION'] = '2.0'

# Initialize chatbot pipeline
chatbot = pipeline(
    "text-generation",
    model="microsoft/DialoGPT-medium",
    tokenizer="microsoft/DialoGPT-medium"
)

# Database path
DB_PATH = "database/restaurant.db"
os.makedirs("database", exist_ok=True)

# Initialize DB tables
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

# Save order to DB
def save_order(items, order_type):
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("INSERT INTO orders (items, order_type) VALUES (?, ?)", (items, order_type))
        conn.commit()
        conn.close()
    except Exception as e:
        print("DB Error (save_order):", e)

# Save reservation to DB
def save_reservation(msg):
    try:
        parts = msg.split(",")
        if len(parts) < 5:
            return
        name, contact, date, time, guests = [p.strip() for p in parts[:5]]
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            "INSERT INTO reservations (name, contact, date, time, guests) VALUES (?, ?, ?, ?, ?)",
            (name, contact, date, time, guests)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print("DB Error (save_reservation):", e)

# Save feedback
def save_feedback(msg):
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("INSERT INTO feedback (message) VALUES (?)", (msg,))
        conn.commit()
        conn.close()
    except Exception as e:
        print("DB Error (save_feedback):", e)

# Clear orders
def clear_orders():
    session["orders"] = []
    session["state"] = {}

# Clear reservations
def clear_reservations():
    session["state"] = {}

# Handle restaurant-specific intents
def handle_restaurant_intents(msg):
    msg_lower = msg.lower().strip()
    state = session.get("state", {})
    orders = session.get("orders", [])
    menu_items = ["pizza", "burger", "pasta", "salad", "coffee", "dessert"]
    mentioned_items = [item for item in menu_items if item in msg_lower]

    # Clear commands
    if msg_lower in ["clear orders", "reset orders"]:
        clear_orders()
        return "🗑️ Your orders have been cleared."
    if msg_lower in ["clear reservations", "reset reservations"]:
        clear_reservations()
        return "🗑️ Reservation process has been reset."

    # Feedback
    if msg_lower.startswith("feedback:"):
        feedback_msg = msg[len("feedback:"):].strip()
        save_feedback(feedback_msg)
        return "✅ Thank you for your feedback!"

    # Orders
    if mentioned_items or "order" in msg_lower or "add" in msg_lower:
        added_items = []
        for item in mentioned_items:
            if item not in orders:
                orders.append(item)
                added_items.append(item)
        session["orders"] = orders

        # If state is expecting upsell for previous item
        if state.get("expecting") == "upsell":
            upsell_item = state["item"]
            upsell_added = ""
            if any(word in msg_lower for word in ["fries", "drink", "coffee", "combo"]):
                if "fries" in msg_lower:
                    upsell_added = "with fries"
                elif "drink" in msg_lower:
                    upsell_added = "with a drink"
                else:
                    upsell_added = "combo"
                for i, o in enumerate(orders):
                    if upsell_item in o:
                        orders[i] = f"{upsell_item} {upsell_added}".strip()
                session["orders"] = orders
                session["state"] = {"expecting": "delivery_or_table"}
                return f"Noted! Your {upsell_item} {upsell_added} is added. Delivery or table?"

        # Normal new orders
        if added_items:
            last_added = ", ".join(added_items)
            session["state"] = {"expecting": "upsell", "item": last_added}
            return f"Great choice! Would you like fries or a drink with your {last_added}?"
        elif mentioned_items:
            return "You already added these items. Anything else?"
        else:
            return f"⚠️ Sorry, we don’t have that item. Our menu: {', '.join(menu_items).title()}."

    # Delivery or Table
    if state.get("expecting") == "delivery_or_table":
        session["state"] = {}
        if "delivery" in msg_lower:
            delivered_items = ', '.join(orders)
            save_order(delivered_items, "Delivery")
            session["orders"] = []
            return f"✅ Your order ({delivered_items}) will be delivered soon!"
        elif "table" in msg_lower or "reservation" in msg_lower:
            session["state"] = {"expecting": "reservation"}
            return "Sure! Please provide your name, contact, date, time, and number of guests (comma separated)."
        else:
            return "Please specify delivery or table reservation."

    # Reservation
    if state.get("expecting") == "reservation":
        session["state"] = {}
        session["orders"] = []
        save_reservation(msg)
        return f"✅ Reservation confirmed. Details: {msg}"

    # Menu
    if "menu" in msg_lower:
        return "Our menu: Pizza 🍕, Pasta 🍝, Burger 🍔, Salad 🥗, Coffee ☕, Dessert 🍰."

    # Past orders
    if msg_lower in ["show my past orders", "my orders", "orders"]:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT items, order_type, timestamp FROM orders ORDER BY id DESC LIMIT 5")
        rows = c.fetchall()
        conn.close()
        if not rows:
            return "You have no past orders."
        reply = "🛒 Your last 5 orders:\n"
        for r in rows:
            reply += f"- {r[0]} ({r[1]}) at {r[2]}\n"
        return reply

    # Past reservations
    if msg_lower in ["show my past reservations", "my reservations", "reservations"]:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT name, contact, date, time, guests FROM reservations ORDER BY id DESC LIMIT 5")
        rows = c.fetchall()
        conn.close()
        if not rows:
            return "You have no past reservations."
        reply = "🍽️ Your last 5 reservations:\n"
        for r in rows:
            reply += f"- {r[0]}, contact: {r[1]}, {r[4]} guests on {r[2]} at {r[3]}\n"
        return reply

    # Greetings
    greetings = ["hi", "hello", "hey", "welcome"]
    if any(greet in msg_lower for greet in greetings):
        return "👋 Hello! I’m your assistant. You can order food 🍕 or book a table 🪑. What would you like to do?"

    return None

# Compute simple chatbot accuracy
def compute_chatbot_accuracy():
    test_cases = {
        "I want pizza": "pizza",
        "Add a burger": "burger",
        "Show me the menu": "menu",
        "I want coffee": "coffee"
    }
    correct = 0
    for user_input, expected in test_cases.items():
        response = handle_restaurant_intents(user_input)
        if response and expected in response.lower():
            correct += 1
    accuracy = (correct / len(test_cases)) * 100
    return f"{accuracy:.2f}"

# Check DB connection
def check_db_connection():
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("SELECT 1")
        conn.close()
        return "Connected ✅"
    except:
        return "Error ❌"

# Get dashboard stats
def get_dashboard_stats():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM orders")
    total_orders = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM reservations")
    total_reservations = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM feedback")
    feedback_count = c.fetchone()[0]
    conn.close()
    return {        
        "model_accuracy": compute_chatbot_accuracy(),
        "data_storage": check_db_connection(),
        "total_orders": total_orders,
        "total_reservations": total_reservations,
        "feedback_count": feedback_count
    }

# ===== Routes =====
@app.route("/")
def home():
    if "orders" not in session:
        session["orders"] = []
    if "state" not in session:
        session["state"] = {}
    return render_template("index.html")

@app.route("/get", methods=["POST"])
def get_bot_response():
    msg = request.form.get("msg", "")
    response = handle_restaurant_intents(msg)
    if not response:
        # Use AI chatbot as fallback
        try:
            result = chatbot(msg, max_length=100, do_sample=True)
            response = result[0]['generated_text']
        except:
            response = "⚠️ Sorry, I couldn't understand. Please try again."
    return jsonify({"response": response})

@app.route("/dashboard")
def dashboard():
    stats = get_dashboard_stats()
    return jsonify(stats)

# ===== Run Server =====
if __name__ == "__main__":
    app.run(debug=True)

