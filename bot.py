# bot.py
import os
import json
import base64
import logging
from typing import Dict, List, Optional

import gspread
from google.oauth2.service_account import Credentials

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("eat_with_nas_bot")

# ----------------------------
# ENV
# ----------------------------
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
SHEET_ID = os.environ.get("GOOGLE_SHEET_ID", "").strip()
WORKSHEET_NAME = os.environ.get("GOOGLE_WORKSHEET_NAME", "places").strip()
GOOGLE_CREDS_B64 = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON_B64", "").strip()

PAGE_SIZE = 10

# ----------------------------
# Labels (RU + EN)
# ----------------------------
CATEGORY_LABELS = {
    "restaurant": "🍽 Restaurants / Рестораны",
    "cafe": "☕️ Cafes / Кафе",
    "beach_club": "🌊 Beach Clubs / Бич-клабы",
    "bar": "🍸 Bars / Бары",
}
STATUS_LABELS = {
    "reviewed": "✅ Reviewed / Обзоры",
    "wishlist": "⭐ Wishlist / Хочу",
}

# ----------------------------
# Callback data
# ----------------------------
CB_HOME = "home"
CB_BROWSE = "browse"
CB_TOP = "menu:top"
CB_WISHLIST = "menu:wishlist"
CB_REVIEWED = "menu:reviewed"
CB_SEARCH = "menu:search"
CB_HELP = "help"

CB_COUNTRY = "country:"
CB_CITY = "city:"
CB_CATEGORY = "cat:"
CB_STATUS = "status:"
CB_LIST = "list:"  # list:<page>
CB_BACK = "back"


# ----------------------------
# Google Sheets
# ----------------------------
def _load_gspread_client() -> gspread.Client:
    if not GOOGLE_CREDS_B64:
        raise RuntimeError("Missing GOOGLE_SERVICE_ACCOUNT_JSON_B64 env var")

    creds_json = base64.b64decode(GOOGLE_CREDS_B64.encode("utf-8")).decode("utf-8")
    info = json.loads(creds_json)

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets.readonly",
        "https://www.googleapis.com/auth/drive.readonly",
    ]
    credentials = Credentials.from_service_account_info(info, scopes=scopes)
    return gspread.authorize(credentials)


def _fetch_rows() -> List[Dict[str, str]]:
    """
    Expected headers:
      id,country,city,category,name,status,link,notes,is_top
    is_top: yes/no (optional but recommended)
    """
    gc = _load_gspread_client()
    sh = gc.open_by_key(SHEET_ID)
    ws = sh.worksheet(WORKSHEET_NAME)

    records = ws.get_all_records()
    cleaned: List[Dict[str, str]] = []
    for r in records:
        row = {str(k).strip(): ("" if r[k] is None else str(r[k]).strip()) for k in r}
        if not row.get("name") and not row.get("id"):
            continue
        cleaned.append(row)
    return cleaned


def _unique_sorted(values: List[str]) -> List[str]:
    return sorted({v for v in values if v})


# ----------------------------
# State
# ----------------------------
def _reset_nav(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data["nav"] = {
        "mode": None,          # browse / quick / search
        "country": None,
        "city": None,
        "category": None,
        "status": None,
        "page": 0,
        "top_only": False,
        "search_query": None,
        "search_results": None,
    }


def _nav(context: ContextTypes.DEFAULT_TYPE) -> Dict:
    if "nav" not in context.user_data:
        _reset_nav(context)
    return context.user_data["nav"]


# ----------------------------
# Keyboards
# ----------------------------
def kb_main_menu() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("🌍 Browse / Страны", callback_data=CB_BROWSE)],
        [InlineKeyboardButton("⭐ Top Picks / Лучшее", callback_data=CB_TOP)],
        [
            InlineKeyboardButton("⭐ Wishlist / Хочу", callback_data=CB_WISHLIST),
            InlineKeyboardButton("✅ Reviewed / Обзоры", callback_data=CB_REVIEWED),
        ],
        [InlineKeyboardButton("🔎 Search / Поиск", callback_data=CB_SEARCH)],
        [InlineKeyboardButton("ℹ️ Help / Помощь", callback_data=CB_HELP)],
    ]
    return InlineKeyboardMarkup(keyboard)


def kb_home_only() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Home / Домой", callback_data=CB_HOME)]])


