# Cookie Policy

**Serenity Discord Bot**  
Last updated: 29 October 2026

---

## Overview

The website at serenitybot.app only uses cookies that are strictly necessary to log you in to the dashboard. There are no analytics, advertising or tracking cookies, and nothing is shared with advertisers. Because every cookie here is strictly necessary, the site doesn't ask for consent; it tells you what it uses instead.

If you never log in, the website sets no cookies at all.

---

## Cookies We Set

| Cookie | Purpose | Lasts |
|---|---|---|
| `serenity_session` | Keeps you logged in to the dashboard. It holds a random token; only a hash of it is stored on our side, linked to your session (see the [Privacy Policy](/privacy)). | 7 days, or until you log out |
| `serenity_oauth_state` | Protects the Discord login from forged requests while you log in. | 10 minutes |
| `serenity_oauth_consent` | Remembers that Discord's consent screen was already shown during this login, so it isn't shown in a loop. | 10 minutes |
| `serenity_oauth_next` | Remembers which transcript to take you back to after logging in. | 10 minutes |

All of them are first-party, `HttpOnly` (scripts on the page can't read them), `Secure` (sent over HTTPS only) and `SameSite=Lax`.

---

## Storage in Your Browser

The site saves one setting in your browser's local storage: that you've dismissed the cookie notice, so it isn't shown again. It never leaves your browser.

---

## Other Websites

- **Discord**: logging in sends you to discord.com, which sets its own cookies under [Discord's policies](https://discord.com/privacy). Avatars and server icons are loaded from Discord's image servers.
- **Google Fonts**: the site's fonts are loaded from Google Fonts, which sets no cookies but receives your browser's IP address and user agent ([Google's Privacy Policy](https://policies.google.com/privacy)).

---

## Managing Cookies

You can delete or block cookies in your browser's settings. Blocking them stops you from logging in to the dashboard; the rest of the website works without them. Logging out deletes the session cookie and ends the session on our side.

---

## Contact

Questions about this policy: the [Serenity Support Server](https://discord.gg/GSHQdQNszP) or [GitHub](https://github.com/patelheet30/Serenity).
