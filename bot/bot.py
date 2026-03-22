"""
Telegram-бот модерации заявок на вступление в чат @samuibiz.
Автоматически оценивает пользователей по ряду признаков
и одобряет/отклоняет/отправляет на ревью.
"""

import asyncio
import os
import re
import logging
import unicodedata
from datetime import datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, User
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    ChatJoinRequestHandler,
    ChatMemberHandler,
    CommandHandler,
    ConversationHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

load_dotenv()

# ── Конфигурация ──────────────────────────────────────────────

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID", "")
SPAMWATCH_TOKEN = os.getenv("SPAMWATCH_TOKEN", "")
SCORE_APPROVE = int(os.getenv("SCORE_APPROVE", "20"))
SCORE_DECLINE = int(os.getenv("SCORE_DECLINE", "-10"))
TEST_MODE = os.getenv("TEST_MODE", "true").lower() in ("true", "1", "yes")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
API_ID = os.getenv("API_ID", "")
API_HASH = os.getenv("API_HASH", "")

_wl_raw = os.getenv("WHITELIST_IDS", "")
WHITELIST_IDS: set[int] = {
    int(x.strip()) for x in _wl_raw.split(",") if x.strip().isdigit()
}

# Группы с автобаном новых подозрительных (без заявок на вступление)
# Бот должен быть админом с правом Ban users
AUTOBAN_CHATS: set[str] = set(
    filter(None, os.getenv("AUTOBAN_CHATS", "").split(","))
)

# Чат для еженедельных donation-напоминаний (по умолчанию @samuibiz)
DONATION_CHAT = os.getenv("DONATION_CHAT", "@samuibiz")

# Путь к QR-коду для donation
QR_DONATION_PATH = Path(__file__).parent / "qr_donation.jpg"

# Путь к фото Натали для четвергового поста
NATALI_SPEAKER_PATH = Path(__file__).parent / "natali_speaker.jpg"

# Путь к картинке еженедельной встречи
WEEKLY_MEETING_PATH = Path(__file__).parent / "weekly_meeting.jpg"

# Дата первой встречи 2026 года (7 января — первая среда)
MEETING_START_DATE = datetime(2026, 1, 7, tzinfo=ZoneInfo("Asia/Bangkok"))

# Путь к картинке прогулки
WALK_PATH = Path(__file__).parent / "walk.jpg"

# Дата первой Вечерней Бизнес-прогулки (23 декабря 2025 = XIII была 17 марта 2026)
WALK_START_DATE = datetime(2025, 12, 23, tzinfo=ZoneInfo("Asia/Bangkok"))

WALK_LOCATION = "https://maps.app.goo.gl/oi7jDoRx2xNuMusW8?g_st=ac"

BANGKOK_TZ = ZoneInfo("Asia/Bangkok")

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=getattr(logging, LOG_LEVEL, logging.INFO),
)
logger = logging.getLogger(__name__)

# ── Оценка возраста аккаунта по user ID ───────────────────────
# Telegram ID растут примерно монотонно со временем.
# Точки калибровки: (user_id, unix_timestamp).

ID_DATE_POINTS: list[tuple[int, int]] = sorted([
    (2768409,      1383264000),   # Nov 2013
    (7679610,      1388448000),   # Dec 2013
    (11538514,     1391212000),   # Jan 2014
    (15835244,     1392940000),   # Feb 2014
    (23646077,     1393459000),   # Feb 2014
    (38015510,     1393632000),   # Mar 2014
    (44634663,     1399334000),   # Jun 2014
    (46145305,     1400198000),   # May 2014
    (54845238,     1411257000),   # Sep 2014
    (63263518,     1414454000),   # Oct 2014
    (101260938,    1425600000),   # Mar 2015
    (111220210,    1429574000),   # Apr 2015
    (103258382,    1432771000),   # May 2015
    (109393468,    1439078000),   # Aug 2015
    (112594714,    1439683000),   # Aug 2015
    (124872445,    1439856000),   # Aug 2015
    (130029930,    1441324000),   # Sep 2015
    (125828524,    1444003000),   # Oct 2015
    (133909606,    1444176000),   # Oct 2015
    (143445125,    1448928000),   # Dec 2015
    (148670295,    1452211000),   # Jan 2016
    (152079341,    1453420000),   # Jan 2016
    (157242073,    1446768000),   # Nov 2015
    (171295414,    1457481000),   # Mar 2016
    (181783990,    1460246000),   # Apr 2016
    (222021233,    1465344000),   # Jun 2016
    (225034354,    1466208000),   # Jun 2016
    (278941742,    1473465000),   # Sep 2016
    (285253072,    1476835000),   # Oct 2016
    (294851037,    1479600000),   # Nov 2016
    (297621225,    1481846000),   # Dec 2016
    (328594461,    1482969000),   # Dec 2016
    (337808429,    1487707000),   # Feb 2017
    (341546272,    1487782000),   # Feb 2017
    (352940995,    1487894000),   # Feb 2017
    (369669043,    1490918000),   # Mar 2017
    (400169472,    1501459000),   # Jul 2017
    (805158066,    1563208000),   # Jul 2019
    (1974255900,   1634000000),   # Oct 2021
    (2000000000,   1638316800),   # Dec 2021
    (3000000000,   1657843200),   # Jul 2022
    (5000000000,   1696118400),   # Oct 2023
    (6000000000,   1715212800),   # May 2024
    (7000000000,   1735689600),   # Jan 2025
    (7500000000,   1740700800),   # Feb 2026
], key=lambda p: p[0])

# Приблизительная скорость роста ID: ~2M в день (для экстраполяции)
ID_GROWTH_PER_DAY = 2_000_000


def estimate_account_age_days(user_id: int) -> int:
    """Оценивает возраст аккаунта в днях по user ID."""
    now_ts = datetime.now(timezone.utc).timestamp()

    # Если ID меньше первой точки — очень старый аккаунт
    if user_id <= ID_DATE_POINTS[0][0]:
        return max(1, int((now_ts - ID_DATE_POINTS[0][1]) / 86400))

    # Если ID больше последней точки — экстраполяция
    if user_id >= ID_DATE_POINTS[-1][0]:
        last_id, last_ts = ID_DATE_POINTS[-1]
        extra_days = (user_id - last_id) / ID_GROWTH_PER_DAY
        created_ts = last_ts + extra_days * 86400
        age = (now_ts - created_ts) / 86400
        return max(0, int(age))

    # Линейная интерполяция между двумя ближайшими точками
    for i in range(len(ID_DATE_POINTS) - 1):
        id_lo, ts_lo = ID_DATE_POINTS[i]
        id_hi, ts_hi = ID_DATE_POINTS[i + 1]
        if id_lo <= user_id <= id_hi:
            ratio = (user_id - id_lo) / (id_hi - id_lo)
            created_ts = ts_lo + ratio * (ts_hi - ts_lo)
            age = (now_ts - created_ts) / 86400
            return max(0, int(age))

    return 0


# ── Функции скоринга ──────────────────────────────────────────

def score_account_age(age_days: int) -> int:
    if age_days < 7:
        return -30
    if age_days < 30:
        return -15
    if age_days < 90:
        return -5
    if age_days < 365:
        return 10
    if age_days < 365 * 3:
        return 20
    return 25


SUSPICIOUS_USERNAME_PATTERNS = [
    re.compile(r"bot$", re.IGNORECASE),
    re.compile(r"^[a-z]{2,4}\d{5,}$"),
    re.compile(r"^\d+[a-z]{1,3}$"),
    re.compile(r"^[a-z]\d[a-z]\d[a-z]", re.IGNORECASE),
    re.compile(r"(.)\1{3,}"),
    re.compile(r"^[a-z]{1,2}\d{7,}$", re.IGNORECASE),
    re.compile(r"^user\d+$", re.IGNORECASE),
    re.compile(r"^[A-Z][a-z]{2,5}_\d{3,}$"),
    re.compile(r"^[a-z]{20,}$"),
]


def _is_random_mixed_case(username: str) -> bool:
    """Рандомный mixed-case: gGWybIj — короткие бессмысленные сегменты."""
    alpha_only = "".join(c for c in username if c.isalpha())
    if len(alpha_only) < 6:
        return False
    segments = re.findall(r"[A-Z][a-z]*|[a-z]+", alpha_only)
    if len(segments) < 3:
        return False
    avg_len = sum(len(s) for s in segments) / len(segments)
    short = sum(1 for s in segments if len(s) <= 2)
    # Рандом: средняя длина сегмента < 2.5 и большинство коротких
    return avg_len < 2.5 and short >= len(segments) * 0.5


