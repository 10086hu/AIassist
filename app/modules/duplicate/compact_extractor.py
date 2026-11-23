"""Build module summaries from long reports before asking a model to extract prose."""
from __future__ import annotations

import re
from dataclasses import replace

from app.modules.duplicate.document_parser import DocumentContent
from app.modules.duplicate.excel_parser import ParsedFunctionPoint


BUILD_SECTIONS = ("项目建设", "建设内容", "建设方案", "功能设计", "应用软件", "软件开发", "功能需求", "业务功能", "系统功能")
EXCLUDED_SECTIONS = ("项目概况", "项目概述", "现状", "必要性", "建设目标", "效益", "实施计划", "项目管理", "建设与运行管理", "安全保障体系")


def _key(text: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", text.lower())


def _title(text: str) -> str:
    return re.sub(r"^(?:第[一二三四五六七八九十\d]+[章节]|\d+(?:\.\d+)*\.?|[一二三四五六七八九十]+[、.])\s*", "", text).strip(" ：:")


def _location(document, block, name, scope, evidence):
    return {
        "file_name": document.filename, "file_type": document.format,
        "paragraph": block.paragraph, "line_start": block.line, "line_end": block.line,
        "precision": "paragraph", "section": " / ".join(block.heading_path) or block.section,
        "quote": block.text, "construction_unit": name, "business_scope": scope,
        "review_context": evidence,
    }


def _covered_heading(heading, groups) -> bool:
    title = _key(_title(heading.text))
    path = [_key(_title(t)) for t in heading.heading_path[:-1]]
    for group in groups.values():
        scope, module = _key(group["scope"]), _key(group["module"])
        if title == scope:
            return True
        if title != module:
            continue
        # The same module name under another business system is a distinct
        # construction unit; never remove it with a global name-only dedup.
        if scope in path:
            return True
    return False


def extract_compact_modules(document: DocumentContent) -> list[ParsedFunctionPoint]:
    """Keep core subfunctions and business scope; merge design/budget repetitions.

    Table cells are already expanded for horizontal/vertical merges by the parser.
    Missing sections are supplemented from module headings, so a partial function
    table does not suppress other software modules later in a long document.
    """
    groups: dict[tuple[str, str], dict] = {}
    table_headers = {}
    table_scope = {}
    for block in document.source_blocks:
        cells = block.cells
        if not cells:
            continue
        if any(word in " / ".join(block.heading_path[:2]) for word in EXCLUDED_SECTIONS):
            continue
        header = {_key(value): index for index, value in enumerate(cells) if value}
        module_col = next((header[k] for k in ("模块名称", "功能模块", "模块") if k in header), None)
        system_col = next((header[k] for k in ("系统名称", "系统", "应用名称", "业务名称") if k in header), None)
        desc_col = next((header[k] for k in ("功能描述", "主要功能", "建设内容描述", "功能说明") if k in header), None)
        if module_col is not None and desc_col is not None:
            core_col = next((header[k] for k in ("子模块名称", "功能名称", "功能点", "子功能") if k in header), None)
            table_headers[block.table_index] = (system_col, module_col, core_col, desc_col)
            continue
        columns = table_headers.get(block.table_index)
        if columns is None:
            continue
        sc, mc, cc, dc = columns
        if max(c for c in columns if c is not None) >= len(cells):
            continue
        previous_scope, previous_module = table_scope.get(block.table_index, ("", ""))
        scope = cells[sc].strip() if sc is not None else ""
        scope = scope or previous_scope or " / ".join(block.heading_path[-2:])
        module = cells[mc].strip() or (previous_module if scope == previous_scope else "")
        table_scope[block.table_index] = (scope, module)
        if not module or _key(scope) in {"合计", "总计", "小计"} or _key(module) in {"合计", "总计", "小计"}:
            continue
        name = f"{scope} / {module}" if scope and scope != module else module
        identity = (_key(scope), _key(module))
        group = groups.setdefault(identity, {"name": name, "scope": scope, "module": module,
                                            "block": block, "cores": [], "details": [], "sources": []})
        core = cells[cc].strip() if cc is not None else ""
        detail = cells[dc].strip()
        for field, value in (("cores", core), ("details", detail)):
            if value and value not in group[field]:
                group[field].append(value)
        if block.text not in [b.text for b in group["sources"]]:
            group["sources"].append(block)

    # Keep original lifecycle sentences when tables omit reuse/upgrade status.
    lifecycle = [b for b in document.source_blocks if not b.heading_level and not b.table_index
                 and any(word in b.text for word in ("复用", "已建", "已完成", "升级", "本期", "往期", "不重复建设", "不新建"))]
    background = "\n".join(b.text[:220] for b in lifecycle[:5])
    points = []
    for group in groups.values():
        # Each capability gets a short detail rather than only taking the start
        # of a long module; late subfunctions remain visible to retrieval.
        description = "核心子功能：" + "、".join(group["cores"])
        description += "；范围：" + "；".join(text[:90] for text in group["details"])
        evidence = "\n".join(b.text for b in group["sources"])
        loc = _location(document, group["block"], group["name"], group["scope"], evidence)
        loc["module_summary"] = True
        loc["core_functions"] = group["cores"]
        relevant = [b.text[:300] for b in lifecycle
                    if group["scope"] in b.text or group["module"] in b.text]
        loc["report_context"] = background
        loc["lifecycle_context"] = "\n".join(relevant[:4])
        loc["source_spans"] = [
            {"paragraph": b.paragraph, "line_start": b.line, "quote": b.text, "file_name": document.filename}
            for b in group["sources"]
        ]
        points.append(ParsedFunctionPoint(0, group["name"][:200], description,
                                          "建设模块", loc))

    blocks = document.source_blocks
    headings = [(i, b) for i, b in enumerate(blocks) if b.heading_level]
    for position, (start, heading) in enumerate(headings):
        title = _title(heading.text)
        path = heading.heading_path
        if not any(word in " / ".join(path) for word in BUILD_SECTIONS):
            continue
        if any(word in " / ".join(path[:2]) for word in EXCLUDED_SECTIONS):
            continue
        if not 2 <= heading.heading_level <= 4 or _covered_heading(heading, groups):
            continue
        if not any(word in title for word in ("模块", "系统", "平台", "一件事", "免申即享", "功能", "查询", "管理", "治理", "分析", "服务", "监控", "告警", "对接", "认证", "采集", "共享", "交换", "发布", "审批", "办理", "展示", "应用")):
            continue
        end = next((i for i, b in headings[position + 1:] if b.heading_level <= heading.heading_level), len(blocks))
        descendants = blocks[start + 1:end]
        if any(b.heading_level and b.heading_level <= 4 for b in descendants):
            continue  # The child module owns the capabilities, not its parent.
        if any(_covered_heading(b, groups) for b in descendants if b.heading_level):
            continue
        content = [b for b in descendants if not b.table_index]
        if not content:
            continue
        scope = " / ".join(_title(t) for t in path[:-1][-2:])
        name = f"{scope} / {title}" if scope else title
        cores = [_title(b.text) for b in content if b.heading_level]
        snippets = [b.text[:160] for b in content if not b.heading_level]
        description = "核心子功能：" + "、".join(cores) + "；范围：" + "；".join(snippets)
        evidence = heading.text + "\n" + "\n".join(b.text for b in content)
        loc = _location(document, heading, name, scope, evidence)
        loc["module_summary"] = True
        loc["core_functions"] = cores
        loc["report_context"] = background
        loc["source_spans"] = [{"paragraph": b.paragraph, "line_start": b.line, "quote": b.text,
                                 "file_name": document.filename} for b in [heading, *content]]
        points.append(ParsedFunctionPoint(0, name[:200], description, "建设模块", loc))

    points = _merge_flattened_modules(points)
    return [replace(point, row_index=i) for i, point in enumerate(points, 1)]


def _merge_flattened_modules(points):
    """Restore modules from flat titles like 中台-采集管理-单表同步."""
    output, groups = [], {}
    for point in points:
        location = point.source_location or {}
        leaf = point.name.rsplit(" / ", 1)[-1]
        parts = re.split(r"[-－—]", leaf)
        if len(parts) < 3 or not all(parts):
            output.append(point)
            continue
        module = "-".join(parts[:2])
        identity = (location.get("business_scope"), module)
        if identity not in groups:
            groups[identity] = []
            output.append(identity)
        groups[identity].append((point, "-".join(parts[2:])))
    result = []
    for item in output:
        if isinstance(item, ParsedFunctionPoint):
            result.append(item)
            continue
        members = groups[item]
        first = members[0][0]
        loc = dict(first.source_location)
        name = f"{item[0]} / {item[1]}" if item[0] else item[1]
        cores = list(dict.fromkeys(core for _, core in members))
        loc.update(construction_unit=name, core_functions=cores,
                   review_context="\n".join(p.source_location["review_context"] for p, _ in members),
                   source_spans=[span for p, _ in members for span in p.source_location.get("source_spans", [])])
        result.append(replace(first, name=name[:200], description="核心子功能：" + "、".join(cores)
                              + "；范围：" + "；".join(p.description[:90] for p, _ in members), source_location=loc))
    return result


def focused_document(document: DocumentContent) -> DocumentContent:
    """Fallback model extraction reads construction sections, not all 200 pages."""
    selected = [s for s in document.sections
                if any(word in " / ".join(s.heading_path or (s.title,)) for word in BUILD_SECTIONS)
                and not any(word in " / ".join(s.heading_path[:2]) for word in EXCLUDED_SECTIONS)]
    return replace(document, sections=selected,
                   raw_text="\n".join(s.title + "\n" + s.content for s in selected)) if selected else document
