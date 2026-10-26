import os
import re
import sys
import json
import time
import random
import string
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

MAIL_APIS = ["https://api.mail.tm", "https://api.mail.gw"]
TRIAL_URL = "https://power-vps.pro/free-trial/"
# Твоя страница быстрого редиректа в Happ (из index.html в репозитории)
REDIRECT_BASE_URL = "https://sashas2mple.github.io/vpnfreeforme/"
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN")
MAX_RETRIES_PER_WAVE = 3
TIMER_INTERVAL_SEC = 252600  # 70 ч. 10 мин. (с учётом очереди GitHub выходит ровно ~71 ч.)
BUTTON_COOLDOWN_SEC = 3600   # Защита от спама кнопкой: не чаще 1 раза в час для одного человека
STATE_FILE = "tg_state.json"
LAST_RUN_FILE = "last_run.txt"

ADMIN_CHAT_ID = os.environ.get("CHAT_ID_1")

USERS = [
    {"name": "Пользователь 1 (Ты)", "chat_id": os.environ.get("CHAT_ID_1")},
    {"name": "Пользователь 2 (Друг 1)", "chat_id": os.environ.get("CHAT_ID_2")},
    {"name": "Пользователь 3 (Друг 2)", "chat_id": os.environ.get("CHAT_ID_3")},
    {"name": "Пользователь 4 (Друг 3)", "chat_id": os.environ.get("CHAT_ID_4")},
    {"name": "Пользователь 5 (Друг 4)", "chat_id": os.environ.get("CHAT_ID_5")},
]


# ==================== БЫСТРАЯ ПРОВЕРКА КНОПОК TG И ТАЙМЕРА (БЕЗ БРАУЗЕРА) ====================

def tg_api_call(method, payload=None):
    if not TG_BOT_TOKEN:
        return {}
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/{method}"
    data = json.dumps(payload).encode("utf-8") if payload else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"[-] TG API ({method}) ошибка: {e}")
        return {}


def load_json_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"last_update_id": 0, "cooldowns": {}}


def save_json_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def get_last_run_time():
    if os.path.exists(LAST_RUN_FILE):
        try:
            with open(LAST_RUN_FILE, "r", encoding="utf-8") as f:
                return int(f.read().strip())
        except Exception:
            pass
    return 0


def write_github_output(key, value):
    out_file = os.environ.get("GITHUB_OUTPUT")
    if out_file:
        with open(out_file, "a", encoding="utf-8") as f:
            f.write(f"{key}={value}\n")
    print(f"[OUTPUT] {key}={value}")


