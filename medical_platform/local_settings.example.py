# 本机私有配置模板（可进仓库，不含任何真实密码 / Key）。
# 首次部署：复制本文件为同目录下的 local_settings.py，再填入本机 MySQL 密码与（可选）AI Key。
# local_settings.py 已被 .gitignore 排除，不会提交到仓库。
import os

# ===== 数据库 =====
# 仅需覆盖密码；host / port / 用户 / 库名在 settings.py 中已有默认值。
DATABASES = {
    'default': {
        'PASSWORD': '你的MySQL密码',
    }
}

# ===== 大模型配置（可选；不填 Key 时 AI 链路自动降级本地规则，离线可跑）=====
# 通用三项：AI_BASE_URL（chat/completions 完整地址）、AI_API_KEY、AI_MODEL。
# 切换平台只改这三项，业务代码不用动。详见 README「8.1 AI 大模型接入」。
os.environ.setdefault('AI_BASE_URL', 'https://api.siliconflow.cn/v1/chat/completions')
os.environ.setdefault('AI_API_KEY', '')  # 可选：填入硅基流动等平台的 API Key
os.environ.setdefault('AI_MODEL', 'Qwen/Qwen2.5-7B-Instruct')
