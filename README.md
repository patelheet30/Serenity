# Serenity Discord Bot

## Overview

The intelligent traffic controller for your Discord server. Monitors channel activity in real-time and automatically adjusts slowmode settings to maintain healthy conversation flow during high-traffic periods. Customisable thresholds, admin controls, and almost zero setup required.

## How different is this from Serenity Legacy?

A modern rewrite of the original [Serenity Discord Bot](https://github.com/patelheet30/Serenity-Legacy) with improved performance, scalability, and maintainability. Initially designed for personal use, the bot has evolved into a multiserver solution, serving upwards of 60 servers and handling thousands of messages daily. The bot had to be rewritten since the legacy version was built quickly with minimal readability for private use, making it difficult to extend and maintain. I have taken the core concepts from the legacy version and reimplemented them with a focus on code quality, and ease of use.

I plan on continuing to add features and improvements to this version, including moderation tools, a web dashboard, and more.

## Features

- **Automatic Slowmode Adjustment**: Dynamically adjusts slowmode settings based on real-time channel activity.
- **Customisable Thresholds**: Set your own message rate thresholds to trigger slowmode changes.
- **Admin Controls**: Admins can manually override slowmode settings as needed.
- **Multi-Server Support**: Designed to operate seamlessly across multiple Discord servers.
- **Minimal Setup**: Easy to deploy with minimal configuration required.
- **Open Source**: Fully open-source, allowing for community contributions and transparency.

## Getting Started

### Prerequisites

- Python 3.13+
- UV (https://github.com/astral-sh/uv) for environment management (optional but recommended)
- A Discord Bot Token (create a bot via the Discord Developer Portal)

### Installation

1. Clone the repository:
   ```bash
    git clone https://github.com/patelheet30/Serenity.git
    cd Serenity
   ```
2. Set up a virtual environment (optional but recommended):
   ```bash
    uv sync
   ```
3. Create a `.env` file in the root directory and copy the contents of `.env.example` into it:
   ```bash
    cp .env.example .env
   ```
4. Update the `.env` file with your Discord Bot Token and other configurations as needed.
5. Run the bot:
   ```bash
    uv run main.py
   ```

## Acknowledgements

- [Hikari](https://docs.hikari-py.dev/en/stable/)
- [Hikari-Arc](https://github.com/hypergonial/hikari-arc)
