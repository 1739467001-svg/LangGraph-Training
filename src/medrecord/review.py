# -*- coding: utf-8 -*-
"""review.py —— 「医生站」：也许是第二天、另一台机器。

打开磁盘存档 → 读出挂起的待审草稿（get_state）→ 医生表态 →
Command(resume) 从暂停点走完。全程不重新抽取、不重新核对——
袋子里的东西是昨天那份。

运行：/usr/bin/python3 -m medrecord.review 测试患者乙 批准
"""
import sqlite3
import sys

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from medrecord.graph import build_main_graph
from medrecord.state import CHECKPOINT_DB, thread_id


def main():
    patient_id = sys.argv[1] if len(sys.argv) > 1 else "测试患者乙"
    decision = sys.argv[2] if len(sys.argv) > 2 else "批准"

    conn = sqlite3.connect(CHECKPOINT_DB, check_same_thread=False)
    app = build_main_graph(checkpointer=SqliteSaver(conn))
    cfg = {"configurable": {"thread_id": thread_id(patient_id)}}

    snap = app.get_state(cfg)
    if not snap.next:
        print("[提示] 该患者没有挂起的审核（可能尚未提交，或已结案）")
        return

    pending = snap.tasks[0].interrupts[0].value
    print("═══ 待审草稿（昨天提交的，从磁盘读回）═══")
    print(pending["question"])
    print(pending["draft"])

    final = app.invoke(Command(resume=decision), cfg)
    print()
    print(final["evaluation"])
    print(f"\n[结案] thread {cfg['configurable']['thread_id']} 处理完毕，结论：{decision}")


if __name__ == "__main__":
    main()
