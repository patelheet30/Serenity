# Privacy Policy

**Serenity Discord Bot**  
Last updated: July 2026

---

## Overview

Serenity ("the bot", "we", "our") is an open-source Discord bot that provides automatic slowmode management, audit logging, and moderation tools for Discord servers. This policy explains what data we collect, how we use it, and your rights regarding that data.

---

## What Data We Collect

### Data We Store

| Data | Purpose | Retention |
|---|---|---|
| Guild (server) IDs | Identify which servers have Serenity installed and store their configuration | Until deletion is requested via our support server |
| Channel IDs | Store slowmode and log channel configuration per server | Until deletion is requested via our support server |
| Message timestamps and counts | Calculate message rates to determine appropriate slowmode settings — **not message content** | Automatically deleted after 24 hours |
| User IDs | Used in moderation cases, warning records, and logging ignore lists | Until cleared by a server administrator via bot commands, or until deletion is requested via our support server |
| Moderation case data | Store the action type, reason, duration, and moderator/target user IDs for each moderation action | Until cleared by a server administrator, or until deletion is requested via our support server |
| Warning records | Track active warnings per user per server | Until cleared by a server administrator via `/clearwarns`, or until deletion is requested via our support server |
| Log channel configuration | Store which Discord channels receive which categories of audit logs, and any configured ignore lists | Until deletion is requested via our support server |
| Historical message rate patterns | Aggregated hourly message-per-minute averages per channel used to detect anomalous activity — **not individual messages** | Automatically deleted after 30 days |

### Data We Do Not Store

- **Message content** — We never store the text of any message. Message content may be read from Discord's temporary cache at the moment a delete or edit event fires in order to send a log embed to your server's configured log channel, but it is never written to our database.
- **Usernames, display names, or avatars** — We store user IDs only.
- **Voice audio** — We only track voice state events (join/leave/move), never audio.
- **Direct messages** — The bot does not operate in DMs.
- **Email addresses, IP addresses, or any personal contact information.**

---

## How We Use Data

Data collected is used solely to operate Serenity's features within your server:

- **Guild and channel configuration** is used to know where to apply slowmode and where to send log embeds.
- **Message activity counts** are used to calculate message rates for the slowmode engine. Individual messages are never identified.
- **Moderation case and warning data** is used to power the `/case`, `/cases`, `/warnings`, and `/modstats` commands within your server.
- **Ignore lists** are used to exclude specific channels or users from audit log embeds.

We do not use any data for advertising, analytics sold to third parties, or any purpose beyond operating the bot.

---

## Data Sharing

We do not sell or share your data with any third parties for commercial purposes.

Audit log embeds (member joins, message edits, moderation actions, etc.) are sent to Discord channels that you configure within your own server. Those messages are governed by Discord's own [Privacy Policy](https://discord.com/privacy).

Guild IDs and channel IDs are transmitted to Grafana Cloud as metric labels for performance monitoring purposes. See the **Third-Party Services** section below for details.

---

## Third-Party Services

### Discord API
Serenity communicates exclusively with Discord to receive events and send responses. Discord's Privacy Policy can be found at [discord.com/privacy](https://discord.com/privacy).

### Prometheus and Grafana Cloud
Serenity collects internal performance metrics using the `prometheus_client` library and exposes them via a protected `/metrics` endpoint. These metrics are scraped by Grafana Cloud and used solely for monitoring the bot's operational health and performance.

**What is included in metrics data:**
- Guild IDs and channel IDs, used as labels on aggregated counters and gauges (e.g., messages processed per guild, current slowmode per channel)
- Aggregated numerical values such as message processing counts, slowmode change frequency, engine calculation durations, and task execution times

**What is not included in metrics data:**
- Message content
- Usernames, display names, or any personally identifiable information
- User IDs

The metrics endpoint is protected with HTTP Basic Auth and is not publicly accessible. Grafana Cloud's Privacy Policy can be found at [grafana.com/legal/privacy-policy](https://grafana.com/legal/privacy-policy).

---

## Data Storage and Security

Serenity's primary data store is a SQLite database on a self-hosted server. We take reasonable precautions to secure our infrastructure, but no system is perfectly secure. The bot is open source and its data handling can be inspected in full at [https://github.com/patelheet30/Serenity](https://github.com/patelheet30/Serenity).

---

## Your Rights

### Server Administrators

- **Request data deletion**: To request full deletion of your server's data, contact us via our support server. We will action all requests within a reasonable timeframe.
- **Manage moderation records**: Moderation cases and warnings can be managed through the bot's own commands (`/clearwarns`, `/case-edit`).
- **Manage log configuration**: Logging configuration can be updated or removed at any time via `/logging` commands.

### Individual Users

- **Opt out of logging**: Server administrators can add you to a logging ignore list via `/logging ignore-user`. This will exclude your actions from audit log embeds in that server.
- **Request data deletion**: You may contact us via our support server to request that any records containing your user ID (moderation cases, warnings) be removed. Note that moderation records belong to the server they were issued in, so we may require consent from the relevant server administrator before removing them.

---

## Data Retention

- **Message activity records** are automatically deleted after **24 hours**.
- **Historical pattern data** is automatically deleted after **30 days**.
- **All other data** — including server configuration, log channel configuration, moderation cases, and warnings — is retained until a server administrator removes it via bot commands, or until deletion is requested via our support server.

---

## Children's Privacy

Serenity does not knowingly collect data from users under the age of 13. As a Discord bot, our service is subject to Discord's own minimum age requirements. If you believe a user under 13 has data stored as a result of using Serenity, please contact us and we will remove it.

---

## Changes to This Policy

We may update this policy as the bot's features change. The date at the top of this document will reflect the most recent revision. Continued use of Serenity after changes are posted constitutes acceptance of the updated policy.

---

## Contact

For privacy requests, data deletion, or questions about this policy:

- **Discord**: [Serenity Support Server](https://discord.gg/GSHQdQNszP)
- **GitHub**: [https://github.com/patelheet30/Serenity](https://github.com/patelheet30/Serenity)
