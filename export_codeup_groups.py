#!/usr/bin/env python3
"""Export the Codeup 'all groups' page to an Excel workbook."""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, build_opener

from export_devops import NoRedirect, ExportError, AuthenticationError

BASE = "https://codeup.aliyun.com"
PAGE = BASE + "/groups?navKey=all"
ROOT = Path(__file__).resolve().parent
NODE = "/Users/vian/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node"

# Excel 生成器位置：宿主机在 environment-web/scripts/，容器内脚本被复制到 /app/runtime/。
# 找不到时按需在 --json-only 模式使用，Excel 生成会明确报错而不是静默失败。
def _find_builder():
    here = Path(__file__).resolve().parent
    for candidate in (here / "environment-web" / "scripts" / "build_codeup_groups.mjs",
                      here / "scripts" / "build_codeup_groups.mjs",
                      here.parent / "environment-web" / "scripts" / "build_codeup_groups.mjs"):
        if candidate.is_file():
            return candidate
    return here / "environment-web" / "scripts" / "build_codeup_groups.mjs"


BUILDER = _find_builder()


def read_cookie(path):
    cookie = path.read_text(encoding="utf-8-sig").strip()
    if cookie.lower().startswith("cookie:"):
        cookie = cookie[7:].strip()
    if not cookie or "\n" in cookie or "\r" in cookie:
        raise ExportError("Cookie 文件必须包含非空单行 Cookie 请求头值")
    return cookie


class GroupsClient:
    def __init__(self, cookie, timeout=30, delay=0.2):
        self.cookie, self.timeout, self.delay = cookie, timeout, delay
        self.opener = build_opener(NoRedirect())

    def read(self, path, params=None):
        if path not in ("/groups", "/api/v4/groups/all"):
            raise ExportError("拒绝访问非代码组只读接口")
        url = BASE + path + ("?" + urlencode(params) if params else "")
        for attempt in range(3):
            time.sleep(self.delay)
            try:
                with self.opener.open(Request(url, headers={"Cookie": self.cookie,
                        "Accept": "application/json" if path.startswith("/api/") else "text/html",
                        "User-Agent": "Mozilla/5.0"}), timeout=self.timeout) as response:
                    return response.read().decode("utf-8")
            except HTTPError as exc:
                if exc.code in (301, 302, 303, 307, 308, 401, 403):
                    raise AuthenticationError("Codeup 登录失效或无权读取，请更新 codeup-cookie.txt") from None
                if exc.code not in (429, 500, 502, 503, 504) or attempt == 2:
                    raise ExportError(f"Codeup 接口 HTTP {exc.code}") from None
            except (URLError, TimeoutError, OSError):
                if attempt == 2:
                    raise ExportError("Codeup 连接失败或超时，请检查网络和代理") from None
            except UnicodeError:
                raise ExportError("接口返回了非 UTF-8 数据") from None
            time.sleep(2 ** attempt)


def namespace_from_page(page):
    # Read only the organization namespace field from the page's startup config.
    # Never execute the JavaScript or persist the HTML (which contains CSRF data).
    match = re.search(r"\borganization\s*:\s*\{([^{}]+)\}", page)
    namespace = re.search(r"\bnamespace_id\s*:\s*['\"](\d+)['\"]", match[1]) if match else None
    if not namespace:
        raise ExportError("未找到当前组织 namespace_id，可能需要重新登录或页面结构已变化")
    return namespace[1]


