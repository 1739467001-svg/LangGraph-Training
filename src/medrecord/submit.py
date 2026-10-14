# -*- coding: utf-8 -*-
"""submit.py —— 「护士站」：启动流程，走到医生审核站挂起，然后下班。

和 InMemorySaver 的本质区别：本进程退出后，存档还在磁盘上。
明天、另一台机器上的「医生站」（review.py）凭同一个 thread_id 接单。

运行：/usr/bin/python3 -m medrecord.submit 测试患者乙
"""
import os
import sqlite3
import sys

from langgraph.checkpoint.sqlite import SqliteSaver

from medrecord.graph import build_main_graph
from medrecord.state import CHECKPOINT_DB, OUT_DIR, thread_id


def main():
    patient_id = sys.argv[1] if len(sys.argv) > 1 else "测试患者乙"

    os.makedirs(OUT_DIR, exist_ok=True)
    conn = sqlite3.connect(CHECKPOINT_DB, check_same_thread=False)
    app = build_main_graph(checkpointer=SqliteSaver(conn))
    cfg = {"configurable": {"thread_id": thread_id(patient_id)}}

    final = None
    for update in app.stream(
        {"folder": patient_id, "patient_id": patient_id, "orders": [], "exams": [], "labs": [], "vitals": [], "derived": [], "facts": [],
         "verified": [], "rejected": [], "qc": {}, "gate": {}, "written": [], "qc_write": {}, "write_feedback": [], "draft": "", "review": {}, "evaluation": ""},
        cfg, stream_mode="updates",
    ):
        for name in update:
            print(f"[节拍] {'★ 挂起于' if name == '__interrupt__' else '完成：'}{name}")
        final = update

    snap = app.get_state(cfg)
    if snap.next:
        print(f"\n[下班] 流程挂在 {snap.next}，草稿已存档：{CHECKPOINT_DB}")
        print(f"[下班] thread_id = {thread_id(patient_id)}——医生站凭它接单")
    else:
        print("[提示] 流程已走完，没有需要审核的环节")


if __name__ == "__main__":
    main()
