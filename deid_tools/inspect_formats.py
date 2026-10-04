# -*- coding: utf-8 -*-
"""
inspect_formats.py —— 看关键列的「值格式」而不泄露值
把每个字符归类：数字→9，字母→a，汉字→X，其他原样。
这样能看出编号是几位、日期长什么样，但看不出真实内容。
"""
import sys, io, os, re, glob
import xlrd

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
BASE = os.path.join(os.path.dirname(__file__), "..", "病历资料", "住院")


def pattern(v):
    s = str(v)
    s = re.sub(r"\d", "9", s)
    s = re.sub(r"[A-Za-z]", "a", s)
    s = re.sub(r"[\u4e00-\u9fff]", "X", s)
    return s[:40]


# 关注列：编号类 + 联系方式类（值打码）
KEY_COLS = {
    "PATIENT_ID", "VISIT_ID", "EXAM_NO", "TEST_NO", "RCPT_NO", "CLINIC_NO",
    "WORKING_ID", "PIS_NO", "ACC_NO", "STUDY_UID", "CURRENT_PRESC_NO",
    "CARE_ID_NO", "CARE_TELEPHONE", "PHONE", "CLINIC_PRE_ADDRESS", "CARE_NAME",
    "STORAGE_CODE", "EMR_ID", "APP_NO",
}

# 只看一位患者的表（各表结构相同），这里取第一个患者目录
patients = sorted(d for d in os.listdir(BASE) if os.path.isdir(os.path.join(BASE, d)))
pdir = os.path.join(BASE, patients[0])

for f in sorted(glob.glob(os.path.join(pdir, "*.xls"))):
    fname = os.path.basename(f)
    book = xlrd.open_workbook(f)
    sh = book.sheet_by_index(0)
    headers = [str(sh.cell_value(0, c)) for c in range(sh.ncols)]
    print(f"\n=== {fname} ({sh.nrows}行) ===")
    for c, h in enumerate(headers):
        if h in KEY_COLS:
            samples = []
            for r in range(1, min(sh.nrows, 60)):
                t, v = sh.cell_type(r, c), sh.cell_value(r, c)
                if t != 0 and str(v).strip():
                    samples.append(pattern(v))
                    if len(samples) >= 3:
                        break
            if samples:
                print(f"  {h:24s} -> {' / '.join(samples)}")
            else:
                print(f"  {h:24s} -> (无数据)")

# 顺带看：医嘱表里那几个被打码的长表头到底是什么（表头是字段名，不是患者数据）
print("\n=== 各表全部列名（字段名非患者数据，原样显示） ===")
seen = set()
for f in sorted(glob.glob(os.path.join(pdir, "*.xls"))):
    book = xlrd.open_workbook(f)
    sh = book.sheet_by_index(0)
    headers = tuple(str(sh.cell_value(0, c)) for c in range(sh.ncols))
    key = (os.path.basename(f), headers[:5])
    if headers in seen:
        continue
    seen.add(headers)
    long_cn = [h for h in headers if len(h) > 8]
    if long_cn:
        print(f"  {os.path.basename(f)} 长字段名: {long_cn}")
