# -*- coding: utf-8 -*-
"""第 17 课（毕业设计参考答案）：检验所见工位测试

先写测试后写实现——测试是规格：
  1. 真实摘录 → 放行
  2. 出处不存在 → 拦截
  3. 项目名与出处不符 → 拦截
  4. 结果值被篡改 → 拦截（检验栏目的灵魂：10.8 改成 110.8 是人命关天的事）
"""
from medrecord.extraction import verify_all

# 夹具：一条真实形状的检验结果行（取自清洗后数据的真实结构，值已虚构化处理）
LABS = [{"row": 2, "test_no": "26052068664", "name": "谷丙转氨酶",
         "value": "10.8", "units": "U/L"}]

LAB_GOOD = {"section": "检验所见", "name": "谷丙转氨酶", "value": "10.8",
            "units": "U/L", "source": "检验结果.xlsx 第2行 检验号26052068664"}
LAB_BAD_SOURCE = {**LAB_GOOD, "source": "检验结果.xlsx 第9999行 检验号26052068664"}
LAB_BAD_NAME = {**LAB_GOOD, "name": "谷草转氨酶"}
LAB_TAMPERED = {**LAB_GOOD, "value": "110.8"}   # 10.8 → 110.8，十倍！


def test_检验所见_真实摘录_放行():
    out = verify_all({"orders": [], "exams": [], "labs": LABS, "facts": [LAB_GOOD], "qc": {}})
    assert len(out["verified"]) == 1
    assert out["verified"][0]["section"] == "检验所见"


def test_检验所见_出处不存在_拦截():
    out = verify_all({"orders": [], "exams": [], "labs": LABS,
                      "facts": [LAB_BAD_SOURCE], "qc": {}})
    assert out["verified"] == []
    assert "出处不存在" in out["rejected"][0]["reason"]


def test_检验所见_项目名与出处不符_拦截():
    out = verify_all({"orders": [], "exams": [], "labs": LABS,
                      "facts": [LAB_BAD_NAME], "qc": {}})
    assert "项目名与出处不符" in out["rejected"][0]["reason"]


def test_检验所见_结果值被篡改_拦截():
    out = verify_all({"orders": [], "exams": [], "labs": LABS,
                      "facts": [LAB_TAMPERED], "qc": {}})
    assert out["verified"] == []
    assert "结果值与原文不符" in out["rejected"][0]["reason"]
