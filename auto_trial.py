import os
import re
import time
import random
import string
from urllib.parse import quote
import requests
from playwright.sync_api import sync_playwright

MAIL_API = "https://api.mail.tm"
TRIAL_URL = "https://power-vps.pro/free-trial/"
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN")
GITHUB_REPOSITORY = os.environ.get("GITHUB_REPOSITORY", "")

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
            valid_links = [
                l for l in links
                if not any(ext in l.lower() for ext in [".png", ".jpg", ".jpeg", ".svg", ".css", ".js", "w3.org"])
            ]
            if valid_links:
                return valid_links
        time.sleep(3)
    return []

def send_photo_to_telegram(chat_id, photo_path, caption):
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
    is_valid_sub = False
    try:
        if page.locator('text="Добавить подписку"').count() > 0 or page.locator('text="Happ"').count() > 0:
            is_valid_sub = True

        happ_tab = page.get_by_text("Happ", exact=False).first
        if happ_tab.is_visible():
            happ_tab.click()
            page.wait_for_timeout(1000)

        add_btn = page.locator('a:has-text("Добавить подписку"), button:has-text("Добавить подписку")').first
        if add_btn.is_visible():
            is_valid_sub = True
            href = add_btn.get_attribute("href")
            if href:
                happ_deeplink = href
    except Exception:
        pass

    if not happ_deeplink:
        try:
            content = page.content()
            found_happ = re.findall(r'(happ://[^\s"\'<>]+)', content)
            if found_happ:
                happ_deeplink = found_happ[0]
                is_valid_sub = True
        except Exception:
            pass

    if is_valid_sub or ("power-vps.pro" not in page_url and page_url.startswith("http")):
        return page_url, happ_deeplink

    return None, None

def get_trial_in_incognito(browser):
    email, token = create_temp_email()
    print("[+] Временная почта сгенерирована")

    context = browser.new_context(
        viewport={"width": 1280, "height": 900},
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
    page = context.new_page()
    sub_page_url = None
    happ_link = None
    error_text = None
    screenshot_path = "debug_screen.png"

    try:
        page.goto(TRIAL_URL, wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(3000)

        before_links = set(page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)"))

        # 1. Нажимаем кнопку "Получить на E-mail"
        email_btn = page.get_by_text("Получить на E-mail", exact=False).first
        email_btn.wait_for(state="visible", timeout=10000)
        email_btn.click()
        page.wait_for_timeout(2000)

        # 2. Заполняем поля "Ваше имя" и "Ваш E-mail"
        inputs = page.locator(
            'input:visible:not([type="submit"]):not([type="button"]):not([type="checkbox"]):not([type="hidden"])'
        )
        inputs.first.wait_for(state="visible", timeout=10000)
        count = inputs.count()

        if count >= 2:
            inputs.nth(0).fill("Alex")
            page.wait_for_timeout(500)
            inputs.nth(1).fill(email)
        else:
            inputs.nth(0).fill(email)

        page.wait_for_timeout(500)

        # 3. Нажимаем синюю кнопку "ПОЛУЧИТЬ VPN ДОСТУП"
        submit_btn = page.get_by_text("ПОЛУЧИТЬ VPN ДОСТУП", exact=False).first
        if submit_btn.is_visible():
            submit_btn.click()
        else:
            page.locator('button[type="submit"]:visible, input[type="submit"]:visible').first.click()

        # 4. Ждём завершения навигации и проверяем ссылки
        new_links = []
        for _ in range(12):
            page.wait_for_timeout(1000)
            try:
                page.wait_for_load_state("domcontentloaded", timeout=3000)
                sub_page_url, happ_link = extract_from_sub_page(page)
                if sub_page_url or happ_link:
                    break

                after_links = page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)")
                new_links = [
                    l for l in after_links
                    if l not in before_links
                    and not l.startswith("javascript:")
                    and not l.startswith("mailto:")
                    and not l.endswith("#")
                ]
                if new_links:
                    break
            except Exception:
                continue

        if not (sub_page_url or happ_link) and new_links:
            target = new_links[0]
            if target.startswith("happ://"):
                happ_link = target
            else:
                page.goto(target, wait_until="domcontentloaded", timeout=20000)
                page.wait_for_timeout(3000)
                sub_page_url, happ_link = extract_from_sub_page(page)
                if not sub_page_url:
                    sub_page_url = page.url

        # 5. Если на экране ссылки нет — проверяем входящие письма на почте
        if not (sub_page_url or happ_link):
            print("[*] Проверяем письмо на почте...")
            email_links = wait_for_email_link(token)
            for link in email_links:
                if link.startswith("happ://"):
                    happ_link = link
                    break
                page.goto(link, wait_until="domcontentloaded", timeout=20000)
                page.wait_for_timeout(3000)
                sub_page_url, happ_link = extract_from_sub_page(page)
                if not sub_page_url and "power-vps.pro/free-trial" not in page.url:
                    sub_page_url = page.url
                if sub_page_url or happ_link:
                    break

        try:
            page.wait_for_load_state("domcontentloaded", timeout=5000)
            page.screenshot(path=screenshot_path, full_page=False)
        except Exception:
            pass

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

def build_redirect_url(target_url):
    if not target_url or "/" not in GITHUB_REPOSITORY:
        return None
    owner, repo = GITHUB_REPOSITORY.split("/", 1)
    return f"https://{owner}.github.io/{repo}/?url={quote(target_url, safe='')}"

def send_to_telegram(chat_id, sub_page_url, happ_link):
    target_for_happ = happ_link if happ_link else sub_page_url
    fast_redirect_url = build_redirect_url(target_for_happ)

    text_lines = ["🔥 <b>Твоя новая подписка VPN на 3 дня готова!</b>\n"]
    if sub_page_url and sub_page_url.startswith("http"):
        text_lines.append(f"🌐 <b>Ссылка подписки (нажми, чтобы скопировать):</b>\n<code>{sub_page_url}</code>\n")

    text_lines.append(
        "<b>Как добавить в 1 клик:</b>\n"
        "1. Нажми кнопку <b>«🚀 Сразу добавить в Happ»</b> ниже — откроется приложение Happ с новой подпиской.\n"
        "2. Старую истёкшую подписку в Happ просто смахни влево."
    )

    payload = {
        "chat_id": chat_id,
        "text": "\n".join(text_lines),
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    buttons = []
    if fast_redirect_url:
        buttons.append([{"text": "🚀 Сразу добавить в Happ", "url": fast_redirect_url}])
    if sub_page_url and sub_page_url.startswith("http"):
        buttons.append([{"text": "🌐 Открыть страницу подписки", "url": sub_page_url}])

    if buttons:
        payload["reply_markup"] = {"inline_keyboard": buttons}

    requests.post(f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage", json=payload, timeout=15)

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

            print(f"\n[*] Генерируем подписку для: {user['name']}")
            sub_page_url, happ_link, screen, err, email = get_trial_in_incognito(browser)

            if sub_page_url or happ_link:
                print(f"[+] Подписка для {user['name']} успешно получена и отправлена в Telegram!")
                send_to_telegram(chat_id, sub_page_url, happ_link)
            else:
                caption = (
                    f"⚠️ Скрипт отправил форму, но не увидел ссылку.\n"
                    f"Детали: {err or 'См. скриншот экрана после отправки формы'}"
                )
                send_photo_to_telegram(chat_id, screen, caption)

            time.sleep(3)
        browser.close()

if __name__ == "__main__":
    main()
