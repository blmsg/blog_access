import os
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import AutoVisitV3 as app


class ArticleLink:
    def __init__(self, href):
        self.href = href

    def get_attribute(self, name):
        return self.href if name == "href" else None


class ArticleListPage:
    def __init__(self, hrefs):
        self.links = [ArticleLink(href) for href in hrefs]

    def query_selector_all(self, selector):
        if selector != app.ARTICLE_SELECTOR:
            return []
        return self.links


class NavigationPage:
    def __init__(self, fail_first_navigation=False):
        self.fail_first_navigation = fail_first_navigation
        self.goto_calls = []
        self.load_states = []

    def goto(self, url, timeout=None):
        self.goto_calls.append((url, timeout))
        if self.fail_first_navigation and len(self.goto_calls) == 1:
            raise RuntimeError("article unavailable")

    def wait_for_load_state(self, state):
        self.load_states.append(state)


class FailingHomepagePage:
    def __init__(self):
        self.goto_calls = []

    def add_init_script(self, script):
        pass

    def goto(self, url, timeout=None):
        self.goto_calls.append((url, timeout))
        if len(self.goto_calls) == 1:
            raise RuntimeError("homepage unavailable")

    def wait_for_load_state(self, state):
        pass


class FakeContext:
    def __init__(self, page):
        self.page = page
        self.cookies = []

    def add_cookies(self, cookies):
        self.cookies.extend(cookies)

    def new_page(self):
        return self.page


class FakeBrowser:
    def __init__(self, page):
        self.context = FakeContext(page)
        self.closed = False

    def new_context(self, **kwargs):
        return self.context

    def close(self):
        self.closed = True


class FakePlaywright:
    def __init__(self, page):
        self.browser = FakeBrowser(page)
        self.chromium = self

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        pass

    def launch(self, **kwargs):
        return self.browser


