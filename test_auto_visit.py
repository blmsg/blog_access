import unittest
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
        if selector != "a.article-title":
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

    def add_cookies(self, cookies):
        pass

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
        self.original_homepage = app.HOMEPAGE_URL
        app.HOMEPAGE_URL = "https://blog.example.test"
        app.visit_data.update({
            "article_visits": 0,
            "failed_visits": 0,
            "successful_visits": 0,
            "total_visits": 0,
        })

    def tearDown(self):
        app.HOMEPAGE_URL = self.original_homepage

    def test_select_random_article_normalizes_relative_url(self):
        page = ArticleListPage([
            "https://blog.example.test/2024/12/31/older",
            "/2025/01/02/newer",
        ])

        with patch.object(app.random, "choice", side_effect=lambda articles: articles[0]):
            article_url = app.select_random_article_url(page)

        self.assertEqual(
            article_url,
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
            )

        self.assertTrue(result)
        self.assertEqual(app.visit_data["article_visits"], 1)
        self.assertEqual(app.visit_data["successful_visits"], 1)
        self.assertEqual(app.visit_data["failed_visits"], 0)
        self.assertEqual(app.visit_data["total_visits"], 1)
        self.assertEqual(
            page.goto_calls,
            [
                ("https://blog.example.test/2025/01/02/post", 30000),
                ("https://blog.example.test", 30000),
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
            )

        self.assertFalse(result)
        self.assertEqual(app.visit_data["article_visits"], 0)
        self.assertEqual(app.visit_data["successful_visits"], 0)
        self.assertEqual(app.visit_data["failed_visits"], 1)
        self.assertEqual(app.visit_data["total_visits"], 1)

    def test_homepage_failure_counts_as_total_visit(self):
        page = FailingHomepagePage()
        fake_playwright = FakePlaywright(page)
        app.visit_data["last_update"] = app.time.time()

        with (
            patch.object(app, "sync_playwright", return_value=fake_playwright),
            patch.object(app, "get_valid_proxy", return_value=None),
            patch.object(app, "generate_unique_cookie", return_value={}),
            patch.object(app, "scroll_page"),
            patch.object(app, "select_random_article_url", return_value=None),
            patch.object(app, "send_telegram_message"),
        ):
            app.run_playwright()

        self.assertEqual(app.visit_data["total_visits"], 2)
        self.assertEqual(app.visit_data["homepage_visits"], 1)
        self.assertEqual(app.visit_data["failed_visits"], 1)
        self.assertEqual(app.visit_data["successful_visits"], 0)
        self.assertEqual(app.visit_data["article_visits"], 0)

    def test_proxy_password_is_encoded_and_not_logged(self):
        response = Mock(status_code=200)
        with (
            patch.object(
                app,
                "load_proxies_from_file",
                return_value=["127.0.0.1:8080:http|user:pa:ss"],
            ),
            patch.object(app.random, "shuffle"),
            patch.object(app.requests, "get", return_value=response) as request_get,
            patch.object(app.logger, "info") as log_info,
        ):
            proxy_url = app.get_valid_proxy()

        self.assertEqual(proxy_url, "http://user:pa%3Ass@127.0.0.1:8080")
        request_get.assert_called_once_with(
            "https://www.google.com",
            proxies={
                "http": proxy_url,
                "https": proxy_url,
            },
            timeout=5,
        )
        self.assertNotIn("user", " ".join(str(call) for call in log_info.call_args_list))

    def test_telegram_without_credentials_skips_request(self):
        with (
            patch.object(app, "BOT_TOKEN", None),
            patch.object(app, "CHAT_ID", None),
            patch.object(app.requests, "get") as request_get,
        ):
            result = app.send_telegram_message("report", "2026-08-25")

        self.assertFalse(result)
        request_get.assert_not_called()


    def test_telegram_request_error_does_not_log_token(self):
        token = "123456:secret-token"
        with (
            patch.object(app, "BOT_TOKEN", token),
            patch.object(app, "CHAT_ID", "chat-id"),
            patch.object(
                app.requests,
                "get",
                side_effect=app.requests.RequestException(
                    f"request failed for https://api.telegram.org/bot{token}/sendMessage"
                ),
            ),
            patch.object(app.logger, "error") as log_error,
        ):
            result = app.send_telegram_message("report", "2026-08-25")

        self.assertFalse(result)
        self.assertNotIn(token, str(log_error.call_args_list))

if __name__ == "__main__":
    unittest.main()
