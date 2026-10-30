# Privacy Policy

**Serenity Discord Bot**  
Last updated: 30 October 2026

---

## Overview

Serenity ("the bot", "we", "our") is an open-source Discord bot that provides automatic slowmode management, audit logging, moderation tools and support tickets for Discord servers. This policy explains what data we collect, how we use it, and your rights regarding that data.

---

## What Data We Collect

### Data We Store

| Data | Purpose | Retention |
|---|---|---|
| Guild (server) IDs | Identify which servers have Serenity installed and store their configuration | Until deletion is requested via our support server |
| Channel IDs | Store slowmode and log channel configuration per server | Until deletion is requested via our support server |
| Message timestamps and counts | Calculate message rates to determine appropriate slowmode settings — **not message content** | Automatically deleted after 24 hours |
| User IDs | Used in moderation cases, warning records, and logging ignore lists | Until cleared by a server administrator via bot commands, or until deletion is requested via our support server |
| Moderation case data | Store the action type, reason, duration, and moderator/target user IDs for each moderation action. When the moderation module is on and Serenity can view the server's audit log, this includes bans, kicks and timeouts made outside Serenity (in the Discord app or by other bots), read from the audit log and marked as external, along with the name of the bot that acted | Until cleared by a server administrator, or until deletion is requested via our support server |
| Security settings | When the Security module is on: the user and role IDs a server trusts, and its alert limits. Anti-nuke counts who made destructive changes in memory only, for one minute, and never stores them | Until the server changes them, or until deletion is requested via our support server |
| Audit history | Only for servers that turn on the Audit history module, and only from then on: each audit log entry Discord sends (who acted, on what, the reason, and what changed), so it can be searched on the dashboard by people with Manage Server | The server's retention, 90 days by default (7 to 365), or until a server administrator clears it with `/audit-history clear` or deletion is requested via our support server |
| Warning records | Track active warnings per user per server | Until cleared by a server administrator via `/clearwarns`, or until deletion is requested via our support server |
| Log channel configuration | Store which Discord channels receive which categories of audit logs, and any configured ignore lists | Until deletion is requested via our support server |
| Historical message rate patterns | Aggregated hourly message-per-minute averages per channel used to detect anomalous activity — **not individual messages** | Automatically deleted after 30 days |
| Ticket records | For each support ticket: its number, type, opener, assigned staff, priority, stage, escalation tier, form answers, timestamps, close reason, who handled it, and a history of what happened to it | Until deletion is requested via our support server |
| Ticket transcripts | A copy of a closed ticket's messages (see [Tickets](#tickets)) | 30 days after the ticket closes by default; see [Tickets](#tickets) |
| Ticket staff notes | Private notes staff add to a ticket | Deleted with the ticket's transcript; if transcripts are off for that kind of ticket, until deletion is requested via our support server |
| Ticket ratings | The opener's 1–5 rating of a closed ticket, an optional comment, and which staff it counts for | Until deletion is requested via our support server |
| Linked-ticket answers | Answers to form questions a server marks as identifying (an in-game name, an order number), lowercased, so staff can see a member's related tickets | With the ticket's record |
| Ticket webhook delivery log | For each ticket event sent to a server's own webhook: the event name, attempts, response status, timing and any error — not the event's contents | Automatically deleted after 7 days |
| Dashboard sessions | When you log in to the dashboard with Discord: your user ID, username, avatar, and Discord login tokens (encrypted) | Deleted after 7 days without use, or when you log out |

### Data We Do Not Store

