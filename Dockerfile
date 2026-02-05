# 使用官方 uv 镜像获取二进制文件
FROM ghcr.io/astral-sh/uv:latest AS uv

# 使用标准 Python 镜像进行构建
FROM python:3.12-slim AS builder

# 从 uv 镜像拷贝二进制文件
COPY --from=uv /usr/bin/uv /usr/bin/uv

# 设置工作目录
WORKDIR /app

# 启用字节码编译
ENV UV_COMPILE_BYTECODE=1

# 拷贝项目文件
COPY pyproject.toml uv.lock ./

# 安装依赖到虚拟环境（利用缓存）
# 使用 --frozen 确保版本锁定
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