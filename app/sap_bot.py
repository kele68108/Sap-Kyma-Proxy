import os
import re
import asyncio
import shlex
from playwright.async_api import async_playwright
import app.k8s_deployer as deployer

# 从环境变量获取账号密码
SAP_USER = os.getenv("SAP_USER")
SAP_PASS = os.getenv("SAP_PASS")

async def run_full_flow(logger):
    if not SAP_USER or not SAP_PASS:
        await logger.broadcast("❌ 错误：未配置 SAP_USER 或 SAP_PASS 环境变量！")
        return

    async with async_playwright() as p:
        # 启动 Chromium 无头浏览器，加入更多抗反爬和稳定性参数
        browser = await p.chromium.launch(
            headless=True, 
            args=[
                '--no-sandbox', 
                '--disable-dev-shm-usage',
                '--disable-gpu',
                '--disable-software-rasterizer'
            ]
        )
        context = await browser.new_context(
            viewport={'width': 1280, 'height': 800},
            locale='en-US' # 强制英文环境，防止按钮文本多语言变化
        )
        # 全局默认超时时间提升至 60 秒，适配缓慢的 PaaS 网络
        context.set_default_timeout(60000)
        page = await context.new_page()
        
        try:
            # ==========================================
            # 第一阶段：登录 SAP BTP Trial
            # ==========================================
            await logger.broadcast("🌐 正在访问 SAP BTP Trial 登录页...")
            await page.goto("https://cockpit.hanatrial.ondemand.com/trial/#/home/trial", wait_until="domcontentloaded", timeout=90000)
            
            # 静默处理可能出现的 Cookie 弹窗
            try:
                cookie_btn = page.locator('button:has-text("Accept All"), button:has-text("同意")').first
                if await cookie_btn.is_visible(timeout=5000):
                    await cookie_btn.click()
            except:
                pass

            await logger.broadcast(f"🔑 正在输入账号: {SAP_USER}")
            # 广谱定位器，应对 SAP 登录页面的动态变动
            await page.locator('input[name="j_username"], input[type="email"], input[type="text"]').first.fill(SAP_USER)
            await page.locator('button[type="submit"], button:has-text("Continue"), button[id="logOnFormSubmit"]').first.click()
            
            await logger.broadcast("🔑 正在输入密码并验证...")
            # 确保密码框真的弹出来了再操作
            await page.wait_for_selector('input[name="j_password"], input[type="password"]', timeout=30000)
            await page.locator('input[name="j_password"], input[type="password"]').first.fill(SAP_PASS)
            await page.locator('button[type="submit"], button:has-text("Log On")').first.click()
            
            await logger.broadcast("⏳ 正在等待 SAP 控制台加载 (PaaS 环境初次渲染极慢，请耐心等待 1-2 分钟)...")
            # 不再依赖特定文本，依赖 SAP UI5 核心元素的出现
            await page.wait_for_selector('a:has-text("trial"), div.sapUiBody', timeout=90000)
            await logger.broadcast("✅ 成功进入 SAP BTP 控制台主体！")

            # ==========================================
            # 第二阶段：进入 Subaccount 并判断 Kyma 状态
            # ==========================================
            await logger.broadcast("🔍 正在直接跳转至 Subaccount 页面寻找 Kyma 实例...")
            # 暴力 URL 跳转到目标层级
            await page.goto("https://cockpit.hanatrial.ondemand.com/trial/#/globalaccount/trial/subaccount/trial", wait_until="domcontentloaded")
            await page.wait_for_selector('text="Kyma Environment", text="kyma"', timeout=60000)

            # 抓取页面上的剩余天数文本
            page_text = await page.content()
            expire_match = re.search(r"expires in (\d+) days", page_text)
            
            needs_rebuild = True
            if expire_match:
                days_left = int(expire_match.group(1))
                await logger.broadcast(f"📊 当前 Kyma 集群状态：存活，剩余 {days_left} 天。")
                if days_left > 1:
                    await logger.broadcast("✅ 集群有效期充足，跳过重建流程，直接提取凭证！")
                    needs_rebuild = False
            else:
                await logger.broadcast("⚠️ 未检测到有效倒计时，可能已过期或未启用。")

            # ==========================================
            # 第三阶段：重置流水线 (删除 & 重建)
            # ==========================================
            if needs_rebuild:
                await logger.broadcast("💣 触发重置协议，准备销毁并重建 Kyma 实例...")
                
                # 点击删除按钮
                delete_btn = page.locator('button[aria-label="Delete Kyma Environment"], button:has-text("Delete")').first
                if await delete_btn.is_visible():
                    await delete_btn.click()
                    # 二次确认弹窗
                    confirm_btn = page.locator('button:has-text("Delete")').filter(has_text="Delete")
                    if await confirm_btn.is_visible():
                        await confirm_btn.click()
                    await logger.broadcast("🗑️ 已下发删除指令，正在等待集群彻底销毁 (预计 1-3 分钟)...")
                    
                    # 轮询等待删除完成 (Enable 按钮出现)
                    while True:
                        if await page.locator('button:has-text("Enable Kyma")').is_visible():
                            break
                        await asyncio.sleep(10)
                
                # 点击启用按钮
                await logger.broadcast("✨ 旧实例已销毁，正在拉起全新 Kyma 集群...")
                await page.locator('button:has-text("Enable Kyma")').click()
                
                # 轮询等待创建完成 (长耗时任务)
                wait_minutes = 0
                await logger.broadcast("⏳ 进入深度轮询模式，等待底层资源分配 (通常需要 10-15 分钟)...")
                while True:
                    await asyncio.sleep(60)
                    wait_minutes += 1
                    status_text = await page.locator('.kyma-status-indicator, body').inner_text() 
                    
                    if "Created" in status_text or "Enabled" in status_text:
                        await logger.broadcast(f"🎉 历时 {wait_minutes} 分钟，全新 Kyma 集群已成功变绿！")
                        break
                    await logger.broadcast(f"   ... 第 {wait_minutes} 分钟，当前状态: Processing，请保持耐心。")

            # ==========================================
            # 第四阶段：提取灵魂 (下载 Kubeconfig)
            # ==========================================
            await logger.broadcast("📥 正在向 SAP 申请 Kubernetes 集群管理凭证...")
            async with page.expect_download() as download_info:
                await page.locator('a:has-text("Kubeconfig")').first.click()
            
            download = await download_info.value
            await download.save_as("kubeconfig.yaml")
            await logger.broadcast("✅ 凭证获取成功！自动化浏览器任务结束，即将移交部署引擎。")

            # 移交给 K8s 部署器
            await deployer.run_deploy(logger)

        except Exception as e:
            # ==========================================
            # 🚑 异常捕获：神级除错系统 (截图 + TG 告警)
            # ==========================================
            current_url = page.url
            page_title = await page.title()
            error_msg = str(e)
            
            await logger.broadcast("================ 🚨 坠机诊断报告 ================")
            await logger.broadcast(f"❌ 错误详情: {error_msg}")
            await logger.broadcast(f"📍 崩溃网址: {current_url}")
            await logger.broadcast(f"🏷️ 页面标题: {page_title}")
            await logger.broadcast("==================================================")
            
            # 1. 截取当前出错页面的图片
            screenshot_path = "crash_screenshot.png"
            try:
                await page.screenshot(path=screenshot_path)
                await logger.broadcast("📸 已成功截取案发现场快照。")
            except Exception as screenshot_error:
                await logger.broadcast("⚠️ 截图失败，可能页面已销毁。")

            # 2. 推送至 Telegram
            tg_token = os.getenv("TG_BOT_TOKEN")
            tg_chat_id = os.getenv("TG_CHAT_ID")
            
            if tg_token and tg_chat_id and os.path.exists(screenshot_path):
                await logger.broadcast("✈️ 正在将现场截图与诊断报告发送至 Telegram...")
                try:
                    # 组装带格式的 Telegram 图片描述 (Caption)
                    caption = f"🚨 **SAP 自动化坠机警报**\n\n📍 **网址:** {current_url}\n🏷️ **标题:** {page_title}\n❌ **报错信息:**\n`{error_msg[:300]}...`"
                    caption_escaped = shlex.quote(caption) # 防止特殊字符破坏 shell 语法
                    
                    # 构造 curl 发送 multipart/form-data 的文件请求
                    cmd = f'curl -s -X POST "https://api.telegram.org/bot{tg_token}/sendPhoto" -F chat_id="{tg_chat_id}" -F photo="@{screenshot_path}" -F parse_mode="Markdown" -F caption={caption_escaped}'
                    
                    process = await asyncio.create_subprocess_shell(
                        cmd,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE
                    )
                    stdout, stderr = await process.communicate()
                    
                    if process.returncode == 0:
                        await logger.broadcast("✅ 案发现场截图已成功投递至你的 Telegram，请查收！")
                    else:
                        await logger.broadcast(f"❌ Telegram 图片推送失败:\n{stderr.decode()}")
                except Exception as tg_e:
                    await logger.broadcast(f"❌ Telegram 请求执行异常: {str(tg_e)}")
            else:
                await logger.broadcast("⚠️ 未检测到 TG_BOT_TOKEN 环境变量，已跳过截图推送。")
                
        finally:
            await browser.close()
