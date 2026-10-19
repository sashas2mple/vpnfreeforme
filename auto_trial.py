import os
import re
import time
import random
import string
import requests
from playwright.sync_api import sync_playwright

MAIL_API = "https://api.mail.tm"
TRIAL_URL = "https://power-vps.pro/free-trial/"
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN")
MAX_RETRIES = 3  # Количество попыток для каждого пользователя при любой ошибке

USERS = [
    {"name": "Пользователь 1 (Ты)", "chat_id": os.environ.get("CHAT_ID_1")},
    {"name": "Пользователь 2 (Друг 1)", "chat_id": os.environ.get("CHAT_ID_2")},
    {"name": "Пользователь 3 (Друг 2)", "chat_id": os.environ.get("CHAT_ID_3")},
    {"name": "Пользователь 4 (Друг 3)", "chat_id": os.environ.get("CHAT_ID_4")},
]

def create_temp_email():
    """Создаёт временную почту с защитой от сбоев (до 3 попыток)"""
    for attempt in range(1, 4):
        try:
            domains_resp = requests.get(f"{MAIL_API}/domains", timeout=15)
            domains = domains_resp.json().get("hydra:member", [])
            if not domains:
                time.sleep(3)
                continue

            domain = random.choice(domains)["domain"]
            username = ''.join(random.choices(string.ascii_lowercase + string.digits, k=10))
            email = f"{username}@{domain}"
            password = "TempPassword123!"

            requests.post(f"{MAIL_API}/accounts", json={"address": email, "password": password}, timeout=15)
            token_resp = requests.post(f"{MAIL_API}/token", json={"address": email, "password": password}, timeout=15).json()
            token = token_resp.get("token")
            if token:
                return email, token
        except Exception as e:
            print(f"[-] Попытка {attempt}/3 создания почты не удалась: {e}")
            time.sleep(3)
    raise RuntimeError("Не удалось создать временную почту на mail.tm после 3 попыток")

def wait_for_email_link(token, timeout=45):
    headers = {"Authorization": f"Bearer {token}"}
    start = time.time()
    while time.time() - start < timeout:
        try:
            msgs = requests.get(f"{MAIL_API}/messages", headers=headers, timeout=15).json().get("hydra:member", [])
            if msgs:
                msg_id = msgs[0]["id"]
                msg = requests.get(f"{MAIL_API}/messages/{msg_id}", headers=headers, timeout=15).json()
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

def send_photo_to_telegram(chat_id, photo_path, caption):
    if not TG_BOT_TOKEN or not chat_id:
        return
    try:
        if os.path.exists(photo_path):
            with open(photo_path, "rb") as photo:
                requests.post(
                    f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendPhoto",
                    data={"chat_id": chat_id, "caption": caption[:1000]},
                    files={"photo": photo},
                    timeout=20
                )
        else:
            requests.post(
                f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",
                json={"chat_id": chat_id, "text": caption[:4000]},
                timeout=15
            )
    except Exception as e:
        print(f"[-] Ошибка отправки отчёта в Telegram: {e}")

