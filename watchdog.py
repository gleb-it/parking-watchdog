#!/usr/bin/env python3
"""Внешний сторож для мониторинга паркинга Михалковский.
Запускается из GitHub Actions по расписанию. Проверяет, что сервер changedetection
жив и проверки идут регулярно. При проблеме шлёт сообщение в Telegram-группу через
наш же бот. Все параметры — из переменных окружения (GitHub Secrets).

Разовые сетевые блипы (ошибка загрузки, которая сама проходит) НЕ поднимают тревогу:
при ошибке сторож принудительно перепроверяет страницу и бьёт тревогу только если
ошибка устойчивая. Падение сервера и остановка проверок — тревога сразу."""
import os, sys, json, time, urllib.request, urllib.parse

SERVER = os.environ["SERVER_URL"].rstrip("/")      # напр. http://201.24.60.188:5000
BASE = SERVER + "/api/v1"
APIKEY = os.environ["CD_API_KEY"]
TG_TOKEN = os.environ["TG_BOT_TOKEN"]
TG_CHAT = os.environ["TG_CHAT_ID"]
RUN_MODE = os.environ.get("RUN_MODE", "schedule")   # 'workflow_dispatch' при ручном запуске
STALE_FACTOR = 3                                     # проверка «протухла», если старше 3× интервала
RECHECK_WAIT = 90                                    # сколько ждать подтверждения ошибки, сек


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


def interval_seconds(iv):
    return (iv["weeks"] * 604800 + iv["days"] * 86400 + iv["hours"] * 3600
            + iv["minutes"] * 60 + iv["seconds"])


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

    now = int(time.time())
    problems = []
    errored = {}   # uuid -> (title, last_checked_на_момент_ошибки)
    try:
        watches = api("/watch")
        for uuid, w in watches.items():
            full = api("/watch/" + uuid)
            interval = interval_seconds(full["time_between_check"])
            age = now - (w.get("last_checked") or 0)
            # проверки вообще не идут — это серьёзно, тревога сразу
            if interval and age > interval * STALE_FACTOR:
                problems.append(f"«{w['title']}»: последняя проверка {age // 60} мин назад "
                                f"(интервал {interval // 60} мин) — проверки встали")
            # ошибку загрузки НЕ считаем сразу — сначала подтвердим (см. ниже)
            if w.get("last_error"):
                errored[uuid] = (w["title"], w.get("last_checked") or 0)
    except Exception as e:
        problems.append("не удалось прочитать список проверок: " + repr(e)[:200])

    # 2) Подтверждаем ошибки загрузки: форсим перепроверку и ждём результат.
    #    Разовый блип к этому моменту уже пройдёт — тревоги не будет.
    if errored:
        for uuid in errored:
            try:
                api(f"/watch/{uuid}?recheck=1")
            except Exception:
                pass
        deadline = time.time() + RECHECK_WAIT
        pending = dict(errored)
        while pending and time.time() < deadline:
            time.sleep(10)
            try:
                fresh = api("/watch")
            except Exception:
                continue
            for uuid, (title, prev_lc) in list(pending.items()):
                w = fresh.get(uuid, {})
                if (w.get("last_checked") or 0) > prev_lc:      # перепроверка завершилась
                    if w.get("last_error"):
                        problems.append(f"«{title}»: устойчивая ошибка загрузки страницы — "
                                        f"{w.get('last_error')}")
                    del pending[uuid]
        # то, что не успело перепровериться за отведённое время — не алертим (следующий
        # запуск поймает, если проблема реальна), только логируем
        for uuid, (title, _) in pending.items():
            print(f"NOTE: {title} — перепроверка не завершилась за {RECHECK_WAIT}с, пропускаю")

    if problems:
        tg("🟠 СТОРОЖ: мониторинг работает НЕ штатно!\n\n"
           + "\n".join("• " + p for p in problems)
           + "\n\nСервер отвечает, но есть проблема — загляни в панель.")
        print("PROBLEMS:", problems)
        return

    uptime_h = int(si.get("uptime", 0) // 3600)
    print(f"OK: healthy, uptime {uptime_h}h, watches ok")
    if RUN_MODE == "workflow_dispatch":
        tg(f"✅ Сторож проверил вручную — всё в порядке.\n"
           f"Сервер жив (аптайм {uptime_h} ч), обе проверки свежие.")


if __name__ == "__main__":
    main()
