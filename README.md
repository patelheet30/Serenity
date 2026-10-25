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

The bot stores everything in Postgres and connects using the standard `PGHOST` / `PGUSER` / `PGPASSWORD` / `PGDATABASE` environment variables. For a local run, point those at any Postgres you have (the schema is created on first start), then:

```bash
uv run main.py
```

### Dashboard (local dev)
The dashboard is a SvelteKit SPA in `dashboard/`; its API lives inside the bot under `/api` on the same port as `/metrics`. Run the bot as above, then:

```bash
cd dashboard
npm install
npm run dev
```

Vite serves the dashboard on http://localhost:5173 and proxies `/api` to the bot on `METRICS_PORT` (default 8080). For login, set `DISCORD_CLIENT_ID`, `DISCORD_CLIENT_SECRET` and `DASHBOARD_SECRET` in `.env` (see `.env.example`) and add `http://localhost:5173/api/auth/callback` as a redirect in the Discord developer portal. `npm run check`, `npm test` and `npm run build` are what CI runs. `/gallery` shows every UI component.

### Deployment
Production runs with `docker compose` (the bot, a `postgres` container and `dashboard`; see `docker-compose.yml`). Every merge to `main` deploys automatically through `.github/workflows/deploy.yml`, which copies the repo to the server and rebuilds the bot and the dashboard.

The `dashboard` container is Caddy serving the built SPA and proxying `/api/*` to the bot; `/metrics` is never proxied. Nginx Proxy Manager on the VM owns ports 80/443: a proxy host for `serenitybot.app` forwards to `172.17.0.1:8090` with a Let's Encrypt certificate. The bot's own port is bound to the Docker bridge (`172.17.0.1:8080`), where Prometheus scrapes it, so it isn't reachable from the internet. The server's `.env` needs `DASHBOARD_URL=https://serenitybot.app`, `DISCORD_CLIENT_ID`, `DISCORD_CLIENT_SECRET` and `DASHBOARD_SECRET`.

### Backups
On the production server, cron runs [`scripts/backup_postgres.sh`](scripts/backup_postgres.sh) nightly at 03:30 UTC. It writes a `pg_dump` (custom format) to `~/serenity-backups/` (last 7 kept) and uploads it to the `serenity-backups` OCI Object Storage bucket through a write-only pre-authenticated request. A bucket lifecycle rule deletes uploads after 30 days. The log is at `~/serenity-backups/backup.log`.

To restore, take the newest dump from `~/serenity-backups/`, or download one from the bucket in the OCI console, then:

```bash
cd ~/serenity && docker compose stop serenity
docker exec -i serenity-postgres sh -c 'pg_restore --clean --if-exists --no-owner -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < ~/serenity-backups/serenity-YYYYMMDDTHHMMSSZ.dump
docker compose start serenity
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
├── dashboard/
│   ├── api.py              # /api app: login, auth + guild-access middleware, Discord data
│   ├── auth.py             # Discord OAuth2, sessions, per-guild authorization
│   ├── common.py           # app keys, JSON errors, input checks, server-log change log
│   ├── moderation.py       # cases, reason edits, clearing warnings, mod stats
│   └── settings.py         # modules, slowmode settings and activity, logging
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
- [asyncpg](https://github.com/MagicStack/asyncpg)
