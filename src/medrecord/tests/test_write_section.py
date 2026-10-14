# -*- coding: utf-8 -*-
"""第 20 课：分批成文测试

覆盖：
  1. 按栏目分组（空栏目不派工位）
  2. 自定义归并规则：__RESET__ 擦黑板 / 其余追加
  3. 成文工位只处理自己栏目的事实，每句引用真实编号
  4. 质检回路：说谎书记员 → 拦截 → 重写 → 上限后带病进审核
  5. 重写不累积重复（RESET 规则的关键验收）
"""
import json

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from medrecord.state import written_reducer
from medrecord.writer import (assign_fact_ids, fan_out_write, group_by_section,
                              qc_write_node, route_after_qc_write, write_plan_node,
                              write_section_node)

FACTS = [
    {"section": "用药记录", "drug": "0.9%氯化钠注射液", "dose": "100 ml",
     "route": "静滴", "freq": "1/8小时", "source": "医嘱.xlsx 第20行 医嘱号10"},
    {"section": "检查所见", "name": "彩超胃肠道检查", "impression": "阑尾炎",
     "recommendation": "外科随诊", "source": "检查结果.xlsx 第5行 检查号8697163"},
    {"section": "检验所见", "name": "谷丙转氨酶", "value": "10.8",
     "units": "U/L", "source": "检验结果.xlsx 第2行 检验号26052068664"},
]


def _state(**over):
    base = {"verified": FACTS, "written": [], "qc_write": {}, "write_feedback": []}
    base.update(over)
    return base


# ---------- 分组与编号 ----------
def test_编号分配_稳定且从F1开始():
    ids = assign_fact_ids(FACTS)
    assert list(ids.keys()) == ["F1", "F2", "F3"]


def test_按栏目分组_三栏目各一组():
    groups = group_by_section(FACTS)
    assert set(groups.keys()) == {"用药记录", "检查所见", "检验所见"}
    assert groups["用药记录"][0][0] == "F1"


def test_按栏目分组_空栏目不出现():
    assert group_by_section([]) == {}


# ---------- 归并规则 ----------
def test_归并规则_RESET清空_其余追加():
    assert written_reducer([{"text": "旧"}], "__RESET__") == []
    assert written_reducer([{"text": "A"}], [{"text": "B"}]) == [{"text": "A"}, {"text": "B"}]
    assert written_reducer(None, [{"text": "B"}]) == [{"text": "B"}]


# ---------- 分发 ----------
def test_分发_每个非空栏目一个工位():
    sends = fan_out_write(_state())
    assert [s.arg["section"] for s in sends] == ["用药记录", "检查所见", "检验所见"]
    assert len(sends[0].arg["facts"]) == 1
    assert all(s.node == "write_section" for s in sends)


def test_分发_没有事实时直通质检():
    assert fan_out_write(_state(verified=[])) == "qc_write"


def test_开工_返回RESET标记():
    assert write_plan_node(_state())["written"] == "__RESET__"


# ---------- 成文工位 ----------
def test_成文工位_只写自己栏目_引用真实编号():
    groups = group_by_section(FACTS)
    sec = "检查所见"
    out = write_section_node({
        "section": sec,
        "facts": [{"id": fid, **fact} for fid, fact in groups[sec]],
        "feedback": "（无）",
    })
    assert len(out["written"]) == 1
    assert out["written"][0]["sources"] == ["F2"]


# ---------- 质检回路 ----------
def test_质检_引用不存在编号_拦截并退回write_plan():
    bad = [{"text": "患者使用了某种神药。", "sources": ["F9999"]}]
    out = qc_write_node(_state(written=bad))
    assert out["qc_write"]["passed"] is False
    assert out["qc_write"]["retry"] is True
    assert any("F9999" in i for i in out["write_feedback"])
    assert route_after_qc_write(out) == "write_plan"


def test_质检_没有出处的句子_拦截():
    out = qc_write_node(_state(written=[{"text": "患者一般情况可。", "sources": []}]))
    assert any("没有出处" in i for i in out["qc_write"]["issues"])


def test_质检_重写上限后_带病进审核不再循环():
    bad = [{"text": "编的话。", "sources": ["F9999"]}]
    out = qc_write_node(_state(written=bad, qc_write={"attempts": 2}))
    assert out["qc_write"]["retry"] is False
    assert route_after_qc_write(out) == "build_draft"


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


def test_说谎书记员_三轮后带病进审核_且无重复累积(monkeypatch):
    """消防演习·第二场（分批版）：
       RESET 规则保证每轮重写都从空黑板开始——最后 written 只有 1 句，
       不是 3 句叠加；上限后带病进审核，交给医生"""
    import medrecord.writer as w
    from medrecord.models import write_prompt, SentenceList

    liar_chain = write_prompt | LiarWriter() | (
        lambda m: SentenceList.model_validate_json(m.content))
    monkeypatch.setattr(w, "write_chain", liar_chain)

    state = _state()
    rounds = 0
    while True:
        # 一轮 = 开工(擦黑板) → 各栏目工位成文(汇合) → 质检
        reset = write_plan_node(state)
        state["written"] = written_reducer(state["written"], reset["written"])
        for send in fan_out_write(state):
            part = write_section_node(send.arg)
            state["written"] = written_reducer(state["written"], part["written"])
        out = qc_write_node(state)
        state["qc_write"] = out["qc_write"]
        state["write_feedback"] = out["write_feedback"]
        rounds += 1
        if not out["qc_write"]["retry"]:
            break

    assert rounds == 3                                   # 初稿 + 重写 2 次（上限生效）
    assert state["qc_write"]["passed"] is False
    assert len(state["written"]) == 3                    # RESET 生效：3 个栏目 × 1 句，
    #                                                      不是 9 句跨轮叠加
    assert route_after_qc_write(state) == "build_draft"
