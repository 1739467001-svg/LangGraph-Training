# -*- coding: utf-8 -*-
"""pipeline_nodes.py —— 抽取科之外的所有节点：读表、拼草稿、医生审核、导出与评估

来自 lesson07，一字未改逻辑，只搬了家。每个节点仍然只做一件事、只返回要更新的字段。
"""
import glob
import os
import re
import zipfile
from datetime import datetime

from langgraph.types import interrupt
from openpyxl import load_workbook

from medrecord.state import DATA_DIR, OUT_DIR
from medrecord.models import extraction_chain
from medrecord.extraction import check_dose  # noqa: F401  评估模块要用（保留导入路径稳定）


def load_orders(state) -> dict:
    path = os.path.join(DATA_DIR, state["folder"], "医嘱.xlsx")
    ws = load_workbook(path, read_only=True).active
    rows = ws.iter_rows(values_only=True)
    headers = [str(h) if h else "" for h in next(rows)]
    idx = {h: i for i, h in enumerate(headers)}

    orders, patient_id = [], state["patient_id"]
    for row_no, row in enumerate(rows, start=2):
        text = row[idx["ORDER_TEXT"]]
        if patient_id.startswith("测试") and row[idx["PATIENT_ID"]]:
            patient_id = str(row[idx["PATIENT_ID"]])
        if not text:
            continue
        dose = " ".join(str(x) for x in (row[idx["DOSAGE"]], row[idx["DOSAGE_UNITS"]]) if x)
        orders.append({
            "row": row_no, "order_no": row[idx["ORDER_NO"]], "text": str(text),
            "dose": dose or "【待医生补充】",
            "route": row[idx["ADMINISTRATION"]] or "【待医生补充】",
            "freq": row[idx["FREQUENCY"]] or "【待医生补充】",
        })

    # —— 检查所见的数据源：检查结果 × 检查申请项目（EXAM_NO 联表，第 13 课）——
    # 结果表里没有项目名，要拿申请项目表按检查号补上——病历数据工程的家常便饭
    exams = []
    exams_path = os.path.join(DATA_DIR, state["folder"], "检查结果.xlsx")
    items_path = os.path.join(DATA_DIR, state["folder"], "检查申请项目.xlsx")
    if os.path.exists(exams_path) and os.path.exists(items_path):
        res_ws = load_workbook(exams_path, read_only=True).active
        res_rows = res_ws.iter_rows(values_only=True)
        rh = [str(h) if h else "" for h in next(res_rows)]
        ridx = {h: i for i, h in enumerate(rh)}
        item_ws = load_workbook(items_path, read_only=True).active
        item_rows = item_ws.iter_rows(values_only=True)
        ih = [str(h) if h else "" for h in next(item_rows)]
        iidx = {h: i for i, h in enumerate(ih)}
        name_of = {}
        for r in item_rows:
            no = r[iidx["EXAM_NO"]]
            if no is not None and r[iidx["EXAM_ITEM"]]:
                name_of.setdefault(no, []).append(str(r[iidx["EXAM_ITEM"]]))
        for row_no, r in enumerate(res_rows, start=2):
            no = r[ridx["EXAM_NO"]]
            if no is None:
                continue
            imp = r[ridx["IMPRESSION"]]
            rec = r[ridx["RECOMMENDATION"]]
            exams.append({
                "row": row_no, "exam_no": str(no),
                "item_name": "、".join(name_of.get(no, ["未知项目"])),
                "impression": str(imp) if imp else "【待医生补充】",
                "recommendation": str(rec) if rec else "【待医生补充】",
            })
    # —— 检验所见的数据源：检验结果表（第 17 课，自带项目名和结果值，无需联表）——
    labs = []
    labs_path = os.path.join(DATA_DIR, state["folder"], "检验结果.xlsx")
    if os.path.exists(labs_path):
        lab_ws = load_workbook(labs_path, read_only=True).active
        lab_rows = lab_ws.iter_rows(values_only=True)
        lh = [str(h) if h else "" for h in next(lab_rows)]
        lidx = {h: i for i, h in enumerate(lh)}
        for row_no, r in enumerate(lab_rows, start=2):
            val = r[lidx["RESULT"]]
            name = r[lidx["REPORT_ITEM_NAME"]]
            if r[lidx["TEST_NO"]] is None:
                continue
            labs.append({
                "row": row_no, "test_no": str(r[lidx["TEST_NO"]]),
                "name": str(name) if name else "未知项目",
                "value": str(val) if val not in (None, "") else "【待医生补充】",
                "units": str(r[lidx["UNITS"]]) if r[lidx["UNITS"]] else "",
            })
    return {"orders": orders, "exams": exams, "labs": labs,
            "patient_id": patient_id, "folder": state["folder"]}


