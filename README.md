# 💊 Health Companion — Local AI Health Assistant

A personal AI health companion app for tracking vitals, medications,
health records, cycle, diet, and doctor appointments — powered by Claude AI.

---

## 🚀 Quick Start (Windows)

1. **Double-click `run.bat`**
2. Paste your Anthropic API key when prompted (first time only)
3. Browser opens automatically at `http://127.0.0.1:5000`

---

## 📋 Requirements

- Python 3.8 or higher → https://www.python.org/downloads/
  ✅ Check **"Add Python to PATH"** during install
- Anthropic API key → https://console.anthropic.com/

---

## ✨ Features

| Tab | What it does |
|-----|-------------|
| 💬 AI Chat | Ask health questions — AI sees your vitals, meds & records |
| ❤️ Vitals | Log O2, heart rate, BP, temperature with trend chart |
| 💊 Medications | Track doses, mark taken, get alerts for missed meds |
| 📋 Records | Upload lab reports / prescriptions — AI analyzes them |
| 🌸 Cycle | Period tracker with predictions & symptom logging |
| 🥗 Diet | Log meals, calories, water intake with daily summary |
| 📅 Appointments | Schedule doctor visits with reminders |

---

## 📁 Files

```
health_companion/
├── run.bat            ← Double-click to start
├── app.py             ← Flask backend
├── requirements.txt   ← Python packages
├── health_data.db     ← Your data (created on first run)
├── .env               ← API key (created on first run)
├── uploads/           ← Uploaded health records
└── templates/
    └── index.html     ← Frontend UI
```

---

## 🔒 Privacy & Data

- **All data is stored locally** on your computer in `health_data.db`
- Nothing is sent to any server except AI chat messages to Anthropic's API
- Uploaded health records are analyzed by Claude and stored locally
- To reset all data: delete `health_data.db`

---

## 🛠 Manual Setup (if .bat doesn't work)

```bash
# 1. Install packages
pip install flask anthropic werkzeug

# 2. Set API key
set ANTHROPIC_API_KEY=your_key_here    # Windows
export ANTHROPIC_API_KEY=your_key_here  # Mac/Linux

# 3. Run the app
python app.py
```

Then open http://127.0.0.1:5000

---

## 🚀 Moving to Production

When ready for production, consider:
- Use PostgreSQL instead of SQLite
- Add user authentication (Flask-Login)
- Deploy to a cloud server (AWS, GCP, Azure, Railway)
- Set up push notifications for medication reminders
- Integrate with wearables (Fitbit, Apple Health, Google Fit APIs)
- Add HTTPS with a proper domain

---

*Built with Flask + Claude AI + SQLite*
