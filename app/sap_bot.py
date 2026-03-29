import os
import re
import asyncio
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
        # 启动 Chromium 无头浏览器（无头模式设为 True 即可在后台静默运行）
        browser = await p.chromium.launch(headless=True, args=['--no-sandbox', '--disable-dev-shm-usage'])
        context = await browser.new_context(viewport={'width': 1280, 'height': 800})
        page = await context.new_page()
        
        try:
            # ==========================================
            # 第一阶段：登录 SAP BTP Trial
            # ==========================================
            await logger.broadcast("🌐 正在访问 SAP BTP Trial 登录页...")
            await page.goto("https://cockpit.hanatrial.ondemand.com/trial/#/home/trial", timeout=60000)
            
            # 处理可能出现的 Cookie 弹窗
            try:
                cookie_btn = page.locator('button:has-text("Accept All")')
                if await cookie_btn.is_visible():
                    await cookie_btn.click()
            except:
                pass

            await logger.broadcast(f"🔑 正在输入账号: {SAP_USER}")
            # SAP 登录流通常分为两步：先输邮箱，再输密码
            await page.locator('input[name="j_username"]').fill(SAP_USER)
            await page.locator('button:has-text("Continue"), button[id="logOnFormSubmit"]').click()
            await page.wait_for_timeout(2000)
            
            await logger.broadcast("🔑 正在输入密码并验证...")
            await page.locator('input[name="j_password"]').fill(SAP_PASS)
            await page.locator('button:has-text("Log On")').click()
            
            # 等待进入后台页面
            await page.wait_for_selector('text="Subaccounts"', timeout=30000)
            await logger.broadcast("✅ 成功登录 SAP BTP 控制台！")

            # ==========================================
            # 第二阶段：进入 Subaccount 并判断 Kyma 状态
            # ==========================================
            await logger.broadcast("🔍 正在进入 Trial Subaccount 寻找 Kyma 实例...")
            # 点击进入 Subaccount 卡片 (匹配 trial 字符)
            await page.locator('a:has-text("trial")').first.click()
            await page.wait_for_selector('text="Kyma Environment"', timeout=30000)

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
                delete_btn = page.locator('button[aria-label="Delete Kyma Environment"]')
                if await delete_btn.is_visible():
                    await delete_btn.click()
                    # 二次确认弹窗
                    await page.locator('button:has-text("Delete")').filter(has_text="Delete").click()
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
                    status_text = await page.locator('.kyma-status-indicator').inner_text() # 这里的 selector 可能需根据实际情况微调
                    
                    if "Created" in status_text or "Enabled" in status_text:
                        await logger.broadcast(f"🎉 历时 {wait_minutes} 分钟，全新 Kyma 集群已成功变绿！")
                        break
                    await logger.broadcast(f"   ... 第 {wait_minutes} 分钟，当前状态: Processing，请保持耐心。")

            # ==========================================
            # 第四阶段：提取灵魂 (下载 Kubeconfig)
            # ==========================================
            await logger.broadcast("📥 正在向 SAP 申请 Kubernetes 集群管理凭证...")
            # 拦截下载事件
            async with page.expect_download() as download_info:
                await page.locator('a:has-text("Kubeconfig")').click()
            
            download = await download_info.value
            await download.save_as("kubeconfig.yaml")
            await logger.broadcast("✅ 凭证获取成功！自动化浏览器任务结束，即将移交部署引擎。")

            # 移交给 K8s 部署器
            await deployer.run_deploy(logger)

        except Exception as e:
            await logger.broadcast(f"❌ 自动化脚本执行异常: {str(e)}")
            # 这里可以考虑截图排错：await page.screenshot(path="error.png")
        finally:
            await browser.close()
