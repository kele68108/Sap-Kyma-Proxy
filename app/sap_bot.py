import os
import re
import asyncio
import shlex
from playwright.async_api import async_playwright
import app.k8s_deployer as deployer

# ==========================================
# 环境变量加载
# ==========================================
SAP_USER = os.getenv("SAP_USER")
SAP_PASS = os.getenv("SAP_PASS")
SAP_SUBACCOUNT = os.getenv("SAP_SUBACCOUNT", "SG-AZ") 

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
                '--disable-gpu',
                '--disable-blink-features=AutomationControlled',
                '--disable-web-security'
            ]
        )
        
        context = await browser.new_context(
            viewport={'width': 1920, 'height': 1080}, 
            locale='zh-CN',
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36',
            ignore_https_errors=True
        )
        context.set_default_timeout(45000)
        
        stealth_js = """
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            window.navigator.chrome = { runtime: {} };
        """
        await context.add_init_script(stealth_js)
        
        page = await context.new_page()
        
        try:
            # ==========================================
            # 第一阶段：登录 SAP (修复 SSO 重定向挂起问题)
            # ==========================================
            await logger.broadcast("🌐 正在访问 SAP BTP 主页...")
            
            try:
                # 关键修复：降低等待级别为 commit，并且即使抛出超时错误也拦截掉
                await page.goto("https://cockpit.hanatrial.ondemand.com/trial/#/home/trial", wait_until="commit", timeout=45000)
            except Exception:
                await logger.broadcast("⚠️ 页面路由可能因 SSO 延迟挂起，强制接管并探测登录入口...")
            
            await logger.broadcast("⏳ 等待 SAP 登录网关...")
            # 把找输入框的超时时间拉长，给 SSO 充足的渲染时间
            await page.wait_for_selector("input[name='j_username'], input[type='email']", timeout=60000)
            
            await logger.broadcast(f"🔑 正在填写账号: {SAP_USER}")
            if await page.locator("input[name='j_username']").count() > 0:
                await page.locator("input[name='j_username']").fill(SAP_USER)
            else:
                await page.locator("input[type='email']").fill(SAP_USER)
                
            submit_btn = page.locator("button#logOnFormSubmit, button[type='submit']")
            if await submit_btn.count() > 0:
                await submit_btn.click(force=True)
                await asyncio.sleep(2)
                 
            await logger.broadcast("🔑 正在填写密码并验证...")
            await page.locator("input[name='j_password'], input[type='password']").fill(SAP_PASS)
            await page.locator("button#logOnFormSubmit, button[type='submit']").click(force=True)
            
            # ==========================================
            # 🌟 第二阶段：全域暴力状态机 (无视前端遮罩)
            # ==========================================
            await logger.broadcast("📡 开启全域暴力状态机，无视前端遮罩执行扫描 (最高 90 秒)...")
            
            target_reached = False
            for _ in range(90): 
                frames_to_check = [page] + page.frames
                
                for frame in frames_to_check:
                    try:
                        wall_btn = frame.locator("text=/仍然继续|Continue anyway|仍要继续/i").first
                        if await wall_btn.count() > 0:
                            await logger.broadcast(f"⚠️ 发现拦截墙代码，正在执行原生穿甲点击...")
                            await wall_btn.evaluate("""node => {
                                if(node.click) node.click();
                                if(node.parentElement && node.parentElement.click) node.parentElement.click();
                                if(node.parentNode && node.parentNode.click) node.parentNode.click();
                            }""")
                            try: await wall_btn.click(force=True, timeout=1000)
                            except: pass
                            await asyncio.sleep(4) 
                    except: pass

                    try:
                        home_btn = frame.locator("text=/转到您的试用账户|Go To Your Trial Account|Enter Your Trial Account/i").first
                        if await home_btn.count() > 0:
                            await logger.broadcast("👉 发现欢迎页，正在点击『转到您的试用账户』...")
                            try: await home_btn.evaluate("node => node.click()")
                            except: pass
                            await home_btn.click(force=True, timeout=1000)
                            await asyncio.sleep(3)
                    except: pass

                    try:
                        ok_btn = frame.locator("text=/OK|Accept All/i").locator("visible=true").first
                        if await ok_btn.count() > 0:
                            checkbox = frame.locator("input[type='checkbox']").first
                            if await checkbox.count() > 0: 
                                await checkbox.check(force=True)
                            await ok_btn.click(force=True)
                            await asyncio.sleep(2) 
                    except: pass

                try:
                    target_locator = page.locator(f"text=/{SAP_SUBACCOUNT}|Kyma Environment|Kyma 环境/i").first
                    if await target_locator.count() > 0:
                        await logger.broadcast("✅ 导航成功，已到达目标账户层级！")
                        target_reached = True
                        break
                except: pass

                await asyncio.sleep(1) 

            if not target_reached:
                raise Exception("状态机导航超时，未能在 90 秒内到达账户浏览器！")

            # ==========================================
            # 🌟 第三阶段：进入 Kyma、击杀弹窗与无遮拦扫描
            # ==========================================
            await logger.broadcast(f"🖱️ 正在寻找并进入子账户 ({SAP_SUBACCOUNT})...")
            kyma_target = page.locator("text=/Kyma Environment|Kyma 环境/i").first
            if await kyma_target.count() == 0:
                subaccount_card = page.locator(f"text=/{SAP_SUBACCOUNT}/i").first
                if await subaccount_card.count() == 0:
                    subaccount_card = page.locator("text=/trial/i").nth(1)
                await subaccount_card.click(force=True)
            
            await logger.broadcast("🧹 页面跳转中，等待 6 秒渲染后启动弹窗清理器...")
            await asyncio.sleep(6) 
            
            await logger.broadcast("🔫 连按 Escape 键强制击碎 SAP 模态框...")
            for _ in range(4):
                await page.keyboard.press("Escape")
                await asyncio.sleep(0.5)
                
            close_selectors = [
                "button[aria-label='Close']", "button[title='Close']", 
                "button[aria-label='关闭']", "button[title='关闭']",
                ".sapMDialogCloseBtn"
            ]
            for sel in close_selectors:
                try:
                    btns = page.locator(sel)
                    for i in range(await btns.count()):
                        await btns.nth(i).click(force=True)
                except: pass
            
            await logger.broadcast("🔍 正在扫描 Kyma 环境配置区...")
            kyma_found = False
            for _ in range(45):
                if await page.locator("text=/Kyma Environment|Kyma 环境/i").count() > 0:
                    kyma_found = True
                    break
                await asyncio.sleep(1)
                
            if not kyma_found:
                raise Exception("未能在 45 秒内扫描到 Kyma 环境，请查看截图排查！")
                
            await logger.broadcast(f"✅ 成功突围！已进入 {SAP_SUBACCOUNT} 子账户的 Kyma 管理界面！")

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
                if await delete_btn.count() > 0:
                    await delete_btn.click(force=True)
                    await asyncio.sleep(1)
                    confirm_btn = page.locator('button:has-text("Delete"), button:has-text("删除")').last
                    if await confirm_btn.count() > 0:
                        await confirm_btn.click(force=True)
                    await logger.broadcast("🗑️ 已下发删除指令，正在等待集群彻底销毁 (预计 1-3 分钟)...")
                    
                    wait_del = 0
                    while True:
                        if await page.locator('button:has-text("Enable Kyma"), button:has-text("启用 Kyma")').count() > 0:
                            break
                        if wait_del > 18:
                            raise Exception("等待删除 Kyma 超时 (超过3分钟)，强制坠机！")
                        await asyncio.sleep(10)
                        wait_del += 1
                
                await logger.broadcast("✨ 旧实例已销毁/不存在，正在拉起全新 Kyma 集群...")
                await page.locator('button:has-text("Enable Kyma"), button:has-text("启用 Kyma")').first.click(force=True)
                
                await logger.broadcast("⏳ 等待配置弹窗渲染...")
                await asyncio.sleep(4) 
                
                try:
                    create_btn = page.locator('button:has-text("Create"), button:has-text("创建")').last
                    if await create_btn.count() > 0:
                        await create_btn.click(force=True)
                        await logger.broadcast("✅ 已在弹窗中成功点击『创建』！")
                    else:
                        await page.evaluate("""() => {
                            const btns = Array.from(document.querySelectorAll('button, bdi, span'));
                            const createBtn = btns.find(b => b.textContent.trim() === '创建' || b.textContent.trim() === 'Create');
                            if(createBtn) createBtn.click();
                        }""")
                        await logger.broadcast("✅ [兜底逻辑] 已在弹窗中强行执行创建指令！")
                except Exception as e:
                    await logger.broadcast(f"⚠️ 弹窗确认环节出现小插曲: {str(e)}")
                
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
            # 🌟 第五阶段：提取灵魂 (精准直链+页面直读)
            # ==========================================
            await logger.broadcast("📥 正在向 SAP 申请 Kubernetes 集群管理凭证...")
            await asyncio.sleep(4) 
            
            # 关键修复：精准匹配 kyma-env-broker 链接，彻底避开 Dashboard 链接的干扰！
            kube_locator = page.locator("a[href*='kyma-env-broker']").first
            kube_url = ""
            
            if await kube_locator.count() > 0:
                kube_url = await kube_locator.get_attribute("href")
            else:
                # 终极兜底：如果 UI 变了找不到 a 标签，直接从网页源码里用正则把链接硬挖出来
                await logger.broadcast("⚠️ 未找到标准 A 标签，启动正则引擎深度挖掘源码...")
                page_content = await page.content()
                match = re.search(r'(https://kyma-env-broker[^"\s<]+)', page_content)
                if match:
                    kube_url = match.group(1)
            
            if kube_url:
                await logger.broadcast(f"🔗 成功精准截获 Kubeconfig 直链: {kube_url[:50]}...")
                
                # 直接用当前已完全验证的页面跳转，完美继承所有 Token 和 Session，无视跨域！
                await page.goto(kube_url, wait_until="domcontentloaded", timeout=45000)
                await asyncio.sleep(3)
                
                # 提取页面上的纯文本内容 (浏览器会直接将 YAML 文本展示在页面上)
                yaml_content = await page.inner_text("body")
                
                # 增加一道保险：校验抓取到的内容是不是真正的 Kubeconfig 格式
                if "apiVersion: v1" in yaml_content and "clusters:" in yaml_content:
                    with open("kubeconfig.yaml", "w", encoding="utf-8") as f:
                        f.write(yaml_content)
                    await logger.broadcast("✅ 凭证文件物理抓取成功并已安全写入本地！")
                else:
                    await logger.broadcast(f"⚠️ 抓取内容格式异常，预览: {yaml_content[:200]}")
                    raise Exception("提取到的内容不是合法的 YAML 凭证，疑似遭遇二次跨域重定向拦截！")
            else:
                raise Exception("页面上彻底未找到 Kubeconfig 凭证提取链接！")

            await logger.broadcast("🚀 自动化浏览器任务圆满结束，即将移交 K8s 部署引擎！")
            await deployer.run_deploy(logger)

        except Exception as e:
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
                pass

            tg_token = os.getenv("TG_BOT_TOKEN")
            tg_chat_id = os.getenv("TG_CHAT_ID")
            
            if tg_token and tg_chat_id and os.path.exists(screenshot_path):
                await logger.broadcast("✈️ 正在将现场截图推送至 Telegram...")
                try:
                    caption = f"🚨 **SAP 自动化坠机警报**\n\n📍 **网址:** {current_url}\n🏷️ **标题:** {page_title}\n❌ **报错信息:**\n`{error_msg[:300]}...`"
                    caption_escaped = shlex.quote(caption) 
                    cmd = f'curl -s -X POST "https://api.telegram.org/bot{tg_token}/sendPhoto" -F chat_id="{tg_chat_id}" -F photo="@{screenshot_path}" -F parse_mode="Markdown" -F caption={caption_escaped}'
                    process = await asyncio.create_subprocess_shell(cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                    await process.communicate()
                except Exception as tg_e:
                    await logger.broadcast(f"❌ TG 推送异常: {str(tg_e)}")
                
        finally:
            await browser.close()
