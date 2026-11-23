from io import BytesIO

from docx import Document
from lxml import html

from app.api.evaluate import _attach_finding_locations, _precise_rule_module, _render_rule_evidence_preview
from app.modules.sensitive_word.sensitive_word_checker import SensitiveTerm, extract_docx_text, match_sensitive_terms
from app.modules.sensitive_word.source_locations import attach_sensitive_locations
from app.services.check_service import _merge_sensitive_findings


def check(document, tmp_path, terms=None):
    path = tmp_path / "报告.docx"
    stream = BytesIO()
    document.save(stream)
    path.write_bytes(stream.getvalue())
    findings = [vars(f).copy() for f in match_sensitive_terms(
        extract_docx_text(path), terms or [SensitiveTerm("Oracle")], []
    )]
    attach_sensitive_locations(findings, path.read_bytes(), path.name)
    return findings, stream.getvalue()


def verify(findings, content):
    dom = html.fromstring(_render_rule_evidence_preview(content, "报告.docx"))
    for finding in findings:
        assert finding["source_locations"]
        for source in finding["source_locations"]:
            if source.get("paragraph"):
                nodes = dom.xpath(
                    '//*[@data-e-kind="paragraph" and @data-e-paragraph="%s"]' % source["paragraph"]
                )
            else:
                nodes = dom.xpath(
                    '//*[@data-e-kind="cell" and @data-e-table="%s" and @data-e-path="%s" '
                    'and @data-e-row="%s" and @data-e-column="%s"]'
                    % (source["table_index"], source.get("table_path", ""), source["row_index"],
                       source["column_start"])
                )
            assert len(nodes) == 1
            assert nodes[0].get("data-e-text") == source["quote"]
            assert source["quote"][source["char_start"]:source["char_end"]] == source["hit_text"]


def test_empty_paragraph_table_and_later_body_have_stable_addresses(tmp_path):
    doc = Document()
    doc.add_heading("建设内容", 1)
    doc.add_paragraph("")
    table = doc.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "数据库选型"
    table.cell(0, 1).text = "拟采用Oracle数据库提供数据服务。"
    doc.add_paragraph("后续使用Oracle数据库，需补充选型依据。")
    findings, content = check(doc, tmp_path)
    verify(findings, content)
    assert any(s.get("paragraph") == 3 for f in findings for s in f["source_locations"])
    assert any(s.get("column_start") == 2 for f in findings for s in f["source_locations"])


def test_same_paragraph_repeated_word_preserves_each_occurrence(tmp_path):
    doc = Document()
    doc.add_paragraph("拟采用Oracle，备用系统也采用Oracle。")
    findings, content = check(doc, tmp_path)
    merged = _merge_sensitive_findings(findings)
    verify(merged, content)
    assert len(merged) == 1
    assert [s["char_start"] for s in merged[0]["source_locations"]] == [3, 17]


def test_merged_group_keeps_more_than_three_context_locations(tmp_path):
    doc = Document()
    for i in range(6):
        doc.add_paragraph(f"第{i}组拟采用Oracle数据库，请补充说明。")
    findings, content = check(doc, tmp_path)
    merged = _merge_sensitive_findings(findings)
    verify(merged, content)
    assert len(merged[0]["source_locations"]) == 6
    assert len(merged[0]["context_examples"]) == 3


def test_normalized_space_fullwidth_split_runs_and_nested_table(tmp_path):
    doc = Document()
    paragraph = doc.add_paragraph("🟦选型说明：")
    paragraph.add_run("Ｗｉｎｄｏｗｓ").bold = True
    paragraph.add_run("   Server，拟部署此平台。")
    table = doc.add_table(rows=1, cols=1)
    nested = table.cell(0, 0).add_table(rows=1, cols=1)
    nested.cell(0, 0).text = "拟采用Oracle数据库，提供服务。"
    findings, content = check(
        doc, tmp_path, [SensitiveTerm("Windows Server"), SensitiveTerm("Oracle")]
    )
    verify(findings, content)
    assert any(s.get("table_path") == "1/1/1" for f in findings for s in f["source_locations"])


def test_conflicting_old_coordinates_are_not_reintroduced(tmp_path):
    doc = Document()
    doc.add_paragraph("拟采用Oracle数据库，需补充说明。")
    findings, content = check(doc, tmp_path)
    original = findings[0]["source_locations"].copy()
    result = _attach_finding_locations(
        {"module_code": "sensitive_word", "findings": findings}, content, "报告.docx"
    )
    assert result["findings"][0]["source_locations"] == original
    assert result["findings"][0]["source_location"]["verified_sensitive"]
    assert _precise_rule_module("SENSITIVE_WORD_SOURCE") == "sensitive_word"


def test_short_table_cell_and_long_whitespace_fallback_maps_real_span(tmp_path):
    doc = Document()
    table = doc.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "数据库配置说明"
    table.cell(0, 1).text = "Oracle"
    doc.add_paragraph("前置说明" * 40 + "WindowsServer部署方案。")
    findings, content = check(doc, tmp_path, [SensitiveTerm("Oracle"), SensitiveTerm("Windows Server")])
    verify(findings, content)
    assert any(s["char_start"] >= 160 for f in findings for s in f["source_locations"])
