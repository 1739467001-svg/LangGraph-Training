# -*- coding: utf-8 -*-
"""extraction.py —— 「抽取科」子图：调度员按栏目派两种工位

第 13 课核心：栏目 = 配置。
  用药记录 → 大模型工位（从非结构化文本抽事实，要用 LLM）
  检查所见 → 纯代码工位（医生已写好印象/建议，逐字摘录带出处，零幻觉零成本）
调度员一个 Send 列表同时派出两种工位，全部并行。
"""
import re

from langgraph.graph import StateGraph, START, END
from langgraph.types import Send

from medrecord.models import extraction_chain
from medrecord.state import (VisitState, CHUNK_SIZE, MAX_RETRY, PASS_RATE_FLOOR,
                             SECTION_DRUG, SECTION_EXAM)


def dispatch(state: VisitState):
    """调度员：返回混合 Send 列表——Send 支持指向不同节点，这就是生产线"""
    sends = []
    orders = state["orders"]
    drug_chunks = [orders[i:i + CHUNK_SIZE] for i in range(0, len(orders), CHUNK_SIZE)]
    for i, ch in enumerate(drug_chunks):
        sends.append(Send("extract_drug_chunk", {"patient_id": state["patient_id"],
                                                 "chunk_no": f"药{i}", "orders": ch}))
    exams = state["exams"]
    exam_chunks = [exams[i:i + CHUNK_SIZE] for i in range(0, len(exams), CHUNK_SIZE)]
    for i, ch in enumerate(exam_chunks):
        sends.append(Send("extract_exam_chunk", {"chunk_no": f"检{i}", "exams": ch}))
    print(f"[调度] 用药 {len(orders)} 条 → {len(drug_chunks)} 个模型工位；"
          f"检查所见 {len(exams)} 条 → {len(exam_chunks)} 个代码工位")
    return sends


def extract_drug_chunk(state: dict) -> dict:
    """大模型工位：只处理分给自己的那批医嘱"""
    chunk, no = state["orders"], state.get("chunk_no", "?")
    orders_text = "\n".join(
        f"第{o['row']}行 医嘱号{o['order_no']}：{o['text']} {o['dose']} {o['route']} {o['freq']}"
        for o in chunk
    )
    # ★ 第 16 课：第二道闸——每次调用模型前再扫一次（防御纵深）
    from medrecord.privacy_gate import scan, GateBlocked
    gate = scan(orders_text)
    if not gate["passed"]:
        raise GateBlocked(f"第二道闸拦截（{gate['total']} 处疑似），模型一次未调用")
    result = extraction_chain.invoke({"order_count": len(chunk), "orders_text": orders_text})
    facts = [{"section": SECTION_DRUG, **f.model_dump()} for f in result.facts]
    print(f"[模型工位{no}] {len(chunk)} 条 → 交货 {len(facts)} 条")
    return {"facts": facts}


def extract_exam_chunk(state: dict) -> dict:
    """代码工位：检查所见不用大模型——印象/建议是医生写的现成结论，
       抽取 = 逐字复制 + 带出处。防编造的极致形态：确定性代码，零幻觉"""
    chunk, no = state["exams"], state.get("chunk_no", "?")
    facts = []
    for e in chunk:
        if e["impression"] == "【待医生补充】":
            continue
        facts.append({
            "section": SECTION_EXAM,
            "name": e["item_name"],
            "impression": e["impression"],
            "recommendation": e["recommendation"],
            "source": f"检查结果.xlsx 第{e['row']}行 检查号{e['exam_no']}",
        })
    print(f"[代码工位{no}] {len(chunk)} 条 → 摘录 {len(facts)} 条")
    return {"facts": facts}


def _norm(s) -> str:
    s = str(s).replace(" ", "").lower()
    return re.sub(r"(\d+)\.0+(?=\D|$)", r"\1", s)


def check_dose(fact_dose: str, row_dose: str) -> bool:
    if "待医生补充" in fact_dose:
        return True
    if "待医生补充" in row_dose:
        return False
    return _norm(fact_dose) == _norm(row_dose)