def extract_from_sub_page(page):
    page_url = page.url
    happ_deeplink = None

    try:
        happ_tab = page.locator('text="Happ"').first
        if happ_tab.is_visible(timeout=3000):
            happ_tab.click()
            page.wait_for_timeout(1000)

        add_btn = page.locator('a:has-text("Добавить подписку"), button:has-text("Добавить подписку")').first
        if add_btn.is_visible(timeout=3000):
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
        email, token = create_temp_email()
        print(f"[+] Сгенерирована почта: {email}")

        page.goto(TRIAL_URL, wait_until="domcontentloaded", timeout=25000)
        page.wait_for_timeout(3000)

        before_links = set(page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)"))

        # 1. Нажимаем кнопку "Получить на E-mail"
        email_trigger = page.locator('text="Получить на E-mail", text="Получить VPN"').first
        if email_trigger.is_visible(timeout=5000):
            email_trigger.click()
            page.wait_for_timeout(2000)

        # 2. Вводим почту
        email_input = page.locator(
            'input[type="email"]:visible, input[name*="mail" i]:visible, '
            'input[placeholder*="mail" i]:visible, input[placeholder*="почт" i]:visible, '
            'input[type="text"]:visible'
        ).first
        email_input.wait_for(state="visible", timeout=10000)
        email_input.fill(email)
        page.wait_for_timeout(500)

        # Отмечаем галочки согласия, если они есть
        checkboxes = page.locator('input[type="checkbox"]:visible')
        for i in range(checkboxes.count()):
            cb = checkboxes.nth(i)
            if not cb.is_checked():
                cb.check(force=True)

        # 3. Отправляем форму
        submit_btn = page.locator(
            'form button[type="submit"]:visible, form input[type="submit"]:visible, '
            'button[type="submit"]:visible, input[type="submit"]:visible, '
            'form button:visible'
        ).first

        if submit_btn.is_visible(timeout=3000):
            submit_btn.click()
        else:
            email_input.press("Enter")

        page.wait_for_timeout(6000)

        # 4. Ищем появившуюся ссылку
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
                page.goto(target, wait_until="domcontentloaded", timeout=25000)
                page.wait_for_timeout(3000)
                sub_page_url, happ_link = extract_from_sub_page(page)
        else:
            print("[*] Проверяем письмо на почте...")
            email_links = wait_for_email_link(token)
            for link in email_links:
                print(f"[+] Ссылка из письма: {link}")
                if link.startswith("happ://"):
                    happ_link = link
                    break
                page.goto(link, wait_until="domcontentloaded", timeout=25000)
                page.wait_for_timeout(3000)
                sub_page_url, happ_link = extract_from_sub_page(page)
                if sub_page_url or happ_link:
                    break

        page.screenshot(path=screenshot_path, full_page=False)
    except Exception as e:
        error_text = str(e)
        print(f"[-] Ошибка при оформлении триала: {e}")
        try:
            page.screenshot(path=screenshot_path, full_page=False)
        except Exception:
            pass
    finally:
        context.close()

    return sub_page_url, happ_link, screenshot_path, error_text, email

def get_trial_with_retries(browser, user_name):
    """Пытается получить ссылку до MAX_RETRIES раз, чтобы исключить случайные сбои"""
    last_result = (None, None, "debug_screen.png", "Неизвестная ошибка", "не создана")
    for attempt in range(1, MAX_RETRIES + 1):
        print(f"[*] {user_name}: Попытка {attempt} из {MAX_RETRIES}...")
        sub_page_url, happ_link, screen, err, email = get_trial_in_incognito(browser)
        last_result = (sub_page_url, happ_link, screen, err, email)
        if sub_page_url or happ_link:
            return last_result
        print(f"[-] Попытка {attempt} не дала ссылку. Ждём 5 секунд перед повтором...")
        time.sleep(5)
    return last_result

def send_to_telegram(chat_id, sub_page_url, happ_link):
    text_lines = ["🔄 <b>Твоя новая подписка VPN (каждые 71 ч.) готова!</b>\n"]

    if sub_page_url and sub_page_url.startswith("http"):
        text_lines.append(f"🌐 <b>Страница подписки:</b>\n<code>{sub_page_url}</code>\n")

    if happ_link:
        text_lines.append(f"⚡ <b>Прямая ссылка для Happ:</b>\n<code>{happ_link}</code>\n")

    text_lines.append(
        "💡 <b>Как добавить (на iPhone и Mac):</b>\n"
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

    for _ in range(3):
        try:
            resp = requests.post(f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage", json=payload, timeout=15)
            if resp.status_code == 200:
                break
        except Exception as e:
            print(f"[-] Ошибка отправки в Telegram: {e}")
            time.sleep(2)

def main():
    if not TG_BOT_TOKEN:
        print("[-] КРИТИЧЕСКАЯ ОШИБКА: Секрет TG_BOT_TOKEN не задан!")
        return

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        for user in USERS:
            chat_id = user["chat_id"]
            if not chat_id:
                continue

            try:
                sub_page_url, happ_link, screen, err, email = get_trial_with_retries(browser, user["name"])

                if sub_page_url or happ_link:
                    print(f"[+] Успешно для {user['name']}! Страница: {sub_page_url} | Happ: {happ_link}")
                    send_to_telegram(chat_id, sub_page_url, happ_link)
                else:
                    caption = (
                        f"⚠️ Все {MAX_RETRIES} попытки получить ссылку не удались (последняя почта {email}).\n"
                        f"Детали: {err or 'Ссылка не найдена'}"
                    )
                    send_photo_to_telegram(chat_id, screen, caption)
            except Exception as e:
                print(f"[-] Критическая ошибка в цикле для {user['name']}: {e}")
                send_photo_to_telegram(chat_id, "debug_screen.png", f"⚠️ Ошибка скрипта: {e}")

            time.sleep(3)

        browser.close()

if __name__ == "__main__":
    main()
