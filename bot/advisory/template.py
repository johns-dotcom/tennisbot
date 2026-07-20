"""Plain deterministic rendering — the fallback when prose fails validation
twice. Built ONLY from fact-block data, so it can never fabricate a number."""
from __future__ import annotations

from bot.advisory.facts import FactBlock


def render_template(block: FactBlock) -> str:
    lines = [f.sentence_hint + "." for f in block.facts]
    rec = (f"**{block.recommended_name.upper()} ML @ {block.executable_price_cents}¢ "
           f"— model edge {abs(block.edge) * 100:.1f}%**")
    lines.append(rec)
    if not block.state_confirmed:
        lines.append(f"*(Market movement implies the match is at {block.state_key} sets, "
                     f"est. confidence {int(block.state_confidence * 100)}%, "
                     f"awaiting score confirmation.)*")
    return " ".join(lines)