def run_precheck():
    """Проверяет таймер 71ч и нажатия кнопок в Telegram за 1 секунду без установки браузера"""
    now = int(time.time())
    write_github_output("run_start", str(now))

    allowed_ids = {str(u["chat_id"]).strip() for u in USERS if u.get("chat_id")}
    state = load_json_state()
    last_update_id = state.get("last_update_id", 0)
    cooldowns = state.get("cooldowns", {})
    state_changed = False
    reissue_chat_ids = []

    # 1. Проверяем новые нажатия кнопок и команды в Telegram-боте
    updates_resp = tg_api_call("getUpdates", {"offset": last_update_id + 1, "timeout": 1})
    for upd in updates_resp.get("result", []):
        uid = upd["update_id"]
        if uid > last_update_id:
            last_update_id = uid
            state_changed = True

        # Обработка нажатия инлайн-кнопки «🔄 Не работает? Прислать новую»
        cb = upd.get("callback_query")
        if cb:
            cb_id = cb["id"]
            chat_id = str(cb.get("message", {}).get("chat", {}).get("id", "")).strip()
            data = cb.get("data", "")

            if chat_id in allowed_ids and data.startswith("reissue"):
                last_req = int(cooldowns.get(chat_id, 0))
                if now - last_req < BUTTON_COOLDOWN_SEC:
                    left_min = max(1, (BUTTON_COOLDOWN_SEC - (now - last_req)) // 60)
                    tg_api_call("answerCallbackQuery", {
                        "callback_query_id": cb_id,
                        "text": f"⏳ Ты недавно уже запрашивал ссылку. Подожди ещё {left_min} мин.",
                        "show_alert": True
                    })
                else:
                    cooldowns[chat_id] = now
                    if chat_id not in reissue_chat_ids:
                        reissue_chat_ids.append(chat_id)
                    tg_api_call("answerCallbackQuery", {
                        "callback_query_id": cb_id,
                        "text": "✅ Запрос принят! Генерирую новую ссылку (~1-2 минуты)...",
                        "show_alert": False
                    })
                    tg_api_call("sendMessage", {
                        "chat_id": chat_id,
                        "text": "⏳ <b>Запрос принят!</b>\nГенерирую для тебя свежую ссылку VPN (подожди ~1-2 минуты)...",
                        "parse_mode": "HTML"
                    })
            continue

        # Обработка текстовых команд (/new и /status)
        msg = upd.get("message", {})
        chat_id = str(msg.get("chat", {}).get("id", "")).strip()
        text = (msg.get("text") or "").strip().lower()

        if chat_id in allowed_ids:
            if text.startswith("/status") or text == "статус":
                last_run = get_last_run_time()
                rem_sec = max(0, (last_run + 255600) - now)
                rem_h = rem_sec // 3600
                rem_m = (rem_sec % 3600) // 60
                tg_api_call("sendMessage", {
                    "chat_id": chat_id,
                    "text": f"📊 <b>Статус авто-рассылки VPN:</b>\nДо следующей плановой выдачи осталось: <b>{rem_h} ч. {rem_m} мин.</b>\n\nЕсли текущая ссылка не работает, нажми кнопку под последним сообщением или отправь /new.",
                    "parse_mode": "HTML"
                })
            elif text.startswith("/new") or text == "новая":
                last_req = int(cooldowns.get(chat_id, 0))
                if now - last_req < BUTTON_COOLDOWN_SEC:
                    left_min = max(1, (BUTTON_COOLDOWN_SEC - (now - last_req)) // 60)
                    tg_api_call("sendMessage", {
                        "chat_id": chat_id,
                        "text": f"⏳ Ты уже запрашивал замену недавно. Подожди ещё {left_min} мин."
                    })
                else:
                    cooldowns[chat_id] = now
                    if chat_id not in reissue_chat_ids:
                        reissue_chat_ids.append(chat_id)
                    tg_api_call("sendMessage", {
                        "chat_id": chat_id,
                        "text": "⏳ <b>Запрос принят!</b>\nГенерирую для тебя свежую ссылку VPN (подожди ~1-2 минуты)...",
                        "parse_mode": "HTML"
                    })

    if state_changed:
        state["last_update_id"] = last_update_id
        state["cooldowns"] = cooldowns
        save_json_state(state)

    write_github_output("state_changed", "true" if state_changed else "false")

    # 2. Проверяем ручной запуск с GitHub
    event_name = os.environ.get("EVENT_NAME", "")
    manual_target = os.environ.get("MANUAL_TARGET", "").strip()

    if event_name == "workflow_dispatch":
        if manual_target.startswith("0"):
            write_github_output("should_run", "true")
            write_github_output("need_browser", "false")
            write_github_output("run_mode", "list_users")
            write_github_output("target_ids", "")
            write_github_output("reset_timer", "false")
            return
        elif manual_target and manual_target[0] in ("1", "2", "3", "4", "5"):
            idx = int(manual_target[0]) - 1
            cid = str(USERS[idx].get("chat_id") or "").strip()
            write_github_output("should_run", "true")
            write_github_output("need_browser", "true")
            write_github_output("run_mode", "single")
            write_github_output("target_ids", cid)
            write_github_output("reset_timer", "false")
            return
        else:
            write_github_output("should_run", "true")
            write_github_output("need_browser", "true")
            write_github_output("run_mode", "full")
            write_github_output("target_ids", "")
            write_github_output("reset_timer", "true")
            return

    # 3. Проверяем 71-часовой таймер
    last_run = get_last_run_time()
    diff = now - last_run
    if last_run == 0 or diff >= TIMER_INTERVAL_SEC:
        print(f"[+] Прошло {diff // 3600} ч. {(diff % 3600) // 60} мин. — запускаем общую рассылку!")
        write_github_output("should_run", "true")
        write_github_output("need_browser", "true")
        write_github_output("run_mode", "full")
        write_github_output("target_ids", "")
        write_github_output("reset_timer", "true")
        return

    # 4. Если общий таймер ещё не подошёл, но кто-то нажал кнопку «Прислать новую» в Telegram
    if reissue_chat_ids:
        print(f"[+] Запрос новой ссылки через кнопку TG для ID: {reissue_chat_ids}")
        write_github_output("should_run", "true")
        write_github_output("need_browser", "true")
        write_github_output("run_mode", "single")
        write_github_output("target_ids", ",".join(reissue_chat_ids))
        write_github_output("reset_timer", "false")
        return

    print(f"[*] Прошло {diff // 3600} ч. {(diff % 3600) // 60} мин. из 71 ч. Кнопок никто не нажимал. Выход.")
    write_github_output("should_run", "false")
    write_github_output("need_browser", "false")
    write_github_output("run_mode", "none")
    write_github_output("target_ids", "")
    write_github_output("reset_timer", "false")


# ==================== ОСНОВНАЯ ГЕНЕРАЦИЯ ССЫЛОК (PLAYWRIGHT) ====================

def create_temp_email():
    import requests
    for attempt in range(1, 7):
        api_base = MAIL_APIS[(attempt - 1) % len(MAIL_APIS)]
        try:
            domains_resp = requests.get(f"{api_base}/domains", timeout=15)
            if domains_resp.status_code != 200:
                time.sleep(3)
                continue
            domains = domains_resp.json().get("hydra:member", [])
            if not domains:
                time.sleep(3)
                continue

            domain = random.choice(domains)["domain"]
            username = ''.join(random.choices(string.ascii_lowercase + string.digits, k=11))
            email = f"{username}@{domain}"
            password = "TempPassword123!"

            acc_resp = requests.post(
                f"{api_base}/accounts",
                json={"address": email, "password": password},
                timeout=15
            )
            if acc_resp.status_code not in (200, 201):
                time.sleep(3)
                continue

            token_resp = requests.post(
                f"{api_base}/token",
                json={"address": email, "password": password},
                timeout=15
            ).json()
            token = token_resp.get("token")
            if token:
                return email, token, api_base
        except Exception as e:
            print(f"[-] Попытка {attempt}/6 создания почты ({api_base}) не удалась: {e}")
            time.sleep(4)
    raise RuntimeError("Не удалось создать временную почту ни на mail.tm, ни на mail.gw")


def wait_for_email_link(api_base, token, timeout=60):
    import requests
    headers = {"Authorization": f"Bearer {token}"}
    start = time.time()
    while time.time() - start < timeout:
        try:
            msgs = requests.get(f"{api_base}/messages", headers=headers, timeout=15).json().get("hydra:member", [])
            if msgs:
                msg_id = msgs[0]["id"]
                msg = requests.get(f"{api_base}/messages/{msg_id}", headers=headers, timeout=15).json()
                text = msg.get("text", "") or str(msg.get("html", ""))
                links = re.findall(r'(https?://[^\s"\'<>]+|happ://[^\s"\'<>]+)', text)
                valid_links = [
                    l for l in links
                    if not any(ext in l.lower() for ext in [".png", ".jpg", ".jpeg", ".svg", ".css", ".js", "w3.org"])
                ]
                if valid_links:
                    return valid_links
        except Exception as e:
            print(f"[-] Ошибка при проверке почты: {e}")
        time.sleep(3)
    return []


def send_admin_debug(photo_path, caption):
    import requests
    if not TG_BOT_TOKEN or not ADMIN_CHAT_ID:
        return
    try:
        if photo_path and os.path.exists(photo_path):
            with open(photo_path, "rb") as photo:
                requests.post(
                    f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendPhoto",
                    data={"chat_id": ADMIN_CHAT_ID, "caption": caption[:1000]},
                    files={"photo": photo},
                    timeout=20
                )
        else:
            requests.post(
                f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",
                json={"chat_id": ADMIN_CHAT_ID, "text": caption[:4000]},
                timeout=15
            )
    except Exception as e:
        print(f"[-] Ошибка отправки отчёта админу: {e}")


def find_email_input(page):
    selectors = [
        'input[type="email"]',
        'input[name*="mail" i]',
        'input[placeholder*="mail" i]',
        'input[placeholder*="почт" i]',
        'input[placeholder*="@" i]',
        'input[type="text"]',
        'input:not([type="hidden"]):not([type="checkbox"]):not([type="radio"]):not([type="submit"]):not([type="button"])'
    ]
    for frame in [page.main_frame] + [f for f in page.frames if f != page.main_frame]:
        for sel in selectors:
            try:
                loc = frame.locator(sel)
                for i in range(loc.count()):
                    el = loc.nth(i)
                    if el.is_visible():
                        return el, frame
            except Exception:
                pass
    return None, None


def open_email_form(page):
    email_input, frame = find_email_input(page)
    if email_input:
        return email_input, frame

    triggers = [
        page.get_by_text("Получить на E-mail", exact=False),
        page.get_by_text("Получить VPN", exact=True),
        page.locator('a:has-text("Получить на E-mail"), button:has-text("Получить на E-mail")'),
        page.locator('a:has-text("Получить VPN"):not(:has-text("Telegram")), button:has-text("Получить VPN"):not(:has-text("Telegram"))')
    ]

    for loc in triggers:
        try:
            count = loc.count()
            for i in range(count):
                el = loc.nth(i)
                if el.is_visible():
                    el.scroll_into_view_if_needed(timeout=3000)
                    el.click(timeout=3000)
                    page.wait_for_timeout(2000)
                    email_input, frame = find_email_input(page)
                    if email_input:
                        return email_input, frame
        except Exception as e:
            print(f"[-] Попытка клика по кнопке формы: {e}")

    return None, None


def extract_and_validate_sub_page(page):
    """Извлекает ссылки и ПРОВЕРЯЕТ, что страница подписки реально рабочая, а не пустая/битая"""
    page_url = page.url
    happ_deeplink = None
    has_add_button = False

    try:
        happ_tab = page.get_by_text("Happ", exact=True).first
        if happ_tab.count() > 0 and happ_tab.is_visible():
            happ_tab.click()
            page.wait_for_timeout(3000)

        add_btn = page.locator('a:has-text("Добавить подписку"), button:has-text("Добавить подписку")').first
        if add_btn.count() > 0 and add_btn.is_visible():
            has_add_button = True
            href = add_btn.get_attribute("href")
            if href:
                happ_deeplink = href
    except Exception:
        pass

    content = page.content()
    if not happ_deeplink:
        found_happ = re.findall(r'(happ://[^\s"\'<>]+)', content)
        if found_happ:
            happ_deeplink = found_happ[0]

    # Валидация: страница считается рабочей только если на ней есть happ:// ссылка,
    # кнопка «Добавить подписку» или URL явно ведёт на панель подписки (и это не стартовая страница триала)
    is_valid = bool(
        happ_deeplink
        or has_add_button
        or (page_url != TRIAL_URL and ("/sub" in page_url or "Установка" in content))
    )

    if not is_valid:
        return None, None
    return page_url, happ_deeplink


def get_trial_in_incognito(browser):
    sub_page_url = None
    happ_link = None
    error_text = None
    email = "не создана"
    screenshot_path = "debug_screen.png"

    context = browser.new_context(
        viewport={"width": 1280, "height": 900},
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    )
    page = context.new_page()

    try:
        email, token, api_base = create_temp_email()
        print(f"[+] Сгенерирована почта: {email} ({api_base})")

        page.goto(TRIAL_URL, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(3000)

        before_links = set(page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)"))

        email_input, target_frame = open_email_form(page)
        if not email_input:
            raise RuntimeError("После нажатия кнопок поле ввода почты не появилось")

        text_inputs = target_frame.locator(
            'input[type="text"], input[name*="name" i], input[placeholder*="имя" i], '
            'input:not([type]):not([type="hidden"]):not([type="checkbox"]):not([type="radio"]):not([type="submit"]):not([type="button"])'
        )
        for i in range(text_inputs.count()):
            inp = text_inputs.nth(i)
            if inp.is_visible():
                try:
                    inp.fill("Alex")
                except Exception:
                    pass
        page.wait_for_timeout(300)

        email_input.fill(email)
        page.wait_for_timeout(500)

        checkboxes = target_frame.locator('input[type="checkbox"]')
        for i in range(checkboxes.count()):
            cb = checkboxes.nth(i)
            if cb.is_visible() and not cb.is_checked():
                cb.check(force=True)

        clicked_submit = False
        submit_selectors = [
            'button:has-text("ПОЛУЧИТЬ VPN ДОСТУП")',
            'button:has-text("Получить VPN доступ")',
            'input[value*="ПОЛУЧИТЬ" i]',
            'form button[type="submit"]',
            'form input[type="submit"]',
            'button[type="submit"]',
            'input[type="submit"]',
            'form button',
            'button:has-text("Получить")',
            'button:has-text("Отправить")',
            'button:has-text("Активировать")',
            'button:has-text("Подключить")',
        ]
        for sel in submit_selectors:
            loc = target_frame.locator(sel)
            for i in range(loc.count()):
                btn = loc.nth(i)
                if btn.is_visible():
                    btn.click()
                    clicked_submit = True
                    break
            if clicked_submit:
                break

        if not clicked_submit:
            email_input.press("Enter")

        page.wait_for_timeout(30000)

        after_links = page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)")
        new_links = [
            l for l in after_links
            if l not in before_links
            and not l.startswith("javascript:")
            and not l.startswith("mailto:")
            and not l.endswith("#")
        ]

        if page.locator('text="Добавить подписку"').count() > 0 or page.url != TRIAL_URL:
            sub_page_url, happ_link = extract_and_validate_sub_page(page)
        elif new_links:
            target = new_links[0]
            print(f"[+] Найдена новая ссылка на странице: {target}")
            if target.startswith("happ://"):
                happ_link = target
            else:
                page.goto(target, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(10000)
                sub_page_url, happ_link = extract_and_validate_sub_page(page)
        else:
            print("[*] Проверяем письмо на почте...")
            email_links = wait_for_email_link(api_base, token, timeout=60)
            for link in email_links:
                print(f"[+] Ссылка из письма: {link}")
                if link.startswith("happ://"):
                    happ_link = link
                    break
                page.goto(link, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(10000)
                sub_page_url, happ_link = extract_and_validate_sub_page(page)
                if sub_page_url or happ_link:
                    break

        page.screenshot(path=screenshot_path, full_page=True)
    except Exception as e:
        error_text = str(e)
        print(f"[-] Ошибка при оформлении триала: {e}")
        try:
            page.screenshot(path=screenshot_path, full_page=True)
        except Exception:
            pass
    finally:
        context.close()

    return sub_page_url, happ_link, screenshot_path, error_text, email


def get_trial_with_retries(browser, user_name):
    last_result = (None, None, "debug_screen.png", "Неизвестная ошибка", "не создана")
    for attempt in range(1, MAX_RETRIES_PER_WAVE + 1):
        print(f"[*] {user_name}: Попытка {attempt} из {MAX_RETRIES_PER_WAVE}...")
        sub_page_url, happ_link, screen, err, email = get_trial_in_incognito(browser)
        last_result = (sub_page_url, happ_link, screen, err, email)
        if sub_page_url or happ_link:
            return last_result
        wait_sec = 15 * attempt
        print(f"[-] Попытка {attempt} не дала валидную ссылку. Ждём {wait_sec} сек...")
        time.sleep(wait_sec)
    return last_result


def send_to_telegram(chat_id, sub_page_url, happ_link):
    import requests
    msk_tz = timezone(timedelta(hours=3))
    now_msk = datetime.now(msk_tz)
    exp_msk = now_msk + timedelta(hours=72)
    next_msk = now_msk + timedelta(hours=71)

    text_lines = ["🔥 <b>Твоя новая подписка VPN на 3 дня готова!</b>\n"]
    if sub_page_url and sub_page_url.startswith("http"):
        text_lines.append(f"🌐 <b>Страница подписки:</b>\n<code>{sub_page_url}</code>\n")
    if happ_link:
        text_lines.append(f"📲 <b>Прямая ссылка для Happ:</b>\n<code>{happ_link}</code>\n")

    text_lines.append(
        f"⏳ <b>Действует до:</b> {exp_msk.strftime('%d.%m в %H:%M')} (МСК)\n"
        f"🤖 <b>Следующая придёт:</b> ~{next_msk.strftime('%d.%m в %H:%M')} (МСК)\n\n"
        "<b>Как добавить (на iPhone и Mac):</b>\n"
        "1. Нажми <b>«🚀 В Happ (1 клик)»</b> или <b>«🌐 Страница подписки»</b> ниже.\n"
        "2. Если ссылка вдруг не открывается — нажми кнопку <b>«🔄 Не работает? Прислать новую»</b> (бот пришлёт замену лично тебе)."
    )

    # Формируем кнопки: 1-й ряд — быстрый импорт и веб-страница, 2-й ряд — запрос новой ссылки
    keyboard = []
    first_row = []

    target_for_one_click = happ_link or sub_page_url
    if target_for_one_click:
        one_click_url = f"{REDIRECT_BASE_URL}?url={urllib.parse.quote(target_for_one_click, safe='')}"
        first_row.append({"text": "🚀 В Happ (1 клик)", "url": one_click_url})

    if sub_page_url and sub_page_url.startswith("http"):
        first_row.append({"text": "🌐 Страница подписки", "url": sub_page_url})

    if first_row:
        keyboard.append(first_row)

    keyboard.append([
        {"text": "🔄 Не работает? Прислать новую", "callback_data": f"reissue_{chat_id}"}
    ])

    payload = {
        "chat_id": chat_id,
        "text": "\n".join(text_lines),
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
        "reply_markup": {"inline_keyboard": keyboard}
    }

    for _ in range(4):
        try:
            resp = requests.post(f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage", json=payload, timeout=15)
            if resp.status_code == 200:
                return True
        except Exception as e:
            print(f"[-] Ошибка отправки в Telegram: {e}")
        time.sleep(3)
    return False


def send_users_list_to_admin():
    lines = ["📋 <b>Кто под каким номером записан в GitHub:</b>\n"]
    for idx, user in enumerate(USERS, start=1):
        cid = user.get("chat_id")
        if not cid:
            lines.append(f"<b>{idx} (CHAT_ID_{idx}):</b> ❌ <i>не задан</i>")
            continue
        r = tg_api_call("getChat", {"chat_id": cid})
        if r.get("ok"):
            chat = r["result"]
            full_name = f"{chat.get('first_name', '')} {chat.get('last_name', '')}".strip()
            username = f"@{chat['username']}" if chat.get("username") else "без юзернейма"
            lines.append(f"<b>{idx} (CHAT_ID_{idx}):</b> {full_name} ({username}) — <code>{cid}</code>")
        else:
            lines.append(f"<b>{idx} (CHAT_ID_{idx}):</b> ID <code>{cid}</code>")

    tg_api_call("sendMessage", {"chat_id": ADMIN_CHAT_ID, "text": "\n".join(lines), "parse_mode": "HTML"})


def main():
    if "--check-only" in sys.argv:
        run_precheck()
        return

    if not TG_BOT_TOKEN:
        print("[-] КРИТИЧЕСКАЯ ОШИБКА: Секрет TG_BOT_TOKEN не задан!")
        raise SystemExit(1)

    run_mode = os.environ.get("RUN_MODE", "full").strip()
    target_ids_raw = os.environ.get("TARGET_IDS", "").strip()

    if run_mode == "list_users":
        send_users_list_to_admin()
        return

    if run_mode == "single" and target_ids_raw:
        target_set = {x.strip() for x in target_ids_raw.split(",") if x.strip()}
        active_users = [u for u in USERS if str(u.get("chat_id") or "").strip() in target_set]
    else:
        active_users = [u for u in USERS if u.get("chat_id")]

    if not active_users:
        print("[-] Нет активных пользователей для отправки!")
        raise SystemExit(1)

    from playwright.sync_api import sync_playwright
    failed_users = []
    success_count = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        for user in active_users:
            sub_page_url, happ_link, screen, err, email = get_trial_with_retries(browser, user["name"])
            if sub_page_url or happ_link:
                print(f"[+] Успешно для {user['name']}! Страница: {sub_page_url} | Happ: {happ_link}")
                send_to_telegram(user["chat_id"], sub_page_url, happ_link)
                success_count += 1
            else:
                failed_users.append((user, screen, err, email))
            time.sleep(4)
        browser.close()

        if failed_users:
            print(f"\n[*] Ждём 120 секунд перед повторной волной для {len(failed_users)} чел...")
            time.sleep(120)
            browser = p.chromium.launch(headless=True)
            still_failed = []

            for user, _, _, _ in failed_users:
                sub_page_url, happ_link, screen, err, email = get_trial_with_retries(browser, f"{user['name']} (Волна 2)")
                if sub_page_url or happ_link:
                    send_to_telegram(user["chat_id"], sub_page_url, happ_link)
                    success_count += 1
                else:
                    still_failed.append((user, screen, err, email))
                time.sleep(4)
            browser.close()

            for user, screen, err, email in still_failed:
                caption = (
                    f"⚠️ Не удалось получить ссылку для «{user['name']}» после 2 волн (почта {email}).\n"
                    f"Друзьям ничего не отправлено.\n"
                    f"Детали: {err or 'Ссылка не прошла валидацию'}"
                )
                send_admin_debug(screen, caption)

    if active_users and success_count == 0:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
