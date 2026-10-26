"""模块 2：新闻与事件分析。

把财联社、东方财富、新闻联播与海外市场数据整理成可复核的事件清单：

* 事件属于政策 / 产业 / 公司 / 海外哪一类
* 来源、发布时间、原文链接
* 可能影响的行业与传导路径
* 可信度与不确定性

行业映射是关键词候选，不是因果结论。它的作用是先把当天几百条快讯收敛到
十几条值得继续查的事件，具体影响仍要由模型结合公告、行业数据和常识判断。
"""

from __future__ import annotations

import math
import re
from datetime import date, datetime, time, timedelta

import akshare as ak

NEWS_LIMIT = 12
MAX_SUMMARY = 260

POLICY = "政策"
INDUSTRY = "产业"
COMPANY = "公司"
OVERSEAS = "海外"

POLICY_WORDS = (
    "国务院", "央行", "人民银行", "证监会", "发改委", "财政部", "工信部",
    "商务部", "住建部", "市场监管", "统计局", "税务总局", "政策", "通知",
    "意见", "规划", "降准", "降息", "关税", "补贴", "监管", "试点",
    "条例", "办法", "规定", "税收", "医保", "集采", "专项债", "特别国债",
)
COMPANY_WORDS = (
    "公告", "业绩", "预增", "预亏", "回购", "减持", "增持", "并购", "重组",
    "中标", "合同", "分红", "停牌", "复牌", "退市", "立案", "处罚", "问询",
    "解禁", "定增", "配股", "股东", "实控人", "董事长",
)
MAJOR_COMPANY_WORDS = (
    "业绩预告", "业绩快报", "预增", "预亏", "回购", "减持", "增持", "并购",
    "重组", "中标", "合同", "分红", "退市", "立案", "处罚", "定增", "实控人",
)
OVERSEAS_STRONG_WORDS = (
    "美股", "美联储", "纳指", "纳斯达克", "道指", "标普", "欧股",
    "欧洲央行", "日本央行", "港股", "恒生", "美债", "非农", "CPI", "PCE",
    "加息", "降息预期", "英国", "加拿大", "日本", "欧洲", "韩国", "美国",
)
OVERSEAS_MACRO_WORDS = ("原油", "黄金", "美元", "汇率")
LOW_CONFIDENCE_WORDS = (
    "传闻", "网传", "据悉", "未经证实", "市场消息", "知情人士", "或将",
)
OFFICIAL_WORDS = (
    "国务院", "央行", "人民银行", "证监会", "发改委", "财政部", "工信部",
    "商务部", "住建部", "统计局", "税务总局",
)
# 这些词说明是营销/观点/行情播报，不是当天必须处理的事件
NOISE_WORDS = (
    "ETF", "基金", "净值", "涨超", "飘红", "标的指数", "机构看好",
    "研报", "晨会", "首席", "观点", "展望", "认为", "表示", "解读",
    "专访", "对话", "午盘", "收盘播报", "开盘播报", "盘中快照",
)

