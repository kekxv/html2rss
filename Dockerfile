# 使用官方 uv 镜像获取二进制文件
FROM ghcr.io/astral-sh/uv:latest AS uv

# 使用标准 Python 镜像进行构建
FROM python:3.12-slim AS builder

# 从 uv 镜像拷贝二进制文件 (官方路径是在根目录 /uv)
COPY --from=uv /uv /uvx /usr/bin/

# 设置工作目录
WORKDIR /app

# 拷贝项目文件
COPY pyproject.toml uv.lock ./

# 安装依赖到虚拟环境
# --frozen: 锁定版本
# --no-dev: 不安装开发依赖
RUN uv sync --frozen --no-dev --no-install-project

# 运行阶段
FROM python:3.12-slim

WORKDIR /app

# 从构建阶段拷贝虚拟环境
COPY --from=builder /app/.venv /app/.venv

# 拷贝应用代码和静态资源
COPY main.py ./
COPY webroot ./webroot

# 确保使用虚拟环境中的 Python
ENV PATH="/app/.venv/bin:$PATH"

# 默认端口
EXPOSE 3000

# 运行应用
ENTRYPOINT ["python", "main.py"]
CMD ["--port", "3000"]