- **Message content outside tickets** — We never store the text of messages anywhere except ticket channels (see [Tickets](#tickets)). For logging, message content may be read from Discord's temporary cache at the moment a delete or edit event fires, and the content of a message Discord's AutoMod acts on is read when that action happens, in order to send a log embed to your server's configured log channel, but it is never written to our database.
- **Usernames, display names, or avatars outside ticket transcripts** — Everywhere else we store user IDs only.
- **Voice audio** — We only track voice state events (join/leave/move), never audio.
- **Direct messages** — The bot doesn't read DMs. Its only DMs are the close notice it sends a ticket's opener, with the transcript and a request to rate the ticket (see [Tickets](#tickets)).
- **Email addresses, IP addresses, or any personal contact information.** Dashboard login asks Discord only for your identity and server list, never your email.

---

## Tickets

Tickets are the one place Serenity stores message content. Servers turn them on with the Tickets module; if a server doesn't use tickets, none of this applies.

**What's stored.** While a ticket is open we store its record (see the table above) but not its messages. When it closes, unless the server turned transcripts off for that kind of ticket, we save a transcript: the ticket channel's messages (the first 10,000, which covers any normal ticket) with their text, embeds, timestamps, and a snapshot of the sender's name, avatar and role colour at that moment; attachment file names, sizes and links (the files themselves aren't copied, and Discord expires the links); the opener's form answers; what happened to the ticket (claimed, escalated, moved between stages, transferred, closed); and staff notes and staff-only fields, which are marked staff-only.

**Who's told.** Each ticket's first message says whether it's being recorded and for how long.

**Who can see it.** The opener receives a copy of the transcript by DM when the ticket closes, without staff notes, and can read it on the dashboard after logging in with Discord, even after leaving the server. Inside the server, the ticket's staff and server administrators can read transcripts, including staff notes, in Discord or on the dashboard. Transcripts never have public links: everyone has to log in, and anyone else is refused.

**How long it's kept.** Transcripts are kept for 30 days after the ticket closes by default. Server administrators can change that to anything from 7 to 365 days, or keep a kind of ticket's transcripts until they delete them. Expired transcripts are permanently deleted every night. A transcript that staff have marked as evidence for a moderation case is kept past its expiry until that hold is released. Reopening a ticket keeps its transcript until it closes again.

**Deletion.** Server administrators can delete any transcript with `/ticket transcript delete`. As the opener, you can ask for yours to be deleted with the button in the close DM, which notifies the server's staff, or by contacting us via our support server.

**Ratings.** When a ticket closes, the opener may be asked to rate it from 1 to 5, with an optional comment. Ratings are shown to the server's staff and used in its support statistics, credited to the staff who handled the ticket. Server administrators can turn ratings off for any kind of ticket.

**Webhooks.** A server's administrators can have ticket events (opened, claimed, closed and so on) sent to a web address they choose, such as their own helpdesk. Each event includes the ticket's number, status, stage, the opener's and staff's user IDs, timestamps, close reason and form answers (including fields only staff fill in), but never its messages or staff notes. Once sent, that copy is governed by whoever runs that address, not by this policy. Only HTTPS addresses on the public internet are allowed, and each event is signed so the receiver can check it came from Serenity.

**Backups.** Deleted transcripts can remain in our database backups for up to 30 more days before those backups are themselves deleted.

---

## How We Use Data

Data collected is used solely to operate and improve Serenity's features:

- **Guild and channel configuration** is used to know where to apply slowmode and where to send log embeds.
- **Message activity counts** are used to calculate message rates for the slowmode engine. Individual messages are never identified.
- **Moderation case and warning data** is used to power the `/case`, `/cases`, `/warnings`, and `/modstats` commands within your server.
- **Ticket records, transcripts and ratings** are used to run the server's support tickets: routing them to staff, showing what happened in a ticket, giving the opener a copy when it closes, and the server's own support statistics.
- **Dashboard sessions** are used to keep you logged in to the dashboard and check which servers you can manage there.
- **Ignore lists** are used to exclude specific channels or users from audit log embeds.
- **Aggregated usage metrics** (how many logs of each type are posted per server, how often each command is used) are used to see which features are used and improve the bot. They cannot identify any individual user.

We do not use any data for advertising, analytics sold to third parties, or any purpose beyond operating and improving the bot.

---

## Data Sharing

We do not sell or share your data with any third parties for commercial purposes.

Audit log embeds (member joins, message edits, moderation actions, etc.) are sent to Discord channels that you configure within your own server. Those messages are governed by Discord's own [Privacy Policy](https://discord.com/privacy).

If a server's administrators set up ticket webhooks, the ticket events they choose are sent to the address they configure (see [Tickets](#tickets)).

Guild IDs and channel IDs are transmitted to Grafana Cloud as metric labels for performance monitoring purposes. See the **Third-Party Services** section below for details.

---

## Third-Party Services

### Website and dashboard
The website at serenitybot.app uses only the cookies it needs to log you in; there are no analytics, advertising or tracking cookies. See the [Cookie Policy](/cookies). The site loads its fonts from Google Fonts, so your browser's IP address and user agent reach Google when a page loads ([Google's Privacy Policy](https://policies.google.com/privacy)); Google Fonts sets no cookies. Avatars and server icons are loaded from Discord's image servers.

### Discord API
Serenity communicates exclusively with Discord to receive events and send responses. Discord's Privacy Policy can be found at [discord.com/privacy](https://discord.com/privacy).

### Prometheus and Grafana Cloud
Serenity collects internal performance metrics using the `prometheus_client` library and exposes them via a protected `/metrics` endpoint. These metrics are scraped by Grafana Cloud and used solely for monitoring the bot's operational health and performance, and for understanding which features are used so the bot can be improved.

**What is included in metrics data:**
- Guild IDs and channel IDs, used as labels on aggregated counters and gauges (e.g., messages processed per guild, current slowmode per channel)
- Counts of audit-log embeds posted per guild, by event type (e.g., message edits and deletes, member joins and updates, role, channel, emoji and server updates, moderation actions). These record only that an event of that type was logged in that guild — never who was involved or what changed
- Counts of slash command uses, by command name only (no guild, channel, or user)
- Aggregated numerical values such as message processing counts, slowmode change frequency, engine calculation durations, and task execution times

**What is not included in metrics data:**
- Message content
- Usernames, display names, or any personally identifiable information
- User IDs

The metrics endpoint is protected with HTTP Basic Auth and is not publicly accessible. Grafana Cloud's Privacy Policy can be found at [grafana.com/legal/privacy-policy](https://grafana.com/legal/privacy-policy).

---

## Data Storage and Security

Serenity's primary data store is a PostgreSQL database on a self-hosted server. We take reasonable precautions to secure our infrastructure, but no system is perfectly secure. The bot is open source and its data handling can be inspected in full at [https://github.com/patelheet30/Serenity](https://github.com/patelheet30/Serenity).

---

## Your Rights

### Server Administrators

- **Request data deletion**: To request full deletion of your server's data, contact us via our support server. We will action all requests within a reasonable timeframe.
- **Manage moderation records**: Moderation cases and warnings can be managed through the bot's own commands (`/clearwarns`, `/case-edit`).
- **Manage log configuration**: Logging configuration can be updated or removed at any time via `/logging` commands.
- **Manage tickets**: Transcript retention can be set with `/ticket settings retention` or on the dashboard (and per ticket type), transcripts deleted with `/ticket transcript delete` or on the dashboard, and transcripts, ratings and webhooks turned off per ticket type or removed.

### Individual Users

- **Opt out of logging**: Server administrators can add you to a logging ignore list via `/logging ignore-user`. This will exclude your actions from audit log embeds in that server.
- **Ticket transcripts**: You'll get a copy of your ticket's transcript when it closes, can read it on the dashboard, and can ask for it to be deleted with the button in that message or on the dashboard.
- **Request data deletion**: You may contact us via our support server to request that any records containing your user ID (moderation cases, warnings, tickets) be removed. Note that moderation records belong to the server they were issued in, so we may require consent from the relevant server administrator before removing them.

---

## Data Retention

- **Message activity records** are automatically deleted after **24 hours**.
- **Historical pattern data** is automatically deleted after **30 days**.
- **Ticket webhook delivery logs** are automatically deleted after **7 days**.
- **Dashboard sessions** are deleted after **7 days** without use.
- **Ticket transcripts** are deleted **30 days** after a ticket closes by default (7–365 days, or until deleted, as the server chooses), unless held as evidence for a moderation case.
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
