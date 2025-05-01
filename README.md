# Crypto BOT  🚀

Python-based scalping & breakout trading bot for the Binance Spot market.

| Branch | Status |
|--------|--------|
| `main` | ![CI](https://img.shields.io/badge/CI-status-passing-brightgreen) |

---

## ✨ Features
- **Modular strategy engine** – plug in indicators in `indicators/`
- **Async Binance client** – fast order placement & market data
- **Simple GUI** (`UI.py`) – start/stop & monitor trades
- **`.env` secrets loading** – no keys in source control
- **Planned**: back-tester, Telegram alerts, multi-exchange support

---

## ⚡ Quick Start

### 1  Clone & set up (local)

```bash
git clone https://github.com/Sportx666/Crypto_BOT.git
cd Crypto_BOT

# create & activate virtual-env
# 1 – create the virtual-env
python -m venv .venv  # use py if using python version > 3.10

# 2 – (temporarily lift the script block, same as before)
Set-ExecutionPolicy -ExecutionPolicy Bypass -Scope Process -Force

# 3 – activate the venv
.\.venv\Scripts\Activate.ps1      # or simply .\.venv\Scripts\activate
                                  # Linux/mac: source .venv/bin/activate

# 4 – install the missing library
pip install -r requirements.txt

# copy template and add your real keys
cp .env.example .env
nano .env   # or VS Code/Notepad

python main.py     # run the bot