def score_username(username: str | None) -> int:
    if username is None:
        return 0
    for pattern in SUSPICIOUS_USERNAME_PATTERNS:
        if pattern.search(username):
            return -25
    if _is_random_mixed_case(username):
        return -25
    return 10


def score_name(first_name: str, last_name: str | None) -> int:
    full = f"{first_name} {last_name}".strip() if last_name else first_name.strip()
    if not full:
        return -20

    alpha_count = sum(1 for ch in full if ch.isalpha())
    digit_count = sum(1 for ch in full if ch.isdigit())
    total = len(full.replace(" ", ""))

    if total == 0:
        return -20
    if alpha_count == 0:
        return -15
    if digit_count > alpha_count:
        return -10
    if alpha_count == 1:
        return -5

    # Детекция «бессмысленных» имён: нет гласных в длинных строках
    vowels = set("aeiouаеёиоуыэюяAEIOUАЕЁИОУЫЭЮЯ")
    alpha_chars = [ch for ch in full if ch.isalpha()]
    if len(alpha_chars) > 5:
        vowel_ratio = sum(1 for ch in alpha_chars if ch in vowels) / len(alpha_chars)
        if vowel_ratio < 0.1:
            return -10

    return 5


async def score_profile_photo(user: User) -> int:
    try:
        photos = await user.get_profile_photos(limit=1)
        return 15 if photos.total_count > 0 else 0
    except Exception:
        return 0


def score_premium(user: User) -> int:
    # Premium легко купить, боты тоже бывают Premium
    return 5 if getattr(user, "is_premium", False) else 0


# ── CAS (Combot Anti-Spam) проверка ──────────────────────────

async def check_cas(user_id: int) -> dict:
    """Проверка пользователя в базе CAS (Combot Anti-Spam)."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"https://api.cas.chat/check?user_id={user_id}")
            data = resp.json()
            return data
    except Exception:
        return {"ok": False}


def score_cas(cas_data: dict) -> int:
    """CAS бан = -50 (жёсткий сигнал)."""
    if cas_data.get("ok"):  # ok=True значит пользователь В базе спамеров
        return -50
    return 0


# ── SpamWatch проверка ───────────────────────────────────────

async def check_spamwatch(user_id: int) -> bool:
    """Проверка пользователя в базе SpamWatch. Возвращает True если забанен."""
    if not SPAMWATCH_TOKEN:
        return False
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(
                f"https://api.spamwat.ch/banlist/{user_id}",
                headers={"Authorization": f"Bearer {SPAMWATCH_TOKEN}"},
            )
            return resp.status_code == 200  # 200 = забанен, 404 = чист
    except Exception:
        return False


def score_spamwatch(is_banned: bool) -> int:
    return -50 if is_banned else 0


# ── Детекция массовых заявок (burst) ──────────────────────────

# Хранилище последних заявок: список timestamp
_recent_requests: list[float] = []
BURST_WINDOW_SEC = int(os.getenv("BURST_WINDOW_SEC", "120"))  # окно 2 мин
BURST_THRESHOLD = int(os.getenv("BURST_THRESHOLD", "3"))       # 3+ заявки = burst


def score_burst() -> int:
    """Если за последние BURST_WINDOW пришло >= BURST_THRESHOLD заявок — подозрительно."""
    now = datetime.now(timezone.utc).timestamp()
    # Очистка старых записей
    while _recent_requests and now - _recent_requests[0] > BURST_WINDOW_SEC:
        _recent_requests.pop(0)
    _recent_requests.append(now)

    if len(_recent_requests) >= BURST_THRESHOLD:
        return -15  # Массовое вступление — подозрительно
    return 0


# ── Оценка пользователя ──────────────────────────────────────

async def evaluate_user(user: User) -> dict:
    age_days = estimate_account_age_days(user.id)
    cas_data = await check_cas(user.id)
    sw_banned = await check_spamwatch(user.id)

    scores = {
        "age": score_account_age(age_days),
        "photo": await score_profile_photo(user),
        "username": score_username(user.username),
        "name": score_name(user.first_name, user.last_name),
        "premium": score_premium(user),
        "cas": score_cas(cas_data),
        "spamwatch": score_spamwatch(sw_banned),
        "burst": score_burst(),
    }
    total = sum(scores.values())

    # Жёсткие правила
    if cas_data.get("ok") or sw_banned:
        decision = "decline"
    elif age_days < 7:
        decision = "decline"
    elif total >= SCORE_APPROVE:
        decision = "approve"
    elif total < SCORE_DECLINE:
        decision = "decline"
    else:
        decision = "review"

    return {
        "total": total,
        "scores": scores,
        "decision": decision,
        "age_days": age_days,
        "cas_banned": cas_data.get("ok", False),
        "sw_banned": sw_banned,
    }


# ── Уведомление админа ───────────────────────────────────────

SCORE_LABELS = {
    "age": "Возраст",
    "photo": "Фото",
    "username": "Username",
    "name": "Имя",
    "premium": "Premium",
    "cas": "CAS антиспам",
    "spamwatch": "SpamWatch",
    "burst": "Массовость",
}

DECISION_EMOJI = {
    "approve": "✅",
    "decline": "🚫",
    "review": "⏳",
}

DECISION_TEXT = {
    "approve": "ОДОБРЕН",
    "decline": "ОТКЛОНЁН",
    "review": "ОЖИДАЕТ РЕВЬЮ",
}


async def notify_admin(
    context: ContextTypes.DEFAULT_TYPE, user: User, result: dict,
):
    if not ADMIN_CHAT_ID:
        return
    try:
        name = user.full_name
        uname = f"@{user.username}" if user.username else "нет"
        scores = result["scores"]
        decision = result["decision"]
        emoji = DECISION_EMOJI.get(decision, "❓")
        action = DECISION_TEXT.get(decision, decision.upper())

        if TEST_MODE:
            if decision == "approve":
                action = "РЕКОМЕНДУЮ ОДОБРИТЬ"
            elif decision == "decline":
                action = "РЕКОМЕНДУЮ ОТКЛОНИТЬ"
            else:
                action = "НЕ УВЕРЕН — РЕШАЙ САМ"

        lines = [
            f"{emoji} Заявка — {action}",
            "",
            f"Имя: {name}",
            f"Username: {uname}",
            f"ID: {user.id}",
            f"Возраст аккаунта: ~{result['age_days']} дн.",
            f"CAS: {'🚨 ЗАБАНЕН' if result.get('cas_banned') else '✔️ чист'}",
            f"SpamWatch: {'🚨 ЗАБАНЕН' if result.get('sw_banned') else '✔️ чист'}",
            f"Итого: {result['total']}",
            "",
            "Баллы:",
        ]
        for key, val in scores.items():
            sign = "+" if val > 0 else ""
            lines.append(f"  {SCORE_LABELS.get(key, key)}: {sign}{val}")

        if TEST_MODE:
            lines.append("")
            lines.append("⚙️ Тестовый режим — реального отклонения нет")

        # В тестовом режиме добавляем кнопки для ручного решения
        keyboard = None
        if TEST_MODE:
            chat_id_for_btn = result.get("chat_id", "")
            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "✅ Одобрить",
                        callback_data=f"approve_{user.id}_{chat_id_for_btn}",
                    ),
                    InlineKeyboardButton(
                        "🚫 Отклонить",
                        callback_data=f"decline_{user.id}_{chat_id_for_btn}",
                    ),
                ]
            ])

        await context.bot.send_message(
            chat_id=int(ADMIN_CHAT_ID),
            text="\n".join(lines),
            reply_markup=keyboard,
        )
    except Exception as e:
        logger.warning("Не удалось отправить уведомление админу: %s", e)


# ── Обработчик заявок ─────────────────────────────────────────

async def handle_join_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    req = update.chat_join_request
    user = req.from_user

    # Белый список
    if user.id in WHITELIST_IDS:
        await req.approve()
        logger.info("WHITELIST: %s (ID: %d) — одобрен", user.full_name, user.id)
        return

    # Удалённый аккаунт — тихое автоотклонение
    if not user.first_name and not user.last_name and not user.username:
        await req.decline()
        logger.info("AUTO-DECLINE: Deleted Account id=%d — отклонён", user.id)
        return

    result = await evaluate_user(user)
    result["chat_id"] = req.chat.id
    scores = result["scores"]
    decision = result["decision"]

    score_str = " ".join(f"{k}={v:+d}" for k, v in scores.items())
    logger.info(
        "ЗАЯВКА: user=%r id=%d username=%s",
        user.full_name, user.id, user.username or "нет",
    )
    logger.info(
        "  Баллы: %s | Итого: %d | Возраст: ~%d дн. | Решение: %s",
        score_str, result["total"], result["age_days"], decision.upper(),
    )

    # Аккаунт 0 дней — тихое автоотклонение
    if result["age_days"] < 1 and decision == "decline":
        await req.decline()
        logger.info("  AUTO-DECLINE: аккаунт 0 дней — отклонён автоматически")
        return

    if TEST_MODE:
        # Тестовый режим: ничего не делаем автоматически, всё через кнопки
        logger.info("  TEST MODE: решение %s — ждём кнопку от админа", decision.upper())
        await notify_admin(context, user, result)
    elif decision == "approve":
        await req.approve()
        await notify_admin(context, user, result)
    elif decision == "decline":
        await req.decline()
        await notify_admin(context, user, result)
    else:
        # review — оставляем заявку и уведомляем админа
        await notify_admin(context, user, result)


# ── Автобан новых участников (группы без заявок) ──────────────

async def handle_chat_member_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Обработка chat_member updates — надёжный способ отлавливать
    новых участников в супергруппах (AUTOBAN_CHATS).
    Срабатывает когда статус пользователя меняется на member.
    """
    chat = update.effective_chat
    member_update = update.chat_member
    if not member_update:
        return

    # Только если пользователь СТАЛ участником (was not member -> became member)
    old_status = member_update.old_chat_member.status
    new_status = member_update.new_chat_member.status
    if new_status not in ("member", "restricted") or old_status in ("member", "administrator", "creator", "restricted"):
        return

    chat_str = str(chat.id)
    chat_uname = f"@{chat.username}" if chat.username else ""
    chat_label = chat_uname or chat.title or chat_str
    user = member_update.new_chat_member.user
    logger.info("CHAT_MEMBER_UPDATE: %s вступил в %s (id=%s)", user.full_name, chat_label, chat_str)

    # Проверяем, включена ли группа в автобан
    if chat_str not in AUTOBAN_CHATS and chat_uname not in AUTOBAN_CHATS:
        return

    users_to_check = [user]
    for user in users_to_check:
        if user.is_bot:
            continue
        if user.id in WHITELIST_IDS:
            logger.info("AUTOBAN: %s (ID: %d) — в белом списке, пропускаю",
                        user.full_name, user.id)
            continue

        # Удалённый аккаунт — сразу кик без скоринга
        if not user.first_name and not user.last_name and not user.username:
            chat_label = chat_uname or chat.title or chat_str
            logger.info("AUTOBAN [%s]: Deleted Account id=%d — кик", chat_label, user.id)
            try:
                await context.bot.ban_chat_member(chat_id=chat.id, user_id=user.id)
                await context.bot.unban_chat_member(chat_id=chat.id, user_id=user.id)
            except Exception as e:
                logger.error("AUTOBAN: не удалось кикнуть deleted %d: %s", user.id, e)
            continue

        # Быстрый скоринг (возраст + имя + username + CAS)
        age_days = estimate_account_age_days(user.id)
        s_age = score_account_age(age_days)
        s_uname = score_username(user.username)
        s_name = score_name(user.first_name, user.last_name)
        s_prem = score_premium(user)
        total = s_age + s_uname + s_name + s_prem

        # CAS для подозрительных
        cas_banned = False
        if age_days < 30 or total < 0:
            cas_data = await check_cas(user.id)
            cas_banned = cas_data.get("ok", False)
            if cas_banned:
                total -= 50

        # Решение
        if cas_banned or age_days < 7 or total < SCORE_DECLINE:
            decision = "ban"
        else:
            decision = "ok"

        chat_label = chat_uname or chat.title or chat_str
        logger.info(
            "AUTOBAN [%s]: user=%r id=%d age=~%dд total=%d → %s",
            chat_label, user.full_name, user.id, age_days, total, decision,
        )

        if decision == "ban":
            try:
                await context.bot.ban_chat_member(
                    chat_id=chat.id, user_id=user.id,
                )
                await context.bot.unban_chat_member(
                    chat_id=chat.id, user_id=user.id,
                )
                logger.info("AUTOBAN: удалён %d из %s", user.id, chat_label)
            except Exception as e:
                logger.error("AUTOBAN: не удалось забанить %d: %s", user.id, e)

            # Уведомление админу
            if ADMIN_CHAT_ID:
                name = user.full_name or "(пусто)"
                uname = f"@{user.username}" if user.username else "нет"
                cas_flag = " | CAS: 🚨" if cas_banned else ""
                text = (
                    f"🤖 Автобан в {chat_label}\n\n"
                    f"Имя: {name}\n"
                    f"Username: {uname}\n"
                    f"ID: {user.id}\n"
                    f"Возраст: ~{age_days} дн.\n"
                    f"Балл: {total}{cas_flag}\n\n"
                    f"Удалён автоматически."
                )
                try:
                    await context.bot.send_message(
                        chat_id=int(ADMIN_CHAT_ID), text=text,
                    )
                except Exception as e:
                    logger.error("AUTOBAN: не удалось уведомить: %s", e)


