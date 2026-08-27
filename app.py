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
from flask import Flask, request, abort, render_template_string, send_file
from zhipuai import ZhipuAI

app = Flask(__name__)

# ============ 配置 ============
ZHIPU_API_KEY = os.environ.get("ZHIPU_API_KEY", "")
WX_TOKEN = os.environ.get("WX_TOKEN", "ali-growth-2024")
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
  * { margin:0; padding:0; box-sizing:border-box; }
  body { font-family:-apple-system,"Microsoft YaHei",sans-serif; background:#f0f2f5; color:#333; padding-bottom:40px; }
  .header { background:linear-gradient(135deg,#667eea 0%,#764ba2 100%); color:white; padding:30px 20px; text-align:center; }
  .header h1 { font-size:28px; margin-bottom:8px; }
  .header .info { font-size:14px; opacity:0.9; }
  .container { max-width:1000px; margin:20px auto; padding:0 16px; }
  .card { background:white; border-radius:12px; padding:24px; margin-bottom:20px; box-shadow:0 2px 8px rgba(0,0,0,0.08); }
  .card h2 { font-size:20px; margin-bottom:16px; color:#333; border-left:4px solid #667eea; padding-left:12px; }
  .stats-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(200px,1fr)); gap:16px; }
  .stat-box { background:linear-gradient(135deg,#f5f7fa 0%,#c3cfe2 100%); border-radius:10px; padding:20px; text-align:center; }
  .stat-box .label { font-size:13px; color:#666; margin-bottom:6px; }
  .stat-box .value { font-size:24px; font-weight:bold; }
  .stat-box .unit { font-size:14px; color:#888; }
  .chart-container { position:relative; height:350px; margin:10px 0; }
  table { width:100%; border-collapse:collapse; margin-top:12px; }
  th,td { padding:10px 14px; text-align:left; border-bottom:1px solid #eee; font-size:14px; }
  th { background:#f8f9fa; font-weight:600; color:#555; }
  .badge { display:inline-block; padding:2px 10px; border-radius:12px; font-size:12px; }
  .badge-vaccine { background:#d1ecf1; color:#0c5460; }
  .badge-birth { background:#fff3cd; color:#856404; }
  .badge-checkup { background:#e2d9f3; color:#4a148c; }
  .section-empty { text-align:center; color:#999; padding:20px; }
  @media(max-width:600px){ .stats-grid{grid-template-columns:1fr 1fr;} .card{padding:16px;} }
</style>
</head>
<body>
<div class="header">
  <h1>阿鲤的成长记录</h1>
  <div class="info">出生：{{ birth_date }} | 男 | 出生47cm/1650g</div>
</div>
<div class="container">
  <div class="card">
    <h2>成长概览</h2>
    <div class="stats-grid">
      <div class="stat-box"><div class="label">当前月龄</div><div class="value">{{ age_months }}<span class="unit">月</span></div></div>
      <div class="stat-box"><div class="label">最新身高</div><div class="value">{{ latest_height }}<span class="unit">cm</span></div></div>
      <div class="stat-box"><div class="label">最新体重</div><div class="value">{{ latest_weight }}<span class="unit">kg</span></div></div>
      <div class="stat-box"><div class="label">记录总数</div><div class="value">{{ total_records }}<span class="unit">条</span></div></div>
    </div>
  </div>
  <div class="card"><h2>身高生长曲线</h2><div class="chart-container"><canvas id="heightChart"></canvas></div></div>
  <div class="card"><h2>体重生长曲线</h2><div class="chart-container"><canvas id="weightChart"></canvas></div></div>
  <div class="card"><h2>生长记录</h2>
    <table><thead><tr><th>日期</th><th>月龄</th><th>身高</th><th>体重</th><th>来源</th></tr></thead><tbody>
    {% for r in growth_records %}
    <tr><td>{{ r.date }}</td><td>{{ r.age_months }}</td><td>{{ r.height_cm }}cm</td><td>{{ r.weight_kg }}kg</td><td>{{ r.source }}</td></tr>
    {% endfor %}
    </tbody></table>
  </div>
  {% if medical_records %}
  <div class="card"><h2>就医记录</h2>
    <table><thead><tr><th>日期</th><th>类型</th><th>描述</th></tr></thead><tbody>
    {% for m in medical_records %}
    <tr><td>{{ m.date }}</td><td><span class="badge badge-{{ m.type }}">{{ m.type }}</span></td><td>{{ m.description }}</td></tr>
    {% endfor %}
    </tbody></table>
  </div>
  {% endif %}
  {% if vaccine_records %}
  <div class="card"><h2>疫苗记录</h2>
    <table><thead><tr><th>日期</th><th>疫苗名称</th><th>状态</th></tr></thead><tbody>
    {% for v in vaccine_records %}
    <tr><td>{{ v.date }}</td><td>{{ v.vaccine_name }}</td><td><span class="badge badge-vaccine">已接种</span></td></tr>
    {% endfor %}
    </tbody></table>
  </div>
  {% endif %}
</div>
<script>
const whoHeight={{ who_height|safe }};
const whoWeight={{ who_weight|safe }};
const actualHeights={{ actual_heights|safe }};
const actualWeights={{ actual_weights|safe }};
function makeChart(id,title,who,actual,unit){
  const ctx=document.getElementById(id).getContext('2d');
  const allLabels=[...new Set([...who.map(d=>d[0]),...actual.map(d=>d[0])])].sort((a,b)=>a-b);
  new Chart(ctx,{type:'line',data:{labels:allLabels,datasets:[
    {label:'WHO P97',data:allLabels.map(m=>{const d=who.find(x=>x[0]===m);return d?d[3]:null}),borderColor:'#fcc',borderDash:[5,5],fill:false,pointRadius:0,tension:0.3},
    {label:'WHO P50',data:allLabels.map(m=>{const d=who.find(x=>x[0]===m);return d?d[2]:null}),borderColor:'#6b6',borderDash:[3,3],fill:false,pointRadius:0,tension:0.3},
    {label:'WHO P3',data:allLabels.map(m=>{const d=who.find(x=>x[0]===m);return d?d[1]:null}),borderColor:'#fcc',borderDash:[5,5],fill:false,pointRadius:0,tension:0.3},
    {label:'实际测量',data:allLabels.map(m=>{const d=actual.find(x=>x[0]===m);return d?d[1]:null}),borderColor:'#667eea',backgroundColor:'rgba(102,126,234,0.15)',borderWidth:3,fill:false,pointRadius:6,pointBackgroundColor:'#667eea',tension:0.3}
  ]},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{position:'bottom'}},scales:{x:{title:{display:true,text:'月龄'}},y:{title:{display:true,text:unit},beginAtZero:false}}}});
}
makeChart('heightChart','身高',whoHeight,actualHeights,'cm');
makeChart('weightChart','体重',whoWeight,actualWeights,'kg');
</script>
</body></html>
"""

@app.route("/report")
def report():
    """生成HTML成长报告页面"""
    data = load_data()
    birth_date = data["child"]["birth_date"]
    birth = __import__("datetime").datetime.strptime(birth_date, "%Y-%m-%d")
    now = __import__("datetime").datetime.now()
    age_months = round((now - birth).days / 30.44, 1)
    
    growth = data.get("growth_records", [])
    
    # 计算月龄
    for r in growth:
        record_date = __import__("datetime").datetime.strptime(r["date"], "%Y-%m-%d")
        r["age_months"] = round((record_date - birth).days / 30.44, 1)
    
    latest_height = growth[-1]["height_cm"] if growth else "N/A"
    latest_weight = growth[-1]["weight_kg"] if growth else "N/A"
    
    # WHO数据
    who_height = [[0,46.1,49.9,53.7],[1,51.1,55.1,59.1],[3,57.6,61.7,65.7],[6,63.3,67.6,71.8],[9,67.5,71.9,76.4],[12,71.0,75.7,80.5],[18,76.9,81.7,86.7],[24,81.7,86.8,92.2],[30,85.6,90.9,96.5],[36,88.7,94.2,99.8]]
    who_weight = [[0,2.5,3.3,4.3],[1,3.4,4.5,5.7],[3,5.0,6.4,7.9],[6,6.4,7.9,9.6],[9,7.1,8.9,10.8],[12,7.7,9.6,11.7],[18,8.8,10.9,13.3],[24,9.7,12.2,14.8],[30,10.2,12.9,15.7],[36,10.8,13.7,16.9]]
    actual_heights = [[g["age_months"], g["height_cm"]] for g in growth]
    actual_weights = [[g["age_months"], g["weight_kg"]] for g in growth]
    
    return render_template_string(
        REPORT_HTML_TEMPLATE,
        birth_date=birth_date,
        age_months=age_months,
        latest_height=latest_height,
        latest_weight=latest_weight,
        total_records=len(growth),
        growth_records=growth,
        medical_records=data.get("medical_records", []),
        vaccine_records=data.get("vaccine_records", []),
        who_height=json.dumps(who_height),
        who_weight=json.dumps(who_weight),
        actual_heights=json.dumps(actual_heights),
        actual_weights=json.dumps(actual_weights)
    )


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
