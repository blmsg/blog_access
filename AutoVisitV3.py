import logging
import os
import random
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urljoin, urlparse, urlunparse

import requests
from dotenv import load_dotenv
from playwright.sync_api import Page, sync_playwright

logger = logging.getLogger(__name__)

DEFAULT_HOMEPAGE_URL = "https://blog.883881.xyz"
DEFAULT_PROXY_FILE = Path("proxies.txt")
ARTICLE_SELECTOR = "a.article-title"
NAVIGATION_TIMEOUT_MS = 30_000
PROXY_TIMEOUT_SECONDS = 5
TELEGRAM_TIMEOUT_SECONDS = 10
REPORT_INTERVAL_SECONDS = 360
HOMEPAGE_VISIT_COUNT = 2
ARTICLE_POOL_SIZE = 10

BROWSER_ARGS = (
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--disable-background-networking",
    "--disable-default-apps",
    "--disable-extensions",
    "--disable-sync",
    "--metrics-recording-only",
    "--mute-audio",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-popup-blocking",
    "--disable-hang-monitor",
    "--disable-prompt-on-repost",
    "--disable-component-update",
    "--disable-client-side-phishing-detection",
    "--password-store=basic",
    "--use-mock-keychain",
    "--disable-renderer-backgrounding",
    "--disable-background-timer-throttling",
    "--disable-features=TranslateUI,BlinkGenPropertyTrees",
    "--hide-scrollbars",
)

USER_AGENTS = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 6.1; WOW64; rv:43.0) "
    "Gecko/20100101 Firefox/43.0",
    "Mozilla/5.0 (Windows NT 6.1; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/54.0.2840.71 Safari/537.36",
)


@dataclass(frozen=True)
class Settings:
    homepage_url: str = DEFAULT_HOMEPAGE_URL
    bot_token: str | None = field(default=None, repr=False)
    chat_id: str | None = field(default=None, repr=False)
    proxy_file: Path = DEFAULT_PROXY_FILE

    def __post_init__(self) -> None:
        homepage_url = self.homepage_url.strip().rstrip("/")
        parsed = urlparse(homepage_url)
        try:
            port = parsed.port
        except ValueError as error:
            raise ValueError("HOMEPAGE_URL 的端口无效") from error
        if port == 0:
            raise ValueError("HOMEPAGE_URL 的端口必须在 1 到 65535 之间")
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("HOMEPAGE_URL 必须是有效的 HTTP(S) 地址")
        if parsed.username or parsed.password:
            raise ValueError("HOMEPAGE_URL 不允许包含用户名或密码")

        object.__setattr__(self, "homepage_url", homepage_url)
        object.__setattr__(self, "bot_token", _clean_optional(self.bot_token))
        object.__setattr__(self, "chat_id", _clean_optional(self.chat_id))
        object.__setattr__(self, "proxy_file", Path(self.proxy_file))

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        homepage_url = _clean_optional(os.getenv("HOMEPAGE_URL"))
        proxy_file = _clean_optional(os.getenv("PROXY_FILE"))
        return cls(
            homepage_url=homepage_url or DEFAULT_HOMEPAGE_URL,
            bot_token=os.getenv("BOT_TOKEN"),
            chat_id=os.getenv("CHAT_ID"),
            proxy_file=Path(proxy_file or DEFAULT_PROXY_FILE),
        )

    @property
    def telegram_enabled(self) -> bool:
        return bool(self.bot_token and self.chat_id)


@dataclass
class VisitStats:
    total_visits: int = 0
    failed_visits: int = 0
    successful_visits: int = 0
    homepage_visits: int = 0
    article_visits: int = 0
    unique_cookies: set[str] = field(default_factory=set)
    unique_devices: set[str] = field(default_factory=set)
    last_update: float = field(default_factory=time.time)
    def reset_run(self) -> None:
        self.total_visits = 0
        self.failed_visits = 0
        self.successful_visits = 0
        self.homepage_visits = 0
        self.article_visits = 0
        self.unique_cookies.clear()
        self.unique_devices.clear()


