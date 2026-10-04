# -*- coding: utf-8 -*-
"""模型工厂测试：三个分支都要考 + 真模型拆包坑

全程不联网：ChatOpenAI 构造不调网络，只有 .invoke() 才会。
"""
import json

from langchain_core.messages import AIMessage

from medrecord.models import FactList, BaselineModel, make_llm, parse_facts


def test_无key_用基线(monkeypatch):
    monkeypatch.delenv("DRCOPILOT_API_KEY", raising=False)
    assert isinstance(make_llm(), BaselineModel)


def test_有key未批准_铁律闸门拦住(monkeypatch):
    monkeypatch.setenv("DRCOPILOT_API_KEY", "sk-dummy")
    monkeypatch.delenv("DRCOPILOT_DATA_APPROVED", raising=False)
    assert isinstance(make_llm(), BaselineModel)   # 铁律6：不批准就不外发


def test_有key且批准_返回真模型零件(monkeypatch):
    monkeypatch.setenv("DRCOPILOT_API_KEY", "sk-dummy")
    monkeypatch.setenv("DRCOPILOT_DATA_APPROVED", "1")
    monkeypatch.setenv("DRCOPILOT_MODEL", "glm-4-flash")
    monkeypatch.setenv("DRCOPILOT_LLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4")
    llm = make_llm()
    assert type(llm).__name__ == "ChatOpenAI"      # 构造成功（未联网，invoke 才联网）


def test_有key批准但缺模型名_明确报错(monkeypatch):
    monkeypatch.setenv("DRCOPILOT_API_KEY", "sk-dummy")
    monkeypatch.setenv("DRCOPILOT_DATA_APPROVED", "1")
    monkeypatch.delenv("DRCOPILOT_MODEL", raising=False)
    try:
        make_llm()
        assert False, "应该报错"
    except SystemExit as e:
        assert "DRCOPILOT_MODEL" in str(e)


def test_拆包_真模型的markdown围栏():
    fenced = AIMessage(content="```json\n" + json.dumps(
        {"facts": [{"drug": "阿司匹林肠溶片", "dose": "【待医生补充】", "route": "口服",
                    "freq": "每日1次", "source": "医嘱.xlsx 第33行 医嘱号21"}]},
        ensure_ascii=False) + "\n```")
    out = parse_facts(fenced)                      # 基线测不出、真模型必踩的坑
    assert out.facts[0].drug == "阿司匹林肠溶片"
