"""
个人自用 TTS 后端服务

技术栈：FastAPI + edge-tts（微软 Edge 的在线朗读引擎，免费、不需要 API Key）
定位：只在本机/局域网跑，没有数据库、没有登录认证、没有 Docker。

启动方式（二选一）：
    python backend\main.py          （在项目根目录下执行）
    uvicorn main:app --host 0.0.0.0 --port 8000   （在 backend 目录下执行）
"""

import io
import os
import re
import socket
import sys
import threading
import time
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Optional

import edge_tts
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

app = FastAPI(
    title="个人 TTS 工具",
    description="局域网内使用的文字转语音服务，输入文字 → 选音色 → 下载 MP3",
)

# 目录约定（按功能归类）：
#   项目根/
#   ├─ backend/   ← 本文件
#   ├─ frontend/  ← index.html
#   ├─ assets/    ← 图标
#   └─ scripts/   ← 启动脚本
# 前端单页由 FastAPI 直接托管，页面和接口同一个端口，手机访问时不会撞上跨域限制。
BASE_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = BASE_DIR / "frontend"
ASSETS_DIR = BASE_DIR / "assets"


@app.get("/", include_in_schema=False)
async def index():
    """访问根路径时返回单页前端"""
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    """浏览器标签页图标，和桌面图标共用同一个文件"""
    return FileResponse(ASSETS_DIR / "logo-robot.ico", media_type="image/x-icon")