def kb_with_back_home(rows: List[List[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    rows.append(
        [
            InlineKeyboardButton("🔙 Back / Назад", callback_data=CB_BACK),
            InlineKeyboardButton("🏠 Home / Домой", callback_data=CB_HOME),
        ]
    )
    return InlineKeyboardMarkup(rows)


def kb_pagination(page: int, total: int) -> List[InlineKeyboardButton]:
    buttons: List[InlineKeyboardButton] = []
    if page > 0:
        buttons.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"{CB_LIST}{page-1}"))
    if (page + 1) * PAGE_SIZE < total:
        buttons.append(InlineKeyboardButton("Next ➡️", callback_data=f"{CB_LIST}{page+1}"))
    return buttons


# ----------------------------
# Screens
# ----------------------------
async def show_home(update: Update, context: ContextTypes.DEFAULT_TYPE, text: Optional[str] = None) -> None:
    _reset_nav(context)
    msg = text or "Choose an option / Выберите действие:"
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(msg, reply_markup=kb_main_menu())
    else:
        await update.message.reply_text(msg, reply_markup=kb_main_menu())


async def show_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "Welcome to Eat with Nas — Food & Travel Guide!\n\n"
        "• Browse / Страны — by country → city → category\n"
        "• Top Picks / Лучшее — my personal favorites\n"
        "• Wishlist / Хочу — places I want to visit\n"
        "• Reviewed / Обзоры — places with published reviews\n"
        "• Search / Поиск — find a place by name\n\n"
        "Tap a place to open the review or official page.\n"
        "—\n"
        "Добро пожаловать в Eat with Nas!\n\n"
        "Нажмите на место, чтобы открыть обзор или официальный сайт."
    )
    await update.callback_query.answer()
    await update.callback_query.edit_message_text(text, reply_markup=kb_home_only())


async def show_countries(update: Update, context: ContextTypes.DEFAULT_TYPE, rows: List[Dict[str, str]]) -> None:
    nav = _nav(context)
    nav.update({"mode": "browse", "country": None, "city": None, "category": None, "status": None, "page": 0, "top_only": False})

    countries = _unique_sorted([r.get("country", "") for r in rows])
    buttons = [[InlineKeyboardButton(c, callback_data=f"{CB_COUNTRY}{c}")] for c in countries]
    await update.callback_query.answer()
    await update.callback_query.edit_message_text("Select a country / Выберите страну:", reply_markup=kb_with_back_home(buttons))


async def show_cities(update: Update, context: ContextTypes.DEFAULT_TYPE, rows: List[Dict[str, str]], country: str) -> None:
    nav = _nav(context)
    nav.update({"mode": "browse", "country": country, "city": None, "category": None, "status": None, "page": 0, "top_only": False})

    cities = _unique_sorted([r.get("city", "") for r in rows if r.get("country") == country])
    buttons = [[InlineKeyboardButton(c, callback_data=f"{CB_CITY}{c}")] for c in cities]
    await update.callback_query.answer()
    await update.callback_query.edit_message_text(f"{country} — select a city / выберите город:", reply_markup=kb_with_back_home(buttons))


async def show_categories(update: Update, context: ContextTypes.DEFAULT_TYPE, city: str) -> None:
    nav = _nav(context)
    nav.update({"city": city, "category": None, "status": None, "page": 0, "top_only": False})

    rows: List[List[InlineKeyboardButton]] = []
    for key in ["restaurant", "cafe", "beach_club", "bar"]:
        rows.append([InlineKeyboardButton(CATEGORY_LABELS[key], callback_data=f"{CB_CATEGORY}{key}")])

    await update.callback_query.answer()
    await update.callback_query.edit_message_text(
        f"{nav['country']} / {city} — select a category / выберите категорию:",
        reply_markup=kb_with_back_home(rows),
    )


async def show_status_choice(update: Update, context: ContextTypes.DEFAULT_TYPE, category: str) -> None:
    nav = _nav(context)
    nav.update({"category": category, "status": None, "page": 0, "top_only": False})

    rows = [
        [InlineKeyboardButton("✅ Reviewed / Обзоры", callback_data=f"{CB_STATUS}reviewed")],
        [InlineKeyboardButton("⭐ Wishlist / Хочу", callback_data=f"{CB_STATUS}wishlist")],
    ]
    await update.callback_query.answer()
    await update.callback_query.edit_message_text(
        f"{nav['country']} / {nav['city']} / {CATEGORY_LABELS[category]} — choose / выберите:",
        reply_markup=kb_with_back_home(rows),
    )


def _filter_rows(rows: List[Dict[str, str]], nav: Dict) -> List[Dict[str, str]]:
    out = rows

    if nav.get("country"):
        out = [r for r in out if r.get("country") == nav["country"]]
    if nav.get("city"):
        out = [r for r in out if r.get("city") == nav["city"]]
    if nav.get("category"):
        out = [r for r in out if r.get("category") == nav["category"]]
    if nav.get("status"):
        out = [r for r in out if r.get("status") == nav["status"]]

    if nav.get("top_only"):
        out = [r for r in out if (r.get("is_top") or "").strip().lower() == "yes"]

    out = [r for r in out if r.get("link")]
    out.sort(key=lambda r: (r.get("name") or "").lower())
    return out


