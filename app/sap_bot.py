import os
import re
import asyncio
import shlex
from playwright.async_api import async_playwright
import app.k8s_deployer as deployer

SAP_USER = os.getenv("SAP_USER")
SAP_PASS = os.getenv("SAP_PASS")

async def run_full_flow(logger):
    if not SAP_USER or not SAP_PASS:
        await logger.broadcast("❌ 错误：未配置 SAP_USER 或 SAP_PASS 环境变量！")
        return

    async with async_playwright() as p:
        # ==========================================
        # 🛡️ 伪装核心 1：注入反检测启动参数
        # ==========================================
        browser = await p.chromium.launch(
            headless=True,
            args=[
                '--no-sandbox',             
                '--disable-dev-shm-usage',  
                '--disable-gpu',
                '--disable-blink-features=AutomationControlled' # 抹除自动化特征
            ]
        )
        
        # ==========================================
        # 🛡️ 伪装核心 2：使用真实的 User-Agent 和环境
        # ==========================================
        context = await browser.new_context(
            viewport={'width': 1920, 'height': 1080}, 
            locale='zh-CN',
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        )
        context.set_default_timeout(45000)
        
        # ==========================================
        # 🛡️ 伪装核心 3：底层干掉 webdriver 标记
        # ==========================================
        await context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        
        page = await context.new_page()
        
        try:
            # ==========================================
            # 第一阶段：登录 SAP
            # ==========================================
            await logger.broadcast("🌐 正在访问 SAP BTP 主页...")
            await page.goto("https://cockpit.hanatrial.ondemand.com/trial/#/home/trial", wait_until="domcontentloaded")
            
            await logger.broadcast("⏳ 等待 SAP 登录网关...")
            await page.wait_for_selector("input[name='j_username'], input[type='email']", timeout=30000)
            
            await logger.broadcast(f"🔑 正在填写账号: {SAP_USER}")
            if await page.locator("input[name='j_username']").is_visible():
                await page.locator("input[name='j_username']").fill(SAP_USER)
            else:
                await page.locator("input[type='email']").fill(SAP_USER)
                
            submit_btn = page.locator("button#logOnFormSubmit, button[type='submit']")
            if await submit_btn.is_visible():
                await submit_btn.click()
                await asyncio.sleep(2)
                 
            await logger.broadcast("🔑 正在填写密码并验证...")
            await page.locator("input[name='j_password'], input[type='password']").fill(SAP_PASS)
            await page.locator("button#logOnFormSubmit, button[type='submit']").click()
            
            # ==========================================
            # 🌟 修复核心：开启 90 秒无敌状态机，见招拆招！
            # ==========================================
            await logger.broadcast("📡 开启自适应状态机，智能导航至子账户 (最高允许 90 秒)...")
            
            target_reached = False
            for _ in range(90): 
                # 状态 1：拦截墙 (强化匹配逻辑)
                try:
                    wall_btn = page.locator('button:has-text("仍然继续"), a:has-text("仍然继续"), text="Continue anyway", text="仍要继续"').first
                    if await wall_btn.is_visible():
                        await logger.broadcast("⚠️ 发现拦截墙，尝试击碎...")
                        await wall_btn.click(force=True)
                        await asyncio.sleep(3) # 给跳转留缓冲
                except: pass

                # 状态 2：欢迎页按钮
                try:
                    home_btn = page.locator('text="转到您的试用账户", text="Go To Your Trial Account", text="Enter Your Trial Account"').first
                    if await home_btn.is_visible():
                        await logger.broadcast("👉 发现欢迎页，正在点击『转到您的试用账户』...")
                        await home_btn.click(force=True)
                        await asyncio.sleep(3)
                except: pass

                # 状态 3：弹窗协议
                try:
                    ok_btn = page.locator("button:has-text('OK'), ui5-button:has-text('OK'), button:has-text('Accept All')").first
                    if await ok_btn.is_visible():
                        checkbox = page.locator("input[type='checkbox']").first
                        if await checkbox.is_visible(): 
                            await checkbox.check()
                        await ok_btn.click(force=True)
                        await asyncio.sleep(2) 
                except: pass

                # 状态 4：成功判定 (看到子账户或者直接看到 Kyma，就说明导航结束！)
                try:
                    if await page.locator('text="SG-AZ", text="Kyma Environment", text="Kyma 环境"').first.is_visible():
                        await logger.broadcast("✅ 导航成功，已到达目标账户层级！")
                        target_reached = True
                        break
                except: pass

                await asyncio.sleep(1) # 每秒扫描一次

            if not target_reached:
                raise Exception("状态机导航超时，未能在 90 秒内到达账户浏览器！")

            # ==========================================
            # 第三阶段：进入 Kyma 并判断状态
            # ==========================================
            await logger.broadcast("🖱️ 正在寻找并进入子账户 (SG-AZ)...")
            # 有可能之前跑过脚本，SAP 记住了路径，直接在 Kyma 页面了
            if not await page.locator('text="Kyma Environment", text="Kyma 环境"').first.is_visible():
                subaccount_card = page.locator('text="SG-AZ"').first
                if not await subaccount_card.is_visible():
                    subaccount_card = page.locator('text="trial"').nth(1)
                await subaccount_card.click(force=True)
            
            await logger.broadcast("🔍 正在扫描 Kyma 环境配置区...")
            await page.wait_for_selector('text="Kyma Environment", text="Kyma 环境"', timeout=45000)
            await logger.broadcast("✅ 成功突围！已进入 SG-AZ 子账户的 Kyma 管理界面！")

            page_text = await page.content()
            expire_match = re.search(r"(?:expires in|剩余)\s*(\d+)\s*(?:days|天)", page_text, re.IGNORECASE)
            
            needs_rebuild = True
            if expire_match:
                days_left = int(expire_match.group(1))
                await logger.broadcast(f"📊 当前 Kyma 集群状态：存活，剩余 {days_left} 天。")
                if days_left > 1:
                    await logger.broadcast("✅ 集群有效期充足，跳过重建流程，直接提取凭证！")
                    needs_rebuild = False
            else:
                await logger.broadcast("⚠️ 未检测到有效倒计时，可能尚未启用或已过期。")

            # ==========================================
            # 第四阶段：重置流水线 (删除 & 重建)
            # ==========================================
            if needs_rebuild:
                await logger.broadcast("💣 触发重置协议，准备销毁并重建 Kyma 实例...")
                
                delete_btn = page.locator('button[aria-label="Delete Kyma Environment"], button[title="删除 Kyma 环境"], button:has-text("Delete"), button:has-text("删除")').first
                if await delete_btn.is_visible():
                    await delete_btn.click()
                    await asyncio.sleep(1)
                    confirm_btn = page.locator('button:has-text("Delete"), button:has-text("删除")').last
                    if await confirm_btn.is_visible():
                        await confirm_btn.click()
                    await logger.broadcast("🗑️ 已下发删除指令，正在等待集群彻底销毁 (预计 1-3 分钟)...")
                    
                    wait_del = 0
                    while True:
                        if await page.locator('button:has-text("Enable Kyma"), button:has-text("启用 Kyma")').is_visible():
                            break
                        if wait_del > 18:
                            raise Exception("等待删除 Kyma 超时 (超过3分钟)，强制坠机！")
                        await asyncio.sleep(10)
                        wait_del += 1
                
                await logger.broadcast("✨ 旧实例已销毁，正在拉起全新 Kyma 集群...")
                await page.locator('button:has-text("Enable Kyma"), button:has-text("启用 Kyma")').first.click()
                
                wait_minutes = 0
                await logger.broadcast("⏳ 进入深度轮询模式，等待底层资源分配 (通常需要 10-15 分钟)...")
                while True:
                    await asyncio.sleep(60)
                    wait_minutes += 1
                    status_text = await page.locator('.kyma-status-indicator, body').inner_text() 
                    
                    if "Created" in status_text or "Enabled" in status_text or "已创建" in status_text or "已启用" in status_text:
                        await logger.broadcast(f"🎉 历时 {wait_minutes} 分钟，全新 Kyma 集群已成功变绿！")
                        break
                    
                    if wait_minutes > 25:
                        raise Exception(f"等待创建 Kyma 超时 (已等待 {wait_minutes} 分钟)，强制坠机！")
                        
                    await logger.broadcast(f"   ... 第 {wait_minutes} 分钟，正在分配集群，请保持耐心。")

            # ==========================================
            # 第五阶段：提取灵魂 (下载 Kubeconfig)
            # ==========================================
            await logger.broadcast("📥 正在向 SAP 申请 Kubernetes 集群管理凭证...")
            async with page.expect_download() as download_info:
                await page.locator('a:has-text("Kubeconfig")').first.click()
            
            download = await download_info.value
            await download.save_as("kubeconfig.yaml")
            await logger.broadcast("✅ 凭证获取成功！自动化浏览器任务结束，即将移交部署引擎。")

            await deployer.run_deploy(logger)

        except Exception as e:
            # ==========================================
            # 🚑 异常捕获：神级除错系统 (截图 + TG)
            # ==========================================
            current_url = page.url
            page_title = await page.title()
            error_msg = str(e)
            
            await logger.broadcast("================ 🚨 坠机诊断报告 ================")
            await logger.broadcast(f"❌ 错误详情: {error_msg}")
            await logger.broadcast(f"📍 崩溃网址: {current_url}")
            await logger.broadcast(f"🏷️ 页面标题: {page_title}")
            await logger.broadcast("==================================================")
            
            screenshot_path = "crash_screenshot.png"
            try:
                await page.screenshot(path=screenshot_path)
                await logger.broadcast("📸 已成功截取案发现场快照。")
            except:
                await logger.broadcast("⚠️ 截图失败，可能页面已销毁。")

            tg_token = os.getenv("TG_BOT_TOKEN")
            tg_chat_id = os.getenv("TG_CHAT_ID")
            
            if tg_token and tg_chat_id and os.path.exists(screenshot_path):
                await logger.broadcast("✈️ 正在将现场截图推送至 Telegram...")
                try:
                    caption = f"🚨 **SAP 自动化坠机警报**\n\n📍 **网址:** {current_url}\n🏷️ **标题:** {page_title}\n❌ **报错信息:**\n`{error_msg[:300]}...`"
                    caption_escaped = shlex.quote(caption) 
                    
                    cmd = f'curl -s -X POST "https://api.telegram.org/bot{tg_token}/sendPhoto" -F chat_id="{tg_chat_id}" -F photo="@{screenshot_path}" -F parse_mode="Markdown" -F caption={caption_escaped}'
                    
                    process = await asyncio.create_subprocess_shell(
                        cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                    )
                    await process.communicate()
                except Exception as tg_e:
                    await logger.broadcast(f"❌ TG 推送异常: {str(tg_e)}")
                
        finally:
            await browser.close()
