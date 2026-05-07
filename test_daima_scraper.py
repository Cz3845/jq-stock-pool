import ast
import json
import time
import types
import unittest
import uuid
from datetime import datetime
from pathlib import Path


SOURCE_PATH = Path(__file__).with_name("daima.py")
REQUIRED_FUNCTIONS = {
    "get_selected_stocks",
    "_get_eastmoney_fingerprint",
    "_build_eastmoney_payload",
    "_extract_eastmoney_stock_list",
    "_find_stock_rows",
    "_preview_response_text",
    "_get_eastmoney_session",
    "_build_eastmoney_common_headers",
    "_warmup_eastmoney_session",
    "_build_eastmoney_cookies",
}


class FakeLog:
    def __init__(self):
        self.messages = []

    def info(self, message):
        self.messages.append(str(message))

    def error(self, message):
        self.messages.append(str(message))


class FakeResponse:
    def __init__(self, status_code=200, text="<html>not json</html>", headers=None, json_data=None, json_error=None):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {"Content-Type": "text/html"}
        self.json_data = json_data
        self.json_error = json_error or ValueError("Expecting value: line 1 column 1 (char 0)")

    def json(self):
        if self.json_data is not None:
            return self.json_data
        raise self.json_error


class FakeListJsonResponse(FakeResponse):
    def __init__(self):
        super().__init__(text="[]", headers={"Content-Type": "application/json"}, json_data=[])


class FakeSession:
    def __init__(self, response=None):
        self.cookies = {}
        self.get_calls = []
        self.post_calls = []
        self.response = response or FakeResponse()
        self.get_error = None

    def get(self, url, params=None, headers=None, timeout=None):
        self.get_calls.append({"url": url, "params": params, "headers": headers, "timeout": timeout})
        if self.get_error:
            raise self.get_error
        return FakeResponse(text='{"code":"100"}', headers={"Content-Type": "application/json"}, json_data={"code": "100"})

    def post(self, url, headers=None, json=None, timeout=None):
        self.post_calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return self.response


class FakeRequests:
    RequestException = Exception

    def __init__(self, response=None, get_response=None, get_sequence=None):
        self.sessions = []
        self.response = response
        self.get_response = get_response or response or FakeResponse()
        self.get_sequence = list(get_sequence or [])
        self.get_calls = []
        self.get_error = None

    def Session(self):
        session = FakeSession(self.response)
        self.sessions.append(session)
        return session

    def get(self, url, params=None, timeout=None):
        self.get_calls.append({"url": url, "params": params, "timeout": timeout})
        if self.get_error:
            raise self.get_error
        if self.get_sequence:
            next_result = self.get_sequence.pop(0)
            if isinstance(next_result, BaseException):
                raise next_result
            return next_result
        return self.get_response


class Context:
    pass


class FakeOrderCost:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class FakeSecurityInfo:
    def __init__(self, display_name="测试股票"):
        self.display_name = display_name


def noop(*args, **kwargs):
    return None


