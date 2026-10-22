"""
Interactive and CLI Text-Based Simulator for Voice-Identity Access Control Layer.

Usage:
  # Single command with named flags:
  python simulator.py --speaker guest --confidence 0.4 --utterance "unlock the front door"
  
  # Single command with raw formatted string:
  python simulator.py "speaker=guest, confidence=0.4, utterance='unlock the front door'"

  # Enrolled user running Tier 4 action with PIN:
  python simulator.py --speaker gauravi --confidence 0.95 --pin 1234 --utterance "Allow guests to use smart home control"

  # Interactive REPL:
  python simulator.py
"""

import argparse
import json
import re
import sys
from typing import Optional

from access_control import (
    AccessDecision,
    PolicyEnforcementGate,
    PolicyStore,
    Role,
    SensitivityTier,
    SpeakerIdentificationResult,
)


def parse_inline_string(text: str) -> dict:
    """
    Parses a string like:
      speaker=guest, confidence=0.4, utterance='unlock the front door'
    """
    params = {}
    m_speaker = re.search(r"speaker\s*=\s*['\"]?([a-zA-Z0-9_-]+)['\"]?", text, re.IGNORECASE)
    if m_speaker:
        val = m_speaker.group(1).strip()
        params["speaker"] = None if val.lower() == "guest" else val

    m_conf = re.search(r"confidence\s*=\s*([0-9.]+)", text, re.IGNORECASE)
    if m_conf:
        params["confidence"] = float(m_conf.group(1))

    m_pin = re.search(r"pin\s*=\s*['\"]?([0-9]+)['\"]?", text, re.IGNORECASE)
    if m_pin:
        params["pin"] = m_pin.group(1)

    m_utt = re.search(r"utterance\s*=\s*['\"]([^'\"]+)['\"]", text, re.IGNORECASE)
    if m_utt:
        params["utterance"] = m_utt.group(1)
    else:
        # If no explicit quotes, take whatever follows utterance=
        m_utt2 = re.search(r"utterance\s*=\s*([^,]+)", text, re.IGNORECASE)
        if m_utt2:
            params["utterance"] = m_utt2.group(1).strip()

    return params


# Ensure UTF-8 output on Windows terminals
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def format_decision(decision: AccessDecision) -> str:
    if decision.allowed:
        status_tag = "[ALLOWED]"
    elif decision.requires_second_factor:
        status_tag = "[CHALLENGE: PIN REQUIRED]"
    elif decision.requires_confirmation:
        status_tag = "[CHALLENGE: CONFIRMATION REQUIRED]"
    else:
        status_tag = "[DENIED]"

    lines = [
        "+" + "-" * 76 + "+",
        f"| Status      : {status_tag}",
        f"| Effective   : {decision.effective_role.value.upper()} (User: {decision.speaker_id or 'Guest'})",
        f"| Category    : {decision.category.upper()} (Tier {decision.tier.value} - {decision.tier.name})",
        f"| Reason      : {decision.reason}",
        f"| Assistant   : \"{decision.response_message}\"",
        "+" + "-" * 76 + "+",
    ]
    return "\n".join(lines)


def run_simulation(
    gate: PolicyEnforcementGate,
    speaker: Optional[str],
    confidence: float,
    utterance: str,
    pin: Optional[str] = None,
    confirmed: bool = False,
    multi_speakers: Optional[list] = None,
    is_ambiguous: bool = False,
) -> AccessDecision:
    detected = multi_speakers or ([speaker] if speaker else ["guest"])
    speaker_res = SpeakerIdentificationResult(
        user_id=speaker,
        confidence=confidence,
        detected_speakers=detected,
        is_ambiguous=is_ambiguous,
        is_live=True,
    )
    return gate.process_request(
        utterance=utterance,
        speaker_result=speaker_res,
        pin_attempt=pin,
        confirmation=confirmed,
    )


