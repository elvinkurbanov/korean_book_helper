"""
Rebels Book bot: asks a few questions, then recommends Korean books.

Customer flow (private chat):
  /start -> name -> age -> country -> Korean level -> learning aim -> book list
  Each book has an "I want this book" button. Clicking it sends the book's
  PDF and audio files (if the book has any), saves the request and notifies the admins.

Admin commands (only for Telegram IDs listed in ADMIN_IDS):
  /admin        - show admin help
  /books        - list all books with their IDs
  /addbook      - add a book:  /addbook Title | Author | level | aims | price | description
  /delbook ID   - delete a book
  /stock ID 0/1 - mark a book out of stock (0) or in stock (1)
  /exportbooks  - get the catalog as books.csv (edit it in Excel, then send it back)
  (send a .csv)  - replace the catalog with the uploaded file
  /export       - download customers, recommendations and requests as CSV
  /stats        - quick counts
  /report       - licence report: copies sent per book (/report 2026-11 for one month)
  (send a PDF or audio file with the book ID as caption) - attach it to that book
  /files ID     - list a book's files
  /delfiles ID  - remove all files from a book

Anyone:
  /myid         - shows your Telegram ID (use it to make yourself an admin)
"""

import functools
import html
import logging
import os
import re
from datetime import datetime

from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.constants import ChatType, ParseMode
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

import db

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)  # hide noisy polling logs
log = logging.getLogger("rebels_book_bot")

ADMIN_IDS = {
    int(x) for x in os.environ.get("ADMIN_IDS", "").replace(" ", "").split(",") if x.isdigit()
}

# PROTECT_FILES=1 stops customers from forwarding or saving the PDF/audio files
PROTECT_FILES = os.environ.get("PROTECT_FILES", "0") == "1"

# Conversation steps
NAME, AGE, COUNTRY, LEVEL, AIM = range(5)

# Button text -> value stored in the database
LEVEL_OPTIONS = {
    "🔤 Complete beginner (learning Hangul)": "beginner",
    "🌱 Beginner (TOPIK 1–2)": "beginner",
    "🌿 Intermediate (TOPIK 3–4)": "intermediate",
    "🌳 Advanced (TOPIK 5–6)": "advanced",
}
AIM_OPTIONS = {
    "📝 TOPIK exam": "topik",
    "💬 Speaking & conversation": "conversation",
    "💼 Work in Korea": "work",
    "🎓 University study": "university",
    "🎬 K-culture (dramas, music, books)": "culture",
}


BACK = "⬅️ Back"


def keyboard(options) -> ReplyKeyboardMarkup:
    """One button per row, plus a Back button at the bottom."""
    return ReplyKeyboardMarkup(
        [[o] for o in options] + [[BACK]], one_time_keyboard=True, resize_keyboard=True
    )


def back_only() -> ReplyKeyboardMarkup:
    """Just a Back button, shown under questions the user answers by typing."""
    return ReplyKeyboardMarkup([[BACK]], resize_keyboard=True)


def esc(text) -> str:
    """Escape text for Telegram HTML messages (so '<' or '&' in a title can't break them)."""
    return html.escape(str(text or ""))


def format_book(book) -> str:
    lines = [f"📖 <b>{esc(book['title'])}</b>"]
    if book["author"]:
        lines.append(f"✍️ {esc(book['author'])}")
    if book["description"]:
        lines.append(esc(book["description"]))
    if book["price"]:
        lines.append(f"💰 {esc(book['price'])}")
    kinds = db.file_kinds(book["id"])
    extras = [label for kind, label in (("pdf", "📄 PDF"), ("audio", "🎧 Audio")) if kind in kinds]
    if extras:
        lines.append(" · ".join(extras) + " included")
    return "\n".join(lines)


def format_customer(c) -> str:
    who = f'<a href="tg://user?id={c["telegram_id"]}">{esc(c["name"])}</a>'
    if c["username"]:
        who += f" (@{esc(c['username'])})"
    return (
        f"👤 {who}\n"
        f"Age: {esc(c['age'])} · Country: {esc(c['country'])}\n"
        f"Level: {esc(c['level'])} · Aim: {esc(c['aim'])}"
    )


