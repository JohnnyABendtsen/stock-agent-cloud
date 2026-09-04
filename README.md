# Stock Analyzer — Cloud (multi-user web version)

This is a **copy** of the desktop app's data/scoring logic, built for shared web
access. The original desktop app (`Stock agent/`) is untouched — nothing here
modifies it.

- `data/`, `scoring/`, `config.py` — copied as-is from the desktop app.
- `display_format.py` — the desktop table's column list + formatting rules,
  copied without the PySide6/Qt parts (Streamlit Cloud doesn't need Qt).
- `update_data.py` — the same fetch → score → save pipeline as the desktop
  app's "Update data" button, as a plain script (no GUI). Run by GitHub Actions
  on a schedule so no one's PC needs to be on.
- `app.py` — the Streamlit web dashboard everyone visits in a browser.
- `.github/workflows/daily_update.yml` — runs `update_data.py` daily at 05:00 UTC,
  then commits the refreshed `data/stocks.db` back into the repo.

## What's left to do (needs your GitHub login — I can't do this part)

1. **Authenticate the GitHub CLI** (one-time):
   ```bash
   gh auth login
   ```
   Follow the prompts (GitHub.com → HTTPS → login via browser).

2. **Create the repo and push** — from this folder:
   ```bash
   gh repo create stock-agent-cloud --private --source=. --remote=origin --push
   ```
   (Drop `--private` if you're OK with a public repo — that also gives GitHub
   Actions *unlimited* free minutes instead of the 2,000/month private-repo cap;
   see the cost discussion earlier in the session for the trade-off.)

3. **Deploy the dashboard** on [share.streamlit.io](https://share.streamlit.io):
   - Sign in with the same GitHub account.
   - "New app" → pick the `stock-agent-cloud` repo, branch `master`, file `app.py`.
   - Deploy. You'll get a public URL anyone can open.

4. **Turn on the scheduled update**: it's already in the repo
   (`.github/workflows/daily_update.yml`) — it activates automatically once the
   repo is pushed to GitHub. You can also trigger it manually right away from
   the repo's **Actions** tab ("Run workflow") instead of waiting for 05:00 UTC.

## Cost recap

| Piece | Cost |
|---|---|
| GitHub Actions (private repo, daily run) | Free up to ~2,000 min/month; a full Europe run has taken up to ~120 min, so daily use is close to the cap — see options discussed earlier (every-other-day, public repo, or ~$13/month for extra minutes) |
| GitHub Actions (public repo) | Unlimited, free |
| Streamlit Community Cloud | Free, but the URL is open to anyone who has it (no login wall) |

## Keeping the two copies in sync

If you fix a bug in the desktop app's `data/`, `scoring/`, or `config.py`, the
same fix needs to be copied here by hand (or re-run the copy step) — this folder
is a snapshot, not a live link.