# ── Команды для отладки ────────────────────────────────────────

def format_report(user: User, result: dict) -> str:
    """Форматирует подробный отчёт по пользователю."""
    scores = result["scores"]
    decision = result["decision"]
    emoji = DECISION_EMOJI.get(decision, "❓")
    action = DECISION_TEXT.get(decision, decision.upper())

    lines = [
        f"{emoji} Проверка пользователя",
        "",
        f"Имя: {user.full_name}",
        f"Username: @{user.username}" if user.username else "Username: нет",
        f"ID: {user.id}",
        f"Premium: {'да' if getattr(user, 'is_premium', False) else 'нет'}",
        f"Возраст аккаунта: ~{result['age_days']} дн.",
        f"CAS: {'🚨 ЗАБАНЕН' if result.get('cas_banned') else '✔️ чист'}",
        f"SpamWatch: {'🚨 ЗАБАНЕН' if result.get('sw_banned') else '✔️ чист'}",
        "",
        "━━━ Разбивка баллов ━━━",
    ]
    for key, val in scores.items():
        sign = "+" if val > 0 else ""
        label = SCORE_LABELS.get(key, key)
        lines.append(f"  {label}: {sign}{val}")
    lines.append(f"  ─────────────")
    lines.append(f"  ИТОГО: {result['total']}")
    lines.append("")
    lines.append(f"Решение: {action}")
    lines.append(f"(порог approve: >={SCORE_APPROVE}, decline: <{SCORE_DECLINE})")

    if TEST_MODE:
        lines.append("")
        lines.append("⚙️ Тестовый режим")

    return "\n".join(lines)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ответ на /start. Если ?start=join — запускаем анкету вступления."""
    args = context.args
    if args and args[0] == "join":
        await update.message.reply_text(
            "👋 Привет! Рады видеть тебя в Бизнес-Клубе Самуи.\n\n"
            "Чтобы вступить, ответь на 3 коротких вопроса.\n\n"
            "<b>Вопрос 1/3:</b> Как тебя зовут и чем ты занимаешься?",
            parse_mode="HTML",
        )
        return JOIN_NAME
    await update.message.reply_text(
        "Привет! Я модератор заявок для чата BKS Samui.\n\n"
        "Команды:\n"
        "/check — проверить себя\n"
        "/check <user_id> — проверить по ID\n"
        "/scan — сканировать группу\n"
        "/status — настройки бота\n\n"
        "Или просто перешли мне сообщение — проверю отправителя."
    )
    return ConversationHandler.END


async def cmd_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /check — проверить себя
    /check 123456789 — проверить пользователя по ID
    """
    if context.args:
        arg = context.args[0]
        # Поддержка @username и числового ID
        target = arg.lstrip("@") if arg.startswith("@") else arg
        try:
            # Пробуем как число или как username
            lookup = int(target) if target.isdigit() else f"@{target}"
            chat = await context.bot.get_chat(lookup)
            user = User(
                id=chat.id,
                is_bot=False,
                first_name=chat.first_name or "",
                last_name=chat.last_name,
                username=chat.username,
            )
            user.set_bot(context.bot)
        except Exception as e:
            await update.message.reply_text(
                f"Не удалось найти: {arg}\n"
                "Бот может найти только тех, кто ему уже писал.\n"
                "Попробуй /check <числовой_id>"
            )
            return
    else:
        user = update.effective_user

    result = await evaluate_user(user)
    report = format_report(user, result)
    await update.message.reply_text(report)