async def notify_admins(context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(admin_id, text, parse_mode=ParseMode.HTML)
        except Exception as e:  # e.g. the admin never pressed Start in the bot
            log.warning("Could not notify admin %s: %s", admin_id, e)


# ==========================================================================
# Customer conversation
# ==========================================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # Personal questions belong in a private chat, not in the group
    if update.effective_chat.type != ChatType.PRIVATE:
        await update.message.reply_text(
            f"Let's find your book in a private chat 👉 https://t.me/{context.bot.username}?start=books"
        )
        return ConversationHandler.END

    context.user_data.clear()
    await update.message.reply_text(
        "안녕하세요! 👋 Welcome to Rebels Book.\n\n"
        "I'll ask you 5 short questions and suggest Korean books that fit you. "
        "Your answers are saved so our team can help you choose and order. "
        "Send /cancel at any time to stop.\n\n"
        "What's your name?",
        reply_markup=ReplyKeyboardRemove(),
    )
    return NAME


async def ask_age(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    name = update.message.text.strip()
    if name == BACK or not 1 <= len(name) <= 50:
        await update.message.reply_text("Please send a name (up to 50 characters).")
        return NAME
    context.user_data["name"] = name
    await update.message.reply_text(
        f"Nice to meet you, {name}! How old are you?", reply_markup=back_only()
    )
    return AGE


async def ask_country(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if not text.isdigit() or not 5 <= int(text) <= 100:
        await update.message.reply_text("Please send your age as a number, for example 24.")
        return AGE
    context.user_data["age"] = int(text)
    await update.message.reply_text(
        "Which country are you from? (your nationality)", reply_markup=back_only()
    )
    return COUNTRY


async def ask_level(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["country"] = update.message.text.strip()[:60]
    await update.message.reply_text(
        "What's your Korean level?", reply_markup=keyboard(LEVEL_OPTIONS)
    )
    return LEVEL


async def ask_aim(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    choice = update.message.text
    if choice not in LEVEL_OPTIONS:
        await update.message.reply_text(
            "Please choose one of the buttons 👇", reply_markup=keyboard(LEVEL_OPTIONS)
        )
        return LEVEL
    context.user_data["level"] = LEVEL_OPTIONS[choice]
    await update.message.reply_text(
        "Why are you learning Korean?", reply_markup=keyboard(AIM_OPTIONS)
    )
    return AIM


# --- Back button: each one re-asks the previous question ----------------------

async def back_to_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("What's your name?", reply_markup=ReplyKeyboardRemove())
    return NAME


async def back_to_age(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("How old are you?", reply_markup=back_only())
    return AGE


async def back_to_country(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "Which country are you from? (your nationality)", reply_markup=back_only()
    )
    return COUNTRY


async def back_to_level(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("What's your Korean level?", reply_markup=keyboard(LEVEL_OPTIONS))
    return LEVEL


async def finish(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    choice = update.message.text
    if choice not in AIM_OPTIONS:
        await update.message.reply_text(
            "Please choose one of the buttons 👇", reply_markup=keyboard(AIM_OPTIONS)
        )
        return AIM

    user = update.effective_user
    data = context.user_data
    data["aim"] = AIM_OPTIONS[choice]

    # 1) Save the customer
    db.save_customer(
        user.id, user.username, data["name"], data["age"],
        data["country"], data["level"], data["aim"],
    )

    # 2) Find books and save what we recommended
    books = db.recommend_books(data["level"], data["aim"])
    db.save_recommendations(user.id, books)

    if not books:
        await update.message.reply_text(
            "Thank you! We don't have a matching book in stock right now, "
            "but our team will contact you soon. 🙏",
            reply_markup=ReplyKeyboardRemove(),
        )
        customer = db.get_customer(user.id)
        await notify_admins(context, "🆕 New customer, no matching books:\n" + format_customer(customer))
        return ConversationHandler.END

    await update.message.reply_text(
        f"Thank you, {data['name']}! Here are books for you 📚",
        reply_markup=ReplyKeyboardRemove(),
    )
    for book in books:
        button = InlineKeyboardMarkup(
            [[InlineKeyboardButton("📩 I want this book", callback_data=f"want:{book['id']}")]]
        )
        await update.message.reply_text(
            format_book(book), parse_mode=ParseMode.HTML, reply_markup=button
        )
    await update.message.reply_text("Tap a button to request a book, or /start to search again.")

    data.clear()
    return ConversationHandler.END


async def want_book(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Customer tapped 'I want this book'."""
    query = update.callback_query
    await query.answer()
    book_id = int(query.data.split(":")[1])
    chat_id = query.message.chat_id

    book = db.get_book(book_id)
    if book is None:
        await query.message.reply_text("Sorry, this book is no longer available.")
        return

    await query.edit_message_reply_markup(
        InlineKeyboardMarkup([[InlineKeyboardButton("✅ Requested", callback_data="done")]])
    )

    files = db.get_files(book_id)
    sent = 0
    if files:
        await query.message.reply_text(f"Here is “{book['title']}” 📚")
        for f in files:
            try:
                # Re-send the file Telegram already has, using its file_id
                if f["send_as"] == "audio":
                    await context.bot.send_audio(chat_id, f["file_id"], protect_content=PROTECT_FILES)
                else:
                    await context.bot.send_document(chat_id, f["file_id"], protect_content=PROTECT_FILES)
                sent += 1
            except Exception as e:
                log.error("Could not send file %s of book %s: %s", f["id"], book_id, e)

    if sent:
        await query.message.reply_text("Enjoy your reading! Send /start to find more books. 🙌")
        status, title = "sent", "📤 <b>Book sent</b>"
    else:
        await query.message.reply_text(
            f"Got it! We'll contact you about “{book['title']}” soon. 🙌"
        )
        status, title = "new", "🛒 <b>Book request</b> (no files to send)"

    db.save_request(query.from_user.id, book, status)
    customer = db.get_customer(query.from_user.id)
    if customer:
        await notify_admins(
            context,
            f"{title}\n{format_customer(customer)}\n\n{format_book(book)} (ID {book['id']})",
        )


async def already_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer("Already requested ✅")


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text(
        "Cancelled. Send /start whenever you want to begin again.",
        reply_markup=ReplyKeyboardRemove(),
    )
    return ConversationHandler.END


async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(f"Your Telegram ID: {update.effective_user.id}")


# ==========================================================================
# Admin commands
# ==========================================================================

def admin_only(func):
    """Wrap a handler so only admins can run it."""
    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id not in ADMIN_IDS:
            await update.message.reply_text("Sorry, this command is for admins only.")
            return
        return await func(update, context)
    return wrapper


ADMIN_HELP = (
    "🔧 <b>Admin commands</b>\n"
    "/books – list all books with IDs\n"
    "/addbook Title | Author | level | aims | price | description\n"
    "   level: beginner, intermediate, advanced, all\n"
    "   aims: topik, conversation, work, university, culture, all (comma-separated)\n"
    "/delbook ID – delete a book\n"
    "/stock ID 0 – out of stock · /stock ID 1 – back in stock\n"
    "/exportbooks – get books.csv, edit it in Excel, send the file back to update the catalog\n"
    "/export – customers, recommendations, requests and deliveries as CSV\n"
    "/report – licence report: copies sent per book (all time)\n"
    "/report 2026-11 – the same for one month\n"
    "/stats – counts\n\n"
    "📎 <b>Book files</b>\n"
    "Send a PDF or audio file to me with the <b>book ID as the caption</b> (e.g. <code>5</code>). "
    "You can attach several audio files to one book.\n"
    "/files ID – see a book's files\n"
    "/delfiles ID – remove all files from a book"
)


@admin_only
async def admin_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(ADMIN_HELP, parse_mode=ParseMode.HTML)


@admin_only
async def list_books_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    books = db.list_books()
    if not books:
        await update.message.reply_text("The catalog is empty. Use /addbook or send a books.csv file.")
        return
    def icons(book_id):
        kinds = db.file_kinds(book_id)
        marks = ("📄" if "pdf" in kinds else "") + ("🎧" if "audio" in kinds else "")
        return f" {marks}" if marks else ""

    lines = [
        f"{b['id']}. {'' if b['in_stock'] else '❌ '}{b['title']}{icons(b['id'])} [{b['level']} | {b['aims']}]"
        for b in books
    ]
    # Telegram messages are limited to 4096 characters, so send in chunks
    chunk = ""
    for line in lines:
        if len(chunk) + len(line) > 3500:
            await update.message.reply_text(chunk)
            chunk = ""
        chunk += line + "\n"
    await update.message.reply_text(chunk + "\n❌ out of stock · 📄 has PDF · 🎧 has audio")


@admin_only
async def add_book_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    raw = update.message.text.partition(" ")[2]  # everything after "/addbook"
    parts = [p.strip() for p in raw.split("|")]
    if len(parts) < 4 or not parts[0]:
        await update.message.reply_text(
            "Format:\n/addbook Title | Author | level | aims | price | description\n\n"
            "Example:\n/addbook Korean Grammar in Use: Beginning | Darakwon | beginner | topik,conversation | 25,000원 | Grammar with exercises"
        )
        return
    parts += [""] * (6 - len(parts))  # price and description are optional
    title, author, level, aims, price, description = parts[:6]
    level, aims = level.lower(), aims.lower().replace(" ", "")
    error = db.validate_book(level, aims)
    if error:
        await update.message.reply_text(f"⚠️ {error}")
        return
    book_id = db.add_book(title, author, level, aims, price, description)
    await update.message.reply_text(f"✅ Added book #{book_id}: {title}")


@admin_only
async def del_book_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) != 1 or not context.args[0].isdigit():
        await update.message.reply_text("Usage: /delbook ID (see /books for IDs)")
        return
    ok = db.delete_book(int(context.args[0]))
    await update.message.reply_text("🗑 Deleted." if ok else "No book with that ID.")


@admin_only
async def stock_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args
    if len(args) != 2 or not args[0].isdigit() or args[1] not in ("0", "1"):
        await update.message.reply_text("Usage: /stock ID 0 (out of stock) or /stock ID 1 (in stock)")
        return
    ok = db.set_stock(int(args[0]), args[1] == "1")
    await update.message.reply_text("✅ Updated." if ok else "No book with that ID.")


@admin_only
async def export_books_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    path = db.export_csv("books")
    with open(path, "rb") as f:
        await update.message.reply_document(
            f,
            filename="books.csv",
            caption=(
                "Edit this file in Excel or Google Sheets, save it as CSV, and send it back to me "
                "to replace the catalog. Leave 'id' empty for new books."
            ),
        )


@admin_only
async def import_books_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin sent a .csv file: replace the catalog with it (after a backup)."""
    file = await update.message.document.get_file()
    data = await file.download_as_bytearray()
    try:
        text = bytes(data).decode("utf-8-sig")
    except UnicodeDecodeError:
        await update.message.reply_text(
            "⚠️ I couldn't read the file. In Excel, use 'Save As → CSV UTF-8'."
        )
        return

    backup_folder = os.path.join("backups", datetime.now().strftime("%Y%m%d_%H%M%S"))
    db.export_csv("books", folder=backup_folder)
    try:
        count = db.import_books_csv(text)
    except ValueError as e:
        await update.message.reply_text(f"⚠️ Nothing was changed.\n{e}")
        return
    await update.message.reply_text(
        f"✅ Catalog updated: {count} books.\n(Old catalog backed up in {backup_folder})"
    )


AUDIO_EXTENSIONS = (".mp3", ".m4a", ".wav", ".ogg", ".aac", ".flac", ".zip")


async def attach_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin sent a PDF or audio file with a book ID as caption: attach it to that book."""
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("Sorry, I can only accept files from admins.")
        return
    msg = update.message

    # 1) What kind of file is it?
    if msg.audio:  # sent as a music/audio message
        kind, send_as = "audio", "audio"
        file_id = msg.audio.file_id
        name = msg.audio.file_name or msg.audio.title or "audio"
    elif msg.document:
        file_id = msg.document.file_id
        name = msg.document.file_name or "file"
        lower = name.lower()
        mime = msg.document.mime_type or ""
        if lower.endswith(".pdf") or mime == "application/pdf":
            kind, send_as = "pdf", "document"
        elif lower.endswith(AUDIO_EXTENSIONS) or mime.startswith("audio/"):
            kind, send_as = "audio", "document"  # e.g. an mp3 sent as a file, or a zip of tracks
        else:
            await msg.reply_text("⚠️ I only accept PDF or audio files (mp3, m4a, wav, zip of audio...).")
            return
    else:
        return

    # 2) Which book? The caption must contain the book ID
    match = re.search(r"\d+", msg.caption or "")
    if not match:
        await msg.reply_text(
            "⚠️ Please send the file again with the book ID as the caption, e.g. 5\n(see /books for IDs)"
        )
        return
    book = db.get_book(int(match.group()))
    if book is None:
        await msg.reply_text(f"⚠️ No book with ID {match.group()}. See /books.")
        return

    db.add_file(book["id"], kind, send_as, file_id, name)
    icon = "📄" if kind == "pdf" else "🎧"
    await msg.reply_text(f"✅ {icon} {name} attached to #{book['id']} {book['title']}")


@admin_only
async def files_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) != 1 or not context.args[0].isdigit():
        await update.message.reply_text("Usage: /files ID")
        return
    files = db.get_files(int(context.args[0]))
    if not files:
        await update.message.reply_text("This book has no files yet.")
        return
    await update.message.reply_text(
        "\n".join(f"{'📄' if f['kind'] == 'pdf' else '🎧'} {f['file_name']}" for f in files)
    )


@admin_only
async def delfiles_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) != 1 or not context.args[0].isdigit():
        await update.message.reply_text("Usage: /delfiles ID")
        return
    n = db.delete_files(int(context.args[0]))
    await update.message.reply_text(f"🗑 Removed {n} file(s).")


@admin_only
async def export_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    for table in ("customers", "recommendations", "requests", "deliveries"):
        path = db.export_csv(table)
        with open(path, "rb") as f:
            await update.message.reply_document(f, filename=f"{table}.csv")


@admin_only
async def report_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    month = context.args[0] if context.args else None
    if month and not re.fullmatch(r"\d{4}-\d{2}", month):
        await update.message.reply_text("Usage: /report (all time) or /report 2026-11 (one month)")
        return
    rows = db.licence_report(month)
    period = month or "all time"
    if not rows:
        await update.message.reply_text(f"No copies sent ({period}).")
        return

    total = sum(r["copies"] for r in rows)
    lines = [f"📊 <b>Licence report – {period}</b>", f"Total copies sent: <b>{total}</b>", ""]
    lines += [
        f"#{r['book_id']} {esc(r['book_title'])}: {r['copies']} copies, {r['customers']} customers"
        for r in rows
    ]
    await update.message.reply_text("\n".join(lines)[:4000], parse_mode=ParseMode.HTML)

    # The same report + the full list of deliveries, as Excel-ready files
    name = f"licence_report_{month or 'all'}"
    with open(db.export_rows(rows, name), "rb") as f:
        await update.message.reply_document(f, filename=f"{name}.csv")
    with open(db.export_rows(db.deliveries(month), f"deliveries_{month or 'all'}"), "rb") as f:
        await update.message.reply_document(
            f, filename=f"deliveries_{month or 'all'}.csv", caption="Every delivered copy, with customer details"
        )


@admin_only
async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    s = db.stats()
    await update.message.reply_text(
        f"📚 Books: {s['books']}\n📎 Book files: {s['book_files']}\n👥 Customers: {s['customers']}\n"
        f"💡 Recommendations: {s['recommendations']}\n🛒 Requests: {s['requests']}"
    )


# ==========================================================================
# Wiring
# ==========================================================================

async def set_menu(app: Application) -> None:
    """Commands shown in Telegram's menu button."""
    await app.bot.set_my_commands(
        [BotCommand("start", "Find Korean books for me"), BotCommand("cancel", "Stop")]
    )


def build_app(token: str) -> Application:
    app = ApplicationBuilder().token(token).post_init(set_menu).build()

    text = filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE
    back = text & filters.Regex(f"^{re.escape(BACK)}$")
    conversation = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            NAME: [MessageHandler(text, ask_age)],
            AGE: [MessageHandler(back, back_to_name), MessageHandler(text, ask_country)],
            COUNTRY: [MessageHandler(back, back_to_age), MessageHandler(text, ask_level)],
            LEVEL: [MessageHandler(back, back_to_country), MessageHandler(text, ask_aim)],
            AIM: [MessageHandler(back, back_to_level), MessageHandler(text, finish)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,  # /start in the middle restarts the questions
    )
    app.add_handler(conversation)
    app.add_handler(CallbackQueryHandler(want_book, pattern=r"^want:\d+$"))
    app.add_handler(CallbackQueryHandler(already_done, pattern=r"^done$"))
    app.add_handler(CommandHandler("myid", myid))

    app.add_handler(CommandHandler("admin", admin_help))
    app.add_handler(CommandHandler("books", list_books_cmd))
    app.add_handler(CommandHandler("addbook", add_book_cmd))
    app.add_handler(CommandHandler("delbook", del_book_cmd))
    app.add_handler(CommandHandler("stock", stock_cmd))
    app.add_handler(CommandHandler("exportbooks", export_books_cmd))
    app.add_handler(CommandHandler("export", export_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("report", report_cmd))
    app.add_handler(CommandHandler("files", files_cmd))
    app.add_handler(CommandHandler("delfiles", delfiles_cmd))
    private = filters.ChatType.PRIVATE
    app.add_handler(MessageHandler(filters.Document.FileExtension("csv") & private, import_books_file))
    # Any other document or audio = a book file (PDF / audio)
    app.add_handler(MessageHandler((filters.Document.ALL | filters.AUDIO) & private, attach_file))
    return app


def main() -> None:
    token = os.environ.get("BOT_TOKEN")
    if not token or token.startswith("PASTE_"):
        raise SystemExit("Set the BOT_TOKEN environment variable first (see README.md).")
    if not ADMIN_IDS:
        log.warning("ADMIN_IDS is empty: nobody can use admin commands. Use /myid to get your ID.")
    db.init_db()
    log.info("Bot is running. Press Ctrl+C to stop.")
    build_app(token).run_polling()


if __name__ == "__main__":
    main()
