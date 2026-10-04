# -*- coding: utf-8 -*-
"""
deidentify.py —— 病历数据脱敏主脚本

思路（两遍扫描）：
  Pass 1 扫描：只读不改，收集所有需要脱敏的「真实值」
       （患者姓名/住院号、医护姓名与工号、电话、身份证、住址、医院名、日期范围）
       每个真实值都登记到映射表，并统计出现次数。
  Pass 2 改写：按映射表写出清洗后的副本到 ../清洗后的数据/
       - 8 张 .xls 表 -> 同名 .xlsx（数值/临床内容保留，标识全部替换）
       - XML 病历文书 zip -> 同结构新 zip（正文里的姓名/编号/日期逐处替换）
       - 日期统一平移到 2099 年（按天平移，保留年龄与间隔）
       - 门诊照片/录音不含在内：二进制媒体无法用脚本可靠脱敏，不复制
  Pass 3 校验：在输出里反向搜索所有真实值，必须 0 命中。

设计原则：
  - 关联键（EXAM_NO/TEST_NO 等流水号）保留，否则检查项目与结果对不上。
  - 医嘱文本、检验数值、诊断描述等临床内容保留——这是教学数据的价值所在。
  - 门诊图片/录音无法脚本脱敏，一律不进入「清洗后的数据」。
"""
import io
import os
import re
import sys
import glob
import json
import shutil
import zipfile
import datetime as dt
from collections import defaultdict

import xlrd
from openpyxl import Workbook
from openpyxl.utils import get_column_letter

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SRC = os.path.join(ROOT, "病历资料", "住院")
DST = os.path.join(ROOT, "清洗后的数据")
MAPPING_PATH = os.path.join(ROOT, "deid_tools", "mapping_真实值勿提交.json")

# ---------------------------------------------------------------- 字段分类
# 患者标识列（值跨表一致，需全局映射）
PATIENT_ID_COLS = {"PATIENT_ID"}
PATIENT_NAME_COLS = {"NAME", "姓名"}
PHONETIC_COLS = {"NAME_PHONETIC"}
DOB_COLS = {"DATE_OF_BIRTH"}
# 联系人/敏感联系方式列
CARE_NAME_COLS = {"CARE_NAME"}
PHONE_COLS = {"PHONE", "CARE_TELEPHONE"}
IDCARD_COLS = {"CARE_ID_NO"}
ADDRESS_COLS = {"CLINIC_PRE_ADDRESS"}
# 医护姓名列（按角色分配假名）
DOCTOR_COLS = {"DOCTOR", "STOP_DOCTOR", "DOCTOR_USER", "CANCEL_DOCTOR",
               "STOP_DOCTOR_USER", "REQ_PHYSICIAN", "CONFIRM_DOCT",
               "ORDERING_PROVIDER", "REPORTER", "TRANSCRIPTIONIST", "VERIFIED_BY"}
NURSE_COLS = {"NURSE", "STOP_NURSE", "NURSE_USER", "STOP_NURSE_USER",
              "PROCESSING_NURSE", "STOP_PROCESSING_NURSE", "CANCEL_NURSE",
              "REQ_NURSE", "REPORT_NURSE", "SPCM_SAMPLE_NURSE"}
TECH_COLS = {"TECHNICIAN", "EXAM_OPERATOR", "APPOINT_OPERATOR"}
# 医护工号列（值可能是数字或字母数字）
STAFF_CODE_COLS = {"REQ_PHYSICIAN_ID", "REPORT_NURSE_CODE", "REPORTER_CODE",
                   "CONFIRM_DOCT_CODE", "EXAM_OPERATOR_CODE", "NURSE_CODE"}

FAKE_PATIENT_NAMES = ["测试患者甲", "测试患者乙", "测试患者丙", "测试患者丁", "测试患者戊"]
FAKE_PHONETIC = ["CESHI HUANZHE JIA", "CESHI HUANZHE YI", "CESHI HUANZHE BING",
                 "CESHI HUANZHE DING", "CESHI HUANZHE WU"]
ANCHOR = dt.date(2099, 1, 5)   # 患者最早日期平移到这一天


def letters_seq(prefix, n):
    return [f"{prefix}{chr(ord('A') + i % 26)}{'' if i < 26 else i // 26}" for i in range(n)]


