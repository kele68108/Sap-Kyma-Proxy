import os
import re
import subprocess
import asyncio

async def run_deploy(logger):
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

        # 核心替换逻辑
        yaml_text = yaml_text.replace("YOUR_KYMA_DOMAIN", new_kyma_domain)
        yaml_text = yaml_text.replace("YOUR_ARGO_DOMAIN", argo_domain)
        yaml_text = yaml_text.replace("YOUR_ARGO_TOKEN", argo_token)
        yaml_text = yaml_text.replace("YOUR_SUB_TOKEN", sub_token)
        yaml_text = yaml_text.replace("YOUR_TG_BOT_TOKEN", tg_bot_token)
        yaml_text = yaml_text.replace("YOUR_TG_CHAT_ID", tg_chat_id)

        # 生成待部署文件
        with open("deploy_ready.yaml", "w") as f:
            f.write(yaml_text)
            
    except Exception as e:
        await logger.broadcast(f"❌ 模板渲染失败: {str(e)}")
        return

    # 3. 通过 Kubectl 执行物理部署
    await logger.broadcast("🚀 正在向 Kubernetes 集群下发 All in One 矩阵配置...")
    try:
        # 使用 subprocess 调用容器内安装好的 kubectl
        cmd = "kubectl --kubeconfig=kubeconfig.yaml apply -f deploy_ready.yaml"
        
        # 因为 kubectl apply 可能会有少量耗时，使用 asyncio 包装一下防阻塞
        process = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()

        if process.returncode == 0:
            await logger.broadcast("✅ 集群配置下发成功！所有 Pod 已进入拉起状态。")
            await logger.broadcast("⏳ 请等待 2-3 分钟 AWS 分配负载均衡 IP，系统将自动把最终面板推送至你的 Telegram！")
            await logger.broadcast(f"🔗 预计面板访问地址: https://{new_kyma_domain}/{sub_token}/")
        else:
            await logger.broadcast(f"❌ Kubectl 部署失败:\n{stderr.decode()}")
            
    except Exception as e:
        await logger.broadcast(f"❌ 命令执行异常: {str(e)}")

    # 清理敏感文件 (可选)
    # os.remove("kubeconfig.yaml")
    # os.remove("deploy_ready.yaml")
