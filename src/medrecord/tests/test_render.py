# -*- coding: utf-8 -*-
"""第 21 课：排版输出测试——医生拿到的是「真实出处 + 状态 + 质控记录」的打印稿"""
from medrecord.pipeline_nodes import render_node

FACTS = [
    {"section": "用药记录", "drug": "0.9%氯化钠注射液", "dose": "100 ml", "route": "静滴",
     "freq": "1/8小时", "source": "医嘱.xlsx 第20行 医嘱号10"},
    {"section": "检验所见", "name": "谷丙转氨酶", "value": "10.8", "units": "U/L",
     "source": "检验结果.xlsx 第2行 检验号26052068664"},
]


def _state(**over):
    base = {
        "patient_id": "FAKE9999",
        "verified": FACTS,
        "written": [
            {"text": "0.9%氯化钠注射液 100 ml，静滴，频次1/8小时。", "sources": ["F1"]},
            {"text": "谷丙转氨酶 10.8 U/L。", "sources": ["F2"]},
        ],
        "qc_write": {"passed": True, "issues": [], "attempts": 0},
        "review": {"decision": "批准", "reviewer": "医生（终端）"},
        "derived": [{"label": "最高腋下体温", "value": "36.9℃",
                     "basis": "出现于 2099-09-18", "source": "生命体征.xlsx 第99行"}],
        "rejected": [],
    }
    base.update(over)
    return base


def _patch_out(monkeypatch, tmp_path):
    monkeypatch.setattr("medrecord.pipeline_nodes.OUT_DIR", str(tmp_path))


def test_排版_三栏目与真实出处(tmp_path, monkeypatch):
    _patch_out(monkeypatch, tmp_path)
    md = render_node(_state())["rendered"]
    assert "## 用药记录" in md and "## 检查所见" in md and "## 检验所见" in md
    assert "医嘱.xlsx 第20行 医嘱号10" in md          # 医生看真实出处
    assert "F1" not in md                              # 内部编号不外露
    assert "## 住院概况（代码计算）" in md
    assert (tmp_path / "FAKE9999_病历草稿.md").exists()   # 文件真的落盘


def test_排版_过审稿带审核记录(tmp_path, monkeypatch):
    _patch_out(monkeypatch, tmp_path)
    md = render_node(_state())["rendered"]
    assert "已过审" in md and "已通过医生审核" in md


def test_排版_驳回稿带警示语(tmp_path, monkeypatch):
    _patch_out(monkeypatch, tmp_path)
    md = render_node(_state(review={"decision": "驳回", "reviewer": "医生（终端）"}))["rendered"]
    assert "不得作为正式病历内容使用" in md


def test_排版_质检遗留问题进入质控记录(tmp_path, monkeypatch):
    _patch_out(monkeypatch, tmp_path)
    st = _state(qc_write={"passed": False,
                          "issues": ["引用了不存在的事实编号 F9999：「编的话。」"],
                          "attempts": 2})
    md = render_node(st)["rendered"]
    assert "未完全通过" in md and "F9999" in md


def test_排版_空栏目显示待医生补充(tmp_path, monkeypatch):
    _patch_out(monkeypatch, tmp_path)
    st = _state(written=[{"text": "0.9%氯化钠注射液 100 ml，静滴，频次1/8小时。",
                          "sources": ["F1"]}])
    md = render_node(st)["rendered"]
    assert "【待医生补充】（本栏目暂无核对通过的事实）" in md