# 行业名尽量对齐同花顺行业板块，方便和模块 3 的轮动数据互相印证
INDUSTRY_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("半导体", ("半导体", "芯片", "集成电路", "晶圆", "光刻", "封测", "存储芯片")),
    ("光伏设备", ("光伏", "硅料", "硅片", "组件", "逆变器", "太阳能", "钙钛矿")),
    ("风电设备", ("风电", "海上风电", "风机")),
    ("电池", ("锂电", "电池", "储能", "充电桩", "固态电池")),
    ("汽车整车", ("新能源车", "整车", "整车厂", "车企", "车型", "汽车", "智能驾驶", "自动驾驶")),
    ("汽车零部件", ("汽车零部件", "零部件", "轮胎", "座椅")),
    ("化学制药", ("创新药", "化学制药", "仿制药", "医药", "医保", "集采")),
    ("生物制品", ("疫苗", "生物制品", "血制品", "生物医药")),
    ("医疗器械", ("医疗器械", "医疗设备", "影像设备")),
    ("中药", ("中药", "中医药")),
    ("医疗服务", ("医疗服务", "医院", "CXO", "临床")),
    ("白酒", ("白酒", "酒企", "茅台", "五粮液")),
    ("食品加工制造", ("食品", "零食", "调味品", "预制菜")),
    ("饮料制造", ("饮料", "乳业", "啤酒")),
    ("房地产", ("房地产", "地产", "楼市", "房贷", "保障房", "城中村")),
    ("银行", ("银行", "信贷", "社融", "存款", "贷款")),
    ("证券", ("券商", "证券", "资本市场", "IPO", "注册制", "并购重组",
              "债市", "债券", "国债", "交易所", "非银")),
    ("保险", ("保险", "险企", "保费")),
    ("军工装备", ("军工", "国防", "航空", "航天", "导弹", "军贸")),
    ("军工电子", ("军工电子", "雷达", "军工芯片")),
    ("软件开发", ("人工智能", "AI", "大模型", "信创", "软件", "算力")),
    ("IT服务", ("数据中心", "云计算", "算力租赁", "服务器")),
    ("计算机设备", ("计算机设备", "服务器", "PC")),
    ("通信设备", ("5G", "6G", "通信设备", "光模块", "交换机")),
    ("自动化设备", ("机器人", "人形机器人", "减速器", "伺服", "工控")),
    ("专用设备", ("专用设备", "锂电设备", "光伏设备", "半导体设备")),
    ("工程机械", ("工程机械", "挖掘机", "起重机")),
    ("贵金属", ("黄金", "贵金属", "金价")),
    ("油气开采及服务", ("原油", "石油", "天然气", "油气", "页岩气")),
    ("石油加工贸易", ("炼化", "成品油", "石油加工")),
    ("煤炭开采加工", ("煤炭", "动力煤", "焦煤")),
    ("电力", ("电力", "火电", "水电", "核电", "电价")),
    ("工业金属", ("铜", "铝", "钢铁", "工业金属")),
    ("小金属", ("稀土", "钨", "钼", "小金属")),
    ("能源金属", ("锂矿", "钴", "镍", "能源金属")),
    ("种植业与林业", ("种业", "粮食", "农业", "转基因")),
    ("养殖业", ("养猪", "生猪", "养殖", "禽")),
    ("旅游及酒店", ("旅游", "酒店", "景区", "免签")),
    # “免税店”写全，避免把“免税进口棉花”这类税收政策误判成零售
    ("零售", ("免税店", "零售", "百货", "超市")),
    ("互联网电商", ("电商", "直播带货", "跨境电商")),
    ("游戏", ("游戏", "版号", "手游")),
    ("文化传媒", ("传媒", "广告", "出版", "短剧")),
    ("影视院线", ("影视", "电影", "院线")),
    ("物流", ("物流", "快递", "仓储")),
    ("港口航运", ("港口", "航运", "集运", "运价")),
    ("化学制品", ("化工", "化学品", "农药", "化肥")),
    ("建筑材料", ("建材", "水泥", "玻璃", "防水")),
    ("建筑装饰", ("基建", "建筑", "工程", "装饰")),
    ("白色家电", ("白电", "空调", "冰箱", "洗衣机")),
    ("黑色家电", ("黑电", "电视", "面板")),
    ("消费电子", ("消费电子", "苹果", "华为", "手机", "MR")),
    ("环境治理", ("环保", "污水", "固废", "碳中和")),
    ("教育", ("教育", "职业教育", "培训")),
    ("美容护理", ("化妆品", "医美", "美容")),
    ("纺织制造", ("纺织", "棉", "服装代工")),
    ("服装家纺", ("服装", "家纺", "品牌服饰")),
    ("造纸", ("造纸", "纸浆", "纸价")),
)

