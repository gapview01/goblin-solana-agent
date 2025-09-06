"""Basic Slack Bolt app placeholder."""
from slack_bolt import App


def create_app(token: str, signing_secret: str) -> App:
    """Create a simple Slack app."""
    app = App(token=token, signing_secret=signing_secret)

    @app.event("app_mention")
    def handle_mention(event, say):
        say("Goblin at your service!")

    return app
