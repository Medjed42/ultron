"""
actions/ask_claude.py — Action to consult Claude Code Brain directly.

Auto-discovered by core/action_loader.py as a native Ultron action.
Allows Ultron or the user to ask Claude Code for complex reasoning, architectural
advice, coding, or capability verification, with seamless Antigravity fallback.
"""
from __future__ import annotations

import json
from core import claude_brain


def ask_claude(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
    speak=None,
) -> str:
    p = parameters or {}
    prompt = p.get("prompt", "").strip()
    force_brain = p.get("force_brain", "auto").strip().lower()

    if not prompt:
        return "Molim vas zadajte pitanje ili zadatak za Claude mozak."

    if player:
        player.write_log(f"[Brain] Pitaj Claude-a: {prompt[:60]}...")

    res = claude_brain.execute(
        prompt=prompt,
        preferred_brain=force_brain,
        timeout=120,
        speak_fn=speak,
    )

    if res.get("success"):
        brain_used = res.get("brain_used", "claude")
        output = res.get("output", "")
        note = res.get("note", "")
        msg = f"[{brain_used.upper()} ODGOVOR]\n{output}"
        if note:
            msg += f"\n\n({note})"
        return msg
    elif res.get("both_exhausted"):
        msg = "UPOZORENJE: I Claude i Antigravity modeli su trenutno dostigli limit kvote!"
        if speak:
            speak(msg)
        return msg
    else:
        return f"Greška u izvršavanju mozga: {res.get('error', 'Nepoznata greška')}"


TOOL = {
    "name": "ask_claude",
    "description": (
        "Consults Claude (through the signed-in Claude Code CLI, using the account's model) "
        "for advanced reasoning, programming, architectural decisions and capability "
        "verification. Claude answers in text only here — no shell, files or browser. "
        "With force_brain=auto it falls back to Gemini when Claude is unavailable, if the "
        "routing settings allow it, and says so. force_brain=claude never substitutes."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "prompt": {
                "type": "STRING",
                "description": "Question, task, or coding requirement to ask Claude",
            },
            "force_brain": {
                "type": "STRING",
                "description": "auto | claude | antigravity (default: auto)",
            },
        },
        "required": ["prompt"],
    },
    "handler": ask_claude,
}
