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

USERS = [
    {"name": "Пользователь 1 (Ты)", "chat_id": os.environ.get("CHAT_ID_1")},
    {"name": "Пользователь 2 (Друг 1)", "chat_id": os.environ.get("CHAT_ID_2")},
    {"name": "Пользователь 3 (Друг 2)", "chat_id": os.environ.get("CHAT_ID_3")},
]

def create_temp_email():
    domains = requests.get(f"{MAIL_API}/domains", timeout=15).json()["hydra:member"]
    domain = domains[0]["domain"]
    username = ''.join(random.choices(string.ascii_lowercase + string.digits, k=10))
    email = f"{username}@{domain}"
    password = "TempPassword123!"

    requests.post(f"{MAIL_API}/accounts", json={"address": email, "password": password}, timeout=15)
    token = requests.post(f"{MAIL_API}/token", json={"address": email, "password": password}, timeout=15).json()["token"]
    return email, token

def wait_for_email_link(token, timeout=45):
    headers = {"Authorization": f"Bearer {token}"}
    start = time.time()
    while time.time() - start < timeout:
        msgs = requests.get(f"{MAIL_API}/messages", headers=headers, timeout=15).json().get("hydra:member", [])
        if msgs:
            msg_id = msgs[0]["id"]
            msg = requests.get(f"{MAIL_API}/messages/{msg_id}", headers=headers, timeout=15).json()
            text = msg.get("text", "") or str(msg.get("html", ""))
            links = re.findall(r'(https?://[^\s"\'<>]+|happ://[^\s"\'<>]+)', text)
            if links:
                return links
        time.sleep(3)
    return []

def send_photo_to_telegram(chat_id, photo_path, caption):
    """Отправляет скриншот браузера в Telegram для наглядной проверки"""
    if not TG_BOT_TOKEN or not chat_id or not os.path.exists(photo_path):
        return
    with open(photo_path, "rb") as photo:
        requests.post(
            f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendPhoto",
            data={"chat_id": chat_id, "caption": caption[:1000]},
            files={"photo": photo},
            timeout=20
        )

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
    email, token = create_temp_email()
    print(f"[+] Сгенерирована почта: {email}")

    context = browser.new_context(
        viewport={"width": 1280, "height": 800},
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    )
    page = context.new_page()
    sub_page_url = None
    happ_link = None
    error_text = None
    screenshot_path = "debug_screen.png"

    try:
        # Используем domcontentloaded, чтобы не зависать на 30 секунд
        page.goto(TRIAL_URL, wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(4000)

        # Ищем поле ввода почты (любое видимое текстовое/email поле)
        email_input = page.locator('input[type="email"], input[name*="mail" i], input[placeholder*="mail" i], input[placeholder*="почт" i], input[type="text"]').first
        email_input.wait_for(state="visible", timeout=10000)
        email_input.fill(email)

        # Ищем кнопку отправки
        submit_btn = page.locator('button[type="submit"], input[type="submit"], button:has-text("Получить"), button:has-text("Trial"), button:has-text("Отправить"), button:has-text("Попробовать"), form button').first
        submit_btn.click()
        page.wait_for_timeout(6000)

        if page.locator('text="Добавить подписку"').count() > 0 or page.url != TRIAL_URL:
            sub_page_url, happ_link = extract_from_sub_page(page)
        else:
            links_on_page = re.findall(r'(https?://[^\s"\'<>]+/sub[^\s"\'<>]*|happ://[^\s"\'<>]+)', page.content())
            if links_on_page:
                target = links_on_page[0]
                if target.startswith("http"):
                    page.goto(target, wait_until="domcontentloaded", timeout=20000)
                    page.wait_for_timeout(3000)
                    sub_page_url, happ_link = extract_from_sub_page(page)
                else:
                    happ_link = target
            else:
                print("[*] Проверяем письмо на почте...")
                email_links = wait_for_email_link(token)
                for link in email_links:
                    if link.startswith("happ://"):
                        happ_link = link
                        break
                    page.goto(link, wait_until="domcontentloaded", timeout=20000)
                    page.wait_for_timeout(3000)
                    sub_page_url, happ_link = extract_from_sub_page(page)
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

def send_to_telegram(chat_id, sub_page_url, happ_link):
    text_lines = ["🔄 <b>Твоя новая подписка VPN на 3 дня готова!</b>\n"]

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

    resp = requests.post(f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage", json=payload, timeout=15)
    print(f"[*] Ответ Telegram API: {resp.status_code} {resp.text}")

def main():
    if not TG_BOT_TOKEN:
        print("[-] КРИТИЧЕСКАЯ ОШИБКА: Секрет TG_BOT_TOKEN не задан в Settings -> Secrets!")
        return

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        for user in USERS:
            chat_id = user["chat_id"]
            if not chat_id:
                print(f"[-] Пропуск {user['name']}: секрет CHAT_ID не задан")
                continue

            print(f"\n[*] Генерируем подписку для: {user['name']} (ID: {chat_id})")
            sub_page_url, happ_link, screen, err, email = get_trial_in_incognito(browser)

            if sub_page_url or happ_link:
                print(f"[+] Успешно! Страница: {sub_page_url} | Happ: {happ_link}")
                send_to_telegram(chat_id, sub_page_url, happ_link)
            else:
                caption = (
                    f"⚠️ Скрипт запустился (почта {email}), но не смог забрать ссылку автоматически.\n"
                    f"Вот что сейчас на экране браузера.\n"
                    f"Детали: {err or 'Ссылка не найдена на странице или в письме'}"
                )
                send_photo_to_telegram(chat_id, screen, caption)

            time.sleep(3)

        browser.close()

if __name__ == "__main__":
    main()
