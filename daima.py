import jqdata
import requests
import json
import uuid
from datetime import datetime, timedelta
import time
import logging
import smtplib
from email.mime.text import MIMEText

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# 设置邮件发送模式：1为开启，0为关闭
MODE = 0

# 最大持仓股票数量（可调整，例如设为2或5、经测试：持仓股越多、收益越好、回撤越低）
MAX_HOLDINGS = 3

EXTERNAL_STOCK_POOL = {
    # 示例：'2026-04-07': ['000001.XSHE', '600000.XSHG', '000002']
}

REMOTE_STOCK_POOL_ENABLED = False
REMOTE_STOCK_POOL_JSON_URL = ""
REMOTE_STOCK_POOL_TIMEOUT = 5

USE_EASTMONEY_FALLBACK = False
MAX_EXTERNAL_POOL_SIZE = 15

EMAIL_SENDER = ""
EMAIL_PASSWORD = ""
EMAIL_RECEIVER = ""
SMTP_HOST = "smtp.qq.com"
SMTP_PORT = 465

def initialize(context):
    # 市值
    context.market_value = 12000000000
    # 排序规则
    context.sort_rule = "按照市值从小到大排序"

    # 费率设置
    set_order_cost(OrderCost(open_tax=0, close_tax=0.001, open_commission=0.0001, close_commission=0.0001, min_commission=0), type='stock')
    # 设置滑点
    set_slippage(PriceRelatedSlippage(0.003))
    # 设定沪深300作为基准
    set_benchmark('000300.XSHG')
    set_option('order_volume_ratio', 0.5)
    # 规避未来函数
    set_option("avoid_future_data", True)
    log.info('@@@规避未来函数，已开启@@@')
    
    context.hold_list = {}
    context.subscribe_list = []
    context.pre_value = {}
    context.today_stock = []
    context.today_sell = []
    context.can_buy = MAX_HOLDINGS
    # 标记是否已完成开盘买入
    context.opening_buy_done = False
    log.info(f'策略开始运行,初始化函数全局只运行一次，最大持仓数量：{MAX_HOLDINGS}')

def before_trading_start(context):
    # 获取日期
    date = context.current_dt.strftime('%Y-%m-%d %H:%M:%S')
    log.info('{} 盘前运行'.format(date))
    log.info('开始执行盘前任务，准备获取选股列表')

    # 重置开盘买入标志
    context.opening_buy_done = False

    valid_stock_list = get_today_stock_pool(context)
    context.stock_list = list(valid_stock_list)

    trade_date = context.current_dt.strftime('%Y-%m-%d')
    remind_text = f"【外部日期字典股票池：{trade_date}】\n"
    for stock_code in valid_stock_list:
        data = get_security_info(stock_code)
        if data is not None:
            tmp_str = "代码：" + stock_code + " - 标的名：" + data.display_name
            remind_text = remind_text + tmp_str + "\n"
    log.info(remind_text)
    # 账户信息
    log.info(context.portfolio)
    context.available_cash = context.portfolio.available_cash
    log.info(f'可用资金量:{context.available_cash}')
    
    # 监听股票
    context.subscribe_list = []
    for stock in list(valid_stock_list) + list(context.portfolio.positions.keys()):
        if stock not in context.subscribe_list:
            context.subscribe_list.append(stock)
    if context.subscribe_list:
        value = get_price(context.subscribe_list, end_date=context.previous_date, count=20, frequency='1d', fields=['close', 'high', 'low'], skip_paused=False, fq=None)
        context.pre_value = value
    else:
        context.pre_value = {}
        log.info('今日无订阅股票，跳过历史价格获取')
    # 今日买入和卖出列表
    context.today_stock = []
    context.today_sell = []
    # 最大持仓基于 MAX_HOLDINGS
    context.can_buy = max(0, MAX_HOLDINGS - len(context.portfolio.positions))

