import datetime
import time
import random
import os
import requests
from playwright.sync_api import sync_playwright
from logger import setup_logger
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()
logger = setup_logger()

# Telegram 配置
BOT_TOKEN = os.getenv('BOT_TOKEN')
CHAT_ID = os.getenv('CHAT_ID')
HOMEPAGE_URL = os.getenv('HOMEPAGE_URL', 'https://blog.883881.xyz')

# === 代理相关 ===
def load_proxies_from_file(path="proxies.txt"):
    if not os.path.exists(path):
        logger.warning(f"⚠️ 未找到代理列表文件：{path}，将尝试本地直连")
        return []
    with open(path, "r") as f:
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
            left, right = line.split("|")
            ip, port, proto = left.strip().split(":")
            user, pwd = right.strip().split(":")

            proxy_url = f"{proto}://{user}:{pwd}@{ip}:{port}"
            logger.info(f"🔌 正在测试代理：{proxy_url}")
            response = requests.get(
                "https://www.google.com",
                proxies={"http": proxy_url, "https": proxy_url},
                timeout=5
            )
            if response.status_code == 200:
                logger.info(f"✅ 验证通过，使用代理：{proxy_url}")
                return proxy_url
        except Exception as e:
            logger.warning(f"⚠️ 第 {attempt} 个代理不可用：{line}")
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
    cookie = {
        "name": "_ga",
        "value": f"GA1.2.{random.randint(1000000000, 9999999999)}.{int(time.time())}",
        "domain": "blog.883881.xyz",
        "path": "/",
        "expires": int(time.time()) + 365 * 24 * 60 * 60
    }
    visit_data["unique_cookies"].add(cookie["value"])
    return cookie

def send_telegram_message(title, date):
    message = f"""
*📅 标题：* 博客访问情况统计
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
    response = requests.get(url, params=params)
    if response.status_code == 200:
        logger.info("✅ Telegram 消息发送成功")
    else:
        logger.error(f"❌ 消息发送失败，状态码: {response.status_code}")

def scroll_page(page, scroll_delay=1, times=3):
    for _ in range(times):
        page.mouse.wheel(0, random.randint(123, 589))
        time.sleep(random.uniform(scroll_delay - 0.5, scroll_delay + 0.5))
    for _ in range(times):
        page.mouse.wheel(0, -random.randint(123, 589))
        time.sleep(random.uniform(scroll_delay - 0.5, scroll_delay + 0.5))

def click_random_article(page):
    article_links = page.query_selector_all('a.article-title')
    if not article_links:
        logger.info("未找到文章链接，跳过点击操作")
        return None
    articles_with_dates = []
    for article in article_links:
        article_url = article.get_attribute('href')
        if not article_url:
            continue
        try:
            date_str = "/".join(article_url.split('/')[1:4])
            article_date = datetime.datetime.strptime(date_str, "%Y/%m/%d")
            articles_with_dates.append((article, article_date))
        except ValueError:
            continue
    sorted_articles = sorted(articles_with_dates, key=lambda x: x[1], reverse=True)
    if not sorted_articles:
        return None
    random_article = random.choice(sorted_articles[:10])[0]
    article_url = random_article.get_attribute('href')
    if article_url:
        logger.info(f"随机点击文章：{article_url}")
        page.wait_for_selector(f'a.article-title[href="{article_url}"]', timeout=10000)
        page.click(f'a.article-title[href="{HOMEPAGE_URL}{article_url}"]')
        time.sleep(random.uniform(2, 4))
        scroll_page(page)
        return article_url
    return None

def visit_article_and_return_home(page, article_url):
    try:
        page.goto(article_url, timeout=30000)
        page.wait_for_load_state("networkidle")
        scroll_page(page)
        time.sleep(random.uniform(10, 15))
        page.goto(HOMEPAGE_URL)
        page.wait_for_load_state("networkidle")
        visit_data["article_visits"] += 1
    except Exception as e:
        logger.error(f"访问文章时发生错误：{e}")
        visit_data["failed_visits"] += 1

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
            logger.info(f"🌍 使用代理访问：{launch_args}")
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
            try:
                page.goto(HOMEPAGE_URL, timeout=30000)
                page.wait_for_load_state("networkidle")
                scroll_page(page)
                visit_data["homepage_visits"] += 1
                visit_data["total_visits"] += 1
                article_url = click_random_article(page)
                if article_url:
                    visit_article_and_return_home(page, article_url)
                    visit_data["successful_visits"] += 1
                    visit_data["total_visits"] += 1
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
    try:
        run_playwright()
    except Exception as e:
        logger.error(f"执行过程中发生错误: {e}")
