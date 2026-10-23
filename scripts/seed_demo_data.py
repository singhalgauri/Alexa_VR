#!/usr/bin/env python3
"""
scripts/seed_demo_data.py – Seed realistic synthetic demo data for enrolled users.

Creates per-user JSON data files under data/ with:
  - Profile (display name, greeting style)
  - Schedule/calendar events (for today, tomorrow, and coming days)
  - Reminders
  - Shopping list
  - Routines

All data is clearly synthetic and labeled as demo data.

Usage:
    python scripts/seed_demo_data.py
    python scripts/seed_demo_data.py --users alice bob charlie
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

# Ensure repo root is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from mcp_server.user_data import DATA_DIR, save_user_data


def _date_str(offset: int = 0) -> str:
    """Return YYYY-MM-DD for today + offset days."""
    return (date.today() + timedelta(days=offset)).isoformat()


# Day of week helpers
def _weekday_name(offset: int = 0) -> str:
    return (date.today() + timedelta(days=offset)).strftime("%A")


DEMO_USERS = {
    "Priya": {
        "profile": {
            "display_name": "Priya",
            "greeting_style": "friendly",
        },
        "schedule": [
            {"title": "Team standup", "date": _date_str(0), "time": "09:30", "description": "Daily sync with engineering team"},
            {"title": "Dentist appointment", "date": _date_str(0), "time": "14:00", "description": "Regular checkup at Dr. Mehta's clinic"},
            {"title": "Yoga class", "date": _date_str(1), "time": "07:00", "description": "Morning flow session"},
            {"title": "Project review", "date": _date_str(1), "time": "11:00", "description": "Q4 milestone review"},
            {"title": "Dinner with Rahul", "date": _date_str(2), "time": "19:30", "description": "At Bombay Brasserie"},
            {"title": "Book club meeting", "date": _date_str(5), "time": "16:00", "description": "Discussing 'Project Hail Mary'"},
        ],
        "reminders": [
            "Pick up dry cleaning by Thursday",
            "Call mom on Sunday",
            "Renew library books",
            "Submit expense report",
        ],
        "shopping_list": [
            "Basmati rice",
            "Greek yogurt",
            "Avocados",
            "Dishwasher pods",
            "Oat milk",
        ],
        "lists": {
            "groceries": ["Tomatoes", "Onions", "Cilantro", "Ginger"],
            "reading": ["Klara and the Sun", "The Ministry of Time"],
        },
        "routines": [
            {"name": "Morning routine", "steps": ["Turn on kitchen light", "Play morning news", "Start coffee maker"]},
            {"name": "Bedtime routine", "steps": ["Turn off all lights", "Lock front door", "Set alarm for 7 AM"]},
        ],
    },
    "Rahul": {
        "profile": {
            "display_name": "Rahul",
            "greeting_style": "casual",
        },
        "schedule": [
            {"title": "Gym session", "date": _date_str(0), "time": "06:30", "description": "Leg day at FitLife"},
            {"title": "Client call with Acme Corp", "date": _date_str(0), "time": "10:00", "description": "Contract renewal discussion"},
            {"title": "Lunch with Priya", "date": _date_str(0), "time": "12:30", "description": "At the cafeteria"},
            {"title": "Guitar lesson", "date": _date_str(1), "time": "17:00", "description": "Intermediate blues techniques"},
            {"title": "Weekend hike", "date": _date_str(3), "time": "08:00", "description": "Trail at Sunrise Peak, 6 miles"},
            {"title": "Movie night", "date": _date_str(4), "time": "20:00", "description": "New sci-fi release"},
        ],
        "reminders": [
            "Fix the leaking faucet",
            "Order new running shoes",
            "Backup laptop photos",
            "Reply to cousin's wedding RSVP",
        ],
        "shopping_list": [
            "Protein powder",
            "Bananas",
            "Chicken breast",
            "Olive oil",
            "Paper towels",
        ],
        "lists": {
            "groceries": ["Eggs", "Milk", "Bread", "Cheese"],
            "movies": ["Interstellar", "Arrival", "Blade Runner 2049"],
        },
        "routines": [
            {"name": "Workout prep", "steps": ["Play energetic music", "Turn on garage light"]},
            {"name": "Movie night", "steps": ["Dim living room lights", "Turn on TV", "Play ambient sound"]},
        ],
    },
    "Alice": {
        "profile": {
            "display_name": "Alice",
            "greeting_style": "formal",
        },
        "schedule": [
            {"title": "Board meeting", "date": _date_str(0), "time": "09:00", "description": "Q4 strategy presentation"},
            {"title": "Lunch with investors", "date": _date_str(0), "time": "12:00", "description": "At The Capital Grille"},
            {"title": "Piano practice", "date": _date_str(1), "time": "18:00", "description": "Chopin Nocturnes"},
            {"title": "Volunteering at shelter", "date": _date_str(2), "time": "10:00", "description": "Community food bank"},
            {"title": "Flight to NYC", "date": _date_str(4), "time": "06:30", "description": "Delta DL-402, JFK Terminal 4"},
        ],
        "reminders": [
            "Review quarterly budget",
            "Water the garden plants",
            "Schedule car service appointment",
            "Send birthday card to Mom",
        ],
        "shopping_list": [
            "Almond flour",
            "Fresh basil",
            "Sparkling water",
            "Laundry detergent",
        ],
        "lists": {
            "groceries": ["Salmon", "Asparagus", "Lemons", "Quinoa"],
            "travel": ["Passport", "Charger", "Noise-cancelling headphones"],
        },
        "routines": [
            {"name": "Morning routine", "steps": ["Turn on bedroom light", "Play classical music", "Read news"]},
        ],
    },
}


def seed_user(name: str, data: dict) -> None:
    """Write demo data for a single user."""
    # Ensure the user key (filename) is lowercase
    save_user_data(name.lower(), data)
    print(f"  [OK] Seeded data for {name} ({len(data.get('schedule', []))} events, "
          f"{len(data.get('reminders', []))} reminders, "
          f"{len(data.get('shopping_list', []))} shopping items)")


def main():
    parser = argparse.ArgumentParser(description="Seed demo data for Voice Gate users")
    parser.add_argument("--users", nargs="*", help="Specific users to seed (default: all demo users)")
    parser.add_argument("--force", action="store_true", help="Overwrite existing data")
    args = parser.parse_args()

    users_to_seed = args.users if args.users else list(DEMO_USERS.keys())

    print(f"Seeding demo data into {DATA_DIR}/")
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    for name in users_to_seed:
        key = name.title() if name.title() in DEMO_USERS else name
        if key in DEMO_USERS:
            data_path = DATA_DIR / f"{name.lower()}.json"
            if data_path.exists() and not args.force:
                print(f"  [INFO] Skipping {name} (already exists, use --force to overwrite)")
                continue
            seed_user(name, DEMO_USERS[key])
        else:
            # Create minimal data for unknown users
            seed_user(name, {
                "profile": {"display_name": name.title(), "greeting_style": "friendly"},
                "schedule": [
                    {"title": "Demo meeting", "date": _date_str(0), "time": "10:00", "description": "Sample event"},
                ],
                "reminders": ["Sample reminder"],
                "shopping_list": ["Sample item"],
                "lists": {},
                "routines": [],
            })

    print(f"\nDone! {len(users_to_seed)} user(s) seeded.")
    print(f"Data location: {DATA_DIR.resolve()}")


if __name__ == "__main__":
    main()
