# Spice Villa – Restaurant Chatbot & Table Reservation System
## Project Overview
Spice Villa Chatbot is a simple, interactive web application built using Flask, HTML, and CSS. It allows customers to chat with a virtual restaurant assistant to:
- Order food items
- Receive upsell suggestions
- Reserve tables for specific times and group sizes
This project demonstrates conversational logic, UI design, and backend handling using a lightweight Flask setup.

## Features
- User-friendly web-based chatbot interface
- Keyword-based chatbot logic (no external ML model required)
- Real-time order and table reservation flow
- Smart responses for menu items, add-ons, and booking confirmation
- Fully deployable on Render or any Flask-compatible platform

## Project Structure
restaurant-chatbot/
│
├── app.py                        # Flask backend with chatbot logic
├── requirements.txt              # Python dependencies
├── runtime.txt                   # Python version for Render deployment
│
├── templates/
│   └── index.html                # Chat UI
│
├── static/
│   ├── css/
│   │   └── style.css             # Chat styling
│   └── images/                   # (Optional) logo or icons
│
└── README.md                     # Project documentation

## Technologies Used
- Frontend: HTML, CSS
- Backend: Flask (Python)
- Deployment: Render
- Version Control: Git & GitHub

## Local Setup Instructions
### 1. Clone the Repository
```bash
git clone https://github.com/<your-username>/restaurant-chatbot.git
cd restaurant-chatbot
2. Install Dependencies
bash
Copy code
pip install -r requirements.txt
3. Run the Flask App
bash
Copy code
python app.py
4. Access the Application
Open your browser and go to:

cpp
Copy code
http://127.0.0.1:5000