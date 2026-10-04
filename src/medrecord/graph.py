# -*- coding: utf-8 -*-
"""graph.py —— 主图组装。子图在这里只是众多节点中的一个。

对比 lesson07 的 200 行一锅炖，主图现在一眼能读完整张图——
这就是真实项目里 graph.py / nodes.py / state.py 三件套的由来。
"""
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import InMemorySaver

from medrecord.state import VisitState
from medrecord.extraction import build_extraction_subgraph
from medrecord.pipeline_nodes import (
    load_orders, build_draft_node, doctor_review, route_after_review,
    finalize, archive, export_eval,
)
from medrecord.privacy_gate import gate_node, route_after_gate, gate_blocked


def build_main_graph(checkpointer=None):
    """checkpointer 可注入：教学默认内存存档；生产传 SqliteSaver（第 10 课）"""
    g = StateGraph(VisitState)
    g.add_node("load_orders", load_orders)
    # ★ 第 16 课：出口闸门第一道——命中则直奔拦截终点，模型零调用
    g.add_node("privacy_gate", gate_node)
    g.add_node("gate_blocked", gate_blocked)
    # ★ 第 9 课：子图当一个节点挂——「抽取科」整个被装进一个黑盒节点
    g.add_node("extraction", build_extraction_subgraph())
    g.add_node("build_draft", build_draft_node)
    g.add_node("doctor_review", doctor_review)
    g.add_node("finalize", finalize)
    g.add_node("archive", archive)
    g.add_node("export_eval", export_eval)

    g.add_edge(START, "load_orders")
    g.add_edge("load_orders", "privacy_gate")
    g.add_conditional_edges("privacy_gate", route_after_gate)   # 放行→抽取 / 拦截→终点
    g.add_edge("gate_blocked", END)
    g.add_edge("extraction", "build_draft")
    g.add_edge("build_draft", "doctor_review")
    g.add_conditional_edges("doctor_review", route_after_review)
    g.add_edge("finalize", "export_eval")
    g.add_edge("archive", "export_eval")
    g.add_edge("export_eval", END)

    return g.compile(checkpointer=checkpointer or InMemorySaver())