class AutoVisitLogicTests(unittest.TestCase):
    def setUp(self):
        self.settings = app.Settings(homepage_url="https://blog.example.test")
        self.stats = app.VisitStats()

    def test_settings_load_from_env_without_exposing_credentials(self):
        environment = {
            "HOMEPAGE_URL": "https://blog.example.test/",
            "BOT_TOKEN": "synthetic-test-token",
            "CHAT_ID": "synthetic-chat-id",
            "PROXY_FILE": "private-proxies.txt",
        }
        with (
            patch.dict(os.environ, environment, clear=True),
            patch.object(app, "load_dotenv"),
        ):
            settings = app.Settings.from_env()

        self.assertEqual(settings.homepage_url, "https://blog.example.test")
        self.assertEqual(settings.proxy_file, Path("private-proxies.txt"))
        self.assertTrue(settings.telegram_enabled)
        self.assertNotIn(environment["BOT_TOKEN"], repr(settings))
        self.assertNotIn(environment["CHAT_ID"], repr(settings))

    def test_blank_optional_env_values_use_defaults(self):
        environment = {
            "HOMEPAGE_URL": " ",
            "BOT_TOKEN": " ",
            "CHAT_ID": "",
            "PROXY_FILE": "",
        }
        with (
            patch.dict(os.environ, environment, clear=True),
            patch.object(app, "load_dotenv"),
        ):
            settings = app.Settings.from_env()

        self.assertEqual(settings.homepage_url, app.DEFAULT_HOMEPAGE_URL)
        self.assertEqual(settings.proxy_file, app.DEFAULT_PROXY_FILE)
        self.assertFalse(settings.telegram_enabled)

    def test_settings_reject_homepage_credentials(self):
        with self.assertRaises(ValueError):
            app.Settings(homepage_url="https://user:password@example.test")

    def test_settings_reject_invalid_homepage_port(self):
        with self.assertRaisesRegex(ValueError, "端口"):
            app.Settings(homepage_url="https://blog.example.test:notaport")

        with self.assertRaisesRegex(ValueError, "端口"):
            app.Settings(homepage_url="https://blog.example.test:0")

    def test_select_random_article_normalizes_relative_url(self):
        page = ArticleListPage([
            "https://blog.example.test/2024/12/31/older",
            "/2025/01/02/newer?tracking=private",
        ])

        with patch.object(app.random, "choice", side_effect=lambda articles: articles[0]):
            article_url = app.select_random_article_url(
                page,
                self.settings.homepage_url,
            )

        self.assertEqual(
            article_url,
            "https://blog.example.test/2025/01/02/newer?tracking=private",
        )
        self.assertEqual(
            app.safe_url_label(article_url),
            "https://blog.example.test/2025/01/02/newer",
        )

    def test_successful_article_visit_returns_home_and_counts_success(self):
        page = NavigationPage()

        with (
            patch.object(app, "scroll_page"),
            patch.object(app.time, "sleep"),
        ):
            result = app.visit_article_and_return_home(
                page,
                "https://blog.example.test/2025/01/02/post",
                self.settings.homepage_url,
                self.stats,
            )

        self.assertTrue(result)
        self.assertEqual(self.stats.article_visits, 1)
        self.assertEqual(self.stats.successful_visits, 1)
        self.assertEqual(self.stats.failed_visits, 0)
        self.assertEqual(self.stats.total_visits, 1)
        self.assertEqual(
            page.goto_calls,
            [
                (
                    "https://blog.example.test/2025/01/02/post",
                    app.NAVIGATION_TIMEOUT_MS,
                ),
                (self.settings.homepage_url, app.NAVIGATION_TIMEOUT_MS),
            ],
        )

    def test_failed_article_visit_is_not_counted_as_success(self):
        page = NavigationPage(fail_first_navigation=True)

        with (
            patch.object(app, "scroll_page"),
            patch.object(app.time, "sleep"),
        ):
            result = app.visit_article_and_return_home(
                page,
                "https://blog.example.test/2025/01/02/post",
                self.settings.homepage_url,
                self.stats,
            )

        self.assertFalse(result)
        self.assertEqual(self.stats.article_visits, 0)
        self.assertEqual(self.stats.successful_visits, 0)
        self.assertEqual(self.stats.failed_visits, 1)
        self.assertEqual(self.stats.total_visits, 1)

    def test_homepage_failure_counts_as_total_visit_and_closes_browser(self):
        page = FailingHomepagePage()
        fake_playwright = FakePlaywright(page)
        stats = app.VisitStats(last_update=time.time())

        with (
            patch.object(app, "sync_playwright", return_value=fake_playwright),
            patch.object(app, "get_valid_proxy", return_value=None),
            patch.object(app, "scroll_page"),
            patch.object(app, "select_random_article_url", return_value=None),
            patch.object(app, "send_telegram_message") as send_message,
        ):
            result = app.run_playwright(self.settings, stats)

        self.assertIs(result, stats)
        self.assertEqual(stats.total_visits, 2)
        self.assertEqual(stats.homepage_visits, 1)
        self.assertEqual(stats.failed_visits, 1)
        self.assertEqual(stats.successful_visits, 0)
        self.assertEqual(stats.article_visits, 0)
        self.assertEqual(len(stats.unique_cookies), 1)
        self.assertEqual(len(stats.unique_devices), 1)
        self.assertTrue(fake_playwright.browser.closed)
        send_message.assert_not_called()

    def test_reused_stats_are_reset_for_each_run(self):
        page = FailingHomepagePage()
        fake_playwright = FakePlaywright(page)
        stats = app.VisitStats(
            total_visits=99,
            failed_visits=98,
            successful_visits=97,
            homepage_visits=96,
            article_visits=95,
            last_update=time.time(),
        )
        stats.unique_cookies.add("old-cookie")
        stats.unique_devices.add("old-device")

        with (
            patch.object(app, "sync_playwright", return_value=fake_playwright),
            patch.object(app, "get_valid_proxy", return_value=None),
            patch.object(app, "scroll_page"),
            patch.object(app, "select_random_article_url", return_value=None),
            patch.object(app, "send_telegram_message") as send_message,
        ):
            result = app.run_playwright(self.settings, stats)

        self.assertIs(result, stats)
        self.assertEqual(stats.total_visits, 2)
        self.assertEqual(stats.homepage_visits, 1)
        self.assertEqual(stats.failed_visits, 1)
        self.assertEqual(stats.successful_visits, 0)
        self.assertEqual(stats.article_visits, 0)
        self.assertNotIn("old-cookie", stats.unique_cookies)
        self.assertNotIn("old-device", stats.unique_devices)
        send_message.assert_not_called()

    def test_proxy_credentials_are_split_for_each_http_client(self):
        endpoint = app.ProxyEndpoint.parse(
            "127.0.0.1:8080:http|synthetic-user:synthetic:password"
        )
        self.assertEqual(
            endpoint.browser_server,
            "http://127.0.0.1:8080",
        )
        self.assertEqual(
            endpoint.requests_url,
            "http://synthetic-user:synthetic%3Apassword@127.0.0.1:8080",
        )
        self.assertEqual(
            endpoint.playwright_proxy,
            {
                "server": endpoint.browser_server,
                "username": "synthetic-user",
                "password": "synthetic:password",
            },
        )
        self.assertNotIn("synthetic-user", repr(endpoint))
        self.assertNotIn("synthetic:password", repr(endpoint))

        response = Mock(status_code=200)
        with (
            patch.object(
                app,
                "load_proxies_from_file",
                return_value=[
                    "127.0.0.1:8080:http|synthetic-user:synthetic:password"
                ],
            ),
            patch.object(app.random, "shuffle"),
            patch.object(app.requests, "get", return_value=response) as request_get,
            patch.object(app.logger, "info") as log_info,
        ):
            proxy_endpoint = app.get_valid_proxy(Path("proxies.txt"))

        self.assertEqual(proxy_endpoint, endpoint)
        request_get.assert_called_once_with(
            "https://www.google.com",
            proxies={
                "http": endpoint.requests_url,
                "https": endpoint.requests_url,
            },
            timeout=app.PROXY_TIMEOUT_SECONDS,
        )
        self.assertEqual(
            app.build_launch_args(endpoint)["proxy"],
            endpoint.playwright_proxy,
        )
        log_output = " ".join(str(call) for call in log_info.call_args_list)
        self.assertNotIn("synthetic-user", log_output)
        self.assertNotIn("synthetic:password", log_output)

    def test_proxy_request_error_does_not_log_credentials(self):
        secret_fragment = "synthetic-proxy-password"
        with (
            patch.object(
                app,
                "load_proxies_from_file",
                return_value=[f"proxy.example.invalid:8080:http|user:{secret_fragment}"],
            ),
            patch.object(app.random, "shuffle"),
            patch.object(
                app.requests,
                "get",
                side_effect=app.requests.RequestException(secret_fragment),
            ),
            patch.object(app.logger, "warning") as log_warning,
        ):
            self.assertIsNone(app.get_valid_proxy(Path("proxies.txt")))

        self.assertNotIn(secret_fragment, str(log_warning.call_args_list))

    def test_telegram_without_credentials_skips_request(self):
        with patch.object(app.requests, "post") as request_post:
            result = app.send_telegram_message(
                self.settings,
                self.stats,
                "report",
                "2026-08-25",
            )

        self.assertFalse(result)
        request_post.assert_not_called()

    def test_telegram_uses_post_body(self):
        settings = app.Settings(
            homepage_url="https://blog.example.test",
            bot_token="synthetic-test-token",
            chat_id="synthetic-chat-id",
        )
        response = Mock(status_code=200)
        with (
            patch.object(app.requests, "post", return_value=response) as request_post,
            patch.object(app.requests, "get") as request_get,
        ):
            result = app.send_telegram_message(
                settings,
                self.stats,
                "report",
                "2026-08-25",
            )

        self.assertTrue(result)
        request_post.assert_called_once_with(
            "https://api.telegram.org/botsynthetic-test-token/sendMessage",
            data={
                "chat_id": "synthetic-chat-id",
                "text": app.build_telegram_message(
                    "report",
                    "2026-08-25",
                    self.stats,
                ),
                "parse_mode": "Markdown",
            },
            timeout=app.TELEGRAM_TIMEOUT_SECONDS,
        )
        request_get.assert_not_called()

    def test_telegram_request_error_does_not_log_token(self):
        token = "synthetic-test-token"
        settings = app.Settings(
            homepage_url="https://blog.example.test",
            bot_token=token,
            chat_id="synthetic-chat-id",
        )
        with (
            patch.object(
                app.requests,
                "post",
                side_effect=app.requests.RequestException(
                    f"request failed for https://api.telegram.org/bot{token}/sendMessage"
                ),
            ),
            patch.object(app.logger, "error") as log_error,
        ):
            result = app.send_telegram_message(
                settings,
                self.stats,
                "report",
                "2026-08-25",
            )

        self.assertFalse(result)
        self.assertNotIn(token, str(log_error.call_args_list))


if __name__ == "__main__":
    unittest.main()
