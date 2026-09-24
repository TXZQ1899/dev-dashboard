#!/usr/bin/env python3
"""Deduplicate exported Git URLs and check Codeup web read access using a cookie."""
import argparse
import csv
import json
import re
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlencode, urlsplit
from urllib.request import Request, build_opener

from export_devops import NoRedirect, csv_value

HOST = "codeup.aliyun.com"
FIELDS = ["git仓库名", "git地址", "是否能访问", "说明", "HTTP状态", "错误代码", "关联应用数"]


def normalize_url(value):
    """Preserve case-sensitive paths; unify HTTPS/SSH/.git/trailing slash."""
    raw = value.strip()
    if not raw:
        raise ValueError("仓库地址为空")
    if "://" not in raw:
        match = re.fullmatch(r"(?:[^/@:]+@)?([^/:]+):(.+)", raw)
        if not match:
            raise ValueError("无法识别 Git 地址")
        raw = "ssh://" + match[1] + "/" + match[2]
    parts = urlsplit(raw)
    if parts.scheme not in ("http", "https", "ssh", "git") or not parts.hostname:
        raise ValueError("不支持的 Git 地址格式")
    if parts.query or parts.fragment or parts.password:
        raise ValueError("地址含查询参数、片段或密码，需人工核对")
    host = parts.hostname.lower()
    port = parts.port
    if port and (parts.scheme, port) not in (("https", 443), ("http", 80), ("ssh", 22), ("git", 9418)):
        raise ValueError("非默认端口，需人工核对")
    path = unquote(parts.path).strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    segments = path.split("/")
    if len(segments) < 2 or any(s in ("", ".", "..") for s in segments) or any(ord(c) < 32 for c in path):
        raise ValueError("仓库路径无效")
    return host, path, f"https://{host}/{quote(path, safe='/@-._~')}.git"


def load_repositories(path):
    repositories = {}
    count = 0
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or "Git/代码库地址" not in reader.fieldnames:
            raise ValueError("输入必须是包含 Git/代码库地址 列的 applications.csv")
        for row in reader:
            count += 1
            raw = row.get("Git/代码库地址", "") or ""
            try:
                host, repo_path, url = normalize_url(raw)
                key = url
                name, error = repo_path.split("/")[-1], ""
            except ValueError as exc:
                host, repo_path, url, name, error = "", "", raw, "", str(exc)
                key = "invalid:" + raw.strip()
            if key not in repositories:
                repositories[key] = dict(host=host, path=repo_path, url=url, name=name, error=error, count=0)
            repositories[key]["count"] += 1
    return count, list(repositories.values())


def classify(data):
    if not isinstance(data, dict):
        return "无法确认", "接口 JSON 结构不匹配", "", False
    code = str(data.get("errorCode") or "")
    action = str(data.get("errorAction") or "").lower()
    if code.upper() in {"UNAUTHORIZED", "NOT_LOGIN", "LOGIN_REQUIRED", "401"} or "login" in action:
        return "无法确认", "Cookie 已失效或需要重新登录", code, True
    if code.upper() in {"FORBIDDEN", "ACCESS_DENIED", "403"}:
        return "否", "当前账号无权访问", code, False
    if code.upper() in {"NOT_FOUND", "PROJECT_NOT_FOUND", "404"}:
        return "否", "仓库不存在或对当前账号不可见", code, False
    result = data.get("result")
    resource = result.get("pathResource") if isinstance(result, dict) else None
    if (data.get("success") is True and isinstance(resource, dict)
            and str(resource.get("type", "")).upper() == "PROJECT" and resource.get("id")
            and result.get("assetsName") != "error"):
        return "是", "仓库查询成功（网页读取权限）", "", False
    return "无法确认", "接口未提供明确的仓库访问结果", code, False


