import datetime
import logging
import os
import random
import time
from urllib.parse import quote, urljoin, urlparse

import requests
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

# 加载环境变量
load_dotenv()
logger = logging.getLogger(__name__)

# Telegram 配置
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
HOMEPAGE_URL = os.getenv("HOMEPAGE_URL", "https://blog.883881.xyz")

# === 代理相关 ===
def load_proxies_from_file(path="proxies.txt"):
    if not os.path.exists(path):
        logger.warning(f"⚠️ 未找到代理列表文件：{path}，将尝试本地直连")
        return []
    with open(path, "r", encoding="utf-8") as f:
        lines = [line.strip() for line in f if line.strip() and "|" in line]
        return lines

def get_valid_proxy(max_attempts=25):
    proxies = load_proxies_from_file()
    if not proxies:
        logger.warning("⚠️ 代理列表为空，将尝试本地直连")
        return None

    random.shuffle(proxies)
    for attempt, line in enumerate(proxies[:max_attempts], start=1):
        try:
            left, separator, right = line.partition("|")
            if not separator:
                raise ValueError("代理格式缺少 | 分隔符")

            ip, port, proto = left.strip().rsplit(":", 2)
            user, separator, pwd = right.strip().partition(":")
            if not separator:
                raise ValueError("代理认证信息缺少 : 分隔符")
            if not ip or not port.isdigit() or not proto or not user:
                raise ValueError("代理地址或认证信息不完整")

            proxy_url = (
                f"{proto}://{quote(user, safe='')}:{quote(pwd, safe='')}"
                f"@{ip}:{port}"
            )
            logger.info("🔌 正在测试代理：%s://%s:%s", proto, ip, port)
            response = requests.get(
                "https://www.google.com",
                proxies={"http": proxy_url, "https": proxy_url},
                timeout=5,
            )
            if response.status_code == 200:
                logger.info("✅ 验证通过，使用代理：%s://%s:%s", proto, ip, port)
                return proxy_url
        except (ValueError, requests.RequestException) as error:
            logger.warning(
                "⚠️ 第 %s 个代理不可用，跳过（%s）",
                attempt,
                type(error).__name__,
            )
    logger.warning("⚠️ 没有可用的代理，将尝试本地直连")
    return None

# === 访问逻辑 ===
visit_data = {
    "total_visits": 0,
    "failed_visits": 0,
    "successful_visits": 0,
    "homepage_visits": 0,
    "article_visits": 0,
    "unique_cookies": set(),
    "unique_devices": set(),
    "last_update": time.time()
}

def generate_unique_cookie():
    cookie_domain = urlparse(HOMEPAGE_URL).hostname
    if not cookie_domain:
        raise ValueError(f"HOMEPAGE_URL 不是有效网址：{HOMEPAGE_URL}")
    cookie = {
        "name": "_ga",
        "value": f"GA1.2.{random.randint(1000000000, 9999999999)}.{int(time.time())}",
        "domain": cookie_domain,
        "path": "/",
        "expires": int(time.time()) + 365 * 24 * 60 * 60
    }
    visit_data["unique_cookies"].add(cookie["value"])
    return cookie

def send_telegram_message(title, date):
    if not BOT_TOKEN or not CHAT_ID:
        logger.warning("⚠️ 未配置 BOT_TOKEN 或 CHAT_ID，跳过 Telegram 通知")
        return False

    message = f"""
*📅 标题：* {title}
*📅 日期：* {date}

*📈 访问页面情况：*
- 📊 总访问次数：{visit_data['total_visits']}
- ❌ 失败次数：{visit_data['failed_visits']}
- ✅ 成功次数：{visit_data['successful_visits']}
- 🏠 首页访问总访问次数：{visit_data['homepage_visits']}
- 📄 访问的文章总数：{visit_data['article_visits']}
- 🍪 模拟 cookie 数：{len(visit_data['unique_cookies'])}
- 🖥️ 模拟设备类型数：{len(visit_data['unique_devices'])}
"""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    params = {'chat_id': CHAT_ID, 'text': message, 'parse_mode': 'Markdown'}
    try:
        response = requests.get(url, params=params, timeout=10)
        if response.status_code == 200:
            logger.info("✅ Telegram 消息发送成功")
            return True
        logger.error(f"❌ 消息发送失败，状态码: {response.status_code}")
    except requests.RequestException as error:
        logger.error("❌ Telegram 请求失败：%s", type(error).__name__)
    return False

