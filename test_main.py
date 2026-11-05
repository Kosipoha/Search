import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import main


class VacancyNotifierTests(unittest.TestCase):
    def test_format_vacancy_escapes_html_and_includes_link(self):
        vacancy = {
            "name": "<Intern>",
            "employer": {"name": "A & B"},
            "area": {"name": "Москва"},
            "alternate_url": "https://hh.ru/vacancy/123",
        }

        message = main.format_vacancy(vacancy)

        self.assertIn("&lt;Intern&gt;", message)
        self.assertIn("A &amp; B", message)
        self.assertIn("https://hh.ru/vacancy/123", message)

    def test_run_once_sends_new_vacancy_only_once(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            config = {
                "state_file": str(Path(temporary_directory) / "sent.json"),
                "telegram_bot_token": "test-token",
                "telegram_chat_id": "123",
            }
            vacancy = {
                "id": "42",
                "name": "Стажер",
                "alternate_url": "https://hh.ru/vacancy/42",
            }

            with patch.object(main, "fetch_vacancies", return_value=[vacancy]), patch.object(
                main, "send_telegram"
            ) as send_telegram:
                self.assertEqual(main.run_once(config), 1)
                self.assertEqual(main.run_once(config), 0)

            send_telegram.assert_called_once()
            self.assertEqual(json.loads(Path(config["state_file"]).read_text()), ["42"])

    def test_search_params_include_hh_filters(self):
        config = {"query": "стажер", "period_days": 1, "per_page": 50, "area": 1}

        params = main.search_params(config, "PART", "noExperience")

        self.assertEqual(params["employment"], "PART")
        self.assertEqual(params["experience"], "noExperience")
        self.assertEqual(params["area"], 1)

    def test_fetch_uses_contact_email_in_user_agent(self):
        config = {
            "query": "стажер",
            "period_days": 1,
            "per_page": 50,
            "area": None,
            "employment": [],
            "experience": [],
            "contact_email": "developer@company.ru",
        }

        with patch.object(main, "request_json", return_value={"items": []}) as request_json:
            main.fetch_vacancies(config)

        headers = request_json.call_args.kwargs["headers"]
        self.assertEqual(headers["User-Agent"], "hh-vacancy-notifier/1.0 (developer@company.ru)")


if __name__ == "__main__":
    unittest.main()