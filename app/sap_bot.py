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
        browser = await p.chromium.launch(
            headless=True,
            args=[
                '--no-sandbox',             
                '--disable-dev-shm-usage',  
                '--disable-gpu'
            ]
        )
        # 强制指定英文 locale，尽量规范 SAP 的输出，但下方代码已做双语兼容
        context = await browser.new_context(viewport={'width': 1920, 'height': 1080}, locale='en-US')
        context.set_default_timeout(45000)
        page = await context.new_page()
        
        try:
            # ==========================================
            # 第一阶段：登录 SAP
            # ==========================================
            await logger.broadcast("🌐 正在访问 SAP BTP 主页...")
            # 🌟 修复 1：不再强行跳转底层链接，老老实实从主页进
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
            # 🌟 修复 2：登录后的“主动雷达”，专门击碎拦截墙
            # ==========================================
            await logger.broadcast("📡 开启主动雷达，监控鉴权跳转与浏览器拦截墙...")
            for _ in range(30):
                try:
                    # 兼容中英文的“仍然继续”按钮
                    bypass_btn = page.locator('text="仍然继续", text="Continue anyway", text="仍要继续"').first
                    if await bypass_btn.is_visible():
                        await logger.broadcast("⚠️ 发现『浏览器不受支持』拦截墙！正在一拳击碎...")
                        await bypass_btn.click(force=True)
                        await asyncio.sleep(3)
                except: pass

                try:
                    # 如果看到“账户浏览器”或“Account Explorer”，说明安全过关！
                    if await page.locator('text="账户浏览器", text="Account Explorer"').first.is_visible():
                        break
                except: pass
                await asyncio.sleep(1)

            # ==========================================
            # 🌟 修复 3：模拟真人操作流转 (账户浏览器 -> 子账户)
            # ==========================================
            await logger.broadcast("🔍 正在等待进入全局账户浏览器 (Account Explorer)...")
            await page.wait_for_selector('text="账户浏览器", text="Account Explorer"', timeout=45000)
            await asyncio.sleep(3) # 给予卡片渲染时间

            await logger.broadcast("🖱️ 正在寻找并进入子账户 (SG-AZ)...")
            # 优先点击 SG-AZ，如果没有则点击第一个看着像子账户的链接
            subaccount_card = page.locator('text="SG-AZ"').first
            if not await subaccount_card.is_visible():
                subaccount_card = page.locator('text="trial"').nth(1)
            
            await subaccount_card.click(force=True)
            
            # ==========================================
            # 第三阶段：进入 Kyma 并判断状态
            # ==========================================
            await logger.broadcast("🔍 正在扫描 Kyma 环境配置区...")
            # 兼容中英文的 Kyma 模块标题
            await page.wait_for_selector('text="Kyma Environment", text="Kyma 环境"', timeout=45000)
            await logger.broadcast("✅ 成功突围！已进入 SG-AZ 子账户的 Kyma 管理界面！")

            page_text = await page.content()
            # 🌟 修复 4：兼容中英文的过期时间正则表达式
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
                
                # 兼容中英文删除按钮
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
                # 点击中英文启用按钮
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
