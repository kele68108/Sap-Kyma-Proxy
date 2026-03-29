# 使用官方带有各种浏览器依赖的 Python 镜像作为底座
FROM mcr.microsoft.com/playwright/python:v1.48.0-jammy

# 设置工作目录
WORKDIR /app

# 下载并安装 kubectl，用于和 Kyma 集群交互部署 YAML
RUN curl -LO "https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl" \
    && chmod +x kubectl \
    && mv kubectl /usr/local/bin/

# 拷贝依赖列表并安装 Python 库
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 拷贝所有代码到容器内
COPY . .

# 暴露 FastAPI 面板端口
EXPOSE 8000

# 启动命令 (指定 app/main.py 中的 app 实例)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