def _verify_drug(f, orders_by_row, rejected, verified):
    m = re.search(r"第(\d+)行", f["source"])
    row = orders_by_row.get(int(m.group(1))) if m else None
    if row is None:
        rejected.append({**f, "reason": f"出处不存在：{f['source']}"})
    elif f["drug"] not in row["text"]:
        rejected.append({**f, "reason": f"药名与出处不符：第{row['row']}行原文『{row['text']}』"})
    elif not check_dose(f["dose"], row["dose"]):
        rejected.append({**f, "reason": f"剂量与出处不符：原文『{row['dose']}』"})
    else:
        verified.append(f)


def _verify_exam(f, exams_by_row, rejected, verified):
    m = re.search(r"第(\d+)行", f["source"])
    row = exams_by_row.get(int(m.group(1))) if m else None
    if row is None:
        rejected.append({**f, "reason": f"出处不存在：{f['source']}"})
    elif f["name"] != row["item_name"]:
        rejected.append({**f, "reason": f"项目名与出处不符：原文『{row['item_name']}』"})
    elif f["impression"] != row["impression"]:
        rejected.append({**f, "reason": f"印象与原文不符：原文『{row['impression']}』"})
    else:
        verified.append(f)


def verify_all(state: VisitState) -> dict:
    """核对闸门按栏目分科：用药走药名/剂量核对，检查所见走项目名/印象核对"""
    orders_by_row = {o["row"]: o for o in state["orders"]}
    exams_by_row = {e["row"]: e for e in state["exams"]}
    seen, verified, rejected = set(), [], []
    for f in state["facts"]:
        sec = f.get("section", SECTION_DRUG)   # 兼容旧格式：没有 section 默认按用药处理
        key = (sec, f.get("drug") or f.get("name"), f["source"])
        if key in seen:
            continue
        seen.add(key)
        if sec == SECTION_EXAM:
            _verify_exam(f, exams_by_row, rejected, verified)
        else:
            _verify_drug(f, orders_by_row, rejected, verified)

    # 重试逻辑只看用药栏目（代码工位是确定性的，重抽没有意义）
    drug_facts = [f for f in state["facts"] if f.get("section", SECTION_DRUG) == SECTION_DRUG]
    drug_ok = [f for f in verified if f.get("section", SECTION_DRUG) == SECTION_DRUG]
    rate = len(drug_ok) / len(drug_facts) if drug_facts else 1.0
    attempts = state["qc"].get("attempts", 0)
    retry = rate < PASS_RATE_FLOOR and attempts < MAX_RETRY and len(drug_facts) > 0
    if retry:
        print(f"[核对] 第{attempts + 1}轮：用药通过率 {rate:.0%} 低于 {PASS_RATE_FLOOR:.0%} → 退回重抽")
    else:
        n_exam = len([f for f in verified if f.get("section") == SECTION_EXAM])
        print(f"[核对] 用药通过 {len(drug_ok)}/{len(drug_facts)} 条（{rate:.0%}），"
              f"检查所见通过 {n_exam} 条，剔除 {len(rejected)} 条")
    return {"verified": verified, "rejected": rejected,
            "qc": {"pass_rate": rate, "retry": retry, "attempts": attempts + (1 if retry else 0)}}


def after_verify(state: VisitState):
    """子图内部的重试循环：要重试 → Send 回模型工位；完成 → 出子图"""
    if state["qc"]["retry"]:
        sends = []
        orders = state["orders"]
        chunks = [orders[i:i + CHUNK_SIZE] for i in range(0, len(orders), CHUNK_SIZE)]
        for i, ch in enumerate(chunks):
            sends.append(Send("extract_drug_chunk", {"patient_id": state["patient_id"],
                                                     "chunk_no": f"药{i}", "orders": ch}))
        return sends
    return END


def build_extraction_subgraph():
    """「抽取科」组装完毕：调度员 → 两种工位 → 核对 → （重试 | 出科）"""
    g = StateGraph(VisitState)
    g.add_node("extract_drug_chunk", extract_drug_chunk)
    g.add_node("extract_exam_chunk", extract_exam_chunk)
    g.add_node("verify_all", verify_all)
    g.add_conditional_edges(START, dispatch)         # 进科即派工
    g.add_edge("extract_drug_chunk", "verify_all")
    g.add_edge("extract_exam_chunk", "verify_all")
    g.add_conditional_edges("verify_all", after_verify)
    return g.compile()
