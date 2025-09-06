"""Streamlit dashboard for monitoring the Goblin agent."""

import streamlit as st

from planner.planner import plan
from wallet.agent_wallet import get_balance

# Attempt to pull history and error information from the orchestrator if
# available.  These fall back to simple placeholders when the orchestrator is
# not present (e.g., during local development).
try:  # pragma: no cover - optional orchestrator dependency
    from orchestrator import get_error_log, get_transaction_history, get_plan_prompt
except Exception:  # pragma: no cover - best effort fallbacks
    def get_transaction_history() -> list:
        return []

    def get_error_log() -> list:
        return []

    def get_plan_prompt() -> str:
        return "Determine the next action for the agent."


def main() -> None:
    """Render the dashboard."""

    st.title("Goblin Agent Dashboard")

    # Current SOL balance
    try:
        balance = get_balance()
    except Exception as exc:  # pragma: no cover - runtime connectivity
        balance = 0.0
        st.error(f"Failed to fetch balance: {exc}")
    st.metric("Current SOL Balance", f"{balance:.4f} SOL")

    # Progress bar from 1 SOL towards a 10 SOL goal
    start, target = 1.0, 10.0
    progress = (balance - start) / (target - start)
    progress = max(0.0, min(progress, 1.0))
    st.progress(progress)
    st.caption(f"Progress to {target} SOL goal")

    # Transaction history
    st.subheader("Transaction History")
    history = get_transaction_history()
    if history:
        st.table(history)
    else:
        st.write("No transactions recorded.")

    # Error log
    st.subheader("Error Log")
    errors = get_error_log()
    if errors:
        for err in errors:
            st.write(err)
    else:
        st.write("No errors logged.")

    # Next action plan
    st.subheader("Next Action Plan")
    try:
        next_plan = plan(get_plan_prompt())
        st.write(next_plan)
    except Exception as exc:  # pragma: no cover - runtime connectivity
        st.error(f"Failed to generate plan: {exc}")


if __name__ == "__main__":
    main()
