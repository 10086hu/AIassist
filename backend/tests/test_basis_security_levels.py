"""Regression coverage for security levels that previously aborted basis checks."""
from io import BytesIO
from pathlib import Path
import sys

import pytest
from docx import Document

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.modules.shanghai_review.construction_basis_validator import (
    _extract_security_level,
    _validate_security,
)
from app.modules.shanghai_review.service import run_basis_check_from_document


@pytest.mark.parametrize(
    ('text', 'level'),
    [
        ('二级等保', 2),
        ('三级等保测评', 3),
        ('四级等级保护', 4),
        ('五级安全保护', 5),
        ('三级 等保', 3),
        ('等级保护为三级', 3),
        ('安全保护等级：二级', 2),
        ('网络安全等级3级', 3),
        ('信息系统安全等级第4级', 4),
        ('安全保护等级5级', 5),
        ('未明确安全保护等级', None),
    ],
)
def test_extract_security_level(text, level):
    assert _extract_security_level(text) == level
    # The standalone security module consumes the same language and must not
    # abort when a reversed level declaration matches its third pattern.
    from app.modules.shanghai_review.security_review import _extract_security_level as security_level
    assert security_level(text) == level


@pytest.mark.parametrize(('construction_level', 'mismatch'), [('三级', False), ('二级', True)])
def test_security_consistency_preserves_level_comparison(construction_level, mismatch):
    errors = _validate_security({
        '4.7': '按三级等保要求开展建设。',
        '6.6': f'按照{construction_level}等保要求落实安全建设。',
    })
    assert any(error.code == 'SCC-001' for error in errors) is mismatch


def test_basis_document_with_reversed_security_level_completes():
    document = Document()
    document.add_paragraph('本项目按照三级等保要求建设。')
    buffer = BytesIO()
    document.save(buffer)

    result = run_basis_check_from_document(buffer.getvalue(), '三级等保.docx')

    assert result['status'] in {'通过', '发现问题', '需人工复核'}
    assert len(result['results']) == 4
    security = next(rule for rule in result['results'] if rule['rule_id'] == 'BASIS_014')
    # A standalone level declaration cannot substitute for two independently
    # identified demand/construction sections.
    assert security['passed'] is False
    assert security['status'] == '需人工复核'
