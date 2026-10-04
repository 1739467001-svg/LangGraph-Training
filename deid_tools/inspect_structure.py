# -*- coding: utf-8 -*-
"""
inspect_structure.py —— 第一步：只看「结构」，不看「内容」
打印每张表：sheet 名、表头行、每列的单元格类型分布。
患者姓名、编号等具体值一律打码（只显示类型和长度），
避免真实患者信息出现在终端记录里。
"""
import sys, io, re, glob, os
import xlrd

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

BASE = os.path.join(os.path.dirname(__file__), "..", "病历资料", "住院")

# xlrd 单元格类型: 0空 1文本 2数字 3日期 4布尔 5错误 6空白
TYPE_NAME = {0: "空", 1: "文本", 2: "数字", 3: "日期", 4: "布尔", 5: "错误", 6: "空白"}


def mask(v, t):
    """把具体值打码成类型描述，防止患者信息泄露到终端"""
    if t == 0:
        return ""
    if t == 2:  # 数字：只看位数/小数
        s = str(v)
        return f"<数:{len(s)}位>"
    if t == 3:
        return "<日期>"
    s = str(v)
    if re.search(r"[\u4e00-\u9fff]{2,4}(?<!症)(?<!炎)", s) and len(s) <= 4 and re.fullmatch(r"[\u4e00-\u9fff]{2,4}", s):
        return f"<疑似人名:{len(s)}字>"
    if re.fullmatch(r"\d{6,}", s):
        return f"<长数字:{len(s)}位>"
    if re.search(r"1[3-9]\d{9}", s):
        return "<含手机号>"
    if len(s) > 20:
        return f"<长文本:{len(s)}字>"
    return s[:20]  # 短的非敏感值（如"静脉滴注"）原样显示，便于理解表结构


def main():
    patients = sorted(os.listdir(BASE))
    print(f"患者文件夹数: {len(patients)}")
    for p in patients:
        pdir = os.path.join(BASE, p)
        if not os.path.isdir(pdir):
            continue
        print(f"\n{'='*60}\n患者文件夹: {p[:1]}**（已打码）  文件数: {len(os.listdir(pdir))}")
        for f in sorted(glob.glob(os.path.join(pdir, "*.xls"))):
            fname = os.path.basename(f)
            try:
                book = xlrd.open_workbook(f)
            except Exception as e:
                print(f"  [打开失败] {fname}: {e}")
                continue
            for sh in book.sheets():
                print(f"  -- {fname} | sheet「{sh.name}」 {sh.nrows}行 x {sh.ncols}列")
                # 找表头：打印前2行（打码）
                for r in range(min(2, sh.nrows)):
                    cells = []
                    for c in range(sh.ncols):
                        t = sh.cell_type(r, c)
                        v = sh.cell_value(r, c)
                        cells.append(f"{mask(v, t)}")
                    print(f"     行{r}: {' | '.join(cells)}")
                # 每列类型分布（用前50行采样）
                if sh.nrows > 2:
                    dist = {}
                    for c in range(sh.ncols):
                        types = [TYPE_NAME.get(sh.cell_type(r, c), "?") for r in range(2, min(sh.nrows, 50))]
                        dist[c] = "/".join(sorted(set(types)))
                    print(f"     各列数据类型(采样): {dist}")


if __name__ == "__main__":
    main()
