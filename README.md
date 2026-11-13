# Feudalism and Population – game engine (code package is named `population`) (no Discord code yet)

- `rules/population.json` holds every game number. To change the rules, edit a copy and pass it to `simulate.py --rules`.
- `population/` is the engine: `world.py` (state, map), `tick.py` (orders and the nightly tick), `combat.py`, `trade.py`, `victory.py`, `views.py` (fog), `bot.py` (test players).
- Run tests: `python3 -m pytest -q`
- Balance check: `python3 simulate.py --players 16 --radius 8 --seeds 5`; building pacing: `python3 pacing.py 12 8`

All numbers in the ruleset are placeholders to be tuned.

## Game modes
- `rules/population.json` – the first, basic mode, with a tribal feel (tribe, chief, warrior, villager): raw resources only (food, wood, stone, ore, oil, livestock), no workshops, ports or steel soldiers. Win on population or stockpile share.
- `rules/feudalism.json` – the developed mode: titles by land share (Lord to Emperor), Emperor at 40% land wins, feudal wording in `terms`, plus refined goods (tools, steel, provisions, bricks), workshops, ports and steel soldiers. Industrialism builds on this.
- `rules/industrialism.json` – a third mode that starts in the industrial age. Coal (hills, mountains) and oil (rare deposits) are fuel with different power (1 and 3); workshops burn it each night. Goods chain: wood/stone/ore/coal refine into tools, bricks, provisions, coke, steel, planks, cement, then factories make machine parts and engine works make engines. Besides land, population and stock shares, you can win with 35% of all factory output (machine parts and engines made in a night) for 5 nights. `/autorefine on|off` (on by default here) keeps workshops busy whenever their inputs are in store. The Activity has an Industrial theme chip in these games. **Rails** (planks + steel, flat price) let your armies cover up to 6 tiles a night along your own connected track, free; an enemy who captures a tile wrecks its rail. **Steamships**: boat trips over ports burn 2 coal instead of wood. **Trade by train**: when a trade is accepted and the sender has an idle train at their capital with a rail line (any nations' rails that join) to the other capital, the train carries the goods (40 per train unit) and delivers them, then drives home; the rest, or any trade without a train, goes by caravan, which is slower. **Special units** (`/train` with `unit`): tanks (engine works; x4 strength, 3x oil), artillery (factory; x2.5, ignores walls and the capital bonus) and trains (engine works; x3, move up to 12 tiles a night, only along connected rails of any nation, never capturing them). **Trains also carry armies**: `/board` puts soldiers, tanks or guns on a train on the same tile (10 seats per train unit; a tank or gun takes 5), `/disembark` unloads them. A train with an army aboard is stopped at the border: it cannot enter another nation's land (cargo-only trains pass). Industrialism's game over screen uses the beam art. Not built: air units.
Run a mode with `python3 simulate.py --rules rules/feudalism.json`. For Industrialism add `--industry 1` (share of bots that build the factory chain, e.g. `python3 simulate.py --rules rules/industrialism.json --industry 1 --players 8 --radius 6 --ticks 300`); the report then shows factories and factory output. The bots are crude (they reach a factory in some seeds, not all), so treat it as a smoke test, not a balance verdict.

## Running the Discord bot
1. Developer Portal -> New Application. On the **Bot** page, Reset Token and copy it (keep it secret, never commit it). No privileged intents are needed.
2. **Installation** page: enable Guild Install, add scopes `bot` and `applications.commands`, and permissions Send Messages, Attach Files, Manage Roles (add Manage Messages and Read Message History to use `/admin wipe`). Open the install link and add the bot to your server.
3. In Discord, put the bot's role **above** the title roles (Lord ... Emperor) so it can assign them. Create a role named `admin` for admins (or change ADMIN_ROLE).
4. `cp .env.example .env` and paste the token. Leave GUILD_ID blank so any server can use the bot (set it only to limit the commands to one test server).
5. `pip install -r requirements.txt` then `python3 discord_bot.py`. On Ubuntu use `feudalism-bot.service` to keep it running.
6. In Discord: `/admin open` (pick a mode; the first time in a server this also creates that server's game), players `/join`, then `/admin start`. Use `/admin tick` to advance by hand. For the playtest set TICK_MINUTES=5 in `.env`.

The bot connects out to Discord, so you do not need a public URL or ngrok (that is only for Discord's webhook-style example app).

## Player commands
- `/zoom` – interactive map: pan, zoom, pick a tile, then Claim / Build / Send army. A layers menu toggles terrain icons, buildings and housing numbers.
- `/info` – your nation in four tabs (Overview, Economy, Land, Military) with alerts at the top; change work priorities with the buttons on the Economy tab.
- `/report` – what happened to you last night. `/status`, `/standings`, `/map` – quick views.
- `/help` – how to play in seven short pages. `/cancel` – clear tonight's orders.
- `/claim`, `/build`, `/upgrade`, `/train`, `/move`, `/priorities`, `/trade`, `/offers`, `/accept`, `/decline` – typed versions of orders. `/trade` with no `to` posts an open offer on the trade board (Accept/Decline buttons; `/admin tradeboard` picks the channel, else announcements).
- `/pact propose|accept|decline|break|list` – promises between nations, kept as a public record and never enforced. Orders are checked when you give them, with a message saying what to do instead.
- `/tutorial start|night|hint|stop` – a private practice game against two simple rivals. While it is on, every command works on the practice realm.

## Admin extras
- `/admin rules` – a private panel with tabs. **Game**: fog of war, hidden oil, win share, hours between ticks, workers per tile. **Land**: what a claim costs. By default a claim costs a share of the wood (20%) and stone (10%) you hold, plus 1% for each tile you own, never less than 3 wood and 1 stone), so claims get cheap when your stock is small: 20 starting stone alone pays for about 13 claims, enough to walk to the nearest hills; set the share to 0 for the old fixed price. The land price rise as the map fills is here too. **Trade**: market needed, caravans, trade speed, offer expiry. **Start**: starting workers, soldiers and stock of each resource (these only affect nations created afterwards; a button on that tab sets everyone's current stock back to the start amounts, after a second press to confirm). A tab shows how many changes are waiting. Numbers can be stepped with -/+ or typed with "Type a number". Changes wait for the next tick and are announced. `/admin resetrules` puts every rule back to the mode's defaults. (Replaces the old `/admin fog`, `hiddenoil` and `empire`.)
- Caravans (off by default; `/admin rules` > Caravans): traded resources walk from one capital to the other over land at the trade speed (default 2 tiles a tick). An army of a third nation that stands on a caravan's tile captures its goods. Both traders always see their caravans and the cargo; others see them only on tiles they can see, without the cargo. With the switch off, goods arrive after a safe delay, as before. A trade with resources needs a land route between the two capitals while caravans are on.
- Sign-ups: `/admin open` starts a fresh list; `/admin close` stops joins (names kept); `/admin reopen` lets people join again; `/admin clear` wipes the names and leaves sign-ups closed. `/admin start` works whether open or closed.
- `/admin errorlog` – the last 10 errors the bot hit (command, who, what, and where in the code). Errors also go to `feudalism logs`.
- `/admin gamelog #channel` – post each night's public events there as a story (leave the channel empty to turn it off).
- Private messages are opt-in: nobody is DMed until they run `/dms on` (or press the button on the Activity's Report tab). Then they get the start message, each night's report and private trade offers. Admins can't switch it on for anyone. `DM_REPORTS=0` in `.env` (or `/admin settings`) is a master switch that stops all report DMs.

## Installing on a server (Ubuntu)

```
git clone https://github.com/Gizmo564/feudalism.git ~/feudalism && ~/feudalism/install.sh
```

The repo is private, so when git asks for a password, paste a GitHub token with read access. The script installs packages, sets up a virtual environment, asks for your bot token once, runs the tests, and installs a systemd service. Update later with `feudalism-update`, which pulls from GitHub, retests, and restarts. `.env` and the `games/` folder stay on the machine and are never committed.

**Automatic updates (optional):** `~/feudalism/install.sh --auto` adds a timer that checks GitHub every 15 minutes. When there is a new commit it first waits for a quiet moment (nobody has used the bot for `AUTO_UPDATE_QUIET_MINUTES`, default 10, and no tick ran or is due within 10 minutes; it just checks again 15 minutes later), then pulls, runs the tests, and restarts the bot. `feudalism update` by hand never waits. If the tests fail it puts the old version back and does not retry that commit. Turn it off with `install.sh --no-auto`. Logs: `journalctl -u feudalism-autoupdate`.

**Many servers:** the bot runs one separate game per Discord server, all in one process. A server's first `/admin open` creates its game (saved as `games/<server id>.db`); anyone with that server's admin role can run it. `MAX_GAMES` (default 20) limits how many games exist. In my test, 20 games of 30 players needed about 70 MB of memory and about 4 MB of disk each per 60 nights, and no single tick took more than a tenth of a second of game logic; Discord posting at tick time is not included, so watch `feudalism games`. Each server's admins can change their tick time, timezone, admin role and DM reports with `/admin settings` (the `.env` values are the defaults). DM night reports start with the server's name, so a player in several games can tell them apart. A game's file can be deleted (with the bot stopped) to free its slot; the old single `game.db` is no longer used.

**Phone notifications (optional):** the bot can send messages to your phone through [ntfy](https://ntfy.sh): an automatic update (or one that failed its tests and was rolled back), the bot coming online, the bot stopping unexpectedly, and a failed tick. Install the ntfy app, subscribe to a hard-to-guess topic name, and put it in `.env` as `NTFY_TOPIC=your-topic` (anyone who knows the topic name can read the messages, so don't use a guessable one). Blank means off. `NTFY_SERVER` (default `https://ntfy.sh`) can point at your own ntfy server, and `NTFY_NAME` (default Crownfall) is the title prefix. Then run `feudalism update` once so the service picks up the crash alert. Test it with `.venv/bin/python -m population.notify "Test" "Hello"`.

## The `feudalism` command

On the server, `feudalism help` lists everything:

- `feudalism status | start | stop | restart | logs [-f]` – look after the bot.
- `feudalism update`, `feudalism auto on|off` – get the latest code, or turn automatic updates on or off.
- `feudalism try <branch>` – switch the box to a test branch from GitHub and restart; `feudalism try main` goes back. (Automatic updates then follow that branch.)
- `feudalism env` – edit the settings file (`.env`) and offer to restart.
- `feudalism backup [folder]` – save every game (default `~/feudalism-backups`, newest 30 kept; safe while the bot runs).
- `feudalism backup -n <name>` – same, but saved as `<name>.db`. Named backups are never deleted automatically. Handy before a risky change: `feudalism backup -n before-war`.
- `feudalism backups` (or `listbkp`) – list backups (each is a folder holding one file per game). `feudalism backup rm <name>` deletes one.
- `feudalism restore <name> [server id]` – put a backup back: every game in it, or only one server's. It asks first, saves the current games as a backup, stops the bot, restores, and starts it again.
- `feudalism games` – how many games and servers, each game's size and last tick time, and whether the box is getting too busy. The bot owner can see the same in Discord with `/admin botstats`.

`feudalism-update` and `feudalism-backup` still work too.


## Map, prices and upgrades (Crownfall update)
- **Map size** scales with the players: `/admin start` with no radius uses the people joined, or `expected_players` if you give it (4 players: radius 5, 8: 8, 12: 9, 16: 11, 24: 13). Give a radius to override.
- **Map shape**: water first (rivers and lakes), then mountain ranges, then big patches of field, forest and hills, with a repair step so every nation can reach each resource within a few claims. Big biomes mean someone often lacks something and has a reason to trade.
- **Prices rise**: each building of a kind you already own makes the next of that kind 15% dearer (`/admin rules`, "Building price rise").
- **Upgrades**: `/upgrade` (or the tile card) makes a home hold more workers or a barracks more soldiers, up to level 3. The map shows a dot per upgrade.
- **Activity**: besides the map it now has Nation (people, work, priorities, training), Trade (offers, private or on the board), Pacts and Report tabs. Every command still works too.
- **Basic and advanced mode** (each player chooses with `/mode`; basic is the default): basic uses the 0-5 priorities. Advanced adds `/assign` (an exact number of workers on a resource, like `food 8, wood 4`; resources without a count still follow the priorities) and `/move` with a count to send part of an army. The Nation tab in the Activity does the same with steppers. Admins can switch advanced off for the server with `/admin settings advanced_mode 0`, and `/admin rules` hides the less-used rules until "Show advanced rules" is pressed. Both modes are explained on the `/help` "modes" page.
- **Refined goods** (Feudalism mode): Toolworks (wood + stone = tools), Forge (ore + oil = steel), Cookhouse (livestock + food = provisions) and Kiln (stone + oil = bricks). A workshop has 3 job slots and does nothing until workers staff it (priorities or `/assign`; a new one starts on priority 2). One worker turns one of each input into one good a night, never more than the store allows. Tools gate home upgrades, steel barracks upgrades, bricks the level 3 upgrades; provisions speed up peasant growth. Rules: `refine`, `refine_slots` in `rules/population.json`. The `/help` "goods" page explains it.
- **Ports**: built on a water tile next to your land or another of your ports, needs a market (cost: wood, stone, tools, bricks). Armies walk over ports, and claims can grow across them, so one water tile is bridged by one port and wider water needs a chain. A route over ports costs a flat `port.boat_wood` per move order. A captured port is wrecked. Trade shipments already ignore water unless caravans are on.
- **Map tuning**: `PATCH_PERCENT` (bigger biomes when lower) and `REACH` in `population/world.py`; forest and hills are rarer in the terrain weights, so wood and stone are the things you trade for.
- **Contested claims**: if two nations claim the same free tile in one night, nobody pays and the tile becomes contested. Each can give it up or fight (buttons on the announcement, `/contest`, or the Nation tab in `/play`) until the next night starts. If everyone gives up it is free again; one left gets it; two or more roll a duel with the soldiers they have within 3 tiles (the loser loses some). No answer counts as fight. Other claims on that tile are refused meanwhile.
- **Steel soldiers**: on by default (`/admin rules`, "Steel soldiers"). Need a barracks at level 2 and steel; they fight 25% stronger, march as their own armies and are drawn as diamonds (a double outline on the Discord map). `/train count steel:True`, or the Train box in the Activity.
- **Trade across water** (`/admin rules`, Trade tab): 0 = water doesn't matter (default), 1 = a port on one side, 2 = ports on both sides. Only applies when water separates the capitals.
- **Workshop upgrades**: `/upgrade` adds job slots (3, then 4, then 5).
- **Lists that stay put**: the `/info` Economy tab shows every resource every time in one fixed-width table (priority, workers, about how much tonight), and its controls keep one width. The Activity lists use fixed columns.
- **Sea trade speed** (`/admin rules`, Trade tab, default 150%): goods whose route between the capitals crosses water over ports travel 1.5 times as fast (3 tiles a night instead of 2). It needs a route over ports, so with trade-across-water set to 0 and no ports there is no bonus.
- **Activity polish**: refined goods and workshop counts only show once you have some, the Nation tab gets a red dot and a "Contested tiles" box when a tile of yours is contested (contested tiles also get a dashed red outline on the map), the people meters turn amber when you hit your housing, and rows and buttons have hover and focus states.
- **Paper theme**: a "Paper" button on the Activity map switches the whole page to an old parchment-map look (remembered per browser).
- **Training limits**: `/train` queues only as many as you can afford right now (counting trainings already queued tonight), and if something changes by night the report says which limit stopped it. Nothing carries over to later nights, so there is no hidden debt.
- **Fast armies on your own land**: armies move 2 tiles a night while crossing tiles you own and stop after the first tile that is not yours (`/admin rules`, Game tab, "Army steps a night on own land"; 1 turns it off).
- **Capital capture** (`/admin rules`, Game tab, on by default): capturing a capital takes all the rest of that nation's land and eliminates it. Its buildings are lost, as with any captured tile, and ports are wrecked.
- **Game over screen**: when a game ends the Activity shows a crown, who won and why, and the final standings for every nation. A nation that has fallen sees its own "Your nation has fallen" screen with the slipped crown.
- **Queued orders**: `/orders` lists what you have queued for tonight, `/cancel <number>` cancels one, and `/cancel` alone clears all. In the Activity every queued order has its own Cancel button.
- **Factory reset**: `/admin factoryreset` erases the server's game, sign-ups, rules and settings. With `clear_messages` on it also deletes the bot's own messages in the announcement, trade and log channels (pinned ones stay).
- **Pact DMs**: when a pact is proposed to you, or one you proposed is accepted, declined or withdrawn, you get a DM right away (only if you turned DMs on with `/dms on`).
- **Your night message**: under the nightly announcement there is a "My night" button. It shows you, privately, your night report, pact proposals, open trade offers and contested tiles, each with its own Accept/Decline (or Give up/Fight) buttons. Players with DMs on (`/dms on`) get the same message by DM right after the announcement.
- **Updates tab** in the Activity: what needs your answer (with the same buttons) and your last 7 nights' reports. The tab shows a dot while something is waiting.
- Help, the welcome message and the tutorial now suggest turning on /dms (night report, pact proposals and trade offers by DM, with buttons).
- **First-launch intro** in the Activity: a short card on what the game is and how to win (from the live rules), shown once per browser with a "Got it" button.
