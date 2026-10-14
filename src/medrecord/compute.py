# -*- coding: utf-8 -*-
"""compute.py —— 「计算器」：算术交给代码，不靠心算（tech-spec 原则 3）

住院天数、最高体温、发热天数这类事，模型一个都不许算——
代码算出来的叫「计算事实」，每条写明依据（哪段区间/哪一行），
和抽取事实一样带着出处进草稿。

设计：纯函数（可单测）+ 节点（进图）分开。
"""
from datetime import datetime

FEVER_THRESHOLD = 37.3   # 发热阈值（℃）——医院口径若不同，只改这一个常量


def compute_stays(vitals: list) -> list:
    """按「入院/出院」事件行配对，算每次住院的天数。
       天数口径：出院日 − 入院日（不含出院当天）；如需「住几晚」等口径改这里"""
    admissions = sorted(v["date"] for v in vitals if v["name"] == "入院" and v["date"])
    discharges = sorted(v["date"] for v in vitals if v["name"] == "出院" and v["date"])
    stays = []
    for adm, dis in zip(admissions, discharges):
        if isinstance(adm, str):
            adm = datetime.fromisoformat(adm)
        if isinstance(dis, str):
            dis = datetime.fromisoformat(dis)
        if dis >= adm:
            stays.append({"start": adm, "end": dis, "days": (dis - adm).days})
    return stays


def _as_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def compute_max_temp(vitals: list):
    """最高腋下体温：值 + 日期 + 出处行号；无体温记录返回 None"""
    best = None
    for v in vitals:
        if v["name"] != "腋下体温":
            continue
        val = _as_float(v["value"])
        if val is None:
            continue
        if best is None or val > best["value"]:
            best = {"value": val, "date": v["date"], "row": v["row"], "units": v["units"]}
    return best


def compute_fever(vitals: list, threshold: float = FEVER_THRESHOLD) -> dict:
    """发热统计：≥阈值的记录次数 + 涉及的不同日期数"""
    records = 0
    dates = set()
    for v in vitals:
        if v["name"] != "腋下体温":
            continue
        val = _as_float(v["value"])
        if val is not None and val >= threshold:
            records += 1
            if v["date"]:
                dates.add(str(v["date"])[:10])
    return {"records": records, "days": len(dates)}


def compute_node(state: dict) -> dict:
    """节点：把全部计算事实装进 state.derived，每条带依据和出处"""
    vitals = state.get("vitals", [])
    derived = []

    stays = compute_stays(vitals)
    for i, s in enumerate(stays, 1):
        derived.append({
            "label": f"第{i}次住院天数",
            "value": f"{s['days']} 天",
            "basis": f"{s['start']:%Y-%m-%d} 入院 → {s['end']:%Y-%m-%d} 出院",
            "source": "生命体征.xlsx 入院/出院事件行",
        })

    mt = compute_max_temp(vitals)
    if mt:
        date_txt = f"{mt['date']:%Y-%m-%d}" if hasattr(mt["date"], "strftime") else str(mt["date"])[:10]
        derived.append({
            "label": "最高腋下体温",
            "value": f"{mt['value']}{mt['units']}",
            "basis": f"出现于 {date_txt}",
            "source": f"生命体征.xlsx 第{mt['row']}行",
        })
        fever = compute_fever(vitals)
        derived.append({
            "label": f"发热（≥{FEVER_THRESHOLD}℃）",
            "value": f"{fever['days']} 天 / {fever['records']} 次记录",
            "basis": "按腋下体温逐条比对阈值统计",
            "source": "生命体征.xlsx 腋下体温全部记录",
        })

    print(f"[计算器] 产出 {len(derived)} 条计算事实（全部由代码算出，模型未参与）")
    return {"derived": derived}