def scroll_page(page, scroll_delay=1, times=3):
    for _ in range(times):
        page.mouse.wheel(0, random.randint(123, 589))
        time.sleep(random.uniform(scroll_delay - 0.5, scroll_delay + 0.5))
    for _ in range(times):
        page.mouse.wheel(0, -random.randint(123, 589))
        time.sleep(random.uniform(scroll_delay - 0.5, scroll_delay + 0.5))

def select_random_article_url(page):
    article_links = page.query_selector_all("a.article-title")
    if not article_links:
        logger.info("未找到文章链接，跳过文章访问")
        return None

    articles_with_dates = []
    for article in article_links:
        raw_url = article.get_attribute("href")
        if not raw_url:
            continue

        article_url = urljoin(HOMEPAGE_URL, raw_url)
        path_parts = [part for part in urlparse(article_url).path.split("/") if part]
        article_date = None
        for index in range(len(path_parts) - 2):
            try:
                article_date = datetime.datetime.strptime(
                    "/".join(path_parts[index:index + 3]), "%Y/%m/%d"
                )
                break
            except ValueError:
                continue
        if article_date is not None:
            articles_with_dates.append((article_url, article_date))

    sorted_articles = sorted(articles_with_dates, key=lambda item: item[1], reverse=True)
    if not sorted_articles:
        logger.info("未找到包含有效日期的文章链接")
        return None

    article_url, _ = random.choice(sorted_articles[:10])
    logger.info("随机选择文章：%s", article_url)
    return article_url

def visit_article_and_return_home(page, article_url):
    visit_data["total_visits"] += 1
    try:
        page.goto(article_url, timeout=30000)
        page.wait_for_load_state("networkidle")
        scroll_page(page)
        time.sleep(random.uniform(10, 15))
        page.goto(HOMEPAGE_URL, timeout=30000)
        page.wait_for_load_state("networkidle")
        visit_data["article_visits"] += 1
        visit_data["successful_visits"] += 1
        return True
    except Exception as error:
        logger.error("访问文章时发生错误：%s", error)
        visit_data["failed_visits"] += 1
        return False

def run_playwright():
    proxy = get_valid_proxy()

    with sync_playwright() as p:
        launch_args = {
            "headless": True,
            "args": [
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
                "--hide-scrollbars"
            ]
        }
        if proxy:
            launch_args["proxy"] = {"server": proxy}
            logger.info("🌍 使用代理访问")
        else:
            logger.info("🌐 未使用代理，采用本地直连方式")

        browser = p.chromium.launch(**launch_args)
        context = browser.new_context(
            user_agent=random.choice([
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                "Mozilla/5.0 (Windows NT 6.1; WOW64; rv:43.0) Gecko/20100101 Firefox/43.0",
                "Mozilla/5.0 (Windows NT 6.1; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/54.0.2840.71 Safari/537.36"
            ]),
            viewport={"width": 1366, "height": 768},
            device_scale_factor=1.25,
            locale="en-US",
            timezone_id="Asia/Shanghai",
            is_mobile=False,
            has_touch=False
        )
        context.add_cookies([generate_unique_cookie()])
        page = context.new_page()
        page.add_init_script("""() => {
            Object.defineProperty(navigator, 'webdriver', {
              get: () => undefined
            });
        }""")
        logger.info(f"访问首页: {HOMEPAGE_URL}")
        visit_data.update({
            "homepage_visits": 0, "total_visits": 0,
            "failed_visits": 0, "successful_visits": 0
        })
        for _ in range(2):
            visit_data["total_visits"] += 1
            try:
                page.goto(HOMEPAGE_URL, timeout=30000)
                page.wait_for_load_state("networkidle")
                scroll_page(page)
                visit_data["homepage_visits"] += 1
                article_url = select_random_article_url(page)
                if article_url:
                    visit_article_and_return_home(page, article_url)
            except Exception as e:
                logger.error(f"执行过程中发生错误: {e}")
                visit_data["failed_visits"] += 1
        logger.info("完成自动访问，准备退出浏览器")
        browser.close()
        if time.time() - visit_data["last_update"] >= 360:
            date = datetime.datetime.now().strftime("%Y-%m-%d")
            send_telegram_message("博客访问量报告", date)
            visit_data["last_update"] = time.time()

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    try:
        run_playwright()
    except Exception as e:
        logger.error(f"执行过程中发生错误: {e}")