# ============================================================================
# 音色表
# ----------------------------------------------------------------------------
# 收录 edge-tts 当前全部 14 个中文音色，外加 15 个常用英文音色，
# 写死在代码里而不是每次去拉微软的完整音色列表：
# 微软那边有几百个音色，请求一次要好几秒，本地自用完全没必要。
# sample 是「试听例句」：前端点音色卡片的试听按钮时，就合成这一句。
# group 只用来给前端分组（普通话 / 方言 / 粤语 / 台湾 / 美式英语 / 英式英语 / 澳洲英语 / 印度英语）。
# ============================================================================
VOICES = [
    # ---- 普通话 ----
    {
        "id": "zh-CN-XiaoxiaoNeural",
        "name": "晓晓",
        "gender": "女",
        "tag": "温柔女声",
        "group": "普通话",
        "sample": "你好呀，我是晓晓，很高兴认识你。",
    },
    {
        "id": "zh-CN-XiaoyiNeural",
        "name": "晓伊",
        "gender": "女",
        "tag": "活泼女声",
        "group": "普通话",
        "sample": "哇，这个声音听起来好活泼呀！",
    },
    {
        "id": "zh-CN-YunxiNeural",
        "name": "云希",
        "gender": "男",
        "tag": "阳光男声",
        "group": "普通话",
        "sample": "今天天气真好，我们一起出去走走吧！",
    },
    {
        "id": "zh-CN-YunjianNeural",
        "name": "云健",
        "gender": "男",
        "tag": "沉稳男声",
        "group": "普通话",
        "sample": "这是一段沉稳有力的朗读示范。",
    },
    {
        "id": "zh-CN-YunxiaNeural",
        "name": "云夏",
        "gender": "男",
        "tag": "少年音",
        "group": "普通话",
        "sample": "哥哥姐姐好，我是云夏，今年十岁啦。",
    },
    {
        "id": "zh-CN-YunyangNeural",
        "name": "云扬",
        "gender": "男",
        "tag": "新闻播报",
        "group": "普通话",
        "sample": "各位听众朋友，大家好，欢迎收听今天的新闻。",
    },
    # ---- 方言 ----
    {
        "id": "zh-CN-liaoning-XiaobeiNeural",
        "name": "晓北",
        "gender": "女",
        "tag": "东北话",
        "group": "方言",
        "sample": "你好呀，我是晓北，咱东北人说话就这味儿。",
    },
    {
        "id": "zh-CN-shaanxi-XiaoniNeural",
        "name": "晓妮",
        "gender": "女",
        "tag": "陕西话",
        "group": "方言",
        "sample": "你好，我是晓妮，西安话听着嘹咋咧。",
    },
    # ---- 粤语 ----
    {
        "id": "zh-HK-HiuGaaiNeural",
        "name": "曉佳",
        "gender": "女",
        "tag": "粤语女声",
        "group": "粤语",
        "sample": "你好，我係曉佳，好高興認識你。",
    },
    {
        "id": "zh-HK-HiuMaanNeural",
        "name": "曉曼",
        "gender": "女",
        "tag": "粤语女声",
        "group": "粤语",
        "sample": "今日天氣好好，不如出去行下啦。",
    },
    {
        "id": "zh-HK-WanLungNeural",
        "name": "雲龍",
        "gender": "男",
        "tag": "粤语男声",
        "group": "粤语",
        "sample": "呢段係粵語男聲嘅示範。",
    },
    # ---- 台湾 ----
    {
        "id": "zh-TW-HsiaoChenNeural",
        "name": "曉臻",
        "gender": "女",
        "tag": "台湾女声",
        "group": "台湾",
        "sample": "您好，我是曉臻，很高興認識您。",
    },
    {
        "id": "zh-TW-HsiaoYuNeural",
        "name": "曉雨",
        "gender": "女",
        "tag": "台湾女声",
        "group": "台湾",
        "sample": "今天天氣真好，我們一起出去走走吧！",
    },
    {
        "id": "zh-TW-YunJheNeural",
        "name": "雲哲",
        "gender": "男",
        "tag": "台湾男声",
        "group": "台湾",
        "sample": "這是一段台灣腔男聲的示範。",
    },
    # ---- 美式英语 ----
    {
        "id": "en-US-AriaNeural",
        "name": "Aria",
        "gender": "女",
        "tag": "自然女声",
        "group": "美式英语",
        "sample": "Hi there, I'm Aria. It's nice to meet you.",
    },
    {
        "id": "en-US-JennyNeural",
        "name": "Jenny",
        "gender": "女",
        "tag": "亲和女声",
        "group": "美式英语",
        "sample": "Hello, this is Jenny. How can I help you today?",
    },
    {
        "id": "en-US-MichelleNeural",
        "name": "Michelle",
        "gender": "女",
        "tag": "成熟女声",
        "group": "美式英语",
        "sample": "Good morning. This is Michelle with the latest update.",
    },
    {
        "id": "en-US-AnaNeural",
        "name": "Ana",
        "gender": "女",
        "tag": "童声",
        "group": "美式英语",
        "sample": "Hi! I'm Ana. Let's read a story together!",
    },
    {
        "id": "en-US-GuyNeural",
        "name": "Guy",
        "gender": "男",
        "tag": "随性男声",
        "group": "美式英语",
        "sample": "Hey, what's up? This is Guy speaking.",
    },
    {
        "id": "en-US-ChristopherNeural",
        "name": "Christopher",
        "gender": "男",
        "tag": "沉稳男声",
        "group": "美式英语",
        "sample": "Please remain calm and follow the instructions.",
    },
    {
        "id": "en-US-EricNeural",
        "name": "Eric",
        "gender": "男",
        "tag": "清晰男声",
        "group": "美式英语",
        "sample": "This is a clear and steady voice test.",
    },
    {
        "id": "en-US-SteffanNeural",
        "name": "Steffan",
        "gender": "男",
        "tag": "叙事男声",
        "group": "美式英语",
        "sample": "Once upon a time, in a small town by the sea.",
    },
    # ---- 英式英语 ----
    {
        "id": "en-GB-SoniaNeural",
        "name": "Sonia",
        "gender": "女",
        "tag": "英式女声",
        "group": "英式英语",
        "sample": "Hello, I'm Sonia. Lovely to meet you.",
    },
    {
        "id": "en-GB-LibbyNeural",
        "name": "Libby",
        "gender": "女",
        "tag": "英式女声",
        "group": "英式英语",
        "sample": "Hi, I'm Libby. Let's get started, shall we?",
    },
    {
        "id": "en-GB-RyanNeural",
        "name": "Ryan",
        "gender": "男",
        "tag": "英式男声",
        "group": "英式英语",
        "sample": "Good afternoon. This is Ryan speaking.",
    },
    # ---- 澳洲英语 ----
    {
        "id": "en-AU-NatashaNeural",
        "name": "Natasha",
        "gender": "女",
        "tag": "澳式女声",
        "group": "澳洲英语",
        "sample": "G'day! I'm Natasha. Nice to meet you.",
    },
    {
        "id": "en-AU-WilliamMultilingualNeural",
        "name": "William",
        "gender": "男",
        "tag": "澳式男声",
        "group": "澳洲英语",
        "sample": "Hello, this is William from down under.",
    },
    # ---- 印度英语 ----
    {
        "id": "en-IN-NeerjaNeural",
        "name": "Neerja",
        "gender": "女",
        "tag": "印度女声",
        "group": "印度英语",
        "sample": "Hello, I'm Neerja. It's a pleasure to meet you.",
    },
    {
        "id": "en-IN-PrabhatNeural",
        "name": "Prabhat",
        "gender": "男",
        "tag": "印度男声",
        "group": "印度英语",
        "sample": "Good morning. This is Prabhat with the news.",
    },
]

