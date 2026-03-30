import os
import re
import subprocess
import asyncio
import uuid

# 注意：这里增加了 page 参数
async def run_deploy(logger, page=None):
    await logger.broadcast("⚙️ 部署引擎已接管，正在解析集群凭证...")
    
    if not os.path.exists("kubeconfig.yaml"):
        await logger.broadcast("❌ 致命错误：找不到 kubeconfig.yaml，凭证下载失败！")
        return

    # 1. 读取并提取新 Cluster ID
    with open("kubeconfig.yaml", "r") as f:
        config_content = f.read()

    match = re.search(r"server:\s*https://api\.(c-[a-z0-9]+)\.kyma", config_content)
    if not match:
        await logger.broadcast("❌ 错误：无法在凭证中定位 Cluster ID，请检查文件格式！")
        return

    cluster_id = match.group(1)
    new_kyma_domain = f"direct-node.{cluster_id}.kyma.ondemand.com"
    await logger.broadcast(f"🎯 提取成功！当前集群的真实域名为: {new_kyma_domain}")

    # 2. 读取 YAML 模板并进行变量渲染
    await logger.broadcast("📝 正在将环境变量注入最终 YAML 配置矩阵...")
    try:
        with open("app/templates/kyma_template.yaml", "r") as f:
            yaml_text = f.read()

        # 环境变量列表
        argo_domain = os.getenv("ARGO_DOMAIN", "YOUR_ARGO_DOMAIN")
        argo_token = os.getenv("ARGO_TOKEN", "YOUR_ARGO_TOKEN")
        sub_token = os.getenv("SUB_TOKEN", "kele666")
        tg_bot_token = os.getenv("TG_BOT_TOKEN", "")
        tg_chat_id = os.getenv("TG_CHAT_ID", "")
        
        # UUID 判定逻辑
        proxy_uuid = os.getenv("PROXY_UUID", "").strip()
        if not proxy_uuid:
            proxy_uuid = str(uuid.uuid4())
            await logger.broadcast(f"🛡️ 未提供固定 UUID，已随机生成强加密 UUID: {proxy_uuid}")
        else:
            await logger.broadcast(f"🛡️ 正在使用环境变量指定的固定 UUID: {proxy_uuid}")

        # 核心替换逻辑
        yaml_text = yaml_text.replace("YOUR_KYMA_DOMAIN", new_kyma_domain)
        yaml_text = yaml_text.replace("YOUR_ARGO_DOMAIN", argo_domain)
        yaml_text = yaml_text.replace("YOUR_ARGO_TOKEN", argo_token)
        yaml_text = yaml_text.replace("YOUR_SUB_TOKEN", sub_token)
        yaml_text = yaml_text.replace("YOUR_TG_BOT_TOKEN", tg_bot_token)
        yaml_text = yaml_text.replace("YOUR_TG_CHAT_ID", tg_chat_id)
        yaml_text = yaml_text.replace("YOUR_UUID", proxy_uuid)

        # 生成待部署文件
        with open("deploy_ready.yaml", "w") as f:
            f.write(yaml_text)
            
    except Exception as e:
        await logger.broadcast(f"❌ 模板渲染失败: {str(e)}")
        return

    # 3. 通过 Kubectl 执行物理部署
    await logger.broadcast("🚀 正在向 Kubernetes 集群下发 All in One 矩阵配置...")
    try:
        # 杀手锏 1：注入 BROWSER=echo，防止 xdg-open 报错并强制其将 URL 打印到终端
        custom_env = os.environ.copy()
        custom_env["BROWSER"] = "echo"

        cmd = "kubectl --kubeconfig=kubeconfig.yaml apply -f deploy_ready.yaml"
        
        process = await asyncio.create_subprocess_shell(
            cmd,
            env=custom_env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        output_log = []
        error_log = []

        # 杀手锏 2：开一个异步协程，实时读取 Kubectl 的控制台输出
        async def handle_stream(stream, is_stderr=False):
            while True:
                line = await stream.readline()
                if not line:
                    break
                line_str = line.decode('utf-8').strip()
                if is_stderr:
                    error_log.append(line_str)
                else:
                    output_log.append(line_str)

                # 杀手锏 3：一旦发现 OIDC 授权服务器启动，立马派 Playwright 过去"串门"
                if "http://localhost:" in line_str and page:
                    match = re.search(r'(http://localhost:\d+)', line_str)
                    if match:
                        login_url = match.group(1)
                        await logger.broadcast(f"🛂 拦截到 OIDC 唤醒请求: {login_url}")
                        await logger.broadcast("🕵️‍♂️ 正在调用底层 Playwright 携带全局 Cookie 强行注入授权...")
                        try:
                            # 带着 SAP 登录状态去访问，直接秒过验证！
                            await page.goto(login_url, timeout=30000)
                            await logger.broadcast("✅ OIDC 本地鉴权闭环完成！")
                        except Exception as e:
                            await logger.broadcast(f"⚠️ 鉴权访问小插曲 (通常可忽略): {str(e)}")

        # 并发读取 stdout 和 stderr
        await asyncio.gather(
            handle_stream(process.stdout, is_stderr=False),
            handle_stream(process.stderr, is_stderr=True)
        )

        await process.wait()

        if process.returncode == 0:
            await logger.broadcast("✅ 集群配置下发成功！所有 Pod 已进入拉起状态。")
            await logger.broadcast("⏳ 请等待 2-3 分钟 AWS 分配负载均衡 IP，系统将自动把最终面板推送至你的 Telegram！")
            await logger.broadcast(f"🔗 面板访问直连 URL 将在初始化完成后生效。")
        else:
            stderr_text = "\n".join(error_log)
            await logger.broadcast(f"❌ Kubectl 部署失败:\n{stderr_text}")
            
    except Exception as e:
        await logger.broadcast(f"❌ 命令执行异常: {str(e)}")
