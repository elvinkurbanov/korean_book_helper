# korean Book bot

A Telegram bot for the korean Book group. It asks customers 5 questions (name, age,
country, Korean level, aim), recommends Korean books from your catalog, and saves everything.

## Files

| File | What it is |
|---|---|
| `bot.py` | The bot: conversation, buttons, admin commands |
| `db.py` | Database functions (SQLite) |
| `books.csv` | Starting catalog, loaded on the first run only. **Sample data, so replace it with your real books** |
| `korean_books.db` | Created automatically. Holds books, customers, recommendations, requests |

## Setup (one time)

1. In Telegram, open **@BotFather** → `/newbot` → copy the token.
2. Install the library:
   ```
   pip install -r requirements.txt
   ```
3. Run the bot once and send it `/myid` to learn your Telegram ID. Then stop it (Ctrl+C).
4. Start it with your token and admin ID(s):

   Mac/Linux
   ```
   export BOT_TOKEN="123456:ABC..."
   export ADMIN_IDS="111111111,222222222"
   python bot.py
   ```
   Windows (PowerShell)
   ```
   $env:BOT_TOKEN="123456:ABC..."
   $env:ADMIN_IDS="111111111,222222222"
   python bot.py
   ```
5. Every admin must press **Start** in the bot once; otherwise Telegram won't let the
   bot message them about new book requests.

## Connecting it to the group

The questions are asked in a **private chat** with the bot (age and nationality
shouldn't be posted in a group). In @korean_book, pin a message like:

> 📚 Not sure which book to buy? Our bot will recommend one: https://t.me/YOUR_BOT_USERNAME

If someone types `/start` in the group, the bot replies with that private link.

## Editing the book catalog

**Option A: Excel (best for many books)**
1. Send `/exportbooks` to the bot → you get `books.csv`.
2. Open it in Excel or Google Sheets and edit it. Leave `id` empty for new books.
3. Save as **CSV UTF-8** and send the file back to the bot. The catalog is replaced,
   and the old one is backed up in `backups/`. If a row has a mistake, nothing changes
   and the bot tells you which line is wrong.

**Option B: commands (quick changes)**
```
/books                       list books with IDs
/addbook Title | Author | level | aims | price | description
/stock 5 0                   book 5 out of stock (1 = back in stock)
/delbook 5                   delete book 5
```

**Allowed values**
- `level`: `beginner`, `intermediate`, `advanced`, `all`
- `aims` (comma-separated): `topik`, `conversation`, `work`, `university`, `culture`, `all`

## Adding PDF and audio files to a book

1. Find the book's ID with `/books`.
2. Send the file to the bot in a private chat, with **the book ID as the caption**
   (for example `5`). This works for:
   - PDF files
   - audio files (mp3, m4a, wav, ...), sent as music or as a file
   - a .zip with all audio tracks
3. You can attach several files to one book. Check them with `/files 5`;
   remove them all with `/delfiles 5` and upload again to replace them.

When a customer taps **📩 I want this book**, the bot immediately sends that book's
PDF first, then its audio. Books with files show "📄 PDF · 🎧 Audio included" in the
recommendation, and 📄🎧 in `/books`. If a book has no files, the customer is told
you'll contact them, and you get a notification as before.

The bot doesn't store the files on your computer. Telegram keeps them, and the bot
only saves each file's Telegram ID, so even large files are sent instantly.
Check that your licence covers digital distribution through Telegram, and use
`/report` to keep a record of what you've distributed.

## Licence tracking

Every copy the bot sends is logged with the date, the book and the customer.

- `/report` → copies sent per book (all time), plus two CSV files:
  - `licence_report_all.csv`: book, copies, number of different customers, first/last date
  - `deliveries_all.csv`: one row per copy sent (date, book, customer name, username, country, age)
- `/report 2026-11` → the same for one month (useful for monthly licence statements)
- `/export` also includes `deliveries.csv`

"Copies" counts every send; "customers" counts different people. If the same person
requests a book twice, that's 2 copies and 1 customer.

**Stop forwarding (optional):** start the bot with `PROTECT_FILES=1` and customers
can't forward the PDF/audio to others or save them from Telegram. They can still
read and listen inside Telegram. Leave it off if customers need to download the
files to their own devices.

```
export PROTECT_FILES=1        # Mac/Linux
$env:PROTECT_FILES="1"        # Windows PowerShell
```

## How recommendations work

The bot shows up to 5 in-stock books for the customer's level (or level `all`).
Books whose aims match the customer's aim are shown first. Every recommendation is
saved. When a customer taps **📩 I want this book**, the files are sent, the request
is saved (status `sent`, or `new` if there were no files), and all admins get a
message with the customer's details.

## Getting the data

- `/export` → `customers.csv`, `recommendations.csv`, `requests.csv` (open in Excel)
- `/stats` → quick counts
- Or open `korean_books.db` with the free app *DB Browser for SQLite*.

## Good to know

- The bot only runs while `python bot.py` is running. To keep it online 24/7,
  run it on a small server (any cheap VPS or a Python hosting service).
- Back up `korean_books.db` regularly; it holds all your customer data.
- Customers are told their answers are saved. Only ask for what you need, and
  delete the data of anyone who asks.
- To change the questions or button texts, edit `LEVEL_OPTIONS`, `AIM_OPTIONS`
  and the messages in `bot.py`.
