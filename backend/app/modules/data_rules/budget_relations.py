"""Evidence for nested budget tables. Never merge tables on total alone."""
import re

def _number(value):
    text = str(value).strip().replace(',', '')
    return float(text) if re.fullmatch(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)', text) else None

def _norm(text):
    return re.sub(r'\s+', '', text).lower()

def entries(rows, hi, amount_col, unit):
    header = rows[hi]
    qi = next((i for i, c in enumerate(header) if '数量' in c or '工作量' in c), None)
    pi = next((i for i, c in enumerate(header) if '单价' in c), None)
    if qi is None or pi is None:
        return []
    result = []
    pu = 10000 if '万元' not in header[pi] and '元' in header[pi] else (10000 if unit == '元' and '万元' not in header[pi] else 1)
    for ri, row in enumerate(rows[hi+1:], hi+2):
        if len(row) <= max(qi, pi, amount_col) or any('合计' in c or '总计' in c for c in row[:3]): continue
        q, p, a = (_number(row[i]) for i in (qi, pi, amount_col))
        if q is None or p is None or a is None: continue
        tokens = []
        anchors = [_norm(row[j]) for j, h in enumerate(header[:qi]) if ('服务事项' in h or '智能体名称' in h) and row[j].strip() and len(_norm(row[j])) <= 20]
        for c in row[:qi]:
            for token in re.split(r'[，,（()）;；]', _norm(c)):
                if 4 <= len(token) <= 80 or (3 <= len(token) <= 30 and token.endswith('库')):
                    tokens.append(token)
        result.append({'row': ri, 'text': _norm(' '.join(row[:qi])), 'tokens': tokens, 'anchors': anchors, 'quantity': q, 'price_wan': round(p/pu, 6), 'amount_wan': round(a/(10000 if unit == '元' else 1), 6)})
    return result

def find_subsets(records):
    relations = []
    for child in records:
        ce = child['entries']
        if not ce or abs(sum(x['amount_wan'] for x in ce) - child['total']) > .0001: continue
        for parent in sorted(records, key=lambda r: r['total'], reverse=True):
            if parent is child or parent['total'] <= child['total'] or len(parent['entries']) < len(ce): continue
            used, links = set(), []
            for c in ce:
                choices = []
                for j, p in enumerate(parent['entries']):
                    if j in used: continue
                    if not all(t in p['text'] for t in c.get('anchors', [])): continue
                    if any(abs(c[k]-p[k]) > .0001 for k in ('quantity', 'price_wan', 'amount_wan')): continue
                    score = max((len(t) for t in c['tokens'] if t in p['text']), default=0)
                    if score: choices.append((score, j, p))
                if not choices: break
                _, j, p = max(choices, key=lambda x: x[0]); used.add(j)
                links.append({'child_row': c['row'], 'parent_row': p['row'], 'amount_wan': c['amount_wan']})
            if len(links) == len(ce) or (len(ce) >= 2 and len(links) / len(ce) >= .5):
                relations.append({'child': child['source'], 'parent': parent['source'], 'amount_wan': child['total'], 'rows': links, 'complete': len(links) == len(ce), 'total_rows': len(ce), 'basis': '逐行名称包含关系及数量、单价、金额核验；未全部匹配时不得排除或认定重复'})
                break
    return relations
