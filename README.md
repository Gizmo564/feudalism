# Feudalism and Population – game engine (code package is named `population`) (no Discord code yet)

- `rules/population.json` holds every game number. To change the rules, edit a copy and pass it to `simulate.py --rules`.
- `population/` is the engine: `world.py` (state, map), `tick.py` (orders and the nightly tick), `combat.py`, `trade.py`, `victory.py`, `views.py` (fog), `bot.py` (test players).
- Run tests: `python3 -m pytest -q`
- Balance check: `python3 simulate.py --players 16 --radius 8 --seeds 5`

All numbers in the ruleset are placeholders to be tuned.

## Game modes
- `rules/population.json` – the simple mode (no titles).
- `rules/feudalism.json` – titles by land share (Lord to Emperor), Emperor at 40% land wins, feudal wording in `terms`.
Run a mode with `python3 simulate.py --rules rules/feudalism.json`.

## Running the Discord bot
1. Developer Portal -> New Application. On the **Bot** page, Reset Token and copy it (keep it secret, never commit it). No privileged intents are needed.
2. **Installation** page: enable Guild Install, add scopes `bot` and `applications.commands`, and permissions Send Messages, Attach Files, Manage Roles. Open the install link and add the bot to your server.
3. In Discord, put the bot's role **above** the title roles (Lord ... Emperor) so it can assign them. Create a role named `admin` for admins (or change ADMIN_ROLE).
4. `cp .env.example .env`, paste the token, and set GUILD_ID to your server's ID (commands then appear instantly).
5. `pip install -r requirements.txt` then `python3 discord_bot.py`. On Ubuntu use `feudalism-bot.service` to keep it running.
6. In Discord: `/admin open` (pick a mode), players `/join`, then `/admin start`. Use `/admin tick` to advance by hand. For the playtest set TICK_MINUTES=5 in `.env`.

The bot connects out to Discord, so you do not need a public URL or ngrok (that is only for Discord's webhook-style example app).

## Player commands
- `/zoom` – interactive map: pan, zoom, pick a tile, then Claim / Build / Send army. A layers menu toggles terrain icons, buildings and housing numbers.
- `/info` – your nation in four tabs (Overview, Economy, Land, Military) with alerts at the top; change work priorities with the buttons on the Economy tab.
- `/report` – what happened to you last night. `/status`, `/standings`, `/map` – quick views.
- `/help` – how to play in five short pages. `/cancel` – clear tonight's orders.
- `/claim`, `/build`, `/train`, `/move`, `/priorities`, `/trade`, `/accept`, `/decline` – typed versions of orders. Orders are checked when you give them, with a message saying what to do instead.
- `/tutorial start|night|hint|stop` – a private practice game against two simple rivals. While it is on, every command works on the practice realm.

## Admin extras
- `/admin gamelog #channel` – post each night's public events there as a story (leave the channel empty to turn it off).
- Players get a private message when the game starts, and each night's `/report` as a DM. Set `DM_REPORTS=0` in `.env` to turn the DMs off (people with DMs closed are skipped quietly).
