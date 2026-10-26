# FREEDOM SMP website (v2)

Responsive Flask + SQLite website for the FREEDOM SMP Minecraft Java server.

## What's inside

**For players**
- Home page with live server status, launch countdown, 3-step join guide, updates, vote links and ranks
- Pages: How to join, Rules (editable in admin), FAQ, Support center, Privacy notice
- Accounts with sign up / log in, password strength meter, show-password buttons, "keep me logged in", and password change
- Player dashboard: whitelist request (with cancel), staff notes, rank orders, support tickets with staff replies
- "Staff reply within 24-48 hours" notes wherever a request or ticket is sent (wording editable in admin)
- Mobile-first layout, works from small phones up

**For admins** (`/admin`)
- Overview with counters and live status
- Whitelist queue with search, status filters, staff notes and a copy-ready `whitelist add` command list
- Rank orders with PolygonScan link, notes, and CSV export
- Support tickets with replies (players see them on their dashboard)
- Player management: search, suspend/restore, make/remove admin, temporary-password reset
- Announcements (create, edit, pin, delete), ranks (edit, hide, delete), vote links (hide, delete)
- Settings: address, version, status mode, launch date/time, hide address until launch, Discord/YouTube/countdown links, support email, response-time text, site-wide banner, open/close sign-ups and whitelist, wallet address, rules text
- Activity log of admin actions

## Look and feel (v4)
Clean, modern dark theme with an emerald accent: Sora headings, Manrope body text, glass header, rounded cards, smooth buttons and a mobile-first layout. A Home link sits in the menu and every inner page has a "Back to home" button. The Minecraft flavour comes from the artwork: a server-list style status bar, block icons and a pixel-art gallery with a lightbox.

The pictures in `static/img/` (`world-day`, `world-night`, `world-caves`, `world-crew`, plus block icons) are original pixel artwork made for this site, not Mojang assets. To use your own in-game screenshots, save them as `.webp` with the same file names (about 1600x880 is ideal) and they replace the artwork everywhere. Edit captions in `templates/index.html` (search for `data-lightbox`). Colours live at the top of `static/mc.css`.

## Launch mode
`launch_at` defaults to **30 Oct 2026, 10:59 PM IST**. Until then the public site shows a countdown and hides the server address ("Revealed at launch"). At launch time the address appears automatically. Admins always see it. Change or turn this off in Admin > Settings.

## Live status
`/api/server-status` pings the Java server directly (Minecraft Server List Ping). If your host blocks outgoing sockets it falls back to the public mcsrvstat.us API. Results are cached for 30 seconds and the page refreshes every 60 seconds. Status mode "Automatic" is recommended; you can force Maintenance/Online/Offline.

## Run locally (Windows)
1. Install Python 3.10+ (tick "Add Python to PATH").
2. Open a terminal in this folder and run:
   ```
   py -m venv .venv
   .venv\Scripts\activate
   python -m pip install -r requirements.txt
   ```
3. Set the admin variables (see `.env.example`), then start:
   ```
   set ADMIN_USERNAME=your-admin-name
   set ADMIN_PASSWORD=use-a-strong-password-here
   set ADMIN_EMAIL=you@example.com
   python app.py
   ```
4. Open http://127.0.0.1:5000 and log in. Admin panel: `/admin`.

The included `freedom_smp.db` already contains your existing accounts; new tables/columns are added automatically on start. Your old admin password keeps working unless you set `ADMIN_PASSWORD`, which resets it every start.

## Security notes
- `SECRET_KEY`: set it in your host's environment. If you don't, a random key is generated once and stored in `.secret_key` (keep that file private and never commit it).
- Login/sign-up/ticket/order actions are rate limited; the same payment hash can't be submitted twice; redirects after login stay on-site.
- For production use HTTPS and set `COOKIE_SECURE=1`; behind a proxy set `TRUST_PROXY=1`. Use Gunicorn/Waitress, not `python app.py`. Back up `freedom_smp.db` and keep it on persistent storage.
- `freedom_smp.db` contains player emails and password hashes. Don't publish it or commit it to a public repo.

## Things still manual
- Approving a whitelist request does not change the Minecraft whitelist. Use the copy-ready commands in Admin > Whitelist.
- Approving a rank order does not grant the in-game rank. Verify the payment on PolygonScan, then grant it via your permissions plugin.
- Auto-deploy test
- There's no email sending, so password resets go through tickets and the temporary-password button.