INDUSTRY_PATHS = {
    "半导体": "半导体/芯片需求与国产替代预期 → 设计、制造、设备、材料",
    "光伏设备": "装机需求与产业链价格 → 硅料、组件、逆变器盈利",
    "风电设备": "招标与装机节奏 → 风机、海缆、塔筒订单",
    "电池": "新能源车与储能需求 → 电池排产、材料价格",
    "汽车整车": "销量与价格战 → 整车盈利与市场份额",
    "汽车零部件": "整车排产与客户结构 → 零部件订单与毛利率",
    "化学制药": "医保支付与集采价格 → 药品放量与利润率",
    "生物制品": "批签发与研发进展 → 疫苗、血制品销售",
    "医疗器械": "医院采购与集采 → 设备与耗材放量",
    "中药": "基药与医保政策 → 中药处方与品牌溢价",
    "医疗服务": "就诊需求与支付能力 → 医院、CXO 订单",
    "白酒": "消费场景与渠道库存 → 批价、回款与利润率",
    "食品加工制造": "成本与终端需求 → 毛利率与渠道库存",
    "饮料制造": "动销与渠道扩张 → 收入增速与费用率",
    "房地产": "销售与融资政策 → 拿地、开工、竣工链",
    "银行": "利率与信贷投放 → 净息差、资产质量",
    "证券": "市场成交与政策 → 经纪、投行、自营收入",
    "保险": "利率与保费增长 → 投资端与负债端",
    "军工装备": "订单与国防预算 → 主机厂、配套企业排产",
    "军工电子": "装备信息化 → 雷达、电子对抗、军工芯片",
    "软件开发": "AI 与信创投入 → 软件订单、云资源消耗",
    "IT服务": "算力需求 → 数据中心、服务器、租赁价格",
    "计算机设备": "算力与政企采购 → 服务器、PC 出货",
    "通信设备": "运营商与海外资本开支 → 光模块、交换机",
    "自动化设备": "制造业投资与机器人量产 → 减速器、伺服、本体",
    "专用设备": "下游扩产 → 设备订单与验收节奏",
    "工程机械": "基建与地产开工 → 挖机销量、出口",
    "贵金属": "实际利率与避险需求 → 金价、矿业利润",
    "油气开采及服务": "油价与资本开支 → 开采、油服订单",
    "石油加工贸易": "原油成本与成品油价差 → 炼化利润",
    "煤炭开采加工": "煤价与电煤需求 → 吨煤利润",
    "电力": "电价与来水/煤价 → 火电、水电、核电盈利",
    "工业金属": "地产与制造业需求 → 铜铝价格、加工费",
    "小金属": "供给约束与新能源需求 → 稀土、钨钼价格",
    "能源金属": "锂钴镍价格 → 矿端与冶炼利润",
    "种植业与林业": "粮价与政策 → 种业、种植收益",
    "养殖业": "猪价与存栏 → 养殖利润、饲料需求",
    "旅游及酒店": "出行人次与客单价 → 景区、酒店收入",
    "零售": "消费政策与客流 → 免税、百货销售",
    "互联网电商": "流量与履约成本 → 平台 GMV、货币化率",
    "游戏": "版号与流水 → 游戏公司收入、买量成本",
    "文化传媒": "广告需求与内容供给 → 传媒公司收入",
    "影视院线": "票房与排片 → 院线、制片方分账",
    "物流": "快递量与原燃料成本 → 单票收入、利润率",
    "港口航运": "运价与吞吐量 → 集运、干散货盈利",
    "化学制品": "原料价格与下游需求 → 价差、开工率",
    "建筑材料": "地产与基建开工 → 水泥、玻璃价格",
    "建筑装饰": "基建投资与回款 → 新签订单、现金流",
    "白色家电": "地产竣工与以旧换新 → 空调、冰洗出货",
    "黑色家电": "面板价格与需求 → 电视整机盈利",
    "消费电子": "新机周期与客户份额 → 零部件、组装订单",
    "环境治理": "环保政策与项目回款 → 运营、设备订单",
    "教育": "招生与政策 → 职业教育、培训收入",
    "美容护理": "消费力与新品 → 化妆品、医美渗透率",
    "纺织制造": "订单转移与棉价 → 代工毛利率",
    "服装家纺": "零售流水与库存 → 品牌服饰折扣率",
    "造纸": "纸浆价格与需求 → 纸企价差",
}


def _records(frame) -> list[dict]:
    if frame is None or getattr(frame, "empty", True):
        return []
    return frame.to_dict("records")