def handle_data(context, data):
    time_str = context.current_dt.strftime('%Y-%m-%d %H:%M:%S')
    log.info(f"[{time_str}] 当前持仓: {context.portfolio.positions}")
    currntStockList = list(context.portfolio.positions.keys())

    # 计算总账户价值
    total_portfolio_value = context.portfolio.available_cash + context.portfolio.positions_value
    target_position_value = total_portfolio_value / MAX_HOLDINGS  # 每只股票目标价值

    # 第一步：清理超额持仓（确保不超过 MAX_HOLDINGS）
    if len(currntStockList) > MAX_HOLDINGS:
        # 按收益率排序，优先卖出收益最低的
        stock_profits = []
        for stock in currntStockList:
            position = context.portfolio.positions[stock]
            profit_rate = (position.price - position.avg_cost) / position.avg_cost if position.avg_cost > 0 else 0
            stock_profits.append((stock, profit_rate))
        stock_profits.sort(key=lambda x: x[1])  # 收益最低的优先
        for stock, _ in stock_profits[MAX_HOLDINGS:]:  # 卖出超过 MAX_HOLDINGS 的部分
            order_result = sellall(context, stock)
            if order_result is None or hasattr(order_result, 'status') and order_result.status == 'failed':
                log.error(f"[{time_str}] 清理超额持仓 {stock} 失败: {str(order_result)}")
                continue
            order_info = {
                "action": "sell",
                "security": stock,
                "order_result": str(order_result),
                "current_price": data[stock].close,
                "cost_price": context.portfolio.positions[stock].avg_cost,
                "position": context.portfolio.positions[stock].closeable_amount,
                "time": str(context.current_dt)
            }
            send_trade_signal(order_info)
            log.info(f"[{time_str}] 清理超额持仓卖出: {order_info}")
            context.available_cash = context.portfolio.available_cash
        # 更新当前持仓列表
        currntStockList = list(context.portfolio.positions.keys())

    # 第二步：处理现有持仓的卖出（止损/止盈）
    for each_stock in currntStockList:
        if each_stock in context.today_stock:
            continue  # 跳过今日买入的股票
        position = context.portfolio.positions[each_stock]
        current_price = data[each_stock].close

        denominator = position.avg_cost * position.total_amount
        profit_rate = (position.value - denominator) / denominator if denominator != 0 else 0
        log.info(f"[{time_str}] {each_stock} 获利 {profit_rate:.4f}")

        # 检查止损：跌幅超过2%
        if position.avg_cost > 0 and (current_price / position.avg_cost - 1 < -0.02):
            order_result = sellall(context, each_stock)
            if order_result is None or hasattr(order_result, 'status') and order_result.status == 'failed':
                log.error(f"[{time_str}] 卖出 {each_stock} 失败: {str(order_result)}")
                continue
            order_info = {
                "action": "sell",
                "security": each_stock,
                "order_result": str(order_result),
                "current_price": current_price,
                "cost_price": position.avg_cost,
                "position": position.closeable_amount,
                "time": str(context.current_dt)
            }
            send_trade_signal(order_info)
            log.info(f"[{time_str}] 触发止损卖出信号: {order_info}")
            context.available_cash = context.portfolio.available_cash
            continue

        # 检查止盈：收益率超过1%
        if profit_rate > 0.01:
            order_result = sellall(context, each_stock)
            if order_result is None or hasattr(order_result, 'status') and order_result.status == 'failed':
                log.error(f"[{time_str}] 卖出 {each_stock} 失败: {str(order_result)}")
                continue
            order_info = {
                "action": "sell",
                "security": each_stock,
                "order_result": str(order_result),
                "current_price": current_price,
                "cost_price": position.avg_cost,
                "position": position.closeable_amount,
                "time": str(context.current_dt)
            }
            send_trade_signal(order_info)
            log.info(f"[{time_str}] 触发止盈卖出信号: {order_info}")
            context.available_cash = context.portfolio.available_cash

    # 第三步：同步 hold_list 与实际持仓
    context.hold_list = {stock: 1 for stock in context.portfolio.positions.keys()}

    # 第四步：开盘买入逻辑
    is_opening = context.current_dt.hour == 9 and context.current_dt.minute == 30
    if is_opening and not context.opening_buy_done:
        # 更新 can_buy 基于当前持仓
        context.can_buy = max(0, MAX_HOLDINGS - len(context.portfolio.positions))
        for each_stock in context.subscribe_list:
            if each_stock in context.hold_list or each_stock in context.today_sell:
                continue  # 跳过已持有或今日卖出的股票
            if context.can_buy <= 0 or len(context.portfolio.positions) >= MAX_HOLDINGS:
                log.info(f'[{time_str}] 不能下单，当前可购买数量为： {context.can_buy}, 持仓数： {len(context.portfolio.positions)}')
                break
            # 使用 order_target_value 确保每只股票占总账户价值的 1/MAX_HOLDINGS
            order_result = order_target_value(each_stock, target_position_value)
            if order_result is None or hasattr(order_result, 'status') and order_result.status == 'failed':
                log.error(f"[{time_str}] 买入 {each_stock} 失败: {str(order_result)}")
                continue
            context.hold_list[each_stock] = 1
            context.today_stock.append(each_stock)
            order_info = {
                "action": "buy",
                "security": each_stock,
                "order_result": str(order_result),
                "target_value": target_position_value,
                "current_price": data[each_stock].close,
                "time": str(context.current_dt)
            }
            send_trade_signal(order_info)
            log.info(f"[{time_str}] 开盘买入: {order_info}")
            context.can_buy -= 1
            context.available_cash = context.portfolio.available_cash
        # 标记开盘买入完成
        context.opening_buy_done = True

    # 第五步：清理订阅列表
    cleaned_subscribe_list = []
    seen_stocks = set()
    for stock in context.subscribe_list:
        if stock not in seen_stocks and get_security_info(stock) is not None:
            cleaned_subscribe_list.append(stock)
            seen_stocks.add(stock)
        else:
            log.info(f"[{time_str}] 移除无效或重复股票: {stock}")
    context.subscribe_list = cleaned_subscribe_list

    log.info(f"[{time_str}] 本轮结束持仓: {context.portfolio.positions}")

