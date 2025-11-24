// updated client JS — adds client-side EN/DE UI toggle and translations

const chatBox = document.getElementById("chat-box");
const userInputField = document.getElementById("user-input");
const sendBtn = document.getElementById("send-btn");

// ---------- TRANSLATIONS ----------
const TRANSLATIONS = {
    en: {
        welcome: `👋 Hello! Welcome to Spice Villa.\nYou can order food, ask for recommendations, or reserve a table.\nTry: “I’d like to order pizza” or “Book a table for 2 at 8 PM”.`,
        typing: "Typing...",
        invalid_input: "⚠️ Sorry, I didn’t understand that. Try asking about the menu, ordering, or reserving a table.",
        server_error: "⚠️ Unable to connect to the server. Try again later!",
        confirm_clear_orders: "Are you sure you want to clear all orders?",
        confirm_clear_reservations: "Are you sure you want to clear all reservations?",
        menu_title: "🍽️ Menu",
        dashboard_label: "📊 Dashboard",
        clear_orders_label: "🗑️ Clear Orders",
        clear_reservations_label: "🗑️ Clear Reservations",
        menu_items: ["pizza", "burger", "pasta", "salad", "coffee", "dessert"],
        placeholder: "Type your message..."
    },
    de: {
        welcome: `👋 Hallo! Willkommen bei Spice Villa.\nDu kannst Essen bestellen, Empfehlungen anfragen oder einen Tisch reservieren.\nVersuche: „Ich möchte Pizza bestellen“ oder „Reserviere einen Tisch für 2 um 20 Uhr“.`,
        typing: "Schreibt...",
        invalid_input: "⚠️ Entschuldigung, das habe ich nicht verstanden. Frag nach der Speisekarte, bestelle Essen oder reserviere einen Tisch.",
        server_error: "⚠️ Verbindung zum Server fehlgeschlagen. Versuche es später erneut!",
        confirm_clear_orders: "Bist du sicher, dass du alle Bestellungen löschen möchtest?",
        confirm_clear_reservations: "Bist du sicher, dass du alle Reservierungen löschen möchtest?",
        menu_title: "🍽️ Speisekarte",
        dashboard_label: "📊 Dashboard",
        clear_orders_label: "🗑️ Bestellungen löschen",
        clear_reservations_label: "🗑️ Reservierungen löschen",
        menu_items: ["pizza", "burger", "pasta", "salat", "kaffee", "dessert"],
        placeholder: "Schreibe deine Nachricht..."
    }
};

let LANG = localStorage.getItem("spicevilla_lang") || "en";

// ---------- Utilities ----------
function escapeHTML(text) {
    const div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
}

function appendMessage(sender, message) {
    // message may contain HTML from server — sanitize
    const msgDiv = document.createElement("div");
    msgDiv.className = sender === "user" ? "user-msg bubble" : "bot-msg bubble";
    // allow minimal line breaks: replace newline with <br>, but escape content first
    msgDiv.innerHTML = escapeHTML(message).replace(/\n/g, "<br>");
    chatBox.appendChild(msgDiv);
    chatBox.scrollTop = chatBox.scrollHeight;
}

function showTypingIndicator() {
    const typingDiv = document.createElement("div");
    typingDiv.className = "bot-msg bubble typing";
    typingDiv.innerHTML = `<span>${escapeHTML(TRANSLATIONS[LANG].typing)}</span>`;
    chatBox.appendChild(typingDiv);
    chatBox.scrollTop = chatBox.scrollHeight;
    return typingDiv;
}

// Basic input validation
function isValidInput(msg) {
    const invalidWords = ["yo", "wtf", "huh", "lol", "what"];
    const trimmed = msg.trim().toLowerCase();
    return trimmed && !invalidWords.includes(trimmed);
}

