# -*- coding: utf-8 -*-
"""privacy_gate.py —— 出口闸门：数据在发给任何模型之前过安检

对应 donge-hospital-ai tech-spec 原则 6 与第 9 节：
  两道闸：流程开头一次；每次调用模型前一次。
  命中疑似身份信息 → 拦截，一次模型调用都不许发生。
  报警信息本身也打码——安检员喊「发现手机号」，不喊号码。

禁用词表（真实姓名/医护姓名）本身就是敏感数据：
  从 deid_tools/mapping_真实值勿提交.json 读取（本地受控、git 忽略），
  文件被删了闸门照常工作（模式类检测不依赖词表）。
"""
import json
import os
import re

from medrecord.state import ROOT

# —— 模式库（与脱敏工具箱同源：同一套正则，两个岗位使用）——
PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
IDCARD = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
LANDLINE = re.compile(r"(?<!\d)0\d{2,3}-\d{7,8}(?!\d)")
LONGNUM = re.compile(r"(?<!\d)\d{11,}(?!\d)")   # 其余长编号疑似（排除上面已报的区间）

MAPPING_PATH = os.path.join(ROOT, "deid_tools", "mapping_真实值勿提交.json")
_gate = {"banned_names": set(), "loaded": False}


class GateBlocked(RuntimeError):
    """第二道闸：工位内拦截，模型一次都不许调用"""


def configure_gate(banned_names=None):
    """显式配置禁用词表；不传则尝试从本地映射文件读取真实姓名（只在本地）"""
    if banned_names is None and not _gate["loaded"]:
        banned_names = _load_from_mapping()
    if banned_names is not None:
        _gate["banned_names"] = set(banned_names)
    _gate["loaded"] = True


def _load_from_mapping():
    if not os.path.exists(MAPPING_PATH):
        return []
    try:
        with open(MAPPING_PATH, encoding="utf-8") as f:
            m = json.load(f)
        # 映射表的 key 才是真实值（患者/医护），value 是假名
        return list(m.get("患者", {}).keys()) + list(m.get("医护姓名", {}).keys())
    except (json.JSONDecodeError, OSError):
        return []


def _mask(s: str) -> str:
    return (s[:3] + "****" + s[-2:]) if len(s) > 6 else "****"


def scan(text: str, banned_names=None) -> dict:
    """扫描文本，返回 {'passed': bool, 'total': n, 'hits': [...]}——
       hits 里的预览全部打码，报警单不泄露它拦下的东西"""
    hits, covered = [], []

    def record(kind, m):
        hits.append({"type": kind, "pos": m.start(),
                     "preview": _mask(m.group())})
        covered.append((m.start(), m.end()))

    for m in PHONE.finditer(text):
        record("手机号", m)
    for m in IDCARD.finditer(text):
        record("身份证号", m)
    for m in LANDLINE.finditer(text):
        record("座机号", m)
    for m in LONGNUM.finditer(text):
        if any(s <= m.start() < e for s, e in covered):
            continue   # 已被手机号/身份证覆盖的区间不重复报警
        record("长编号疑似", m)

    names = set(banned_names if banned_names is not None else _gate["banned_names"])
    for name in names:
        for m in re.finditer(re.escape(name), text):
            hits.append({"type": "禁用姓名", "pos": m.start(),
                         "preview": name[0] + "**"})
    hits.sort(key=lambda h: h["pos"])
    return {"passed": not hits, "total": len(hits), "hits": hits[:20]}


def gate_node(state: dict) -> dict:
    """第一道闸节点：流程开头，把要进流程的全部文本扫一遍（三栏目）"""
    configure_gate()
    text = "\n".join(o["text"] for o in state["orders"])
    text += "\n" + "\n".join(e["item_name"] + e["impression"] for e in state["exams"])
    text += "\n" + "\n".join(l["name"] + l["value"] for l in state.get("labs", []))
    result = scan(text)
    if result["passed"]:
        print("[出口闸门①] 放行：未发现疑似身份信息")
    else:
        print(f"[出口闸门①] ⛔ 拦截：发现 {result['total']} 处疑似身份信息（预览已打码）")
    return {"gate": result}


def route_after_gate(state: dict) -> str:
    """命中 → 去拦截终点（模型零调用）；干净 → 进抽取科"""
    return "extraction" if state["gate"]["passed"] else "gate_blocked"


def gate_blocked(state: dict) -> dict:
    lines = ["⛔ 出口闸门拦截：内容含疑似身份信息，已停止，未调用任何模型。",
             f"命中 {state['gate']['total']} 处（预览已打码）：",
             *[f"  · {h['type']} @ 位置{h['pos']}：{h['preview']}" for h in state["gate"]["hits"]],
             "处理：回到脱敏环节复查，或修正禁用词表后重跑。"]
    return {"draft": "\n".join(lines)}
