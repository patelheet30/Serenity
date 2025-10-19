# Serenity Discord Bot

A Discord bot that automatically sets slowmode on channels based on message activity.

The bot is still growing and is currently being utilised by 50+ servers across Discord.

[Invite the Bot here](https://discord.com/oauth2/authorize?client_id=1359250509009260604&permissions=3120&integration_type=0&scope=bot+applications.commands)

## Features

- Automatically adjust slowmode settings based on channel activity
- Configure thresholds per channel or server-wide
- Admin commands for configuration and monitoring
- Message rate tracking with configurable time windows
- Scheduled cleanup of old message data

## Local Development

### Prerequisites

- Python 3.13+
- UV (https://github.com/astral-sh/uv)

### Setup

1. Clone the repository:
   ```bash
   git clone https://github.com/patelheet30/serenity.git
   cd serenity
   ```

2. Install dependencies:
   ```bash
   uv sync
   ```

3. Create a `.env` file with your Discord bot token:
   ```
   TOKEN=your_discord_bot_token_here
   ```

4. Run the bot:
   ```bash
   uv run bot.py
   ```

## Deployment to Fly.io

### Prerequisites

1. Install the Fly.io CLI:
   ```bash
   curl -L https://fly.io/install.sh | sh
   ```
   
   Or on macOS with Homebrew:
   ```bash
   brew install flyctl
   ```

2. Log in to Fly.io:
   ```bash
   fly auth login
   ```

### Deployment Steps

1. Create the Fly.io app:
   ```bash
   fly apps create auto-slowmode-bot
   ```

2. Set up volume storage for database persistence:
   ```bash
   fly volumes create auto_slowmode_data --size 1 --region lhr
   ```
   Note: Replace `lhr` with your preferred region.

3. Add your Discord bot token as a secret:
   ```bash
   fly secrets set TOKEN=your_discord_bot_token_here
   ```

4. Deploy your application:
   ```bash
   fly deploy
   ```

5. Monitor your deployment:
   ```bash
   fly status
   fly logs
   ```

## Bot Commands

### Admin Commands

- `/auto-slowmode channel enable [channel]` - Enable auto-slowmode for a channel
- `/auto-slowmode channel disable [channel]` - Disable auto-slowmode for a channel
- `/auto-slowmode channel threshold <threshold> [channel]` - Set message rate threshold
- `/auto-slowmode server enable` - Enable auto-slowmode server-wide
- `/auto-slowmode server disable` - Disable auto-slowmode server-wide
- `/auto-slowmode server threshold <threshold>` - Set default message rate threshold
- `/auto-slowmode stats [channel]` - View activity and slowmode statistics

## Configuration

The bot stores configuration and message data in an SQLite database, which is automatically created when the bot starts.

### Default Settings
- Default message rate threshold: 10 messages per minute
- Update interval: 30 seconds
- Message data retention: 24 hours

## Acknowledgements

- Built with [Hikari](https://github.com/hikari-py/hikari) and [Hikari-arc](https://github.com/hypergonial/hikari-arc)
- Uses [UV](https://github.com/astral-sh/uv) for dependency management