def get_selected_stocks(context):
    log.info('开始发起东方财富条件选股请求')
    url = "https://np-tjxg-b.eastmoney.com/api/smart-tag/stock/v3/pw/search-code"
    fingerprint = _get_eastmoney_fingerprint(context)
    session = _get_eastmoney_session(context, fingerprint)
    common_headers = _build_eastmoney_common_headers()
    _warmup_eastmoney_session(context, session, common_headers)

    headers = {
        **common_headers,
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "Origin": "https://xuangu.eastmoney.com",
        "Referer": "https://xuangu.eastmoney.com/",
        "Sec-Fetch-Site": "same-site",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
    }
    payload = _build_eastmoney_payload(fingerprint)
    log.info(f"东方财富条件选股请求参数：condition={payload['keyWordNew']}, pageSize={payload['pageSize']}, fingerprint后6位={fingerprint[-6:]}")

    try:
        response = session.post(url, headers=headers, json=payload, timeout=(5, 15))
    except requests.RequestException as e:
        log.info(f"东方财富条件选股请求网络异常: {type(e).__name__}: {e}")
        return []

    content_type = response.headers.get("Content-Type", "")
    body_preview = _preview_response_text(response)
    if response.status_code != 200:
        log.info(f"东方财富条件选股接口HTTP异常：status={response.status_code}, Content-Type={content_type}, body前500字符={body_preview}")
        return []
    if not response.text or not response.text.strip():
        log.info(f"东方财富条件选股接口返回空响应：status={response.status_code}, Content-Type={content_type}")
        return []

    try:
        result = response.json()
    except ValueError as e:
        log.info(f"东方财富条件选股接口返回非JSON响应：status={response.status_code}, Content-Type={content_type}, body前500字符={body_preview}, 异常={e}")
        return []
    if not isinstance(result, dict):
        log.info(f"东方财富条件选股接口JSON结构异常：status={response.status_code}, Content-Type={content_type}, 顶层类型={type(result).__name__}, body前500字符={body_preview}")
        return []

    code = str(result.get("code", ""))
    msg = result.get("msg", "")
    if code != "100":
        log.info(f"东方财富条件选股接口业务失败：code={code}, msg={msg}, body前500字符={body_preview}")
        return []

    stocks = _extract_eastmoney_stock_list(result)
    if stocks:
        log.info(f"东方财富条件选股接口解析成功：code={code}, msg={msg}, 股票数量={len(stocks)}")
        return stocks

    result_data = result.get("data") if isinstance(result, dict) else None
    result_obj = result_data.get("result") if isinstance(result_data, dict) else None
    result_keys = list(result_obj.keys()) if isinstance(result_obj, dict) else []
    log.info(f"东方财富条件选股接口请求成功但未解析到股票列表：code={code}, msg={msg}, result_keys={result_keys}")
    return []


