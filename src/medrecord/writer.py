# -*- coding: utf-8 -*-
"""writer.py —— 「书记员科室」：成文 → 质检 →（不合格带意见重写，最多 2 次）

对应 donge-hospital-ai tech-spec 的 write + qc 两站：
  write：大模型只用【核对过的事实】写成病历语言，每句带事实编号
  qc   ：代码逐句检查——每句必须有真实存在的编号；不合格把意见写回提示词重写

这就是第 2 课「退回重写」的第二次实战，区别在于：
  退回时不再只是重跑，而是把【质检意见】注入提示词——模型带着批注改稿。
"""
import json

from medrecord.models import write_chain
from medrecord.state import VisitState

MAX_WRITE_RETRY = 2   # 成文重写上限（tech-spec：退回重写，最多 2 次）


def assign_fact_ids(verified: list) -> dict:
    """给核对通过的事实编上稳定编号 F1..Fn——书记员只能引用这些编号。
       编号在「成文」和「质检」两处必须一致，所以只在这里定义一次"""
    return {f"F{i + 1}": f for i, f in enumerate(verified)}


def write_node(state: VisitState) -> dict:
    ids = assign_fact_ids(state["verified"])
    facts_payload = [{"id": k, **v} for k, v in ids.items()]
    feedback = "；".join(state.get("write_feedback", [])) or "（无）"
    result = write_chain.invoke({
        "facts_json": json.dumps(facts_payload, ensure_ascii=False, default=str),
        "feedback": feedback,
    })
    written = [s.model_dump() for s in result.sentences]
    rnd = state["qc_write"].get("attempts", 0) + 1
    tag = "初稿" if feedback == "（无）" else f"重写第{rnd - 1}次"
    print(f"[书记员] {tag}：成文 {len(written)} 句")
    return {"written": written}


def qc_write_node(state: VisitState) -> dict:
    """逐句质检：每句必须有真实存在的编号。引用不存在的编号 = 编造的铁证"""
    valid_ids = set(assign_fact_ids(state["verified"]).keys())
    issues = []
    for s in state["written"]:
        if not s["text"].strip():
            issues.append("出现空句子")
        if not s["sources"]:
            issues.append(f"句子没有出处：「{s['text'][:24]}」")
        for src in s["sources"]:
            if src not in valid_ids:
                issues.append(f"引用了不存在的事实编号 {src}：「{s['text'][:24]}」")
    attempts = state["qc_write"].get("attempts", 0)
    retry = bool(issues) and attempts < MAX_WRITE_RETRY
    if issues:
        print(f"[质检·成文] 第{attempts + 1}轮：发现 {len(issues)} 个问题"
              + ("→ 带意见退回重写" if retry else "→ 已达重写上限，带问题进入审核"))
    else:
        print(f"[质检·成文] 第{attempts + 1}轮：{len(state['written'])} 句全部通过")
    return {
        "qc_write": {"passed": not issues, "issues": issues, "retry": retry,
                     "attempts": attempts + (1 if retry else 0)},
        "write_feedback": issues if retry else [],
    }


def route_after_qc_write(state: VisitState) -> str:
    """条件边：质检不合格且没到上限 → 回书记员重写；否则 → 拼草稿"""
    return "write" if state["qc_write"]["retry"] else "build_draft"
