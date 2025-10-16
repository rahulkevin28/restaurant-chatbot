import sqlite3

DB_PATH = "database/restaurant.db"

def show_last_5_orders():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, items, order_type, timestamp FROM orders ORDER BY id DESC LIMIT 5")
    rows = c.fetchall()
    if rows:
        print("Last 5 Orders:")
        for r in rows:
            print(r)
    else:
        print("No orders found.")
    conn.close()

def show_last_5_reservations():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, name, contact, date, time, guests, timestamp FROM reservations ORDER BY id DESC LIMIT 5")
    rows = c.fetchall()
    if rows:
        print("\nLast 5 Reservations:")
        for r in rows:
            print(r)
    else:
        print("No reservations found.")
    conn.close()

if __name__ == "__main__":
    show_last_5_orders()
    show_last_5_reservations()
