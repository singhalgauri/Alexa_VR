"""
Mock Skill Handlers for every category to test end-to-end assistant flows.
Includes strict per-user isolated data stores (calendar, notes, reminders, etc.).
"""

from typing import Any, Dict, List, Optional
from .models import IntentClassification

# Per-user isolated mock personal data
USER_PERSONAL_DATA: Dict[str, Dict[str, Any]] = {
    "gauravi": {
        "calendar": ["10:00 AM: AI Security Sync", "3:00 PM: KremlinSpies demo"],
        "todo_list": ["Review Voice Gate pull request", "Submit contest entry"],
        "reminders": ["Take evening vitamins at 8 PM"],
        "personal_alarms": ["7:00 AM Weekday Alarm"],
        "notes": ["Secret project codename: Horizon"],
        "email": ["2 new unread messages from Engineering"],
        "contacts": [{"name": "Mom", "phone": "555-0199"}, {"name": "Bob", "phone": "555-0144"}],
        "messages": ["Mom: Are you coming home this weekend?"],
        "purchase_history": ["Smart Thermostat (delivered yesterday)"],
        "location_history": ["Home (last 12 hours)"],
    },
    "john": {
        "calendar": ["1:00 PM: Financial Planning", "4:30 PM: Dentist appointment"],
        "todo_list": ["Mow lawn", "Buy groceries"],
        "reminders": ["Water the plants"],
        "personal_alarms": ["6:30 AM Gym Alarm"],
        "notes": ["Gym workout split"],
        "email": ["1 new invoice from utility company"],
        "contacts": [{"name": "Boss", "phone": "555-0101"}],
        "messages": ["Dentist: Your appointment is confirmed."],
        "purchase_history": ["Running shoes"],
        "location_history": ["Office (9 AM - 5 PM)"],
    },
}

# Anonymous Guest data (e.g. guest's own anonymous timers)
GUEST_TIMERS: List[Dict[str, Any]] = []


class SkillDispatcher:
    """
    Dispatches authorized requests to mock handlers for each category.
    """

    def dispatch(
        self,
        intent: IntentClassification,
        user_id: Optional[str] = None,
        effective_role: str = "guest",
    ) -> str:
        cat = intent.category

        if cat == "emergency":
            return "Calling emergency services now. Help is on the way."

        if cat == "personal_data":
            return self._handle_personal_data(intent, user_id)

        if cat == "music_entertainment":
            return "Playing your requested music playlist."

        if cat == "general_info":
            return "Currently it is 72 degrees and sunny with clear skies."

        if cat == "smart_home":
            return "Okay, smart home device command executed."

        if cat == "video_tv":
            return "Turning on Fire TV and resuming playback."

        if cat == "generic_timers":
            GUEST_TIMERS.append({"owner": user_id or "guest", "duration": "10 minutes"})
            return "A 10-minute timer has been set."

        if cat == "third_party_skills":
            skill = intent.details.get("skill_name", "skill")
            return f"Welcome to {skill.replace('_', ' ').title()}! Let's get started."

        if cat == "communication":
            return "Connecting your call now."

        if cat == "home_security":
            return "Security action performed. Front door locked / garage closed."

        if cat == "shopping":
            return "Item added to your shopping cart."

        if cat == "routines":
            routine_name = intent.details.get("routine_name", "routine").replace("_", " ")
            return f"All actions in the {routine_name} have been executed."

        if cat == "kids_family":
            return "Family controls updated successfully."

        if cat == "payments_banking":
            return "Transaction processed successfully."

        if cat == "food_ride_ordering":
            return "Your ride has been ordered and is arriving in 5 minutes."

        if cat == "device_account_settings":
            return "Device settings successfully updated."

        if cat == "guest_permission_management":
            return "Guest permission settings updated."

        return "Command completed."

    def _handle_personal_data(self, intent: IntentClassification, caller_id: Optional[str]) -> str:
        """
        Retrieves personal data strictly for caller_id.
        """
        if not caller_id or caller_id not in USER_PERSONAL_DATA:
            return "You have no personal data recorded."

        data_type = intent.details.get("data_type", "calendar")
        user_store = USER_PERSONAL_DATA[caller_id]
        records = user_store.get(data_type, [])

        if not records:
            return f"Your {data_type.replace('_', ' ')} is currently empty."

        if isinstance(records, list):
            items_str = ", ".join(str(r) for r in records[:3])
            return f"Here is what's on your {data_type.replace('_', ' ')}: {items_str}."

        return f"Here is your {data_type.replace('_', ' ')}: {records}."
