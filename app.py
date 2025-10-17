from flask import Flask, render_template, request, jsonify, session
import sqlite3
import os
from datetime import datetime
from transformers import pipeline

app = Flask(__name__)
app.secret_key = "rahul123"
app.config['VERSION'] = '2.2'
chatbot = pipeline(
    "text-generation",
    model="microsoft/DialoGPT-medium",
    tokenizer="microsoft/DialoGPT-medium"
)
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
def save_order(items, order_type):
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("INSERT INTO orders (items, order_type) VALUES (?, ?)", (items, order_type))
        conn.commit()
        conn.close()
    except Exception as e:
        print("DB Error (save_order):", e)

def save_reservation_from_string(msg):
    """
    Expecting a comma-separated string: name, contact, date, time, guests
    """
    try:
        parts = msg.split(",")
        if len(parts) < 5:
            return False
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

def ensure_session():
    if "orders" not in session:
        session["orders"] = []
    if "state" not in session:
        session["state"] = {}
    if "last_food" not in session:
        session["last_food"] = None
def clear_orders_session():
    session["orders"] = []
    session["state"] = {}
    session["last_food"] = None

def reset_state_only():
    session["state"] = {}
    session["last_food"] = None
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
        feedback_msg = msg[len("feedback:"):].strip()
        save_feedback(feedback_msg)
        return "✅ Thank you for your feedback!"
    if "menu" in msg_lower:
        return "📜 Our menu: Pizza 🍕, Pasta 🍝, Burger 🍔, Salad 🥗, Coffee ☕, Dessert 🍰."
    if state.get("expecting") == "upsell":
        last_food = session.get("last_food")
        if any(word in msg_lower for word in ["fries", "drink", "coffee", "combo"]):
            upsell_added = ""
            if "fries" in msg_lower:
                upsell_added = "with fries"
            elif "drink" in msg_lower:
                upsell_added = "with a drink"
            elif "coffee" in msg_lower:
                upsell_added = "with coffee"
            elif "combo" in msg_lower:
                upsell_added = "combo"
            updated = False
            for i, o in enumerate(orders):
                if last_food and (o == last_food or o.startswith(last_food)):
                    orders[i] = f"{last_food} {upsell_added}".strip()
                    updated = True
                    break
            if not updated:
                if last_food:
                    orders.append(f"{last_food} {upsell_added}".strip())
                else:
                    for item in menu_items:
                        if item in msg_lower:
                            orders.append(f"{item} {upsell_added}".strip())
                            updated = True
                            break

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
    if "how are you" in msg_lower:
        return "I'm doing great, thanks! Hungry today?"
    if "your name" in msg_lower or "who are you" in msg_lower:
        return "I'm the Spice Villa assistant 🤖 — here to help with orders and reservations!"
    greetings = ["hi", "hello", "hey", "welcome"]
    if any(g in msg_lower for g in greetings):
        return "👋 Hello! Welcome to Spice Villa. You can order food 🍕 or book a table 🪑."
    return None
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
def check_db_connection():
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("SELECT 1")
        conn.close()
        return "Connected ✅"
    except:
        return "Error ❌"
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
@app.route("/")
def home():
    ensure_session()
    return render_template("index.html")

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

@app.route("/clear_orders", methods=["POST"])
def clear_orders_route():
    clear_orders_session()
    return jsonify({"response": "🗑️ All orders cleared."})

@app.route("/clear_reservations", methods=["POST"])
def clear_reservations_route():
    reset_state_only()
    return jsonify({"response": "🗑️ Reservation state reset."})

@app.route("/feedback", methods=["POST"])
def feedback_route():
    msg = request.form.get("message", "")
    if msg:
        save_feedback(msg)
        return jsonify({"response": "✅ Thanks for your feedback!"})
    return jsonify({"response": "⚠️ No feedback message received."})

@app.route("/dashboard")
def dashboard_route():
    stats = get_dashboard_stats()
    return jsonify(stats)
if __name__ == "__main__":
    app.run(debug=True)