def _number(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(number) else number


def _round(value, digits: int = 2):
    return None if value is None else round(value, digits)


def _norm_date(value) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    text = str(value or "").strip()
    digits = re.sub(r"\D", "", text)
    if len(digits) >= 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return text[:10]


def _norm_time(value) -> str:
    if isinstance(value, time):
        return value.strftime("%H:%M:%S")
    text = str(value or "").strip()
    match = re.search(r"(\d{1,2}:\d{2}(?::\d{2})?)", text)
    return match.group(1) if match else text


def _published_at(date_value, time_value=None) -> str:
    day = _norm_date(date_value)
    if time_value is not None:
        clock = _norm_time(time_value)
        return f"{day} {clock}".strip() if clock else day
    text = str(date_value or "").strip()
    if " " in text and re.search(r"\d{1,2}:\d{2}", text):
        return f"{day} {_norm_time(text)}"
    return day


# 快讯平台常把栏目名塞进标题，留着会让读者以为是事件本身
TITLE_LABELS = ("电报解读", "快讯", "要闻", "焦点", "独家", "重磅", "公告")


def _strip_label(title: str) -> str:
    match = re.match(r"^[【\[]?\s*([^】\[]{1,12})[】\]]\s*", title)
    if match and match.group(1).strip() in TITLE_LABELS:
        title = title[match.end():]
    return title.lstrip("】").strip("【】 ")


def _title_from(record: dict, source_kind: str) -> str:
    title = str(
        record.get("标题") or record.get("title") or ""
    ).strip()
    title = _strip_label(title)
    if title:
        return title[:100]
    content = str(
        record.get("内容") or record.get("content") or record.get("摘要") or ""
    ).strip()
    match = re.search(r"【(.+?)】", content)
    if match:
        label = match.group(1).strip()
        if label not in TITLE_LABELS:
            return label[:100]
        content = content[match.end():].strip()
    first = re.split(r"[。！？\n]", content, maxsplit=1)[0].strip()
    return first[:100]


def _summary_from(record: dict, source_kind: str) -> str:
    text = str(
        record.get("摘要") or record.get("内容") or record.get("content") or ""
    ).strip()
    text = re.sub(r"^【.+?】", "", text).strip()
    text = re.sub(r"^财联社.*?电，", "", text).strip()
    return text[:MAX_SUMMARY]


def _source_fields(record: dict, source_kind: str) -> tuple[str, str]:
    """返回 ``(发布日期, 发布时间)`` 的原始值。"""
    if source_kind == "cls":
        return str(record.get("发布日期") or ""), str(record.get("发布时间") or "")
    if source_kind == "cctv":
        return str(record.get("date") or ""), ""
    return str(record.get("发布时间") or ""), ""


def _normalize(record: dict, source_kind: str) -> dict | None:
    title = _title_from(record, source_kind)
    if not title:
        return None
    date_value, time_value = _source_fields(record, source_kind)
    published = _published_at(date_value, time_value or None)
    url = str(record.get("链接") or record.get("url") or "").strip() or None
    return {
        "title": title,
        "summary": _summary_from(record, source_kind),
        "published_at": published,
        "url": url,
    }


_ASCII_TOKEN = re.compile(r"^[A-Za-z0-9]+$")


def _contains(text: str, word: str) -> bool:
    """英文/数字关键词按词边界匹配，避免 EPC 撞上 PC、MAIN 撞上 AI。"""
    if _ASCII_TOKEN.match(word):
        pattern = rf"(?<![A-Za-z0-9]){re.escape(word)}(?![A-Za-z0-9])"
        return re.search(pattern, text) is not None
    return word in text


def _matches(text: str, words: tuple[str, ...]) -> list[str]:
    return [word for word in words if _contains(text, word)]


def _industries(title: str, summary: str = "") -> list[dict]:
    """标题命中权重高于正文：正文里一处泛词不足以认定行业相关。"""
    hits: list[tuple[int, int, str, list[str]]] = []
    for order, (industry, words) in enumerate(INDUSTRY_RULES):
        in_title = [word for word in words if _contains(title, word)]
        in_summary = [
            word for word in words
            if word not in in_title and _contains(summary, word)
        ]
        score = 3 * len(in_title) + len(in_summary)
        if not in_title and score < 2:
            continue
        hits.append((score, order, industry, in_title + in_summary))
    # 同分时按规则表的顺序（大致按行业权重）而不是名字拼音
    hits.sort(key=lambda item: (-item[0], item[1]))
    seen: set[str] = set()
    result: list[dict] = []
    for _, _, industry, matched in hits:
        if industry in seen:
            continue
        seen.add(industry)
        result.append({"name": industry, "keywords": matched[:5]})
        if len(result) >= 3:
            break
    return result


def _category(title: str, text: str, industries: list[dict]) -> str | None:
    """先看标题定性质，标题不够明确时再借助正文；避免把公司新闻误判成政策。

    海外新闻以标题里的市场/国家词为主，正文里的“美元/原油”只作为补充，
    否则一条公司成本新闻里出现“美元”就会被归到海外。
    """
    official_title = _matches(title, OVERSEAS_STRONG_WORDS)
    if official_title:
        return OVERSEAS
    policy_title = _matches(title, POLICY_WORDS)
    company_title = _matches(title, COMPANY_WORDS)
    if _official(title) or (policy_title and not company_title):
        return POLICY
    if company_title and _matches(title, MAJOR_COMPANY_WORDS):
        return COMPANY
    if industries:
        return INDUSTRY
    if _matches(text, OVERSEAS_STRONG_WORDS):
        return OVERSEAS
    if _matches(text, POLICY_WORDS):
        return POLICY
    if _matches(text, OVERSEAS_MACRO_WORDS) and not company_title:
        return OVERSEAS
    return None


def _impact_path(category: str, industries: list[dict]) -> str:
    if not industries:
        return "事件 → 市场风险偏好 → 指数与相关板块（行业映射不明确）"
    path = INDUSTRY_PATHS.get(
        industries[0]["name"], "该行业供需、价格与盈利预期"
    )
    if category == POLICY:
        return f"政策 → {path}"
    if category == OVERSEAS:
        return f"海外市场 → 风险偏好/汇率/商品价格 → {path}"
    if category == COMPANY:
        return f"公司事件 → {path}（先看直接影响，再看同业比较）"
    return f"产业变化 → {path}"


def _official(text: str) -> bool:
    return any(word in text for word in OFFICIAL_WORDS)


def _confidence(source_names: list[str], text: str) -> str:
    low = any(word in text for word in LOW_CONFIDENCE_WORDS)
    if low and len(source_names) < 2:
        return "低"
    if "新闻联播" in source_names or _official(text) or len(source_names) >= 2:
        return "高"
    return "中"


def _uncertainty(category: str, industries: list[dict], confidence: str) -> str:
    if category == POLICY:
        text = "政策细则、执行力度与落地时间未定"
    elif category == OVERSEAS:
        text = "隔夜或外部市场变化，A 股映射不一定同步"
    elif category == COMPANY:
        text = "公告细节、影响金额与会计处理待核"
    else:
        text = "单条新闻不足以确认行业趋势，需后续订单、价格与资金数据验证"
    if not industries:
        text += "；行业映射不明确，可能只影响风险偏好"
    if confidence == "低":
        text = "消息尚未获得官方确认；" + text
    return text


def _score(event: dict, today: str) -> float:
    weights = {POLICY: 3.0, OVERSEAS: 2.5, INDUSTRY: 2.0, COMPANY: 1.0}
    score = weights.get(event["category"], 0.0)
    score += min(len(event["industries"]), 3) * 0.6
    text = event["title"] + " " + event["summary"]
    if _official(text):
        score += 0.8
    if event["published_at"].startswith(today):
        score += 0.5
    score += min(len(event["keywords"]), 4) * 0.1
    if event["confidence"] == "高":
        score += 0.4
    if any(word in event["title"] for word in NOISE_WORDS):
        score -= 0.8
    return round(score, 2)


def _is_noise(title: str) -> bool:
    """ETF 行情、基金净值、盘中播报不是当天要处理的“事件”。"""
    hard_noise = (
        "ETF", "净值", "涨超", "飘红", "标的指数",
        "午盘", "收盘播报", "开盘播报", "盘中快照",
    )
    return any(word in title for word in hard_noise) and not _official(title)


def _make_event(
    record: dict,
    source_name: str,
    source_id: str,
    today: str,
    source_kind: str,
) -> dict | None:
    normalized = _normalize(record, source_kind)
    if normalized is None or _is_noise(normalized["title"]):
        return None
    text = normalized["title"] + " " + normalized["summary"]
    industries = _industries(normalized["title"], normalized["summary"])
    category = _category(normalized["title"], text, industries)
    if category is None:
        return None
    if category == INDUSTRY and not industries:
        return None

    source = {
        "name": source_name,
        "url": normalized["url"],
        "published_at": normalized["published_at"],
        "source_id": source_id,
    }
    confidence = _confidence([source_name], text)
    event = {
        "title": normalized["title"],
        "summary": normalized["summary"],
        "category": category,
        "source": source_name,
        "source_id": source_id,
        "published_at": normalized["published_at"],
        "url": normalized["url"],
        "sources": [source],
        "industries": industries,
        "keywords": _matches(
            text,
            POLICY_WORDS + COMPANY_WORDS + OVERSEAS_STRONG_WORDS + OVERSEAS_MACRO_WORDS,
        )[:6],
        "confidence": confidence,
        "uncertainty": _uncertainty(category, industries, confidence),
    }
    event["impact_path"] = _impact_path(category, industries)
    event["score"] = _score(event, today)
    return event


def _dedupe_key(title: str) -> str:
    return re.sub(r"[^0-9A-Za-z一-鿿]", "", title)[:32]


def _merge_sources(first: list[dict], second: list[dict]) -> list[dict]:
    merged: list[dict] = []
    seen: set[tuple] = set()
    for item in first + second:
        key = (item.get("name"), item.get("url"), item.get("published_at"))
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
    return merged


def _merge_industries(first: list[dict], second: list[dict]) -> list[dict]:
    by_name = {item["name"]: item for item in first}
    for item in second:
        if item["name"] not in by_name:
            by_name[item["name"]] = item
    return list(by_name.values())[:3]


def dedupe_events(events: list[dict]) -> list[dict]:
    """同一事件被多家媒体报道时合并来源，而不是重复列出。"""
    merged: dict[str, dict] = {}
    for event in events:
        key = _dedupe_key(event.get("title") or "")
        if not key:
            continue
        existing = merged.get(key)
        if existing is None:
            merged[key] = dict(event)
            continue
        existing["sources"] = _merge_sources(
            existing.get("sources") or [], event.get("sources") or []
        )
        existing["industries"] = _merge_industries(
            existing.get("industries") or [], event.get("industries") or []
        )
        existing["keywords"] = list(dict.fromkeys(
            (existing.get("keywords") or []) + (event.get("keywords") or [])
        ))[:6]
        existing["published_at"] = max(
            str(existing.get("published_at") or ""),
            str(event.get("published_at") or ""),
        )
        existing["summary"] = max(
            str(existing.get("summary") or ""),
            str(event.get("summary") or ""),
            key=len,
        )
        names = [item.get("name") for item in existing["sources"] if item.get("name")]
        text = existing["title"] + " " + existing["summary"]
        existing["confidence"] = _confidence(names, text)
        existing["uncertainty"] = _uncertainty(
            existing["category"], existing["industries"], existing["confidence"]
        )
        existing["impact_path"] = _impact_path(
            existing["category"], existing["industries"]
        )
        existing["score"] = round(
            max(existing.get("score") or 0, event.get("score") or 0)
            + 0.3 * (len(existing["sources"]) - 1),
            2,
        )
    result = list(merged.values())
    result.sort(
        key=lambda item: (item.get("score") or 0, item.get("published_at") or ""),
        reverse=True,
    )
    return result


CATEGORY_LIMITS = {POLICY: 6, INDUSTRY: 4, COMPANY: 3, OVERSEAS: 3}
POLICY_NO_INDUSTRY_LIMIT = 2


def select_events(events: list[dict], limit: int) -> list[dict]:
    """按类别配额挑选，避免政策或公司新闻把行业与海外事件全部挤掉。"""
    selected: list[dict] = []
    counts: dict[str, int] = {}
    policy_without_industry = 0
    for event in events:
        if len(selected) >= limit:
            break
        category = event.get("category") or ""
        if counts.get(category, 0) >= CATEGORY_LIMITS.get(category, limit):
            continue
        if category == POLICY and not event.get("industries"):
            if policy_without_industry >= POLICY_NO_INDUSTRY_LIMIT:
                continue
            policy_without_industry += 1
        selected.append(event)
        counts[category] = counts.get(category, 0) + 1
    return selected


def _overseas_markets(today: str) -> tuple[list[dict], dict[str, str]]:
    items: list[dict] = []
    errors: dict[str, str] = {}
    for symbol, name in (
        (".INX", "标普500"),
        (".DJI", "道琼斯工业指数"),
        (".IXIC", "纳斯达克综合指数"),
    ):
        try:
            rows = _records(ak.index_us_stock_sina(symbol=symbol)[-3:])
            if len(rows) < 2:
                errors[symbol] = "历史数据不足"
                continue
            last, previous = rows[-1], rows[-2]
            close = _number(last.get("close"))
            prev_close = _number(previous.get("close"))
            if close is None or not prev_close:
                errors[symbol] = "收盘价缺失"
                continue
            items.append({
                "name": name,
                "code": symbol,
                "market": "美股",
                "date": _norm_date(last.get("date")),
                "close": _round(close),
                "chg_pct": _round((close / prev_close - 1) * 100),
            })
        except Exception as error:  # noqa: BLE001
            errors[symbol] = str(error)

    try:
        rows = _records(ak.stock_hk_index_spot_sina())
        by_code = {str(row.get("代码") or ""): row for row in rows}
        for code, name in (("HSI", "恒生指数"), ("HSTECH", "恒生科技指数")):
            row = by_code.get(code)
            if row is None:
                continue
            price = _number(row.get("最新价"))
            change = _number(row.get("涨跌幅"))
            if price is None:
                continue
            items.append({
                "name": name,
                "code": code,
                "market": "港股",
                "date": today,
                "close": _round(price),
                "chg_pct": _round(change),
            })
    except Exception as error:  # noqa: BLE001
        errors["HK"] = str(error)
    return items, errors


def build_news_events(limit: int = NEWS_LIMIT) -> dict:
    """取当日政策、产业、公司与海外事件；各来源独立失败。"""
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    stamp = now.strftime("%Y%m%d%H%M%S")
    events: list[dict] = []
    feeds: list[dict] = []
    errors: dict[str, str] = {}

    def collect(
        name: str,
        detail: str,
        source_id: str,
        rows: list[dict],
        kind: str,
        as_of: str,
    ) -> None:
        count = 0
        for row in rows:
            event = _make_event(row, name, source_id, today, kind)
            if event is not None:
                events.append(event)
                count += 1
        feeds.append({
            "name": name,
            "detail": detail,
            "source_id": source_id,
            "count": count,
            "as_of": as_of,
        })

    try:
        rows = _records(ak.stock_info_global_cls())
        collect(
            "财联社", "市场快讯", f"news:cls:{stamp}", rows, "cls", today,
        )
    except Exception as error:  # noqa: BLE001
        errors["cls"] = f"财联社：{error}"

    try:
        rows = _records(ak.stock_info_global_em())
        collect(
            "东方财富", "财经快讯", f"news:em:{stamp}", rows, "em", today,
        )
    except Exception as error:  # noqa: BLE001
        errors["em"] = f"东方财富：{error}"

    # 新闻联播当天内容常在晚间才出；同时看今天与昨天，避免漏掉前一晚的政策
    for offset in (0, 1):
        day = (now - timedelta(days=offset)).strftime("%Y%m%d")
        try:
            rows = _records(ak.news_cctv(date=day))
            collect(
                "新闻联播", "央视新闻", f"news:cctv:{day}", rows, "cctv", day,
            )
        except Exception as error:  # noqa: BLE001
            errors[f"cctv:{day}"] = f"新闻联播：{error}"

    overseas, overseas_errors = _overseas_markets(today)
    if overseas_errors:
        errors.update({
            f"overseas:{key}": f"海外市场：{value}"
            for key, value in overseas_errors.items()
        })
    overseas_as_of = (
        max(item["date"] for item in overseas) if overseas else today
    )
    feeds.append({
        "name": "海外市场",
        "detail": "新浪财经",
        "source_id": f"news:overseas:{overseas_as_of}",
        "count": len(overseas),
        "as_of": overseas_as_of,
    })

    deduped = select_events(
        dedupe_events(events), max(1, int(limit))
    )
    latest = max(
        [event["published_at"] for event in deduped]
        + [overseas_as_of]
        + [today]
    )
    counts: dict[str, int] = {}
    for event in deduped:
        counts[event["category"]] = counts.get(event["category"], 0) + 1
    return {
        "as_of": latest,
        "events": deduped,
        "overseas": overseas,
        "feeds": feeds,
        "counts": counts,
        "errors": errors,
        "notes": [
            "事件按政策、海外、产业、公司的顺序评分排序；同一事件多源报道会合并",
            "行业映射是关键词候选，不是因果结论；需要结合公告、行业数据与常识继续核实",
            "海外市场取最近一个交易日收盘，A 股映射不一定同步",
        ],
    }