def _get_eastmoney_fingerprint(context):
    if not hasattr(context, "eastmoney_fingerprint"):
        context.eastmoney_fingerprint = uuid.uuid4().hex
    return context.eastmoney_fingerprint


def _get_eastmoney_session(context, fingerprint):
    if not hasattr(context, "eastmoney_session"):
        context.eastmoney_session = requests.Session()
        context.eastmoney_session.cookies.update(_build_eastmoney_cookies(fingerprint))
        context.eastmoney_warmed_up = False
    return context.eastmoney_session


def _build_eastmoney_cookies(fingerprint):
    timestamp_ms = str(int(time.time() * 1000))
    visit_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()).replace(" ", "%20").replace(":", "%3A")
    st_trace = time.strftime("%Y%m%d%H%M%S", time.localtime()) + "000-119144370786-" + str(uuid.uuid4().int % 10000000000)
    return {
        "qgqp_b_id": fingerprint,
        "st_si": str(uuid.uuid4().int % 100000000000000).zfill(14),
        "st_psi": st_trace,
        "st_pvi": str(uuid.uuid4().int % 100000000000000).zfill(14),
        "st_nvi": uuid.uuid4().hex[:24],
        "st_sp": visit_time,
        "st_inirUrl": "",
        "st_sn": "1",
        "nid18": uuid.uuid4().hex,
        "nid18_create_time": timestamp_ms,
        "gviem": uuid.uuid4().hex[:24],
        "gviem_create_time": timestamp_ms,
        "st_asi": st_trace + "-webznxg.dbssk.qxg-1",
    }


def _build_eastmoney_common_headers():
    return {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.6998.205 Safari/537.36",
        "sec-ch-ua": '"Chromium";v="134", "Not:A-Brand";v="24", "Google Chrome";v="134"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
    }


