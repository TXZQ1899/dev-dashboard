#!/usr/bin/env python3
"""Read-only exporter for Foliday DevOps. Python 3.9+, standard library only."""
import argparse
import csv
import getpass
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

ENVIRONMENTS = {"TEST": "测试", "SIMULATION": "仿真", "PRODUCT": "生产"}
APP_FIELDS = ["应用ID", "应用名", "应用类型", "代码库类型", "Git/代码库地址", "分支", "HTTP名", "HTTP端口", "详情地址"]
ENV_FIELDS = ["应用ID", "应用名", "Git/代码库地址", "环境代码", "环境名", "服务器IP", "部署ID", "配置ID", "状态", "错误"]


class ExportError(Exception):
    pass


class CollectionError(ExportError):
    """A collector-generated diagnostic safe for display; never raw HTTP text."""
    def __init__(self, message):
        super().__init__(message)
        self.safe_message = message


class AuthenticationError(ExportError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Client:
    def __init__(self, base_url, cookie, timeout=30, delay=0.15, retries=2):
        self.base_url = base_url.rstrip("/")
        self.cookie = cookie
        self.timeout = timeout
        self.delay = delay
        self.retries = retries
        self.opener = build_opener(NoRedirect())

    def get(self, path, params, allow_empty_environment=False):
        url = self.base_url + path + "?" + urlencode(params)
        for attempt in range(self.retries + 1):
            if self.delay:
                time.sleep(self.delay)
            request = Request(url, headers={"Cookie": self.cookie, "Accept": "application/json"})
            try:
                with self.opener.open(request, timeout=self.timeout) as response:
                    body = response.read()
            except HTTPError as exc:
                if exc.code in (301, 302, 303, 307, 308, 401, 403):
                    raise AuthenticationError("登录失效、无权限或发生重定向，请更新 Cookie 后重试") from None
                if exc.code not in (429, 500, 502, 503, 504) or attempt == self.retries:
                    raise ExportError(f"接口返回 HTTP {exc.code}") from None
            except (URLError, TimeoutError, OSError):
                if attempt == self.retries:
                    raise ExportError("连接失败或超时，请检查内网/VPN、域名和代理配置") from None
            else:
                try:
                    data = json.loads(body)
                except (ValueError, UnicodeError):
                    raise AuthenticationError("接口未返回 JSON，可能已跳转登录页，请检查 Cookie") from None
                if not isinstance(data, dict):
                    raise ExportError("接口响应不是对象")
                if str(data.get("code")) in ("401", "403") or data.get("isLogin") is False:
                    raise AuthenticationError("登录失效或没有读取权限")
                if str(data.get("code")) != "200":
                    # Do not log arbitrary server messages, which may contain sensitive data.
                    raise ExportError("接口业务状态不是 200")
                if "obj" not in data:
                    if allow_empty_environment and path == "/theone-web/ops/app/list/app" and data.get("isSucess") is True:
                        return None
                    raise ExportError("接口响应缺少 obj")
                return data["obj"]
            time.sleep(2 ** attempt)


def fetch_apps(client, page_size=100, app_types="", app_id=None):
    apps, seen, expected = [], set(), None
    page = 1
    while True:
        params = {"page.size": page_size, "page.pn": page, "sort.createTime": "desc",
                  "search.appType_in": app_types}
        if app_id is not None:
            params["search.id_eq"] = app_id
        obj = client.get("/theone-web/ops/app/list", params)
        if not isinstance(obj, dict) or not isinstance(obj.get("content"), list):
            raise ExportError("列表响应缺少 obj.content 数组")
        total = obj.get("totalElements")
        if not isinstance(total, int) or isinstance(total, bool) or total < 0:
            raise ExportError("列表响应缺少有效 totalElements，无法确认是否导出完整")
        if expected is None:
            expected = total
        elif total != expected:
            raise ExportError("导出期间应用总数变化，请在列表稳定后重试")
        rows = obj["content"]
        for row in rows:
            if not isinstance(row, dict) or row.get("id") is None:
                raise ExportError("应用数据缺少 id")
            key = str(row["id"])
            if key in seen:
                raise ExportError("分页返回重复应用，可能列表已变动；停止以避免遗漏")
            seen.add(key)
            apps.append(row)
        print(f"应用列表：第 {page} 页，{len(apps)}/{expected}", file=sys.stderr)
        if len(apps) == expected:
            return apps
        if not rows or len(apps) > expected:
            raise ExportError("分页条数与 totalElements 不一致")
        page += 1


def app_row(app, base_url):
    return dict(zip(APP_FIELDS, [app["id"], app.get("appName", ""), app.get("appType", ""),
        app.get("defaultContrVersionType", ""),
        app.get("defaultContrVersionUrlMaster") or app.get("defaultContrVersionUrl", ""),
        app.get("defaultContrVersionMasterName", ""), app.get("webName", ""),
        app.get("httpPort", ""), f"{base_url}/#/project/application/info?id={app['id']}"]))


def environment_rows(app, env, obj):
    common = {key: app[key] for key in ("应用ID", "应用名", "Git/代码库地址")}
    common.update({"环境代码": env, "环境名": ENVIRONMENTS[env]})
    if obj is None:
        return [dict(common, 状态="无环境配置")]
    if not isinstance(obj, dict) or not isinstance(obj.get("deployList"), list):
        raise ExportError("环境响应缺少 deployList 数组")
    if not obj["deployList"]:
        return [dict(common, 状态="无部署实例")]
    rows = []
    for deploy in obj["deployList"]:
        if not isinstance(deploy, dict):
            raise ExportError("部署实例不是对象")
        ip = deploy.get("ip")
        if ip is not None and not isinstance(ip, (str, int)):
            raise ExportError("服务器 IP 字段格式发生变化")
        rows.append(dict(common, 服务器IP=ip or "", 部署ID=deploy.get("deployId", ""),
                         配置ID=deploy.get("configId", ""), 状态="成功" if ip else "实例未返回IP"))
    return rows


def csv_value(value):
    if value is None:
        return ""
    value = str(value)
    # Prevent spreadsheet formula interpretation when opening CSV in Excel.
    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value


def write_csv(path, fields, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: csv_value(row.get(key, "")) for key in fields})


