from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from bot.texts import render_compare_card


def _kb(token: str, best_id: Optional[str] = None) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    rows.append([
        InlineKeyboardButton("7d", callback_data=f"h:7d:{token}"),
        InlineKeyboardButton("30d", callback_data=f"h:30d:{token}"),
        InlineKeyboardButton("90d", callback_data=f"h:90d:{token}"),
    ])
    rows.append([
        InlineKeyboardButton("▶️ Simulate All", callback_data=f"sim:all:{token}"),
        InlineKeyboardButton("🔁 Refresh", callback_data=f"refresh:{token}"),
    ])
    rows.append([
        InlineKeyboardButton("✅ Approve ≤ micro", callback_data=f"approve_micro:{token}"),
        InlineKeyboardButton("📝 Edit Goal", callback_data="edit_goal"),
    ])
    if best_id:
        rows.append([
            InlineKeyboardButton("🔎 Simulate best", callback_data=f"sim:best:{best_id}:{token}"),
            InlineKeyboardButton("🛑 Cancel", callback_data="cancel"),
        ])
    else:
        rows.append([InlineKeyboardButton("🛑 Cancel", callback_data="cancel")])
    # optional view button left to server layer
    return InlineKeyboardMarkup(rows)


async def show_compare(update: Update, context: ContextTypes.DEFAULT_TYPE, plan_payload: Dict[str, Any]) -> None:
    token = str(plan_payload.get("token") or "")
    text = render_compare_card(plan_payload)
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=_kb(token))


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q or not q.data:
        return
    parts = q.data.split(":")
    if not parts:
        return
    kind = parts[0]
    if kind in {"h", "refresh"}:
        # h:{horizon}:{token} or refresh:{token}
        horizon = parts[1] if kind == "h" and len(parts) >= 3 else None
        token = parts[2] if kind == "h" and len(parts) >= 3 else (parts[1] if len(parts) >= 2 else "")
        # Let server fetch latest plan from cache; here we only echo UI
        payload = context.user_data.get("last_plan") or {}
        if horizon:
            payload = {**payload, "horizon": horizon}
        await q.edit_message_text(render_compare_card(payload), parse_mode=ParseMode.HTML, reply_markup=_kb(payload.get("token") or ""))
        return
    # Other actions handled in server by existing callbacks; ignore here
    await q.answer()


