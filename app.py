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
- 出生身高：47cm，出生体重：1650g（早产低体重儿）
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
            "birth_height_cm": 47, "birth_weight_g": 1650
        },
        "growth_records": [
            {"date": "2025-06-11", "height_cm": 74, "weight_kg": 10.7},
            {"date": "2025-10-18", "height_cm": 79, "weight_kg": 11.0},
            {"date": "2025-12-14", "height_cm": 80, "weight_kg": 11.7},
            {"date": "2026-01-12", "height_cm": 80, "weight_kg": 11.5},
            {"date": "2026-03-01", "height_cm": 82, "weight_kg": 12.3},
            {"date": "2026-04-28", "height_cm": 84, "weight_kg": 12.0},
            {"date": "2026-05-08", "height_cm": 85, "weight_kg": 12.0},
            {"date": "2026-06-04", "height_cm": 85, "weight_kg": 11.7},
            {"date": "2026-07-05", "height_cm": 86.5, "weight_kg": 12.4}
        ],
        "medical_records": [
            {"date": "2024-03-13", "description": "出生，早产低体重儿", "type": "birth"}
        ],
        "vaccine_records": [],
        "chat_history": []
    }

def save_data(data):
    """保存数据文件"""
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ============ 消息解析 ============
def parse_growth_message(text):
    """从消息文本中提取身高体重数据"""
    # 匹配多种格式
    patterns = [
        r'(\d+(?:\.\d+)?)\s*cm\s*,?\s*(\d+(?:\.\d+)?)\s*kg',
        r'(\d+(?:\.\d+)?)\s*cm\s+(\d+(?:\.\d+)?)\s*kg',
        r'身高\s*(\d+(?:\.\d+)?)\s*.*?体重\s*(\d+(?:\.\d+)?)',
        r'(\d{2,3}(?:\.\d+)?)\s+(\d{1,2}(?:\.\d+)?)\s*(?:kg|公斤|斤)?',
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            height = float(m.group(1))
            weight = float(m.group(2))
            # 合理性检查
            if 40 < height < 120 and 3 < weight < 30:
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


def record_growth(height, weight):
    """记录身高体重"""
    data = load_data()
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
    record = {"date": today, "description": description, "type": mtype}
    data["medical_records"].append(record)
    save_data(data)
    return record


def record_vaccine(description):
    """记录疫苗"""
    data = load_data()
    today = time.strftime("%Y-%m-%d")
    record = {"date": today, "vaccine_name": description, "dose": "", "status": "completed"}
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
            record_growth(parsed["height_cm"], parsed["weight_kg"])
            # 让AI评估并生成回复
            reply = chat_with_ai(
                f"家长记录了阿鲤今天的身高{parsed['height_cm']}cm，体重{parsed['weight_kg']}kg。请确认记录并简要评估。",
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
  .next-item{display:flex;align-items:center;gap:10px;padding:10px 14px;background:#F9F9FF;border-radius:10px;margin-top:8px;font-size:14px;border:1px solid #f0f0fa;}
  .next-item .icon{font-size:20px;flex-shrink:0;}
  .next-item .info{flex:1;}
  .next-item .info .name{font-weight:600;color:#333;}
  .next-item .info .date{font-size:12px;color:#888;margin-top:2px;}
  .next-item .countdown{font-size:13px;font-weight:600;color:#9990ED;white-space:nowrap;}
  .collapsible-header{cursor:pointer;display:flex;align-items:center;justify-content:space-between;user-select:none;}
  .collapsible-header .arrow{transition:transform 0.3s;font-size:14px;color:#9990ED;}
  .collapsible-header.collapsed .arrow{transform:rotate(-90deg);}
  .collapsible-content{overflow:hidden;transition:max-height 0.3s ease-out;max-height:3000px;}
  .collapsible-content.collapsed{max-height:0;}
  .advice-section{margin-top:8px;}
  .advice-item{padding:12px 16px;background:#F9F9F9;border-radius:10px;margin-top:8px;border-left:3px solid #9990ED;}
  .advice-item .advice-title{font-size:14px;font-weight:600;color:#2B4A9A;margin-bottom:4px;}
  .advice-item .advice-text{font-size:13px;color:#666;line-height:1.6;}
  .advice-item.diet{border-left-color:#FAD465;}
  .advice-item.diet .advice-title{color:#B8860B;}
  .advice-item.sleep{border-left-color:#9990ED;}
  .advice-item.sleep .advice-title{color:#7B68EE;}
  .advice-item.exercise{border-left-color:#66D9D0;}
  .advice-item.exercise .advice-title{color:#26A69A;}
  .advice-item.skill{border-left-color:#F86C5B;}
  .advice-item.skill .advice-title{color:#E53935;}
  .advice-item.life{border-left-color:#93C1FE;}
  .advice-item.life .advice-title{color:#5C9BFE;}
  .advice-item.season{border-left-color:#2B4A9A;}
  .advice-item.season .advice-title{color:#2B4A9A;}
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
  @media(max-width:600px){.stats-grid{grid-template-columns:1fr 1fr;}.stat-box.full-width{grid-column:1/-1;}.card{padding:16px;}.chart-container{height:300px;}}
</style>
</head>
<body>
<div class="header">
  <h1>阿鲤的成长记录</h1>
  <div class="info">出生：{{ birth_date }} | 男 | 早产低体重儿（出生47cm/1650g）</div>
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
      <div style="font-size:14px;color:#666;margin-bottom:4px;">近期节点</div>
      {% for item in next_items %}
      <div class="next-item">
        <div class="icon">{{ item.icon }}</div>
        <div class="info">
          <div class="name">{{ item.name }}</div>
          <div class="date">{{ item.date }}</div>
        </div>
        <div class="countdown">{{ item.countdown }}</div>
      </div>
      {% endfor %}
    </div>
    {% endif %}
    <div class="advice-section" style="margin-top:16px;">
      <div class="collapsible-header collapsed" onclick="toggleSection(this)">
        <h2 style="margin:0;font-size:18px;color:#2B4A9A;border-left:4px solid #FAD465;padding-left:12px;">喂养建议</h2>
        <span class="arrow">▼</span>
      </div>
      <div class="collapsible-content collapsed">
        {% for a in advice_items %}
        <div class="advice-item {{ a.type }}">
          <div class="advice-title">{{ a.icon }} {{ a.title }}</div>
          <div class="advice-text">{{ a.text }}</div>
        </div>
        {% endfor %}
      </div>
    </div>
  </div>

  <div class="card">
    <h2>身高生长曲线</h2>
    <div class="chart-controls">
      <div class="ctrl-group"><label>区间</label><select id="heightRange" onchange="updateChart('height')">
        <option value="auto">默认（近1年+后3月）</option>
        <option value="0-60">0-60月（全部）</option>
        <option value="0-24">0-24月</option>
        <option value="0-36">0-36月</option>
        <option value="0-12">0-12月</option>
        <option value="12-36">12-36月</option>
        <option value="24-60">24-60月</option>
      </select></div>
    </div>
    <div class="chart-container"><canvas id="heightChart"></canvas></div>
  </div>

  <div class="card">
    <h2>体重生长曲线</h2>
    <div class="chart-controls">
      <div class="ctrl-group"><label>区间</label><select id="weightRange" onchange="updateChart('weight')">
        <option value="auto">默认（近1年+后3月）</option>
        <option value="0-60">0-60月（全部）</option>
        <option value="0-24">0-24月</option>
        <option value="0-36">0-36月</option>
        <option value="0-12">0-12月</option>
        <option value="12-36">12-36月</option>
        <option value="24-60">24-60月</option>
      </select></div>
    </div>
    <div class="chart-container"><canvas id="weightChart"></canvas></div>
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
  {% if medical_records %}
  <div class="card">
    <div class="collapsible-header collapsed" onclick="toggleSection(this)">
      <h2 style="border-left:4px solid #9990ED;padding-left:12px;">就医记录</h2>
      <span class="arrow">▼</span>
    </div>
    <div class="collapsible-content collapsed">
      <table><thead><tr><th>日期</th><th>类型</th><th>描述</th></tr></thead><tbody>
      {% for m in medical_records %}
      <tr><td>{{ m.date }}</td><td><span class="badge badge-{{ m.type }}">{{ m.type }}</span></td><td>{{ m.description }}</td></tr>
      {% endfor %}
      </tbody></table>
    </div>
  </div>
  {% endif %}
  {% if vaccine_records %}
  <div class="card">
    <div class="collapsible-header collapsed" onclick="toggleSection(this)">
      <h2 style="border-left:4px solid #F86C5B;padding-left:12px;">疫苗记录</h2>
      <span class="arrow">▼</span>
    </div>
    <div class="collapsible-content collapsed">
      <table><thead><tr><th>日期</th><th>疫苗名称</th><th>状态</th></tr></thead><tbody>
      {% for v in vaccine_records %}
      <tr><td>{{ v.date }}</td><td>{{ v.vaccine_name }}</td><td><span class="badge badge-vaccine">已接种</span></td></tr>
      {% endfor %}
      </tbody></table>
    </div>
  </div>
  {% endif %}
</div>
<script>
const allWhoHeight={{ who_height|safe }};
const allWhoWeight={{ who_weight|safe }};
const actualHeights={{ actual_heights|safe }};
const actualWeights={{ actual_weights|safe }};
const autoMin={{ chart_min_month }};
const autoMax={{ chart_max_month }};
let heightChart=null,weightChart=null;
let tooltipTimer=null;

function toggleSection(header){
  header.classList.toggle('collapsed');
  header.nextElementSibling.classList.toggle('collapsed');
}

function buildChart(ctxId,whoData,actual,range,yLabel){
  let minM,maxM;
  if(range==='auto'){minM=autoMin;maxM=autoMax;}
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
    pointRadius:7,pointHoverRadius:10,
    pointBackgroundColor:'#2B4A9A',
    pointBorderColor:'#fff',pointBorderWidth:2,
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
            if(tooltipTimer){clearTimeout(tooltipTimer);tooltipTimer=null;}
            tooltipTimer=setTimeout(function(){
              context.tooltip.opacity=0;
              context.chart.update('none');
            },3000);
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
    """根据月龄和季节生成喂养建议"""
    advice = []
    if age_months < 24:
        advice.append({"type":"diet","icon":"🍽","title":"饮食","text":"保证每日奶量400-500ml，三餐两点心，食物切碎便于咀嚼。注意补充含铁食物如红肉、蛋黄。"})
        advice.append({"type":"exercise","icon":"🏃","title":"运动","text":"每日户外活动至少1小时，鼓励跑跳、攀爬等大运动，同时练习串珠、翻书等精细动作。"})
        advice.append({"type":"skill","icon":"📚","title":"技巧学习","text":"多读绘本、听儿歌，鼓励模仿说话和简单短语。认识常见物品名称，培养因果关系理解。"})
        advice.append({"type":"life","icon":"🌟","title":"生活技能","text":"学习自己用勺子吃饭、协助收玩具。开始如厕训练，培养洗手等卫生习惯。"})
    elif age_months < 36:
        advice.append({"type":"diet","icon":"🍽","title":"饮食","text":"每日奶量维持350-400ml，三餐规律，食物种类多样化。注意蛋白质（鱼、肉、蛋）和新鲜蔬果摄入，控制零食和含糖饮料。"})
        advice.append({"type":"exercise","icon":"🏃","title":"运动","text":"每日户外活动1-2小时，鼓励跑、跳、踢球、骑滑板车等。练习上下楼梯、单脚站立等平衡运动。"})
        advice.append({"type":"skill","icon":"📚","title":"技巧学习","text":"多进行亲子阅读，鼓励复述简单故事。练习颜色、形状分类，数数1-10。涂鸦画画锻炼手眼协调。"})
        advice.append({"type":"life","icon":"🌟","title":"生活技能","text":"练习自己穿脱简单衣物、洗手擦嘴。学习自己上厕所。鼓励帮忙做简单家务如收碗筷。培养固定作息时间。"})
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

    # 就医记录逆序
    medical_display = sorted(data.get("medical_records", []), key=lambda x: x["date"], reverse=True)

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

    # 图表默认区间：最新记录前1年后3月
    chart_min_month = int(max(0, latest_age_months - 12))
    chart_max_month = int(latest_age_months + 3)

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
            if days_left > 0:
                if days_left <= 30:
                    countdown = f"{days_left}天后"
                elif days_left <= 90:
                    countdown = f"约{days_left//7}周后"
                else:
                    countdown = f"约{round(days_left/30)}个月后"
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
        vaccine_records=data.get("vaccine_records", []),
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
  .header .avatar{width:40px;height:40px;border-radius:50%;background:rgba(255,255,255,.2);display:flex;align-items:center;justify-content:center;font-size:22px;flex-shrink:0;}
  .header .title-wrap{flex:1;min-width:0;}
  .header .title{font-size:16px;font-weight:600;}
  .header .subtitle{font-size:11px;opacity:.85;margin-top:2px;}
  .header .report-btn{background:rgba(255,255,255,.2);border:none;color:#fff;padding:8px 14px;border-radius:20px;font-size:13px;cursor:pointer;white-space:nowrap;flex-shrink:0;}
  .header .report-btn:active{background:rgba(255,255,255,.35);}
  .messages{flex:1;overflow-y:auto;-webkit-overflow-scrolling:touch;padding:16px 12px;display:flex;flex-direction:column;gap:12px;}
  .msg{max-width:80%;padding:10px 14px;border-radius:16px;font-size:15px;line-height:1.6;word-break:break-word;white-space:pre-wrap;animation:fadeIn .3s ease;}
  .msg.user{align-self:flex-end;background:#667eea;color:#fff;border-bottom-right-radius:4px;}
  .msg.bot{align-self:flex-start;background:#fff;color:#333;border-bottom-left-radius:4px;box-shadow:0 1px 3px rgba(0,0,0,.08);}
  .msg.bot a{color:#667eea;text-decoration:underline;}
  .typing{align-self:flex-start;background:#fff;color:#999;padding:12px 16px;border-radius:16px;border-bottom-left-radius:4px;box-shadow:0 1px 3px rgba(0,0,0,.08);}
  .typing span{display:inline-block;width:6px;height:6px;border-radius:50%;background:#aaa;margin:0 1px;animation:blink 1.4s infinite both;}
  .typing span:nth-child(2){animation-delay:.2s;}
  .typing span:nth-child(3){animation-delay:.4s;}
  .quick-tags{display:flex;flex-wrap:wrap;gap:8px;padding:8px 12px;flex-shrink:0;background:#f0f0f0;}
  .quick-tags .tag{background:#fff;border:1px solid #ddd;border-radius:18px;padding:6px 14px;font-size:13px;color:#555;cursor:pointer;white-space:nowrap;}
  .quick-tags .tag:active{background:#667eea;color:#fff;border-color:#667eea;}
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
  <div class="avatar">🐟</div>
  <div class="title-wrap">
    <div class="title">阿鲤成长助手</div>
    <div class="subtitle">记录成长 · 科学育儿</div>
  </div>
  <button class="report-btn" onclick="window.open('/report','_blank')">📊 报告</button>
</div>
<div class="messages" id="messages">
  <div class="msg bot">👋 你好！我是阿鲤的成长助手，可以帮你：
    \n1. 记录身高体重，如发"87cm 12.5kg"
    \n2. 记录就医/疫苗信息
    \n3. 在线咨询育儿问题
    \n4. 点击右上角"报告"查看成长曲线
    \n\n有什么我可以帮你的？</div>
</div>
<div class="quick-tags" id="quickTags">
  <span class="tag" onclick="sendQuick('帮助')">帮助</span>
  <span class="tag" onclick="sendQuick('体检')">下次体检</span>
  <span class="tag" onclick="sendQuick('疫苗')">疫苗</span>
  <span class="tag" onclick="sendQuick('数据')">数据</span>
</div>
<div class="input-bar">
  <input type="text" id="input" placeholder="输入消息..." onkeydown="if(event.key==='Enter')sendMsg()" maxlength="500">
  <button onclick="sendMsg()" id="sendBtn">↑</button>
</div>
<script>
let sending=false;
function scrollBottom(){const m=document.getElementById('messages');m.scrollTop=m.scrollHeight;}
function appendMsg(text,isUser){const d=document.createElement('div');d.className='msg '+(isUser?'user':'bot');d.textContent=text;document.getElementById('messages').appendChild(d);scrollBottom();}
function appendTyping(){const d=document.createElement('div');d.className='typing';d.id='typing';d.innerHTML='<span></span><span></span><span></span>';document.getElementById('messages').appendChild(d);scrollBottom();}
function removeTyping(){const t=document.getElementById('typing');if(t)t.remove();}
function sendQuick(text){document.getElementById('input').value=text;sendMsg();}
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
    const r=await fetch('/web/api',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:text})});
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
