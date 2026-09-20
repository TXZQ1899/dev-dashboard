import csv
import io
import json
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError

from export_devops import (AuthenticationError, Client, ExportError, app_row,
                           environment_rows, fetch_apps, run_export)


class FakeClient:
    base_url = "http://devops.example"

    def __init__(self, fail=None):
        self.fail = fail

    def get(self, path, params):
        if path.endswith("/list"):
            page = params["page.pn"]
            return {"totalElements": 2, "content": [
                {"id": page, "appName": f"应用{page}", "defaultContrVersionUrlMaster": "git@example:repo.git"}
            ]}
        if self.fail and params["envtype"] == "SIMULATION":
            raise self.fail("模拟错误")
        return {"deployList": [{"ip": "10.0.0.1", "deployId": 1}, {"ip": "10.0.0.2", "deployId": 2}]}


class ExportTests(unittest.TestCase):
    def test_pagination_and_all_environments(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            run_export(FakeClient(), output, 100, "", None)
            with (output / "applications.csv").open(encoding="utf-8-sig") as f:
                self.assertEqual(len(list(csv.DictReader(f))), 2)
            with (output / "environments.csv").open(encoding="utf-8-sig") as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(len(rows), 12)
            self.assertEqual({r["环境代码"] for r in rows}, {"TEST", "SIMULATION", "PRODUCT"})
            self.assertEqual({r["服务器IP"] for r in rows}, {"10.0.0.1", "10.0.0.2"})
            self.assertTrue(json.loads((output / "summary.json").read_text())["completed"])

    def test_duplicate_page_is_failure(self):
        client = FakeClient()
        client.get = lambda *args: {"totalElements": 2, "content": [{"id": 1}]}
        with self.assertRaises(ExportError):
            fetch_apps(client)

    def test_empty_early_page_is_failure(self):
        client = FakeClient()
        client.get = lambda *args: {"totalElements": 2, "content": []}
        with self.assertRaises(ExportError):
            fetch_apps(client)

    def test_environment_states(self):
        app = app_row({"id": 1}, "http://example")
        self.assertEqual(environment_rows(app, "TEST", None)[0]["状态"], "无环境配置")
        self.assertEqual(environment_rows(app, "TEST", {"deployList": []})[0]["状态"], "无部署实例")
        self.assertEqual(environment_rows(app, "TEST", {"deployList": [{}]})[0]["状态"], "实例未返回IP")
        with self.assertRaises(ExportError):
            environment_rows(app, "TEST", {})

    def test_partial_failures_do_not_claim_completion(self):
        for failure in (ExportError, AuthenticationError):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as folder:
                output = Path(folder)
                with self.assertRaises(failure):
                    run_export(FakeClient(failure), output, 100, "", None)
                self.assertFalse((output / "summary.json").exists())
                self.assertTrue((output / "applications.csv").exists())
                self.assertTrue(json.loads((output / "errors.json").read_text()))

    def test_redirect_and_business_auth_error(self):
        class RedirectOpener:
            def open(self, request, timeout):
                raise HTTPError(request.full_url, 302, "Found", {}, None)
        client = Client("http://example", "test=value", delay=0)
        client.opener = RedirectOpener()
        with self.assertRaises(AuthenticationError):
            client.get("/list", {})
        class JsonOpener:
            def open(self, request, timeout):
                return io.BytesIO(b'{"code":401,"obj":null}')
        client.opener = JsonOpener()
        with self.assertRaises(AuthenticationError):
            client.get("/list", {})


if __name__ == "__main__":
    unittest.main()