def validate_group(row):
    if not isinstance(row, dict) or row.get("id") is None:
        raise ExportError("代码组记录缺少 id")
    for field in ("name", "path", "path_with_namespace", "web_url"):
        if not isinstance(row.get(field), str) or not row[field]:
            raise ExportError(f"代码组记录缺少 {field}")
    count = row.get("project_count")
    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
        raise ExportError("代码库数量 project_count 无效；不会将缺失数量当成 0")
    description = row.get("description")
    if description is not None and not isinstance(description, str):
        raise ExportError("代码组描述不是文本")
    updated = row.get("last_activity_at") or row.get("updated_at")
    if updated:
        try:
            dt = datetime.fromisoformat(updated.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise ExportError("更新日期格式未知或缺少时区，无法可靠转换") from None
    return {"id": str(row["id"]), "group_name": row["name"], "english_name": row["path"],
            "chinese_name": description or "", "updated_at": updated,
            "repository_count": count, "full_path": row["path_with_namespace"], "url": row["web_url"],
            "subgroup_count": row.get("group_count"),
            "date_source_field": "last_activity_at" if row.get("last_activity_at") else "updated_at"}


def fetch_groups(client, page_size=100):
    namespace = namespace_from_page(client.read("/groups", {"navKey": "all"}))
    result, seen = [], set()
    for page in range(1, 10001):
        body = client.read("/api/v4/groups/all", {"page": page, "per_page": page_size,
            "order_by": "updated_at", "parent_id": namespace, "search": "", "sort": ""})
        try:
            rows = json.loads(body)
        except ValueError:
            raise AuthenticationError("列表未返回 JSON，可能登录失效或遇到安全验证") from None
        if not isinstance(rows, list):
            raise ExportError("代码组接口未返回列表，请检查账号权限或接口变化")
        # No total header was provided by this endpoint. Request until an empty page,
        # even after a short page, to accommodate a server-side page-size cap.
        if not rows:
            return namespace, result
        for item in rows:
            group = validate_group(item)
            if group["id"] in seen:
                raise ExportError("分页出现重复代码组，列表可能已变化，停止以免导出不完整")
            seen.add(group["id"])
            result.append(group)
        print(f"代码组：第 {page} 页，累计 {len(result)} 条", flush=True)
    raise ExportError("分页超过安全上限，未生成完整报表")


def main():
    parser = argparse.ArgumentParser(description="导出 Codeup 全部代码组为 Excel，默认使用 codeup-cookie.txt")
    parser.add_argument("--cookie-file", type=Path, default=ROOT / "codeup-cookie.txt")
    parser.add_argument("--output", type=Path, help="新的输出目录，禁止覆盖已有目录")
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--delay", type=float, default=0.2)
    parser.add_argument("--node", default=os.environ.get("CODEUP_NODE", NODE), help="运行 Excel 构建器的 Node 可执行文件")
    parser.add_argument("--json-only", action="store_true", help="只读取并保存 JSON，供离线重新生成 Excel")
    args = parser.parse_args()
    if args.page_size < 1 or args.timeout <= 0 or args.delay < 0:
        parser.error("page-size 和 timeout 必须为正数，delay 不能为负数")
    try:
        if not args.json_only and not Path(args.node).is_file():
            raise ExportError("Node 运行时不存在，请用 --node 指定已配置 artifact-tool 的 Node 环境")
        namespace, groups = fetch_groups(GroupsClient(read_cookie(args.cookie_file), args.timeout, args.delay), args.page_size)
        output = args.output or ROOT / "outputs" / "01a0610e-07ba-7da0-97e9-8fcf30d12c6b" / ("codeup-groups-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
        output.mkdir(parents=True, exist_ok=False)
        payload = {"source_url": PAGE, "namespace_id": namespace, "exported_at": datetime.now().astimezone().isoformat(),
                   "group_count": len(groups), "scope": "全部代码组页面的分页列表，不递归展开子组", "groups": groups}
        data_path = output / "codeup_groups.json"
        data_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        if not args.json_only:
            if not BUILDER.is_file():
                raise ExportError(f"未找到 Excel 生成器 {BUILDER}；完整 JSON 已保留在 {data_path}，可用 --json-only 跳过 Excel")
            process = subprocess.run([args.node, str(BUILDER), str(data_path.resolve()), str(output.resolve())], check=False)
            if process.returncode:
                raise ExportError(f"Excel 生成失败，完整 JSON 已保留在 {data_path}")
        print(f"导出完成：{len(groups)} 个代码组，目录 {output.resolve()}")
        return 0
    except (ExportError, OSError) as exc:
        print(f"导出未完成：{exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("导出已中断。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