def run_export(client, output, page_size, app_types, app_id):
    apps = fetch_apps(client, page_size, app_types, app_id)
    application_rows = [app_row(app, client.base_url) for app in apps]
    write_csv(output / "applications.csv", APP_FIELDS, application_rows)
    errors, total_rows = [], 0
    try:
        with (output / "environments.csv").open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=ENV_FIELDS)
            writer.writeheader()
            stream.flush()
            for index, app in enumerate(application_rows, 1):
                for env in ENVIRONMENTS:
                    try:
                        obj = client.get("/theone-web/ops/app/list/app", {"appId": app["应用ID"], "envtype": env})
                        rows = environment_rows(app, env, obj)
                    except ExportError as exc:
                        error = {"应用ID": app["应用ID"], "环境代码": env, "错误": str(exc)}
                        errors.append(error)
                        rows = [dict(app, 环境代码=env, 环境名=ENVIRONMENTS[env], 状态="失败", 错误=str(exc))]
                        if isinstance(exc, AuthenticationError):
                            raise
                    for row in rows:
                        writer.writerow({key: csv_value(row.get(key, "")) for key in ENV_FIELDS})
                        total_rows += 1
                    stream.flush()
                print(f"环境信息：{index}/{len(apps)}", file=sys.stderr)
    finally:
        (output / "errors.json").write_text(json.dumps(errors, ensure_ascii=False, indent=2), encoding="utf-8")
    if errors:
        raise ExportError(f"{len(errors)} 个环境读取失败，已保留成功数据，详见 errors.json")
    (output / "summary.json").write_text(json.dumps({
        "completed": True, "applications": len(apps), "environment_rows": total_rows,
        "app_types": app_types or "全部类型", "app_id": app_id,
        "finished_at": datetime.now().astimezone().isoformat(),
    }, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="导出 DevOps 应用与环境服务器；仅调用两个只读 GET 接口")
    parser.add_argument("--base-url", default="http://devops.folidaymall.com")
    parser.add_argument("--cookie-file", type=Path, help="包含 Cookie 请求头值的本地文本文件")
    parser.add_argument("--output", type=Path, help="新的输出目录（不能已存在）")
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--app-types", default="", help="逗号分隔；默认全部类型")
    parser.add_argument("--app-id", type=int, help="只导出指定应用，便于首次验证")
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--delay", type=float, default=0.15, help="请求间隔秒数")
    args = parser.parse_args()
    parts = urlsplit(args.base_url)
    if parts.scheme not in ("http", "https") or not parts.netloc or parts.username or parts.password or parts.query or parts.fragment or parts.path not in ("", "/"):
        parser.error("--base-url 必须是平台源地址，例如 http://devops.folidaymall.com")
    if args.page_size < 1 or args.timeout <= 0 or args.delay < 0:
        parser.error("page-size、timeout 必须为正数，delay 不能为负数")
    try:
        cookie = (args.cookie_file.read_text(encoding="utf-8") if args.cookie_file
                  else os.environ.get("DEVOPS_COOKIE") or getpass.getpass("粘贴已登录请求的 Cookie（输入不回显）：")).strip()
        if cookie.lower().startswith("cookie:"):
            cookie = cookie[7:].strip()
        if not cookie or "\n" in cookie or "\r" in cookie:
            raise ExportError("Cookie 必须是非空的单行请求头值")
        output = args.output or Path("exports") / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        output.mkdir(parents=True, exist_ok=False)
        client = Client(args.base_url, cookie, args.timeout, args.delay)
        run_export(client, output, args.page_size, args.app_types, args.app_id)
        print(f"导出完成：{output.resolve()}")
        return 0
    except (ExportError, OSError, EOFError) as exc:
        print(f"导出未完成：{exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("导出已中断，已写入的数据保留。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
