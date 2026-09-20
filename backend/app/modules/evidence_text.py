import re
import unicodedata

def compact(text):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', str(text or '')))

def positive_mentions(text, terms):
    """Affirmative clauses; punctuation and contrast limit negation scope."""
    for raw in re.split(r'[。；;\r\n]|但|然而', str(text or '')):
        clause = compact(raw)
        for term in terms:
            for m in re.finditer(re.escape(compact(term)), clause, re.I):
                prefix, suffix = clause[:m.start()], clause[m.end():]
                prefix = re.split(r'[，,]', prefix)[-1]
                if re.search(r'不(?:涉及|包含|含|纳入|申报|建设|采购|采用|使用)|无需|不得|禁止|不应', prefix):
                    continue
                if re.match(r'[(:：\s]*(?:不涉及|不包含|不含|不纳入|不申报|不采购|无需)', suffix):
                    continue
                yield clause
                break
