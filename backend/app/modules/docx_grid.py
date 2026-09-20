"""Linear-time Word table grid with stable coordinates and merge semantics."""
W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'

def table_grid(table, *, repeat_vertical=True):
    element = getattr(table, '_tbl', table)
    previous = []
    result = []
    for tr in element.findall(W + 'tr'):
        before = tr.find(W + 'trPr/' + W + 'gridBefore')
        row = [''] * (int(before.get(W + 'val', '0')) if before is not None else 0)
        for tc in tr.findall(W + 'tc'):
            span = tc.find(W + 'tcPr/' + W + 'gridSpan')
            width = int(span.get(W + 'val', '1')) if span is not None else 1
            merge = tc.find(W + 'tcPr/' + W + 'vMerge')
            continuation = merge is not None and merge.get(W + 'val') != 'restart'
            # Only direct cell paragraphs: nested tables are distinct evidence.
            text = ' '.join(''.join(t.text or '' for t in p.iter(W + 't')) for p in tc.findall(W + 'p'))
            text = ' '.join(text.split())
            for _ in range(width):
                col = len(row)
                value = previous[col] if continuation and repeat_vertical and col < len(previous) else ('' if continuation else text)
                row.append(value)
        result.append(row)
        previous = row
    return result