async def show_list(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 0) -> None:
    nav = _nav(context)
    nav["page"] = max(0, page)

    rows = _fetch_rows()
    filtered = _filter_rows(rows, nav)

    total = len(filtered)
    start = nav["page"] * PAGE_SIZE
    end = start + PAGE_SIZE
    page_items = filtered[start:end]

    title_parts: List[str] = []
    if nav.get("top_only"):
        title_parts.append("⭐ Top Picks / Лучшее")
    if nav.get("country"):
        title_parts.append(nav["country"])
    if nav.get("city"):
        title_parts.append(nav["city"])
    if nav.get("category"):
        title_parts.append(CATEGORY_LABELS[nav["category"]])
    if nav.get("status"):
        title_parts.append(STATUS_LABELS[nav["status"]])

    header = " / ".join(title_parts) if title_parts else "Results / Результаты"
    shown_a = min(start + 1, total) if total else 0
    shown_b = min(end, total)

    text = f"{header}\n\nShowing {shown_a}-{shown_b} of {total}"

    buttons: List[List[InlineKeyboardButton]] = []
    for item in page_items:
        buttons.append([InlineKeyboardButton(item.get("name", "Unnamed"), url=item.get("link", ""))])

    pag = kb_pagination(nav["page"], total)
    if pag:
        buttons.append(pag)

    await update.callback_query.answer()
    await update.callback_query.edit_message_text(text, reply_markup=kb_with_back_home(buttons))


async def show_quick_list(update: Update, context: ContextTypes.DEFAULT_TYPE, status: Optional[str], top_only: bool) -> None:
    nav = _nav(context)
    nav.update({
        "mode": "quick",
        "country": None,
        "city": None,
        "category": None,
        "status": status,
        "page": 0,
        "top_only": top_only,
        "search_query": None,
        "search_results": None,
    })
    await show_list(update, context, page=0)


# ----------------------------
# Search
# ----------------------------
async def start_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    nav = _nav(context)
    nav.update({
        "mode": "search",
        "country": None,
        "city": None,
        "category": None,
        "status": None,
        "page": 0,
        "top_only": False,
        "search_query": None,
        "search_results": None,
    })
    await update.callback_query.answer()
    await update.callback_query.edit_message_text(
        "Type a place name to search / Введите название места.\n\nExample: Masala",
        reply_markup=kb_with_back_home([]),
    )


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    nav = _nav(context)
    q = (update.message.text or "").strip()
    if not q:
        return

    if nav.get("mode") != "search":
        await update.message.reply_text("Use menu / Используйте меню. Tap 🔎 Search / Поиск.", reply_markup=kb_main_menu())
        return

    rows = _fetch_rows()
    needle = q.lower()
    results = [r for r in rows if needle in (r.get("name") or "").lower() and r.get("link")]
    results.sort(key=lambda r: (r.get("name") or "").lower())

    nav["search_query"] = q
    nav["search_results"] = results
    nav["page"] = 0

    await update.message.reply_text(f"Found {len(results)} results / Найдено: {len(results)}")
    await send_search_page(update, context, 0)


async def send_search_page(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int) -> None:
    nav = _nav(context)
    results = nav.get("search_results") or []
    nav["page"] = max(0, page)

    total = len(results)
    start = nav["page"] * PAGE_SIZE
    end = start + PAGE_SIZE
    page_items = results[start:end]

    shown_a = min(start + 1, total) if total else 0
    shown_b = min(end, total)

    text = f"Search / Поиск: “{nav.get('search_query')}”\n\nShowing {shown_a}-{shown_b} of {total}"

    buttons: List[List[InlineKeyboardButton]] = []
    for item in page_items:
        buttons.append([InlineKeyboardButton(item.get("name", "Unnamed"), url=item.get("link", ""))])

    pag = kb_pagination(nav["page"], total)
    if pag:
        buttons.append(pag)

    await update.message.reply_text(text, reply_markup=kb_with_back_home(buttons))


