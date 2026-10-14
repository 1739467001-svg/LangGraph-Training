# -*- coding: utf-8 -*-
"""第 19 课：计算器测试——算术交给代码，所以算术必须有测试

纯函数逐条考：住院天数口径、最高体温取值、发热阈值统计、
坏数据（值不是数字）不能把计算器弄崩。
"""
from datetime import datetime

from medrecord.compute import (compute_stays, compute_max_temp, compute_fever,
                               compute_node, FEVER_THRESHOLD)

D = datetime


def _v(row, name, value, date, units="℃"):
    return {"row": row, "name": name, "value": value, "units": units, "date": date}


VITALS = [
    _v(2, "入院", None, D(2099, 7, 1)),
    _v(3, "腋下体温", "36.6", D(2099, 7, 1)),
    _v(4, "腋下体温", "37.5", D(2099, 7, 2)),
    _v(5, "腋下体温", "38.2", D(2099, 7, 2)),     # 同一天两次发热
    _v(6, "腋下体温", "36.8", D(2099, 7, 3)),
    _v(7, "脉搏", "105", D(2099, 7, 3), units="次/分"),
    _v(8, "腋下体温", "坏数据", D(2099, 7, 4)),   # 不可转数值 → 跳过
    _v(9, "出院", None, D(2099, 7, 8)),
]


def test_住院天数_按入院出院配对():
    stays = compute_stays(VITALS)
    assert len(stays) == 1
    assert stays[0]["days"] == 7                  # 7-01 → 7-08，出院日减入院日


def test_住院天数_缺出院不配对():
    only_adm = [v for v in VITALS if v["name"] != "出院"]
    assert compute_stays(only_adm) == []          # 缺一半的事件不硬算


def test_最高体温_取最大值带出处行号():
    mt = compute_max_temp(VITALS)
    assert mt["value"] == 38.2
    assert mt["row"] == 5                          # 出处：第 5 行
    assert mt["date"] == D(2099, 7, 2)


def test_最高体温_坏数据不崩():
    bad = [_v(2, "腋下体温", "无记录", D(2099, 7, 1))]
    assert compute_max_temp(bad) is None


def test_发热统计_按天去重():
    f = compute_fever(VITALS)
    assert f["records"] == 2                       # 37.5 和 38.2 两次记录
    assert f["days"] == 1                          # 同一天，按天算 1 天
    assert FEVER_THRESHOLD == 37.3


def test_计算节点_产出带依据带出处的计算事实():
    out = compute_node({"vitals": VITALS})
    labels = [d["label"] for d in out["derived"]]
    assert any("住院天数" in l for l in labels)
    assert any("最高腋下体温" in l for l in labels)
    assert any("发热" in l for l in labels)
    for d in out["derived"]:
        assert d["basis"] and d["source"]          # 每条都带依据和出处


def test_计算节点_没有体征数据_安静产出空列表():
    assert compute_node({"vitals": []})["derived"] == []
