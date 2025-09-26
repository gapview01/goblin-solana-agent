import logging
from telegram import Update
from telegram.ext import ContextTypes

from bot.texts import START_TEXT, CHECK_TEXT, DO_TEXT, GROW_TEXT

log = logging.getLogger(__name__)


async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    log.info("menu:start")
    await context.bot.send_message(chat_id=update.effective_chat.id, text=START_TEXT)


async def handle_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    log.info("menu:check")
    await context.bot.send_message(chat_id=update.effective_chat.id, text=CHECK_TEXT)


async def handle_do(update: Update, context: ContextTypes.DEFAULT_TYPE):
    log.info("menu:do")
    await context.bot.send_message(chat_id=update.effective_chat.id, text=DO_TEXT)


async def handle_grow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    log.info("menu:grow")
    await context.bot.send_message(chat_id=update.effective_chat.id, text=GROW_TEXT)


async def handle_plain_words(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (update.message.text or "").strip().lower()
    if txt == "check":
        return await context.bot.send_message(chat_id=update.effective_chat.id, text="Try /check to see Balance and Quote.")
    if txt == "do":
        return await context.bot.send_message(chat_id=update.effective_chat.id, text="Try /do to see Swap, Stake, and Unstake.")
    if txt == "grow":
        return await context.bot.send_message(chat_id=update.effective_chat.id, text="Try /grow to set a goal and get a plan.")
    # fall through: let other handlers manage
    return None


