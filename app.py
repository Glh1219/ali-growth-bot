#!/usr/bin/env python3
"""
阿鲤成长记录 - 微信公众号后端服务
部署在 Render.com 上的 Flask 应用，处理微信消息、调用智谱AI生成回复。

环境变量配置：
    ZHIPU_API_KEY   - 智谱AI API密钥
    WX_TOKEN        - 微信公众号Token（自定义）
    WX_AES_KEY      - 微信公众号EncodingAESKey（自定义，可选）
    PORT            - 服务端口（Render自动设置）
"""

import os
import re
import json
import time
import hashlib
import xml.etree.ElementTree as ET
from flask import Flask, request, abort, render_template_string, send_file, jsonify, Response
from zhipuai import ZhipuAI

app = Flask(__name__)

# ============ 配置 ============
ZHIPU_API_KEY = os.environ.get("ZHIPU_API_KEY", "")
WX_TOKEN = os.environ.get("WX_TOKEN", "aligrowth2024")
WX_AES_KEY = os.environ.get("WX_AES_KEY", "")

# 初始化智谱AI客户端
ai_client = None
if ZHIPU_API_KEY:
    ai_client = ZhipuAI(api_key=ZHIPU_API_KEY)

# 阿鲤基础信息（系统提示词用）
CHILD_PROFILE = """你是一个专业的儿童成长记录助手，服务于一个叫"阿鲤"的男孩的家庭。

阿鲤基本信息：
- 性别：男
- 出生日期：2024年3月13日
- 出生身高：47cm，出生体重：2650g
- 当前月龄：约29个月（2岁5个月）

你的职责：
1. 当家长发送身高体重数据时（如"87cm 12.5kg"），确认已记录，并简要评估是否在WHO正常范围内
2. 当家长发送就医/体检信息时，确认记录并追问关键细节
3. 当家长发送疫苗信息时，确认记录并提醒下一次疫苗节点
4. 当家长咨询喂养/育儿问题时，给出专业、温暖、实用的建议，适合2岁左右男孩
5. 回复要简洁（150字以内），语气亲切，像一位懂育儿的朋友

WHO 0-2岁男孩身高参考（P50中位数）：
- 24月：86.8cm, 12.2kg
- 30月：90.9cm, 12.9kg

回复时不要提及你是AI或大模型，直接以"成长助手"身份回复。"""


# ============ 数据存储（JSON文件） ============
DATA_FILE = "/tmp/growth_data.json"

def load_data():
    """加载数据文件"""
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "child": {
            "name": "阿鲤", "gender": "M", "birth_date": "2024-03-13",
            "birth_height_cm": 47, "birth_weight_g": 2650
        },
        "growth_records": [
            {"date": "2024-03-13", "height_cm": 47, "weight_kg": 2.65},
            {"date": "2024-04-16", "height_cm": 52, "weight_kg": 4.0},
            {"date": "2024-06-21", "height_cm": 58, "weight_kg": 6.35},
            {"date": "2024-11-13", "height_cm": 66, "weight_kg": 8.95},
            {"date": "2025-06-11", "height_cm": 74, "weight_kg": 10.7},
            {"date": "2025-10-18", "height_cm": 79, "weight_kg": 11.0},
            {"date": "2025-12-14", "height_cm": 80, "weight_kg": 11.7},
            {"date": "2026-01-12", "height_cm": 80, "weight_kg": 11.5},
            {"date": "2026-03-01", "height_cm": 82, "weight_kg": 12.3},
            {"date": "2026-03-24", "height_cm": 83.5, "weight_kg": 11.5},
            {"date": "2026-04-28", "height_cm": 84, "weight_kg": 12.0},
            {"date": "2026-05-08", "height_cm": 85, "weight_kg": 12.0},
            {"date": "2026-06-04", "height_cm": 85, "weight_kg": 11.7},
            {"date": "2026-07-05", "height_cm": 86.5, "weight_kg": 12.4}
        ],
        "medical_records": [
            {"date": "2024-03-13", "type": "birth", "hospital": "福建医科大学附属第一医院", "department": "产科", "doctor": "", "chief_complaint": "出生（胎膜早破，剖宫产，Apgar 10-10-10）", "exam": "出生体重2650g(P10-25)、身长47cm", "diagnosis": "新生儿健康", "advice": "", "medication": ""},
            {"date": "2024-04-24", "type": "checkup", "hospital": "福建医科大学附属第一医院", "department": "儿科门诊", "doctor": "李权济", "chief_complaint": "42天儿保体检", "exam": "AABR双耳通过；NBNA评分38分；体重4.5kg(P25)、身长54cm(P10-25)、头围36cm(P5-10)", "diagnosis": "婴儿痤疮；婴儿脂溢性皮炎；鞘膜积液（可能）", "advice": "每日补充维生素AD至18岁；加强户外活动1-2小时/日；注意皮肤护理（水温35-37℃、时长不超5分钟）；3月龄复诊", "medication": "日常：AD、D3交替补充；信谊益生菌"},
            {"date": "2024-06-24", "type": "checkup", "hospital": "福建医科大学附属第一医院", "department": "奥体儿科门诊", "doctor": "黄铃沂", "chief_complaint": "3月龄儿保体检", "exam": "AABR双耳通过；NBNA评分38分；6-21社区体检：体重6.5kg(P50-75)、身长58cm(P5)、头围36cm(P10)", "diagnosis": "鞘膜积液", "advice": "加强家庭训练；每日补充维生素D至18岁；皮肤护理（水温35-37℃、时长不超5分钟）；4月龄复诊；辅食建议：从含铁米糊、蛋黄、肉泥、肝泥开始", "medication": ""},
            {"date": "2024-07-31", "type": "checkup", "hospital": "福建医科大学附属第一医院", "department": "儿科门诊", "doctor": "黄铃沂", "chief_complaint": "4月龄儿保体检", "exam": "AMIS17分(10th)；IFANIB78分；体重7.1kg(P25-50)、身长62cm(P5-10)、头围40cm(P5)；面部有湿疹", "diagnosis": "婴儿湿疹", "advice": "加强户外活动2小时/日；补充LGG、DHA；皮肤护理（水温30-35℃、时长不超5分钟、保湿）；依次添加鸡蛋白及花生等辅食；1个月后复诊", "medication": "无新开药品"},
            {"date": "2024-09-11", "type": "checkup", "hospital": "福建医科大学附属第一医院", "department": "儿科门诊", "doctor": "黄铃沂", "chief_complaint": "6月龄儿保体检", "exam": "AMIS28分；IFANIB76分；体重7.72kg(P25-50)、身高65cm(P10-25)、头围42cm(P10-25)", "diagnosis": "婴儿湿疹", "advice": "加强户外活动2小时/日；补充LGG、DHA；皮肤护理；辅食加小麦；1个月后复诊；满6月龄接种流感疫苗", "medication": ""},
            {"date": "2024-10-16", "type": "checkup", "hospital": "福建医科大学附属第一医院", "department": "儿科门诊", "doctor": "黄铃沂", "chief_complaint": "7月龄儿保体检", "exam": "AMIS29分(IFANIB84分)；体重8.2kg(P50-75)、身高67.5cm(P25-50)、头围43cm(P10-25)", "diagnosis": "婴儿湿疹", "advice": "", "medication": ""},
            {"date": "2025-03-19", "type": "checkup", "hospital": "福建医科大学附属第一医院", "department": "儿科门诊", "doctor": "黄铃沂", "chief_complaint": "12月龄儿保体检", "exam": "AMIS48分(IFANIB96分)；体重10kg(P50-75)、身高71.5cm(P2-5)、头围43cm(<P2)；微量血常规", "diagnosis": "婴儿湿疹", "advice": "一日三餐规律，每日奶量500-600ml；增加豆制品、虾等蛋白质摄入；前往林碧云医生处完成DDST测试；3个月后复诊", "medication": ""},
            {"date": "2025-03-24", "type": "checkup", "hospital": "福建医科大学附属第一医院", "department": "儿科门诊", "doctor": "林碧云", "chief_complaint": "1岁儿保复诊", "exam": "DDST：大运动落后，余运动能区正常；扶站扶走、可发babamama、单词、牙6颗", "diagnosis": "儿童常规健康检查", "advice": "", "medication": ""},
            {"date": "2025-09-18", "type": "checkup", "hospital": "仓山有来门诊", "department": "儿科门诊", "doctor": "孙志文", "chief_complaint": "18月龄儿童常规生长发育体检，评估体格、发育、营养、健康状况，排查潜在发育异常", "exam": "测量体重、头围；体格查体（颈部、腹部、外生殖器提示阴囊空虚、包茎；四肢脊柱等）；发育商评估；血常规、粪常规化验；视力屈光筛查；生长发育图表评估", "diagnosis": "发育评估中", "advice": "1.营养膳食：每日和家人共同进餐、定时定量，保证奶量500ml，搭配主食、肉鱼蛋、蔬菜水果，停止夜奶；2.睾丸观察：洗澡、入睡时若摸不到睾丸，前往小儿泌尿外科就诊，动态追踪生长曲线；3.疾病预警：出现拒食、发热、腹泻等及时就医，关注眼部异常信号；4.疫苗：完成18月龄五联、甲肝、麻腮风疫苗接种；5.安全防护：正确使用安全座椅，做好防摔、电源防护；6.早期教育：开展语言、手指精细动作训练；约定2岁（2026-03-17）复查", "medication": ""},
            {"date": "2025-09-18", "type": "checkup", "hospital": "仓山有来门诊", "department": "口腔科", "doctor": "", "chief_complaint": "儿童口腔咬合及口腔肌肉功能、不良口腔习惯筛查评估", "exam": "错颌咬合类型检查，骨性面型评估，恒牙阻生排查；吞咽、舌功能评估；评估咬唇、吸唇、扯物、偏侧咀嚼、夜磨牙等不良口腔习惯", "diagnosis": "口腔检查（初筛）", "advice": "本次记录未见明确诊疗建议，仅完成口腔习惯与咬合初筛；若后续出现咬合异常、口呼吸、夜磨牙、吞咽异常等表现，建议儿童口腔科复诊进一步评估干预", "medication": ""},
            {"date": "2026-03-24", "type": "checkup", "hospital": "福建医科大学附属第一医院", "department": "儿科门诊", "doctor": "刘健", "chief_complaint": "2岁复查", "exam": "体重11.5kg(P10-25)、身高83.5cm(P3-10)、头围46cm(P3)；心脏可闻及皿/6级SM期杂音；心脏彩超待查", "diagnosis": "心脏杂音（待查）", "advice": "一日三餐规律、食物多样化；每日饮奶350-500ml；注意安全；服用维生素AD、钙剂（钙500-600mg/日）；建议行心脏彩超；2岁6个月复诊", "medication": "自备维生素AD、钙剂（钙元素500-600mg/日）"},
            {"date": "2026-06-07", "type": "consult", "hospital": "复旦大学附属华山医院福建医院", "department": "儿科门诊", "doctor": "线上问诊", "chief_complaint": "身高体重落后且停滞较久；咨询睡前奶、是否换奶粉、排查过敏、喂养建议", "exam": "线上问诊", "diagnosis": "喂养困难", "advice": "", "medication": ""},
            {"date": "2026-07-23", "type": "illness", "hospital": "福建省儿童医院", "department": "耳鼻喉科门诊", "doctor": "", "chief_complaint": "声嘶", "exam": "", "diagnosis": "声嘶", "advice": "", "medication": ""},
            {"date": "2026-08-28", "type": "illness", "hospital": "福建省儿童医院", "department": "中医科门诊", "doctor": "", "chief_complaint": "儿童型生长不足", "exam": "", "diagnosis": "儿童型生长不足", "advice": "", "medication": "中药处方：鸡内金、麦芽、稻芽、山药、薏苡仁、白扁豆、神曲、姜半夏、陈皮、茯苓、连翘、太子参、蜻蜓菊、首乌藤（7剂）"}
        ],
        "vaccine_records": [
            {"date": "2024-03-13", "vaccine": "乙肝疫苗", "dose": "1/3", "status": "completed"},
            {"date": "2024-04-16", "vaccine": "乙肝疫苗", "dose": "2/3", "status": "completed"},
            {"date": "2024-05-07", "vaccine": "13价肺炎疫苗", "dose": "1/4", "status": "completed"},
            {"date": "2024-05-21", "vaccine": "百白破IPV和Hib五联疫苗", "dose": "1/4", "status": "completed"},
            {"date": "2024-06-21", "vaccine": "百白破IPV和Hib五联疫苗", "dose": "2/4", "status": "completed"},
            {"date": "2024-07-18", "vaccine": "13价肺炎疫苗", "dose": "2/4", "status": "completed"},
            {"date": "2024-08-09", "vaccine": "百白破IPV和Hib五联疫苗", "dose": "3/4", "status": "completed"},
            {"date": "2024-09-27", "vaccine": "A群流脑疫苗", "dose": "1/2", "status": "completed"},
            {"date": "2024-10-09", "vaccine": "乙肝疫苗", "dose": "3/3", "status": "completed"},
            {"date": "2024-10-29", "vaccine": "13价肺炎疫苗", "dose": "3/4", "status": "completed"},
            {"date": "2024-11-13", "vaccine": "麻风腮疫苗(MMR)", "dose": "1/2", "status": "completed"},
            {"date": "2024-12-17", "vaccine": "乙脑减毒活疫苗", "dose": "1/2", "status": "completed"},
            {"date": "2025-01-24", "vaccine": "A群流脑疫苗", "dose": "2/2", "status": "completed"},
            {"date": "2025-04-16", "vaccine": "13价肺炎疫苗", "dose": "4/4", "status": "completed"},
            {"date": "2025-05-15", "vaccine": "水痘疫苗", "dose": "1/1", "status": "completed"},
            {"date": "2025-10-18", "vaccine": "麻风腮疫苗(MMR)", "dose": "2/2", "status": "completed"},
            {"date": "2025-11-19", "vaccine": "甲肝减毒活疫苗", "dose": "1/1", "status": "completed"},
            {"date": "2025-11-26", "vaccine": "百白破IPV和Hib五联疫苗", "dose": "4/4", "status": "completed"},
            {"date": "2026-04-29", "vaccine": "乙脑减毒活疫苗", "dose": "2/2", "status": "completed"}
        ],
        "chat_history": []
    }

