# -*- coding: utf-8 -*-
"""run.py —— 薄启动器：解析参数、走两段式（暂停→恢复）。

真正的库代码都在 medrecord/ 里；脚本只做「怎么跑」，不做「做什么」。
运行（在 src/ 目录下）：
    /usr/bin/python3 -m medrecord.run 测试患者甲 批准
"""
import sys

from langgraph.types import Command

from medrecord.graph import build_main_graph
from medrecord.state import VisitState


def main():
    patient_id = sys.argv[1] if len(sys.argv) > 1 else "测试患者甲"
    decision = sys.argv[2] if len(sys.argv) > 2 else "批准"

    app = build_main_graph()
    config = {"configurable": {"thread_id": f"full-{patient_id}"}}

    # 第 9 课加餐：stream 模式——每个「节拍」结束都吐一次更新，图执行全程可见
    print("═══ 第一段：stream 观察每个节拍 ═══")
    final = None
    for update in app.stream(
        {"folder": patient_id, "patient_id": patient_id, "orders": [], "exams": [], "labs": [], "vitals": [], "derived": [], "facts": [],
         "verified": [], "rejected": [], "qc": {}, "gate": {}, "written": [], "qc_write": {}, "write_feedback": [], "rendered": "", "draft": "", "review": {}, "evaluation": ""},
        config, stream_mode="updates",
    ):
        for node_name in update:
            if node_name == "__interrupt__":
                print(f"[节拍] ★ interrupt —— 流程停在医生审核站")
            else:
                print(f"[节拍] 节点完成：{node_name}")
        final = update

    if final and "__interrupt__" in final:
        # 第二段：医生表态，从暂停点恢复
        resumed = app.invoke(Command(resume=decision), config)
        print()
        print(resumed["evaluation"])
    else:
        print("（没看到 interrupt？检查 doctor_review）")


if __name__ == "__main__":
    main()
