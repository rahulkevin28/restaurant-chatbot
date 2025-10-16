// ===== DOM Elements =====
const chatBox = document.getElementById("chat-box");
const userInputField = document.getElementById("user-input");
const sendBtn = document.getElementById("send-btn");

// ===== Utility Functions =====
function escapeHTML(text) {
    const div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
}

function appendMessage(sender, message) {
    const msgDiv = document.createElement("div");
    msgDiv.className = sender === "user" ? "user-msg bubble" : "bot-msg bubble";
    msgDiv.innerHTML = `<span>${message}</span>`;
    chatBox.appendChild(msgDiv);
    chatBox.scrollTop = chatBox.scrollHeight;
}

// Show typing animation
function showTypingIndicator() {
    const typingDiv = document.createElement("div");
    typingDiv.className = "bot-msg bubble typing";
    typingDiv.innerHTML = `<span>Typing...</span>`;
    chatBox.appendChild(typingDiv);
    chatBox.scrollTop = chatBox.scrollHeight;
    return typingDiv;
}

// ===== Input Validation =====
function isValidInput(msg) {
    const invalidWords = ["yo", "what", "wtf", "huh", "lol"];
    const trimmed = msg.trim().toLowerCase();
    if (!trimmed) return false;
    return !invalidWords.includes(trimmed);
}

// ===== Message Sending =====
function sendMessage(msg = null) {
    const message = msg || userInputField.value.trim();
    if (!message) return;

    if (!msg && !isValidInput(message)) {
        appendMessage("bot", "⚠️ Sorry, I didn’t understand that. Try asking about the menu, ordering, or reserving a table.");
        userInputField.value = "";
        return;
    }

    appendMessage("user", escapeHTML(message));
    if (!msg) userInputField.value = "";

    const typingIndicator = showTypingIndicator();

    fetch("/get", {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: "msg=" + encodeURIComponent(message)
    })
    .then(res => res.json())
    .then(data => {
        chatBox.removeChild(typingIndicator);
        appendMessage("bot", escapeHTML(data.response));
        if (data.response.includes("✅ Your order") || data.response.includes("✅ Reservation confirmed")) {
            updateDashboard();
        }
    })
    .catch(() => {
        chatBox.removeChild(typingIndicator);
        appendMessage("bot", "⚠️ Unable to connect to the server. Try again later!");
    });
}

// ===== Event Listeners =====
sendBtn.addEventListener("click", () => sendMessage());
userInputField.addEventListener("keypress", e => {
    if (e.key === "Enter") sendMessage();
});

// ===== Menu Panel =====
function createMenuPanel(items) {
    if (document.querySelector(".menu-panel")) return;

    const panel = document.createElement("div");
    panel.className = "menu-panel";

    const title = document.createElement("h3");
    title.innerText = "🍽️ Menu";
    panel.appendChild(title);

    const itemsContainer = document.createElement("div");
    itemsContainer.className = "menu-items";

    items.forEach(item => {
        const btn = document.createElement("button");
        btn.className = "menu-item";
        btn.innerText = item.charAt(0).toUpperCase() + item.slice(1);
        btn.addEventListener("click", () => sendMessage(item));
        itemsContainer.appendChild(btn);
    });

    panel.appendChild(itemsContainer);

    // Dashboard Button
    const dashboardBtn = document.createElement("button");
    dashboardBtn.className = "menu-item";
    dashboardBtn.style.background = "#333";
    dashboardBtn.innerText = "📊 Dashboard";
    dashboardBtn.addEventListener("click", toggleDashboard);
    panel.appendChild(dashboardBtn);

    // Clear Orders & Reservations
    const clearOrdersBtn = document.createElement("button");
    clearOrdersBtn.className = "menu-item";
    clearOrdersBtn.innerText = "🗑️ Clear Orders";
    clearOrdersBtn.addEventListener("click", clearOrders);
    panel.appendChild(clearOrdersBtn);

    const clearReservationsBtn = document.createElement("button");
    clearReservationsBtn.className = "menu-item";
    clearReservationsBtn.innerText = "🗑️ Clear Reservations";
    clearReservationsBtn.addEventListener("click", clearReservations);
    panel.appendChild(clearReservationsBtn);

    chatBox.prepend(panel);
}

createMenuPanel(["pizza", "burger", "pasta", "salad", "coffee", "dessert"]);

// ===== Dashboard Section =====
let dashboardPanel = document.querySelector(".dashboard-panel");
if (!dashboardPanel) {
    dashboardPanel = document.createElement("div");
    dashboardPanel.className = "dashboard-panel";
    dashboardPanel.style.display = "none";
    chatBox.appendChild(dashboardPanel);
}

function updateDashboard() {
    fetch("/dashboard")
        .then(res => res.json())
        .then(data => {
            const now = new Date().toLocaleString();
            dashboardPanel.innerHTML = `
                <h3>📊 System Dashboard</h3>
                <p><b>🧠 Model Accuracy:</b> ${data.model_accuracy || "N/A"}</p>
                <p><b>💾 Database Connection:</b> ${data.data_storage || "N/A"}</p>
                <p><b>📈 Orders Stored:</b> ${data.total_orders ?? 0}</p>
                <p><b>🍽️ Reservations Stored:</b> ${data.total_reservations ?? 0}</p>
                <p><b>💬 Feedback Count:</b> ${data.feedback_count ?? 0}</p>
                <p><b>🕒 Last Update:</b> ${data.last_update || now}</p>
            `;
            dashboardPanel.style.display = "block";
            chatBox.scrollTop = chatBox.scrollHeight;
        })
        .catch(() => {
            dashboardPanel.innerHTML = `
                <h3>📊 System Dashboard</h3>
                <p>⚠️ Error ❌ Unable to fetch data.</p>
            `;
            dashboardPanel.style.display = "block";
            chatBox.scrollTop = chatBox.scrollHeight;
        });
}

function toggleDashboard() {
    if (dashboardPanel.style.display === "block") {
        dashboardPanel.style.display = "none";
        return;
    }
    updateDashboard();
}

// ===== Clear Functions =====
function clearOrders() {
    if (!confirm("Are you sure you want to clear all orders?")) return;
    fetch("/clear_orders", { method: "POST" })
        .then(res => res.json())
        .then(data => appendMessage("bot", data.response))
        .catch(() => appendMessage("bot", "⚠️ Error clearing orders."));
}

function clearReservations() {
    if (!confirm("Are you sure you want to clear all reservations?")) return;
    fetch("/clear_reservations", { method: "POST" })
        .then(res => res.json())
        .then(data => appendMessage("bot", data.response))
        .catch(() => appendMessage("bot", "⚠️ Error clearing reservations."));
}

// ===== Welcome Message =====
window.onload = () => {
    appendMessage("bot", "👋 Hello! Welcome to Spice Villa.<br> You can order food, ask for recommendations, or reserve a table.<br>Try: “I’d like to order pizza” or “Book a table for 2 at 8 PM”.");
};
