START_TEXT = """👾 GoblinBot Ready ✅

💰 /check (balance, quote)
🛠️ /do (swap, stake, unstake)
🌱 /grow (plan)
"""

CHECK_TEXT = """💰 Check Options:
- 👛 /balance  — see tokens in your wallet
- 📊 /quote    — estimate what you’d receive on a swap
"""

DO_TEXT = """🛠️ Do Options:
- 🔄 /swap     — trade one token for another
- 🌱 /stake    — deposit to earn
- 📤 /unstake  — withdraw your deposit
"""

GROW_TEXT = """🌱 Grow Options:
- 🧠 /plan     — set a goal and get a plan

Takes ~7 minutes ⏳😅
"""

QUOTE_HELP = """📊 Quote time!
Pick one or type your own:

- 🔄 /quote SOL USDC 0.5
- 💰 /quote SOL BONK 1
- ✍️ Custom — type: /quote FROM TO AMOUNT

(Advanced: add [slippage_bps], e.g. /quote SOL USDC 0.5 100)
"""

SWAP_HELP = """🔄 Let’s swap.
Pick one or type your own:

- 🔄 /swap SOL USDC 0.5
- 🔁 /swap USDC SOL 25
- ✍️ Custom — type: /swap FROM TO AMOUNT

Defaults: slippage 1.0%
(Advanced: add [slippage_bps], e.g. /swap SOL USDC 0.5 100)
"""

STAKE_HELP = """🌱 Stake to earn.
Examples:
- 🌱 /stake SOL 1
- 🌱 /stake mSOL 2
- ✍️ Custom — type: /stake TOKEN AMOUNT
"""

UNSTAKE_HELP = """📤 Unstake your deposit.
Examples:
- 📤 /unstake SOL 1
- 📤 /unstake mSOL 2
- ✍️ Custom — type: /unstake TOKEN AMOUNT
"""

PLAN_HELP = """🧠 Set a goal and get a plan.
Try:
- 🧠 /plan grow 1 SOL to 10 SOL
- 🧠 /plan earn yield on 2 SOL this month

(You can also describe your goal in plain English.)
"""

ERR_TOO_MUCH = "⚠️ That’s more than your balance ({balance}). Try a smaller amount or MAX."
ERR_UNKNOWN_TOKEN = "🤔 I don’t recognize “{token}”. Try SOL, USDC, BONK, mSOL, or type the symbol again."
ERR_MISSING = "👀 I need a bit more info. See examples above or type “help”."