# 白名单：合成时只接受上面这几个 id，防止用户传个乱七八糟的值给微软服务器
VOICE_IDS = {voice["id"] for voice in VOICES}

# 单次合成的文本上限（edge-tts 是逐段流式返回的，但太长会明显变慢甚至超时）
MAX_TEXT_LENGTH = 5000

# edge-tts 的默认参数。注意：它们不是数字，而是「带正负号的字符串」
DEFAULT_RATE = "+0%"
DEFAULT_PITCH = "+0Hz"
DEFAULT_VOLUME = "+0%"

# 格式校验用的正则
# rate   必须形如 "+10%" / "-5%" / "+0%"
# pitch  必须形如 "+10Hz" / "-5Hz" / "+0Hz"
# volume 必须形如 "-30%" / "+0%"
_RATE_PATTERN = re.compile(r"^[+-]\d+(\.\d+)?%$")
_PITCH_PATTERN = re.compile(r"^[+-]\d+(\.\d+)?Hz$")
_VOLUME_PATTERN = re.compile(r"^[+-]\d+(\.\d+)?%$")


# ============================================================================
# 请求体模型
# ============================================================================
class SynthesizeRequest(BaseModel):
    """POST /api/synthesize 的入参"""

    text: str = Field(..., description="要朗读的文字，1 ~ 5000 字")
    voice: str = Field(..., description="音色 id，必须是 /api/voices 返回的 id")
    # 下面三个允许不传；不传就走默认值
    rate: Optional[str] = Field(default=None, description='语速，如 "+10%"、"-5%"')
    pitch: Optional[str] = Field(default=None, description='音调，如 "+10Hz"、"-5Hz"')
    volume: Optional[str] = Field(default=None, description='音量，如 "-30%"、"+0%"')


# ============================================================================
# 参数格式化
# ----------------------------------------------------------------------------
# 为什么需要这一步：
# edge-tts 底层的 rate / pitch 不是数字，也不会帮你补单位，它要求的是
# 「带正负号的字符串」，例如 rate="+20%"、pitch="-5Hz"。
# 如果传 "20%"、"10"、20 这种写法，底层会直接抛异常，而且报错信息很难看懂。
# 所以这里统一做「补正负号 + 补单位校验 + 范围校验」，把错误提前挡在 400。
# ============================================================================
def normalize_rate(value: Optional[str]) -> str:
    """校验并规范化语速参数"""
    if not value:
        return DEFAULT_RATE

    rate = value.strip().replace(" ", "")
    # 容忍用户漏写正负号："10%" → "+10%"
    if rate and rate[0] not in "+-":
        rate = "+" + rate

    if not _RATE_PATTERN.match(rate):
        raise HTTPException(
            status_code=400,
            detail=f'语速格式不正确：{value}。正确写法如 "+10%" 或 "-5%"（必须带正负号和百分号）',
        )

    # 语速范围：-50%（0.5 倍）~ +100%（2 倍），和前端 0.5×~2× 的滑条对应
    rate_num = float(rate[:-1])
    if rate_num < -50 or rate_num > 100:
        raise HTTPException(status_code=400, detail="语速超出范围，请在 -50% ~ +100% 之间")

    return rate


