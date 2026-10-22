"""
Second-Factor Verifier: Voice PIN and confirmation stub.
Used to protect Tier 4 administrative actions and Tier 3 guest access delegations.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


class SecondFactorVerifier:
    """
    Manages second-factor authentication for sensitive commands:
    - Voice PIN verification
    - Verbal confirmation flags
    """

    def __init__(self, policy_store):
        self.policy_store = policy_store

    def verify_pin(self, user_id: str, pin_attempt: Optional[str]) -> bool:
        """
        Validates the provided voice PIN against the enrolled user's record.
        """
        if not user_id or not pin_attempt:
            return False
        return self.policy_store.verify_user_pin(user_id, pin_attempt)

    def verify_confirmation(self, confirmed: bool) -> bool:
        """
        Validates explicit verbal confirmation for risky delegations.
        """
        return bool(confirmed)
