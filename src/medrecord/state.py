# -*- coding: utf-8 -*-
"""state.py —— 全项目唯一的「资料袋」定义 + 全局常量 + 路径

第 1 课学过：State 是 TypedDict。现在它有了自己的文件——
所有节点从这里 import 同一个定义，谁也不许私自加字段（要加就改这里，
全项目一起生效）。这就是模块化的第一层收益：单一事实来源。
"""
import os
from typing import Annotated, TypedDict
import operator

# 路径：medrecord 包在 src/ 下，ROOT = 仓库根
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DATA_DIR = os.path.join(ROOT, "清洗后的数据", "住院")
OUT_DIR = os.path.join(ROOT, "outputs")
CHECKPOINT_DB = os.path.join(OUT_DIR, "checkpoints.sqlite")   # 第 10 课：存档落盘


def thread_id(patient_id: str) -> str:
    """存档位编号：同一患者的流程，提交方和审核方必须报同一个号"""
    return f"medrecord-{patient_id}"

# 抽取策略常量（第 8 课的教训：别再设工位内截断上限）
CHUNK_SIZE = 20
MAX_RETRY = 1
PASS_RATE_FLOOR = 0.5

# 第 13 课：栏目即配置——每个栏目一种数据源、一种工位
SECTION_DRUG = "用药记录"    # 大模型工位（从非结构化文本抽事实）
SECTION_EXAM = "检查所见"    # 纯代码工位（医生已写好印象，逐字摘录即可）
SECTION_LAB = "检验所见"     # 纯代码工位（第 17 课：结果表自带项目名和结果值）


class VisitState(TypedDict):
    folder: str        # 数据文件夹名（测试患者甲）——找 zip 用
    patient_id: str    # 住院号（FAKE0001）——表里的正式身份
    orders: list       # 医嘱行（用药栏目的数据源）
    exams: list        # 检查结果行（检查所见栏目的数据源，已联表带项目名）
    labs: list         # 检验结果行（检验所见栏目的数据源，自带项目名和结果值）
    vitals: list       # 生命体征行（第 19 课：计算器的数据源）
    derived: list      # 第 19 课：代码算出的计算事实（住院天数/最高体温等）
    facts: Annotated[list, operator.add]   # 并行工位追加（第 5 课 reducer）
    verified: list
    rejected: list
    qc: dict           # 抽取阶段质检（第 2 课）
    written: list      # 第 18 课：书记员成文的句子 [{"text","sources"}]
    qc_write: dict     # 成文阶段质检：{"passed","issues","attempts","retry"}
    write_feedback: list   # 质检意见（重写时注入提示词）
    gate: dict         # 第 16 课：出口闸门结果 {"passed": bool, "hits": [...]}
    draft: str
    review: dict
    evaluation: str