def normalize_pitch(value: Optional[str]) -> str:
    """校验并规范化音调参数"""
    if not value:
        return DEFAULT_PITCH

    pitch = value.strip().replace(" ", "")
    # 容忍用户漏写正负号："10Hz" → "+10Hz"
    if pitch and pitch[0] not in "+-":
        pitch = "+" + pitch

    if not _PITCH_PATTERN.match(pitch):
        raise HTTPException(
            status_code=400,
            detail=f'音调格式不正确：{value}。正确写法如 "+10Hz" 或 "-5Hz"（必须带正负号和 Hz）',
        )

    if abs(float(pitch[:-2])) > 50:
        raise HTTPException(status_code=400, detail="音调超出范围，请在 -50Hz ~ +50Hz 之间")

    return pitch


def normalize_volume(value: Optional[str]) -> str:
    """校验并规范化音量参数"""
    if not value:
        return DEFAULT_VOLUME

    volume = value.strip().replace(" ", "")
    if volume and volume[0] not in "+-":
        volume = "+" + volume

    if not _VOLUME_PATTERN.match(volume):
        raise HTTPException(
            status_code=400,
            detail=f'音量格式不正确：{value}。正确写法如 "-30%" 或 "+0%"（必须带正负号和百分号）',
        )

    volume_num = float(volume[:-1])
    if volume_num < -100 or volume_num > 100:
        raise HTTPException(status_code=400, detail="音量超出范围，请在 -100% ~ +100% 之间")

    return volume


# ============================================================================
# 合成
# ============================================================================
async def synthesize_mp3(
    text: str, voice: str, rate: str, pitch: str, volume: str
) -> bytes:
    """
    调用 edge-tts 把文字合成成 MP3，返回二进制内容。

    用 stream() 而不是 save()：save() 只能写文件，而我们不想在电脑上留一堆
    临时 mp3。stream() 会一块一块地吐出数据，攒到内存里的 BytesIO 即可。
    每一块的格式是 {"type": "audio", "data": b"..."}，
    还有一种是 "WordBoundary"（逐词时间戳），本工具用不到，忽略。
    """
    communicate = edge_tts.Communicate(
        text=text, voice=voice, rate=rate, pitch=pitch, volume=volume
    )

    buffer = io.BytesIO()
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            buffer.write(chunk["data"])

    audio = buffer.getvalue()
    if not audio:
        raise RuntimeError("edge-tts 没有返回任何音频数据")

    return audio


# ============================================================================
# 接口
# ============================================================================
@app.get("/api/voices")
async def list_voices():
    """音色列表：前端用它渲染音色卡片和试听例句"""
    return {"voices": VOICES}


