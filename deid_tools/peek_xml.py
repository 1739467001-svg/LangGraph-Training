# -*- coding: utf-8 -*-
"""peek_xml.py —— 看病历 XML 的编码与结构（文本全部模式化，不显示真实内容）"""
import sys, io, os, re, zipfile, glob

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
BASE = os.path.join(os.path.dirname(__file__), "..", "病历资料", "住院")

patients = sorted(d for d in os.listdir(BASE) if os.path.isdir(os.path.join(BASE, d)))
pdir = os.path.join(BASE, patients[0])
zpath = sorted(glob.glob(os.path.join(pdir, "*.zip")))[0]
print("zip:", os.path.basename(zpath)[:6], "***(文件名打码)")

zf = zipfile.ZipFile(zpath)
names = zf.namelist()
print("条目数:", len(names))


def fix_name(n):
    # 老压缩包：非UTF-8文件名被按cp437解码，需还原成GBK再转UTF-8
    try:
        return n.encode("cp437").decode("gbk")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return n


for raw in names[:3]:
    fixed = fix_name(raw)
    print("\n--- 文件名(模式化):", re.sub(r"\d", "9", re.sub(r"[\u4e00-\u9fff]", "X", fixed)))
    data = zf.read(raw)
    head = data[:200]
    m = re.search(rb'encoding="([^"]+)"', head)
    enc = m.group(1).decode() if m else "未声明"
    print("  声明编码:", enc)
    for candidate in (enc.lower(), "utf-8", "gb18030"):
        try:
            text = data.decode(candidate)
            break
        except (UnicodeDecodeError, LookupError):
            continue
    # 顶层标签名
    tags = re.findall(r"<([A-Za-z_][\w.:-]*)", text[:3000])
    print("  开头标签:", tags[:15])
    # 文本节点模式化展示（前几个非空文本）
    texts = [t.strip() for t in re.findall(r">([^<>]+)<", text) if t.strip()]
    def pat(s):
        s = re.sub(r"\d", "9", s)
        s = re.sub(r"[A-Za-z]", "a", s)
        s = re.sub(r"[\u4e00-\u9fff]", "X", s)
        return s[:60]
    for t in texts[:8]:
        print("   文本:", pat(t))
    print("  非空文本节点总数:", len(texts))
    # 找日期样式的文本
    datey = [t for t in texts if re.search(r"(20\d{2}[-/年]\s*\d{1,2}|\b20\d{12}\b)", t)]
    print("  含日期样式文本数:", len(datey), " 示例模式:", [pat(t)[:40] for t in datey[:3]])
zf.close()
