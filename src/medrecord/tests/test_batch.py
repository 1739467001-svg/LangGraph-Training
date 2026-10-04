# -*- coding: utf-8 -*-
"""批量验收测试：治理规则必须被断言锁住"""
from medrecord.batch import run_batch


def test_批量验收_乙_报告与治理():
    report = run_batch(["测试患者乙"])
    assert "| 测试患者乙 |" in report          # 汇总表有该患者
    assert "未过审" in report                  # 治理：批量稿一律归档
    assert "自动化评估不等于自动化批准" in report