def build_draft_node(state) -> dict:
    drug_lines, exam_lines, lab_lines = [], [], []
    for f in state["verified"]:
        if f["section"] == "检查所见":
            exam_lines.append(f"· {f['name']}｜印象：{f['impression']}"
                              f"｜建议：{f['recommendation']}｜出处：{f['source']}")
        elif f["section"] == "检验所见":
            units = f" {f['units']}" if f.get("units") else ""
            lab_lines.append(f"· {f['name']}：{f['value']}{units}｜出处：{f['source']}")
        else:
            drug_lines.append(f"{len(drug_lines) + 1}. {f['drug']} | {f['dose']} | {f['route']}"
                              f" | 频次:{f['freq']} | 出处：{f['source']}")
    lines = [f"病历文书草稿：{state['patient_id']}"
             f"（用药 {len(drug_lines)} 条 + 检查所见 {len(exam_lines)} 条 + "
             f"检验所见 {len(lab_lines)} 条，均核对通过）"]
    lines.append("【用药记录】")
    lines.extend(drug_lines)
    lines.append("【检查所见】")
    lines.extend(exam_lines)
    lines.append("【检验所见】")
    lines.extend(lab_lines)
    if state["rejected"]:
        lines.append("剔除（未进入草稿）：")
        for f in state["rejected"]:
            label = f.get("drug") or f.get("name")
            lines.append(f"  · [{f['section']}] {label} —— {f['reason']}")
    return {"draft": "\n".join(lines)}


def doctor_review(state) -> dict:
    decision = interrupt({"question": "请审核以下用药草稿（批准 / 驳回）", "draft": state["draft"]})
    print(f"[审核] 医生态度：{decision}")
    return {"review": {"decision": str(decision), "reviewer": "医生（终端）"}}


def route_after_review(state) -> str:
    return "finalize" if "批准" in state["review"]["decision"] else "archive"


def finalize(state) -> dict:
    lines = ["═══ 正式用药记录（已通过医生审核）═══", state["draft"],
             f"审核记录：{state['review']['reviewer']} · 结论 {state['review']['decision']}"]
    return {"draft": "\n".join(lines)}


def archive(state) -> dict:
    lines = ["═══ 未过审草稿（已归档）═══", state["draft"],
             f"审核记录：{state['review']['reviewer']} · 结论 {state['review']['decision']}",
             "⚠ 该稿未经医生确认，不得作为正式病历内容使用。"]
    return {"draft": "\n".join(lines)}


# ---------- 评估（第 7 课）----------
def load_standard_drugs(folder: str) -> list:
    drugs = []
    for zp in sorted(glob.glob(os.path.join(DATA_DIR, folder, "*.zip"))):
        z = zipfile.ZipFile(zp)
        for n in z.namelist():
            m = re.match(r"用药记录_(.+?)用药记录_\d+\.xml", n)
            if m:
                drugs.append(m.group(1))
        z.close()
    return drugs


def norm_name(s: str) -> str:
    return re.sub(r"（[^）]*）|\([^)]*\)|\s", "", str(s))


def match(a: str, b: str) -> bool:
    na, nb = norm_name(a), norm_name(b)
    return na in nb or nb in na


