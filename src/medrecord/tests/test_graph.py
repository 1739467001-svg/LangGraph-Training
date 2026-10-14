# -*- coding: utf-8 -*-
"""整机测试：核对闸门 + 全图冒烟 + 消防演习（注入说谎模型）

本文件守住的是整个课程的核心承诺：**模型说谎，系统不漏**。
"""
import json

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from medrecord.extraction import verify_all
from medrecord.graph import build_main_graph
from medrecord.models import FactList, prompt


# ---------- 核对闸门（第 4 课的 verify_all，安全关键节点）----------
ORDERS = [{"row": 20, "order_no": 10, "text": "0.9%氯化钠注射液（双阀）",
           "dose": "100.0 ml", "route": "静滴", "freq": "1/8小时"}]

GOOD_FACT = {"drug": "0.9%氯化钠注射液", "dose": "100 ml", "route": "静滴",
             "freq": "1/8小时", "source": "医嘱.xlsx 第20行 医嘱号10"}
BAD_SOURCE_FACT = {"drug": "注射用头孢呋辛钠", "dose": "1.5g", "route": "静脉滴注",
                   "freq": "每8小时1次", "source": "医嘱.xlsx 第9999行 医嘱号404"}
BAD_DRUG_FACT = {"drug": "阿司匹林肠溶片", "dose": "100 mg", "route": "口服",
                 "freq": "每日1次", "source": "医嘱.xlsx 第20行 医嘱号10"}


def test_真实事实_放行():
    out = verify_all({"orders": ORDERS, "exams": [], "labs": [], "facts": [GOOD_FACT], "qc": {}})
    assert len(out["verified"]) == 1
    assert out["qc"]["retry"] is False


def test_出处不存在_拦截():
    out = verify_all({"orders": ORDERS, "exams": [], "labs": [], "facts": [BAD_SOURCE_FACT], "qc": {}})
    assert out["verified"] == []
    assert "出处不存在" in out["rejected"][0]["reason"]


def test_药名与出处不符_拦截():
    out = verify_all({"orders": ORDERS, "exams": [], "labs": [], "facts": [BAD_DRUG_FACT], "qc": {}})
    assert "药名与出处不符" in out["rejected"][0]["reason"]


def test_重复事实_去重():
    out = verify_all({"orders": ORDERS, "exams": [], "labs": [], "facts": [GOOD_FACT, dict(GOOD_FACT)], "qc": {}})
    assert len(out["verified"]) == 1


def test_全部被剔除时_触发重抽():
    out = verify_all({"orders": ORDERS, "exams": [], "labs": [], "facts": [BAD_SOURCE_FACT], "qc": {}})
    assert out["qc"]["retry"] is True          # 通过率 0% → 退回重抽
    assert out["qc"]["attempts"] == 1


# ---------- 消防演习：注入一个专门说谎的模型，看整机拦不拦 ----------
class LiarModel(BaseChatModel):
    """不管喂什么医嘱，都编一条引用不存在的第 9999 行的用药事实"""

    @property
    def _llm_type(self) -> str:
        return "liar-model"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        facts = [BAD_SOURCE_FACT]
        return ChatResult(generations=[ChatGeneration(message=AIMessage(
            content=json.dumps({"facts": facts}, ensure_ascii=False)))])


