import argparse
import html
import itertools
import json
import logging
import os
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


HH_API_URL = "https://api.hh.ru/vacancies"
TELEGRAM_API_URL = "https://api.telegram.org/bot{}/sendMessage"
LOGGER = logging.getLogger("hh-vacancy-notifier")


def load_config(path):
    with open(path, encoding="utf-8") as config_file:
        config = json.load(config_file)

    if not isinstance(config.get("query"), str) or not config["query"].strip():
        raise ValueError("В config.json нужно указать непустой поисковый запрос в поле query.")
    contact_email = config.get("contact_email")
    if (
        not isinstance(contact_email, str)
        or "@" not in contact_email
        or any(character.isspace() for character in contact_email)
        or "example.com" in contact_email
    ):
        raise ValueError("Укажите свой контактный email в поле contact_email для User-Agent hh.ru.")

    for key in ("employment", "experience"):
        if not isinstance(config.get(key, []), list):
            raise ValueError(f"Поле {key} должно быть списком.")

    config.setdefault("employment", [])
    config.setdefault("experience", [])
    config.setdefault("period_days", 1)
    config.setdefault("per_page", 50)
    config.setdefault("interval_seconds", 900)
    config.setdefault("area", None)
    config.setdefault("state_file", "sent_vacancies.json")

    if not 1 <= int(config["period_days"]) <= 30:
        raise ValueError("period_days должен быть от 1 до 30.")
    if not 1 <= int(config["per_page"]) <= 100:
        raise ValueError("per_page должен быть от 1 до 100.")
    if int(config["interval_seconds"]) < 60:
        raise ValueError("interval_seconds должен быть не меньше 60.")

    config["period_days"] = int(config["period_days"])
    config["per_page"] = int(config["per_page"])
    config["interval_seconds"] = int(config["interval_seconds"])
    return config


def request_json(url, data=None, headers=None):
    request = Request(url, data=data, headers=headers or {})
    try:
        with urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError) as error:
        raise RuntimeError(f"Ошибка запроса к {url.split('/')[2]}: {error}") from error


def search_params(config, employment=None, experience=None):
    params = {
        "text": config["query"],
        "period": config["period_days"],
        "per_page": config["per_page"],
        "order_by": "publication_time",
    }
    if config.get("area") is not None:
        params["area"] = config["area"]
    if employment:
        params["employment"] = employment
    if experience:
        params["experience"] = experience
    return params


def fetch_vacancies(config):
    employments = config["employment"] or [None]
    experiences = config["experience"] or [None]
    vacancies = {}

    for employment, experience in itertools.product(employments, experiences):
        query = urlencode(search_params(config, employment, experience))
        result = request_json(
            f"{HH_API_URL}?{query}",
            headers={
                "User-Agent": f"hh-vacancy-notifier/1.0 ({config['contact_email']})",
                "Accept": "application/json",
            },
        )
        for vacancy in result.get("items", []):
            vacancies[str(vacancy["id"])] = vacancy

    return sorted(
        vacancies.values(),
        key=lambda vacancy: vacancy.get("published_at", ""),
        reverse=True,
    )


def format_vacancy(vacancy):
    title = html.escape(vacancy.get("name", "Вакансия"))
    employer = vacancy.get("employer") or {}
    employer_name = html.escape(employer.get("name", "Работодатель не указан"))
    location = vacancy.get("area") or {}
    location_name = html.escape(location.get("name", ""))
    salary = vacancy.get("salary") or {}
    salary_text = ""
    if salary:
        lower = salary.get("from")
        upper = salary.get("to")
        currency = salary.get("currency") or ""
        if lower is not None and upper is not None:
            salary_text = f"{lower}–{upper} {currency}"
        elif lower is not None:
            salary_text = f"от {lower} {currency}"
        elif upper is not None:
            salary_text = f"до {upper} {currency}"

    lines = [f"<b>{title}</b>", employer_name]
    if location_name:
        lines.append(location_name)
    if salary_text:
        lines.append(html.escape(salary_text))
    lines.append(html.escape(vacancy["alternate_url"]))
    return "\n".join(lines)


def send_telegram(token, chat_id, message):
    body = urlencode(
        {
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        }
    ).encode("utf-8")
    result = request_json(
        TELEGRAM_API_URL.format(token),
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    if not result.get("ok"):
        raise RuntimeError(f"Telegram не принял сообщение: {result}")


def load_sent_ids(path):
    try:
        with open(path, encoding="utf-8") as state_file:
            return set(json.load(state_file))
    except FileNotFoundError:
        return set()


def save_sent_ids(path, sent_ids):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    with open(temporary_path, "w", encoding="utf-8") as state_file:
        json.dump(sorted(sent_ids), state_file, ensure_ascii=False)
    os.replace(temporary_path, path)


def run_once(config, dry_run=False):
    state_path = config["state_file"]
    sent_ids = load_sent_ids(state_path)
    vacancies = fetch_vacancies(config)
    new_count = 0

    for vacancy in vacancies:
        vacancy_id = str(vacancy["id"])
        if vacancy_id in sent_ids:
            continue

        message = format_vacancy(vacancy)
        if dry_run:
            print(message.replace("<b>", "").replace("</b>", ""))
            print()
        else:
            token = config.get("telegram_bot_token")
            chat_id = config.get("telegram_chat_id")
            if not token or token == "YOUR_BOT_TOKEN" or not chat_id or chat_id == "YOUR_CHAT_ID":
                raise ValueError("Заполните telegram_bot_token и telegram_chat_id в config.json.")
            send_telegram(token, chat_id, message)
            sent_ids.add(vacancy_id)
            save_sent_ids(state_path, sent_ids)
        new_count += 1

    if new_count == 0:
        LOGGER.info("Новых вакансий нет.")
    elif dry_run:
        LOGGER.info("Найдено новых вакансий: %s (тестовый режим, не отправлено).", new_count)
    else:
        LOGGER.info("Отправлено новых вакансий: %s.", new_count)
    return new_count


def main():
    parser = argparse.ArgumentParser(description="Поиск новых вакансий hh.ru с отправкой в Telegram.")
    parser.add_argument("--config", default="config.json", help="Путь к JSON-файлу настроек.")
    parser.add_argument("--once", action="store_true", help="Выполнить один цикл и завершиться.")
    parser.add_argument("--dry-run", action="store_true", help="Показать вакансии без отправки в Telegram.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        config = load_config(args.config)
        config["state_file"] = str(Path(args.config).resolve().parent / config["state_file"])
        if args.dry_run:
            run_once(config, dry_run=True)
            return 0

        while True:
            try:
                run_once(config)
            except Exception:
                LOGGER.exception("Ошибка цикла поиска.")
                if args.once:
                    return 1
            if args.once:
                return 0
            time.sleep(config["interval_seconds"])
    except (OSError, ValueError, json.JSONDecodeError, RuntimeError) as error:
        LOGGER.error("%s", error)
        return 1


if __name__ == "__main__":
    sys.exit(main())