async def _fetch_all_members(chat_id_or_username: str) -> list[dict]:
    """Получает ВСЕХ участников через Telethon (MTProto API)."""
    from telethon import TelegramClient
    from telethon.tl.functions.channels import GetParticipantsRequest
    from telethon.tl.types import ChannelParticipantsSearch, PeerChannel

    if not API_ID or not API_HASH:
        raise ValueError(
            "API_ID и API_HASH не заданы в .env!\n"
            "Получить: https://my.telegram.org → API development tools"
        )

    client = TelegramClient("bot_session", int(API_ID), API_HASH)
    await client.start(bot_token=BOT_TOKEN)

    try:
        # Поддержка числового ID и @username
        if chat_id_or_username.lstrip("-").isdigit():
            cid = int(chat_id_or_username)
            # Telegram MTProto: supergroup/channel ID без -100 префикса
            if cid < 0:
                cid = int(str(cid).replace("-100", "", 1))
            entity = await client.get_entity(PeerChannel(cid))
        else:
            entity = await client.get_entity(chat_id_or_username)
        all_users = []
        offset = 0
        batch = 200

        while True:
            participants = await client(GetParticipantsRequest(
                channel=entity,
                filter=ChannelParticipantsSearch(""),
                offset=offset,
                limit=batch,
                hash=0,
            ))
            if not participants.users:
                break
            for u in participants.users:
                all_users.append({
                    "id": u.id,
                    "first_name": u.first_name or "",
                    "last_name": u.last_name,
                    "username": u.username,
                    "is_bot": u.bot or False,
                    "is_premium": getattr(u, "premium", False) or False,
                    "deleted": getattr(u, "deleted", False) or False,
                })
            offset += len(participants.users)
            if offset >= participants.count:
                break

        return all_users
    finally:
        await client.disconnect()


async def cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /scan — сканировать ВСЕХ участников чата @samuibiz.
    /scan <chat_username> — сканировать указанный чат.
    Использует Telethon (MTProto) для получения полного списка.
    """
    if context.args:
        target = context.args[0]
        if not target.startswith("@"):
            target = f"@{target}"
    else:
        target = "@samuibiz"

    await update.message.reply_text(
        f"Сканирую ВСЕХ участников {target}...\n"
        "Это может занять некоторое время."
    )

    # Получаем полный список через Telethon
    try:
        members = await _fetch_all_members(target)
    except ValueError as e:
        await update.message.reply_text(str(e))
        return
    except Exception as e:
        logger.error("Ошибка Telethon: %s", e)
        await update.message.reply_text(f"Ошибка получения участников: {e}")
        return

    # Фильтруем ботов
    humans = [m for m in members if not m["is_bot"]]
    bots = [m for m in members if m["is_bot"]]

    suspicious = []
    clean = []
    checked = 0

    # Прогресс
    total = len(humans)
    progress_msg = await update.message.reply_text(f"Проверяю {total} участников...")

    for m in humans:
        # Создаём User-объект для evaluate_user
        user = User(
            id=m["id"],
            is_bot=False,
            first_name=m["first_name"],
            last_name=m.get("last_name"),
            username=m.get("username"),
        )
        user.set_bot(context.bot)
        # Ставим premium вручную
        if m.get("is_premium"):
            user._is_premium = True

        result = await evaluate_user(user)
        entry = {"user_data": m, "result": result}

        if result["decision"] in ("decline", "review"):
            suspicious.append(entry)
        else:
            clean.append(entry)

        checked += 1
        # Обновляем прогресс каждые 20 человек
        if checked % 20 == 0:
            try:
                await progress_msg.edit_text(
                    f"Проверяю... {checked}/{total}"
                )
            except Exception:
                pass

    try:
        await progress_msg.delete()
    except Exception:
        pass

    # ── Формируем отчёт ──
    lines = [
        f"📊 Полное сканирование {target}",
        f"Всего: {len(members)} (людей: {len(humans)}, ботов: {len(bots)})",
        "",
    ]

    if suspicious:
        lines.append(f"🔴 Подозрительных: {len(suspicious)}")
        lines.append("")
        # Сортируем по баллам (худшие первые)
        suspicious.sort(key=lambda e: e["result"]["total"])
        for entry in suspicious:
            m = entry["user_data"]
            r = entry["result"]
            emoji = DECISION_EMOJI.get(r["decision"], "❓")
            name = f"{m['first_name']} {m.get('last_name') or ''}".strip()
            uname = f"@{m['username']}" if m.get("username") else "нет"
            cas_flag = " 🚨CAS" if r.get("cas_banned") else ""
            sw_flag = " 🚨SW" if r.get("sw_banned") else ""
            lines.append(
                f"{emoji} {name} ({uname})\n"
                f"   ID: {m['id']} | ~{r['age_days']}дн | "
                f"балл: {r['total']}{cas_flag}{sw_flag}"
            )
            # Детали баллов
            scores = r["scores"]
            details = []
            for key, val in scores.items():
                if val != 0:
                    sign = "+" if val > 0 else ""
                    details.append(f"{SCORE_LABELS.get(key, key)}={sign}{val}")
            lines.append(f"   [{', '.join(details)}]")
            lines.append("")
    else:
        lines.append("✅ Подозрительных не найдено!")
        lines.append("")

    lines.append(f"✅ Чистых: {len(clean)}")

    # Отправляем, разбивая на части если надо
    text = "\n".join(lines)
    chunks = []
    while text:
        if len(text) <= 4000:
            chunks.append(text)
            break
        # Ищем последний перенос строки до 4000
        cut = text.rfind("\n", 0, 4000)
        if cut == -1:
            cut = 4000
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")

    for chunk in chunks:
        await update.message.reply_text(chunk)


async def cmd_purge(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /purge [@chat] — показать явных ботов с кнопками Удалить/Пропустить.
    /purge — по умолчанию @samuibiz.
    """
    if context.args:
        target = context.args[0]
        if not target.startswith("@") and not target.startswith("-"):
            target = f"@{target}"
    else:
        target = "@samuibiz"
    admin_chat = update.effective_chat.id

    status_msg = await update.message.reply_text(
        "⏳ Загружаю список участников..."
    )

    try:
        members = await _fetch_all_members(target)
    except Exception as e:
        logger.exception("purge: ошибка загрузки участников")
        await status_msg.edit_text(f"Ошибка загрузки: {e}")
        return

    # Получаем chat_id группы через Bot API
    try:
        chat_arg = int(target) if target.lstrip("-").isdigit() else target
        chat = await context.bot.get_chat(chat_arg)
        group_chat_id = chat.id
        chat_title = chat.title or target
    except Exception as e:
        await status_msg.edit_text(f"Не удалось получить chat_id: {e}")
        return

    humans = [m for m in members if not m["is_bot"]]
    await status_msg.edit_text(
        f"⏳ Загружено {len(humans)} участников. Скоринг..."
    )

    # Отбираем только явных ботов: плохой скоринг → DECLINE
    candidates = []
    for m in humans:
        age = estimate_account_age_days(m["id"])
        s_age = score_account_age(age)
        s_uname = score_username(m.get("username"))
        s_name = score_name(m["first_name"] or "", m.get("last_name"))
        s_prem = 5 if m.get("is_premium") else 0
        total = s_age + s_uname + s_name + s_prem

        if age < 7:
            decision = "decline"
        elif total >= SCORE_APPROVE:
            continue
        elif total < SCORE_DECLINE:
            decision = "decline"
        else:
            continue  # review — не трогаем в purge

        candidates.append({
            "m": m, "age": age, "total": total,
            "scores": {"age": s_age, "uname": s_uname, "name": s_name, "prem": s_prem},
        })

    if not candidates:
        await status_msg.edit_text("Явных ботов не найдено!")
        return

    await status_msg.edit_text(
        f"⏳ Найдено {len(candidates)} кандидатов. Проверяю CAS..."
    )

    # CAS проверки параллельно (все сразу)
    cas_results = await asyncio.gather(
        *(check_cas(c["m"]["id"]) for c in candidates),
        return_exceptions=True,
    )
    for c, cas in zip(candidates, cas_results):
        c["cas"] = cas if isinstance(cas, bool) else False

    candidates.sort(key=lambda r: r["total"])

    await status_msg.edit_text(
        f"Найдено {len(candidates)} подозрительных. Отправляю карточки..."
    )

    for r in candidates:
        m = r["m"]
        name = f"{m['first_name'] or ''} {m.get('last_name') or ''}".strip() or "(пусто)"
        uname = f"@{m['username']}" if m.get("username") else "нет"
        cas_flag = "\nCAS: 🚨 ЗАБАНЕН" if r["cas"] else ""

        sc = r["scores"]
        details = []
        for k, v in [("Возраст", sc["age"]), ("Username", sc["uname"]),
                      ("Имя", sc["name"]), ("Premium", sc["prem"])]:
            if v != 0:
                details.append(f"{k}={v:+d}")

        text = (
            f"🚫 {name} ({uname})\n"
            f"ID: {m['id']}\n"
            f"Возраст: ~{r['age']} дн.\n"
            f"Балл: {r['total']}\n"
            f"[{', '.join(details)}]"
            f"{cas_flag}"
        )

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🗑 Удалить",
                    callback_data=f"ban_{m['id']}_{group_chat_id}",
                ),
                InlineKeyboardButton(
                    "⏭ Пропустить",
                    callback_data=f"skip_{m['id']}_{group_chat_id}",
                ),
            ]
        ])

        try:
            await context.bot.send_message(
                chat_id=admin_chat,
                text=text,
                reply_markup=keyboard,
            )
        except Exception as e:
            logger.error("purge: не удалось отправить карточку %d: %s", m["id"], e)

    await context.bot.send_message(
        chat_id=admin_chat,
        text=f"✅ Готово! {len(candidates)} карточек отправлено.\n"
             "Нажимай кнопки для каждого.",
    )


