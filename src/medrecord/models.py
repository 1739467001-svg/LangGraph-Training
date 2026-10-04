# -*- coding: utf-8 -*-
"""models.py —— 和大模型有关的一切：交货合同、基线模型、真模型工厂、提示词、链

接真模型的环境变量契约（铁律：key 只放环境变量，绝不写进代码、不进 git）：
  DRCOPILOT_API_KEY          大模型 key（必填才能上真模型）
  DRCOPILOT_MODEL            模型名，如 glm-4-flash / deepseek-chat（必填）
  DRCOPILOT_LLM_BASE_URL     OpenAI 兼容端点（智谱/DeepSeek/Qwen 等都兼容）
  DRCOPILOT_DATA_APPROVED    必须等于 "1" 才允许把清洗后数据发给厂商
                             ——这就是铁律 6 的「明确批准」，代码里的物理闸门
"""
import json
import os
import re
from typing import List

from pydantic import BaseModel, Field
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.prompts import ChatPromptTemplate


class MedicationFact(BaseModel):
    drug: str = Field(description="药品名称，逐字照抄医嘱原文")
    dose: str = Field(description="剂量+单位；原文找不到依据就填【待医生补充】")
    route: str = Field(description="给药途径")
    source: str = Field(description="出处，格式：医嘱.xlsx 第N行 医嘱号M")
    freq: str = Field(description="用药频次，逐字照抄医嘱原文；找不到依据就填【待医生补充】")


class FactList(BaseModel):
    facts: List[MedicationFact]


DRUG_HINT = re.compile(r"注射液|注射用|吸入用|片|胶囊|颗粒|口服液|滴丸|软膏|丸|滴眼液")


class BaselineModel(BaseChatModel):
    """规则基线抽取器（替身模型）：接口与真模型一致，获批后整个换掉"""

    @property
    def _llm_type(self) -> str:
        return "baseline-rule-extractor"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        text = messages[-1].content
        facts = []
        for line in text.splitlines():
            if "：" not in line:
                continue
            head, body = line.split("：", 1)
            m_row, m_no = re.search(r"第(\d+)行", head), re.search(r"医嘱号(\d+)", head)
            parts = body.split()
            if not (m_row and m_no and parts):
                continue
            name = parts[0]
            if not DRUG_HINT.search(name):
                continue
            has_dose = len(parts) >= 3 and re.match(r"\d", parts[1])
            facts.append({
                "drug": name,
                "dose": " ".join(parts[1:3]) if has_dose else "【待医生补充】",
                "route": parts[3] if has_dose and len(parts) >= 4 else "【待医生补充】",
                "freq": parts[4] if has_dose and len(parts) >= 5 else "【待医生补充】",
                "source": f"医嘱.xlsx 第{m_row.group(1)}行 医嘱号{m_no.group(1)}",
            })
            # 第 8 课教训：不设条数上限，静默截断会杀召回
        content = json.dumps({"facts": facts}, ensure_ascii=False)
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=content))])


prompt = ChatPromptTemplate.from_messages([
    ("system", """你是一名住院病历的用药信息抽取助手。请从下面的医嘱列表里抽取用药事实。

规则：
1. 只使用医嘱原文里出现的信息，药名、剂量逐字照抄，不改写不翻译；
2. 每一条事实都必须给出处，格式：医嘱.xlsx 第N行 医嘱号M；
3. 原文里没有的信息，一律填【待医生补充】，绝不猜测、绝不编造数值；
4. 你只负责抽取事实，不判断用药对错、不下诊断、不建议换药——那是医生的工作。"""),
    ("human", "医嘱列表（共 {order_count} 条）：\n{orders_text}"),
])


def make_llm() -> BaseChatModel:
    """模型工厂：三个判断决定了最终用哪个「零件」

    ① 没有 key → 规则基线（教学模式，零成本、零数据外发）
    ② 有 key 但 DRCOPILOT_DATA_APPROVED != "1" → 仍用基线。
       这就是铁律 6 的物理实现：批准是环境变量级的显式动作，
       不是聊天里说一句「可以」——代码不认识聊天记录
    ③ key + 批准齐全 → OpenAI 兼容真模型（temperature=0：
       抽取任务要可复现，不要创造性）
    """
    key = os.environ.get("DRCOPILOT_API_KEY")
    if not key:
        print("[模型] 未配置 DRCOPILOT_API_KEY → 规则基线（教学模式）")
        return BaselineModel()
    if os.environ.get("DRCOPILOT_DATA_APPROVED") != "1":
        print("[模型] 有 key 但未获数据外发批准（DRCOPILOT_DATA_APPROVED=1）→ 铁律6：仍用基线")
        return BaselineModel()
    from langchain_openai import ChatOpenAI   # 懒加载：基线模式不需要装这个包
    model = os.environ.get("DRCOPILOT_MODEL")
    if not model:
        raise SystemExit("已批准数据外发，但缺 DRCOPILOT_MODEL（如 glm-4-flash / deepseek-chat）")
    print(f"[模型] 真模型上线：{model} @ {os.environ.get('DRCOPILOT_LLM_BASE_URL') or '官方端点'}")
    return ChatOpenAI(model=model, api_key=key,
                      base_url=os.environ.get("DRCOPILOT_LLM_BASE_URL") or None,
                      temperature=0)


def parse_facts(ai_msg: AIMessage) -> FactList:
    """验货前的拆包：真模型常把 JSON 包进 markdown 代码围栏（```json ... ```），
       基线模型不会——这就是「替身测不出真模型的坑」的活例子，必须剥掉"""
    text = ai_msg.content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text).strip()
    return FactList.model_validate_json(text)


extraction_chain = prompt | make_llm() | parse_facts
