#!/usr/bin/env python3
"""Внешний сторож для мониторинга паркинга Михалковский.
Запускается из GitHub Actions по расписанию. Проверяет, что сервер changedetection
жив и проверки идут регулярно. При проблеме шлёт сообщение в Telegram-группу через
наш же бот. Все параметры — из переменных окружения (GitHub Secrets)."""
import os, sys, json, time, urllib.request, urllib.parse

SERVER = os.environ["SERVER_URL"].rstrip("/")      # напр. http://201.24.60.188:5000
BASE = SERVER + "/api/v1"
APIKEY = os.environ["CD_API_KEY"]
TG_TOKEN = os.environ["TG_BOT_TOKEN"]
TG_CHAT = os.environ["TG_CHAT_ID"]
RUN_MODE = os.environ.get("RUN_MODE", "schedule")   # 'workflow_dispatch' при ручном запуске
STALE_FACTOR = 3                                     # проверка «протухла», если старше 3× интервала


def tg(text):
    data = urllib.parse.urlencode({"chat_id": TG_CHAT, "text": text}).encode()
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    try:
        urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=20)
    except Exception as e:
        print("Telegram send error:", e)


def api(path):
    req = urllib.request.Request(BASE + path, headers={"x-api-key": APIKEY})
    return json.load(urllib.request.urlopen(req, timeout=20))


def main():
    # 1) Сервер вообще жив?
    try:
        si = api("/systeminfo")
    except Exception as e:
        tg("🔴 СТОРОЖ: сервер мониторинга НЕ отвечает!\n\n"
           f"{SERVER}\n{repr(e)[:250]}\n\n"
           "Вероятно: упал VPS, кончился баланс Timeweb или лёг Docker.")
        print("SERVER DOWN:", e)
        return

    # 2) Проверки идут регулярно?
    now = int(time.time())
    problems = []
    try:
        for uuid, w in api("/watch").items():
            full = api("/watch/" + uuid)
            iv = full["time_between_check"]
            interval = (iv["weeks"] * 604800 + iv["days"] * 86400 + iv["hours"] * 3600
                        + iv["minutes"] * 60 + iv["seconds"])
            age = now - (w.get("last_checked") or 0)
            if interval and age > interval * STALE_FACTOR:
                problems.append(f"«{w['title']}»: последняя проверка {age // 60} мин назад "
                                f"(интервал {interval // 60} мин)")
            if w.get("last_error"):
                problems.append(f"«{w['title']}»: ошибка загрузки страницы — {w.get('last_error')}")
    except Exception as e:
        problems.append("не удалось прочитать список проверок: " + repr(e)[:200])

    if problems:
        tg("🟠 СТОРОЖ: проверки идут НЕ штатно!\n\n"
           + "\n".join("• " + p for p in problems)
           + "\n\nСервер отвечает, но мониторинг сбоит — загляни в панель.")
        print("PROBLEMS:", problems)
        return

    # Всё в порядке. Молчим по расписанию, но при ручном запуске подтверждаем, что сторож работает.
    uptime_h = int(si.get("uptime", 0) // 3600)
    print(f"OK: healthy, uptime {uptime_h}h, watches ok")
    if RUN_MODE == "workflow_dispatch":
        tg(f"✅ Сторож проверил вручную — всё в порядке.\n"
           f"Сервер жив (аптайм {uptime_h} ч), обе проверки свежие.")


if __name__ == "__main__":
    main()
