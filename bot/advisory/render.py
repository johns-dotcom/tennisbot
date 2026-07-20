"""Stage 2: render the fact block as narrative prose via the Anthropic API,
then validate. Two attempts, then the plain template (which cannot fabricate).
"""
from __future__ import annotations

import json

from bot.advisory.facts import FactBlock
from bot.advisory.template import render_template
from bot.advisory.validate import validate_numbers
from bot.config import settings
from bot.log import get_logger

log = get_logger("advisory.render")

STYLE_EXAMPLE = """Julia Adams has won 5 of her last 7 matches that have gone to set 3. \
Zelnickova has lost 3 straight set 3s and has not won one since April. Adams' set-3 win \
rate is 15% higher than Zelnickova's and she has been much better in the last 2 months. \
All of Zelnickova's wins have been 2-0 skunks — she does not have the set-3 experience \
Adams does. Adams has won 9 of her last 12 matches and has taken 11 of those to set 3. \
She is the better player down the stretch. **ADAMS ML @ 54¢ — model edge 8%** \
*(Market movement implies the match just went 1-1, est. confidence 91%, awaiting score \
confirmation.)*"""

SYSTEM = """You write live tennis betting advisories. Voice: stat-dense, sharp, \
a professional bettor talking to other bettors. No hedging, no filler, no preamble.

HARD RULES:
- Use ONLY numbers that appear in the fact block JSON. Never compute, combine, \
round differently, or invent a number. If unsure, leave the number out.
- In prose, "games" may colloquially mean MATCHES (bettor slang). Never state \
tennis-game-level stats — you have none.
- End with the recommendation in this exact form: \
**{NAME} ML @ {price}¢ — model edge {edge}%**
- If state_confirmed is false, append exactly one italic sentence noting the \
state is inferred from market movement with its confidence, awaiting score \
confirmation.
- 3 to 6 sentences before the recommendation. No headers, no bullet lists."""


def render_prose(block: FactBlock) -> tuple[str, bool, bool]:
    """Returns (text, validator_passed, used_template)."""
    cfg = settings()
    if not cfg.anthropic_api_key:
        log.warning("ANTHROPIC_API_KEY not set — using template rendering")
        return render_template(block), True, True

    import anthropic

    client = anthropic.Anthropic(api_key=cfg.anthropic_api_key)
    payload = json.dumps(block.to_json(), indent=1)
    user_msg = (f"Style example (different match, do not copy its numbers):\n"
                f"{STYLE_EXAMPLE}\n\nFact block:\n{payload}\n\n"
                f"Write the advisory for {block.recommended_name}.")
    feedback = ""
    for attempt in (1, 2):
        try:
            resp = client.messages.create(
                model=cfg.anthropic_model, max_tokens=400,
                system=SYSTEM,
                messages=[{"role": "user", "content": user_msg + feedback}])
            prose = resp.content[0].text.strip()
        except Exception as e:
            log.error("anthropic render failed", error=str(e), attempt=attempt)
            break
        ok, bad = validate_numbers(prose, block.allowed_numbers)
        if ok:
            return prose, True, False
        log.warning("numeric validation failed", attempt=attempt, offending=bad)
        feedback = (f"\n\nYour previous draft used numbers not in the fact block: "
                    f"{bad}. Rewrite using ONLY fact-block numbers.")
    return render_template(block), True, True
