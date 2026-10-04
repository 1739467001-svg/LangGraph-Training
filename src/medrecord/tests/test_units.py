# -*- coding: utf-8 -*-
"""零件级测试：纯函数的上岗考核

原则：每个测试只考一个知识点，名字用中文说清「考什么」——
测试是给人看的规格，不是给机器的谜语。
"""
from medrecord.extraction import _norm, check_dose
from medrecord.pipeline_nodes import norm_name, match


# ---------- 剂量归一化（第 4 课：100 与 100.0 是同一个数）----------
def test_归一化_数值等价():
    assert _norm("100.0 ml") == _norm("100ml")
    assert _norm("1.5 g") == _norm("1.5g")


def test_归一化_大小写与空格无关():
    assert _norm("5 MG") == _norm("5mg")


# ---------- 剂量核对的方向性（第 4 课：诚实放行，编造拦截）----------
def test_模型待补充是诚实_放行():
    assert check_dose("【待医生补充】", "100 ml") is True


def test_表里没有模型却报了_是编造():
    assert check_dose("100 ml", "【待医生补充】") is False


def test_数值等价算一致():
    assert check_dose("100ml", "100.0 ml") is True


def test_数值不等要拦截():
    assert check_dose("100ml", "500 ml") is False


# ---------- 药名归一化与匹配（第 7 课：括号备注和详略差异）----------
def test_归一化_去全角括号备注():
    assert norm_name("0.9%氯化钠注射液（双阀）") == "0.9%氯化钠注射液"


def test_归一化_去半角括号备注():
    assert norm_name("5%葡萄糖氯化钠注射液(双阀)") == "5%葡萄糖氯化钠注射液"


def test_匹配_医嘱名与记录名详略差异():
    assert match("氯化钠注射液", "0.9%氯化钠注射液")
    assert match("注射用头孢曲松钠", "注射用头孢曲松钠")
    assert not match("氯化钾注射液", "氯化钠注射液")
