#!/usr/bin/env python3
"""Export application summaries and every step shown by the runtime detail page."""
import argparse
import hashlib
import html
import json
import re
import sys
import time
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request

from export_devops import Client, ExportError, AuthenticationError, fetch_apps

ENVS = {"TEST": "测试环境", "SIMULATION": "仿真环境", "PRODUCT": "线上环境"}


def now():
    return datetime.now().astimezone().isoformat()


def save_json(path, value):
    temp = path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def safe_name(value, fallback="unknown"):
    raw = str(value) if value not in (None, "") else fallback
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", raw).strip(" .") or fallback
    if len(name.encode("utf-8")) > 110:
        name = name.encode("utf-8")[:90].decode("utf-8", errors="ignore") + "-" + hashlib.sha256(raw.encode()).hexdigest()[:12]
    return name


class StepsParser(HTMLParser):
    """Read table cells and buildResourceClass identifiers, never execute page JS."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.steps, self.cells, self.ids = [], [], []
        self.cell, self.in_row, self.skip = None, False, 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ("script", "style"):
            self.skip += 1
        if tag == "tr":
            self.in_row, self.cells, self.ids = True, [], []
        if tag == "td" and self.in_row:
            self.cell = []
        if "buildResourceClass" in attrs.get("class", "").split():
            identifier = attrs.get("id", "")
            if not re.fullmatch(r"\d+_\d+_\d+", identifier):
                raise ExportError("运行详情中的步骤 ID 格式变化")
            self.ids.append(identifier.split("_"))

    def handle_data(self, data):
        if self.cell is not None and not self.skip:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip = max(0, self.skip - 1)
        if tag == "td" and self.cell is not None:
            self.cells.append("".join(self.cell).strip())
            self.cell = None
        if tag == "tr" and self.in_row:
            if self.ids and (len(self.cells) < 3 or not self.cells[1]):
                raise ExportError("无法识别运行详情表格的操作名")
            for build, resource, action in self.ids:
                self.steps.append({"build_id": build, "resource_id": resource, "action_id": action,
                                   "operation": self.cells[1], "resource_name": self.cells[0], "ip": self.cells[2]})
            self.in_row = False


def parse_steps(page):
    parser = StepsParser()
    parser.feed(page)
    found, result = set(), []
    for step in parser.steps:
        key = (step["build_id"], step["resource_id"], step["action_id"])
        if key not in found:
            result.append(step)
            found.add(key)
    if not result:
        raise ExportError("运行详情未识别到操作步骤，可能暂无运行记录或页面结构已变化")
    return result


def log_text(raw):
    # The observed endpoint returns text/plain containing <br/> and <b> markup.
    # Decode entities LAST to preserve escaped XML/shell angle brackets in logs.
    text = re.sub(r"<br\s*/?>", "\n", raw, flags=re.I)
    text = re.sub(r"</?b(?:\s[^>]*)?>", "", text, flags=re.I)
    return html.unescape(text)


class RuntimeClient(Client):
    def read(self, path, params=None, json_response=False):
        allowed = (path == "/theone-web/ops/app/deploy/searchPlandetail" or
                   re.fullmatch(r"/theone-web/build/\d+/\d+/\d+/(actionRun|getLogContentFromFile)", path))
        if not allowed:
            raise ExportError("拒绝请求非日志查询接口")
        url = self.base_url + path + ("?" + urlencode(params) if params else "")
        for attempt in range(self.retries + 1):
            time.sleep(self.delay)
            try:
                with self.opener.open(Request(url, headers={"Cookie": self.cookie,
                        "Accept": "application/json" if json_response else "*/*",
                        "X-Requested-With": "XMLHttpRequest"}), timeout=self.timeout) as response:
                    body = response.read().decode("utf-8")
            except HTTPError as exc:
                if exc.code in (301, 302, 303, 307, 308, 401, 403):
                    raise AuthenticationError("登录失效、无权限或发生重定向，请更新 DevOps Cookie") from None
                if exc.code not in (429, 500, 502, 503, 504) or attempt == self.retries:
                    raise ExportError(f"运行详情接口 HTTP {exc.code}") from None
            except (URLError, TimeoutError, OSError):
                if attempt == self.retries:
                    raise ExportError("运行详情接口连接失败或超时") from None
            except UnicodeError:
                raise ExportError("日志不是有效 UTF-8，停止该步骤以避免损坏内容") from None
            else:
                if json_response:
                    try:
                        data = json.loads(body)
                    except ValueError:
                        raise ExportError("步骤状态未返回 JSON") from None
                    if not isinstance(data, dict) or not isinstance(data.get("status"), str):
                        raise ExportError("步骤状态响应缺少 status")
                    return data
                if path.endswith("getLogContentFromFile") and re.match(r"\s*(?:<!doctype|<html)", body, re.I):
                    raise ExportError("日志接口返回完整 HTML 页面，未将其误存为日志")
                return body
            time.sleep(2 ** attempt)


def export_step(client, step, folder):
    prefix = "/theone-web/build/{build_id}/{resource_id}/{action_id}".format(**step)
    before = client.read(prefix + "/actionRun", json_response=True)
    raw = client.read(prefix + "/getLogContentFromFile")
    after = client.read(prefix + "/actionRun", json_response=True)
    stable_keys = ("startTime", "endTime", "status", "toActionRunId")
    changed = any(before.get(k) != after.get(k) for k in stable_keys)
    step_dir = folder / ("build-{build_id}_resource-{resource_id}_action-{action_id}".format(**step))
    step_dir.mkdir(parents=True, exist_ok=False)
    filename = "{}_{}_{}_log.txt".format(safe_name(step["operation"]),
        safe_name(before.get("startTime"), "未开始"), safe_name(before["status"]))
    (step_dir / filename).write_text(log_text(raw), encoding="utf-8")
    # Retain the exact endpoint body as a .txt sidecar, never a runnable HTML file.
    (step_dir / "source_response.txt").write_text(raw, encoding="utf-8")
    result = dict(step, start_time=before.get("startTime"), end_time=before.get("endTime"),
                  status=before["status"], state_before=before, state_after=after,
                  changed_during_export=changed, snapshot_only=changed or before["status"] in ("INIT", "RUNNING"),
                  log_file=str((step_dir / filename).relative_to(folder)),
                  log_bytes=len(log_text(raw).encode("utf-8")), captured_at=now())
    save_json(step_dir / "step.json", result)
    return result


def export_application(client, app, folder):
    folder.mkdir(parents=True, exist_ok=False)
    summary = {"url": f"{client.base_url}/#/project/application/info?id={app['id']}",
               "app_id": app["id"], "app_name": app.get("appName"),
               "repository_type": app.get("defaultContrVersionType"),
               "repository_url": app.get("defaultContrVersionUrlMaster") or app.get("defaultContrVersionUrl"),
               "jdk_version": app.get("jdkVersion"), "environments": {},
               "export_started_at": now(), "export_status": "in_progress",
               "log_scope": "各部署实例的运行详情页面当前返回的全部步骤；不是历史构建列表"}
    save_json(folder / "summary.json", summary)
    errors = []
    try:
        # Save ALL server IPs before downloading the first log.
        for env, label in ENVS.items():
            env_dir = folder / label / "logs"
            env_dir.mkdir(parents=True)
            info = {"name": label, "server_ips": [], "deployments": [], "status": "pending"}
            summary["environments"][env] = info
            try:
                obj = client.get("/theone-web/ops/app/list/app", {"appId": app["id"], "envtype": env})
                if obj is None:
                    info["status"] = "no_environment"
                    continue
                if not isinstance(obj, dict) or not isinstance(obj.get("deployList"), list):
                    raise ExportError("环境响应缺少 deployList")
                for deploy in obj["deployList"]:
                    if not isinstance(deploy, dict):
                        raise ExportError("部署实例格式错误")
                    info["deployments"].append({"deploy_id": deploy.get("deployId"), "scene_id": deploy.get("sceneId"),
                                                "ip": deploy.get("ip"), "log_status": "pending"})
                info["server_ips"] = list(dict.fromkeys(d["ip"] for d in info["deployments"] if d["ip"]))
                info["status"] = "ready" if info["deployments"] else "no_deployments"
            except ExportError as exc:
                info["status"], info["error"] = "failed", str(exc)
                errors.append({"environment": env, "error": str(exc)})
                if isinstance(exc, AuthenticationError):
                    raise
            finally:
                save_json(folder / "summary.json", summary)
        for env, info in summary["environments"].items():
            log_root = folder / info["name"] / "logs"
            for deployment_index, deploy in enumerate(info["deployments"]):
                manifest = {"deployment": dict(deploy), "steps": [], "errors": []}
                deploy_dir = log_root / (safe_name(deploy["ip"], "无IP") + "_deploy-" + safe_name(deploy["deploy_id"]))
                if deploy_dir.exists():
                    deploy_dir = log_root / (deploy_dir.name + f"_record-{deployment_index}")
                deploy_dir.mkdir()
                try:
                    if not deploy["scene_id"]:
                        deploy["log_status"] = "no_runtime_button"
                        continue
                    if not str(deploy["deploy_id"]).isdigit() or not str(deploy["scene_id"]).isdigit():
                        raise ExportError("运行详情缺少有效 deployId/sceneId")
                    params = {"sceneId": deploy["scene_id"], "objectId": deploy["deploy_id"], "objectType": "deploy_type"}
                    page = client.read("/theone-web/ops/app/deploy/searchPlandetail", params)
                    manifest["runtime_url"] = client.base_url + "/theone-web/ops/app/deploy/searchPlandetail?" + urlencode(params)
                    for step in parse_steps(page):
                        try:
                            manifest["steps"].append(export_step(client, step, deploy_dir))
                        except ExportError as exc:
                            manifest["errors"].append(dict(step, error=str(exc)))
                            if isinstance(exc, AuthenticationError):
                                raise
                        finally:
                            save_json(deploy_dir / "index.json", manifest)
                    deploy["log_status"] = "partial" if manifest["errors"] else "exported"
                    deploy["step_count"] = len(manifest["steps"])
                except ExportError as exc:
                    deploy["log_status"] = "failed"
                    manifest["errors"].append({"error": str(exc)})
                    if isinstance(exc, AuthenticationError):
                        raise
                finally:
                    manifest["deployment"] = dict(deploy)
                    save_json(deploy_dir / "index.json", manifest)
                    errors.extend(dict(e, environment=env, deploy_id=deploy["deploy_id"]) for e in manifest["errors"])
                    save_json(folder / "summary.json", summary)
        summary["export_status"] = "partial" if errors else "complete"
    finally:
        if summary["export_status"] == "in_progress":
            summary["export_status"] = "interrupted"
        summary["export_finished_at"] = now()
        summary["errors"] = errors
        save_json(folder / "summary.json", summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://devops.folidaymall.com")
    parser.add_argument("--cookie-file", type=Path, default=Path("cookie.txt"))
    parser.add_argument("--app-id", type=int, help="只导出指定应用")
    parser.add_argument("--app-name", action="append", help="精确应用名；可重复指定，省略则全部应用")
    parser.add_argument("--output", type=Path, help="新的输出目录")
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--delay", type=float, default=0.15)
    args = parser.parse_args()
    parts = urlsplit(args.base_url)
    if parts.scheme not in ("http", "https") or not parts.netloc or parts.username or parts.password or parts.path not in ("", "/") or parts.query or parts.fragment:
        parser.error("base-url 必须是平台源地址")
    if args.timeout <= 0 or args.delay < 0:
        parser.error("timeout 必须为正数，delay 不能为负数")
    try:
        cookie = args.cookie_file.read_text(encoding="utf-8-sig").strip()
        if cookie.lower().startswith("cookie:"):
            cookie = cookie[7:].strip()
        if not cookie or "\n" in cookie or "\r" in cookie:
            raise ExportError("Cookie 文件需包含单行 Cookie 请求头值")
        client = RuntimeClient(args.base_url, cookie, args.timeout, args.delay)
        apps = fetch_apps(client, app_id=args.app_id)
        if args.app_name:
            missing = set(args.app_name) - {a.get("appName") for a in apps}
            if missing:
                raise ExportError("未找到应用名：" + ", ".join(sorted(missing)))
            apps = [a for a in apps if a.get("appName") in args.app_name]
        if not apps:
            raise ExportError("没有找到匹配应用")
        root = args.output or Path("exports") / ("devops-details-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
        root.mkdir(parents=True, exist_ok=False)
        report, used = {"started_at": now(), "status": "in_progress", "applications": []}, set()
        try:
            for index, app in enumerate(apps, 1):
                directory = safe_name(app.get("appName"), f"app-{app['id']}")
                if directory.casefold() in used:
                    directory += "__app-" + str(app["id"])
                while directory.casefold() in used:
                    directory += "_"
                used.add(directory.casefold())
                entry = {"app_id": app["id"], "app_name": app.get("appName"), "directory": directory, "status": "in_progress"}
                report["applications"].append(entry)
                save_json(root / "export_report.json", report)
                print(f"[{index}/{len(apps)}] {app.get('appName')}：导出运行详情", flush=True)
                try:
                    result = export_application(client, app, root / directory)
                    entry["status"] = result["export_status"]
                except AuthenticationError:
                    entry["status"] = "interrupted"
                    raise
                finally:
                    save_json(root / "export_report.json", report)
            report["status"] = "complete" if all(a["status"] == "complete" for a in report["applications"]) else "partial"
        finally:
            if report["status"] == "in_progress":
                report["status"] = "interrupted"
            report["finished_at"] = now()
            save_json(root / "export_report.json", report)
            print(f"输出目录：{root.resolve()}", flush=True)
        return 0 if report["status"] == "complete" else 2
    except (ExportError, OSError) as exc:
        print(f"导出未完成：{exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("已中断，已写入的汇总和日志保留。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