def test_说谎模型_闸门拦截_草稿不收(monkeypatch):
    # 把抽取链整个换成「说谎版」——模型层是可替换零件（第 9 课布局的回报）
    import medrecord.extraction as ex
    liar_chain = prompt | LiarModel() | (lambda m: FactList.model_validate_json(m.content))
    monkeypatch.setattr(ex, "extraction_chain", liar_chain)

    app = build_main_graph(checkpointer=InMemorySaver())
    cfg = {"configurable": {"thread_id": "test-liar"}}
    init = {"folder": "测试患者乙", "patient_id": "测试患者乙", "orders": [], "exams": [], "labs": [], "facts": [],
            "verified": [], "rejected": [], "qc": {}, "gate": {}, "draft": "", "review": {}, "evaluation": ""}
    r = app.invoke(init, cfg)
    assert "__interrupt__" in r                       # 流程照样能走到审核站
    final = app.invoke(Command(resume="批准"), cfg)

    assert len(final["rejected"]) == 1                # 谎言被闸门抓住
    assert "出处不存在" in final["rejected"][0]["reason"]
    # 谎言是用药事实：核对通过的用药必须一条没收（检查所见是代码工位，合法产出）
    assert all(f["source"] != BAD_SOURCE_FACT["source"] for f in final["verified"])
    assert all(f.get("section") != "用药记录" for f in final["verified"])
    assert "编造：1 条" in final["evaluation"]         # 评估如实记账


# ---------- 全图冒烟：真基线模型走一遍，流程不炸、承诺不破 ----------
def test_整机_乙_停审核_批准走完():
    app = build_main_graph(checkpointer=InMemorySaver())
    cfg = {"configurable": {"thread_id": "test-smoke-乙"}}
    init = {"folder": "测试患者乙", "patient_id": "测试患者乙", "orders": [], "exams": [], "labs": [], "facts": [],
            "verified": [], "rejected": [], "qc": {}, "gate": {}, "draft": "", "review": {}, "evaluation": ""}
    r = app.invoke(init, cfg)
    assert "__interrupt__" in r
    assert app.get_state(cfg).next == ("doctor_review",)

    final = app.invoke(Command(resume="批准"), cfg)
    drug = [f for f in final["verified"] if f.get("section") == "用药记录"]
    exam = [f for f in final["verified"] if f.get("section") == "检查所见"]
    assert len(drug) >= 1
    assert all("医嘱号" in f["source"] for f in drug)      # 用药带医嘱号
    assert all("检查号" in f["source"] for f in exam)      # 检查所见带检查号
    assert "覆盖率" in final["evaluation"]


# ---------- 第 13 课：检查所见工位（代码工位 + 新核对分支）----------
EXAMS = [{"row": 5, "exam_no": "8697163", "item_name": "彩超胃肠道检查",
          "impression": "阑尾炎", "recommendation": "外科随诊"}]
EXAM_GOOD = {"section": "检查所见", "name": "彩超胃肠道检查", "impression": "阑尾炎",
             "recommendation": "外科随诊", "source": "检查结果.xlsx 第5行 检查号8697163"}
EXAM_BAD = {"section": "检查所见", "name": "彩超胃肠道检查", "impression": "阑尾炎",
            "recommendation": "外科随诊", "source": "检查结果.xlsx 第9999行 检查号8697163"}
EXAM_WRONG = {"section": "检查所见", "name": "胸部CT", "impression": "阑尾炎",
              "recommendation": "外科随诊", "source": "检查结果.xlsx 第5行 检查号8697163"}


def test_检查所见_真实摘录_放行():
    out = verify_all({"orders": ORDERS, "exams": EXAMS, "labs": [], "facts": [EXAM_GOOD], "qc": {}})
    assert len(out["verified"]) == 1
    assert out["verified"][0]["section"] == "检查所见"


def test_检查所见_出处不存在_拦截():
    out = verify_all({"orders": ORDERS, "exams": EXAMS, "labs": [], "facts": [EXAM_BAD], "qc": {}})
    assert "出处不存在" in out["rejected"][0]["reason"]


def test_检查所见_项目名与出处不符_拦截():
    out = verify_all({"orders": ORDERS, "exams": EXAMS, "labs": [], "facts": [EXAM_WRONG], "qc": {}})
    assert "项目名与出处不符" in out["rejected"][0]["reason"]


def test_检查所见_印象被篡改_拦截():
    tampered = {**EXAM_GOOD, "impression": "正常"}
    out = verify_all({"orders": ORDERS, "exams": EXAMS, "labs": [], "facts": [tampered], "qc": {}})
    assert "印象与原文不符" in out["rejected"][0]["reason"]
