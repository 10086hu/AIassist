from __future__ import annotations

import sys
import unittest
from io import BytesIO
from pathlib import Path

from openpyxl import Workbook
from sqlalchemy import create_engine
from sqlalchemy.orm import Session


BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.db.models import Base, PriceBenchmark  # noqa: E402
from app.api.evaluate import _attach_function_rule_results, _findings_count  # noqa: E402
from app.modules.maintenance.service import evaluate_maintenance_rules  # noqa: E402
from app.modules.resource.maintenance_rules import is_maintenance_project_document  # noqa: E402
from app.modules.resource.service import run_resource_check_from_file  # noqa: E402
from app.services.check_service import list_check_rules  # noqa: E402


CASE_ROOT = Path(r"D:\桌面\文件")


class RuleGroupConfigurationTests(unittest.TestCase):
    def test_data_reasonableness_exposes_rule_24(self) -> None:
        payload = list_check_rules("data_reasonableness")
        rule_ids = {str(rule["rule_id"]) for rule in payload["rules"]}

        self.assertIn("DATA_REASON_024", rule_ids)

    def test_resource_rule_library_uses_grouped_frontend_rules(self) -> None:
        payload = list_check_rules("resource")
        rule_ids = [str(rule["rule_id"]) for rule in payload["rules"]]

        self.assertIn("R15_SECURITY_PAAS_CRYPTO_QUANTITY", rule_ids)
        self.assertIn("RESOURCE_REASON_002", rule_ids)
        self.assertIn("MAINT_OPS_ALL", rule_ids)
        self.assertNotIn("R16_SERVER_OS_QUANTITY", rule_ids)
        self.assertNotIn("R16_DB_SERVER_DATABASE_QUANTITY", rule_ids)
        self.assertFalse(any(rule_id.startswith("MAINT_OPS_") and rule_id != "MAINT_OPS_ALL" for rule_id in rule_ids))

    def test_func_corr_004_rule_result_uses_function_correspondence_findings(self) -> None:
        normalized = {
            "findings": [
                {
                    "rule_id": "FUNC_CORR_004",
                    "rule_name": "建设内容一致性校验规则",
                    "risk_level": "中",
                    "review_opinion": "feature correspondence issue",
                    "revision_advice": "add mapping",
                }
            ],
            "rules_used": [
                {
                    "rule_id": "FUNC_CORR_004",
                    "rule_name": "建设内容一致性校验规则",
                    "rule_category": "一致性校验规则",
                    "rule_detail": "需求分析、建设内容、功能点设计、投资概算应能关联对应",
                    "source": "function_correspondence",
                }
            ],
            "summary": {},
        }

        result = _attach_function_rule_results(normalized)

        self.assertEqual(_findings_count(result), 1)
        self.assertEqual(len(result["rule_results"]), 1)
        self.assertEqual(result["rule_results"][0]["rule_id"], "FUNC_CORR_004")
        self.assertEqual(result["rule_results"][0]["source"], "function_correspondence")
        self.assertEqual(result["rule_results"][0]["finding_count"], 1)

    def test_maintenance_group_id_runs_all_twenty_rules(self) -> None:
        content = (
            "section,item,amount\n"
            "operation plan,total amount,10\n"
            "self check,total amount,10\n"
            "system declaration,total amount,10\n"
        ).encode("utf-8")

        findings = evaluate_maintenance_rules(content, "maintenance.csv", selected_rule_ids=["MAINT_OPS_ALL"])

        self.assertEqual(len(findings), 20)
        self.assertEqual({finding.rule_code for finding in findings}, {f"MAINT_OPS_{index:03d}" for index in range(1, 21)})

    def test_construction_feasibility_doc_with_operations_mentions_is_not_maintenance(self) -> None:
        content = (
            "章节,内容\n"
            "项目名称,上海海关智关航贸建设项目可行性研究\n"
            "应用软件开发投资估算明细表,系统支持统一运维管理、运行维护状态设置和运维便捷性。\n"
            "安全建设内容,包含等保测评、满意度统计和申报金额说明。\n"
        ).encode("utf-8")

        self.assertFalse(
            is_maintenance_project_document(
                content,
                "20260128上海海关智关航贸建设项目可行性研究.csv",
            )
        )

    def test_generic_operations_mentions_without_project_signal_are_not_maintenance(self) -> None:
        content = (
            "章节,内容\n"
            "功能说明,支持通过解决方案、业务流程和工作流名称筛选查询，实现统一运维管理。\n"
            "技术说明,查看服务运维信息、操作日志及软硬件运行状态，提升运维便捷性。\n"
            "投资估算,应用软件开发投资估算明细表。\n"
        ).encode("utf-8")

        self.assertFalse(is_maintenance_project_document(content, "建设说明.csv"))

    def test_explicit_maintenance_material_still_runs_gate(self) -> None:
        content = (
            "章节,内容,金额\n"
            "运维项目,运维方案申报金额,10万元\n"
            "运维台账,去年核定价格,9万元\n"
        ).encode("utf-8")

        self.assertTrue(is_maintenance_project_document(content, "运维方案.csv"))

    def test_kajia_table_with_maintenance_columns_still_runs_gate(self) -> None:
        content = (
            "序号,产品名称,购置金额,运维金额,核定运维金额\n"
            "1,应用软件模块,100万元,4万元,4万元\n"
        ).encode("utf-8")

        self.assertTrue(is_maintenance_project_document(content, "核价清单.csv"))

    def test_selected_maintenance_group_runs_only_when_document_is_maintenance(self) -> None:
        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Base.metadata.create_all(engine)
        db = Session(engine)
        try:
            content = "名称,数量\n普通服务器,1\n".encode("utf-8")

            result = run_resource_check_from_file(
                db,
                content,
                "resource.csv",
                project_id=None,
                project_name="非运维项目",
                department=None,
                selected_rule_ids=["MAINT_OPS_ALL"],
            )

            self.assertEqual(result.checked_rule_count, 0)
            self.assertFalse(any(item.rule_code.startswith("MAINT_OPS_") for item in result.findings))
        finally:
            db.close()
            engine.dispose()

    def test_selected_rule_group_row_16_runs_for_non_maintenance_project(self) -> None:
        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Base.metadata.create_all(engine)
        db = Session(engine)
        try:
            content = (
                "资源名称,数量\n"
                "云服务器,2\n"
                "服务器操作系统,3\n"
                "数据库服务器,1\n"
                "数据库软件,1\n"
            ).encode("utf-8")

            result = run_resource_check_from_file(
                db,
                content,
                "resource.csv",
                project_id=None,
                project_name="非运维项目",
                department=None,
                selected_rule_ids=["16"],
            )

            self.assertEqual(result.checked_rule_count, 2)
            self.assertTrue(all(item.rule_name == "三大件数量一致性校验规则" for item in result.findings))
            self.assertTrue(all(item.result_label == "通过" for item in result.findings))
            self.assertFalse(any(item.rule_code.startswith("MAINT_OPS_") for item in result.findings))
        finally:
            db.close()
            engine.dispose()

    def test_rule_16_treats_server_row_os_version_as_os_coverage(self) -> None:
        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Base.metadata.create_all(engine)
        db = Session(engine)
        try:
            content = (
                "资源名称,配置,数量\n"
                "计算服务器,kylin-v10-sp3,5\n"
            ).encode("utf-8")

            result = run_resource_check_from_file(
                db,
                content,
                "resource.csv",
                project_id=None,
                project_name="建设项目",
                department=None,
                selected_rule_ids=["16"],
            )

            os_finding = next(item for item in result.findings if item.rule_code == "R16_SERVER_OS_QUANTITY")
            self.assertEqual(os_finding.result_label, "通过")
            self.assertEqual(os_finding.source_quantities["服务器数量"], 5.0)
            self.assertEqual(os_finding.source_quantities["操作系统数量"], 5.0)
        finally:
            db.close()
            engine.dispose()

    def test_rule_16_treats_plain_database_row_with_product_context_as_database_software(self) -> None:
        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Base.metadata.create_all(engine)
        db = Session(engine)
        try:
            content = (
                "资源名称,配置,数量\n"
                "数据库服务器,物理机,10\n"
                "数据库,国产数据库 KingbaseES 永久授权,10\n"
            ).encode("utf-8")

            result = run_resource_check_from_file(
                db,
                content,
                "resource.csv",
                project_id=None,
                project_name="建设项目",
                department=None,
                selected_rule_ids=["16"],
            )

            db_finding = next(item for item in result.findings if item.rule_code == "R16_DB_SERVER_DATABASE_QUANTITY")
            self.assertEqual(db_finding.result_label, "通过")
            self.assertEqual(db_finding.source_quantities["数据库服务器数量"], 10.0)
            self.assertEqual(db_finding.source_quantities["数据库数量"], 10.0)
        finally:
            db.close()
            engine.dispose()

    def test_rule_16_excludes_device_type_servers_without_resource_context(self) -> None:
        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Base.metadata.create_all(engine)
        db = Session(engine)
        try:
            content = (
                "产品名称,规格,数量\n"
                "视频图像质量诊断服务器,1U专用视频诊断设备,4\n"
                "人脸抓拍智能服务器,视频监控专用设备,1\n"
            ).encode("utf-8")

            result = run_resource_check_from_file(
                db,
                content,
                "device_budget.csv",
                project_id=None,
                project_name="安防设备项目",
                department=None,
                selected_rule_ids=["16"],
            )

            os_finding = next(item for item in result.findings if item.rule_code == "R16_SERVER_OS_QUANTITY")
            self.assertEqual(os_finding.result_label, "不适用")
            self.assertEqual(os_finding.source_quantities["服务器数量"], 0.0)
            self.assertEqual(os_finding.source_quantities["操作系统数量"], 0.0)
        finally:
            db.close()
            engine.dispose()

    def test_unselected_maintenance_group_does_not_auto_run_for_maintenance_document(self) -> None:
        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Base.metadata.create_all(engine)
        db = Session(engine)
        try:
            content = (
                "章节,内容,金额\n"
                "运维项目,运维方案申报金额,10万元\n"
                "运维台账,去年核定价格,9万元\n"
            ).encode("utf-8")

            result = run_resource_check_from_file(
                db,
                content,
                "运维方案.csv",
                project_id=None,
                project_name="运维项目",
                department=None,
                selected_rule_ids=[],
            )

            self.assertFalse(any(item.rule_code.startswith("MAINT_OPS_") for item in result.findings))
        finally:
            db.close()
            engine.dispose()

    def test_selected_maintenance_group_runs_when_document_is_maintenance(self) -> None:
        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Base.metadata.create_all(engine)
        db = Session(engine)
        try:
            content = (
                "章节,内容,金额\n"
                "运维项目,运维方案申报金额,10万元\n"
                "运维台账,去年核定价格,9万元\n"
            ).encode("utf-8")

            result = run_resource_check_from_file(
                db,
                content,
                "运维方案.csv",
                project_id=None,
                project_name="运维项目",
                department=None,
                selected_rule_ids=["MAINT_OPS_ALL"],
            )

            self.assertEqual(result.checked_rule_count, 20)
            self.assertEqual({item.rule_code for item in result.findings}, {f"MAINT_OPS_{index:03d}" for index in range(1, 21)})
        finally:
            db.close()
            engine.dispose()

    def test_maintenance_price_rule_requires_kajia_table_for_labeled_amounts(self) -> None:
        content = "应用软件运维 申报金额 7 万元 购置金额 100 万元 新增".encode("utf-8")

        findings = evaluate_maintenance_rules(content, "maintenance.csv", selected_rule_ids=["MAINT_OPS_014"])

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].result_label, "资料不足")
        self.assertIn("核价清单", findings[0].reason)

    def test_maintenance_security_fee_rule_uses_labeled_system_amount(self) -> None:
        content = "等保三级 测评费 10 万元 系统金额 100 万元".encode("utf-8")

        findings = evaluate_maintenance_rules(content, "maintenance.csv", selected_rule_ids=["MAINT_OPS_017"])

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].source_quantities["系统金额"], 100.0)
        self.assertEqual(findings[0].source_quantities["测评费"], 10.0)
        self.assertEqual(findings[0].source_quantities["规则核定金额"], 9.0)

    def test_maintenance_rule_20_reuses_price_reference_library(self) -> None:
        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Base.metadata.create_all(engine)
        db = Session(engine)
        try:
            db.add(
                PriceBenchmark(
                    source="上海市产品库",
                    item_name_std="应用服务器",
                    brand="厂商A",
                    model="S1",
                    unit_price=10_000,
                    raw_data='{"spec":"32核"}',
                )
            )
            content = (
                "产品名称,品牌,型号,类别,规格,单位,数量,单价,总金额,备注\n"
                "应用服务器,厂商A,S1,新增硬件产品,32核,台,1,13000,13000,新增软硬件产品\n"
            ).encode("utf-8")

            findings = evaluate_maintenance_rules(
                content,
                "maintenance.csv",
                selected_rule_ids=["MAINT_OPS_020"],
                db=db,
            )

            self.assertEqual(len(findings), 1)
            self.assertEqual(findings[0].rule_code, "MAINT_OPS_020")
            self.assertEqual(findings[0].result_label, "价格参考库比对异常")
            self.assertEqual(findings[0].severity, "高")
            self.assertEqual(findings[0].source_quantities["价格库匹配数"], 1.0)
        finally:
            db.close()
            engine.dispose()

    def test_maintenance_negative_list_catches_non_new_interface_and_hardware_exclusions(self) -> None:
        software_content = "历史运维项目包含视频汇聚-国标平台对接、接口运维和数据迁移，未说明新增项。".encode("utf-8")
        hardware_content = "硬件运维清单包含综合布线、网线、办公电脑、打印机、辅材和账号租赁。".encode("utf-8")

        software_findings = evaluate_maintenance_rules(software_content, "运维方案.txt.csv", selected_rule_ids=["MAINT_OPS_010"])
        hardware_findings = evaluate_maintenance_rules(hardware_content, "运维方案.txt.csv", selected_rule_ids=["MAINT_OPS_011"])

        self.assertEqual(software_findings[0].result_label, "应核定为0")
        self.assertIn("接口", software_findings[0].reason)
        self.assertEqual(hardware_findings[0].result_label, "应核定为0")
        self.assertGreaterEqual(hardware_findings[0].source_quantities["命中申报项数"], 4.0)

    def test_maintenance_rate_rule_uses_kajia_table_and_ten_percent_cap(self) -> None:
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "核价清单"
        sheet.append(["序号", "产品名称", "配置", "购置数量", "购置单价", "购置金额", "运维数量", "运维单价", "运维金额", "核定运维数量", "核定运维单价", "核定运维金额"])
        sheet.append([1, "应用软件开发", "历史系统维护", 1, 100000, 100000, 1, 12000, 12000, "", "", ""])
        output = BytesIO()
        workbook.save(output)

        findings = evaluate_maintenance_rules(output.getvalue(), "运维核价清单.xlsx", selected_rule_ids=["MAINT_OPS_014"])

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].result_label, "核价疑似偏高")
        self.assertEqual(findings[0].source_quantities["申报费率超过10%项数"], 1.0)
        self.assertEqual(findings[0].source_quantities["申报金额高于规则测算项数"], 1.0)

    def test_hardware_rate_rule_ignores_plain_text_without_kajia_table(self) -> None:
        content = "运维方案正文提到硬件资源、服务器、存储巡检和运行维护，但没有核价清单或可核价明细表。".encode("utf-8")

        findings = evaluate_maintenance_rules(content, "硬件运维方案.csv", selected_rule_ids=["MAINT_OPS_016"])

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].rule_code, "MAINT_OPS_016")
        self.assertEqual(findings[0].result_label, "通过")
        self.assertIn("不因正文", findings[0].reason)

    def test_hardware_rate_rule_parses_parenthesized_amount_headers(self) -> None:
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "核价清单"
        sheet.append(["序号", "硬件描述", "配置要求", "购置金额（元）", "申报金额（元）"])
        sheet.append([1, "主机-->PC服务器", "机架式服务器", 100000, 6000])
        output = BytesIO()
        workbook.save(output)

        findings = evaluate_maintenance_rules(output.getvalue(), "硬件运维核价清单.xlsx", selected_rule_ids=["MAINT_OPS_016"])

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].rule_code, "MAINT_OPS_016")
        self.assertEqual(findings[0].result_label, "核价疑似偏高")
        self.assertEqual(findings[0].source_quantities["明细项数"], 1.0)
        self.assertEqual(findings[0].source_quantities["申报金额高于规则测算项数"], 1.0)

    def test_maintenance_history_price_allows_new_item_exception_for_manual_review(self) -> None:
        content = "本年度核定价格 12 万元 去年核定价格 9 万元，含新增功能开发和新增硬件。".encode("utf-8")

        findings = evaluate_maintenance_rules(content, "运维方案.csv", selected_rule_ids=["MAINT_OPS_019"])

        self.assertEqual(findings[0].result_label, "新增项例外需确认")
        self.assertEqual(findings[0].severity, "需人工确认")

    @unittest.skipUnless(CASE_ROOT.exists(), "local maintenance case folder is not available")
    def test_actual_hardware_case_kajia_xlsx_passes_mature_hardware_rule(self) -> None:
        matches = list(CASE_ROOT.rglob("122-松江区进博会安保区及九里亭等4个街（镇）住宅小区智能安防系统（泗泾）(运维).xlsx"))
        self.assertTrue(matches)
        content = matches[0].read_bytes()

        findings = evaluate_maintenance_rules(content, matches[0].name, selected_rule_ids=["MAINT_OPS_016"])

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].rule_code, "MAINT_OPS_016")
        self.assertEqual(findings[0].result_label, "通过")
        self.assertGreater(findings[0].source_quantities["可核价项数"], 0)

    @unittest.skipUnless(CASE_ROOT.exists(), "local maintenance case folder is not available")
    def test_actual_mixed_case_hits_negative_list_from_kajia_xlsx(self) -> None:
        matches = list(CASE_ROOT.rglob("130-智慧健康松江——区域医疗卫生一体化云平台(变更)(运维).xlsx"))
        self.assertTrue(matches)
        content = matches[0].read_bytes()

        findings = evaluate_maintenance_rules(content, matches[0].name, selected_rule_ids=["MAINT_OPS_011"])

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].result_label, "应核定为0")
        self.assertGreater(findings[0].source_quantities["命中申报项数"], 0)


if __name__ == "__main__":
    unittest.main()