def load_function_namespace(function_names, extra_namespace=None):
    tree = ast.parse(SOURCE_PATH.read_text(encoding="utf-8"))
    selected_nodes = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            selected_nodes.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name in function_names:
            selected_nodes.append(node)
    module = ast.Module(body=selected_nodes, type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {
        "json": json,
        "uuid": uuid,
        "time": time,
        "requests": FakeRequests(),
        "log": FakeLog(),
        "OrderCost": FakeOrderCost,
        "set_order_cost": noop,
        "set_slippage": noop,
        "PriceRelatedSlippage": lambda value: value,
        "set_benchmark": noop,
        "set_option": noop,
    }
    if extra_namespace:
        namespace.update(extra_namespace)
    exec(compile(module, str(SOURCE_PATH), "exec"), namespace)
    return namespace


def load_scraper_namespace(response=None):
    tree = ast.parse(SOURCE_PATH.read_text(encoding="utf-8"))
    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    missing = REQUIRED_FUNCTIONS - set(functions)
    if missing:
        raise AssertionError(f"missing scraper functions: {sorted(missing)}")
    module = ast.Module(body=[functions[name] for name in REQUIRED_FUNCTIONS], type_ignores=[])
    ast.fix_missing_locations(module)
    fake_requests = FakeRequests(response)
    namespace = {
        "json": json,
        "uuid": uuid,
        "time": time,
        "requests": fake_requests,
        "log": FakeLog(),
    }
    exec(compile(module, str(SOURCE_PATH), "exec"), namespace)
    namespace["fake_requests"] = fake_requests
    return namespace


def load_stock_pool_namespace(extra_namespace=None):
    tree = ast.parse(SOURCE_PATH.read_text(encoding="utf-8"))
    function_names = {
        "normalize_stock_code",
        "add_suffix",
        "get_external_stock_pool",
        "normalize_and_validate_stock_pool",
        "get_remote_stock_pool",
        "get_today_stock_pool",
        "_preview_response_text",
        "_get_remote_stock_pool_urls",
        "_request_remote_stock_pool_payload",
        "_extract_remote_stock_pool_codes",
    }
    selected_nodes = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            selected_nodes.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name in function_names:
            selected_nodes.append(node)
    module = ast.Module(body=selected_nodes, type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {
        "log": FakeLog(),
        "requests": FakeRequests(),
        "time": time,
        "get_security_info": lambda code: FakeSecurityInfo(code),
        "get_selected_stocks": lambda context: [],
    }
    if extra_namespace:
        namespace.update(extra_namespace)
    exec(compile(module, str(SOURCE_PATH), "exec"), namespace)
    return namespace


class ExternalStockPoolTests(unittest.TestCase):
    def test_normalize_stock_code_supports_six_digit_and_jq_codes(self):
        namespace = load_stock_pool_namespace()

        self.assertEqual(namespace["normalize_stock_code"]("000001"), "000001.XSHE")
        self.assertEqual(namespace["normalize_stock_code"]("300750"), "300750.XSHE")
        self.assertEqual(namespace["normalize_stock_code"]("600000"), "600000.XSHG")
        self.assertEqual(namespace["normalize_stock_code"]("688981"), "688981.XSHG")
        self.assertEqual(namespace["normalize_stock_code"]("000001.XSHE"), "000001.XSHE")
        self.assertEqual(namespace["normalize_stock_code"]("600000.xshg"), "600000.XSHG")
        self.assertIsNone(namespace["normalize_stock_code"](""))
        self.assertIsNone(namespace["normalize_stock_code"](None))
        self.assertIsNone(namespace["normalize_stock_code"]("abc"))
        self.assertEqual(namespace["add_suffix"]("000001.XSHE"), "000001.XSHE")

    def test_get_external_stock_pool_uses_current_date_key(self):
        namespace = load_stock_pool_namespace()
        namespace["EXTERNAL_STOCK_POOL"] = {
            "2026-04-07": ["000001.XSHE", "600000"],
        }
        context = Context()
        context.current_dt = datetime(2026, 4, 7, 9, 0)

        self.assertEqual(namespace["get_external_stock_pool"](context), ["000001.XSHE", "600000"])

        context.current_dt = datetime(2026, 4, 8, 9, 0)
        self.assertEqual(namespace["get_external_stock_pool"](context), [])

    def test_normalize_and_validate_stock_pool_filters_duplicates_and_applies_limit(self):
        def fake_get_security_info(code):
            if code == "688981.XSHG":
                return None
            return FakeSecurityInfo(code)

        namespace = load_stock_pool_namespace({"get_security_info": fake_get_security_info})

        result = namespace["normalize_and_validate_stock_pool"]([
            "000001",
            "000001.XSHE",
            "600000.xshg",
            "",
            "abc",
            "688981",
            "300750",
        ], limit=3)

        self.assertEqual(result, ["000001.XSHE", "600000.XSHG", "300750.XSHE"])

    def test_get_today_stock_pool_uses_external_pool_without_eastmoney_when_present(self):
        eastmoney_calls = []
        namespace = load_stock_pool_namespace({"get_selected_stocks": lambda context: eastmoney_calls.append(context)})
        namespace["EXTERNAL_STOCK_POOL"] = {"2026-04-07": ["000001", "600000"]}
        namespace["USE_EASTMONEY_FALLBACK"] = True
        context = Context()
        context.current_dt = datetime(2026, 4, 7, 9, 0)

        result = namespace["get_today_stock_pool"](context)

        self.assertEqual(result, ["000001.XSHE", "600000.XSHG"])
        self.assertEqual(eastmoney_calls, [])

    def test_get_today_stock_pool_uses_remote_pool_when_code_date_missing(self):
        fake_requests = FakeRequests(get_response=FakeResponse(
            text='{"2026-04-08":["000001","600000"]}',
            headers={"Content-Type": "application/json"},
            json_data={"2026-04-08": ["000001", "600000"]},
        ))
        namespace = load_stock_pool_namespace({"requests": fake_requests})
        namespace["EXTERNAL_STOCK_POOL"] = {}
        namespace["REMOTE_STOCK_POOL_ENABLED"] = True
        namespace["REMOTE_STOCK_POOL_JSON_URL"] = "https://raw.example.test/stock_pool.json"
        context = Context()
        context.current_dt = datetime(2026, 4, 8, 9, 0)

        result = namespace["get_today_stock_pool"](context)

        self.assertEqual(result, ["000001.XSHE", "600000.XSHG"])
        self.assertEqual(fake_requests.get_calls, [{
            "url": "https://raw.example.test/stock_pool.json",
            "params": None,
            "timeout": (3, 15),
        }])

    def test_get_today_stock_pool_external_date_overrides_remote_pool(self):
        fake_requests = FakeRequests(get_response=FakeResponse(json_data={"2026-04-08": ["600000"]}))
        namespace = load_stock_pool_namespace({"requests": fake_requests})
        namespace["EXTERNAL_STOCK_POOL"] = {"2026-04-08": ["300750"]}
        namespace["REMOTE_STOCK_POOL_ENABLED"] = True
        namespace["REMOTE_STOCK_POOL_JSON_URL"] = "https://example.test/stock-pool"
        context = Context()
        context.current_dt = datetime(2026, 4, 8, 9, 0)

        result = namespace["get_today_stock_pool"](context)

        self.assertEqual(result, ["300750.XSHE"])
        self.assertEqual(fake_requests.get_calls, [])

    def test_get_remote_stock_pool_retries_after_timeout(self):
        fake_requests = FakeRequests(get_sequence=[
            Exception("read timed out"),
            FakeResponse(
                text='{"2026-05-07":["000001"]}',
                headers={"Content-Type": "application/json"},
                json_data={"2026-05-07": ["000001"]},
            ),
        ])
        namespace = load_stock_pool_namespace({"requests": fake_requests})
        namespace["REMOTE_STOCK_POOL_ENABLED"] = True
        namespace["REMOTE_STOCK_POOL_JSON_URL"] = "https://raw.example.test/stock_pool.json"
        namespace["REMOTE_STOCK_POOL_RETRY_SLEEP"] = 0
        context = Context()
        context.current_dt = datetime(2026, 5, 7, 9, 0)

        result = namespace["get_remote_stock_pool"](context)

        self.assertEqual(result, ["000001"])
        self.assertEqual(len(fake_requests.get_calls), 2)

    def test_get_remote_stock_pool_tries_second_url_after_first_fails(self):
        fake_requests = FakeRequests(get_sequence=[
            FakeResponse(status_code=500, text="server error"),
            FakeResponse(
                text='{"2026-05-07":["600000"]}',
                headers={"Content-Type": "application/json"},
                json_data={"2026-05-07": ["600000"]},
            ),
        ])
        namespace = load_stock_pool_namespace({"requests": fake_requests})
        namespace["REMOTE_STOCK_POOL_ENABLED"] = True
        namespace["REMOTE_STOCK_POOL_JSON_URL"] = "https://raw.example.test/stock_pool.json"
        namespace["REMOTE_STOCK_POOL_JSON_URLS"] = ["https://mirror.example.test/stock_pool.json"]
        namespace["REMOTE_STOCK_POOL_RETRIES"] = 0
        context = Context()
        context.current_dt = datetime(2026, 5, 7, 9, 0)

        result = namespace["get_remote_stock_pool"](context)

        self.assertEqual(result, ["600000"])
        self.assertEqual([call["url"] for call in fake_requests.get_calls], [
            "https://raw.example.test/stock_pool.json",
            "https://mirror.example.test/stock_pool.json",
        ])

    def test_get_remote_stock_pool_uses_cached_payload_after_later_timeout(self):
        fake_requests = FakeRequests(get_response=FakeResponse(
            text='{"2026-05-07":["300750"]}',
            headers={"Content-Type": "application/json"},
            json_data={"2026-05-07": ["300750"]},
        ))
        namespace = load_stock_pool_namespace({"requests": fake_requests})
        namespace["REMOTE_STOCK_POOL_ENABLED"] = True
        namespace["REMOTE_STOCK_POOL_JSON_URL"] = "https://raw.example.test/stock_pool.json"
        namespace["REMOTE_STOCK_POOL_RETRY_SLEEP"] = 0
        context = Context()
        context.current_dt = datetime(2026, 5, 7, 9, 0)

        self.assertEqual(namespace["get_remote_stock_pool"](context), ["300750"])
        fake_requests.get_error = Exception("read timed out")

        self.assertEqual(namespace["get_remote_stock_pool"](context), ["300750"])

    def test_get_today_stock_pool_remote_empty_list_does_not_use_eastmoney(self):
        eastmoney_calls = []
        fake_requests = FakeRequests(get_response=FakeResponse(
            text='{"2026-04-08":[]}',
            headers={"Content-Type": "application/json"},
            json_data={"2026-04-08": []},
        ))
        namespace = load_stock_pool_namespace({
            "requests": fake_requests,
            "get_selected_stocks": lambda context: eastmoney_calls.append(context),
        })
        namespace["EXTERNAL_STOCK_POOL"] = {}
        namespace["REMOTE_STOCK_POOL_ENABLED"] = True
        namespace["REMOTE_STOCK_POOL_JSON_URL"] = "https://example.test/stock-pool"
        namespace["USE_EASTMONEY_FALLBACK"] = True
        context = Context()
        context.current_dt = datetime(2026, 4, 8, 9, 0)

        result = namespace["get_today_stock_pool"](context)

        self.assertEqual(result, [])
        self.assertEqual(eastmoney_calls, [])

    def test_get_remote_stock_pool_returns_none_for_malformed_payloads(self):
        malformed_payloads = [
            [],
            {"2026-04-08": "000001"},
            {"2026-04-08": {"codes": ["000001"]}},
        ]
        for payload in malformed_payloads:
            with self.subTest(payload=payload):
                fake_requests = FakeRequests(get_response=FakeResponse(
                    text=json.dumps(payload),
                    headers={"Content-Type": "application/json"},
                    json_data=payload,
                ))
                namespace = load_stock_pool_namespace({"requests": fake_requests})
                namespace["REMOTE_STOCK_POOL_ENABLED"] = True
                namespace["REMOTE_STOCK_POOL_JSON_URL"] = "https://example.test/stock-pool"
                context = Context()
                context.current_dt = datetime(2026, 4, 8, 9, 0)

                self.assertIsNone(namespace["get_remote_stock_pool"](context))

    def test_remote_failure_falls_back_to_eastmoney_when_enabled(self):
        fake_requests = FakeRequests(get_response=FakeResponse(status_code=500, text="server error"))
        namespace = load_stock_pool_namespace({
            "requests": fake_requests,
            "get_selected_stocks": lambda context: [{"SECURITY_CODE": "600000"}],
        })
        namespace["EXTERNAL_STOCK_POOL"] = {}
        namespace["REMOTE_STOCK_POOL_ENABLED"] = True
        namespace["REMOTE_STOCK_POOL_JSON_URL"] = "https://example.test/stock-pool"
        namespace["USE_EASTMONEY_FALLBACK"] = True
        context = Context()
        context.current_dt = datetime(2026, 4, 8, 9, 0)

        result = namespace["get_today_stock_pool"](context)

        self.assertEqual(result, ["600000.XSHG"])

    def test_get_today_stock_pool_returns_empty_without_fallback_when_missing(self):
        eastmoney_calls = []
        namespace = load_stock_pool_namespace({"get_selected_stocks": lambda context: eastmoney_calls.append(context)})
        namespace["EXTERNAL_STOCK_POOL"] = {}
        namespace["USE_EASTMONEY_FALLBACK"] = False
        context = Context()
        context.current_dt = datetime(2026, 4, 8, 9, 0)

        result = namespace["get_today_stock_pool"](context)

        self.assertEqual(result, [])
        self.assertEqual(eastmoney_calls, [])

    def test_get_today_stock_pool_empty_date_entry_does_not_use_fallback(self):
        eastmoney_calls = []
        namespace = load_stock_pool_namespace({"get_selected_stocks": lambda context: eastmoney_calls.append(context)})
        namespace["EXTERNAL_STOCK_POOL"] = {"2026-04-08": []}
        namespace["USE_EASTMONEY_FALLBACK"] = True
        context = Context()
        context.current_dt = datetime(2026, 4, 8, 9, 0)

        result = namespace["get_today_stock_pool"](context)

        self.assertEqual(result, [])
        self.assertEqual(eastmoney_calls, [])

    def test_get_today_stock_pool_uses_eastmoney_only_when_fallback_enabled_and_missing(self):
        namespace = load_stock_pool_namespace({
            "get_selected_stocks": lambda context: [
                {"SECURITY_CODE": "000001"},
                {"SECURITY_CODE": "600000"},
            ]
        })
        namespace["EXTERNAL_STOCK_POOL"] = {}
        namespace["USE_EASTMONEY_FALLBACK"] = True
        context = Context()
        context.current_dt = datetime(2026, 4, 8, 9, 0)

        result = namespace["get_today_stock_pool"](context)

        self.assertEqual(result, ["000001.XSHE", "600000.XSHG"])


class StrategyContextDefaultsTests(unittest.TestCase):
    def test_initialize_creates_handle_data_defaults(self):
        namespace = load_function_namespace({"initialize"})
        context = Context()

        namespace["initialize"](context)

        self.assertEqual(context.subscribe_list, [])
        self.assertEqual(context.pre_value, {})
        self.assertEqual(context.today_stock, [])
        self.assertEqual(context.today_sell, [])
        self.assertEqual(context.can_buy, 3)


class EastmoneyScraperTests(unittest.TestCase):
    def test_payload_uses_latest_report_fields_and_matching_fingerprint(self):
        namespace = load_scraper_namespace()
        payload = namespace["_build_eastmoney_payload"]("abc123")

        self.assertEqual(payload["fingerprint"], "abc123")
        self.assertEqual(payload["keyWordNew"], "前一日收盘价涨停，人气排名,非ST；非首板；非科创板；")
        self.assertNotIn("keyWord", payload)
        self.assertTrue(payload["needAmbiguousSuggest"])
        self.assertEqual(payload["client"], "WEB")
        self.assertEqual(payload["biz"], "web_ai_select_stocks")
        self.assertFalse(payload["needShowStockNum"])
        self.assertEqual(payload["dxInfoNew"], [])
        self.assertEqual(json.loads(payload["customDataNew"]), [
            {"type": "text", "value": payload["keyWordNew"], "extra": ""}
        ])

    def test_extracts_stock_rows_from_known_and_nested_result_shapes(self):
        namespace = load_scraper_namespace()
        direct_result = {
            "data": {
                "result": {
                    "dataList": [
                        {"SECURITY_CODE": "000001", "SECURITY_SHORT_NAME": "平安银行"},
                        {"SECURITY_CODE": "", "SECURITY_SHORT_NAME": "无效"},
                    ]
                }
            }
        }
        nested_result = {
            "data": {
                "result": {
                    "columns": [],
                    "payload": {"rows": [{"SECURITY_CODE": "600000"}]},
                }
            }
        }

        self.assertEqual(namespace["_extract_eastmoney_stock_list"](direct_result), [
            {"SECURITY_CODE": "000001", "SECURITY_SHORT_NAME": "平安银行"}
        ])
        self.assertEqual(namespace["_extract_eastmoney_stock_list"](nested_result), [
            {"SECURITY_CODE": "600000"}
        ])

    def test_get_selected_stocks_returns_empty_list_for_non_json_response(self):
        namespace = load_scraper_namespace()
        context = Context()

        result = namespace["get_selected_stocks"](context)

        self.assertEqual(result, [])
        fake_requests = namespace["fake_requests"]
        self.assertEqual(len(fake_requests.sessions), 1)
        session = fake_requests.sessions[0]
        self.assertEqual(session.cookies["qgqp_b_id"], context.eastmoney_fingerprint)
        self.assertEqual(session.post_calls[0]["json"]["fingerprint"], context.eastmoney_fingerprint)

    def test_get_selected_stocks_returns_empty_list_for_non_dict_json_response(self):
        namespace = load_scraper_namespace(FakeListJsonResponse())
        context = Context()

        result = namespace["get_selected_stocks"](context)

        self.assertEqual(result, [])
        self.assertTrue(any("JSON结构异常" in message for message in namespace["log"].messages))

    def test_get_selected_stocks_warms_browser_session_before_search(self):
        namespace = load_scraper_namespace(FakeResponse(text='{"code":"100","data":{"result":{"dataList":[{"SECURITY_CODE":"000001"}]}}}', headers={"Content-Type": "application/json"}, json_data={"code": "100", "data": {"result": {"dataList": [{"SECURITY_CODE": "000001"}]}}}))
        context = Context()

        result = namespace["get_selected_stocks"](context)

        self.assertEqual(result, [{"SECURITY_CODE": "000001"}])
        session = namespace["fake_requests"].sessions[0]
        self.assertEqual([call["url"] for call in session.get_calls], [
            "https://xuangu.eastmoney.com/",
            "https://np-tjxg-operation-b.eastmoney.com/isPreserve",
            "https://np-tjxg-operation-b.eastmoney.com/ip/needFilter",
        ])
        self.assertEqual(session.get_calls[1]["params"], {"source": "WEB"})
        self.assertEqual(len(session.post_calls), 1)
        self.assertIn("qgqp_b_id", session.cookies)
        self.assertIn("st_si", session.cookies)
        self.assertIn("sec-ch-ua", session.post_calls[0]["headers"])

    def test_get_selected_stocks_reuses_context_session_across_calls(self):
        namespace = load_scraper_namespace(FakeResponse(text='{"code":"100","data":{"result":{"dataList":[{"SECURITY_CODE":"000001"}]}}}', headers={"Content-Type": "application/json"}, json_data={"code": "100", "data": {"result": {"dataList": [{"SECURITY_CODE": "000001"}]}}}))
        context = Context()

        namespace["get_selected_stocks"](context)
        namespace["get_selected_stocks"](context)

        self.assertEqual(len(namespace["fake_requests"].sessions), 1)
        self.assertEqual(len(context.eastmoney_session.get_calls), 3)
        self.assertEqual(len(context.eastmoney_session.post_calls), 2)

    def test_common_headers_do_not_request_brotli(self):
        namespace = load_scraper_namespace()

        headers = namespace["_build_eastmoney_common_headers"]()

        self.assertEqual(headers["Accept-Encoding"], "gzip, deflate")

    def test_failed_warmup_is_retried_on_next_call(self):
        namespace = load_scraper_namespace(FakeResponse(text='{"code":"100","data":{"result":{"dataList":[{"SECURITY_CODE":"000001"}]}}}', headers={"Content-Type": "application/json"}, json_data={"code": "100", "data": {"result": {"dataList": [{"SECURITY_CODE": "000001"}]}}}))
        context = Context()
        session = namespace["_get_eastmoney_session"](context, "abc123")
        session.get_error = Exception("network down")

        namespace["get_selected_stocks"](context)
        session.get_error = None
        namespace["get_selected_stocks"](context)

        self.assertEqual(len(session.get_calls), 6)


if __name__ == "__main__":
    unittest.main()
