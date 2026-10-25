import os
import re
import time
import random
import string
import requests
from playwright.sync_api import sync_playwright

# Два независимых почтовых сервиса (основной + резервный) для защиты от лимитов на 5 человек
MAIL_APIS = ["https://api.mail.tm", "https://api.mail.gw"]
TRIAL_URL = "https://power-vps.pro/free-trial/"
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN")
MAX_RETRIES_PER_WAVE = 3

# Твой ID — ошибки и скриншоты при сбоях приходят ТОЛЬКО сюда
ADMIN_CHAT_ID = os.environ.get("CHAT_ID_1")

# Все 5 пользователей
USERS = [
    {"name": "Пользователь 1 (Ты)", "chat_id": os.environ.get("CHAT_ID_1")},
    {"name": "Пользователь 2 (Друг 1)", "chat_id": os.environ.get("CHAT_ID_2")},
    {"name": "Пользователь 3 (Друг 2)", "chat_id": os.environ.get("CHAT_ID_3")},
    {"name": "Пользователь 4 (Друг 3)", "chat_id": os.environ.get("CHAT_ID_4")},
    {"name": "Пользователь 5 (Друг 4)", "chat_id": os.environ.get("CHAT_ID_5")},
]

def create_temp_email():
    """Создаёт временную почту, чередуя mail.tm и mail.gw при любых задержках"""
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
    """Отправляет отчёт об ошибке ТОЛЬКО тебе (ADMIN_CHAT_ID), чтобы не спамить друзьям"""
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
    """Ищет видимое поле ввода почты на странице и внутри всех iframe"""
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
    """По очереди нажимает кнопки открытия E-mail формы, пока не появится поле ввода почты"""
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