// ---------- sendMessage ----------
function sendMessage(msg = null) {
    const message = msg || userInputField.value.trim();
    if (!message) return;

    if (!msg && !isValidInput(message)) {
        appendMessage("bot", TRANSLATIONS[LANG].invalid_input);
        userInputField.value = "";
        return;
    }

    appendMessage("user", message);
    if (!msg) userInputField.value = "";

    const typingIndicator = showTypingIndicator();

    fetch("/get", {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: "msg=" + encodeURIComponent(message)
    })
    .then(res => res.json())
    .then(data => {
        if (typingIndicator && typingIndicator.parentNode) chatBox.removeChild(typingIndicator);
        // server responses are trusted but still sanitized by appendMessage
        appendMessage("bot", data.response || "");
        // If order/reservation confirmed (server returns localized text), trigger dashboard update
        if ((data.response || "").includes("✅ Your order") || (data.response || "").includes("✅ Reservation confirmed") ||
            (data.response || "").includes("✅ Deine Bestellung") || (data.response || "").includes("✅ Reservierung bestätigt")) {
            updateDashboard();
        }
    })
    .catch(() => {
        if (typingIndicator && typingIndicator.parentNode) chatBox.removeChild(typingIndicator);
        appendMessage("bot", TRANSLATIONS[LANG].server_error);
    });
}

// ---------- Event listeners ----------
sendBtn.addEventListener("click", () => sendMessage());
userInputField.addEventListener("keypress", e => { if (e.key === "Enter") sendMessage(); });

// ---------- Menu panel creation (translated) ----------
function createMenuPanel(items) {
    if (document.querySelector(".menu-panel")) return;

    const panel = document.createElement("div");
    panel.className = "menu-panel";

    const title = document.createElement("h3");
    title.innerText = TRANSLATIONS[LANG].menu_title;
    panel.appendChild(title);

    const itemsContainer = document.createElement("div");
    itemsContainer.className = "menu-items";
    items.forEach(item => {
        const btn = document.createElement("button");
        btn.className = "menu-item";
        // show label capitalized and (if German) map known words (we use passed items array)
        btn.innerText = item.charAt(0).toUpperCase() + item.slice(1);
        btn.addEventListener("click", () => sendMessage(item));
        itemsContainer.appendChild(btn);
    });
    panel.appendChild(itemsContainer);

    // actions: note — you asked previously to remove menu actions from chat in some places.
    // keep them here (you can remove if desired).
    const actions = [
        { text: TRANSLATIONS[LANG].dashboard_label, handler: toggleDashboard, style: "#333" },
        { text: TRANSLATIONS[LANG].clear_orders_label, handler: clearOrders },
        { text: TRANSLATIONS[LANG].clear_reservations_label, handler: clearReservations }
    ];

    actions.forEach(a => {
        const btn = document.createElement("button");
        btn.className = "menu-item";
        btn.innerText = a.text;
        if (a.style) btn.style.background = a.style;
        btn.addEventListener("click", a.handler);
        panel.appendChild(btn);
    });

    // Put panel at top of chat
    chatBox.prepend(panel);
}

