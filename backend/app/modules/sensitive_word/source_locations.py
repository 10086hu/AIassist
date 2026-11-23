"""Resolve legacy sensitive-word segments to the original DOCX and word span.

The detector and its rules are unchanged. Its paragraph enumeration includes
table paragraphs, whereas the preview numbers body paragraphs independently.
This adapter translates those identities before findings are grouped.
"""
from __future__ import annotations

import re

from app.modules.resource.quantity_parser import DocumentIndex, W, _public_location
from .sensitive_word_checker import normalize_text


def compact_map(text):
    value, positions = [], []
    for offset, char in enumerate(text):
        normalized = normalize_text(char)
        for part in normalized:
            if not part.isspace():
                value.append(part.casefold())
                positions.extend([offset] * len(part.casefold()))
    return ''.join(value), positions


def location_key(source):
    return tuple(source.get(key) for key in ('paragraph', 'table_index', 'table_path',
        'row_index', 'column_start', 'char_start', 'char_end'))


def attach_sensitive_locations(findings, content, filename):
    index = DocumentIndex(content, filename)
    root = index.document.element
    paragraph_sources = {}
    blocks = {b['paragraph']: b for b in index.blocks if b.get('paragraph')}
    rows = {(b['table_index'], b.get('table_path', ''), b['row_index']): b
            for b in index.blocks if b.get('table_index')}

    def register_paragraph(element, source, base=0):
        paragraph_sources[element] = (source, base)

    def register_table(element, number, path=''):
        for row_number, row in enumerate(element.findall(W+'tr'), 1):
            block = rows.get((number, path, row_number))
            element_cells = row.findall(W+'tc')
            for cell_index, element_cell in enumerate(element_cells):
                # A parent row may contain only a nested table and therefore
                # has no DocumentIndex row block of its own.
                cell = block['cells'][cell_index] if block and cell_index < len(block['cells']) else None
                if cell is not None:
                    source = _public_location(block, filename)
                    source.pop('cells', None)
                    source.update(quote=cell['quote'], row_index=cell['origin_row'],
                                  column_start=cell['column_start'], precision='cell')
                    base = 0
                    for paragraph in element_cell.findall(W+'p'):
                        register_paragraph(paragraph, source, base)
                        base += len(compact_map(''.join(t.text or '' for t in paragraph.iter(W+'t')))[0])
                column_start = cell['column_start'] if cell is not None else cell_index + 1
                for nested_number, nested in enumerate(element_cell.findall(W+'tbl'), 1):
                    nested_path = f'{path + "/" if path else ""}{row_number}/{column_start}/{nested_number}'
                    register_table(nested, number, nested_path)

    paragraph_number = table_number = 0
    for child in index.body_children(index.document.element.body):
        if child.tag == W+'p':
            paragraph_number += 1
            if paragraph_number in blocks:
                register_paragraph(child, _public_location(blocks[paragraph_number], filename))
        elif child.tag == W+'tbl':
            table_number += 1
            register_table(child, table_number)

    # Reproduce the detector's enumeration, including nonempty table paragraphs.
    segments = {}
    legacy_number = 0
    for paragraph in root.iter(W+'p'):
        text = normalize_text(''.join(t.text or '' for t in paragraph.iter(W+'t')))
        if not text:
            continue
        legacy_number += 1
        segments[legacy_number] = (text, [(0, len(compact_map(text)[0]), paragraph)])
    for number, table in enumerate(root.iter(W+'tbl'), 1):
        pieces, row_texts, cursor = [], [], 0
        for row in table.iter(W+'tr'):
            cells = []
            for cell in row.iter(W+'tc'):
                text = normalize_text(''.join(t.text or '' for t in cell.iter(W+'t')))
                if not text:
                    continue
                if cells:
                    cursor += 1  # compact form of the literal row separator " | "
                start = cursor
                for paragraph in cell.iter(W+'p'):
                    length = len(compact_map(''.join(t.text or '' for t in paragraph.iter(W+'t')))[0])
                    pieces.append((start, start + length, paragraph))
                    start += length
                cursor += len(compact_map(text)[0])
                cells.append(text)
            if cells:
                row_texts.append(' | '.join(cells))
        segments[100000 + number] = (normalize_text('\n'.join(row_texts)), pieces)

    for finding in findings:
        segment = segments.get(finding.get('paragraph_index'))
        locations = []
        if segment:
            text, pieces = segment
            compact, _ = compact_map(text)
            hit, _ = compact_map(str(finding.get('hit_text') or ''))
            start, end = finding.get('start', -1), finding.get('end', -1)
            span_start = len(compact_map(text[:start])[0]) if start >= 0 else -1
            span_end = len(compact_map(text[:end])[0]) if end >= 0 else -1
            spans = [(span_start, span_end)] if compact[span_start:span_end] == hit else []
            if not spans and hit:
                candidates = [(m.start(), m.end()) for m in re.finditer(re.escape(hit), compact)]
                if len(candidates) == 1:
                    spans = candidates
            for start, end in spans:
                for first, last, paragraph in pieces:
                    if not (first <= start < end <= last) or paragraph not in paragraph_sources:
                        continue
                    source, base = paragraph_sources[paragraph]
                    actual, positions = compact_map(source['quote'])
                    left, right = base + start - first, base + end - first
                    if actual[left:right] != hit or right > len(positions):
                        continue
                    location = dict(source, char_start=positions[left], char_end=positions[right-1]+1,
                                    verified_sensitive=True)
                    location['hit_text'] = location['quote'][location['char_start']:location['char_end']]
                    locations.append(location)
        finding['source_locations'] = list({location_key(s): s for s in locations}.values())
        finding['source_location'] = {'file_name': filename, 'file_type': 'docx',
            'verified_sensitive': True, 'precision': 'document_blocks',
            'match_status': 'matched' if locations else 'unresolved', 'entries': finding['source_locations']}
    return findings