def extract_from_sub_page(page):
    page_url = page.url
    happ_deeplink = None
    try:
        happ_tab = page.get_by_text("Happ", exact=True).first
        if happ_tab.count() > 0 and happ_tab.is_visible():
            happ_tab.click()
            page.wait_for_timeout(1000)

        add_btn = page.locator('a:has-text("Добавить подписку"), button:has-text("Добавить подписку")').first
        if add_btn.count() > 0 and add_btn.is_visible():
            href = add_btn.get_attribute("href")
            if href:
                happ_deeplink = href
    except Exception:
        pass

    if not happ_deeplink:
        content = page.content()
        found_happ = re.findall(r'(happ://[^\s"\'<>]+)', content)
        if found_happ:
            happ_deeplink = found_happ[0]

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

        # 1. Открываем форму ввода почты
        email_input, target_frame = open_email_form(page)
        if not email_input:
            raise RuntimeError("После нажатия кнопок 'Получить на E-mail' и 'Получить VPN' поле ввода почты не появилось")

        # 2. Заполняем обязательное поле «Ваше имя»
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

        # 3. Заполняем поле E-mail
        email_input.fill(email)
        page.wait_for_timeout(500)

        # Отмечаем галочки согласия
        checkboxes = target_frame.locator('input[type="checkbox"]')
        for i in range(checkboxes.count()):
            cb = checkboxes.nth(i)
            if cb.is_visible() and not cb.is_checked():
                cb.check(force=True)

        # 4. Нажимаем кнопку получения доступа
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

        page.wait_for_timeout(6000)

        # 5. Проверяем, появилась ли ссылка на странице или в письме
        after_links = page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)")
        new_links = [
            l for l in after_links
            if l not in before_links
            and not l.startswith("javascript:")
            and not l.startswith("mailto:")
            and not l.endswith("#")
        ]

        if page.locator('text="Добавить подписку"').count() > 0 or page.url != TRIAL_URL:
            sub_page_url, happ_link = extract_from_sub_page(page)
        elif new_links:
            target = new_links[0]
            print(f"[+] Найдена новая ссылка на странице: {target}")
            if target.startswith("happ://"):
                happ_link = target
            else:
                page.goto(target, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(3000)
                sub_page_url, happ_link = extract_from_sub_page(page)
        else:
            print("[*] Проверяем письмо на почте...")
            email_links = wait_for_email_link(api_base, token, timeout=60)
            for link in email_links:
                print(f"[+] Ссылка из письма: {link}")
                if link.startswith("happ://"):
                    happ_link = link
                    break
                page.goto(link, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(3000)
                sub_page_url, happ_link = extract_from_sub_page(page)
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
        print(f"[-] Попытка {attempt} не дала ссылку. Ждём {wait_sec} сек...")
        time.sleep(wait_sec)
    return last_result

def send_to_telegram(chat_id, sub_page_url, happ_link):
    text_lines = ["🔥 <b>Твоя новая подписка VPN на 3 дня готова!</b>\n"]
    if sub_page_url and sub_page_url.startswith("http"):
        text_lines.append(f"🌐 <b>Страница подписки:</b>\n<code>{sub_page_url}</code>\n")
    if happ_link:
        text_lines.append(f"📲 <b>Прямая ссылка для Happ:</b>\n<code>{happ_link}</code>\n")

    text_lines.append(
        "<b>Как добавить (на iPhone и Mac):</b>\n"
        "1. Нажми кнопку <b>«➕ Открыть страницу подписки»</b> ниже и нажми <b>«+ Добавить подписку»</b>.\n"
        "2. Либо скопируй ссылку выше, открой <b>Happ</b> → нажми <b>«+»</b> → <b>«Вставить из буфера»</b>."
    )

    payload = {
        "chat_id": chat_id,
        "text": "\n".join(text_lines),
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if sub_page_url and sub_page_url.startswith("http"):
        payload["reply_markup"] = {
            "inline_keyboard": [
                [{"text": "➕ Открыть страницу подписки", "url": sub_page_url}]
            ]
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
    """Узнаёт у Telegram реальные имена и @username всех CHAT_ID и присылает список админу"""
    lines = ["📋 <b>Кто под каким номером записан в GitHub:</b>\n"]
    for idx, user in enumerate(USERS, start=1):
        cid = user.get("chat_id")
        if not cid:
            lines.append(f"<b>{idx} (CHAT_ID_{idx}):</b> ❌ <i>не задан</i>")
            continue
        try:
            r = requests.get(
                f"https://api.telegram.org/bot{TG_BOT_TOKEN}/getChat",
                params={"chat_id": cid},
                timeout=10
            ).json()
            if r.get("ok"):
                chat = r["result"]
                first_name = chat.get("first_name", "")
                last_name = chat.get("last_name", "")
                full_name = f"{first_name} {last_name}".strip()
                username = f"@{chat['username']}" if chat.get("username") else "без юзернейма"
                lines.append(f"<b>{idx} (CHAT_ID_{idx}):</b> {full_name} ({username}) — <code>{cid}</code>")
            else:
                lines.append(f"<b>{idx} (CHAT_ID_{idx}):</b> ID <code>{cid}</code> (не нажал /start у бота)")
        except Exception as e:
            lines.append(f"<b>{idx} (CHAT_ID_{idx}):</b> ID <code>{cid}</code> (ошибка: {e})")

    requests.post(
        f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",
        json={"chat_id": ADMIN_CHAT_ID, "text": "\n".join(lines), "parse_mode": "HTML"},
        timeout=15
    )

def main():
    if not TG_BOT_TOKEN:
        print("[-] КРИТИЧЕСКАЯ ОШИБКА: Секрет TG_BOT_TOKEN не задан!")
        raise SystemExit(1)

    target_input = os.environ.get("TARGET_USER", "").strip()

    # Если выбрана опция "0 — Прислать мне список"
    if target_input.startswith("0"):
        print("[*] Отправляем список пользователей админу...")
        send_users_list_to_admin()
        return

    if target_input and target_input[0] in ("1", "2", "3", "4", "5"):
        idx = int(target_input[0]) - 1
        selected_user = USERS[idx]
        print(f"[*] Одиночный запуск: отправляем ТОЛЬКО для «{selected_user['name']}» (таймер 71ч не трогаем)")
        active_users = [selected_user] if selected_user.get("chat_id") else []
    else:
        active_users = [u for u in USERS if u.get("chat_id")]

    if not active_users:
        print("[-] У выбранного пользователя не задан CHAT_ID в Secrets!")
        raise SystemExit(1)

    failed_users = []
    success_count = 0

    with sync_playwright() as p:
        # ВОЛНА 1: Основной проход
        browser = p.chromium.launch(headless=True)
        for user in active_users:
            sub_page_url, happ_link, screen, err, email = get_trial_with_retries(browser, user["name"])
            if sub_page_url or happ_link:
                print(f"[+] Успешно для {user['name']}! Страница: {sub_page_url} | Happ: {happ_link}")
                send_to_telegram(user["chat_id"], sub_page_url, happ_link)
                success_count += 1
            else:
                print(f"[-] {user['name']} отложен на повторную волну через 2 минуты.")
                failed_users.append((user, screen, err, email))
            time.sleep(4)
        browser.close()

        # ВОЛНА 2: Повтор через 2 минуты при сбое
        if failed_users:
            print(f"\n[*] Ждём 120 секунд (2 минуты) перед повторной попыткой для {len(failed_users)} чел...")
            time.sleep(120)
            browser = p.chromium.launch(headless=True)
            still_failed = []

            for user, _, _, _ in failed_users:
                sub_page_url, happ_link, screen, err, email = get_trial_with_retries(browser, f"{user['name']} (Волна 2)")
                if sub_page_url or happ_link:
                    print(f"[+] Успешно со 2-й волны для {user['name']}!")
                    send_to_telegram(user["chat_id"], sub_page_url, happ_link)
                    success_count += 1
                else:
                    still_failed.append((user, screen, err, email))
                time.sleep(4)
            browser.close()

            for user, screen, err, email in still_failed:
                caption = (
                    f"⚠️ Не удалось получить ссылку для «{user['name']}» после 2 волн по {MAX_RETRIES_PER_WAVE} попытки (почта {email}).\n"
                    f"Друзьям ничего не отправлено.\n"
                    f"Детали: {err or 'Ссылка не найдена'}"
                )
                send_admin_debug(screen, caption)

    if active_users and success_count == 0:
        print("[-] Ссылка не была получена.")
        raise SystemExit(1)

if __name__ == "__main__":
    main()
