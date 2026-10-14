# -*- coding: utf-8 -*-
"""第 18 课：成文节点测试

覆盖三件事：
  1. 基线书记员：成文的每句话都引用真实存在的事实编号
  2. 质检回路：说谎的书记员（引用 F9999）→ 质检拦截 → 带意见退回 → 上限后带病进审核
  3. 空栏目由代码加「待医生补充」，不交给模型
"""
import json

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from medrecord.writer import assign_fact_ids, qc_write_node, route_after_qc_write, write_node

FACTS = [
    {"section": "用药记录", "drug": "0.9%氯化钠注射液", "dose": "100 ml",
     "route": "静滴", "freq": "1/8小时", "source": "医嘱.xlsx 第20行 医嘱号10"},
    {"section": "检查所见", "name": "彩超胃肠道检查", "impression": "阑尾炎",
     "recommendation": "外科随诊", "source": "检查结果.xlsx 第5行 检查号8697163"},
]


def _state(**over):
    base = {"verified": FACTS, "written": [], "qc_write": {}, "write_feedback": []}
    base.update(over)
    return base


def test_编号分配_稳定且从F1开始():
    ids = assign_fact_ids(FACTS)
    assert list(ids.keys()) == ["F1", "F2"]
    assert ids["F1"]["drug"] == "0.9%氯化钠注射液"


def test_基线书记员_每句引用真实编号():
    out = write_node(_state())
    ids = set(assign_fact_ids(FACTS).keys())
    assert len(out["written"]) == 2
    for s in out["written"]:
        assert s["sources"] and set(s["sources"]) <= ids   # 只引用真实编号


def test_质检_基线初稿一次通过():
    written = write_node(_state())["written"]
    out = qc_write_node(_state(written=written))
    assert out["qc_write"]["passed"] is True
    assert out["qc_write"]["retry"] is False
    assert route_after_qc_write(out) == "build_draft"


def test_质检_引用不存在编号_拦截并带意见退回():
    bad = [{"text": "患者使用了某种神药。", "sources": ["F9999"]}]
    out = qc_write_node(_state(written=bad))
    assert out["qc_write"]["passed"] is False
    assert out["qc_write"]["retry"] is True                       # 还有重写机会
    assert any("F9999" in i for i in out["write_feedback"])       # 意见注入提示词
    assert route_after_qc_write(out) == "write"                   # 退回书记员


def test_质检_没有出处的句子_拦截():
    out = qc_write_node(_state(written=[{"text": "患者一般情况可。", "sources": []}]))
    assert any("没有出处" in i for i in out["qc_write"]["issues"])


def test_质检_重写上限后_带病进审核不再循环():
    # 已经重写过 2 次（MAX_WRITE_RETRY），仍有问题 → 不再退回
    bad = [{"text": "编的话。", "sources": ["F9999"]}]
    out = qc_write_node(_state(written=bad, qc_write={"attempts": 2}))
    assert out["qc_write"]["retry"] is False
    assert route_after_qc_write(out) == "build_draft"             # 保险丝：进审核
    assert out["qc_write"]["passed"] is False                     # 但如实记录未通过


class LiarWriter(BaseChatModel):
    """说谎的书记员：每句都引用不存在的事实编号 F9999"""

    @property
    def _llm_type(self) -> str:
        return "liar-writer"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        content = json.dumps({"sentences": [
            {"text": "患者今日应用某药，剂量合适。", "sources": ["F9999"]},
        ]}, ensure_ascii=False)
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=content))])


def test_说谎书记员_质检拦截_重写两轮后带病进审核(monkeypatch):
    """消防演习·第二场：书记员编造出处 → 质检每轮拦截 →
       上限后草稿带警示语进入医生审核（不静默放行，也不死循环）"""
    import medrecord.writer as w
    from medrecord.models import write_prompt, SentenceList

    liar_chain = write_prompt | LiarWriter() | (
        lambda m: SentenceList.model_validate_json(m.content))
    monkeypatch.setattr(w, "write_chain", liar_chain)

    state = _state()
    rounds = 0
    while True:
        written = w.write_node(state)["written"]
        state["written"] = written
        out = w.qc_write_node(state)
        state["qc_write"] = out["qc_write"]
        state["write_feedback"] = out["write_feedback"]
        rounds += 1
        if not out["qc_write"]["retry"]:
            break
    assert rounds == 3                                  # 初稿 + 重写 2 次（上限生效）
    assert state["qc_write"]["passed"] is False         # 全程没通过
    assert state["write_feedback"] == []                # 上限后不再注入意见
    assert route_after_qc_write(state) == "build_draft" # 带病进审核，交给医生
