"""Local HTTP smoke test for the newly wired rule modules.

The test starts the FastAPI app against a temporary SQLite database, then verifies
the public rule APIs and the security/resource/price multipart endpoints. It does
not require an LLM key or any external service.
"""
from __future__ import annotations

import os
import json
import socket
import subprocess
import sys
import tempfile
import time
from io import BytesIO
from pathlib import Path

import requests
from docx import Document


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
PYTHON = BACKEND / ".venv" / "Scripts" / "python.exe"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_health(base_url: str, process: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Uvicorn exited early with code {process.returncode}")
        try:
            if requests.get(f"{base_url}/api/health", timeout=1).status_code == 200:
                return
        except requests.RequestException:
            time.sleep(0.2)
    raise TimeoutError("Timed out waiting for /api/health")


def _assert_status(response: requests.Response, expected: int = 200) -> dict:
    if response.status_code != expected:
        raise AssertionError(f"HTTP {response.status_code}: {response.text}")
    return response.json()


def _post_file(base_url: str, endpoint: str, filename: str, content: bytes | str, data: dict[str, str]) -> dict:
    payload = content.encode("utf-8") if isinstance(content, str) else content
    response = requests.post(
        f"{base_url}{endpoint}",
        files={"file": (filename, payload, "application/octet-stream")},
        data=data,
        timeout=20,
    )
    return _assert_status(response)


def _security_docx() -> bytes:
    document = Document()
    document.add_paragraph("本项目为上海市市级新建项目。")
    document.add_heading("4.7 安全需求分析", level=2)
    document.add_paragraph("系统开展风险评估，按等保三级建设，实行数据分类分级、访问控制、日志审计和SM4密码应用。")
    document.add_heading("6.6 安全建设内容", level=2)
    document.add_paragraph("按等保三级落实安全建设。")
    resource_table = document.add_table(rows=1, cols=4)
    for cell, value in zip(resource_table.rows[0].cells, ("来源", "资源名称", "数量", "单位")):
        cell.text = value
    for values in (
        ("安全服务需求表", "安全防病毒服务", "2", "项"),
        ("PaaS服务清单", "安全防病毒服务", "3", "项"),
        ("计算资源清单", "云服务器", "2", "台"),
        ("PaaS服务清单", "服务器操作系统", "1", "套"),
        ("计算资源清单", "数据库服务器", "1", "台"),
        ("PaaS服务清单", "数据库软件", "2", "套"),
    ):
        cells = resource_table.add_row().cells
        for cell, value in zip(cells, values):
            cell.text = value
    summary = document.add_table(rows=1, cols=3)
    summary.rows[0].cells[0].text = "投资估算总表（单位：万元）"
    for values in (
        ("一、系统建设费", "1000", ""),
        ("1.1应用软件开发", "200", ""),
        ("1.2硬件购置", "300", ""),
        ("1.3产品软件", "100", ""),
        ("1.4安全产品", "100", ""),
        ("二、其他费用", "55", ""),
        ("1", "咨询费", "20"),
        ("2", "监理费", "10"),
        ("3", "软测费", "5"),
        ("4", "安全测评费", "10"),
        ("5", "密测费", "10"),
        ("总计", "1055", ""),
    ):
        cells = summary.add_row().cells
        for cell, value in zip(cells, values):
            cell.text = value
    price_table = document.add_table(rows=1, cols=6)
    for cell, value in zip(price_table.rows[0].cells, ("产品名称", "类别", "数量", "单价", "合价", "人月")):
        cell.text = value
    for values in (
        ("软件开发功能", "软件开发", "1", "30000", "30000", "6"),
        ("应用服务器", "服务器", "2", "15000", "30000", ""),
    ):
        cells = price_table.add_row().cells
        for cell, value in zip(cells, values):
            cell.text = value
    output = BytesIO()
    document.save(output)
    return output.getvalue()


def main() -> int:
    if not PYTHON.exists():
        raise FileNotFoundError(f"Python environment not found: {PYTHON}")

    with tempfile.TemporaryDirectory(prefix="aiassist-rule-api-", ignore_cleanup_errors=True) as temp_dir:
        port = _free_port()
        base_url = f"http://127.0.0.1:{port}"
        env = os.environ.copy()
        env["AIASSIST_DATA_DIR"] = temp_dir
        env.pop("DEEPSEEK_API_KEY", None)
        process = subprocess.Popen(
            [str(PYTHON), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
            cwd=BACKEND,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            _wait_for_health(base_url, process)

            for module, expected_rule_ids in {
                "security": {"SECURITY_REASON_001"},
                "price": {"PRICE_REASON_001"},
                "price_reference": {"PRICE_REF_001", "PRICE_REF_002"},
            }.items():
                payload = _assert_status(requests.get(f"{base_url}/api/evaluate/rules", params={"module": module}, timeout=10))
                assert {item["rule_id"] for item in payload["rules"]} == expected_rule_ids, payload

            security = _post_file(
                base_url,
                "/api/evaluate/security/document",
                "security.docx",
                _security_docx(),
                {"project_name": "安全样例", "use_llm": "false"},
            )
            assert security["module_code"] == "security" and security["status"] == "通过", security

            resource = _post_file(
                base_url,
                "/api/evaluate/resource",
                "resource.csv",
                "来源,资源名称,数量,单位\n安全服务需求表,安全防病毒服务,2,项\nPaaS服务清单,安全防病毒服务,3,项\n计算资源清单,云服务器,2,台\nPaaS服务清单,服务器操作系统,1,套\n计算资源清单,数据库服务器,1,台\nPaaS服务清单,数据库软件,2,套\n",
                {"project_name": "资源样例"},
            )
            assert len(resource["findings"]) >= 3 and resource["checked_rule_count"] >= 3, resource
            assert {item["rule_code"] for item in resource["findings"]} >= {"R15_SECURITY_PAAS_CRYPTO_QUANTITY", "R16_SERVER_OS_QUANTITY", "R16_DB_SERVER_DATABASE_QUANTITY"}, resource

            imported = _assert_status(requests.post(
                f"{base_url}/api/evaluate/price-benchmarks/import",
                json=[{"item_name": "应用服务器", "brand": "示例厂商", "model": "S1", "unit_price": 10000, "source": "测试价库"}],
                timeout=10,
            ))
            assert imported["imported"] == 1, imported

            imported_file = _post_file(
                base_url,
                "/api/evaluate/price-benchmarks/import-file",
                "benchmarks.csv",
                "产品名称,品牌,型号,规格,单价\n数据库,示例厂商,DB1,国产数据库,70000\n",
                {},
            )
            assert imported_file["parsed"] == 1 and imported_file["imported"] == 1, imported_file

            price_csv = "产品名称,类别,数量,单价,合价,人月\n软件开发功能,软件开发,1,30000,30000,6\n应用服务器,服务器,2,15000,30000,\n"
            price = _post_file(
                base_url,
                "/api/evaluate/price/document",
                "integrated.docx",
                _security_docx(),
                {"project_name": "价格样例"},
            )
            assert price["module_code"] == "price", price
            assert {item["rule_id"] for item in price["rules_used"]} == {"PRICE_REASON_001"}, price
            assert price["summary"]["standard_id"] == "SHANGHAI_MUNICIPAL_CLASS2_2023", price
            assert price["summary"]["evaluated_fee_count"] == 6, price
            assert price["summary"]["passed_fee_count"] == 6, price

            price_reference = _post_file(
                base_url,
                "/api/evaluate/price-reference/document",
                "price.csv",
                price_csv,
                {"project_name": "价格参考样例"},
            )
            assert price_reference["module_code"] == "price_reference", price_reference
            assert {item["rule_id"] for item in price_reference["rules_used"]} == {"PRICE_REF_001", "PRICE_REF_002"}, price_reference

            task_response = _assert_status(requests.post(
                f"{base_url}/api/evaluate/tasks",
                files={"file": ("integrated.docx", _security_docx(), "application/octet-stream")},
                data={
                    "project_name": "统一任务样例",
                    "modules": "security,resource,price,price_reference",
                    "rule_source": "api",
                    "use_llm": "false",
                    "selected_rule_ids": json.dumps({
                        "security": ["SECURITY_REASON_001"],
                        "resource": ["R15_SECURITY_PAAS_CRYPTO_QUANTITY", "R16_SERVER_OS_QUANTITY", "R16_DB_SERVER_DATABASE_QUANTITY"],
                        "price": ["PRICE_REASON_001"],
                        "price_reference": ["PRICE_REF_001", "PRICE_REF_002"],
                    }),
                },
                timeout=20,
            ))
            task_id = task_response["task_id"]
            task = task_response
            for _ in range(100):
                task = _assert_status(requests.get(f"{base_url}/api/evaluate/tasks/{task_id}", timeout=10))
                if task["status"] not in {"queued", "running"}:
                    break
                time.sleep(0.1)
            assert task["status"] == "completed" and len(task["result_ids"]) == 4, task

            large_file_value = os.getenv("AIASSIST_LARGE_PRICE_TEST_FILE", "").strip()
            if large_file_value:
                large_file = Path(large_file_value)
                if not large_file.exists():
                    raise FileNotFoundError(large_file)
                started = time.monotonic()
                large_task_response = _assert_status(requests.post(
                    f"{base_url}/api/evaluate/tasks",
                    files={"file": (large_file.name, large_file.read_bytes(), "application/octet-stream")},
                    data={
                        "project_name": "大型价格模块回归",
                        "modules": "price,price_reference",
                        "rule_source": "api",
                        "use_llm": "false",
                        "selected_rule_ids": json.dumps({
                            "price": ["PRICE_REASON_001"],
                            "price_reference": ["PRICE_REF_001", "PRICE_REF_002"],
                        }),
                    },
                    timeout=30,
                ))
                large_task_id = large_task_response["task_id"]
                for _ in range(200):
                    assert requests.get(f"{base_url}/api/health", timeout=2).status_code == 200
                    large_task = _assert_status(requests.get(f"{base_url}/api/evaluate/tasks/{large_task_id}", timeout=5))
                    if large_task["status"] not in {"queued", "running"}:
                        break
                    time.sleep(0.1)
                assert large_task["status"] == "completed" and len(large_task["result_ids"]) == 2, large_task
                assert time.monotonic() - started < 20, large_task

            print("PASS: direct APIs and unified background task for security/resource/price modules")
            return 0
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
