#!/bin/bash
#
# Script – starta med ett DUBBELKLICK (ingen terminal-pillande behövs).
# Första gången sätts allt upp automatiskt; därefter startar den direkt.
# Stäng det här fönstret för att stänga av appen.
#
cd "$(dirname "$0")" || exit 1

echo "── Script ──────────────────────────────────"

# 1. Skapa virtuell miljö + installera appens delar (bara första gången)
if [ ! -d ".venv" ]; then
  echo "Förbereder första gången – det kan ta ett par minuter ..."
  if ! command -v python3 >/dev/null 2>&1; then
    echo "❌ Python 3 saknas. Installera från https://www.python.org/downloads/"
    echo "   och dubbelklicka sedan igen."
    read -r -p "Tryck Enter för att stänga."
    exit 1
  fi
  python3 -m venv .venv || { echo "Kunde inte skapa miljön."; read -r; exit 1; }
  ./.venv/bin/pip install --upgrade pip >/dev/null
  ./.venv/bin/pip install -r requirements.txt || { echo "Installationen misslyckades."; read -r; exit 1; }
fi

# 2. Skapa .env från mallen om den saknas
if [ ! -f ".env" ] && [ -f ".env.example" ]; then
  cp .env.example .env
  echo "ℹ️  En .env-fil skapades – öppna den och fyll i dina inställningar (nyckel, watch-mapp)."
fi

# 3. Ladda inställningar (hanterar mellanslag i sökvägar)
if [ -f ".env" ]; then
  set -a
  . ./.env
  set +a
fi

# 4. Öppna webbläsaren strax efter start (app-fönster i Chrome om det finns, annars standardwebbläsare)
URL="http://127.0.0.1:8000"
(
  sleep 2
  if [ -d "/Applications/Google Chrome.app" ]; then
    open -na "Google Chrome" --args --app="$URL" 2>/dev/null || open "$URL"
  else
    open "$URL"
  fi
) &

echo "✅ Script körs på $URL"
echo "   Stäng det här fönstret för att stänga av."
echo "────────────────────────────────────────────"

exec ./.venv/bin/uvicorn app.main:app