def save_data(data):
    """保存数据文件"""
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ============ 消息解析 ============
def parse_growth_message(text):
    """从消息文本中提取身高体重数据，支持带日期格式"""
    # 匹配多种格式
    patterns = [
        r'(\d+(?:\.\d+)?)\s*cm\s*,?\s*(\d+(?:\.\d+)?)\s*kg',
        r'(\d+(?:\.\d+)?)\s*cm\s+(\d+(?:\.\d+)?)\s*kg',
        r'身高\s*(\d+(?:\.\d+)?)\s*.*?体重\s*(\d+(?:\.\d+)?)',
        r'(\d{2,3}(?:\.\d+)?)\s+(\d{1,2}(?:\.\d+)?)\s*(?:kg|公斤|斤)?',
    ]
    # 先尝试带日期格式：如 "2024.3.13 47 2.65" / "2024-03-13 47 2.65" / "3月13日 47 2.65"
    date_patterns = [
        r'(?P<y>\d{4})[.\-/年](?P<m>\d{1,2})[.\-/月](?P<d>\d{1,2})日?\s*(?P<h>\d{2,3}(?:\.\d+)?)\s+(?P<w>\d{1,2}(?:\.\d+)?)',
        r'(?P<m2>\d{1,2})月(?P<d2>\d{1,2})日\s*(?P<h2>\d{2,3}(?:\.\d+)?)\s+(?P<w2>\d{1,2}(?:\.\d+)?)',
    ]
    for dp in date_patterns:
        m = re.search(dp, text)
        if m:
            gd = m.groupdict()
            height = float(gd.get("h") or gd.get("h2"))
            weight = float(gd.get("w") or gd.get("w2"))
            if 40 < height < 120 and 1.5 < weight < 30:
                if gd.get("y"):
                    year = int(gd["y"])
                else:
                    year = int(time.strftime("%Y"))
                month = int(gd.get("m") or gd.get("m2"))
                day = int(gd.get("d") or gd.get("d2"))
                date_str = "%04d-%02d-%02d" % (year, month, day)
                return {"height_cm": height, "weight_kg": weight, "date": date_str}
    for pattern in patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            height = float(m.group(1))
            weight = float(m.group(2))
            # 合理性检查
            if 40 < height < 120 and 1.5 < weight < 30:
                return {"height_cm": height, "weight_kg": weight}
    return None


def detect_message_type(text):
    """判断消息类型"""
    text_lower = text.lower().strip()
    if text_lower in ["报告", "report", "成长报告", "查看报告"]:
        return "report"
    if text_lower in ["体检", "下次体检", "体检时间", "儿保"]:
        return "checkup_query"
    if text_lower in ["疫苗", "打疫苗", "接种", "下次疫苗"]:
        return "vaccine_query"
    if text_lower in ["帮助", "help", "？", "?", "怎么用"]:
        return "help"
    if text_lower in ["数据", "记录", "查看数据"]:
        return "data_query"
    
    # 尝试解析身高体重
    growth = parse_growth_message(text)
    if growth:
        return "growth"
    
    # 检查是否包含就医/疫苗关键词
    if any(kw in text for kw in ["就诊", "看病", "发烧", "咳嗽", "腹泻", "体检", "儿保"]):
        return "medical"
    if any(kw in text for kw in ["打针", "接种", "疫苗", "打了"]):
        return "vaccine"
    
    return "chat"  # 默认：通用对话


def record_growth(height, weight, record_date=None):
    """记录身高体重，record_date 可选（默认当天）"""
    data = load_data()
    if record_date:
        today = record_date
    else:
        today = time.strftime("%Y-%m-%d")
    record = {"date": today, "height_cm": height, "weight_kg": weight, "source": "wechat"}
    data["growth_records"].append(record)
    data["growth_records"].sort(key=lambda x: x["date"])
    save_data(data)
    return record


def record_medical(description):
    """记录就医"""
    data = load_data()
    today = time.strftime("%Y-%m-%d")
    mtype = "other"
    if "体检" in description or "儿保" in description:
        mtype = "checkup"
    elif "发烧" in description or "就诊" in description or "看病" in description:
        mtype = "illness"
    record = {"date": today, "type": mtype, "hospital": "", "department": "", "doctor": "",
              "chief_complaint": description, "exam": "", "diagnosis": "", "advice": "", "medication": ""}
    data["medical_records"].append(record)
    save_data(data)
    return record


def record_vaccine(description):
    """记录疫苗"""
    data = load_data()
    today = time.strftime("%Y-%m-%d")
    record = {"date": today, "vaccine": description, "dose": "", "status": "completed"}
    data["vaccine_records"].append(record)
    save_data(data)
    return record


# ============ 智谱AI对话 ============
def chat_with_ai(user_message, context_type="chat"):
    """调用智谱AI生成回复"""
    if not ai_client:
        return "AI服务暂未配置，请联系管理员设置API密钥。"
    
    # 构建系统提示
    system_prompt = CHILD_PROFILE
    
    # 根据消息类型调整提示
    if context_type == "growth":
        system_prompt += "\n\n当前用户发送的是身高体重数据，已自动记录。请确认记录并简要评估。"
    elif context_type == "medical":
        system_prompt += "\n\n当前用户发送的是就医信息，已自动记录。请确认记录并追问体检/就诊的关键细节。"
    elif context_type == "vaccine":
        system_prompt += "\n\n当前用户发送的是疫苗接种信息，已自动记录。请确认并提醒后续疫苗节点。"
    elif context_type == "checkup_query":
        # 计算下次体检时间
        data = load_data()
        next_checkups = [
            ("30月龄体检", "2026-09-12"),
            ("36月龄体检（3岁）", "2027-03-13"),
        ]
        info = "；".join([f"{n}：{d}" for n, d in next_checkups])
        return f"阿鲤接下来的体检安排：\n{info}\n\n建议提前1-2周预约儿保门诊。"
    elif context_type == "vaccine_query":
        return ("阿鲤接下来需要接种的疫苗：\n"
                "3岁（2027-03）：A+C群流脑多糖疫苗第1剂\n"
                "6岁：A+C群流脑多糖疫苗第2剂、白破疫苗加强\n\n"
                "建议接种前确认孩子身体状况良好，接种后观察30分钟。")
    elif context_type == "help":
        return ("阿鲤成长助手，你可以发：\n"
                "1. 身高体重：如\"87cm 12.5kg\" → 自动记录+评估\n"
                "2. 就医信息：如\"今天去儿保了\" → 自动记录\n"
                "3. 疫苗信息：如\"打了流感疫苗\" → 自动记录\n"
                "4. \"报告\" → 查看成长曲线报告\n"
                "5. \"体检\" → 查看下次体检时间\n"
                "6. 任何喂养/育儿问题 → 在线咨询")
    elif context_type == "data_query":
        data = load_data()
        records = data.get("growth_records", [])
        if records:
            latest = records[-1]
            total = len(records)
            return (f"阿鲤共有{total}条生长记录\n"
                    f"最新：{latest['date']} 身高{latest['height_cm']}cm 体重{latest['weight_kg']}kg\n"
                    f"就医记录{len(data.get('medical_records',[]))}条\n"
                    f"疫苗记录{len(data.get('vaccine_records',[]))}条\n"
                    f"发送\"报告\"查看完整成长曲线。")
        return "暂无记录数据。"
    
    try:
        response = ai_client.chat.completions.create(
            model="glm-4-flash",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message}
            ],
            temperature=0.7,
            max_tokens=300
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        return f"AI回复出错，请稍后重试。"


# ============ 微信消息处理 ============
def handle_text_message(content, from_user):
    """处理文本消息，返回回复内容"""
    msg_type = detect_message_type(content)
    
    if msg_type == "growth":
        parsed = parse_growth_message(content)
        if parsed:
            rec_date = parsed.get("date")
            record_growth(parsed["height_cm"], parsed["weight_kg"], rec_date)
            # 让AI评估并生成回复
            date_txt = f"日期{rec_date}，" if rec_date else "今天"
            reply = chat_with_ai(
                f"家长记录了阿鲤{date_txt}身高{parsed['height_cm']}cm，体重{parsed['weight_kg']}kg。请确认记录并简要评估。",
                "growth"
            )
            return reply
    
    elif msg_type == "medical":
        record_medical(content)
        reply = chat_with_ai(f"家长发送了就医信息：{content}", "medical")
        return reply
    
    elif msg_type == "vaccine":
        record_vaccine(content)
        reply = chat_with_ai(f"家长发送了疫苗信息：{content}", "vaccine")
        return reply
    
    elif msg_type == "report":
        return "成长报告已生成，请访问：\nhttps://你的应用地址.onrender.com/report\n\n（首次打开可能需要10秒唤醒服务）"
    
    elif msg_type == "help":
        return chat_with_ai(content, "help")
    
    elif msg_type == "data_query":
        return chat_with_ai(content, "data_query")
    
    elif msg_type == "checkup_query":
        return chat_with_ai(content, "checkup_query")
    
    elif msg_type == "vaccine_query":
        return chat_with_ai(content, "vaccine_query")
    
    else:
        # 通用对话
        return chat_with_ai(content, "chat")


# ============ 微信接口验证 ============
def verify_signature(signature, timestamp, nonce):
    """验证微信消息签名"""
    token = WX_TOKEN
    tmp_list = sorted([token, timestamp, nonce])
    tmp_str = "".join(tmp_list).encode("utf-8")
    sign = hashlib.sha1(tmp_str).hexdigest()
    return sign == signature


# ============ Flask 路由 ============
@app.route("/wechat", methods=["GET"])
def wechat_verify():
    """微信公众号服务器验证"""
    signature = request.args.get("signature", "")
    timestamp = request.args.get("timestamp", "")
    nonce = request.args.get("nonce", "")
    echostr = request.args.get("echostr", "")
    
    if verify_signature(signature, timestamp, nonce):
        return echostr
    else:
        abort(403)


@app.route("/wechat", methods=["POST"])
def wechat_message():
    """处理微信消息"""
    body = request.data.decode("utf-8")
    root = ET.fromstring(body)
    
    msg_type = root.find("MsgType").text
    from_user = root.find("FromUserName").text
    to_user = root.find("ToUserName").text
    
    if msg_type == "text":
        content = root.find("Content").text
        reply_text = handle_text_message(content, from_user)
        
        # 构建XML回复
        reply_xml = f"""<xml>
<ToUserName><![CDATA[{from_user}]]></ToUserName>
<FromUserName><![CDATA[{to_user}]]></FromUserName>
<CreateTime>{int(time.time())}</CreateTime>
<MsgType><![CDATA[text]]></MsgType>
<Content><![CDATA[{reply_text}]]></Content>
</xml>"""
        return reply_xml, {"Content-Type": "application/xml"}
    
    elif msg_type == "event":
        event = root.find("Event").text
        if event == "subscribe":
            welcome = ("欢迎关注阿鲤成长记录！\n\n"
                       "我是阿鲤的成长助手，你可以发：\n"
                       "1. 身高体重：\"87cm 12.5kg\" → 自动记录+评估\n"
                       "2. 就医信息：\"今天去儿保了\" → 自动记录\n"
                       "3. 疫苗信息：\"打了流感疫苗\" → 自动记录\n"
                       "4. \"报告\" → 查看成长曲线\n"
                       "5. \"体检\" → 查看下次体检时间\n"
                       "6. 任何喂养问题 → 在线咨询\n\n"
                       "发送\"帮助\"查看完整功能。")
            reply_xml = f"""<xml>
<ToUserName><![CDATA[{from_user}]]></ToUserName>
<FromUserName><![CDATA[{to_user}]]></FromUserName>
<CreateTime>{int(time.time())}</CreateTime>
<MsgType><![CDATA[text]]></MsgType>
<Content><![CDATA[{welcome}]]></Content>
</xml>"""
            return reply_xml, {"Content-Type": "application/xml"}
    
    return "success", {"Content-Type": "application/xml"}