@app.post("/api/synthesize")
async def synthesize(req: SynthesizeRequest):
    """
    语音合成接口，成功时直接返回 MP3 文件流。

    为什么用 POST 而不是 GET：GET 的参数挂在 URL 上，URL 有约 2KB 的长度限制，
    长一点的文字会被截断；POST 把文字放进请求体里，不受这个限制。
    """
    text = (req.text or "").strip()

    if not text:
        raise HTTPException(status_code=400, detail="文本不能为空")
    if len(text) > MAX_TEXT_LENGTH:
        raise HTTPException(
            status_code=400, detail=f"文本太长了，请控制在 {MAX_TEXT_LENGTH} 字以内"
        )
    if req.voice not in VOICE_IDS:
        raise HTTPException(status_code=400, detail=f"不支持的音色：{req.voice}")

    rate = normalize_rate(req.rate)
    pitch = normalize_pitch(req.pitch)
    volume = normalize_volume(req.volume)

    try:
        audio = await synthesize_mp3(text, req.voice, rate, pitch, volume)
    except HTTPException:
        raise
    except Exception as exc:
        # 常见原因：电脑断网、公司网络拦了微软域名、微软侧临时抽风
        raise HTTPException(
            status_code=502,
            detail="语音服务暂时不可用，请检查电脑网络后重试",
        ) from exc

    # 文件名带时间戳，避免手机上下载第二次时被浏览器改成 xxx(1).mp3
    filename = f"TTS_{datetime.now():%Y%m%d_%H%M%S}.mp3"

    return Response(
        content=audio,
        media_type="audio/mpeg",
        headers={
            # attachment 表示「当作附件下载」，浏览器就会触发下载框；
            # 如果写成 inline，浏览器会试着在页面里直接播放。
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


# ============================================================================
# 启动入口
# ============================================================================
def get_lan_ip() -> str:
    """
    获取本机在局域网里的 IP（手机访问要用的那个地址）。

    原理小技巧：用一个 UDP socket 去「假装」连一下外网地址。UDP 不会真的发包，
    但系统在 connect 时会顺手挑好出口网卡，于是 getsockname() 拿到的就是
    这块网卡上的局域网 IP —— 比手工去 ipconfig 里找准确得多。
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        # 没网的时候兜底，至少启动不会报错
        return "127.0.0.1"
    finally:
        sock.close()


# ============================================================================
# 页面心跳：没窗口了，靠它判断「网页还开着没有」
# ----------------------------------------------------------------------------
# 前端每 5 秒 POST 一次 /api/ping；关网页时用 sendBeacon 发一次 /api/bye。
# 收到 bye 后 4 秒内没有新的 ping，就认为用户真的关了页面，服务自己退出。
# 另外还有一条兜底：连续 120 秒没有任何 ping，也退出（防止浏览器崩溃没发 bye）。
# ============================================================================
LAST_PING = 0.0
CLOSE_REQUEST = 0.0


@app.post("/api/ping")
def ping():
    global LAST_PING
    LAST_PING = time.time()
    return {"ok": True}


@app.post("/api/bye")
def bye():
    global CLOSE_REQUEST
    CLOSE_REQUEST = time.time()
    return {"ok": True}


def watch_idle(server) -> None:
    """后台线程：页面关了就让服务体面退出。"""
    while not server.should_exit:
        time.sleep(2)
        now = time.time()
        if CLOSE_REQUEST and now - CLOSE_REQUEST > 4 and LAST_PING < CLOSE_REQUEST:
            print("页面已关闭，服务自动退出")
            server.should_exit = True
        elif LAST_PING and now - LAST_PING > 120:
            print("超过 2 分钟没有页面连接，服务自动退出")
            server.should_exit = True


if __name__ == "__main__":
    # pythonw.exe（无窗口启动）下 sys.stdout / sys.stderr 是 None，
    # 而 uvicorn 启动时要调用 sys.stdout.isatty()，会直接崩溃。
    # 这里补一个「黑 hole」兜底：能写、能 isatty，但什么也不显示。
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")

    lan_ip = get_lan_ip()

    print("=" * 62)
    print("  TTS 服务已启动")
    print(f"  电脑本机访问： http://127.0.0.1:8000")
    print(f"  手机访问：     http://{lan_ip}:8000")
    print("  手机必须连接和电脑同一个 Wi-Fi 才能打开上面的地址。")
    print("  如果手机打不开，先检查 Windows 防火墙是否放行了 8000 端口。")
    print("  关掉网页后服务会自动停止。")
    print("=" * 62)

    # ★ 为什么是 0.0.0.0 而不是 127.0.0.1：
    #   127.0.0.1 是「只听本机自己说话」——只有这台电脑能访问，手机会一直转圈超时；
    #   0.0.0.0  是「所有网卡都听」——本机 IP、局域网 IP 都能连进来，手机才进得来。
    #   类比：127.0.0.1 是只有屋主自己能开的后门，0.0.0.0 是正门后门全都开。
    # 端口已被占用 = 服务已经在跑（用户重复点了图标），直接开网页就行。
    # 注意：这里用「连一下」而不是「绑一下」判断——Windows 允许两个进程分别绑
    # 0.0.0.0:8000 和 127.0.0.1:8000，用 bind 判断会误以为端口空闲。
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.settimeout(0.4)
    already_running = probe.connect_ex(("127.0.0.1", 8000)) == 0
    probe.close()

    if already_running:
        print("服务已在运行，直接打开浏览器")
        webbrowser.open("http://127.0.0.1:8000")
        sys.exit(0)

    config = uvicorn.Config(app, host="0.0.0.0", port=8000, log_level="warning")
    server = uvicorn.Server(config)

    threading.Thread(target=watch_idle, args=(server,), daemon=True).start()

    # 用 pythonw.exe 启动时没有控制台窗口，顺手帮用户把浏览器打开
    if "pythonw" in Path(sys.executable).name.lower():
        threading.Timer(1.8, lambda: webbrowser.open("http://127.0.0.1:8000")).start()

    server.run()
