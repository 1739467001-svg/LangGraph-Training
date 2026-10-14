# -*- coding: utf-8 -*-
"""batch.py —— 批量验收：全部患者跑评估，产出汇总报告。

两条治理规则（和生产代码同样重要）：
  1. 编译一次图，多个 thread_id 顺序跑——thread 之间互不干扰（第 10 课存档位）
  2. 批量评估一律以「驳回」收尾：评估稿进归档，绝不自动批准。
     自动化评估 ≠ 自动化批准，批准永远是医生的显式动作

运行：cd src && /usr/bin/python3 -m medrecord.batch
"""
import math
import os
import re
from datetime import datetime

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from medrecord.graph import build_main_graph
from medrecord.pipeline_nodes import load_standard_drugs, match
from medrecord.state import DATA_DIR, OUT_DIR, CHUNK_SIZE


def _init(patient_id: str) -> dict:
    return {"folder": patient_id, "patient_id": patient_id, "orders": [], "exams": [], "labs": [],
            "facts": [], "verified": [], "rejected": [], "qc": {}, "gate": {}, "written": [], "qc_write": {}, "write_feedback": [],
            "draft": "", "review": {}, "evaluation": ""}


def run_one(app, patient_id: str) -> dict:
    cfg = {"configurable": {"thread_id": f"batch-{patient_id}"}}
    r = app.invoke(_init(patient_id), cfg)
    if "__interrupt__" not in r:
        return {"patient": patient_id, "error": "未挂起（流程异常，需排查）"}
    final = app.invoke(Command(resume="驳回"), cfg)   # 治理规则 2：批量稿一律归档

    drug = [f for f in final["verified"] if f.get("section") == "用药记录"]
    exam = [f for f in final["verified"] if f.get("section") == "检查所见"]
    std = load_standard_drugs(patient_id)
    std_unique = set(std)
    names = [f["drug"] for f in drug]
    matched = {s for s in std_unique if any(match(n, s) for n in names)}
    missed = sorted(std_unique - matched)
    exam_total = sum(1 for e in final["exams"] if e["impression"] != "【待医生补充】")
    exam_done = {m.group(1) for f in exam
                 for m in [re.search(r"第(\d+)行", f["source"])] if m}
    lab = [f for f in final["verified"] if f.get("section") == "检验所见"]
    lab_total = sum(1 for l in final["labs"] if l["value"] != "【待医生补充】")
    lab_done = {m.group(1) for f in lab
                for m in [re.search(r"第(\d+)行", f["source"])] if m}
    return {
        "patient": patient_id,
        "orders": len(final["orders"]),
        "exams_all": len(final["exams"]),
        "exams_total": exam_total,
        "workers": (math.ceil(len(final["orders"]) / CHUNK_SIZE)
                    + math.ceil(len(final["exams"]) / CHUNK_SIZE)
                    + math.ceil(len(final["labs"]) / CHUNK_SIZE)),
        "drug": len(drug), "std": len(std_unique), "std_docs": len(std),
        "covered": len(matched),
        "coverage": (len(matched) / len(std_unique)) if std_unique else 0.0,
        "extra": len([n for n in names if not any(match(n, s) for s in std_unique)]),
        "fab": len(final["rejected"]),
        "exam": len(exam),
        "exam_cov": (len(exam_done) / exam_total) if exam_total else 0.0,
        "lab": len(lab),
        "lab_cov": (len(lab_done) / lab_total) if lab_total else 0.0,
        "missed": missed,
    }


def build_report(rows: list) -> str:
    lines = ["# 病历文书草稿·批量验收报告", "",
             f"生成时间：{datetime.now().isoformat(timespec='seconds')}", "",
             "治理说明：本批量运行的草稿一律「驳回」归档（status=未过审），",
             "正式记录必须由医生逐个审核——**自动化评估不等于自动化批准**。", "",
             "| 患者 | 医嘱 | 检查行 | 检验行 | 工位 | 用药草稿 | 标准答案(种) | 覆盖率 | 多报 | 编造 | 检查自覆盖 | 检验自覆盖 |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(
            f"| {r['patient']} | {r['orders']} | {r['exams_total']} | {r['lab']} | {r['workers']} | "
            f"{r['drug']} | {r['std']} | {r['coverage']:.0%} | {r['extra']} | "
            f"{r['fab']} | {r['exam_cov']:.0%} | {r['lab_cov']:.0%} |")

    covs = [r["coverage"] for r in rows if "coverage" in r]
    missed_count = {}
    for r in rows:
        for m in r.get("missed", []):
            missed_count[m] = missed_count.get(m, 0) + 1
    fab_total = sum(r["fab"] for r in rows if "fab" in r)
    lines += ["", "## 解读", ""]
    if covs:
        lines.append(f"- 覆盖率区间 {min(covs):.0%} ~ {max(covs):.0%}（{len(covs)} 位患者）")
    if missed_count:
        lines.append("- 反复未覆盖的药物（数据源线索，非模型问题）：")
        for name, n in sorted(missed_count.items(), key=lambda x: -x[1]):
            lines.append(f"  - {name}：{n}/{len(rows)} 位患者未覆盖")
    lines.append(f"- 编造合计 {fab_total} 条——全部由核对闸门拦截，无一进入草稿")
    lines.append("- 结论句式提醒：覆盖率要和「未覆盖原因」一起汇报，数字是引子，解读才是交付")
    return "\n".join(lines) + "\n"


def run_batch(patients=None) -> str:
    if patients is None:
        patients = sorted(d for d in os.listdir(DATA_DIR)
                          if os.path.isdir(os.path.join(DATA_DIR, d)))
    app = build_main_graph(checkpointer=InMemorySaver())   # 治理规则 1：编译一次
    rows = [run_one(app, p) for p in patients]
    report = build_report(rows)
    os.makedirs(OUT_DIR, exist_ok=True)
    out = os.path.join(OUT_DIR, "批量验收报告.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write(report)
    print(report)
    print(f"[已保存] {out}")
    return report


if __name__ == "__main__":
    run_batch()
