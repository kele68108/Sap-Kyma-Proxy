import os
import asyncio
import uuid
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Form, WebSocket, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# ==========================================
# 环境变量加载 (Stateless 架构核心)
# ==========================================
PANEL_PASSWORD = os.getenv("PANEL_PASSWORD", "123456")
SAP_USER = os.getenv("SAP_USER", "未配置")
ARGO_DOMAIN = os.getenv("ARGO_DOMAIN", "未配置")
SUB_TOKEN = os.getenv("SUB_TOKEN", "kele666")
PROXY_UUID = os.getenv("PROXY_UUID", "") # 可以留空自动生成
CHECK_TIME = os.getenv("CHECK_TIME", "03:00") # 默认凌晨 3 点检查

# 解析检查时间
try:
    check_hour, check_minute = map(int, CHECK_TIME.split(":"))
except ValueError:
    check_hour, check_minute = 3, 0

# ==========================================
# WebSocket 日志广播频道
# ==========================================
class LogEmitter:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: str):
        print(f"[LOG] {message}")
        for connection in self.active_connections:
            try:
                await connection.send_text(message)
            except:
                pass

logger = LogEmitter()

# ==========================================
# 核心自动化流与定时器 (Lifespan)
# ==========================================
async def run_deployment_task():
    await logger.broadcast("🚀 接收到部署/检查指令，初始化后端流水线...")
    # 动态导入防止循环依赖
    import app.sap_bot as bot
    await bot.run_full_flow(logger)

# 定义后台调度器
scheduler = AsyncIOScheduler()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 容器启动时：挂载定时任务
    scheduler.add_job(run_deployment_task, 'cron', hour=check_hour, minute=check_minute)
    scheduler.start()
    print(f"⏰ 系统定时检查任务已开启，每天 {check_hour:02d}:{check_minute:02d} 准时巡检 Kyma 状态。")
    yield
    # 容器销毁时：清理任务
    scheduler.shutdown()

app = FastAPI(title="Sap Kyma Proxy Controller", lifespan=lifespan)
templates = Jinja2Templates(directory="app/templates")

# ==========================================
# 权限校验拦截器
# ==========================================
async def verify_auth(request: Request):
    return request.cookies.get("kyma_auth") == PANEL_PASSWORD

# ==========================================
# 路由映射
# ==========================================
@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    if await verify_auth(request):
        return RedirectResponse(url="/panel", status_code=302)
    return templates.TemplateResponse("login.html", {"request": request})

@app.post("/login")
async def login(request: Request, password: str = Form(...)):
    if password == PANEL_PASSWORD:
        response = RedirectResponse(url="/panel", status_code=302)
        response.set_cookie(key="kyma_auth", value=password, max_age=86400 * 30)
        return response
    return templates.TemplateResponse("login.html", {"request": request, "error": "密码错误，请重试。"})

@app.get("/panel", response_class=HTMLResponse)
async def panel(request: Request):
    if not await verify_auth(request):
        return RedirectResponse(url="/", status_code=302)
    
    config_info = {
        "sap_user": SAP_USER,
        "argo_domain": ARGO_DOMAIN,
        "sub_token": SUB_TOKEN,
        "proxy_uuid": PROXY_UUID if PROXY_UUID else "自动生成(推荐)",
        "check_time": f"{check_hour:02d}:{check_minute:02d} (Cron)"
    }
    return templates.TemplateResponse("index.html", {"request": request, "config": config_info})

@app.websocket("/ws/logs")
async def websocket_endpoint(websocket: WebSocket):
    await logger.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except:
        logger.disconnect(websocket)

@app.post("/api/deploy")
async def trigger_deploy(request: Request, background_tasks: BackgroundTasks):
    if not await verify_auth(request):
        return {"status": "error", "msg": "Unauthorized"}
    background_tasks.add_task(run_deployment_task)
    return {"status": "success"}