# ----------------------------
# Router
# ----------------------------
async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    data = query.data
    nav = _nav(context)

    if data == CB_HOME:
        await show_home(update, context)
        return

    if data == CB_HELP:
        await show_help(update, context)
        return

    if data == CB_BROWSE:
        try:
            rows = _fetch_rows()
        except Exception as e:
            logger.exception("Sheets error")
            await query.answer()
            await query.edit_message_text(f"Cannot read Google Sheet.\nError: {e}", reply_markup=kb_main_menu())
            return
        await show_countries(update, context, rows)
        return

    if data == CB_TOP:
        await show_quick_list(update, context, status=None, top_only=True)
        return

    if data == CB_WISHLIST:
        await show_quick_list(update, context, status="wishlist", top_only=False)
        return

    if data == CB_REVIEWED:
        await show_quick_list(update, context, status="reviewed", top_only=False)
        return

    if data == CB_SEARCH:
        await start_search(update, context)
        return

    if data.startswith(CB_COUNTRY):
        country = data[len(CB_COUNTRY):]
        try:
            rows = _fetch_rows()
        except Exception as e:
            logger.exception("Sheets error")
            await query.answer()
            await query.edit_message_text(f"Cannot read Google Sheet.\nError: {e}", reply_markup=kb_main_menu())
            return
        await show_cities(update, context, rows, country)
        return

    if data.startswith(CB_CITY):
        city = data[len(CB_CITY):]
        await show_categories(update, context, city)
        return

    if data.startswith(CB_CATEGORY):
        cat = data[len(CB_CATEGORY):]
        await show_status_choice(update, context, cat)
        return

    if data.startswith(CB_STATUS):
        status = data[len(CB_STATUS):]
        nav["status"] = status
        await show_list(update, context, page=0)
        return

    if data.startswith(CB_LIST):
        page_str = data[len(CB_LIST):]
        try:
            page = int(page_str)
        except ValueError:
            page = 0

        if nav.get("mode") == "search" and nav.get("search_results") is not None:
            await query.answer()
            results = nav.get("search_results") or []
            nav["page"] = max(0, page)

            total = len(results)
            start = nav["page"] * PAGE_SIZE
            end = start + PAGE_SIZE
            page_items = results[start:end]

            shown_a = min(start + 1, total) if total else 0
            shown_b = min(end, total)
            text = f"Search / Поиск: “{nav.get('search_query')}”\n\nShowing {shown_a}-{shown_b} of {total}"

            buttons: List[List[InlineKeyboardButton]] = []
            for item in page_items:
                buttons.append([InlineKeyboardButton(item.get("name", "Unnamed"), url=item.get("link", ""))])

            pag = kb_pagination(nav["page"], total)
            if pag:
                buttons.append(pag)

            await query.edit_message_text(text, reply_markup=kb_with_back_home(buttons))
            return

        await show_list(update, context, page=page)
        return

    if data == CB_BACK:
        await query.answer()

        # Back from search => home
        if nav.get("mode") == "search":
            await query.edit_message_text("Choose an option / Выберите действие:", reply_markup=kb_main_menu())
            _reset_nav(context)
            return

        # Back logic in browse chain
        if nav.get("status"):
            nav["status"] = None
            cat = nav.get("category")
            if not cat:
                await show_home(update, context)
                return
            rows = [
                [InlineKeyboardButton("✅ Reviewed / Обзоры", callback_data=f"{CB_STATUS}reviewed")],
                [InlineKeyboardButton("⭐ Wishlist / Хочу", callback_data=f"{CB_STATUS}wishlist")],
            ]
            await query.edit_message_text(
                f"{nav.get('country')} / {nav.get('city')} / {CATEGORY_LABELS.get(cat, '')} — choose / выберите:",
                reply_markup=kb_with_back_home(rows),
            )
            return

        if nav.get("category"):
            nav["category"] = None
            await show_categories(update, context, nav.get("city"))
            return

        if nav.get("city"):
            nav["city"] = None
            nav["category"] = None
            nav["status"] = None
            try:
                rows = _fetch_rows()
            except Exception as e:
                logger.exception("Sheets error")
                await query.edit_message_text(f"Cannot read Google Sheet.\nError: {e}", reply_markup=kb_main_menu())
                _reset_nav(context)
                return
            await show_cities(update, context, rows, nav.get("country"))
            return

        if nav.get("country"):
            nav["country"] = None
            nav["city"] = None
            nav["category"] = None
            nav["status"] = None
            try:
                rows = _fetch_rows()
            except Exception as e:
                logger.exception("Sheets error")
                await query.edit_message_text(f"Cannot read Google Sheet.\nError: {e}", reply_markup=kb_main_menu())
                _reset_nav(context)
                return
            await show_countries(update, context, rows)
            return

        # Back from quick/top => home
        await query.edit_message_text("Choose an option / Выберите действие:", reply_markup=kb_main_menu())
        _reset_nav(context)
        return

    # fallback
    await query.answer()
    await query.edit_message_text("Choose an option / Выберите действие:", reply_markup=kb_main_menu())
    _reset_nav(context)


# ----------------------------
# Commands
# ----------------------------
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await show_home(update, context, text="Hi! / Привет!\nChoose an option / Выберите действие:")


def main() -> None:
    if not TELEGRAM_TOKEN:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN env var")
    if not SHEET_ID:
        raise RuntimeError("Missing GOOGLE_SHEET_ID env var")
    if not GOOGLE_CREDS_B64:
        raise RuntimeError("Missing GOOGLE_SERVICE_ACCOUNT_JSON_B64 env var")

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    logger.info("Bot starting polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