class Checker:
    def __init__(self, cookie, timeout=25, delay=0.2):
        self.cookie, self.timeout, self.delay = cookie, timeout, delay
        self.opener = build_opener(NoRedirect())

    def check(self, repo):
        # Cookie is only ever sent to this fixed HTTPS host; never follow redirects.
        if repo["host"] != HOST:
            return "未检查", "非 Codeup 域名，需要该平台的独立登录态", "", "", False
        url = f"https://{HOST}/portal/path_info?" + urlencode({"path": "/" + repo["path"]})
        for attempt in range(3):
            time.sleep(self.delay)
            try:
                with self.opener.open(Request(url, headers={"Cookie": self.cookie,
                        "Accept": "application/json", "User-Agent": "Mozilla/5.0"}), timeout=self.timeout) as response:
                    status = response.status
                    body = response.read()
            except HTTPError as exc:
                status = exc.code
                if status in (401, 301, 302, 303, 307, 308):
                    return "无法确认", "登录失效或发生重定向，停止后续检查", str(status), "", True
                if status in (429, 500, 502, 503, 504) and attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                if status == 404:
                    return "无法确认", "查询接口 HTTP 404，无法确认仓库状态", str(status), "", False
                if status == 403:
                    # A WAF can also return 403; only structured API FORBIDDEN proves denial.
                    body = exc.read()
                else:
                    return "无法确认", "服务异常或请求受限", str(status), "", False
            except (URLError, TimeoutError, OSError):
                if attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                return "无法确认", "网络连接失败或超时", "", "", False
            try:
                data = json.loads(body)
            except (ValueError, UnicodeError):
                return "无法确认", "响应不是 JSON，可能遇到登录页或安全验证", str(status), "", True
            access, reason, code, stop = classify(data)
            return access, reason, str(status), code, stop


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, help="applications.csv；默认选择最近修改的导出文件")
    parser.add_argument("--cookie-file", type=Path, default=Path("codeup-cookie.txt"))
    parser.add_argument("--output", type=Path, help="新的输出目录")
    parser.add_argument("--timeout", type=float, default=25)
    parser.add_argument("--delay", type=float, default=0.2)
    parser.add_argument("--dedupe-only", action="store_true", help="仅去重，不联网、不读取 Cookie")
    args = parser.parse_args()
    if args.timeout <= 0 or args.delay < 0:
        parser.error("timeout 必须为正数，delay 不能为负数")
    try:
        candidates = list(Path("exports").glob("*/applications.csv"))
        source = args.input or (max(candidates, key=lambda p: p.stat().st_mtime) if candidates else None)
        if source is None:
            raise ValueError("没有找到 applications.csv，请指定 --input")
        count, repos = load_repositories(source)
        checker = None
        if not args.dedupe_only:
            cookie = args.cookie_file.read_text(encoding="utf-8-sig").strip()
            if cookie.lower().startswith("cookie:"):
                cookie = cookie[7:].strip()
            if not cookie or "\n" in cookie or "\r" in cookie:
                raise ValueError("Cookie 文件必须包含非空、单行的 Cookie 请求头值")
            checker = Checker(cookie, args.timeout, args.delay)
        output = args.output or Path("exports") / ("codeup-access-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
        output.mkdir(parents=True, exist_ok=False)
        counts, stopped = Counter(), False
        print(f"输入：{source}；应用 {count} 条，去重后地址 {len(repos)} 条", flush=True)
        with (output / "repository_access.csv").open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=FIELDS)
            writer.writeheader()
            for index, repo in enumerate(repos, 1):
                http, code = "", ""
                if repo["error"]:
                    access, reason = "未检查", repo["error"]
                elif repo["host"] != HOST:
                    access, reason = "未检查", "非 Codeup 域名，需要该平台的独立登录态"
                elif args.dedupe_only:
                    access, reason = "未检查", "仅执行去重"
                elif stopped:
                    access, reason = "未检查", "前序登录或安全验证异常，未继续请求"
                else:
                    access, reason, http, code, stopped = checker.check(repo)
                values = [repo["name"], repo["url"], access, reason, http, code, repo["count"]]
                writer.writerow(dict(zip(FIELDS, map(csv_value, values))))
                stream.flush()
                counts[access] += 1
                print(f"[{index}/{len(repos)}] {access}", flush=True)
        summary = {"input": str(source.resolve()), "application_rows": count,
                   "unique_addresses_including_invalid": len(repos),
                   "valid_unique_repositories": sum(not r["error"] for r in repos),
                   "codeup_repositories": sum(r["host"] == HOST for r in repos),
                   "results": dict(counts), "authentication_stopped": stopped,
                   "scope": "Codeup 网页仓库读取权限，不验证 Git SSH/HTTPS clone 凭据"}
        (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"结果：{output.resolve() / 'repository_access.csv'}\n{dict(counts)}")
        return 2 if stopped or counts["无法确认"] else 0
    except (OSError, ValueError) as exc:
        print(f"检查失败：{exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("检查中断，已写入 CSV 的结果保留。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
