import os
import asyncio
from fastapi import FastAPI, Request, Form, WebSocket, BackgroundTasks, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Sap Kyma Proxy Controller")
templates = Jinja2Templates(directory="app/templates")

# ==========================================
# 环境变量加载 (Stateless 架构核心)
# ==========================================
PANEL_PASSWORD = os.getenv("PANEL_PASSWORD", "123456")  # 默认密码123456，建议在部署时覆盖
SAP_USER = os.getenv("SAP_USER", "未配置")
ARGO_DOMAIN = os.getenv("ARGO_DOMAIN", "未配置")
SUB_TOKEN = os.getenv("SUB_TOKEN", "kele666")

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
        self.active_connections.remove(websocket)

    async def broadcast(self, message: str):
        print(f"[LOG] {message}") # 同时也打印到 Docker 容器后台
        for connection in self.active_connections:
            try:
                await connection.send_text(message)
            except:
                pass

logger = LogEmitter()

# ==========================================
# 权限校验拦截器
# ==========================================
async def verify_auth(request: Request):
    auth_cookie = request.cookies.get("kyma_auth")
    if auth_cookie != PANEL_PASSWORD:
        return False
    return True

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
        response.set_cookie(key="kyma_auth", value=password, max_age=86400 * 30) # 保持登录30天
        return response
    return templates.TemplateResponse("login.html", {"request": request, "error": "密码错误，请重试。"})

@app.get("/panel", response_class=HTMLResponse)
async def panel(request: Request):
    if not await verify_auth(request):
        return RedirectResponse(url="/", status_code=302)
    
    # 传递脱敏后的环境变量给前端展示
    config_info = {
        "sap_user": SAP_USER,
        "argo_domain": ARGO_DOMAIN,
        "sub_token": SUB_TOKEN
    }
    return templates.TemplateResponse("index.html", {"request": request, "config": config_info})

# ==========================================
# 核心触发接口 & WebSocket
# ==========================================
@app.websocket("/ws/logs")
async def websocket_endpoint(websocket: WebSocket):
    await logger.connect(websocket)
    try:
        while True:
            await websocket.receive_text() # 保持连接活跃
    except:
        logger.disconnect(websocket)

# 这里是给自动化机器人的入口，将在下一步接入 Playwright 逻辑
async def run_deployment_task():
    await logger.broadcast("🚀 接收到部署指令，初始化后端流水线...")
    await asyncio.sleep(1)
    await logger.broadcast("🤖 正在拉起 Playwright 无头浏览器...")
    await asyncio.sleep(2)
    await logger.broadcast(f"🔑 准备登录 SAP 账号: {SAP_USER} ...")
    
    # TODO: 这里将调用 sap_bot.py 和 k8s_deployer.py 的真实逻辑
    # import app.sap_bot as bot
    # await bot.run_full_flow(logger)
    
    await asyncio.sleep(2)
    await logger.broadcast("⚠️ 注意：真实部署逻辑尚未接入，这只是一次连通性测试。")
    await logger.broadcast("✅ 任务队列结束。")

@app.post("/api/deploy")
async def trigger_deploy(request: Request, background_tasks: BackgroundTasks):
    if not await verify_auth(request):
        return {"status": "error", "msg": "Unauthorized"}
    
    # 将长耗时任务推入后台执行，立刻返回前端请求，避免 HTTP 超时！
    background_tasks.add_task(run_deployment_task)
    return {"status": "success", "msg": "Task started"}
