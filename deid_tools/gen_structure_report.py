# -*- coding: utf-8 -*-
"""
gen_structure_report.py —— 从「清洗后的数据」生成 docs/structure-report.md
只写结构：表名、行列数、字段清单、类型分布。不含任何单元格内容。
"""
import io
import os
import sys
import glob
from collections import Counter

from openpyxl import load_workbook

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SRC = os.path.join(ROOT, "清洗后的数据", "住院")
OUT = os.path.join(ROOT, "docs", "structure-report.md")

os.makedirs(os.path.dirname(OUT), exist_ok=True)

lines = []
lines.append("# 数据结构报告（来源：清洗后的数据）\n")
lines.append("> 由 `deid_tools/gen_structure_report.py` 自动生成，仅含表结构与字段名，不含任何单元格内容。\n")


def kind_of(v):
    if v is None:
        return "空"
    if isinstance(v, (int, float)):
        return "数值"
    return "文本"


table_desc = {
    "医嘱": "住院期间全部医嘱（长期/临时），核心表：药名 ORDER_TEXT、剂量 DOSAGE、"
           "途径 ADMINISTRATION、频次 FREQUENCY、起止时间 START/STOP_DATE_TIME、开单医生 DOCTOR",
    "检验申请主表": "检验申请单头信息：申请医生、标本、检验目的、联系人字段（多为空）",
    "检验申请项目": "每张检验申请单里的具体项目（TEST_NO 关联主表）",
    "检验结果": "检验结果明细：项目名、结果值、单位、异常标志、报告时间（TEST_NO 关联）",
    "检查申请主表": "检查（影像/病理等）申请单头：检查类别、申请医生、执行与报告时间",
    "检查申请项目": "每张检查申请单的具体项目（EXAM_NO 关联主表）",
    "检查结果": "检查报告：所见 DESCRIPTION、印象 IMPRESSION、建议 RECOMMENDATION（EXAM_NO 关联）",
    "生命体征": "护理记录的体征：体征名、值、单位、记录时间与护士",
}

patients = sorted(p for p in os.listdir(SRC) if os.path.isdir(os.path.join(SRC, p)))
first = patients[0]
seen_tables = {}

for pdir in patients:
    lines.append(f"\n## {pdir}\n")
    for f in sorted(glob.glob(os.path.join(SRC, pdir, "*.xlsx"))):
        name = os.path.basename(f).replace(".xlsx", "")
        sh = load_workbook(f, read_only=True).active
        rows, cols = sh.max_row - 1, sh.max_column
        headers = [c.value for c in next(sh.iter_rows(min_row=1, max_row=1))]
        kinds = Counter()
        for row in sh.iter_rows(min_row=2, values_only=True):
            pass
        sh2 = load_workbook(f, read_only=True).active
        for row in sh2.iter_rows(min_row=2, values_only=True):
            for v in row:
                kinds[kind_of(v)] += 1
        if name not in seen_tables:
            seen_tables[name] = (headers, rows, cols, dict(kinds))
        lines.append(f"- **{name}.xlsx**：{rows} 行 × {cols} 列")

lines.append("\n\n## 各表字段清单\n")
for name, (headers, rows, cols, kinds) in seen_tables.items():
    lines.append(f"\n### {name}（{rows} 行 × {cols} 列，取自 {first}）\n")
    if name in table_desc:
        lines.append(f"{table_desc[name]}\n")
    lines.append("字段：" + "、".join(h for h in headers if h) + "\n")
    lines.append(f"单元格类型分布：{kinds}\n")

lines.append("\n## 关联关系\n")
lines.append("- `PATIENT_ID`：患者唯一标识，8 张表都有（已脱敏为 FAKE0001~0005）")
lines.append("- `医嘱.TEST/EXAM 无关`：医嘱表独立，靠 PATIENT_ID + 时间与用药记录对照")
lines.append("- `TEST_NO`：检验申请主表 → 检验申请项目 → 检验结果")
lines.append("- `EXAM_NO`：检查申请主表 → 检查申请项目 → 检查结果")
lines.append("- XML 病历文书在每人目录的 zip 包内（文件名 = 文书类型_患者_编号.xml）\n")

with open(OUT, "w", encoding="utf-8") as fp:
    fp.write("\n".join(lines))
print(f"已生成 {OUT}（{len(seen_tables)} 张表）")