@dataclass(frozen=True)
class ProxyEndpoint:
    scheme: str
    host: str
    port: int
    username: str = field(repr=False)
    password: str = field(repr=False)

    @classmethod
    def parse(cls, line: str) -> "ProxyEndpoint":
        address, separator, credentials = line.partition("|")
        if not separator:
            raise ValueError("代理格式缺少 | 分隔符")

        host, port_text, scheme = address.strip().rsplit(":", 2)
        username, separator, password = credentials.strip().partition(":")
        if not separator:
            raise ValueError("代理认证信息缺少 : 分隔符")
        if not host or not port_text.isdigit() or not scheme or not username:
            raise ValueError("代理地址或认证信息不完整")

        return cls(
            scheme=scheme.lower(),
            host=host,
            port=int(port_text),
            username=username,
            password=password,
        )

    @property
    def browser_server(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"{self.scheme}://{host}:{self.port}"

    @property
    def requests_url(self) -> str:
        encoded_username = quote(self.username, safe="")
        encoded_password = quote(self.password, safe="")
        return (
            f"{self.scheme}://{encoded_username}:{encoded_password}"
            f"@{self.browser_server.removeprefix(f'{self.scheme}://')}"
        )

    @property
    def playwright_proxy(self) -> dict[str, str]:
        return {
            "server": self.browser_server,
            "username": self.username,
            "password": self.password,
        }


def _clean_optional(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def safe_url_label(url: str) -> str:
    parsed = urlparse(url)
    hostname = parsed.hostname or ""
    if parsed.port:
        hostname = f"{hostname}:{parsed.port}"
    return urlunparse((parsed.scheme, hostname, parsed.path, "", "", ""))


def load_proxies_from_file(path: Path) -> list[str]:
    if not path.exists():
        logger.warning("未找到代理列表文件，将尝试本地直连")
        return []

    with path.open("r", encoding="utf-8") as proxy_file:
        return [
            line.strip()
            for line in proxy_file
            if line.strip() and "|" in line
        ]


def get_valid_proxy(
    proxy_file: Path,
    max_attempts: int = 25,
) -> ProxyEndpoint | None:
    proxies = load_proxies_from_file(proxy_file)
    if not proxies:
        logger.warning("代理列表为空，将尝试本地直连")
        return None

    random.shuffle(proxies)
    for attempt, line in enumerate(proxies[:max_attempts], start=1):
        try:
            endpoint = ProxyEndpoint.parse(line)
            logger.info("正在测试代理：%s", endpoint.browser_server)
            response = requests.get(
                "https://www.google.com",
                proxies={
                    "http": endpoint.requests_url,
                    "https": endpoint.requests_url,
                },
                timeout=PROXY_TIMEOUT_SECONDS,
            )
            if response.status_code == 200:
                logger.info("代理验证通过：%s", endpoint.browser_server)
                return endpoint
        except (ValueError, requests.RequestException) as error:
            logger.warning(
                "第 %s 个代理不可用，跳过（%s）",
                attempt,
                type(error).__name__,
            )

    logger.warning("没有可用的代理，将尝试本地直连")
    return None


def generate_unique_cookie(settings: Settings, stats: VisitStats) -> dict[str, Any]:
    cookie_value = (
        f"GA1.2.{random.randint(1_000_000_000, 9_999_999_999)}."
        f"{int(time.time())}"
    )
    stats.unique_cookies.add(cookie_value)
    return {
        "name": "_ga",
        "value": cookie_value,
        "domain": urlparse(settings.homepage_url).hostname,
        "path": "/",
        "expires": int(time.time()) + 365 * 24 * 60 * 60,
    }


def build_telegram_message(title: str, date: str, stats: VisitStats) -> str:
    return f"""
*📅 标题：* {title}
*📅 日期：* {date}

*📈 访问页面情况：*
- 📊 总访问次数：{stats.total_visits}
- ❌ 失败次数：{stats.failed_visits}
- ✅ 成功次数：{stats.successful_visits}
- 🏠 首页访问总访问次数：{stats.homepage_visits}
- 📄 访问的文章总数：{stats.article_visits}
- 🍪 模拟 cookie 数：{len(stats.unique_cookies)}
- 🖥️ 模拟设备类型数：{len(stats.unique_devices)}
"""


def send_telegram_message(
    settings: Settings,
    stats: VisitStats,
    title: str,
    date: str,
) -> bool:
    if not settings.telegram_enabled:
        logger.warning("未配置 BOT_TOKEN 或 CHAT_ID，跳过 Telegram 通知")
        return False

    url = f"https://api.telegram.org/bot{settings.bot_token}/sendMessage"
    params = {
        "chat_id": settings.chat_id,
        "text": build_telegram_message(title, date, stats),
        "parse_mode": "Markdown",
    }
    try:
        response = requests.post(
            url,
            data=params,
            timeout=TELEGRAM_TIMEOUT_SECONDS,
        )
        if response.status_code == 200:
            logger.info("Telegram 消息发送成功")
            return True
        logger.error("Telegram 消息发送失败，状态码：%s", response.status_code)
    except requests.RequestException as error:
        logger.error("Telegram 请求失败：%s", type(error).__name__)
    return False


def scroll_page(page: Page, scroll_delay: float = 1, times: int = 3) -> None:
    for _ in range(times):
        page.mouse.wheel(0, random.randint(123, 589))
        time.sleep(random.uniform(scroll_delay - 0.5, scroll_delay + 0.5))
    for _ in range(times):
        page.mouse.wheel(0, -random.randint(123, 589))
        time.sleep(random.uniform(scroll_delay - 0.5, scroll_delay + 0.5))


def extract_article_date(article_url: str) -> datetime | None:
    path_parts = [
        part
        for part in urlparse(article_url).path.split("/")
        if part
    ]
    for index in range(len(path_parts) - 2):
        try:
            return datetime.strptime(
                "/".join(path_parts[index:index + 3]),
                "%Y/%m/%d",
            )
        except ValueError:
            continue
    return None


def select_random_article_url(page: Page, homepage_url: str) -> str | None:
    article_links = page.query_selector_all(ARTICLE_SELECTOR)
    if not article_links:
        logger.info("未找到文章链接，跳过文章访问")
        return None

    articles_with_dates = []
    base_url = f"{homepage_url.rstrip('/')}/"
    for article in article_links:
        raw_url = article.get_attribute("href")
        if not raw_url:
            continue

        article_url = urljoin(base_url, raw_url)
        article_date = extract_article_date(article_url)
        if article_date is not None:
            articles_with_dates.append((article_url, article_date))

    sorted_articles = sorted(
        articles_with_dates,
        key=lambda item: item[1],
        reverse=True,
    )
    if not sorted_articles:
        logger.info("未找到包含有效日期的文章链接")
        return None

    article_url, _ = random.choice(sorted_articles[:ARTICLE_POOL_SIZE])
    logger.info("随机选择文章：%s", safe_url_label(article_url))
    return article_url


def visit_article_and_return_home(
    page: Page,
    article_url: str,
    homepage_url: str,
    stats: VisitStats,
) -> bool:
    stats.total_visits += 1
    try:
        page.goto(article_url, timeout=NAVIGATION_TIMEOUT_MS)
        page.wait_for_load_state("networkidle")
        scroll_page(page)
        time.sleep(random.uniform(10, 15))
        page.goto(homepage_url, timeout=NAVIGATION_TIMEOUT_MS)
        page.wait_for_load_state("networkidle")
        stats.article_visits += 1
        stats.successful_visits += 1
        return True
    except Exception as error:
        logger.error("访问文章失败：%s", type(error).__name__)
        stats.failed_visits += 1
        return False


def build_launch_args(proxy: ProxyEndpoint | None) -> dict[str, Any]:
    launch_args: dict[str, Any] = {
        "headless": True,
        "args": list(BROWSER_ARGS),
    }
    if proxy:
        launch_args["proxy"] = proxy.playwright_proxy
    return launch_args


def run_playwright(
    settings: Settings,
    stats: VisitStats | None = None,
) -> VisitStats:
    stats = stats or VisitStats()
    stats.reset_run()
    proxy = get_valid_proxy(settings.proxy_file)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(**build_launch_args(proxy))
        try:
            if proxy:
                logger.info("使用代理访问")
            else:
                logger.info("未使用代理，采用本地直连方式")

            user_agent = random.choice(USER_AGENTS)
            stats.unique_devices.add(user_agent)
            context = browser.new_context(
                user_agent=user_agent,
                viewport={"width": 1366, "height": 768},
                device_scale_factor=1.25,
                locale="en-US",
                timezone_id="Asia/Shanghai",
                is_mobile=False,
                has_touch=False,
            )
            context.add_cookies([generate_unique_cookie(settings, stats)])
            page = context.new_page()
            page.add_init_script("""() => {
                Object.defineProperty(navigator, 'webdriver', {
                  get: () => undefined
                });
            }""")

            logger.info("访问首页：%s", safe_url_label(settings.homepage_url))
            for _ in range(HOMEPAGE_VISIT_COUNT):
                stats.total_visits += 1
                try:
                    page.goto(
                        settings.homepage_url,
                        timeout=NAVIGATION_TIMEOUT_MS,
                    )
                    page.wait_for_load_state("networkidle")
                    scroll_page(page)
                    stats.homepage_visits += 1
                    article_url = select_random_article_url(
                        page,
                        settings.homepage_url,
                    )
                    if article_url:
                        visit_article_and_return_home(
                            page,
                            article_url,
                            settings.homepage_url,
                            stats,
                        )
                except Exception as error:
                    logger.error("首页访问失败：%s", type(error).__name__)
                    stats.failed_visits += 1
        finally:
            browser.close()

    if time.time() - stats.last_update >= REPORT_INTERVAL_SECONDS:
        date = datetime.now().strftime("%Y-%m-%d")
        send_telegram_message(settings, stats, "博客访问量报告", date)
        stats.last_update = time.time()

    return stats


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    try:
        run_playwright(Settings.from_env())
    except Exception as error:
        logger.error("执行失败：%s", type(error).__name__)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
