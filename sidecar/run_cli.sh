#!/bin/bash

# 定义数组存储组装好的命令行参数
ARGS=()
USER_INPUT=""

# 逐行读取 .env 文件（处理可能没有任何换行符的最后一行）
while IFS='=' read -r key value || [ -n "$key" ]; do
    # 跳过空行和以 # 开头的注释
    if [[ -z "$key" || "$key" == \#* ]]; then
        continue
    fi
    
    # 移除首尾可能存在的双引号或单引号
    value="${value%\"}"
    value="${value#\"}"
    value="${value%\'}"
    value="${value#\'}"
    
    # 区分位置参数和长参数
    if [[ "$key" == "user_input" ]]; then
        USER_INPUT="$value"
    else
        ARGS+=("--$key" "$value")
    fi
done < .env

echo "[*] 执行命令: uv run python -m nsbot_sidecar.cli run \"$USER_INPUT\" ${ARGS[*]}"
echo "------------------------------------------------------------"

uv run python -m nsbot_sidecar.cli run "$USER_INPUT" "${ARGS[@]}"
