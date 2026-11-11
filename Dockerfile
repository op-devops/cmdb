FROM python:3.12-slim

# 国内网络默认走清华源;海外构建可 --build-arg PIP_INDEX_URL=https://pypi.org/simple
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

WORKDIR /app
ENV PYTHONPATH=/app:/app/gen \
    PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -i ${PIP_INDEX_URL} -r requirements.txt

COPY protos/ protos/
# 依赖别的服务的契约:vendor/ 在版本库里,构建期不需要访问 contracts 仓库
COPY vendor/ vendor/
COPY libs/ libs/
COPY services/ services/
COPY gateway/ gateway/
COPY scripts/ scripts/
COPY nacos-configs/ nacos-configs/

# 构建期用 grpcio-tools 生成 stub:与镜像内 protobuf 运行时同源,离线且版本必配
RUN mkdir -p gen && python -m grpc_tools.protoc -I protos -I vendor \
      --python_out=gen --pyi_out=gen --grpc_python_out=gen \
      $(find protos vendor -name '*.proto')

# 默认起内部服务;compose 里按服务覆盖 command
CMD ["python", "-m", "services.cmdb.server"]