class DeidEngine:
    def __init__(self):
        # 全局收集
        self.patient_pids = defaultdict(int)  # pid -> 出现次数
        self.patient_names = {}         # 姓名 -> pid（哪个文件夹）
        self.staff_names = defaultdict(set)   # 姓名 -> {角色}
        self.staff_codes = set()        # 工号字符串
        self.phones = defaultdict(int)
        self.idcards = defaultdict(int)
        self.addresses = defaultdict(int)
        self.hospitals = defaultdict(int)
        self.min_date = None
        self.all_dates = []             # 用于取中位数做锚点（抗出生日期等极端值干扰）
        self.real_years = set()         # 2020+ 的住院期间年份（用于校验）
        # Pass 2 使用
        self.pid_map = {}
        self.name_map = {}
        self.phonetic_map = {}
        self.staff_map = {}
        self.code_map = {}
        self.phone_map = {}
        self.idcard_map = {}
        self.addr_map = {}
        self.hospital_map = {}
        self.offset_days = 0
        # 统计
        self.stats = defaultdict(int)

    # ---------------- Pass 1：扫描 ----------------
    def scan(self):
        pdirs = sorted(d for d in os.listdir(SRC)
                       if os.path.isdir(os.path.join(SRC, d)))
        print(f"[扫描] 共 {len(pdirs)} 位患者")
        for pdir_name in pdirs:
            pdir = os.path.join(SRC, pdir_name)
            self.patient_names[pdir_name] = None  # 占位，稍后填 pid
            for f in sorted(glob.glob(os.path.join(pdir, "*.xls"))):
                self._scan_xls(f, pdir_name)
            for f in sorted(glob.glob(os.path.join(pdir, "*.zip"))):
                self._scan_zip(f)
        self._build_maps()
        print(f"[扫描] 住院号 {len(self.patient_pids)} 个，医护姓名 {len(self.staff_names)} 个，"
              f"电话 {len(self.phones)} 个，身份证 {len(self.idcards)} 个，"
              f"住址 {len(self.addresses)} 个，医院名 {len(self.hospitals)} 个")
        print(f"[扫描] 最早日期 {self.min_date}，平移 {self.offset_days} 天 → {ANCHOR}")

    def _note_date(self, d):
        if d is None:
            return
        d = d.date() if isinstance(d, dt.datetime) else d
        if self.min_date is None or d < self.min_date:
            self.min_date = d
        self.all_dates.append(d)
        if d.year >= 2020:
            self.real_years.add(d.year)

    def _scan_xls(self, path, pdir_name):
        book = xlrd.open_workbook(path)
        sh = book.sheet_by_index(0)
        headers = [str(sh.cell_value(0, c)).strip() for c in range(sh.ncols)]
        col_role = {}  # c -> (类别, 列名)
        for c, h in enumerate(headers):
            if h in PATIENT_ID_COLS:
                col_role[c] = ("pid", h)
            elif h in PATIENT_NAME_COLS:
                col_role[c] = ("pname", h)
            elif h in PHONETIC_COLS:
                col_role[c] = ("phonetic", h)
            elif h in DOB_COLS:
                col_role[c] = ("dob", h)
            elif h in CARE_NAME_COLS:
                col_role[c] = ("care", h)
            elif h in PHONE_COLS:
                col_role[c] = ("phone", h)
            elif h in IDCARD_COLS:
                col_role[c] = ("idcard", h)
            elif h in ADDRESS_COLS:
                col_role[c] = ("addr", h)
            elif h in DOCTOR_COLS:
                col_role[c] = ("doctor", h)
            elif h in NURSE_COLS:
                col_role[c] = ("nurse", h)
            elif h in TECH_COLS:
                col_role[c] = ("tech", h)
            elif h in STAFF_CODE_COLS:
                col_role[c] = ("scode", h)

        for r in range(1, sh.nrows):
            for c, role in col_role.items():
                t, v = sh.cell_type(r, c), sh.cell_value(r, c)
                if t == 0 or v == "" or v is None:
                    continue
                if role[0] == "pid":
                    key = str(int(v)) if isinstance(v, float) else str(v).strip()
                    self.patient_pids[key] += 1
                    self.patient_names[pdir_name] = key
                elif role[0] == "pname":
                    self.stats["表格-患者姓名单元格"] += 1
                elif role[0] == "phone":
                    s = str(int(v)) if isinstance(v, float) else str(v)
                    for m in re.findall(r"1[3-9]\d{9}", s):
                        self.phones[m] += 1
                elif role[0] == "idcard":
                    s = str(v).strip()
                    if re.fullmatch(r"\d{17}[\dXx]", s):
                        self.idcards[s] += 1
                elif role[0] == "addr":
                    s = str(v).strip()
                    if len(s) >= 6:
                        self.addresses[s] += 1
                elif role[0] in ("doctor", "nurse", "tech"):
                    s = str(v).strip()
                    if re.fullmatch(r"[\u4e00-\u9fff]{2,4}", s):
                        self.staff_names[s].add(role[0])
                    elif str(v).strip() not in ("",):
                        self.staff_codes.add(str(v).strip())  # 账号型
                elif role[0] == "scode":
                    s = str(int(v)) if isinstance(v, float) else str(v).strip()
                    if s:
                        self.staff_codes.add(s)
                elif role[0] == "dob":
                    if t == 3:
                        self._note_date(xlrd.xldate.xldate_as_datetime(v, book.datemode))
            # 任何字符串列里的手机号/身份证兜底
            for c in range(sh.ncols):
                if sh.cell_type(r, c) == 1:
                    s = str(sh.cell_value(r, c))
                    for m in re.findall(r"(?<!\d)1[3-9]\d{9}(?!\d)", s):
                        self.phones[m] += 1
            # 日期单元格：追踪最小日期（排除出生日期列）
            for c, h in enumerate(headers):
                if sh.cell_type(r, c) == 3 and h not in DOB_COLS:
                    self._note_date(xlrd.xldate.xldate_as_datetime(sh.cell_value(r, c), book.datemode))

    def _scan_zip(self, zpath):
        zf = zipfile.ZipFile(zpath)
        for raw in zf.namelist():
            data = zf.read(raw)
            text = self._decode(data)
            # 手机号/身份证兜底
            for m in re.findall(r"(?<!\d)1[3-9]\d{9}(?!\d)", text):
                self.phones[m] += 1
            for m in re.findall(r"(?<!\d)\d{17}[\dXx](?!\d)", text):
                self.idcards[m] += 1
            # 医院名
            for m in re.findall(r"[\u4e00-\u9fff]{2,12}医院", text):
                self.hospitals[m] += 1
            # 日期范围
            for y, mo, d in re.findall(r"(\d{4})-(\d{1,2})-(\d{1,2})", text):
                try:
                    self._note_date(dt.date(int(y), int(mo), int(d)))
                except ValueError:
                    pass
            # 正文里「XXXX年」写法的年份（病史提及的既往年份）
            for y in re.findall(r"(20\d{2})年", text):
                if int(y) >= 2020:
                    self.real_years.add(int(y))
        zf.close()

    @staticmethod
    def _decode(data):
        for enc in ("utf-8", "gb18030"):
            try:
                return data.decode(enc)
            except UnicodeDecodeError:
                continue
        return data.decode("utf-8", errors="replace")

    # ---------------- 映射表 ----------------
    def _build_maps(self):
        # 患者：按文件夹名排序 → 甲乙丙丁戊
        names_sorted = sorted(self.patient_names)
        self.fake_of = {}
        for i, real in enumerate(names_sorted):
            pid = self.patient_names[real]
            self.fake_of[real] = FAKE_PATIENT_NAMES[i]
            self.pid_map[pid] = f"FAKE{i + 1:04d}"
            self.name_map[real] = FAKE_PATIENT_NAMES[i]
            self.phonetic_map[real] = FAKE_PHONETIC[i]
        # 医护：角色 → 医师A/护士A/技师A
        roles = {"doctor": "医师", "nurse": "护士", "tech": "技师"}
        counters = defaultdict(int)
        for name in sorted(self.staff_names):
            rset = self.staff_names[name]
            role = "doctor" if "doctor" in rset else ("nurse" if "nurse" in rset else
                   ("tech" if "tech" in rset else sorted(rset)[0]))
            counters[role] += 1
            self.staff_map[name] = f"{roles.get(role, '人员')}{chr(ord('A') + counters[role] - 1)}"
        # 工号
        for i, code in enumerate(sorted(self.staff_codes)):
            self.code_map[code] = f"CODE{i + 1:04d}"
        # 电话/身份证/住址/医院
        for i, p in enumerate(sorted(self.phones)):
            self.phone_map[p] = f"138{(i + 1):08d}"
        for i, c in enumerate(sorted(self.idcards)):
            self.idcard_map[c] = f"FAKEIDNO{i + 1:04d}"
        for i, a in enumerate(sorted(self.addresses)):
            self.addr_map[a] = f"虚构省虚构市虚构区健康路{i + 1}号"
        for h in self.hospitals:
            self.hospital_map[h] = "XX县人民医院"
        # 日期偏移：用「中位数日期」做锚点。
        # 直接用最早日期会被 1956 年这样的出生日期带偏（平移量变成 142 年），
        # 中位数必然落在住院期间附近，把中位数平移到 2099 年年中。
        if self.all_dates:
            ds = sorted(self.all_dates)
            med = ds[len(ds) // 2]
            self.offset_days = (dt.date(2099, 7, 1) - med).days
        else:
            self.offset_days = 0

    # ---------------- 文本改写核心 ----------------
    def shift_date_str(self, text):
        """把文本里各种格式的日期平移 offset_days 天"""
        off = self.offset_days

        def shift(y, m, d, fmt):
            try:
                nd = dt.date(int(y), int(m), int(d)) + dt.timedelta(days=off)
            except ValueError:
                return None
            if fmt == "dash":
                return f"{nd.year}-{nd.month}-{nd.day}"
            if fmt == "dashz":
                return f"{nd.year:04d}-{nd.month:02d}-{nd.day:02d}"
            if fmt == "slash":
                return f"{nd.year}/{nd.month}/{nd.day}"
            if fmt == "cn":
                return f"{nd.year}年{nd.month}月{nd.day}日"
            if fmt is None:  # 紧凑格式 YYYYMMDD
                return f"{nd.year:04d}{nd.month:02d}{nd.day:02d}"
            return None

        # YYYY-MM-DD / YYYY-M-D（不带前导零，保留原风格）
        def rep_dash(m):
            s = shift(m.group(1), m.group(2), m.group(3), "dashz" if len(m.group(2)) == 2 else "dash")
            return s if s else m.group(0)
        text = re.sub(r"(?<!\d)(\d{4})-(\d{1,2})-(\d{1,2})", rep_dash, text)

        def rep_slash(m):
            s = shift(m.group(1), m.group(2), m.group(3), "slash")
            return s if s else m.group(0)
        text = re.sub(r"(?<!\d)(\d{4})/(\d{1,2})/(\d{1,2})", rep_slash, text)

        def rep_cn(m):
            s = shift(m.group(1), m.group(2), m.group(3), "cn")
            return s if s else m.group(0)
        text = re.sub(r"(?<!\d)(\d{4})年(\d{1,2})月(\d{1,2})日", rep_cn, text)

        # 14/16 位数字串（文档ID、导出时间戳）：前 8 位是日期 → 平移，其余保留
        def rep_compact(m):
            s = shift(m.group(1), m.group(2), m.group(3), None)
            if s is None:
                return m.group(0)
            rest = m.group(4) or ""
            return s.replace("-", "") + rest
        text = re.sub(r"(?<!\d)(20\d{2})(\d{2})(\d{2})(\d{6,10})(?!\d)", rep_compact, text)
        # 独立 8 位 YYYYMMDD
        text = re.sub(r"(?<![\d])(20\d{2})(\d{2})(\d{2})(?!\d)", rep_compact, text)
        # 只有年份的写法（如「2024年行阑尾切除术」）：按年平移
        yoff = round(off / 365.25)

        def rep_year(m):
            return f"{int(m.group(1)) + yoff}年"
        text = re.sub(r"(?<!\d)(\d{4})年(?!\d)", rep_year, text)
        return text

    def apply_text(self, text, is_xml=False):
        """对一段文本执行全部替换（先长后短，防止子串误伤）"""
        stats = self.stats
        # 1) 住院号后紧跟日期的连写（如 9999000001 + 20260921 连成 999900000120260921）
        for pid, fake in sorted(self.pid_map.items(), key=lambda x: -len(x[0])):
            text = re.sub(re.escape(pid) + r"(20\d{6})",
                          lambda m: fake + (dt.date(int(m.group(1)[:4]), int(m.group(1)[4:6]), int(m.group(1)[6:8]))
                                            + dt.timedelta(days=self.offset_days)).strftime("%Y%m%d")
                          if self._valid_ymd(m.group(1)) else fake,
                          text)
        # 2) 住院号（任意位置）
        for pid, fake in sorted(self.pid_map.items(), key=lambda x: -len(x[0])):
            n = text.count(pid)
            if n:
                stats[f"替换-住院号"] += n
                text = text.replace(pid, fake)
        # 3) 患者姓名 / 拼音
        for real, fake in sorted(self.name_map.items(), key=lambda x: -len(x[0])):
            n = text.count(real)
            if n:
                stats[f"替换-患者姓名"] += n
                text = text.replace(real, fake)
        for real, fake in self.phonetic_map.items():
            if real in text:
                text = text.replace(real, fake)
        # 4) 医护姓名 / 工号
        for real, fake in sorted(self.staff_map.items(), key=lambda x: -len(x[0])):
            n = text.count(real)
            if n:
                stats[f"替换-医护姓名"] += n
                text = text.replace(real, fake)
        for real, fake in sorted(self.code_map.items(), key=lambda x: -len(x[0])):
            if real in text:
                text = re.sub(r"(?<![\dA-Za-z])" + re.escape(real) + r"(?![\dA-Za-z])", fake, text)
        # 5) 电话 / 身份证 / 住址 / 医院名
        for real, fake in sorted(self.phone_map.items()):
            if real in text:
                stats["替换-电话"] += 1
                text = re.sub(r"(?<!\d)" + real + r"(?!\d)", fake, text)
        for real, fake in sorted(self.idcard_map.items()):
            if real in text:
                stats["替换-身份证"] += 1
                text = text.replace(real, fake)
        for real, fake in sorted(self.addr_map.items(), key=lambda x: -len(x[0])):
            if real in text:
                stats["替换-住址"] += 1
                text = text.replace(real, fake)
        for real, fake in sorted(self.hospital_map.items(), key=lambda x: -len(x[0])):
            if real in text:
                stats["替换-医院名"] += 1
                text = text.replace(real, fake)
        # 6) 兜底：任何剩余手机号/座机/身份证样式
        text = re.sub(r"(?<!\d)1[3-9]\d{9}(?!\d)", "13800000000", text)
        text = re.sub(r"(?<!\d)0\d{2,3}-\d{7,8}(?!\d)", "0000-0000000", text)
        text = re.sub(r"(?<!\d)\d{17}[\dXx](?!\d)", "FAKEIDNO0000", text)
        # 7) 自由文本里的住址（表格字段之外，入院记录等正文常见）
        #    必须含「省/自治区」开头 + 市/县/区 + 路/街/村/镇/号 等，避免误伤临床文本
        text = re.sub(
            r"[\u4e00-\u9fff]{2,8}(?:省|自治区)[\u4e00-\u9fff]{0,8}(?:市|自治州|县|区|旗)"
            r"[\u4e00-\u9fff]{0,12}?(?:路|街道|街|巷|村|镇|乡|小区|花园|大道|广场)"
            r"[\u4e00-\u9fff0-9]{0,10}(?:号|栋|幢|单元|室|楼|号院?)?",
            "虚构省虚构市虚构区虚构路1号", text)
        text = re.sub(
            r"[\u4e00-\u9fff]{2,8}(?:市|县)[\u4e00-\u9fff]{0,8}?(?:区|县)"
            r"[\u4e00-\u9fff]{0,12}?(?:路|街道|街|巷|村|镇|乡|小区|花园|大道|广场)"
            r"[\u4e00-\u9fff0-9]{0,10}(?:号|栋|幢|单元|室|楼|号院?)?",
            "虚构市虚构区虚构路1号", text)
        # 8) 标签导向：联系人/工作单位/籍贯等「标签：值」结构（值不在任何映射表里）
        text = re.sub(r"(联系人(?:姓名)?|家属姓名)\s*[:：]\s*[\u4e00-\u9fff]{2,4}",
                      r"\1：家属甲", text)
        text = re.sub(r"(工作单位|单位名称)\s*[:：]\s*[^\s<，,。;；\"』」]{2,24}",
                      r"\1：虚构单位", text)
        text = re.sub(r"(籍贯|户籍地址|出生地)\s*[:：]\s*[^\s<，,。;；\"』」]{2,24}",
                      r"\1：虚构省虚构市", text)
        text = re.sub(r"((?:现|家|联)?住址|联系地址|家庭地址)\s*[:：]\s*[^\s<，,。;；\"』」]{2,30}",
                      r"\1：虚构省虚构市虚构区虚构路1号", text)
        # 9) 日期平移
        text = self.shift_date_str(text)
        return text

    @staticmethod
    def _valid_ymd(s):
        try:
            dt.date(int(s[:4]), int(s[4:6]), int(s[6:8]))
            return True
        except (ValueError, IndexError):
            return False

    # ---------------- Pass 2：改写 ----------------
    def transform(self):
        if os.path.isdir(DST):
            shutil.rmtree(DST)
        os.makedirs(os.path.join(DST, "住院"), exist_ok=True)
        pdirs = sorted(d for d in os.listdir(SRC) if os.path.isdir(os.path.join(SRC, d)))
        for pdir_name in pdirs:
            fake = self.fake_of[pdir_name]
            outdir = os.path.join(DST, "住院", fake)
            os.makedirs(outdir, exist_ok=True)
            pdir = os.path.join(SRC, pdir_name)
            print(f"[改写] {fake} ← 原文件夹({len(os.listdir(pdir))} 个文件)")
            for f in sorted(glob.glob(os.path.join(pdir, "*.xls"))):
                self._write_xlsx(f, outdir)
            for f in sorted(glob.glob(os.path.join(pdir, "*.zip"))):
                self._write_zip(f, outdir, pdir_name)

    def _write_xlsx(self, src, outdir):
        book = xlrd.open_workbook(src)
        sh = book.sheet_by_index(0)
        headers = [str(sh.cell_value(0, c)).strip() for c in range(sh.ncols)]
        wb = Workbook()
        ws = wb.active
        ws.title = "SQL Results"
        ws.append(headers)
        pid_cols = {c for c, h in enumerate(headers) if h in PATIENT_ID_COLS}
        pname_cols = {c for c, h in enumerate(headers) if h in PATIENT_NAME_COLS}
        phon_cols = {c for c, h in enumerate(headers) if h in PHONETIC_COLS}
        dob_cols = {c for c, h in enumerate(headers) if h in DOB_COLS}
        care_cols = {c for c, h in enumerate(headers) if h in CARE_NAME_COLS}
        phone_cols = {c for c, h in enumerate(headers) if h in PHONE_COLS}
        idc_cols = {c for c, h in enumerate(headers) if h in IDCARD_COLS}
        addr_cols = {c for c, h in enumerate(headers) if h in ADDRESS_COLS}
        staff_cols = {}
        for c, h in enumerate(headers):
            for kind, cols in (("doctor", DOCTOR_COLS), ("nurse", NURSE_COLS),
                               ("tech", TECH_COLS), ("scode", STAFF_CODE_COLS)):
                if h in cols:
                    staff_cols[c] = kind

        for r in range(1, sh.nrows):
            row = []
            for c in range(sh.ncols):
                t, v = sh.cell_type(r, c), sh.cell_value(r, c)
                h = headers[c]
                if t == 0:
                    row.append(None)
                    continue
                if t == 3:  # 日期
                    if v < 1:  # 纯时间（如 08:30），不是日期，保持不变
                        secs = round(v * 86400)
                        row.append(dt.time(secs // 3600, secs % 3600 // 60))
                        continue
                    d = xlrd.xldate.xldate_as_datetime(v, book.datemode)
                    row.append(d + dt.timedelta(days=self.offset_days))
                    continue
                if t == 2:  # 数字
                    iv = int(v) if float(v).is_integer() else None
                    if iv is not None and h in PATIENT_ID_COLS and str(iv) in self.pid_map:
                        row.append(self.pid_map[str(iv)])
                        self.stats["表格-住院号"] += 1
                    elif iv is not None and h in STAFF_CODE_COLS and str(iv) in self.code_map:
                        row.append(self.code_map[str(iv)])
                        self.stats["表格-工号"] += 1
                    elif h in PHONE_COLS and iv is not None and re.fullmatch(r"1[3-9]\d{9}", str(iv)):
                        row.append(self.phone_map.get(str(iv), "13800000000"))
                    elif iv is not None and 19000102 <= iv <= 20991231 and self._valid_ymd(str(iv)):
                        # 以 YYYYMMDD 数字形式存的日期：平移后仍写回数字
                        s = str(iv)
                        nd = dt.date(int(s[:4]), int(s[4:6]), int(s[6:8])) + dt.timedelta(days=self.offset_days)
                        row.append(int(nd.strftime("%Y%m%d")))
                        self.stats["表格-数字日期"] += 1
                    elif iv is not None and len(str(iv)) == 14 and self._valid_ymd(str(iv)[:8]):
                        s = str(iv)
                        nd = dt.date(int(s[:4]), int(s[4:6]), int(s[6:8])) + dt.timedelta(days=self.offset_days)
                        row.append(int(nd.strftime("%Y%m%d") + s[8:]))
                        self.stats["表格-数字日期"] += 1
                    else:
                        row.append(v)
                    continue
                # 文本
                s = str(v)
                if h in PATIENT_NAME_COLS:
                    self.stats["表格-患者姓名"] += 1
                    row.append(self.name_map.get(s, s))
                    continue
                if h in PHONETIC_COLS:
                    row.append(self.phonetic_map.get(s, "CESHI HUANZHE"))
                    continue
                if h in CARE_NAME_COLS and s:
                    row.append(self.staff_map.get(s, "家属甲"))
                    continue
                if h in PHONE_COLS and s.strip():
                    row.append(self.phone_map.get(s.strip(), "13800000000"))
                    continue
                if h in IDCARD_COLS and s.strip():
                    row.append(self.idcard_map.get(s.strip(), "FAKEIDNO0000"))
                    continue
                if h in ADDRESS_COLS and len(s.strip()) >= 6:
                    row.append(self.addr_map.get(s.strip(), "虚构省虚构市虚构区健康路1号"))
                    continue
                if c in staff_cols:
                    kind = staff_cols[c]
                    s2 = str(int(v)) if isinstance(v, float) and float(v).is_integer() else s.strip()
                    if re.fullmatch(r"[\u4e00-\u9fff]{2,4}", s2) and s2 in self.staff_map:
                        row.append(self.staff_map[s2])
                    elif s2 in self.code_map:
                        row.append(self.code_map[s2])
                    else:
                        row.append(s)
                    continue
                # 其余文本列：通用替换（覆盖姓名/编号/日期散落在文本里的情况）
                row.append(self.apply_text(s))
            ws.append(row)
        out = os.path.join(outdir, os.path.basename(src).replace(".xls", ".xlsx"))
        wb.save(out)

    def _write_zip(self, src, outdir, pdir_name):
        zf = zipfile.ZipFile(src)
        fake_pid = self.pid_map[self.patient_names[pdir_name]]
        fake_name = self.fake_of[pdir_name]
        # zip 文件名本身含 住院号_姓名(时间戳)
        base = os.path.basename(src)
        new_base = self.apply_text(base)
        out = os.path.join(outdir, new_base)
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
            for raw in zf.namelist():
                data = zf.read(raw)
                name = raw
                try:  # 老压缩包 GBK 文件名还原
                    name = raw.encode("cp437").decode("gbk")
                except (UnicodeEncodeError, UnicodeDecodeError):
                    pass
                new_name = self.apply_text(name)
                text = self._decode(data)
                new_text = self.apply_text(text, is_xml=True)
                self.stats["XML-文书数"] += 1
                # 与原文件保持同一编码（无声明默认 utf-8）
                enc = "utf-8"
                m = re.search(rb'encoding="([^"]+)"', data[:200])
                if m:
                    enc = m.group(1).decode()
                try:
                    zout.writestr(new_name, new_text.encode(enc))
                except UnicodeEncodeError:
                    zout.writestr(new_name, new_text.encode("utf-8"))
        zf.close()

    # ---------------- Pass 2.5：完整性核对 ----------------
    def integrity(self):
        """核对清洗前后：表格行/列数一致、每包 XML 文书数一致（脱敏不许破坏结构）"""
        issues = 0
        for pdir_name in sorted(os.listdir(SRC)):
            pdir = os.path.join(SRC, pdir_name)
            if not os.path.isdir(pdir):
                continue
            fake = self.fake_of[pdir_name]
            outdir = os.path.join(DST, "住院", fake)
            for f in sorted(glob.glob(os.path.join(pdir, "*.xls"))):
                sh = xlrd.open_workbook(f).sheet_by_index(0)
                out = os.path.join(outdir, os.path.basename(f).replace(".xls", ".xlsx"))
                from openpyxl import load_workbook
                ws = load_workbook(out, read_only=True).active
                if (ws.max_row - 1, ws.max_column) != (sh.nrows - 1, sh.ncols):
                    print(f"  ✗ {fake}/{os.path.basename(out)}: "
                          f"{sh.nrows - 1}x{sh.ncols} → {ws.max_row - 1}x{ws.max_column}")
                    issues += 1
            src_zips = sorted(glob.glob(os.path.join(pdir, "*.zip")))
            out_zips = sorted(glob.glob(os.path.join(outdir, "*.zip")))
            if len(src_zips) != len(out_zips):
                print(f"  ✗ {fake}: zip 数 {len(src_zips)} → {len(out_zips)}")
                issues += 1
                continue
            for zs, zo in zip(src_zips, out_zips):
                a, b = zipfile.ZipFile(zs), zipfile.ZipFile(zo)
                if len(a.namelist()) != len(b.namelist()):
                    print(f"  ✗ {fake}/{os.path.basename(zo)}: 文书数不一致")
                    issues += 1
                a.close()
                b.close()
        if issues == 0:
            print("[完整性] ✓ 所有表格行列数与 XML 文书数与原始数据一致")
        return issues

    # ---------------- Pass 3：校验 ----------------
    def verify(self):
        """
        在清洗后的输出里反向搜索真实值。
        校验语义与替换语义一致：
          - 姓名/住院号/电话/住址/医院名：子串搜索（这些长值出现在任何位置都是泄露）
          - 工号：边界搜索（长编号里恰好包含这段数字不算，替换时也是边界替换）
          - 年份：只查「日期形态」（YYYY-MM-DD / YYYY年 / YYYYMMDD / YYYY/MM）
          - 警告级：XML 文本节点里孤立出现的真实年份（可能是没见过的日期写法）
        """
        reals = {}
        idx = 0
        for pid in self.pid_map:
            reals[f"住院号#{idx}"] = pid
            idx += 1
        for n in self.name_map:
            reals[f"患者姓名#{idx}"] = n
            idx += 1
        for n in self.staff_map:
            reals[f"医护姓名#{idx}"] = n
            idx += 1
        for p in self.phone_map:
            reals[f"手机号#{idx}"] = p
            idx += 1
        for a in self.addr_map:
            reals[f"住址前6字#{idx}"] = a[:6]
            idx += 1
        for h in self.hospital_map:
            reals[f"医院名#{idx}"] = h
            idx += 1
        code_res = {c: re.compile(r"(?<![\dA-Za-z])" + re.escape(c) + r"(?![\dA-Za-z])")
                    for c in self.code_map}
        year_sep_res = []
        for y in sorted(self.real_years):
            year_sep_res.append((y, re.compile(rf"(?<!\d){y}[-/]\d{{1,2}}(?!\d)")))
            year_sep_res.append((y, re.compile(rf"(?<!\d){y}年(?!\d)")))
            # 紧凑日期形态（YYYYMMDD 或更长的日期前缀数字串，如文档ID/时间戳）
            year_sep_res.append((y, re.compile(rf"(?<!\d){y}(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])")))

        # 收集输出内容：全文 blob + XML 文本节点 + xlsx 数字单元格
        blob, xml_nodes, xlsx_nums = [], [], []
        for root, _dirs, files in os.walk(DST):
            for fn in files:
                p = os.path.join(root, fn)
                blob.append(fn)
                if fn.endswith(".xlsx"):
                    from openpyxl import load_workbook
                    wb = load_workbook(p, read_only=True)
                    for ws in wb.worksheets:
                        for row in ws.iter_rows(values_only=True):
                            for v in row:
                                if v is None or hasattr(v, "year"):
                                    continue
                                if isinstance(v, (int, float)):
                                    xlsx_nums.append((fn, str(int(v))))
                                else:
                                    blob.append(str(v))
                    wb.close()
                elif fn.endswith(".zip"):
                    z = zipfile.ZipFile(p)
                    for n in z.namelist():
                        blob.append(n)
                        text = self._decode(z.read(n))
                        blob.append(text)
                        xml_nodes.extend(t.strip() for t in re.findall(r">([^<>]+)<", text))
                    z.close()
        full = "\n".join(blob)

        failures, warnings = [], []
        for label, real in reals.items():
            if full.count(real):
                failures.append(label)
        for code, res in code_res.items():
            if res.search(full):
                failures.append(f"工号({len(self.code_map)}个中的某个)")
                break
        for y, res in year_sep_res:
            if res.search(full):
                failures.append(f"真实年份{y}(日期形态)")
        for node in xml_nodes:
            for y in sorted(self.real_years):
                if re.search(rf"(?<!\d){y}(?!\d)", node):
                    warnings.append(f"XML文本节点孤立年份{y}: 模式 {self._pat(node)}")

        # xlsx 数字单元格：长得像日期且年份为真实年份 → 泄露
        for fn, s in xlsx_nums:
            for y in sorted(self.real_years):
                if s.startswith(str(y)) and len(s) >= 8 and self._valid_ymd(s[:8]):
                    failures.append(f"表格数字日期({fn})")
                    break
        # 警告级：自由文本地址样式残留、联系人标签后仍跟人名样式
        # （排除「虚构省…」——那是替换后的假地址，不是残留）
        addr_n = 0
        for m in re.finditer(r"[\u4e00-\u9fff]{2,8}(?:省|自治区)[\u4e00-\u9fff]{0,8}(?:市|自治州|县|区|旗)"
                             r"[\u4e00-\u9fff]{0,12}?(?:路|街道|街|巷|村|镇|乡|小区|花园|大道|广场)", full):
            if "虚构" not in m.group(0):
                addr_n += 1
        if addr_n:
            warnings.append(f"输出中仍有「省/市/区+路/村」地址样式 {addr_n} 处，需人工复核")
        contact_n = len(re.findall(r"联系人(?:姓名)?\s*[:：]\s*[\u4e00-\u9fff]{2,4}(?!甲)", full))
        if contact_n:
            warnings.append(f"输出中「联系人：人名」样式 {contact_n} 处，需人工复核")
        return failures, warnings

    @staticmethod
    def _pat(s):
        s = re.sub(r"\d", "9", s)
        s = re.sub(r"[A-Za-z]", "a", s)
        s = re.sub(r"[\u4e00-\u9fff]", "X", s)
        return s[:50]

    def save_mapping(self):
        m = {
            "说明": "真实值→虚构值映射表，仅本地留存，禁止提交 git，核对后可删除",
            "患者": self.name_map,
            "住院号": self.pid_map,
            "医护姓名": self.staff_map,
            "工号": self.code_map,
            "电话": self.phone_map,
            "身份证": {k[:6] + "**": v for k, v in self.idcard_map.items()},
            "住址": {k[:6] + "**": v for k, v in self.addr_map.items()},
            "医院名": self.hospital_map,
            "日期偏移天数": self.offset_days,
        }
        with open(MAPPING_PATH, "w", encoding="utf-8") as f:
            json.dump(m, f, ensure_ascii=False, indent=2)


def main():
    eng = DeidEngine()
    eng.scan()
    eng.transform()
    eng.save_mapping()
    print("\n[统计] 替换明细:", dict(eng.stats))
    if eng.integrity():
        print("  完整性核对未通过！")
        sys.exit(1)
    failures, warnings = eng.verify()
    print("\n[校验] 输出目录反向搜索真实值：")
    if warnings:
        shown = set()
        for w in warnings:
            if w not in shown:
                shown.add(w)
                print(f"  ⚠ {w}")
        if len(warnings) > len(shown):
            print(f"  ⚠ …另有 {len(warnings) - len(shown)} 处同类警告")
    if failures:
        for label in sorted(set(failures)):
            print(f"  ✗ {label}")
        print("  校验未通过，请检查！")
        sys.exit(1)
    print("  ✓ 全部通过：姓名/住院号/医护/工号/电话/住址/医院名/真实年份(日期形态) 均 0 残留")
    print(f"\n完成。输出目录: {DST}")


if __name__ == "__main__":
    main()
