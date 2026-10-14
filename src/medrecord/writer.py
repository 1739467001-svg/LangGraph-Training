# -*- coding: utf-8 -*-
"""writer.py —— 「书记员科室」：按栏目分批成文 → 质检 →（带意见重写，最多 2 次）

第 18 课的书记员是一个人看全部事实写全部句子——真模型一上线会爆 token。
第 20 课改成「按栏目分批」：用药/检查/检验各开一个成文工位并行写，
写完汇合到质检站。分几批由数据里的栏目决定（和抽取科同一套 Send 打法）。

三个新概念：
  1. write_plan  → 返回 "__RESET__"，让自定义归并规则先擦黑板（防重写累积重复）
  2. Send 按栏目分发成文工位（每个工位只看自己栏目的事实）
  3. 多个工位汇合到同一个 qc_write 节点——LangGraph 等全部到齐后只跑一次（join）
"""
import json

from langgraph.types import Send

from medrecord.models import write_chain
from medrecord.state import VisitState

MAX_WRITE_RETRY = 2   # 成文重写上限（tech-spec：退回重写，最多 2 次）


def assign_fact_ids(verified: list) -> dict:
    """给核对通过的事实编上稳定编号 F1..Fn——书记员只能引用这些编号。
       编号在「成文」和「质检」两处必须一致，所以只在这里定义一次"""
    return {f"F{i + 1}": f for i, f in enumerate(verified)}


def group_by_section(verified: list) -> dict:
    """按栏目分组：{"用药记录": [(F1, fact), ...], ...}——只保留非空栏目"""
    groups = {}
    for fid, fact in assign_fact_ids(verified).items():
        groups.setdefault(fact.get("section", "用药记录"), []).append((fid, fact))
    return {sec: items for sec, items in groups.items() if items}


def sources_to_text(sources: list, ids: dict) -> str:
    """把内部事实编号（F3）翻译成医生看的真实出处（医嘱.xlsx 第20行 医嘱号10）"""
    parts = []
    for s in sources:
        fact = ids.get(s)
        parts.append(fact["source"] if fact else s)
    return "；".join(parts)


def write_plan_node(state: VisitState) -> dict:
    """成文轮次开工：擦黑板（清掉上一轮的句子），本轮由 fan_out_write 分发"""
    rnd = state["qc_write"].get("attempts", 0) + 1
    tag = "初稿" if not state.get("write_feedback") else f"重写第{rnd - 1}次（带质检意见）"
    print(f"[书记员] {tag}：按栏目分发成文工位")
    return {"written": "__RESET__"}


def fan_out_write(state: VisitState):
    """条件边：每个非空栏目开一个成文工位；一个栏目都没有 → 直接去质检"""
    groups = group_by_section(state["verified"])
    if not groups:
        return "qc_write"
    feedback = "；".join(state.get("write_feedback", [])) or "（无）"
    sends = []
    for sec, items in groups.items():
        sends.append(Send("write_section", {
            "section": sec,
            "facts": [{"id": fid, **fact} for fid, fact in items],
            "feedback": feedback,
        }))
    print(f"[书记员] 分发 {len(sends)} 个成文工位：" +
          "、".join(f"{s.arg['section']}({len(s.arg['facts'])}条)" for s in sends))
    return sends


def write_section_node(state: dict) -> dict:
    """成文工位：只处理自己栏目的事实（token 预算 = 栏目内事实量）"""
    sec, facts, feedback = state["section"], state["facts"], state.get("feedback", "（无）")
    result = write_chain.invoke({
        "facts_json": json.dumps(facts, ensure_ascii=False, default=str),
        "feedback": feedback,
    })
    written = [s.model_dump() for s in result.sentences]
    print(f"[书记员·{sec}] 成文 {len(written)} 句")
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
    return "write_plan" if state["qc_write"]["retry"] else "build_draft"