def evaluate(state) -> dict:
    std_all = load_standard_drugs(state["folder"])
    std_unique = set(std_all)
    drug_facts = [f for f in state["verified"] if f["section"] == "用药记录"]
    exam_facts = [f for f in state["verified"] if f["section"] == "检查所见"]
    draft = [f["drug"] for f in drug_facts]
    matched_std = {s for s in std_unique if any(match(d, s) for d in draft)}
    hits = [d for d in draft if any(match(d, s) for s in std_unique)]
    extra = [d for d in draft if d not in hits]
    coverage = (len(matched_std) / len(std_unique)) if std_unique else 0.0

    # 检查所见/检验所见（第 17 课）的自覆盖率：有结论的行里，多少被摘录进草稿
    exam_total = sum(1 for e in state["exams"] if e["impression"] != "【待医生补充】")
    exam_rows_done = {re.search(r"第(\d+)行", f["source"]).group(1)
                      for f in exam_facts if re.search(r"第(\d+)行", f["source"])}
    exam_cov = (len(exam_rows_done) / exam_total) if exam_total else 0.0
    lab_facts = [f for f in state["verified"] if f["section"] == "检验所见"]
    lab_total = sum(1 for l in state["labs"] if l["value"] != "【待医生补充】")
    lab_rows_done = {re.search(r"第(\d+)行", f["source"]).group(1)
                     for f in lab_facts if re.search(r"第(\d+)行", f["source"])}
    lab_cov = (len(lab_rows_done) / lab_total) if lab_total else 0.0

    lines = [
        "═══ 评估报告（只含统计，不含病历内容）═══",
        f"用药记录：草稿 {len(drug_facts)} 条；标准答案 {len(std_all)} 份（去重 {len(std_unique)} 种）",
        f"  覆盖：命中 {len(hits)} 条，覆盖 {len(matched_std)}/{len(std_unique)} 种 → 覆盖率 {coverage:.0%}",
        f"  多报：{len(extra)} 条（草稿有、标准答案没有——交给医生判断，"
        f"常见原因是普通溶媒/辅助用药不单独写用药记录）",
        f"检查所见：草稿 {len(exam_facts)} 条，自覆盖率 {exam_cov:.0%}"
        f"（{len(exam_rows_done)}/{exam_total} 行有印象的检查）",
        f"检验所见：草稿 {len(lab_facts)} 条，自覆盖率 {lab_cov:.0%}"
        f"（{len(lab_rows_done)}/{lab_total} 行有结果的检验）",
        f"编造：{len(state['rejected'])} 条（核对剔除，三栏目合计）",
    ]
    for d in sorted(set(hits)):
        lines.append(f"  ✓ 用药命中：{d}")
    for s in sorted(std_unique - matched_std):
        lines.append(f"  ✗ 标准答案未覆盖：{s}")
    return {"evaluation": "\n".join(lines)}


def export_eval(state) -> dict:
    status = "已过审" if "批准" in state["review"]["decision"] else "未过审"
    drug_records, exam_records, lab_records = [], [], []
    for f in state["verified"]:
        if f["section"] == "检查所见":
            exam_records.append(
                f"  <检查所见 patient=\"{state['patient_id']}\" status=\"{status}\">\n"
                f"    <项目>{f['name']}</项目>\n"
                f"    <印象>{f['impression']}</印象>\n"
                f"    <建议>{f['recommendation']}</建议>\n"
                f"    <出处>{f['source']}</出处>\n"
                f"    <审核 医生=\"{state['review']['reviewer']}\" 结论=\"{state['review']['decision']}\"/>\n"
                f"  </检查所见>"
            )
        elif f["section"] == "检验所见":
            lab_records.append(
                f"  <检验所见 patient=\"{state['patient_id']}\" status=\"{status}\">\n"
                f"    <项目>{f['name']}</项目>\n"
                f"    <结果>{f['value']}</结果>\n"
                f"    <单位>{f.get('units', '')}</单位>\n"
                f"    <出处>{f['source']}</出处>\n"
                f"    <审核 医生=\"{state['review']['reviewer']}\" 结论=\"{state['review']['decision']}\"/>\n"
                f"  </检验所见>"
            )
        else:
            drug_records.append(
                f"  <用药记录 patient=\"{state['patient_id']}\" status=\"{status}\">\n"
                f"    <药品>{f['drug']}</药品>\n"
                f"    <剂量>{f['dose']}</剂量>\n"
                f"    <途径>{f['route']}</途径>\n"
                f"    <频次>{f['freq']}</频次>\n"
                f"    <出处>{f['source']}</出处>\n"
                f"    <审核 医生=\"{state['review']['reviewer']}\" 结论=\"{state['review']['decision']}\"/>\n"
                f"  </用药记录>"
            )
    xml = ("<?xml version=\"1.0\" encoding=\"utf-8\"?>\n"
           f"<病历文书草稿 generated=\"{datetime.now().isoformat(timespec='seconds')}\">\n"
           + "\n".join(drug_records + exam_records + lab_records) + "\n</病历文书草稿>\n")
    os.makedirs(OUT_DIR, exist_ok=True)
    xml_path = os.path.join(OUT_DIR, f"lesson13_{state['patient_id']}_文书草稿.xml")
    with open(xml_path, "w", encoding="utf-8") as fp:
        fp.write(xml)
    print(f"[导出] XML → {xml_path}")
    return evaluate(state)
