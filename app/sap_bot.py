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
