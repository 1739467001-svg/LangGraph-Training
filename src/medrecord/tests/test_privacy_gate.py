# -*- coding: utf-8 -*-
"""出口闸门测试：命中必拦、报警打码、拦截时模型零调用（对应真实仓库 --leak-test）"""
import pytest

from medrecord.privacy_gate import scan, gate_node, route_after_gate, GateBlocked, configure_gate


def test_手机号_拦截且报警打码():
    r = scan("联系患者家属 13800001234 确认")
    assert not r["passed"]
    assert r["hits"][0]["type"] == "手机号"
    assert "13800001234" not in r["hits"][0]["preview"]   # 报警单不喊号码
    assert "138" in r["hits"][0]["preview"] and "****" in r["hits"][0]["preview"]


def test_身份证号_拦截():
    r = scan("证件号 11010120990101123X 登记在册")
    assert not r["passed"] and r["hits"][0]["type"] == "身份证号"


def test_干净文本_放行():
    r = scan("0.9%氯化钠注射液 100ml 静滴 1/8小时（医嘱号40）")
    assert r["passed"]


def test_禁用姓名_命中():
    r = scan("患者张三丰今日出院", banned_names=["张三丰"])
    assert not r["passed"] and r["hits"][0]["type"] == "禁用姓名"
    assert "张三丰" not in r["hits"][0]["preview"]        # 报警单打码姓名


def test_路由_命中去拦截_干净进抽取():
    assert route_after_gate({"gate": {"passed": False}}) == "gate_blocked"
    assert route_after_gate({"gate": {"passed": True}}) == "extraction"


def test_闸门节点_第一道(monkeypatch):
    monkeypatch.setattr("medrecord.privacy_gate._gate",
                        {"banned_names": set(), "loaded": True})
    state = {"orders": [{"text": "普通医嘱文本"}], "exams": [{"item_name": "心电图", "impression": "窦性心律不齐"}]}
    out = gate_node(state)
    assert out["gate"]["passed"]


def test_第二道闸_拦截时模型零调用(monkeypatch):
    """防御纵深：就算第一道闸失守，工位内再扫一次，模型一次都不许被调用"""
    import medrecord.extraction as ex
    from medrecord.models import prompt, FactList
    calls = []

    class CountingModel:
        def __getattr__(self, name):   # 占位：只为了记录是否被调用
            raise AssertionError("闸门拦截后不得触碰模型")

    class Probe:
        def invoke(self, *a, **kw):
            calls.append(1)
            raise AssertionError("不应调用模型")

    monkeypatch.setattr(ex, "extraction_chain", Probe())
    chunk = [{"row": 2, "order_no": 1, "text": "泄漏文本 13911112222",
              "dose": "100ml", "route": "静滴", "freq": "1/日"}]
    with pytest.raises(GateBlocked):
        ex.extract_drug_chunk({"orders": chunk, "chunk_no": 0})
    assert calls == []                      # 模型零调用
