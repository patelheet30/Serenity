# Serenity Discord Bot

## Overview

Serenity is an intelligent Discord bot that monitors channel activity and automatically adjusts slowmode settings to maintain healthy conversation flow. It has since grown into a broader moderation platform, adding full audit logging, moderation commands with case tracking, and a module system so servers can enable only the features they need.

Originally built for personal use, Serenity now serves 60+ servers handling thousands of messages daily. This is a full rewrite of [Serenity Legacy](https://github.com/patelheet30/Serenity-Legacy), focused on code quality, maintainability, and multi-server scalability.

---

## Features

### 🤖 Automatic Slowmode
Dynamically adjusts slowmode based on real-time channel activity using a multi-factor scoring system: current message rate, historical patterns, velocity (rate of change), and past slowmode effectiveness.

### 📋 Audit Logging
Sends formatted embeds directly to configured Discord channels — nothing is stored in the database (ToS compliant). Five log categories:
- **Member** — joins, leaves, nickname/role/avatar changes
- **Message** — deletions, edits, bulk deletes
- **Voice** — joins, leaves, moves, mutes, streaming
- **Server** — channel/role/emoji/server setting changes
- **Mod** — all moderation actions with case numbers

### 🔨 Moderation
Full moderation suite with per-guild case tracking:
- Ban, unban, softban, kick
- Timeout and untimeout with duration support
- Warnings with active warning counts
- Case viewing, history, and reason editing
- Server moderation statistics

### 🧩 Module System
Enable only the features you need. Moderation requires logging to be enabled first (it needs somewhere to send mod-log embeds).

---

## Getting Started

### Prerequisites
- Python 3.13+
- [UV](https://github.com/astral-sh/uv) (recommended)
- A Discord bot token ([Discord Developer Portal](https://discord.com/developers/applications))

### Privileged Intents
In the Developer Portal under your bot's settings, enable:
- **Server Members Intent** — required for member join/leave/update logs
- **Message Content Intent** — required for message edit/delete log content

### Installation

```bash
git clone https://github.com/patelheet30/Serenity.git
cd Serenity
uv sync
cp .env.example .env
```

Edit `.env` and add your bot token:
```
TOKEN=your-token-here
LOG_LEVEL=INFO
```

```bash
uv run main.py
```

### Docker / Fly.io
The repo includes a `Dockerfile` and `fly.toml` for deployment. The database is stored on a mounted volume at `/data`.

```bash
flyctl deploy --remote-only
```

---

## Setup in Discord

### 1. Automatic Slowmode
```
/module enable slowmode
/serenity guild enable
/serenity channel enable          (in each channel you want managed)
/serenity guild threshold 15      (optional: messages/min before slowmode kicks in)
```

### 2. Audit Logging
```
/module enable logging
/logging setup                    (creates all 5 log channels automatically)
```

Or configure manually:
```
/logging member-log #member-log
/logging message-log #message-log
/logging voice-log #voice-log
/logging server-log #server-log
/logging mod-log #mod-log
```

### 3. Moderation
Requires logging to be enabled first (mod actions are sent to the mod-log channel).
```
/module enable moderation
```

---

## Commands

### General
| Command | Description |
|---|---|
| `/ping` | Check bot latency |
| `/stats [channel]` | View slowmode activity stats for a channel |
| `/about` | About Serenity |

### Slowmode (`/serenity`)
| Command | Description |
|---|---|
| `/serenity guild enable/disable` | Enable or disable Serenity in this server |
| `/serenity guild threshold <n>` | Default messages/min before slowmode activates |
| `/serenity guild interval <n>` | Slowmode check interval in minutes |
| `/serenity channel enable/disable` | Enable or disable a specific channel |
| `/serenity channel threshold <n>` | Per-channel message threshold |
| `/serenity config` | View server configuration |
| `/serenity channel-info` | View channel configuration |

### Logging (`/logging`) — requires Logging module
| Command | Description |
|---|---|
| `/logging setup` | Create all 5 log channels automatically |
| `/logging view` | View current logging configuration |
| `/logging <type>-log <channel>` | Manually set a log channel |
| `/logging enable/disable <type>` | Toggle a log type |
| `/logging ignore-channel <channel>` | Exclude a channel from logs |
| `/logging ignore-user <user>` | Exclude a user from logs |

### Moderation — requires Moderation module
| Command | Permission | Description |
|---|---|---|
| `/ban <user> [reason]` | Ban Members | Ban a user |
| `/unban <user> [reason]` | Ban Members | Unban a user |
| `/kick <user> [reason]` | Kick Members | Kick a member |
| `/timeout <user> <duration> <unit> [reason]` | Moderate Members | Timeout a member |
| `/untimeout <user> [reason]` | Moderate Members | Remove a timeout |
| `/warn <user> [reason]` | Manage Messages | Issue a warning |
| `/warnings <user>` | Manage Messages | View active warnings |
| `/clearwarns <user>` | Moderate Members | Clear all warnings |
| `/case <number>` | Manage Messages | View a case |
| `/cases <user>` | Manage Messages | View moderation history |
| `/case-edit <number> <reason>` | Manage Messages | Update a case reason |
| `/reason <number> <reason>` | Manage Messages | Alias for `/case-edit` |
| `/modstats [days]` | Manage Messages | Server moderation statistics |

### Modules (`/module`) — requires Manage Guild
| Command | Description |
|---|---|
| `/module list` | View all modules and their status |
| `/module enable <module>` | Enable a module |
| `/module disable <module>` | Disable a module |

---

## Architecture

```
serenity/
├── core/
│   ├── constants.py        # Slowmode and database config
│   ├── hooks.py            # Arc hooks (require_module)
│   ├── modules.py          # ModuleType enum, ModuleManager
│   └── types.py            # Shared dataclasses
├── database/
│   ├── migrations/         # Versioned schema migrations
│   ├── logging_repository.py
│   ├── moderation_repository.py
│   ├── repository.py       # Core + slowmode data
│   └── schema.sql
├── extensions/
│   ├── admin.py            # /serenity commands
│   ├── cases.py            # /case, /cases, /warnings etc.
│   ├── events.py           # Message activity tracking
│   ├── logging.py          # /logging commands
│   ├── logging_events.py   # Audit log event handlers
│   ├── moderation.py       # /ban, /kick, /timeout etc.
│   ├── modules.py          # /module commands
│   ├── tasks.py            # Background tasks
│   └── user.py             # /ping, /stats, /about
├── services/
│   ├── logging_service.py  # Formats and sends log embeds
│   ├── moderation_service.py # Executes mod actions + cases
│   └── slowmode_engine.py  # Slowmode calculation
└── utils/
    ├── errors.py
    └── logging.py          # Contextual logger
```

---

## Acknowledgements

- [Hikari](https://docs.hikari-py.dev/en/stable/)
- [Hikari-Arc](https://github.com/hypergonial/hikari-arc)
- [aiosqlite](https://aiosqlite.omnilib.dev/)