def interactive_repl(gate: PolicyEnforcementGate):
    print("\n" + "=" * 70)
    print("  Alexa+ Voice-Identity Access Control Simulator (Interactive REPL)")
    print("=" * 70)
    print("Commands:")
    print("  - Type an utterance directly (uses current default speaker)")
    print("  - Type full spec: speaker=guest, confidence=0.4, utterance='...'")
    print("  - ':speaker <name|guest>' to change active speaker")
    print("  - ':conf <0.0-1.0>' to change confidence")
    print("  - ':pin <1234>' to set active voice PIN")
    print("  - ':status' to view guest permissions")
    print("  - ':reset' to reset policies to default")
    print("  - ':quit' or ':exit' to leave")
    print("-" * 70)

    current_speaker: Optional[str] = None
    current_confidence: float = 0.90
    current_pin: Optional[str] = None

    while True:
        try:
            prompt_user = current_speaker or "guest"
            user_input = input(f"\n[{prompt_user} @ {current_confidence:.2f}] > ").strip()
            if not user_input:
                continue

            if user_input in {":quit", ":exit"}:
                print("Exiting simulator. Goodbye!")
                break
            elif user_input.startswith(":speaker"):
                parts = user_input.split(maxsplit=1)
                if len(parts) > 1 and parts[1].lower() != "guest":
                    current_speaker = parts[1].strip()
                else:
                    current_speaker = None
                print(f"Active speaker set to: {current_speaker or 'guest'}")
                continue
            elif user_input.startswith(":conf"):
                parts = user_input.split(maxsplit=1)
                if len(parts) > 1:
                    current_confidence = float(parts[1])
                print(f"Active confidence set to: {current_confidence:.2f}")
                continue
            elif user_input.startswith(":pin"):
                parts = user_input.split(maxsplit=1)
                current_pin = parts[1].strip() if len(parts) > 1 else None
                print(f"Active PIN set to: {current_pin}")
                continue
            elif user_input == ":status":
                perms = gate.policy_store.get_guest_permissions_summary()
                print("\nCurrent Guest Permissions:")
                for cat, allowed in perms.items():
                    print(f"  - {cat:28s}: {'ALLOWED' if allowed else 'BLOCKED'}")
                continue
            elif user_input == ":reset":
                gate.policy_store.reset_to_defaults()
                print("Policy permissions reset to standard defaults.")
                continue

            # Check if user entered inline syntax
            if "utterance=" in user_input or "speaker=" in user_input:
                parsed = parse_inline_string(user_input)
                spk = parsed.get("speaker", current_speaker)
                cnf = parsed.get("confidence", current_confidence)
                utt = parsed.get("utterance", user_input)
                p = parsed.get("pin", current_pin)
            else:
                spk = current_speaker
                cnf = current_confidence
                utt = user_input
                p = current_pin

            decision = run_simulation(gate, spk, cnf, utt, pin=p)
            print(format_decision(decision))

        except (KeyboardInterrupt, EOFError):
            print("\nExiting simulator.")
            break
        except Exception as e:
            print(f"Error: {e}")


def main():
    parser = argparse.ArgumentParser(description="Voice-Identity Access Control Simulator")
    parser.add_argument("inline", nargs="?", help="Inline format: \"speaker=guest, confidence=0.4, utterance='...'\"")
    parser.add_argument("--speaker", type=str, default=None, help="Speaker user_id (default: guest / None)")
    parser.add_argument("--confidence", type=float, default=0.90, help="Speaker match confidence (0.0 to 1.0)")
    parser.add_argument("--utterance", type=str, default=None, help="The spoken voice utterance")
    parser.add_argument("--pin", type=str, default=None, help="Voice PIN for second factor")
    parser.add_argument("--confirmed", action="store_true", help="Explicit confirmation flag")
    parser.add_argument("--multi", nargs="*", default=None, help="Detected multi-speakers list")
    parser.add_argument("--ambiguous", action="store_true", help="Flag ambiguous speaker match")
    args = parser.parse_args()

    gate = PolicyEnforcementGate()

    if args.inline:
        parsed = parse_inline_string(args.inline)
        speaker = parsed.get("speaker", args.speaker)
        confidence = parsed.get("confidence", args.confidence)
        utterance = parsed.get("utterance", args.inline)
        pin = parsed.get("pin", args.pin)
        decision = run_simulation(gate, speaker, confidence, utterance, pin=pin)
        print(format_decision(decision))
    elif args.utterance:
        decision = run_simulation(
            gate,
            speaker=args.speaker if args.speaker and args.speaker.lower() != "guest" else None,
            confidence=args.confidence,
            utterance=args.utterance,
            pin=args.pin,
            confirmed=args.confirmed,
            multi_speakers=args.multi,
            is_ambiguous=args.ambiguous,
        )
        print(format_decision(decision))
    else:
        interactive_repl(gate)


if __name__ == "__main__":
    main()