def _warmup_eastmoney_session(context, session, common_headers):
    if getattr(context, "eastmoney_warmed_up", False):
        return

    warmup_requests = [
        ("https://xuangu.eastmoney.com/", None, {**common_headers, "Sec-Fetch-Site": "none", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"}),
        ("https://np-tjxg-operation-b.eastmoney.com/isPreserve", {"source": "WEB"}, {**common_headers, "Accept": "application/json, text/plain, */*", "Origin": "https://xuangu.eastmoney.com", "Referer": "https://xuangu.eastmoney.com/", "Sec-Fetch-Site": "same-site", "Sec-Fetch-Mode": "cors", "Sec-Fetch-Dest": "empty"}),
        ("https://np-tjxg-operation-b.eastmoney.com/ip/needFilter", None, {**common_headers, "Accept": "application/json, text/plain, */*", "Origin": "https://xuangu.eastmoney.com", "Referer": "https://xuangu.eastmoney.com/", "Sec-Fetch-Site": "same-site", "Sec-Fetch-Mode": "cors", "Sec-Fetch-Dest": "empty"}),
    ]

    successful_warmup_count = 0
    for url, params, headers in warmup_requests:
        try:
            response = session.get(url, params=params, headers=headers, timeout=(5, 10))
        except requests.RequestException as e:
            log.info(f"东方财富浏览器预热请求异常：url={url}, 异常={type(e).__name__}: {e}")
            continue
        successful_warmup_count += 1
        log.info(f"东方财富浏览器预热：url={url}, status={response.status_code}, Content-Type={response.headers.get('Content-Type', '')}, cookies={list(session.cookies.keys())}, body前300字符={_preview_response_text(response)[:300]}")

    context.eastmoney_warmed_up = successful_warmup_count > 0


def _build_eastmoney_payload(fingerprint):
    condition = "前一日收盘价涨停，人气排名,非ST；非首板；非科创板；"
    custom_data_new = json.dumps(
        [{"type": "text", "value": condition, "extra": ""}],
        ensure_ascii=False,
        separators=(",", ":")
    )
    timestamp = str(int(time.time() * 1000000))
    request_id = "id" + uuid.uuid4().hex + str(int(time.time() * 1000))
    xc_id = "xc" + uuid.uuid4().hex[:20]

    return {
        "needAmbiguousSuggest": True,
        "pageSize": 50,
        "pageNo": 1,
        "fingerprint": fingerprint,
        "matchWord": "",
        "shareToGuba": False,
        "timestamp": timestamp,
        "requestId": request_id,
        "removedConditionIdList": [],
        "ownSelectAll": False,
        "needCorrect": True,
        "client": "WEB",
        "product": "",
        "needShowStockNum": False,
        "biz": "web_ai_select_stocks",
        "xcId": xc_id,
        "gids": [],
        "dxInfoNew": [],
        "keyWordNew": condition,
        "customDataNew": custom_data_new,
    }


def _extract_eastmoney_stock_list(result):
    data = result.get("data") if isinstance(result, dict) else None
    result_obj = data.get("result") if isinstance(data, dict) else None

    candidates = []
    if isinstance(result_obj, dict):
        for key in ("dataList", "list", "rows", "data"):
            value = result_obj.get(key)
            if isinstance(value, list):
                candidates.append(value)
    elif isinstance(result_obj, list):
        candidates.append(result_obj)

    if isinstance(data, dict) and isinstance(data.get("dataList"), list):
        candidates.append(data["dataList"])

    for candidate in candidates:
        stocks = [
            item for item in candidate
            if isinstance(item, dict) and str(item.get("SECURITY_CODE", "")).strip()
        ]
        if stocks:
            return stocks

    return _find_stock_rows(result)


def _find_stock_rows(value):
    if isinstance(value, list):
        rows = [
            item for item in value
            if isinstance(item, dict) and str(item.get("SECURITY_CODE", "")).strip()
        ]
        if rows:
            return rows
        for item in value:
            rows = _find_stock_rows(item)
            if rows:
                return rows
    elif isinstance(value, dict):
        for item in value.values():
            rows = _find_stock_rows(item)
            if rows:
                return rows
    return []


def _preview_response_text(response):
    text = response.text or ""
    return text[:500].replace("\n", " ").replace("\r", " ")


def normalize_stock_code(stock_code):
    if stock_code is None:
        return None
    code = str(stock_code).strip().upper()
    if not code:
        return None
    if len(code) == 11 and code[:6].isdigit() and code[6:] in (".XSHE", ".XSHG"):
        return code
    if len(code) == 6 and code.isdigit():
        if code.startswith(("00", "30")):
            return code + ".XSHE"
        if code.startswith(("60", "68")):
            return code + ".XSHG"
    return None


def get_external_stock_pool(context):
    trade_date = context.current_dt.strftime('%Y-%m-%d')
    raw_codes = EXTERNAL_STOCK_POOL.get(trade_date, [])
    log.info(f'外部股票池日期={trade_date}, 原始数量={len(raw_codes)}')
    return raw_codes


def get_remote_stock_pool(context):
    if not REMOTE_STOCK_POOL_ENABLED or not REMOTE_STOCK_POOL_JSON_URL:
        return None
    trade_date = context.current_dt.strftime('%Y-%m-%d')
    try:
        response = requests.get(REMOTE_STOCK_POOL_JSON_URL, timeout=REMOTE_STOCK_POOL_TIMEOUT)
    except requests.RequestException as e:
        log.info(f'远程股票池请求异常：date={trade_date}, 异常={type(e).__name__}: {e}')
        return None
    if response.status_code != 200:
        log.info(f'远程股票池HTTP异常：date={trade_date}, status={response.status_code}, body前500字符={_preview_response_text(response)}')
        return None
    try:
        payload = response.json()
    except ValueError as e:
        log.info(f'远程股票池返回非JSON：date={trade_date}, body前500字符={_preview_response_text(response)}, 异常={e}')
        return None
    if not isinstance(payload, dict):
        log.info(f'远程股票池JSON结构异常：date={trade_date}, 顶层类型={type(payload).__name__}')
        return None
    if trade_date not in payload:
        log.info(f'远程股票池缺少日期：date={trade_date}')
        return None
    codes = payload.get(trade_date)
    if not isinstance(codes, list):
        log.info(f'远程股票池日期数据结构异常：date={trade_date}, 类型={type(codes).__name__}')
        return None
    log.info(f'远程股票池读取成功：date={trade_date}, 原始数量={len(codes)}')
    return codes


def normalize_and_validate_stock_pool(raw_codes, limit=None):
    valid_codes = []
    seen_codes = set()
    for raw_code in raw_codes:
        stock_code = normalize_stock_code(raw_code)
        if stock_code is None:
            log.info(f'跳过无法识别的股票代码：{raw_code}')
            continue
        if stock_code in seen_codes:
            log.info(f'跳过重复股票代码：{stock_code}')
            continue
        security_info = get_security_info(stock_code)
        if security_info is None:
            log.info(f'跳过聚宽无效股票代码：{stock_code}')
            continue
        seen_codes.add(stock_code)
        valid_codes.append(stock_code)
        if limit is not None and len(valid_codes) >= limit:
            break
    return valid_codes


def get_today_stock_pool(context):
    trade_date = context.current_dt.strftime('%Y-%m-%d')
    raw_codes = get_external_stock_pool(context)
    if trade_date in EXTERNAL_STOCK_POOL:
        log.info(f'使用外部日期字典股票池：date={trade_date}')
        return normalize_and_validate_stock_pool(raw_codes, limit=MAX_EXTERNAL_POOL_SIZE)
    remote_codes = get_remote_stock_pool(context)
    if remote_codes is not None:
        log.info(f'使用远程HTTP股票池：date={trade_date}')
        return normalize_and_validate_stock_pool(remote_codes, limit=MAX_EXTERNAL_POOL_SIZE)
    if not USE_EASTMONEY_FALLBACK:
        log.info(f'外部股票池缺少日期且远程股票池不可用：date={trade_date}，且未开启东方财富回退')
        return []
    log.info(f'外部股票池缺少日期且远程股票池不可用：date={trade_date}，使用东方财富回退')
    stocks_data = get_selected_stocks(context)
    raw_fallback_codes = [stock.get("SECURITY_CODE", "") for stock in stocks_data if isinstance(stock, dict)]
    return normalize_and_validate_stock_pool(raw_fallback_codes, limit=MAX_EXTERNAL_POOL_SIZE)


def sellall(context, each_stock):
    log.info("清仓卖出股票：" + each_stock)
    order_result = order_target(each_stock, 0)
    if each_stock in context.hold_list:
        del context.hold_list[each_stock]
    context.today_sell.append(each_stock)
    context.can_buy = max(0, MAX_HOLDINGS - len(context.portfolio.positions))
    return order_result

def add_suffix(stock_code):
    normalized_code = normalize_stock_code(stock_code)
    if normalized_code is not None:
        return normalized_code
    return stock_code

def yesterday():
    now = datetime.now()
    yesterday = now - timedelta(days=1)
    yesterday_formatted = yesterday.strftime("%Y%m%d")
    return yesterday_formatted

def send_trade_signal(trade_info):
    if MODE == 1:
        if not EMAIL_SENDER or not EMAIL_PASSWORD or not EMAIL_RECEIVER:
            logging.error("邮件发送功能已开启，但邮箱配置不完整")
            return
        message = MIMEText(json.dumps(trade_info), 'plain', 'utf-8')
        message['Subject'] = '交易信号'
        message['From'] = EMAIL_SENDER
        message['To'] = EMAIL_RECEIVER
        try:
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as server:
                server.login(EMAIL_SENDER, EMAIL_PASSWORD)
                server.sendmail(EMAIL_SENDER, EMAIL_RECEIVER, message.as_string())
            logging.info(f"交易信号邮件发送成功: {trade_info}")
        except Exception as e:
            logging.error(f"交易信号邮件发送失败: {e}")
    else:
        logging.info(f"邮件发送功能已关闭，交易信号: {trade_info}")