async def cmd_audit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /audit [@chat] — проверить ВСЕХ участников по CAS + SpamWatch.
    Показывает только тех, кто найден в чёрных списках.
    """
    if context.args:
        target = context.args[0]
        if not target.startswith("@") and not target.startswith("-"):
            target = f"@{target}"
    else:
        target = "@samuibiz"
    admin_chat = update.effective_chat.id

    status_msg = await update.message.reply_text(
        "🔍 Загружаю список участников для аудита..."
    )

    try:
        members = await _fetch_all_members(target)
    except Exception as e:
        logger.exception("audit: ошибка загрузки участников")
        await status_msg.edit_text(f"Ошибка загрузки: {e}")
        return

    # Получаем chat_id группы
    try:
        chat_arg = int(target) if target.lstrip("-").isdigit() else target
        chat = await context.bot.get_chat(chat_arg)
        group_chat_id = chat.id
        chat_title = chat.title or target
    except Exception as e:
        await status_msg.edit_text(f"Не удалось получить chat_id: {e}")
        return

    humans = [m for m in members if not m["is_bot"]]
    total_count = len(humans)
    await status_msg.edit_text(
        f"🔍 Проверяю {total_count} участников по CAS + SpamWatch...\n"
        "Это может занять пару минут."
    )

    # Проверяем CAS и SpamWatch параллельно, батчами по 50
    BATCH = 50
    flagged = []
    checked = 0

    for i in range(0, len(humans), BATCH):
        batch = humans[i:i + BATCH]
        cas_tasks = [check_cas(m["id"]) for m in batch]
        sw_tasks = [check_spamwatch(m["id"]) for m in batch]

        cas_results = await asyncio.gather(*cas_tasks, return_exceptions=True)
        sw_results = await asyncio.gather(*sw_tasks, return_exceptions=True)

        for m, cas, sw in zip(batch, cas_results, sw_results):
            cas_banned = cas if isinstance(cas, bool) else False
            sw_banned = sw if isinstance(sw, bool) else False
            if cas_banned or sw_banned:
                flagged.append({
                    "m": m,
                    "cas": cas_banned,
                    "sw": sw_banned,
                })

        checked += len(batch)
        if checked % 200 == 0 or checked == total_count:
            try:
                await status_msg.edit_text(
                    f"🔍 Проверено {checked}/{total_count}... "
                    f"Найдено: {len(flagged)} в чёрных списках"
                )
            except Exception:
                pass  # rate limit

    if not flagged:
        await status_msg.edit_text(
            f"✅ Аудит завершён: {total_count} участников проверено.\n"
            "Никто не найден в CAS или SpamWatch."
        )
        return

    await status_msg.edit_text(
        f"⚠️ Найдено {len(flagged)} участников в чёрных списках!\n"
        "Отправляю карточки..."
    )

    for r in flagged:
        m = r["m"]
        name = f"{m['first_name'] or ''} {m.get('last_name') or ''}".strip() or "(пусто)"
        uname = f"@{m['username']}" if m.get("username") else "нет"

        flags = []
        if r["cas"]:
            flags.append("CAS: 🚨 ЗАБАНЕН")
        if r["sw"]:
            flags.append("SpamWatch: 🚨 ЗАБАНЕН")

        age = estimate_account_age_days(m["id"])

        text = (
            f"🚨 {name} ({uname})\n"
            f"ID: {m['id']}\n"
            f"Возраст: ~{age} дн.\n"
            f"{chr(10).join(flags)}"
        )

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🗑 Удалить",
                    callback_data=f"ban_{m['id']}_{group_chat_id}",
                ),
                InlineKeyboardButton(
                    "⏭ Пропустить",
                    callback_data=f"skip_{m['id']}_{group_chat_id}",
                ),
            ]
        ])

        try:
            await context.bot.send_message(
                chat_id=admin_chat, text=text, reply_markup=keyboard,
            )
        except Exception as e:
            logger.error("audit: не удалось отправить карточку %d: %s", m["id"], e)

    await context.bot.send_message(
        chat_id=admin_chat,
        text=f"✅ Аудит завершён!\n"
             f"Проверено: {total_count}\n"
             f"В чёрных списках: {len(flagged)}\n"
             "Нажимай кнопки для каждого.",
    )


async def cmd_cleanup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /cleanup — удалить все «Deleted Account» из всех трёх групп.
    /cleanup <chat> — только из указанной группы.
    """
    if context.args:
        target = context.args[0]
        if not target.startswith("@") and not target.startswith("-"):
            target = f"@{target}"
        targets = [target]
    else:
        # Все три группы: samuibiz + обе из AUTOBAN_CHATS
        targets = ["@samuibiz"] + list(AUTOBAN_CHATS)

    admin_chat = update.effective_chat.id
    total_kicked = 0

    for target in targets:
        # Название группы
        try:
            chat_arg = int(target) if target.lstrip("-").isdigit() else target
            chat_obj = await context.bot.get_chat(chat_arg)
            chat_title = chat_obj.title or target
            group_chat_id = chat_obj.id
        except Exception as e:
            await context.bot.send_message(
                chat_id=admin_chat, text=f"Не удалось получить {target}: {e}",
            )
            continue

        status_msg = await context.bot.send_message(
            chat_id=admin_chat,
            text=f"⏳ [{chat_title}] Загружаю участников...",
        )

        try:
            members = await _fetch_all_members(target)
        except Exception as e:
            logger.exception("cleanup: ошибка загрузки %s", target)
            await status_msg.edit_text(f"[{chat_title}] Ошибка: {e}")
            continue

        deleted = [m for m in members if m["deleted"]]

        if not deleted:
            await status_msg.edit_text(f"✅ [{chat_title}] Удалённых аккаунтов нет.")
            continue

        await status_msg.edit_text(
            f"⏳ [{chat_title}] Найдено {len(deleted)} удалённых. Кикаю..."
        )

        kicked = 0
        for m in deleted:
            try:
                await context.bot.ban_chat_member(
                    chat_id=group_chat_id, user_id=m["id"],
                )
                await context.bot.unban_chat_member(
                    chat_id=group_chat_id, user_id=m["id"],
                )
                kicked += 1
            except Exception as e:
                logger.error("cleanup: не удалось кикнуть %d из %s: %s",
                             m["id"], chat_title, e)

        total_kicked += kicked
        await status_msg.edit_text(
            f"🧹 [{chat_title}] Удалено {kicked}/{len(deleted)} удалённых аккаунтов."
        )
        logger.info("CLEANUP: %s — удалено %d/%d deleted accounts",
                     chat_title, kicked, len(deleted))

    await context.bot.send_message(
        chat_id=admin_chat,
        text=f"✅ Cleanup завершён. Всего удалено: {total_kicked}",
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/status — показать текущие настройки."""
    chat = update.effective_chat
    logger.info("CMD /status: chat=%s (id=%s)", chat.title or chat.username or "DM", chat.id)
    mode = "ТЕСТОВЫЙ" if TEST_MODE else "БОЕВОЙ"
    lines = [
        f"Режим: {mode}",
        f"Порог одобрения: >= {SCORE_APPROVE}",
        f"Порог отклонения: < {SCORE_DECLINE}",
        f"Белый список: {len(WHITELIST_IDS)} ID",
        f"Уведомления админу: {'да' if ADMIN_CHAT_ID else 'нет (ADMIN_CHAT_ID не задан)'}",
    ]
    await update.message.reply_text("\n".join(lines))


# ── Обработчик пересланных сообщений ──────────────────────────

async def handle_forwarded(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Пересланное сообщение — проверяем отправителя."""
    msg = update.message
    fwd = msg.forward_origin

    if fwd is None:
        return

    # forward_origin может быть разных типов
    from telegram import MessageOriginUser
    if not isinstance(fwd, MessageOriginUser):
        await msg.reply_text(
            "Не удалось определить пользователя — "
            "скорее всего у него скрыт профиль при пересылке.\n"
            "Попробуй /check <user_id>"
        )
        return

    sender = fwd.sender_user
    user = User(
        id=sender.id,
        is_bot=sender.is_bot,
        first_name=sender.first_name or "",
        last_name=sender.last_name,
        username=sender.username,
    )
    user.set_bot(context.bot)

    result = await evaluate_user(user)
    report = format_report(user, result)
    await msg.reply_text(report)


# ── Обработчик кнопок (approve/decline) ───────────────────────

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка нажатий на inline-кнопки одобрить/отклонить."""
    query = update.callback_query
    await query.answer()

    data = query.data

    # Обрабатываем кнопки анкеты вступления отдельно
    if data.startswith("join_approve_") or data.startswith("join_decline_"):
        applicant_id = int(data.split("_")[2])
        if data.startswith("join_approve_"):
            try:
                invite = await context.bot.create_chat_invite_link(
                    chat_id="@samuibiz",
                    member_limit=1,
                    name=f"join_{applicant_id}",
                )
                await context.bot.send_message(
                    chat_id=applicant_id,
                    text=(
                        "🎉 Твоя заявка одобрена!\n\n"
                        f"Вот твоя персональная ссылка для вступления:\n{invite.invite_link}\n\n"
                        "Ссылка одноразовая — не передавай её другим."
                    ),
                )
                await query.edit_message_text(query.message.text + "\n\n✅ ОДОБРЕНО — ссылка отправлена")
                logger.info("JOIN: одобрен user_id=%d", applicant_id)
            except Exception as e:
                await query.edit_message_text(query.message.text + f"\n\n❌ Ошибка: {e}")
                logger.error("JOIN: ошибка одобрения %d: %s", applicant_id, e)
        else:
            try:
                await context.bot.send_message(
                    chat_id=applicant_id,
                    text=(
                        "К сожалению, твоя заявка не была одобрена.\n"
                        "Если есть вопросы — напиши @Natali_na_Samui."
                    ),
                )
                await query.edit_message_text(query.message.text + "\n\n❌ ОТКЛОНЕНО")
                logger.info("JOIN: отклонён user_id=%d", applicant_id)
            except Exception as e:
                await query.edit_message_text(query.message.text + f"\n\n❌ Ошибка: {e}")
        _join_pending.pop(applicant_id, None)
        return

    # Остальные кнопки: approve/decline/ban/skip
    # "approve_USERID_CHATID" или "decline_USERID_CHATID"
    parts = data.split("_", 2)
    if len(parts) < 3:
        return

    action, user_id_str, chat_id_str = parts
    try:
        user_id = int(user_id_str)
        chat_id = int(chat_id_str)
    except ValueError:
        return

    try:
        if action == "approve":
            await context.bot.approve_chat_join_request(
                chat_id=chat_id, user_id=user_id,
            )
            await query.edit_message_text(
                query.message.text + "\n\n✅ ОДОБРЕНО вручную",
            )
            logger.info("КНОПКА: одобрен user_id=%d chat_id=%d", user_id, chat_id)
        elif action == "decline":
            await context.bot.decline_chat_join_request(
                chat_id=chat_id, user_id=user_id,
            )
            await query.edit_message_text(
                query.message.text + "\n\n🚫 ОТКЛОНЕНО вручную",
            )
            logger.info("КНОПКА: отклонён user_id=%d chat_id=%d", user_id, chat_id)
        elif action == "ban":
            # Кик из группы: ban + unban = удаление без перманентного бана
            await context.bot.ban_chat_member(
                chat_id=chat_id, user_id=user_id,
            )
            await context.bot.unban_chat_member(
                chat_id=chat_id, user_id=user_id,
            )
            await query.edit_message_text(
                query.message.text + "\n\n🗑 УДАЛЁН из группы",
            )
            logger.info("КНОПКА: удалён user_id=%d chat_id=%d", user_id, chat_id)
        elif action == "skip":
            await query.edit_message_text(
                query.message.text + "\n\n⏭ Пропущен",
            )
            logger.info("КНОПКА: пропущен user_id=%d", user_id)
    except Exception as e:
        await query.edit_message_text(
            query.message.text + f"\n\n❌ Ошибка: {e}",
        )
        logger.warning("КНОПКА ошибка: %s", e)


# ── Еженедельное donation-напоминание (среда 11:30) ──────────

DONATION_TEXT = (
    "<b>Чтобы двигаться дальше, клубу нужен регулярный бюджет.</b>\n"
    "Эти деньги пойдут на продвижение клуба: SMM, SEO, рекламу, "
    "настройку Google / Facebook / Яндекс, а также на привлечение "
    "спикеров и развитие соцсетей.\n\n"
    "<b>QR-код для donation выше.</b>\n"
    "На встречах также стоит коробка для donation.\n\n"
    "<b>Поддержка полностью добровольная</b> — каждый участвует "
    "по желанию и по возможности.\n\n"
    "Спасибо всем, кто помогает клубу расти!"
)


async def cmd_jobs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/jobs — показать запланированные задачи."""
    jq = context.application.job_queue
    if jq is None:
        await update.message.reply_text("JobQueue не настроен.")
        return
    jobs = jq.jobs()
    if not jobs:
        await update.message.reply_text("Нет запланированных задач.")
        return
    now_bkk = datetime.now(BANGKOK_TZ)
    lines = [f"Сейчас Bangkok: {now_bkk:%Y-%m-%d %H:%M:%S} ({now_bkk:%A})"]
    lines.append(f"donation_sent_today: {_donation_sent_today}")
    for j in jobs:
        lines.append(f"• {j.name}")
    await update.message.reply_text("\n".join(lines))


async def cmd_testdonation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/testdonation — отправить donation-пост прямо сейчас (тест)."""
    await send_donation_reminder(context)
    await update.message.reply_text("✅ Donation отправлен в " + DONATION_CHAT)


_donation_sent_today = False


async def donation_checker(context: ContextTypes.DEFAULT_TYPE):
    """Проверяем каждую минуту: среда + 11:30 Bangkok → отправляем."""
    global _donation_sent_today
    now = datetime.now(BANGKOK_TZ)

    # Сброс флага в полночь
    if now.hour == 0 and now.minute == 0:
        _donation_sent_today = False
        return

    # Среда (weekday=2) в 11:30
    if now.weekday() == 2 and now.hour == 11 and now.minute == 30 and not _donation_sent_today:
        _donation_sent_today = True
        await send_donation_reminder(context)


async def send_donation_reminder(context: ContextTypes.DEFAULT_TYPE):
    """Еженедельная отправка donation-напоминания в чат клуба."""
    logger.info("DONATION: запуск отправки в %s", DONATION_CHAT)
    chat = DONATION_CHAT
    try:
        chat_target = int(chat) if chat.lstrip("-").isdigit() else chat
    except ValueError:
        chat_target = chat

    try:
        if QR_DONATION_PATH.exists():
            with open(QR_DONATION_PATH, "rb") as photo:
                await context.bot.send_photo(
                    chat_id=chat_target,
                    photo=photo,
                    caption=DONATION_TEXT,
                    parse_mode="HTML",
                )
        else:
            await context.bot.send_message(
                chat_id=chat_target,
                text=DONATION_TEXT,
                parse_mode="HTML",
            )
            logger.warning("QR-код не найден: %s — отправлено без картинки", QR_DONATION_PATH)
        logger.info("DONATION: напоминание отправлено в %s", chat)
    except Exception as e:
        logger.error("DONATION: ошибка отправки в %s: %s", chat, e)


# ── Еженедельный анонс встречи (вторник 15:00) ───────────────

WEEKLY_MEETING_LOCATION = "https://maps.app.goo.gl/YxfihWLdaXxWZBtC9"

_ORDINALS_RU = {
    1: "1-я", 2: "2-я", 3: "3-я", 4: "4-я", 5: "5-я",
    6: "6-я", 7: "7-я", 8: "8-я", 9: "9-я", 10: "10-я",
    11: "11-я", 12: "12-я", 13: "13-я", 14: "14-я", 15: "15-я",
    16: "16-я", 17: "17-я", 18: "18-я", 19: "19-я", 20: "20-я",
    21: "21-я", 22: "22-я", 23: "23-я", 24: "24-я", 25: "25-я",
    26: "26-я", 27: "27-я", 28: "28-я", 29: "29-я", 30: "30-я",
    31: "31-я", 32: "32-я", 33: "33-я", 34: "34-я", 35: "35-я",
    36: "36-я", 37: "37-я", 38: "38-я", 39: "39-я", 40: "40-я",
    41: "41-я", 42: "42-я", 43: "43-я", 44: "44-я", 45: "45-я",
    46: "46-я", 47: "47-я", 48: "48-я", 49: "49-я", 50: "50-я",
    51: "51-я", 52: "52-я", 53: "53-я",
}

_MONTHS_RU = [
    "", "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря"
]


def _get_meeting_number(wednesday: datetime) -> str:
    """Порядковый номер встречи в году (считаем среды с MEETING_START_DATE)."""
    delta = (wednesday.date() - MEETING_START_DATE.date()).days
    n = delta // 7 + 1
    return _ORDINALS_RU.get(n, f"{n}-я")


def _build_weekly_meeting_text(now: datetime) -> str:
    """Строим текст анонса для следующей среды."""
    from datetime import timedelta
    # Находим ближайшую среду (weekday=2)
    days_ahead = (2 - now.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7  # если сегодня среда — берём следующую
    wednesday = now + timedelta(days=days_ahead)
    meeting_num = _get_meeting_number(wednesday)
    day = wednesday.day
    month = _MONTHS_RU[wednesday.month]
    text = (
        f"💼 <b>Бизнес-клуб Самуи | {meeting_num} встреча года</b>\n\n"
        f"📅 {day} {month} (среда), 10:00\n"
        f"📍 Кафе «Планктон»\n\n"
        f"☕ Формат: Бизнес-завтрак, нетворкинг, свободное общение.\n"
        f"Рассказать о себе и своём проекте.\n"
        f"Вход свободный.\n\n"
        f"📍 Локация: {WEEKLY_MEETING_LOCATION}"
    )
    return text


_weekly_meeting_sent_today = False


async def weekly_meeting_checker(context: ContextTypes.DEFAULT_TYPE):
    """Проверяем каждую минуту: вторник + 15:00 Bangkok → отправляем."""
    global _weekly_meeting_sent_today
    now = datetime.now(BANGKOK_TZ)

    if now.hour == 0 and now.minute == 0:
        _weekly_meeting_sent_today = False
        return

    # Вторник (weekday=1) в 15:00
    if now.weekday() == 1 and now.hour == 15 and now.minute == 0 and not _weekly_meeting_sent_today:
        _weekly_meeting_sent_today = True
        await send_weekly_meeting(context)


async def send_weekly_meeting(context: ContextTypes.DEFAULT_TYPE):
    """Отправляем еженедельный анонс встречи."""
    now = datetime.now(BANGKOK_TZ)
    text = _build_weekly_meeting_text(now)
    logger.info("WEEKLY_MEETING: отправка в %s", DONATION_CHAT)
    try:
        chat_target = int(DONATION_CHAT) if DONATION_CHAT.lstrip("-").isdigit() else DONATION_CHAT
    except ValueError:
        chat_target = DONATION_CHAT

    try:
        if WEEKLY_MEETING_PATH.exists():
            with open(WEEKLY_MEETING_PATH, "rb") as photo:
                await context.bot.send_photo(
                    chat_id=chat_target,
                    photo=photo,
                    caption=text,
                    parse_mode="HTML",
                )
        else:
            await context.bot.send_message(
                chat_id=chat_target,
                text=text,
                parse_mode="HTML",
            )
            logger.warning("WEEKLY_MEETING: картинка не найдена: %s", WEEKLY_MEETING_PATH)
        logger.info("WEEKLY_MEETING: анонс отправлен в %s", DONATION_CHAT)
    except Exception as e:
        logger.error("WEEKLY_MEETING: ошибка: %s", e)


async def cmd_testmeeting(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/testmeeting — отправить анонс встречи прямо сейчас (тест)."""
    await send_weekly_meeting(context)
    await update.message.reply_text("✅ Анонс встречи отправлен в " + DONATION_CHAT)


# ── Еженедельная Вечерняя Бизнес-прогулка (понедельник 15:00) ─

def _to_roman(n: int) -> str:
    vals = [(1000,"M"),(900,"CM"),(500,"D"),(400,"CD"),(100,"C"),(90,"XC"),
            (50,"L"),(40,"XL"),(10,"X"),(9,"IX"),(5,"V"),(4,"IV"),(1,"I")]
    result = ""
    for v, s in vals:
        while n >= v:
            result += s
            n -= v
    return result


def _build_walk_text(now: datetime) -> str:
    from datetime import timedelta
    # Ближайший вторник
    days_ahead = (1 - now.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    tuesday = now + timedelta(days=days_ahead)
    # Номер прогулки
    delta = (tuesday.date() - WALK_START_DATE.date()).days
    n = delta // 7 + 1
    roman = _to_roman(n)
    day = tuesday.day
    month = _MONTHS_RU[tuesday.month]
    year = tuesday.year
    return (
        f"💼 Бизнес-клуб Самуи | {roman} Вечерняя Бизнес-прогулка\n\n"
        f"📅 Вторник, {day} {month} {year}\n"
        f"🕙 Начало в 22:00 | Длительность до 2 часов\n"
        f"📍 Место старта на озере у баскетбольной площадки ({WALK_LOCATION})\n\n"
        f"⚡ Не попадаешь на утренние встречи из-за другой таймзоны\n"
        f"Просто чувствуешь, что лучше думаешь на ходу —\n"
        f"приходи на бизнес-прогулку.\n"
        f"Идём, разговариваем, думаем! ;)"
    )


_walk_sent_today = False


async def walk_checker(context: ContextTypes.DEFAULT_TYPE):
    """Проверяем каждую минуту: понедельник + 15:00 Bangkok → отправляем."""
    global _walk_sent_today
    now = datetime.now(BANGKOK_TZ)

    if now.hour == 0 and now.minute == 0:
        _walk_sent_today = False
        return

    # Понедельник (weekday=0) в 15:00
    if now.weekday() == 0 and now.hour == 15 and now.minute == 0 and not _walk_sent_today:
        _walk_sent_today = True
        await send_walk_reminder(context)


async def send_walk_reminder(context: ContextTypes.DEFAULT_TYPE):
    """Отправляем анонс Вечерней Бизнес-прогулки."""
    now = datetime.now(BANGKOK_TZ)
    text = _build_walk_text(now)
    logger.info("WALK: отправка в %s", DONATION_CHAT)
    try:
        chat_target = int(DONATION_CHAT) if DONATION_CHAT.lstrip("-").isdigit() else DONATION_CHAT
    except ValueError:
        chat_target = DONATION_CHAT

    try:
        if WALK_PATH.exists():
            with open(WALK_PATH, "rb") as photo:
                await context.bot.send_photo(
                    chat_id=chat_target,
                    photo=photo,
                    caption=text,
                )
        else:
            await context.bot.send_message(chat_id=chat_target, text=text)
            logger.warning("WALK: картинка не найдена: %s", WALK_PATH)
        logger.info("WALK: анонс отправлен в %s", DONATION_CHAT)
    except Exception as e:
        logger.error("WALK: ошибка: %s", e)


async def cmd_testwalk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/testwalk — отправить анонс прогулки прямо сейчас (тест)."""
    await send_walk_reminder(context)
    await update.message.reply_text("✅ Анонс прогулки отправлен в " + DONATION_CHAT)


# ── Еженедельный спикерский пост (четверг 11:30) ─────────────

SPEAKER_TEXT = (
    "👋 <b>Если ты новенький</b> — представься и расскажи о себе!\n\n"
    "🎤 <b>Если ты хочешь провести полезную встречу или стать спикером</b> — "
    "напиши Натали: @Natali_na_Samui"
)

_speaker_sent_today = False


async def speaker_checker(context: ContextTypes.DEFAULT_TYPE):
    """Проверяем каждую минуту: четверг + 11:30 Bangkok → отправляем."""
    global _speaker_sent_today
    now = datetime.now(BANGKOK_TZ)

    if now.hour == 0 and now.minute == 0:
        _speaker_sent_today = False
        return

    # Четверг (weekday=3) в 11:30
    if now.weekday() == 3 and now.hour == 11 and now.minute == 30 and not _speaker_sent_today:
        _speaker_sent_today = True
        await send_speaker_reminder(context)


async def send_speaker_reminder(context: ContextTypes.DEFAULT_TYPE):
    """Еженедельная отправка спикерского поста в чат клуба."""
    logger.info("SPEAKER: запуск отправки в %s", DONATION_CHAT)
    try:
        chat_target = int(DONATION_CHAT) if DONATION_CHAT.lstrip("-").isdigit() else DONATION_CHAT
    except ValueError:
        chat_target = DONATION_CHAT

    try:
        if NATALI_SPEAKER_PATH.exists():
            with open(NATALI_SPEAKER_PATH, "rb") as photo:
                await context.bot.send_photo(
                    chat_id=chat_target,
                    photo=photo,
                    caption=SPEAKER_TEXT,
                    parse_mode="HTML",
                )
        else:
            await context.bot.send_message(
                chat_id=chat_target,
                text=SPEAKER_TEXT,
                parse_mode="HTML",
            )
            logger.warning("SPEAKER: фото не найдено: %s", NATALI_SPEAKER_PATH)
        logger.info("SPEAKER: пост отправлен в %s", DONATION_CHAT)
    except Exception as e:
        logger.error("SPEAKER: ошибка отправки в %s: %s", DONATION_CHAT, e)


async def cmd_testspeaker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/testspeaker — отправить спикерский пост прямо сейчас (тест)."""
    await send_speaker_reminder(context)
    await update.message.reply_text("✅ Speaker-пост отправлен в " + DONATION_CHAT)


# ── Анкета вступления (deep link ?start=join) ─────────────────

JOIN_NAME, JOIN_WHY, JOIN_SAMUI = range(3)

# user_id → {name, why, samui, tg_user}
_join_pending: dict[int, dict] = {}


async def join_got_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    _join_pending[user.id] = {"name": update.message.text, "tg_user": user}
    await update.message.reply_text(
        "<b>Вопрос 2/3:</b> Почему хочешь вступить в клуб? Что рассчитываешь получить?",
        parse_mode="HTML",
    )
    return JOIN_WHY


async def join_got_why(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    _join_pending.setdefault(user.id, {})["why"] = update.message.text
    await update.message.reply_text(
        "<b>Вопрос 3/3:</b> Ты сейчас на Самуи?",
        parse_mode="HTML",
    )
    return JOIN_SAMUI


async def join_got_samui(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    data = _join_pending.get(user.id, {})
    data["samui"] = update.message.text
    data["tg_user"] = user

    tg_link = f"@{user.username}" if user.username else f"tg://user?id={user.id}"
    text = (
        f"📋 <b>Новая заявка на вступление</b>\n\n"
        f"👤 {user.full_name} ({tg_link})\n"
        f"ID: <code>{user.id}</code>\n\n"
        f"1️⃣ <b>Кто:</b> {data.get('name', '—')}\n"
        f"2️⃣ <b>Зачем:</b> {data.get('why', '—')}\n"
        f"3️⃣ <b>На Самуи:</b> {data.get('samui', '—')}"
    )
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Одобрить", callback_data=f"join_approve_{user.id}"),
        InlineKeyboardButton("❌ Отклонить", callback_data=f"join_decline_{user.id}"),
    ]])

    try:
        await context.bot.send_message(
            chat_id=int(ADMIN_CHAT_ID),
            text=text,
            parse_mode="HTML",
            reply_markup=keyboard,
        )
    except Exception as e:
        logger.error("JOIN: не удалось отправить анкету админу: %s", e)

    await update.message.reply_text(
        "✅ Спасибо! Твоя заявка отправлена.\n"
        "Мы рассмотрим её и пришлём ссылку для вступления."
    )
    return ConversationHandler.END


async def join_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Заявка отменена. Напиши /start join чтобы начать заново.")
    return ConversationHandler.END


# ── Запуск ────────────────────────────────────────────────────

def main():
    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN не задан! Создайте bot/.env на основе .env.example"
        )

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    # Команды и пересылка — только в личке с ботом
    pm = filters.ChatType.PRIVATE

    # Анкета вступления через deep link ?start=join
    join_conv = ConversationHandler(
        entry_points=[CommandHandler("start", cmd_start, filters=pm)],
        states={
            JOIN_NAME:  [MessageHandler(pm & filters.TEXT & ~filters.COMMAND, join_got_name)],
            JOIN_WHY:   [MessageHandler(pm & filters.TEXT & ~filters.COMMAND, join_got_why)],
            JOIN_SAMUI: [MessageHandler(pm & filters.TEXT & ~filters.COMMAND, join_got_samui)],
        },
        fallbacks=[CommandHandler("cancel", join_cancel, filters=pm)],
        allow_reentry=True,
    )
    app.add_handler(join_conv)
    app.add_handler(CommandHandler("check", cmd_check, filters=pm))
    app.add_handler(CommandHandler("scan", cmd_scan, filters=pm))
    app.add_handler(CommandHandler("purge", cmd_purge, filters=pm))
    app.add_handler(CommandHandler("audit", cmd_audit, filters=pm))
    app.add_handler(CommandHandler("cleanup", cmd_cleanup, filters=pm))
    app.add_handler(CommandHandler("status", cmd_status, filters=pm))
    app.add_handler(CommandHandler("jobs", cmd_jobs, filters=pm))
    app.add_handler(CommandHandler("testdonation", cmd_testdonation, filters=pm))
    app.add_handler(CommandHandler("testspeaker", cmd_testspeaker, filters=pm))
    app.add_handler(CommandHandler("testmeeting", cmd_testmeeting, filters=pm))
    app.add_handler(CommandHandler("testwalk", cmd_testwalk, filters=pm))
    app.add_handler(ChatMemberHandler(
        handle_chat_member_update, ChatMemberHandler.CHAT_MEMBER,
    ))
    app.add_handler(MessageHandler(pm & filters.FORWARDED, handle_forwarded))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(ChatJoinRequestHandler(callback=handle_join_request))

    async def error_handler(update, context):
        """Логируем ошибки, но не роняем бота."""
        logger.warning("Ошибка обработки обновления: %s", context.error)

    app.add_error_handler(error_handler)

    # Еженедельное donation-напоминание: среда 11:30 по Бангкоку
    job_queue = app.job_queue
    if job_queue is not None:
        job_queue.run_repeating(
            donation_checker,
            interval=60,  # каждую минуту
            first=5,      # через 5 секунд после старта
            name="donation_checker",
        )
        job_queue.run_repeating(
            speaker_checker,
            interval=60,
            first=10,
            name="speaker_checker",
        )
        job_queue.run_repeating(
            weekly_meeting_checker,
            interval=60,
            first=15,
            name="weekly_meeting_checker",
        )
        logger.info("DONATION: checker запущен (каждую минуту, ср 11:30 Bangkok)")
        logger.info("SPEAKER: checker запущен (каждую минуту, чт 11:30 Bangkok)")
        job_queue.run_repeating(
            walk_checker,
            interval=60,
            first=20,
            name="walk_checker",
        )
        logger.info("WEEKLY_MEETING: checker запущен (каждую минуту, вт 15:00 Bangkok)")
        logger.info("WALK: checker запущен (каждую минуту, пн 15:00 Bangkok)")
    else:
        logger.warning("DONATION: JobQueue недоступен — установите python-telegram-bot[job-queue]")

    mode = "ТЕСТОВЫЙ (без реальных отклонений)" if TEST_MODE else "БОЕВОЙ"
    logger.info("Бот запущен в режиме: %s", mode)
    logger.info("Пороги: approve >= %d, decline < %d", SCORE_APPROVE, SCORE_DECLINE)
    app.run_polling(
        drop_pending_updates=False,
        allowed_updates=["message", "callback_query", "chat_join_request", "chat_member"],
    )


if __name__ == "__main__":
    main()