// ---------- Dashboard handling (unchanged but uses server-provided keys) ----------
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
                <p><b>🧠 Model Accuracy:</b> ${data.model_accuracy ?? "N/A"}</p>
                <p><b>💾 Database Connection:</b> ${data.data_storage ?? "N/A"}</p>
                <p><b>📈 Orders Stored:</b> ${data.total_orders ?? 0}</p>
                <p><b>🍽️ Reservations Stored:</b> ${data.total_reservations ?? 0}</p>
                <p><b>💬 Feedback Count:</b> ${data.total_feedback ?? 0}</p>
                <p><b>🕒 Last Update:</b> ${data.last_update || now}</p>
            `;
            dashboardPanel.style.display = "block";
            chatBox.scrollTop = chatBox.scrollHeight;
        })
        .catch(() => {
            dashboardPanel.innerHTML = `<h3>📊 System Dashboard</h3><p>⚠️ Error ❌ Unable to fetch data.</p>`;
            dashboardPanel.style.display = "block";
        });
}

function toggleDashboard() {
    if (dashboardPanel.style.display === "block") {
        dashboardPanel.style.display = "none";
    } else {
        updateDashboard();
    }
}

// ---------- Clear functions ----------
function clearOrders() {
    if (!confirm(TRANSLATIONS[LANG].confirm_clear_orders)) return;
    fetch("/clear_orders", { method: "POST" })
        .then(res => res.json())
        .then(data => appendMessage("bot", data.response))
        .catch(() => appendMessage("bot", TRANSLATIONS[LANG].server_error));
}

function clearReservations() {
    if (!confirm(TRANSLATIONS[LANG].confirm_clear_reservations)) return;
    fetch("/clear_reservations", { method: "POST" })
        .then(res => res.json())
        .then(data => appendMessage("bot", data.response))
        .catch(() => appendMessage("bot", TRANSLATIONS[LANG].server_error));
}

// ---------- Language toggle UI ----------
function createLanguageToggle() {
    // add small toggle to top-right of chat container if not present
    const chatContainer = document.querySelector(".chat-container") || document.body;
    if (document.getElementById("lang-toggle")) return;

    const wrapper = document.createElement("div");
    wrapper.id = "lang-toggle";
    wrapper.style.position = "absolute";
    wrapper.style.top = "12px";
    wrapper.style.right = "18px";
    wrapper.style.zIndex = "50";
    wrapper.style.display = "flex";
    wrapper.style.gap = "6px";

    const enBtn = document.createElement("button");
    enBtn.innerText = "EN";
    enBtn.dataset.lang = "en";
    enBtn.style.padding = "6px 8px";
    enBtn.style.borderRadius = "6px";
    enBtn.style.cursor = "pointer";

    const deBtn = document.createElement("button");
    deBtn.innerText = "DE";
    deBtn.dataset.lang = "de";
    deBtn.style.padding = "6px 8px";
    deBtn.style.borderRadius = "6px";
    deBtn.style.cursor = "pointer";

    function updateToggleStyles() {
        enBtn.style.background = LANG === "en" ? "#25d366" : "#eee";
        enBtn.style.color = LANG === "en" ? "#fff" : "#333";
        deBtn.style.background = LANG === "de" ? "#25d366" : "#eee";
        deBtn.style.color = LANG === "de" ? "#fff" : "#333";
    }

    enBtn.addEventListener("click", () => {
        LANG = "en";
        localStorage.setItem("spicevilla_lang", LANG);
        updateUIForLang();
        updateToggleStyles();
    });
    deBtn.addEventListener("click", () => {
        LANG = "de";
        localStorage.setItem("spicevilla_lang", LANG);
        updateUIForLang();
        updateToggleStyles();
    });

    wrapper.appendChild(enBtn);
    wrapper.appendChild(deBtn);
    (document.querySelector(".chat-container") || document.body).appendChild(wrapper);
    updateToggleStyles();
}

// ---------- Update UI text for selected language ----------
function updateUIForLang() {
    // placeholder
    if (userInputField) userInputField.placeholder = TRANSLATIONS[LANG].placeholder;

    // remove existing menu panel so we create a translated one
    const existing = document.querySelector(".menu-panel");
    if (existing) existing.remove();

    // create menu with translated items
    createMenuPanel(TRANSLATIONS[LANG].menu_items);

    // update welcome message (append only if chat is mostly empty)
    const firstBot = chatBox.querySelector(".bot-msg.bubble");
    if (!firstBot) {
        appendMessage("bot", TRANSLATIONS[LANG].welcome);
    } else {
        // update first bot bubble content only if it matches the default old welcome (rough heuristic)
        const text = firstBot.textContent || "";
        if (text.includes("Spice Villa") || text.includes("Spice Villa")) {
            // replace content
            firstBot.innerHTML = escapeHTML(TRANSLATIONS[LANG].welcome).replace(/\n/g, "<br>");
        }
    }
}

// ---------- Init on load ----------
window.onload = () => {
    createLanguageToggle();
    updateUIForLang();
    // if no bot messages yet, show one (use appendMessage which escapes)
    const existingBot = chatBox.querySelector(".bot-msg.bubble");
    if (!existingBot) {
        appendMessage("bot", TRANSLATIONS[LANG].welcome);
    }
};