# ============ HTML报告页面 ============
REPORT_HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>阿鲤的成长记录</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
  *{margin:0;padding:0;box-sizing:border-box;}
  body{font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;background:#F9F9F9;color:#333;padding-bottom:40px;}
  .header{background:linear-gradient(135deg,#2B4A9A 0%,#3A5BB5 100%);color:white;padding:30px 20px;text-align:center;}
  .header h1{font-size:28px;margin-bottom:8px;}
  .header .info{font-size:14px;opacity:0.9;}
  .container{max-width:1000px;margin:20px auto;padding:0 16px;}
  .card{background:white;border-radius:16px;padding:24px;margin-bottom:20px;box-shadow:0 2px 12px rgba(43,74,154,0.06);}
  .card h2{font-size:20px;margin-bottom:16px;color:#2B4A9A;border-left:4px solid #9990ED;padding-left:12px;}
  .stats-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;}
  .stat-box{background:#F9F9F9;border:1px solid #f0f0f0;border-radius:12px;padding:16px 12px;text-align:center;position:relative;overflow:hidden;}
  .stat-box .label{font-size:12px;color:#888;margin-bottom:6px;}
  .stat-box .value{font-size:28px;font-weight:bold;color:#2B4A9A;}
  .stat-box .unit{font-size:13px;color:#aaa;margin-left:2px;}
  .stat-box .extra{font-size:11px;margin-top:4px;font-weight:500;}
  .stat-box.highlight{background:linear-gradient(135deg,#2B4A9A 0%,#3A5BB5 100%);}
  .stat-box.highlight .label{color:rgba(255,255,255,0.8);}
  .stat-box.highlight .value,.stat-box.highlight .unit{color:#fff;}
  .stat-box.highlight .extra{color:#FAD465;}
  .stat-box.full-width{grid-column:1/-1;display:flex;align-items:center;justify-content:space-between;padding:18px 24px;}
  .stat-box.full-width .left{flex:1;}
  .stat-box.full-width .right{text-align:right;}
  .stat-box.full-width .value{font-size:32px;}
  .stat-box.coral .extra{color:#F86C5B;}
  .stat-box.mint .extra{color:#66D9D0;}
  .stat-box.yellow .extra{color:#FAD465;}
  .stat-box.purple .extra{color:#9990ED;}
  .percentile-bar{margin-top:8px;height:6px;border-radius:3px;background:linear-gradient(90deg,#F86C5B 0%,#F86C5B 15%,#66D9D0 15%,#66D9D0 85%,#F86C5B 85%);position:relative;}
  .percentile-marker{position:absolute;top:-3px;width:3px;height:12px;background:#2B4A9A;border-radius:2px;transform:translateX(-50%);}
  .item-card{display:flex;align-items:center;gap:10px;padding:12px 16px;background:#F9F9FF;border-radius:10px;margin-top:8px;font-size:14px;border:1px solid #f0f0fa;}
  .item-card .icon{font-size:20px;flex-shrink:0;}
  .item-card .info{flex:1;}
  .item-card .info .name{font-weight:600;color:#333;}
  .item-card .info .date{font-size:12px;color:#888;margin-top:2px;}
  .item-card .countdown{font-size:13px;font-weight:600;color:#9990ED;white-space:nowrap;}
  .item-card.diet{border-left:3px solid #FAD465;}
  .item-card.diet .name{color:#B8860B;}
  .item-card.sleep{border-left:3px solid #9990ED;}
  .item-card.sleep .name{color:#7B68EE;}
  .item-card.exercise{border-left:3px solid #66D9D0;}
  .item-card.exercise .name{color:#26A69A;}
  .item-card.skill{border-left:3px solid #F86C5B;}
  .item-card.skill .name{color:#E53935;}
  .item-card.life{border-left:3px solid #93C1FE;}
  .item-card.life .name{color:#5C9BFE;}
  .item-card.season{border-left:3px solid #2B4A9A;}
  .item-card.season .name{color:#2B4A9A;}
  .collapsible-header{cursor:pointer;display:flex;align-items:center;justify-content:space-between;user-select:none;}
  .collapsible-header .arrow{transition:transform 0.3s;font-size:14px;color:#9990ED;}
  .collapsible-header.collapsed .arrow{transform:rotate(-90deg);}
  .collapsible-content{overflow:hidden;transition:max-height 0.3s ease-out;max-height:4000px;}
  .collapsible-content.collapsed{max-height:0;}
  .chart-controls{display:flex;flex-wrap:wrap;gap:10px;margin-bottom:12px;align-items:center;}
  .chart-controls .ctrl-group{display:flex;align-items:center;gap:6px;}
  .chart-controls label{font-size:13px;color:#666;}
  .chart-controls select{padding:4px 10px;border:1px solid #ddd;border-radius:8px;font-size:13px;background:#fff;cursor:pointer;outline:none;color:#2B4A9A;}
  .chart-controls select:focus{border-color:#9990ED;}
  .chart-container{position:relative;height:380px;margin:10px 0;}
  table{width:100%;border-collapse:collapse;margin-top:12px;}
  th,td{padding:10px 14px;text-align:left;border-bottom:1px solid #eee;font-size:14px;}
  th{background:#F9F9F9;font-weight:600;color:#555;}
  .badge{display:inline-block;padding:2px 10px;border-radius:12px;font-size:12px;}
  .badge-vaccine{background:#E0F7FA;color:#00838F;}
  .badge-birth{background:#FFF8E1;color:#F57F17;}
  .badge-checkup{background:#EDE7F6;color:#4527A0;}
  .section-empty{text-align:center;color:#999;padding:20px;}
  .med-item{background:#fff;border-radius:10px;margin-top:8px;overflow:hidden;}
  .med-main{display:flex;align-items:center;gap:10px;padding:10px 14px;background:#F9F9FF;cursor:pointer;flex-wrap:wrap;border:1px solid #f0f0fa;border-radius:10px;}
  .med-main .med-date{font-weight:600;color:#2B4A9A;white-space:nowrap;font-size:13px;}
  .med-main .med-hospital{font-size:13px;color:#333;flex:1;min-width:120px;}
  .med-main .med-doctor{font-size:12px;color:#666;background:#eef1fb;border-radius:6px;padding:2px 8px;white-space:nowrap;}
  .med-main .med-dept{font-size:12px;color:#888;white-space:nowrap;}
  .med-main .arrow{font-size:12px;color:#9990ED;transition:transform .2s;}
  .med-detail{padding:12px 16px;background:#fff;border:1px solid #f0f0fa;border-radius:0 0 10px 10px;}
  .med-detail-row{display:flex;gap:8px;margin-top:6px;font-size:13px;line-height:1.6;}
  .med-detail-row .med-label{flex-shrink:0;color:#9990ED;font-weight:600;width:70px;}
  .med-detail-row span:last-child{color:#555;}
  @media(max-width:600px){.stats-grid{grid-template-columns:1fr 1fr;}.stat-box.full-width{grid-column:1/-1;}.card{padding:16px;}.chart-container{height:300px;}}
</style>
</head>
<body>
<div class="header">
  <h1>阿鲤的成长记录</h1>
  <div class="info">出生：{{ birth_date }} | 男 | 出生47cm/2650g</div>
</div>
<div class="container">
  <div class="card">
    <h2>成长概览</h2>
    <div class="stats-grid">
      <div class="stat-box highlight">
        <div class="label">当前月龄</div>
        <div class="value">{{ age_months }}<span class="unit">月</span></div>
        <div class="extra">{{ age_years }}岁{{ age_months_remainder }}月</div>
      </div>
      <div class="stat-box mint">
        <div class="label">身高增速</div>
        <div class="value">{{ height_growth_rate }}<span class="unit">cm/月</span></div>
        <div class="extra">近{{ growth_rate_months }}个月</div>
      </div>
      <div class="stat-box coral">
        <div class="label">体重增速</div>
        <div class="value">{{ weight_growth_rate }}<span class="unit">kg/月</span></div>
        <div class="extra">近{{ growth_rate_months }}个月</div>
      </div>
      <div class="stat-box full-width coral">
        <div class="left">
          <div class="label">身高（{{ latest_record_date }}）</div>
          <div class="extra" style="margin-top:4px;">{{ height_percentile_desc }}</div>
          <div class="percentile-bar"><div class="percentile-marker" style="left:{{ height_percentile_pos }}%"></div></div>
        </div>
        <div class="right">
          <div class="value">{{ latest_height }}<span class="unit">cm</span></div>
        </div>
      </div>
      <div class="stat-box full-width mint">
        <div class="left">
          <div class="label">体重（{{ latest_record_date }}）</div>
          <div class="extra" style="margin-top:4px;">{{ weight_percentile_desc }}</div>
          <div class="percentile-bar"><div class="percentile-marker" style="left:{{ weight_percentile_pos }}%"></div></div>
        </div>
        <div class="right">
          <div class="value">{{ latest_weight }}<span class="unit">kg</span></div>
        </div>
      </div>
    </div>
    {% if next_items %}
    <div style="margin-top:16px;">
      <div class="collapsible-header" onclick="toggleSection(this)">
        <div style="font-size:14px;color:#666;">近期节点（1个月内）</div>
        <span class="arrow">▼</span>
      </div>
      <div class="collapsible-content">
      {% for item in next_items %}
      <div class="item-card">
        <div class="icon">{{ item.icon }}</div>
        <div class="info">
          <div class="name">{{ item.name }}</div>
          <div class="date">{{ item.date }}</div>
        </div>
        <div class="countdown">{{ item.countdown }}</div>
      </div>
      {% endfor %}
      </div>
    </div>
    {% endif %}
    <div class="advice-section" style="margin-top:16px;">
      <div class="collapsible-header" onclick="toggleSection(this)">
        <h2 style="margin:0;font-size:18px;color:#2B4A9A;border-left:4px solid #FAD465;padding-left:12px;">喂养建议</h2>
        <span class="arrow">▼</span>
      </div>
      <div class="collapsible-content">
        {% for a in advice_items %}
        <div class="item-card {{ a.type }}">
          <div class="icon">{{ a.icon }}</div>
          <div class="info">
            <div class="name">{{ a.title }}</div>
            <div class="date">{{ a.text }}</div>
          </div>
        </div>
        {% endfor %}
      </div>
    </div>
  </div>

  <div class="card">
    <div class="collapsible-header" onclick="toggleSection(this)">
      <h2 style="border-left:4px solid #66D9D0;padding-left:12px;">身高生长曲线</h2>
      <span class="arrow">▼</span>
    </div>
    <div class="collapsible-content">
    <div class="chart-controls">
      <div class="ctrl-group"><label>区间</label><select id="heightRange" onchange="updateChart('height')">
        <option value="full" selected>全览（0-{{ chart_max_month }}月）</option>
        <option value="recent">近期（{{ recent_min_month }}-{{ recent_max_month }}月）</option>
        <option value="0-12">1岁（0-12月）</option>
        <option value="12-24">12-24月</option>
        <option value="24-36">24-36月</option>
        <option value="36-48">36-48月</option>
        <option value="48-60">48-60月</option>
      </select></div>
    </div>
    <div class="chart-container"><canvas id="heightChart"></canvas></div>
    </div>
  </div>

  <div class="card">
    <div class="collapsible-header" onclick="toggleSection(this)">
      <h2 style="border-left:4px solid #66D9F5;padding-left:12px;">体重生长曲线</h2>
      <span class="arrow">▼</span>
    </div>
    <div class="collapsible-content">
    <div class="chart-controls">
      <div class="ctrl-group"><label>区间</label><select id="weightRange" onchange="updateChart('weight')">
        <option value="full" selected>全览（0-{{ chart_max_month }}月）</option>
        <option value="recent">近期（{{ recent_min_month }}-{{ recent_max_month }}月）</option>
        <option value="0-12">1岁（0-12月）</option>
        <option value="12-24">12-24月</option>
        <option value="24-36">24-36月</option>
        <option value="36-48">36-48月</option>
        <option value="48-60">48-60月</option>
      </select></div>
    </div>
    <div class="chart-container"><canvas id="weightChart"></canvas></div>
    </div>
  </div>

  <div class="card">
    <div class="collapsible-header collapsed" onclick="toggleSection(this)">
      <h2 style="border-left:4px solid #66D9D9;padding-left:12px;">生长记录</h2>
      <span class="arrow">▼</span>
    </div>
    <div class="collapsible-content collapsed">
      <table><thead><tr><th>日期</th><th>月龄</th><th>身高</th><th>体重</th></tr></thead><tbody>
      {% for r in growth_records %}
      <tr><td>{{ r.date }}</td><td>{{ r.age_months }}</td><td>{{ r.height_cm }}cm</td><td>{{ r.weight_kg }}kg</td></tr>
      {% endfor %}
      </tbody></table>
    </div>
  </div>
</div>
<script>
const allWhoHeight={{ who_height|safe }};
const allWhoWeight={{ who_weight|safe }};
const actualHeights={{ actual_heights|safe }};
const actualWeights={{ actual_weights|safe }};
const autoMin={{ chart_min_month }};
const autoMax={{ chart_max_month }};
const recentMin={{ recent_min_month }};
const recentMax={{ recent_max_month }};
let heightChart=null,weightChart=null;
let tooltipTimer=null;

function toggleSection(header){
  header.classList.toggle('collapsed');
  header.nextElementSibling.classList.toggle('collapsed');
}
function toggleMed(main){
  const detail=main.nextElementSibling;
  const arrow=main.querySelector('.arrow');
  if(detail.style.display==='none'){detail.style.display='block';arrow.textContent='▼';}
  else{detail.style.display='none';arrow.textContent='▶';}
}

function buildChart(ctxId,whoData,actual,range,yLabel){
  let minM,maxM;
  if(range==='full'){minM=autoMin;maxM=autoMax;}
  else if(range==='recent'){minM=recentMin;maxM=recentMax;}
  else{const parts=range.split('-');minM=Number(parts[0]);maxM=Number(parts[1]);}
  let months=whoData.map(d=>d[0]).filter(m=>m>=minM&&m<=maxM).sort((a,b)=>a-b);
  const datasets=[];
  const pConfig={
    1:{label:'P3',color:'#F86C5B',dash:[4,4]},
    2:{label:'P15',color:'#FAD465',dash:[3,3]},
    3:{label:'P50',color:'#66D9D0',dash:[6,3]},
    4:{label:'P85',color:'#FAD465',dash:[3,3]},
    5:{label:'P97',color:'#F86C5B',dash:[4,4]}
  };
  [1,2,3,4,5].forEach(col=>{
    datasets.push({
      label:pConfig[col].label,
      data:months.map(m=>{const d=whoData.find(x=>x[0]===m);return d?d[col]:null}),
      borderColor:pConfig[col].color,
      borderDash:pConfig[col].dash,
      borderWidth:1.5,fill:false,pointRadius:0,tension:0.4,
      order:2
    });
  });
  // P3-P97 fill band
  datasets.push({
    label:'正常范围',
    data:months.map(m=>{const d=whoData.find(x=>x[0]===m);return d?d[5]:null}),
    borderColor:'transparent',
    backgroundColor:'rgba(102,217,208,0.07)',
    fill:'+1',
    pointRadius:0,
    order:3
  });
  datasets.push({
    label:'_p3fill',
    data:months.map(m=>{const d=whoData.find(x=>x[0]===m);return d?d[1]:null}),
    borderColor:'transparent',
    backgroundColor:'transparent',
    fill:false,pointRadius:0,
    order:3
  });
  // Actual data on top
  const filteredActual=actual.filter(d=>d[0]>=minM&&d[0]<=maxM);
  datasets.push({
    label:'实际测量',
    data:filteredActual.map(d=>({x:d[0],y:d[1]})),
    borderColor:'#2B4A9A',
    backgroundColor:'#2B4A9A',
    borderWidth:3,fill:false,
    pointRadius:4,pointHoverRadius:7,
    pointBackgroundColor:'#2B4A9A',
    pointBorderColor:'#fff',pointBorderWidth:1.5,
    tension:0.3,
    order:1,
    parsing:{xAxisKey:'x',yAxisKey:'y'}
  });
  const ctx=document.getElementById(ctxId).getContext('2d');
  return new Chart(ctx,{
    type:'line',
    data:{labels:months,datasets},
    options:{
      responsive:true,maintainAspectRatio:false,
      interaction:{mode:'nearest',intersect:false},
      plugins:{
        legend:{position:'bottom',labels:{usePointStyle:true,filter:item=>!item.text.startsWith('_')&&item.text!=='正常范围'||item.text==='正常范围'?true:false}},
        tooltip:{
          callbacks:{label:function(ctx){const v=ctx.parsed.y;return ctx.dataset.label+': '+v+(yLabel==='cm'?'cm':'kg');}},
          external:function(context){
            const tip=context.tooltip;
            if(tip.opacity===0){
              if(tooltipTimer){clearTimeout(tooltipTimer);tooltipTimer=null;}
              return;
            }
            if(!tooltipTimer){
              tooltipTimer=setTimeout(function(){
                tip.opacity=0;
                context.chart.update('none');
                tooltipTimer=null;
              },3000);
            }
          }
        }
      },
      scales:{
        x:{type:'linear',title:{display:true,text:'月龄'},min:minM,max:maxM,
           ticks:{stepSize:maxM-minM>24?6:3}},
        y:{title:{display:true,text:yLabel},beginAtZero:false}
      }
    }
  });
}

function updateChart(type){
  if(type==='height'){
    if(heightChart)heightChart.destroy();
    const range=document.getElementById('heightRange').value;
    heightChart=buildChart('heightChart',allWhoHeight,actualHeights,range,'cm');
  }else{
    if(weightChart)weightChart.destroy();
    const range=document.getElementById('weightRange').value;
    weightChart=buildChart('weightChart',allWhoWeight,actualWeights,range,'kg');
  }
}
updateChart('height');
updateChart('weight');
</script>
</body></html>
"""


def _calc_percentile(value, who_row):
    """根据WHO数据行计算百分位描述和位置（0-100）"""
    if not who_row or len(who_row) < 6:
        return "暂无参考", 50
    p3, p15, p50, p85, p97 = who_row[1], who_row[2], who_row[3], who_row[4], who_row[5]
    if value < p3:
        return "低于P3，需关注", 5
    elif value < p15:
        pct = 5 + (value - p3) / (p15 - p3) * 10
        return "P3-P15区间", max(5, min(15, pct))
    elif value < p50:
        pct = 15 + (value - p15) / (p50 - p15) * 35
        return "P15-P50区间", max(15, min(50, pct))
    elif value < p85:
        pct = 50 + (value - p50) / (p85 - p50) * 35
        return "P50-P85区间", max(50, min(85, pct))
    elif value < p97:
        pct = 85 + (value - p85) / (p97 - p85) * 12
        return "P85-P97区间", max(85, min(97, pct))
    else:
        return "高于P97，需关注", 99


def _find_who_row(who_data, age_months):
    """找到最接近的WHO月龄行"""
    if not who_data:
        return None
    best = None
    best_diff = 999
    for row in who_data:
        diff = abs(row[0] - age_months)
        if diff < best_diff:
            best_diff = diff
            best = row
    return best


def _build_advice(age_months, season):
    """根据月龄和季节生成喂养建议（按周龄细分）"""
    advice = []

    # 按月龄分段，覆盖 0-60 月，重点细化 24-36 月（阿鲤当前 29 月龄）
    if age_months < 6:
        advice.append({"type":"diet","icon":"🍽","title":"饮食","text":"纯母乳喂养，按需哺乳，每日8-12次；补充维生素D400IU/日。6月龄前一般不添加辅食，以奶为主。"})
        advice.append({"type":"exercise","icon":"🏃","title":"运动","text":"每日俯卧练习（Tummy Time）2-3次，每次几分钟；练习抓握、追视、听声辨位。户外活动逐步增加至1小时。"})
        advice.append({"type":"skill","icon":"📚","title":"技巧学习","text":"多与宝宝面对面说话、微笑，用黑白卡/彩色卡刺激视觉；模仿发声，为语言发展打基础。"})
        advice.append({"type":"life","icon":"🌟","title":"生活技能","text":"建立吃-玩-睡的规律作息；睡前固定流程（洗澡、抚触、哼歌）帮助养成睡眠习惯。"})
    elif age_months < 12:
        advice.append({"type":"diet","icon":"🍼","title":"饮食","text":"奶量600-800ml/日，辅食从含铁米糊开始，逐步引入蛋黄、肉泥、肝泥、蔬菜泥、水果泥。食物由稀到稠、由细到粗。新食材每次只加一种，观察2-3天防过敏。"})
        advice.append({"type":"exercise","icon":"🏃","title":"运动","text":"每日户外活动1小时；练习独坐、匍匐、爬行，鼓励扶站。精细动作可玩撕纸、抓握小件。"})
        advice.append({"type":"skill","icon":"📚","title":"技巧学习","text":"多与宝宝对话、指物命名，鼓励发baba/mama音；读布书、触摸书，帮助认知常见物品。"})
        advice.append({"type":"life","icon":"🌟","title":"生活技能","text":"培养定时喂奶、辅食与睡眠的节律；鼓励宝宝自己抓握食物，为自主进食打基础。"})
    elif age_months < 18:
        advice.append({"type":"diet","icon":"🍽","title":"饮食","text":"每日奶量600ml左右，三餐两点心；食物切成小丁，鼓励尝试块状。增加含铁红肉、动物肝脏、鱼类补充蛋白质和铁。"})
        advice.append({"type":"exercise","icon":"🏃","title":"运动","text":"每日户外活动1-1.5小时；练习独立行走、扶着上台阶、蹲下站起。精细动作可练习搭积木、翻书。"})
        advice.append({"type":"skill","icon":"📚","title":"技巧学习","text":"多听儿歌童谣，鼓励模仿大人说话、说简单词；认识身体部位，练习把物品放进容器。"})
        advice.append({"type":"life","icon":"🌟","title":"生活技能","text":"学着自己用勺子吃饭（允许洒漏）；协助收拾玩具；开始如厕训练的意识培养（换尿布时预告）。"})
    elif age_months < 24:
        advice.append({"type":"diet","icon":"🍽","title":"饮食","text":"保证每日奶量400-500ml，三餐两点心，食物切碎便于咀嚼。注意补充含铁食物如红肉、蛋黄；逐渐过渡到家庭饮食。"})
        advice.append({"type":"exercise","icon":"🏃","title":"运动","text":"每日户外活动至少1小时，鼓励跑跳、攀爬等大运动，同时练习串珠、翻书等精细动作。"})
        advice.append({"type":"skill","icon":"📚","title":"技巧学习","text":"多读绘本、听儿歌，鼓励模仿说话和简单短语。认识常见物品名称，培养因果关系理解。"})
        advice.append({"type":"life","icon":"🌟","title":"生活技能","text":"学习自己用勺子吃饭、协助收玩具。开始如厕训练，培养洗手等卫生习惯。"})
    elif age_months < 30:
        # 30月龄前：强化自主进食、精细动作
        advice.append({"type":"diet","icon":"🍽","title":"饮食","text":"每日奶量350-400ml，三餐规律；食物种类多样化，蛋白质（鱼、肉、蛋）和新鲜蔬果每日都要有。控制零食与含糖饮料。建议开始学习使用筷子夹取大块食物，提升手口协调。"})
        advice.append({"type":"exercise","icon":"🏃","title":"运动","text":"每日户外活动1-2小时，鼓励跑、跳、踢球、骑滑板车。本周重点：上下楼梯（扶着扶手）、单脚站立3秒以上，练习平衡。"})
        advice.append({"type":"skill","icon":"📚","title":"技巧学习","text":"进行亲子阅读并鼓励复述简单故事。本周建议开始学习使用剪刀：从儿童安全剪刀剪纸、剪面开始，目标3岁前能熟练使用。同时练习颜色、形状分类和数数1-10。"})
        advice.append({"type":"life","icon":"🌟","title":"生活技能","text":"开始学习自己穿鞋、穿袜：先从容易穿脱的大开口鞋袜练起，学会区分左右；练习自己解扣子、拉拉链。鼓励帮忙做简单家务如收碗筷。"})
    elif age_months < 36:
        advice.append({"type":"diet","icon":"🍽","title":"饮食","text":"每日奶量维持350-400ml，三餐规律；食物种类多样化。注意蛋白质（鱼、肉、蛋）和新鲜蔬果摄入，控制零食和含糖饮料。可开始让宝宝参与简单的餐前准备，培养对食物的兴趣。"})
        advice.append({"type":"exercise","icon":"🏃","title":"运动","text":"每日户外活动1-2小时，鼓励跑、跳、踢球、骑滑板车等。练习上下楼梯、单脚站立等平衡运动。2岁半后可尝试儿童小三轮车。"})
        advice.append({"type":"skill","icon":"📚","title":"技巧学习","text":"多进行亲子阅读，鼓励复述简单故事。练习颜色、形状分类，数数1-10。涂鸦画画锻炼手眼协调。若尚未熟练，抓紧练习使用安全剪刀剪纸，目标3岁前熟练。"})
        advice.append({"type":"life","icon":"🌟","title":"生活技能","text":"练习自己穿脱简单衣物、扣扣子，自己穿鞋袜。学习自己上厕所。鼓励帮忙做简单家务如收碗筷、浇花。培养固定作息时间。"})
    else:
        advice.append({"type":"diet","icon":"🍽","title":"饮食","text":"三餐两点心制，保证均衡营养。培养良好就餐习惯，不挑食不偏食。适当增加粗粮和膳食纤维。"})
        advice.append({"type":"exercise","icon":"🏃","title":"运动","text":"每日户外活动2小时以上，鼓励跑跳攀爬、骑三轮车等。可开始尝试简单的游泳、球类运动。"})
        advice.append({"type":"skill","icon":"📚","title":"技巧学习","text":"加强语言表达训练，鼓励讲完整句子和简单故事。练习画圆、折纸、使用安全剪刀。认识数字和简单汉字。"})
        advice.append({"type":"life","icon":"🌟","title":"生活技能","text":"学习独立穿衣、系扣子、刷牙。培养整理玩具和床铺的习惯。鼓励自己吃饭不撒漏。"})
    # 季节建议
    advice.append({"type":"sleep","icon":"😴","title":"作息","text":f"保持早睡早起（20:00-21:00入睡），午睡1-1.5小时，总睡眠11-13小时。{season}注意调整室内温湿度，避免过热或过冷影响睡眠质量。"})
    return advice


@app.route("/report")
def report():
    """生成HTML成长报告页面"""
    from datetime import datetime, date as dt_date

    data = load_data()
    birth_date = data["child"]["birth_date"]
    birth = datetime.strptime(birth_date, "%Y-%m-%d")
    now = datetime.now()
    age_months = round((now - birth).days / 30.44, 1)
    age_years = int(age_months // 12)
    age_months_remainder = int(age_months % 12)

    growth = data.get("growth_records", [])

    # 计算月龄
    for r in growth:
        record_date = datetime.strptime(r["date"], "%Y-%m-%d")
        r["age_months"] = round((record_date - birth).days / 30.44, 1)

    # 生长记录逆序
    growth_display = sorted(growth, key=lambda x: x["date"], reverse=True)

    # 就医记录逆序（归一化字段，兼容旧格式）
    raw_medical = data.get("medical_records", [])
    for m in raw_medical:
        m.setdefault("hospital", "")
        m.setdefault("department", "")
        m.setdefault("doctor", "")
        m.setdefault("chief_complaint", m.get("description", ""))
        m.setdefault("exam", "")
        m.setdefault("diagnosis", "")
        m.setdefault("advice", "")
        m.setdefault("medication", "")
    medical_display = sorted(raw_medical, key=lambda x: x["date"], reverse=True)

    # 疫苗记录归一化字段（兼容旧格式）
    raw_vaccine = data.get("vaccine_records", [])
    for v in raw_vaccine:
        v.setdefault("vaccine", v.get("vaccine_name", ""))
        v.setdefault("dose", "")
        v.setdefault("status", "completed")
    vaccine_display = sorted(raw_vaccine, key=lambda x: x["date"], reverse=True)

    # WHO完整数据（0-60月，5个百分位列）
    who_height = [
        [0,46.1,48.0,49.9,51.8,53.7],[1,51.1,53.1,55.1,57.1,59.1],[2,54.7,56.7,58.7,60.7,62.7],
        [3,57.6,59.6,61.7,63.7,65.7],[4,59.9,62.0,64.0,66.0,68.1],[5,61.8,63.9,65.9,67.9,69.9],
        [6,63.3,65.4,67.6,69.7,71.8],[7,64.8,66.9,69.0,71.1,73.3],[8,66.2,68.3,70.5,72.6,74.8],
        [9,67.5,69.7,71.9,74.2,76.4],[10,68.7,70.9,73.1,75.4,77.7],[11,69.9,72.1,74.4,76.6,78.9],
        [12,71.0,73.3,75.7,78.1,80.5],[15,74.1,76.4,78.8,81.2,83.6],[18,76.9,79.2,81.7,84.2,86.7],
        [21,79.4,81.7,84.1,86.5,89.0],[24,81.7,84.1,86.8,89.5,92.2],[27,83.7,86.1,88.8,91.5,94.3],
        [30,85.6,88.2,90.9,93.7,96.5],[33,87.2,89.9,92.6,95.4,98.2],[36,88.7,91.4,94.2,97.0,99.8],
        [42,91.6,94.4,97.2,100.1,103.0],[48,94.1,97.0,100.0,103.0,106.0],[54,96.5,99.4,102.4,105.5,108.6],
        [60,98.7,101.8,105.0,108.2,111.3]
    ]
    who_weight = [
        [0,2.5,2.9,3.3,3.9,4.3],[1,3.4,3.9,4.5,5.1,5.7],[2,4.3,4.9,5.6,6.3,7.0],
        [3,5.0,5.7,6.4,7.2,7.9],[4,5.6,6.2,7.0,7.8,8.6],[5,6.0,6.7,7.5,8.3,9.1],
        [6,6.4,7.1,7.9,8.8,9.6],[7,6.7,7.5,8.3,9.2,10.1],[8,7.0,7.8,8.6,9.5,10.5],
        [9,7.1,7.9,8.9,9.8,10.8],[10,7.4,8.2,9.2,10.1,11.2],[11,7.6,8.4,9.4,10.4,11.5],
        [12,7.7,8.5,9.6,10.6,11.7],[15,8.3,9.1,10.3,11.4,12.5],[18,8.8,9.6,10.9,12.0,13.3],
        [21,9.2,10.1,11.4,12.7,14.1],[24,9.7,10.5,12.2,13.5,14.8],[27,10.0,10.9,12.6,14.0,15.3],
        [30,10.2,11.1,12.9,14.3,15.7],[33,10.5,11.5,13.3,14.8,16.3],[36,10.8,11.8,13.7,15.3,16.9],
        [42,11.3,12.3,14.3,16.1,17.9],[48,11.7,12.7,15.1,17.0,18.8],[54,12.1,13.0,15.8,17.9,20.0],
        [60,12.4,13.3,16.5,18.8,20.7]
    ]

    # WHO数据按整数月线性插值，确保任意显示区间内曲线完整
    def _interp_who(rows, max_month=60):
        """将WHO稀疏月龄点插值到0-max_month每个整数月"""
        out = []
        months = [r[0] for r in rows]
        for m in range(0, max_month + 1):
            if m in months:
                out.append(list(rows[months.index(m)]))
            else:
                # 找到相邻上下界插值
                hi = next((i for i, x in enumerate(months) if x > m), None)
                lo = hi - 1 if hi is not None else len(months) - 1
                if hi is None:
                    out.append(list(rows[lo]))
                    continue
                r1, r2 = rows[lo], rows[hi]
                t = (m - r1[0]) / (r2[0] - r1[0])
                out.append([m] + [round(r1[i] + (r2[i] - r1[i]) * t, 1) for i in range(1, 6)])
        return out

    who_height = _interp_who(who_height)
    who_weight = _interp_who(who_weight)

    actual_heights = [[g["age_months"], g["height_cm"]] for g in growth]
    actual_weights = [[g["age_months"], g["weight_kg"]] for g in growth]

    latest_height = round(growth[-1]["height_cm"], 1) if growth else 0
    latest_weight = round(growth[-1]["weight_kg"], 1) if growth else 0
    latest_record_date = growth[-1]["date"] if growth else "N/A"
    latest_age_months = growth[-1]["age_months"] if growth else age_months

    # 百分位评估
    h_who_row = _find_who_row(who_height, latest_age_months)
    w_who_row = _find_who_row(who_weight, latest_age_months)
    h_pct_desc, h_pct_pos = _calc_percentile(latest_height, h_who_row)
    w_pct_desc, w_pct_pos = _calc_percentile(latest_weight, w_who_row)

    # 生长速率（最近N个月）
    growth_rate_months = 6
    if len(growth) >= 2:
        recent = growth[-2:]
        months_diff = (recent[1]["age_months"] - recent[0]["age_months"]) or 1
        height_growth_rate = round((recent[1]["height_cm"] - recent[0]["height_cm"]) / months_diff, 2)
        weight_growth_rate = round((recent[1]["weight_kg"] - recent[0]["weight_kg"]) / months_diff, 2)
        growth_rate_months = int(months_diff)
    else:
        height_growth_rate = 0
        weight_growth_rate = 0

    # 图表区间
    # 全览：0月到当前月龄+3月
    chart_min_month = 0
    chart_max_month = int(latest_age_months + 3)
    # 近期：往前12个月、往后2个月
    recent_min_month = int(max(0, latest_age_months - 12))
    recent_max_month = int(latest_age_months + 2)

    # 近期节点
    next_items = []
    today = dt_date.today()
    checkup_items = [
        ("30月龄体检", "2026-09-12", "🏥"),
        ("3岁体检", "2027-03-13", "🏥"),
    ]
    vaccine_items = [
        ("A+C群流脑多糖疫苗(第1剂)", "2027-03-13", "💉"),
    ]
    for name, dstr, icon in checkup_items + vaccine_items:
        try:
            d = dt_date.fromisoformat(dstr)
            days_left = (d - today).days
            if 0 < days_left <= 30:
                countdown = f"{days_left}天后"
                next_items.append({"name": name, "date": dstr, "icon": icon, "countdown": countdown})
        except:
            pass
    next_items = next_items[:4]

    # 季节判断
    month = now.month
    if month in [3,4,5]:
        season = "春季"
    elif month in [6,7,8]:
        season = "夏季"
    elif month in [9,10,11]:
        season = "秋季"
    else:
        season = "冬季"

    # 喂养建议
    advice_items = _build_advice(age_months, season)

    # 季节温度建议
    season_advice_map = {
        "春季": f"当前{season}，气温回暖但早晚温差大，注意春捂适时增减衣物。花粉季注意过敏，外出可戴口罩。多晒太阳补充维生素D。",
        "夏季": f"当前{season}，炎热需防中暑，避免正午暴晒。饮食清淡，多喝水防脱水。空调温度建议26-28度，注意蚊虫防护。",
        "秋季": f"当前{season}，干燥需多补充水分和润肺食物如梨、百合。早晚凉需适时添衣。是接种流感疫苗的好时机。",
        "冬季": f"当前{season}，寒冷需注意保暖但不宜过度包裹。室内开暖气需注意加湿通风。多喝温水，适当增加热量摄入。",
    }
    advice_items.append({"type":"season","icon":"🌡","title":f"季节建议（{season}）","text":season_advice_map[season]})

    return render_template_string(
        REPORT_HTML_TEMPLATE,
        birth_date=birth_date,
        age_months=age_months,
        age_years=age_years,
        age_months_remainder=age_months_remainder,
        latest_height=latest_height,
        latest_weight=latest_weight,
        latest_record_date=latest_record_date,
        growth_records=growth_display,
        medical_records=medical_display,
        vaccine_records=vaccine_display,
        height_percentile_desc=h_pct_desc,
        height_percentile_pos=round(h_pct_pos),
        weight_percentile_desc=w_pct_desc,
        weight_percentile_pos=round(w_pct_pos),
        height_growth_rate=height_growth_rate,
        weight_growth_rate=weight_growth_rate,
        growth_rate_months=growth_rate_months,
        next_items=next_items,
        advice_items=advice_items,
        chart_min_month=chart_min_month,
        chart_max_month=chart_max_month,
        recent_min_month=recent_min_month,
        recent_max_month=recent_max_month,
        who_height=json.dumps(who_height),
        who_weight=json.dumps(who_weight),
        actual_heights=json.dumps(actual_heights),
        actual_weights=json.dumps(actual_weights)
    )



# ============ H5聊天页面 ============
WEB_CHAT_HTML = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="theme-color" content="#667eea">
<title>阿鲤成长助手</title>
<style>
  *{margin:0;padding:0;box-sizing:border-box;-webkit-tap-highlight-color:transparent;}
  html,body{height:100%;overflow:hidden;}
  body{font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;background:#f0f2f5;display:flex;flex-direction:column;height:100vh;}
  .header{background:linear-gradient(135deg,#667eea 0%,#764ba2 100%);color:#fff;padding:14px 16px;display:flex;align-items:center;gap:12px;flex-shrink:0;z-index:10;box-shadow:0 2px 8px rgba(0,0,0,.15);}
  .header .avatar{width:42px;height:42px;border-radius:50%;background:#fff;display:flex;align-items:center;justify-content:center;font-size:24px;flex-shrink:0;box-shadow:0 2px 6px rgba(0,0,0,.15);overflow:hidden;}
  .header .avatar img{width:100%;height:100%;object-fit:cover;border-radius:50%;}
  .header .title-wrap{flex:1;min-width:0;}
  .header .title{font-size:16px;font-weight:600;}
  .header .subtitle{font-size:11px;opacity:.85;margin-top:2px;}
  .content{flex:1;overflow-y:auto;-webkit-overflow-scrolling:touch;padding:20px 16px;}
  .entry-grid{display:flex;flex-direction:column;gap:12px;}
  .entry-card{background:#fff;border-radius:16px;padding:18px 16px;cursor:pointer;text-decoration:none;box-shadow:0 1px 6px rgba(0,0,0,.06);transition:transform .15s,box-shadow .15s;display:flex;align-items:center;gap:14px;}
  .entry-card:active{transform:scale(.98);box-shadow:0 2px 12px rgba(102,126,234,.2);}
  .entry-card .e-icon{width:48px;height:48px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:24px;flex-shrink:0;}
  .entry-card .e-text{flex:1;min-width:0;}
  .entry-card .e-name{font-size:15px;font-weight:600;color:#333;}
  .entry-card .e-desc{font-size:12px;color:#999;line-height:1.5;margin-top:2px;}
  .entry-card .e-arrow{font-size:18px;color:#ccc;flex-shrink:0;}
  .entry-card.c1 .e-icon{background:#E8F0FE;}
  .entry-card.c2 .e-icon{background:#E6F4EA;}
  .entry-card.c3 .e-icon{background:#FEF7E0;}
  .entry-card.c4 .e-icon{background:#FCE8E6;}
</style>
</head>
<body>
<div class="header">
  <div class="avatar"><img src="data:image/png;base64,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" alt="阿鲤"></div>
  <div class="title-wrap">
    <div class="title">阿鲤成长助手</div>
    <div class="subtitle">记录成长 · 科学育儿</div>
  </div>
</div>
<div class="content">
  <div class="entry-grid">
    <a class="entry-card c1" href="/report" target="_blank">
      <div class="e-icon">📈</div>
      <div class="e-text"><div class="e-name">成长趋势</div><div class="e-desc">身高体重曲线与百分位评估</div></div>
      <div class="e-arrow">›</div>
    </a>
    <a class="entry-card c2" href="/web/med" target="_blank">
      <div class="e-icon">🏥</div>
      <div class="e-text"><div class="e-name">医疗情况</div><div class="e-desc">就医记录与疫苗记录</div></div>
      <div class="e-arrow">›</div>
    </a>
    <a class="entry-card c3" href="/web/add" target="_blank">
      <div class="e-icon">📝</div>
      <div class="e-text"><div class="e-name">新增记录</div><div class="e-desc">记录成长 / 医疗 / 疫苗</div></div>
      <div class="e-arrow">›</div>
    </a>
    <a class="entry-card c4" href="/web/chat" target="_blank">
      <div class="e-icon">💬</div>
      <div class="e-text"><div class="e-name">养育咨询</div><div class="e-desc">随时咨询育儿问题</div></div>
      <div class="e-arrow">›</div>
    </a>
  </div>
</div>
</body>
</html>
"""

@app.route("/web")
def web_chat():
    """H5聊天页面"""
    return render_template_string(WEB_CHAT_HTML)


@app.route("/web/api", methods=["POST"])
def web_api():
    """H5聊天API接口"""
    data = request.get_json()
    if not data or "message" not in data:
        return jsonify({"reply": "请输入有效消息"}), 400

    message = data["message"]
    reply = handle_text_message(message, "web_user")
    return jsonify({"reply": reply})

# ============ 医疗疫苗添加页面 ============
MED_PAGE_HTML = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<meta name="theme-color" content="#667eea">
<title>医疗情况</title>
<style>
  *{margin:0;padding:0;box-sizing:border-box;-webkit-tap-highlight-color:transparent;}
  body{font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;background:#f0f2f5;min-height:100vh;padding-bottom:40px;}
  .header{background:linear-gradient(135deg,#667eea 0%,#764ba2 100%);color:#fff;padding:16px;display:flex;align-items:center;gap:12px;box-shadow:0 2px 8px rgba(0,0,0,.15);}
  .header .back{background:rgba(255,255,255,.2);border:none;color:#fff;width:32px;height:32px;border-radius:50%;font-size:18px;cursor:pointer;display:flex;align-items:center;justify-content:center;text-decoration:none;}
  .header .title{font-size:16px;font-weight:600;flex:1;}
  .container{padding:16px;max-width:640px;margin:0 auto;}
  .card{background:#fff;border-radius:16px;padding:20px;margin-bottom:16px;box-shadow:0 1px 6px rgba(0,0,0,.06);}
  .card h2{font-size:17px;color:#333;margin-bottom:14px;border-left:4px solid #9990ED;padding-left:10px;}
  .card h2.vac{border-left-color:#F86C5B;}
  .med-item{background:#fff;border:1px solid #f0f0fa;border-radius:10px;margin-top:8px;overflow:hidden;}
  .med-main{display:flex;align-items:center;gap:8px;padding:10px 14px;background:#F9F9FF;cursor:pointer;flex-wrap:wrap;}
  .med-main .med-date{font-weight:600;color:#2B4A9A;white-space:nowrap;font-size:13px;}
  .med-main .med-hospital{font-size:13px;color:#333;flex:1;min-width:100px;}
  .med-main .med-doctor{font-size:12px;color:#666;background:#eef1fb;border-radius:6px;padding:2px 8px;white-space:nowrap;}
  .med-main .med-dept{font-size:12px;color:#888;white-space:nowrap;}
  .med-main .arrow{font-size:12px;color:#9990ED;transition:transform .2s;}
  .med-detail{padding:12px 16px;background:#fff;display:none;border-top:1px solid #f0f0fa;}
  .med-detail-row{display:flex;gap:8px;margin-top:6px;font-size:13px;line-height:1.6;}
  .med-detail-row .med-label{flex-shrink:0;color:#9990ED;font-weight:600;width:70px;}
  .med-detail-row span:last-child{color:#555;}
  .badge{display:inline-block;padding:2px 10px;border-radius:12px;font-size:11px;}
  .badge-birth{background:#FFF8E1;color:#F57F17;}
  .badge-checkup{background:#EDE7F6;color:#4527A0;}
  .badge-illness{background:#FCE8E6;color:#C5221F;}
  .badge-consult{background:#E8F0FE;color:#1A73E8;}
  .badge-other{background:#E6F4EA;color:#137333;}
  table{width:100%;border-collapse:collapse;margin-top:10px;}
  th,td{padding:8px 10px;text-align:left;border-bottom:1px solid #eee;font-size:13px;}
  th{background:#F9F9F9;font-weight:600;color:#555;}
  .vac-done{display:inline-block;padding:2px 10px;border-radius:12px;font-size:11px;background:#E0F7FA;color:#00838F;}
  .section-empty{text-align:center;color:#999;padding:20px;font-size:14px;}
  .add-btn{display:block;width:100%;padding:12px;border:none;border-radius:12px;background:linear-gradient(135deg,#667eea 0%,#764ba2 100%);color:#fff;font-size:15px;font-weight:600;cursor:pointer;text-align:center;text-decoration:none;margin-top:12px;}
</style>
</head>
<body>
<div class="header">
  <a class="back" href="/web">←</a>
  <div class="title">医疗情况</div>
</div>
<div class="container">
  <div class="card">
    <h2>🏥 就医记录（{{ medical_count }}）</h2>
    {% if medical_records %}
    {% for m in medical_records[:5] %}
    <div class="med-item">
      <div class="med-main" onclick="toggleMed(this)">
        <div class="med-date">{{ m.date }}</div>
        <div class="med-hospital">{{ m.hospital }}</div>
        <div class="med-doctor">{{ m.doctor }}</div>
        <div class="med-dept">{{ m.department }}</div>
        <span class="badge badge-{{ m.type }}">{{ m.type_label }}</span>
        <span class="arrow">▶</span>
      </div>
      <div class="med-detail">
        <div class="med-detail-row"><span class="med-label">主要诉求</span><span>{{ m.chief_complaint }}</span></div>
        {% if m.exam %}<div class="med-detail-row"><span class="med-label">检查内容</span><span>{{ m.exam }}</span></div>{% endif %}
        {% if m.diagnosis %}<div class="med-detail-row"><span class="med-label">诊断</span><span>{{ m.diagnosis }}</span></div>{% endif %}
        {% if m.advice %}<div class="med-detail-row"><span class="med-label">医生建议</span><span>{{ m.advice }}</span></div>{% endif %}
        {% if m.medication %}<div class="med-detail-row"><span class="med-label">用药情况</span><span>{{ m.medication }}</span></div>{% endif %}
      </div>
    </div>
    {% endfor %}
    {% if medical_count > 5 %}
    <div id="medMore" style="display:none;">
    {% for m in medical_records[5:] %}
    <div class="med-item">
      <div class="med-main" onclick="toggleMed(this)">
        <div class="med-date">{{ m.date }}</div>
        <div class="med-hospital">{{ m.hospital }}</div>
        <div class="med-doctor">{{ m.doctor }}</div>
        <div class="med-dept">{{ m.department }}</div>
        <span class="badge badge-{{ m.type }}">{{ m.type_label }}</span>
        <span class="arrow">▶</span>
      </div>
      <div class="med-detail">
        <div class="med-detail-row"><span class="med-label">主要诉求</span><span>{{ m.chief_complaint }}</span></div>
        {% if m.exam %}<div class="med-detail-row"><span class="med-label">检查内容</span><span>{{ m.exam }}</span></div>{% endif %}
        {% if m.diagnosis %}<div class="med-detail-row"><span class="med-label">诊断</span><span>{{ m.diagnosis }}</span></div>{% endif %}
        {% if m.advice %}<div class="med-detail-row"><span class="med-label">医生建议</span><span>{{ m.advice }}</span></div>{% endif %}
        {% if m.medication %}<div class="med-detail-row"><span class="med-label">用药情况</span><span>{{ m.medication }}</span></div>{% endif %}
      </div>
    </div>
    {% endfor %}
    </div>
    <div style="text-align:center;margin-top:10px;">
      <button onclick="var e=document.getElementById('medMore');e.style.display='block';this.style.display='none';" style="border:none;background:#f0f0fa;color:#2B4A9A;padding:8px 20px;border-radius:20px;font-size:13px;cursor:pointer;">展开剩余 {{ medical_count - 5 }} 条记录 ▼</button>
    </div>
    {% endif %}
    {% else %}
    <div class="section-empty">暂无就医记录</div>
    {% endif %}
    <a class="add-btn" href="/web/add?tab=medical">＋ 添加就医记录</a>
  </div>
  <div class="card">
    <h2 class="vac">💉 疫苗记录（{{ vaccine_count }}）</h2>
    {% if vaccine_records %}
    <table>
      <thead><tr><th>日期</th><th>疫苗名称</th><th>针剂</th><th>状态</th></tr></thead>
      <tbody>
      {% for v in vaccine_records[:5] %}
      <tr><td>{{ v.date }}</td><td>{{ v.vaccine }}</td><td>{{ v.dose }}</td><td><span class="vac-done">已接种</span></td></tr>
      {% endfor %}
      </tbody>
    </table>
    {% if vaccine_count > 5 %}
    <div id="vacMore" style="display:none;">
    <table>
      <tbody>
      {% for v in vaccine_records[5:] %}
      <tr><td>{{ v.date }}</td><td>{{ v.vaccine }}</td><td>{{ v.dose }}</td><td><span class="vac-done">已接种</span></td></tr>
      {% endfor %}
      </tbody>
    </table>
    </div>
    <div style="text-align:center;margin-top:10px;">
      <button onclick="var e=document.getElementById('vacMore');e.style.display='block';this.style.display='none';" style="border:none;background:#fef0ef;color:#F86C5B;padding:8px 20px;border-radius:20px;font-size:13px;cursor:pointer;">展开剩余 {{ vaccine_count - 5 }} 条记录 ▼</button>
    </div>
    {% endif %}
    {% else %}
    <div class="section-empty">暂无疫苗记录</div>
    {% endif %}
    <a class="add-btn" href="/web/add?tab=vaccine">＋ 添加疫苗记录</a>
  </div>
</div>
<script>
function toggleMed(main){
  const detail=main.nextElementSibling;
  const arrow=main.querySelector('.arrow');
  if(detail.style.display==='none'||!detail.style.display){detail.style.display='block';arrow.textContent='▼';}
  else{detail.style.display='none';arrow.textContent='▶';}
}
</script>
</body>
</html>
"""

@app.route("/web/med")
def web_med():
    """医疗情况页：就医记录+疫苗记录"""
    data = load_data()
    raw_medical = data.get("medical_records", [])
    for m in raw_medical:
        m.setdefault("hospital", "")
        m.setdefault("department", "")
        m.setdefault("doctor", "")
        m.setdefault("chief_complaint", m.get("description", ""))
        m.setdefault("exam", "")
        m.setdefault("diagnosis", "")
        m.setdefault("advice", "")
        m.setdefault("medication", "")
        # 类型中文标签
        type_label = {
            "birth": "出生", "checkup": "体检", "illness": "就诊",
            "consult": "咨询", "other": "其他"
        }.get(m.get("type"), "其他")
        m["type_label"] = type_label
    medical_display = sorted(raw_medical, key=lambda x: x["date"], reverse=True)

    raw_vaccine = data.get("vaccine_records", [])
    for v in raw_vaccine:
        v.setdefault("vaccine", v.get("vaccine_name", ""))
        v.setdefault("dose", "")
        v.setdefault("status", "completed")
    vaccine_display = sorted(raw_vaccine, key=lambda x: x["date"], reverse=True)

    return render_template_string(
        MED_PAGE_HTML,
        medical_records=medical_display,
        medical_count=len(medical_display),
        vaccine_records=vaccine_display,
        vaccine_count=len(vaccine_display)
    )


@app.route("/web/api/med", methods=["POST"])
def web_api_med():
    """医疗疫苗添加API"""
    data = request.get_json()
    if not data:
        return jsonify({"ok": False, "error": "无效请求"}), 400
    try:
        rec = load_data()
        if data.get("type") == "medical":
            entry = {
                "date": data.get("date", time.strftime("%Y-%m-%d")),
                "type": "illness",
                "hospital": data.get("hospital", ""),
                "department": data.get("department", ""),
                "doctor": data.get("doctor", ""),
                "chief_complaint": data.get("chief_complaint", ""),
                "exam": data.get("exam", ""),
                "diagnosis": data.get("diagnosis", ""),
                "advice": data.get("advice", ""),
                "medication": data.get("medication", ""),
            }
            # 图片转为路径占位（当前环境存储有限，仅记录数量）
            photos = data.get("photos", [])
            if photos:
                rec["photos"] = len(photos)
            rec["medical_records"].append(entry)
            rec["medical_records"].sort(key=lambda x: x["date"])
        elif data.get("type") == "vaccine":
            rec["vaccine_records"].append({
                "date": data.get("date", time.strftime("%Y-%m-%d")),
                "vaccine": data.get("vaccine", ""),
                "dose": data.get("dose", ""),
                "status": "completed"
            })
            rec["vaccine_records"].sort(key=lambda x: x["date"])
        else:
            return jsonify({"ok": False, "error": "未知类型"}), 400
        save_data(rec)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500



# ============ 新增记录页面 ============
ADD_PAGE_HTML = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<meta name="theme-color" content="#667eea">
<title>新增记录</title>
<script src="https://cdn.jsdelivr.net/npm/tesseract.js@5/dist/tesseract.min.js"></script>
<style>
  *{margin:0;padding:0;box-sizing:border-box;-webkit-tap-highlight-color:transparent;}
  body{font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;background:#f0f2f5;min-height:100vh;padding-bottom:40px;}
  .header{background:linear-gradient(135deg,#667eea 0%,#764ba2 100%);color:#fff;padding:16px;display:flex;align-items:center;gap:12px;box-shadow:0 2px 8px rgba(0,0,0,.15);}
  .header .back{background:rgba(255,255,255,.2);border:none;color:#fff;width:32px;height:32px;border-radius:50%;font-size:18px;cursor:pointer;display:flex;align-items:center;justify-content:center;text-decoration:none;}
  .header .title{font-size:16px;font-weight:600;}
  .container{padding:16px;max-width:600px;margin:0 auto;}
  .tabs{display:flex;gap:8px;margin-bottom:16px;}
  .tab{flex:1;padding:12px;border-radius:12px;border:2px solid #e0e0e0;background:#fff;text-align:center;font-size:14px;color:#666;cursor:pointer;}
  .tab.active{border-color:#667eea;color:#667eea;background:#f5f6ff;font-weight:600;}
  .panel{display:none;}
  .panel.active{display:block;}
  .card{background:#fff;border-radius:16px;padding:20px;margin-bottom:16px;box-shadow:0 1px 6px rgba(0,0,0,.06);}
  .card h3{font-size:15px;color:#333;margin-bottom:14px;}
  .form-group{margin-bottom:14px;}
  .form-group label{display:block;font-size:13px;color:#666;margin-bottom:6px;}
  .form-group input,.form-group textarea,.form-group select{width:100%;border:1px solid #e0e0e0;border-radius:10px;padding:10px 12px;font-size:15px;outline:none;background:#fafafa;font-family:inherit;}
  .form-group input:focus,.form-group textarea:focus,.form-group select:focus{border-color:#667eea;background:#fff;}
  .form-group textarea{min-height:70px;resize:vertical;}
  .mode-btn{flex:1;padding:10px;border:2px solid #e0e0e0;border-radius:10px;background:#fff;color:#666;font-size:14px;cursor:pointer;text-align:center;}
  .mode-btn.active{border-color:#667eea;color:#667eea;background:#f5f6ff;font-weight:600;}
  .submit-btn{width:100%;padding:14px;border:none;border-radius:12px;background:linear-gradient(135deg,#667eea 0%,#764ba2 100%);color:#fff;font-size:16px;font-weight:600;cursor:pointer;}
  .submit-btn:active{transform:scale(.98);}
  .submit-btn:disabled{background:#ccc;}
  .tip{font-size:12px;color:#999;margin-top:10px;line-height:1.6;}
  /* 滑动选择器 */
  .picker-overlay{position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,.5);z-index:100;display:none;justify-content:center;align-items:flex-end;}
  .picker-overlay.show{display:flex;}
  .picker,.picker-box{background:#fff;width:100%;max-width:600px;border-radius:16px 16px 0 0;padding:16px 0 calc(16px + env(safe-area-inset-bottom));box-shadow:0 -4px 24px rgba(0,0,0,.15);}
  .picker-header{display:flex;justify-content:space-between;align-items:center;padding:0 16px 12px;border-bottom:1px solid #eee;}
  .picker-header .picker-toggle{font-size:13px;color:#667eea;padding:6px 10px;border:1px solid #667eea;border-radius:8px;background:#f5f6ff;cursor:pointer;margin-right:8px;}
  .picker-header .cancel{font-size:15px;color:#999;background:none;border:none;cursor:pointer;}
  .picker-header .confirm{font-size:15px;color:#667eea;font-weight:600;background:none;border:none;cursor:pointer;}
  .picker-body{display:flex;justify-content:center;position:relative;height:220px;overflow:hidden;}
  .picker-col{flex:1;text-align:center;position:relative;height:220px;overflow:hidden;}
  .picker-col .wheel{position:absolute;top:0;left:0;right:0;transition:transform .3s ease;}
  .picker-col .wheel .item{height:44px;line-height:44px;font-size:17px;color:#333;background:transparent;}
  .picker-col .wheel .item.disabled{color:#ccc;}
  .picker-mask-top,.picker-mask-bottom{position:absolute;left:0;right:0;height:88px;pointer-events:none;z-index:2;}
  .picker-mask-top{top:0;background:linear-gradient(to bottom,#fff 0%,rgba(255,255,255,0) 100%);}
  .picker-mask-bottom{bottom:0;background:linear-gradient(to top,#fff 0%,rgba(255,255,255,0) 100%);}
  .picker-selection{position:absolute;top:88px;left:0;right:0;height:44px;border-top:2px solid #667eea;border-bottom:2px solid #667eea;z-index:1;pointer-events:none;border-radius:0;background:rgba(102,126,234,.08);}
  .picker-col .item.selected{color:#667eea;font-weight:600;background:rgba(102,126,234,.12);border-radius:8px;}
  .growth-preview{margin-bottom:14px;padding:12px 16px;background:#f5f6ff;border-radius:10px;font-size:14px;color:#4d5ec0;text-align:center;}
</style>
</head>
<body>
<div class="header">
  <a class="back" href="/web">←</a>
  <div class="title">新增记录</div>
</div>
<div class="container">
  <div class="tabs">
    <div class="tab active" onclick="switchTab('growth')" id="tab-growth">📏 记录成长</div>
    <div class="tab" onclick="switchTab('medical')" id="tab-medical">🏥 就医</div>
    <div class="tab" onclick="switchTab('vaccine')" id="tab-vaccine">💉 疫苗</div>
  </div>

  <!-- 记录成长 -->
  <div class="panel active" id="panel-growth">
    <div class="card">
      <h3>记录身高体重</h3>
      <div class="pick-preview" id="growthPreview" style="padding:12px 16px;background:#f5f6ff;border-radius:10px;font-size:14px;color:#4d5ec0;text-align:center;margin-bottom:14px;">
        请选择日期、身高和体重
      </div>
      <div class="form-group">
        <label>日期（点击选择）</label>
        <div class="picker-trigger" onclick="openPicker('date')" style="border:1px solid #e0e0e0;border-radius:10px;padding:10px 12px;font-size:15px;background:#fafafa;cursor:pointer;" id="date-trigger">选择日期</div>
      </div>
      <div class="form-group">
        <label>身高（点击选择，0.5cm颗粒度）</label>
        <div class="picker-trigger" onclick="openPicker('height')" style="border:1px solid #e0e0e0;border-radius:10px;padding:10px 12px;font-size:15px;background:#fafafa;cursor:pointer;" id="height-trigger">选择身高</div>
      </div>
      <div class="form-group">
        <label>体重（点击选择，0.1kg颗粒度）</label>
        <div class="picker-trigger" onclick="openPicker('weight')" style="border:1px solid #e0e0e0;border-radius:10px;padding:10px 12px;font-size:15px;background:#fafafa;cursor:pointer;" id="weight-trigger">选择体重</div>
      </div>
      <button class="submit-btn" onclick="submitGrowth()">保存成长记录</button>
    </div>
  </div>

  <!-- 记录就医 -->
  <div class="panel" id="panel-medical">
    <div class="card">
      <h3>就医记录</h3>
      <div class="mode-toggle" style="display:flex;gap:8px;margin-bottom:14px;">
        <button type="button" class="mode-btn active" id="med-mode-manual" onclick="switchMedMode('manual')">📝 人工记录</button>
        <button type="button" class="mode-btn" id="med-mode-photo" onclick="switchMedMode('photo')">📷 拍照记录</button>
      </div>
      <!-- 拍照模式 -->
      <div id="med-photo-box" style="display:none;">
        <div class="form-group">
          <label>上传就诊单据/病历照片</label>
          <input type="file" id="med-photo-input" accept="image/*" capture="environment" onchange="handleMedPhoto(this)">
        </div>
        <div id="med-photo-preview" style="display:none;margin-bottom:10px;">
          <img id="med-photo-img" style="width:100%;border-radius:10px;border:1px solid #eee;">
        </div>
        <div id="med-ocr-status" class="tip" style="margin-bottom:10px;">上传照片后将自动识别就诊信息</div>
        <button class="submit-btn" style="background:#5b6cb8;" onclick="runMedOcr()">识别照片信息</button>
        <div style="margin-top:10px;font-size:12px;color:#999;line-height:1.6;">识别结果会填入下方表单，请核对修改后再保存</div>
      </div>
      <!-- 人工模式 -->
      <div id="med-manual-box">
        <div class="form-group"><label>就诊日期</label><input type="date" id="med-date"></div>
        <div class="form-group"><label>医院名称</label><input type="text" id="med-hospital" placeholder="如：福建省儿童医院"></div>
        <div class="form-group"><label>科室</label><input type="text" id="med-dept" placeholder="如：儿科门诊"></div>
        <div class="form-group"><label>医生</label><input type="text" id="med-doctor" placeholder="如：张医生"></div>
        <div class="form-group"><label>主要诉求</label><textarea id="med-complaint" placeholder="简要描述就诊原因"></textarea></div>
        <div class="form-group"><label>诊断（可选）</label><input type="text" id="med-diagnosis" placeholder="如：上呼吸道感染"></div>
        <div class="form-group"><label>检查内容（可选）</label><textarea id="med-exam" placeholder="如：血常规、B超等"></textarea></div>
        <div class="form-group"><label>医生建议（可选）</label><textarea id="med-advice" placeholder="医生的建议或注意事项"></textarea></div>
        <div class="form-group"><label>用药情况（可选）</label><textarea id="med-medication" placeholder="如：头孢克洛 3天"></textarea></div>
      </div>
      <button class="submit-btn" onclick="submitMed()">保存就医记录</button>
    </div>
  </div>

  <!-- 记录疫苗 -->
  <div class="panel" id="panel-vaccine">
    <div class="card">
      <h3>疫苗记录</h3>
      <div class="form-group"><label>接种日期</label><input type="date" id="vac-date"></div>
      <div class="form-group"><label>疫苗名称（输入关键字选择标准名称）</label><input type="text" id="vac-name" list="vaccine-list" placeholder="如：五联、麻腮风"></div>
      <datalist id="vaccine-list">
        <option value="乙肝疫苗"></option>
        <option value="卡介苗"></option>
        <option value="脊灰灭活疫苗(IPV)"></option>
        <option value="脊灰减毒活疫苗(OPV)"></option>
        <option value="百白破疫苗"></option>
        <option value="百白破IPV和Hib五联疫苗"></option>
        <option value="麻腮风疫苗"></option>
        <option value="麻风腮疫苗(MMR)"></option>
        <option value="甲肝减毒活疫苗"></option>
        <option value="乙脑减毒活疫苗"></option>
        <option value="A群流脑疫苗"></option>
        <option value="A+C群流脑多糖疫苗"></option>
        <option value="13价肺炎疫苗"></option>
        <option value="23价肺炎疫苗"></option>
        <option value="轮状病毒疫苗"></option>
        <option value="EV71手足口疫苗"></option>
        <option value="水痘疫苗"></option>
        <option value="流感疫苗"></option>
        <option value="白破疫苗"></option>
        <option value="HPV疫苗"></option>
      </datalist>
      <div class="form-group"><label>针剂数</label><input type="text" id="vac-dose" placeholder="如：1/2、2/2"></div>
      <button class="submit-btn" onclick="submitVac()">保存疫苗记录</button>
    </div>
  </div>
  <p class="tip">提示：身高以0.5cm、体重以0.1kg为颗粒度，保存后可随时在成长趋势中查看。</p>
</div>

<!-- 访问码验证弹窗 -->
<div class="picker-overlay show" id="authOverlay" style="align-items:center;z-index:200;">
  <div style="background:#fff;width:85%;max-width:340px;border-radius:16px;padding:28px 24px;text-align:center;box-shadow:0 8px 32px rgba(0,0,0,.2);">
    <div style="font-size:40px;margin-bottom:8px;">🔒</div>
    <h3 style="font-size:18px;color:#333;margin-bottom:6px;">需要访问码</h3>
    <p style="font-size:13px;color:#888;margin-bottom:16px;">新增记录需输入访问码验证</p>
    <input type="password" id="auth-code" placeholder="请输入访问码" style="width:100%;border:1px solid #e0e0e0;border-radius:10px;padding:12px;font-size:16px;outline:none;text-align:center;box-sizing:border-box;">
    <div id="auth-error" style="color:#e74c3c;font-size:13px;margin-top:8px;display:none;">访问码错误，请重试</div>
    <button onclick="checkAuth()" style="width:100%;padding:12px;border:none;border-radius:10px;background:linear-gradient(135deg,#667eea 0%,#764ba2 100%);color:#fff;font-size:15px;font-weight:600;cursor:pointer;margin-top:14px;">进入</button>
  </div>
</div>

<!-- 滑动选择器 -->
<div class="picker-overlay" id="pickerOverlay">
  <div class="picker-box">
    <div class="picker-header">
      <button class="cancel" onclick="closePicker()">取消</button>
      <span id="pickerTitle">选择</span>
      <button class="confirm" onclick="confirmPicker()">确定</button>
    </div>
    <div class="picker-body">
      <div class="picker-mask-top"></div>
      <div class="picker-mask-bottom"></div>
      <div class="picker-selection"></div>
      <div class="picker-col" id="pickerCol">
        <div class="wheel" id="pickerWheel"></div>
      </div>
    </div>
  </div>
</div>

<script>
// ===== 滑动选择器 =====
let pickerType='date';
let pickerItems=[];
let pickerIndex=0;
let selected={date:null,height:null,weight:null};
let isTouch=false,startY=0,currentOffset=0,startOffset=0;

const ITEM_H=44;
const CENTER_OFFSET=88;

function pad(n){return n<10?'0'+n:''+n;}

// 日期：出生日至今
function buildDates(){
  const items=[];
  const end=new Date();
  const start=new Date(2024,2,13);
  const cur=new Date(end);
  while(cur>=start){
    items.push(cur.getFullYear()+'-'+pad(cur.getMonth()+1)+'-'+pad(cur.getDate()));
    cur.setDate(cur.getDate()-1);
  }
  return items;
}
// 身高：40~120cm，0.5步进
function buildHeights(){
  const items=[];
  for(let h=40;h<=120;h+=0.5){
    items.push((Math.round(h*10)/10).toFixed(1)+'cm');
  }
  return items;
}
// 体重：1.0~30kg，0.1步进
function buildWeights(){
  const items=[];
  for(let w=1.0;w<=30;w+=0.1){
    items.push((Math.round(w*10)/10).toFixed(1)+'kg');
  }
  return items;
}

function openPicker(type){
  pickerType=type;
  const pTitle=document.getElementById('pickerTitle');
  if(type==='date'){pickerItems=buildDates();pTitle.textContent='选择日期';}
  else if(type==='height'){pickerItems=buildHeights();pTitle.textContent='选择身高';}
  else{pickerItems=buildWeights();pTitle.textContent='选择体重';}
  // 定位当前值
  let curIdx=Math.floor(pickerItems.length/2);
  if(type==='date'&&selected.date){curIdx=pickerItems.indexOf(selected.date);}
  else if(type==='height'&&selected.height){curIdx=pickerItems.indexOf(selected.height+'cm');}
  else if(type==='weight'&&selected.weight){curIdx=pickerItems.indexOf(selected.weight+'kg');}
  if(curIdx<0)curIdx=Math.floor(pickerItems.length/2);
  pickerIndex=curIdx;
  renderWheel();
  document.getElementById('pickerOverlay').classList.add('show');
}

function renderWheel(){
  const wheel=document.getElementById('pickerWheel');
  wheel.innerHTML='';
  pickerItems.forEach((item,i)=>{
    const d=document.createElement('div');
    d.className='item'+(i===pickerIndex?' selected':'');
    d.textContent=item;
    wheel.appendChild(d);
  });
  wheel.style.transform='translateY('+(-pickerIndex*ITEM_H+CENTER_OFFSET)+'px)';
}

// 触摸滑动
const wheel=document.getElementById('pickerWheel');
let touchStartY=0,touchBaseOffset=0;
wheel.addEventListener('touchstart',e=>{
  isTouch=true;
  touchStartY=e.touches[0].clientY;
  const m=wheel.style.transform.match(/translateY\((-?[\d.]+)px\)/);
  touchBaseOffset=m?parseFloat(m[1]):(-pickerIndex*ITEM_H+CENTER_OFFSET);
});
wheel.addEventListener('touchmove',e=>{
  if(!isTouch)return;
  e.preventDefault();
  const dy=e.touches[0].clientY-touchStartY;
  wheel.style.transform='translateY('+(touchBaseOffset+dy)+'px)';
});
wheel.addEventListener('touchend',e=>{
  if(!isTouch)return;
  isTouch=false;
  const m=wheel.style.transform.match(/translateY\((-?[\d.]+)px\)/);
  const finalY=m?parseFloat(m[1]):0;
  const idx=Math.round((CENTER_OFFSET-finalY)/ITEM_H);
  pickerIndex=Math.max(0,Math.min(pickerItems.length-1,idx));
  renderWheel();
});

// 也支持点击
document.getElementById('pickerWheel').addEventListener('click',e=>{
  const itemEl=e.target.closest('.item');
  if(!itemEl)return;
  const items=document.querySelectorAll('#pickerWheel .item');
  items.forEach((it,i)=>{
    if(it===itemEl){
      pickerIndex=i;
      renderWheel();
    }
  });
});

function confirmPicker(){
  const val=pickerItems[pickerIndex];
  if(pickerType==='date'){
    selected.date=val;
    document.getElementById('date-trigger').textContent=val;
  }else if(pickerType==='height'){
    selected.height=parseFloat(val);
    document.getElementById('height-trigger').textContent=val;
  }else{
    selected.weight=parseFloat(val);
    document.getElementById('weight-trigger').textContent=val;
  }
  closePicker();
  updatePreview();
}

function closePicker(){
  document.getElementById('pickerOverlay').classList.remove('show');
}

function updatePreview(){
  const p=document.getElementById('growthPreview');
  if(selected.date&&selected.height&&selected.weight){
    p.textContent='📅 '+selected.date+'  |  📏 '+selected.height+'cm  |  ⚖️ '+selected.weight+'kg';
  }else{
    let parts=[];
    if(selected.date)parts.push('日期:'+selected.date);
    if(selected.height)parts.push('身高:'+selected.height+'cm');
    if(selected.weight)parts.push('体重:'+selected.weight+'kg');
    p.textContent=parts.length?('已选：'+parts.join('、')):'请选择日期、身高和体重';
  }
}

async function submitGrowth(){
  if(!selected.date||!selected.height||!selected.weight){
    alert('请完整选择日期、身高和体重');
    return;
  }
  const btn=document.querySelector('#panel-growth .submit-btn');
  btn.disabled=true;
  try{
    const r=await fetch('/web/api/growth',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({date:selected.date,height:selected.height,weight:selected.weight,auth_code:'20240313'})});
    const data=await r.json();
    if(data.ok){
      alert('已保存成长记录！');
      selected={date:null,height:null,weight:null};
      document.getElementById('date-trigger').textContent='选择日期';
      document.getElementById('height-trigger').textContent='选择身高';
      document.getElementById('weight-trigger').textContent='选择体重';
      updatePreview();
    }else{alert('保存失败：'+data.error);}
  }catch(e){alert('网络异常，请重试');}
  btn.disabled=false;
}

// ===== 医疗/疫苗表单 =====
function switchTab(t){
  ['growth','medical','vaccine'].forEach(x=>{
    document.getElementById('tab-'+x).classList.toggle('active',x===t);
    document.getElementById('panel-'+x).classList.toggle('active',x===t);
  });
}

// ===== 就医记录：人工/拍照模式切换 =====
let medPhotoData=null;
function switchMedMode(mode){
  const manualBtn=document.getElementById('med-mode-manual');
  const photoBtn=document.getElementById('med-mode-photo');
  if(mode==='photo'){
    manualBtn.classList.remove('active');photoBtn.classList.add('active');
    document.getElementById('med-manual-box').style.display='none';
    document.getElementById('med-photo-box').style.display='block';
  }else{
    photoBtn.classList.remove('active');manualBtn.classList.add('active');
    document.getElementById('med-photo-box').style.display='none';
    document.getElementById('med-manual-box').style.display='block';
  }
}
function handleMedPhoto(input){
  const file=input.files[0];
  if(!file)return;
  medImageData=null;
  document.getElementById('med-ocr-status').textContent='照片已选择，点击"识别照片信息"开始识别';
  const img=document.getElementById('med-photo-img');
  img.src=URL.createObjectURL(file);
  document.getElementById('med-photo-preview').style.display='block';
}
// OCR：使用 Tesseract.js 纯前端识别（无需外部服务密钥）
async function runMedOcr(){
  const input=document.getElementById('med-photo-input');
  if(!input.files[0]){alert('请先选择照片');return;}
  const status=document.getElementById('med-ocr-status');
  status.textContent='正在识别照片（首次加载识别引擎较慢）…';
  const btn=document.querySelector('#med-photo-box .submit-btn');
  btn.disabled=true;
  try{
    const worker=await Tesseract.createWorker('chi_sim');
    const ret=await worker.recognize(input.files[0]);
    const text=ret.data.text;
    await worker.terminate();
    status.textContent='识别完成，请核对下方表单';
    const info=parseOcrText(text);
    if(info.date)document.getElementById('med-date').value=info.date;
    if(info.hospital)document.getElementById('med-hospital').value=info.hospital;
    if(info.dept)document.getElementById('med-dept').value=info.dept;
    if(info.doctor)document.getElementById('med-doctor').value=info.doctor;
    if(info.complaint)document.getElementById('med-complaint').value=info.complaint;
    if(info.diagnosis)document.getElementById('med-diagnosis').value=info.diagnosis;
    // 自动切换到人工模式展示识别结果
    switchMedMode('manual');
    alert('已识别照片信息并填入表单，请核对修改后保存');
  }catch(e){
    status.textContent='识别失败：'+e.message;
    alert('识别失败，请手动记录');
  }
  btn.disabled=false;
}
function parseOcrText(text){
  const info={};
  // 日期（支持 2024-03-13 / 2024年3月13日 / 2024.3.13）
  const dm=text.match(/(20\d{2})[年.\/-](\d{1,2})[月.\/-](\d{1,2})/);
  if(dm)info.date=dm[1]+'-'+String(dm[2]).padStart(2,'0')+'-'+String(dm[3]).padStart(2,'0');
  // 医院（以 医院/门诊/中心 结尾的连续中文）
  const hm=text.match(/([\u4e00-\u9fa5]{2,}(?:医院|门诊部|门诊|中心|卫生院|诊所))/);
  if(hm)info.hospital=hm[1];
  // 科室（XX科）
  const dm2=text.match(/([\u4e00-\u9fa5]{2,6}科(?:室|门诊)?)/);
  if(dm2)info.dept=dm2[1];
  // 医生（XX医生/XX医师/姓+医）
  const docm=text.match(/([\u4e00-\u9fa5]{2,4}(?:医生|医师|大夫))/);
  if(docm)info.doctor=docm[1];
  // 诊断：匹配 "诊断[:：]xxx" 或 "印象[:：]xxx"
  const diagm=text.match(/(?:诊断|印象|初步诊断)[:：]?\s*([^\n，。;；]{2,30})/);
  if(diagm)info.diagnosis=diagm[1];
  // 主要诉求：匹配 主诉/就诊原因 后内容
  const cm=text.match(/(?:主诉|就诊原因|来诊原因)[:：]?\s*([^\n，。;；]{2,40})/);
  if(cm)info.complaint=cm[1];
  return info;
}

// ===== 疫苗名称标准校验 =====
const STANDARD_VACCINES=[
  '乙肝疫苗','卡介苗','脊灰灭活疫苗(IPV)','脊灰减毒活疫苗(OPV)','百白破疫苗',
  '百白破IPV和Hib五联疫苗','麻腮风疫苗','麻风腮疫苗(MMR)','甲肝减毒活疫苗','乙脑减毒活疫苗',
  'A群流脑疫苗','A+C群流脑多糖疫苗','13价肺炎疫苗','23价肺炎疫苗','轮状病毒疫苗',
  'EV71手足口疫苗','水痘疫苗','流感疫苗','白破疫苗','HPV疫苗'
];
// 常见别名→标准名映射
const VACCINE_ALIASES={
  '五联':'百白破IPV和Hib五联疫苗','五联疫苗':'百白破IPV和Hib五联疫苗','四联':'百白破疫苗','百白破':'百白破疫苗',
  '麻风腮':'麻风腮疫苗(MMR)','麻腮风':'麻腮风疫苗','mmr':'麻风腮疫苗(MMR)','MMR':'麻风腮疫苗(MMR)',
  '乙肝':'乙肝疫苗','卡介':'卡介苗','脊灰':'脊灰灭活疫苗(IPV)','IPV':'脊灰灭活疫苗(IPV)',
  '甲肝':'甲肝减毒活疫苗','乙脑':'乙脑减毒活疫苗','流脑':'A群流脑疫苗','ac流脑':'A+C群流脑多糖疫苗',
  '13价':'13价肺炎疫苗','肺炎':'13价肺炎疫苗','轮状':'轮状病毒疫苗','手足口':'EV71手足口疫苗',
  '水痘':'水痘疫苗','流感':'流感疫苗','白破':'白破疫苗','hpv':'HPV疫苗','HPV':'HPV疫苗'
};
function normalizeVaccine(raw){
  const name=(raw||'').trim();
  if(!name)return {ok:false,error:'请输入疫苗名称'};
  // 精确匹配标准名
  if(STANDARD_VACCINES.includes(name))return {ok:true,name};
  // 别名映射
  if(VACCINE_ALIASES[name])return {ok:true,name:VACCINE_ALIASES[name]};
  // 模糊匹配：标准名包含输入 或 输入包含标准名
  const found=STANDARD_VACCINES.filter(v=>v.includes(name)||name.includes(v));
  if(found.length===1)return {ok:true,name:found[0]};
  if(found.length>1)return {ok:false,error:'名称不明确，请从下拉列表选择：'+found.join('、')};
  return {ok:false,error:'未找到匹配的标准疫苗名称，请从下拉列表选择'};
}

// ===== 访问码验证 =====
function checkAuth(){
  const code=document.getElementById('auth-code').value.trim();
  if(code==='20240313'){
    document.getElementById('authOverlay').style.display='none';
    sessionStorage.setItem('ali_add_auth','1');
  }else{
    document.getElementById('auth-error').style.display='block';
    document.getElementById('auth-code').value='';
  }
}
(function(){
  if(sessionStorage.getItem('ali_add_auth')==='1'){
    document.getElementById('authOverlay').style.display='none';
  }
})();

function today(){const d=new Date();return d.getFullYear()+'-'+pad(d.getMonth()+1)+'-'+pad(d.getDate());}
document.getElementById('med-date').value=today();
document.getElementById('vac-date').value=today();

async function submitMed(){
  const payload={
    type:'medical',
    date:document.getElementById('med-date').value,
    hospital:document.getElementById('med-hospital').value,
    department:document.getElementById('med-dept').value,
    doctor:document.getElementById('med-doctor').value,
    chief_complaint:document.getElementById('med-complaint').value,
    diagnosis:document.getElementById('med-diagnosis').value,
    exam:document.getElementById('med-exam').value,
    advice:document.getElementById('med-advice').value,
    medication:document.getElementById('med-medication').value,
    auth_code:'20240313'
  };
  const r=await fetch('/api/med',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
  const data=await r.json();
  if(data.ok){alert('已保存就医记录！');window.location.href='/web/med';}
  else{alert('保存失败：'+data.error);}
}
async function submitVac(){
  const rawName=document.getElementById('vac-name').value;
  const check=normalizeVaccine(rawName);
  if(!check.ok){alert(check.error);return;}
  const payload={
    type:'vaccine',
    date:document.getElementById('vac-date').value,
    vaccine:check.name,
    dose:document.getElementById('vac-dose').value,
    auth_code:'20240313'
  };
  const r=await fetch('/api/med',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
  const data=await r.json();
  if(data.ok){alert('已保存疫苗记录！');window.location.href='/web/med';}
  else{alert('保存失败：'+data.error);}
}
// 初始预览
updatePreview();
</script>
</body>
</html>
"""

@app.route("/web/add")
def web_add():
    """新增记录页"""
    return render_template_string(ADD_PAGE_HTML)


@app.route("/api/growth", methods=["POST"])
def api_growth():
    """新增成长记录API（滑动选择器提交）"""
    data = request.get_json()
    if not data:
        return jsonify({"ok": False, "error": "无效请求"}), 400
    # 访问码校验
    if data.get("auth_code") != "20240313":
        return jsonify({"ok": False, "error": "访问码错误"}), 403
    try:
        date = data.get("date")
        height = float(data.get("height"))
        weight = float(data.get("weight"))
        if not date:
            date = time.strftime("%Y-%m-%d")
        # 合理性检查（放宽到早产儿范围）
        if not (40 <= height <= 120):
            return jsonify({"ok": False, "error": "身高范围异常"}), 400
        if not (1.5 <= weight <= 30):
            return jsonify({"ok": False, "error": "体重范围异常"}), 400
        rec = load_data()
        rec["growth_records"].append({
            "date": date, "height_cm": round(height, 1), "weight_kg": round(weight, 1),
            "source": "web"
        })
        rec["growth_records"].sort(key=lambda x: x["date"])
        save_data(rec)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/med", methods=["POST"])
def api_med():
    """新增医疗/疫苗记录API（新增记录页提交）"""
    data = request.get_json()
    if not data:
        return jsonify({"ok": False, "error": "无效请求"}), 400
    # 访问码校验
    if data.get("auth_code") != "20240313":
        return jsonify({"ok": False, "error": "访问码错误"}), 403
    try:
        rec = load_data()
        if data.get("type") == "medical":
            entry = {
                "date": data.get("date", time.strftime("%Y-%m-%d")),
                "type": "illness",
                "hospital": data.get("hospital", ""),
                "department": data.get("department", ""),
                "doctor": data.get("doctor", ""),
                "chief_complaint": data.get("chief_complaint", ""),
                "exam": data.get("exam", ""),
                "diagnosis": data.get("diagnosis", ""),
                "advice": data.get("advice", ""),
                "medication": data.get("medication", ""),
            }
            rec["medical_records"].append(entry)
            rec["medical_records"].sort(key=lambda x: x["date"])
        elif data.get("type") == "vaccine":
            rec["vaccine_records"].append({
                "date": data.get("date", time.strftime("%Y-%m-%d")),
                "vaccine": data.get("vaccine", ""),
                "dose": data.get("dose", ""),
                "status": "completed"
            })
            rec["vaccine_records"].sort(key=lambda x: x["date"])
        else:
            return jsonify({"ok": False, "error": "未知类型"}), 400
        save_data(rec)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ============ 养育咨询（对话框形式） ============
CHAT_PAGE_HTML = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="theme-color" content="#667eea">
<title>养育咨询</title>
<style>
  *{margin:0;padding:0;box-sizing:border-box;-webkit-tap-highlight-color:transparent;}
  html,body{height:100%;overflow:hidden;}
  body{font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;background:#f0f2f5;display:flex;flex-direction:column;height:100vh;}
  .header{background:linear-gradient(135deg,#667eea 0%,#764ba2 100%);color:#fff;padding:14px 16px;display:flex;align-items:center;gap:12px;flex-shrink:0;z-index:10;box-shadow:0 2px 8px rgba(0,0,0,.15);}
  .header .back{background:rgba(255,255,255,.2);border:none;color:#fff;width:32px;height:32px;border-radius:50%;font-size:18px;cursor:pointer;display:flex;align-items:center;justify-content:center;text-decoration:none;}
  .header .title{font-size:16px;font-weight:600;flex:1;}
  .messages{flex:1;overflow-y:auto;-webkit-overflow-scrolling:touch;padding:16px 12px;display:flex;flex-direction:column;gap:12px;}
  .msg{max-width:80%;padding:10px 14px;border-radius:16px;font-size:15px;line-height:1.6;word-break:break-word;white-space:pre-wrap;animation:fadeIn .3s ease;}
  .msg.user{align-self:flex-end;background:#667eea;color:#fff;border-bottom-right-radius:4px;}
  .msg.bot{align-self:flex-start;background:#fff;color:#333;border-bottom-left-radius:4px;box-shadow:0 1px 3px rgba(0,0,0,.08);}
  .typing{align-self:flex-start;background:#fff;color:#999;padding:12px 16px;border-radius:16px;border-bottom-left-radius:4px;box-shadow:0 1px 3px rgba(0,0,0,.08);}
  .typing span{display:inline-block;width:6px;height:6px;border-radius:50%;background:#aaa;margin:0 1px;animation:blink 1.4s infinite both;}
  .typing span:nth-child(2){animation-delay:.2s;}
  .typing span:nth-child(3){animation-delay:.4s;}
  .input-bar{display:flex;align-items:center;gap:8px;padding:10px 12px;background:#fff;border-top:1px solid #e0e0e0;flex-shrink:0;padding-bottom:calc(10px + env(safe-area-inset-bottom));}
  .input-bar input{flex:1;border:1px solid #e0e0e0;border-radius:22px;padding:10px 16px;font-size:16px;outline:none;background:#f8f8f8;}
  .input-bar input:focus{border-color:#667eea;background:#fff;}
  .input-bar button{width:40px;height:40px;border-radius:50%;border:none;background:#667eea;color:#fff;font-size:20px;cursor:pointer;display:flex;align-items:center;justify-content:center;flex-shrink:0;}
  .input-bar button:active{transform:scale(.92);}
  .input-bar button:disabled{background:#ccc;}
  @keyframes fadeIn{from{opacity:0;transform:translateY(8px);}to{opacity:1;transform:translateY(0);}}
  @keyframes blink{0%,80%,100%{opacity:.2;}40%{opacity:1;}}
</style>
</head>
<body>
<div class="header">
  <a class="back" href="/web">←</a>
  <div class="title">💬 养育咨询</div>
</div>
<div class="messages" id="messages">
  <div class="msg bot">你好，我是阿鲤的养育助手。关于喂养、睡眠、发育、疫苗接种等问题都可以问我～</div>
</div>
<div class="input-bar">
  <input type="text" id="input" placeholder="输入养育问题..." onkeydown="if(event.key==='Enter')sendMsg()" maxlength="500">
  <button onclick="sendMsg()" id="sendBtn">↑</button>
</div>
<script>
let sending=false;
function scrollBottom(){const m=document.getElementById('messages');m.scrollTop=m.scrollHeight;}
function appendMsg(text,isUser){const d=document.createElement('div');d.className='msg '+(isUser?'user':'bot');d.textContent=text;document.getElementById('messages').appendChild(d);scrollBottom();}
function appendTyping(){const d=document.createElement('div');d.className='typing';d.id='typing';d.innerHTML='<span></span><span></span><span></span>';document.getElementById('messages').appendChild(d);scrollBottom();}
function removeTyping(){const t=document.getElementById('typing');if(t)t.remove();}
async function sendMsg(){
  if(sending)return;
  const input=document.getElementById('input');
  const text=input.value.trim();
  if(!text)return;
  input.value='';
  sending=true;
  document.getElementById('sendBtn').disabled=true;
  appendMsg(text,true);
  appendTyping();
  try{
    const r=await fetch('/web/chat/api',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:text})});
    const data=await r.json();
    removeTyping();
    appendMsg(data.reply,false);
  }catch(e){
    removeTyping();
    appendMsg('网络异常，请稍后重试',false);
  }
  sending=false;
  document.getElementById('sendBtn').disabled=false;
  input.focus();
}
scrollBottom();
</script>
</body>
</html>
"""

@app.route("/web/chat")
def web_chat_page():
    """养育咨询对话框页面"""
    return render_template_string(CHAT_PAGE_HTML)


@app.route("/web/chat/api", methods=["POST"])
def web_chat_api():
    """养育咨询API — 仅对话，记录保留一年"""
    data = request.get_json()
    if not data or "message" not in data:
        return jsonify({"reply": "请输入有效消息"}), 400

    message = data["message"]
    rec = load_data()

    # 清理超过1年的历史记录
    from datetime import datetime as _dt, timedelta as _td
    cutoff = (_dt.now() - _td(days=365)).strftime("%Y-%m-%d %H:%M:%S")
    history = rec.get("chat_history", [])
    history = [h for h in history if h.get("time", "") >= cutoff]
    rec["chat_history"] = history

    # 只走AI对话（不触发记录解析）
    reply = chat_with_ai(message, "chat")

    # 保存本次对话（含时间戳，用于1年清理）
    now_str = _dt.now().strftime("%Y-%m-%d %H:%M:%S")
    history.append({"role": "user", "content": message, "time": now_str})
    history.append({"role": "assistant", "content": reply, "time": now_str})
    rec["chat_history"] = history[-500:]  # 最多保留500条
    save_data(rec)

    return jsonify({"reply": reply})


# ============ 健康检查 ============
@app.route("/")
def health():
    return "ok", 200


@app.route("/health")
def health_check():
    return json.dumps({"status": "ok", "ai_enabled": ai_client is not None}), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
