"""一键启动独立 MySQL 联调环境，不连接业务数据。

在项目根目录运行：venv\Scripts\python.exe -B -m pharmacist.e2e_server
访问 http://127.0.0.1:8767/；Ctrl+C 停止并删除本次随机隔离库。
账号见 test_support.TEST_PHONES，演示口令见 test_support.TEST_PASSWORD。
"""
import os
import sys
import threading
from wsgiref.simple_server import make_server


def main():
    os.environ['DJANGO_SETTINGS_MODULE'] = 'pharmacist.test_settings'
    # 仅联调进程关闭外部 AI，实际使用项目已有的本地规则降级。
    os.environ['ARK_API_KEY'] = ''
    os.environ['ARK_MODEL'] = ''
    import django
    django.setup()
    from django.contrib.staticfiles.handlers import StaticFilesHandler
    from django.core.wsgi import get_wsgi_application
    from doctor import ai_client
    from .test_support import create_isolated_database, create_actors, drop_isolated_database
    ai_client.LOCAL_API_KEY = ''
    ai_client.LOCAL_MODEL = ''
    # 先绑定端口；端口占用时不创建任何数据库。
    with make_server('127.0.0.1', 8767, StaticFilesHandler(get_wsgi_application())) as server:
        config = create_isolated_database()
        try:
            create_actors()
            print('隔离联调服务：http://127.0.0.1:8767/（独立数据库，真实网页登录/业务写入）', flush=True)
            print('测试账号来源：pharmacist/test_support.py；Ctrl+C 停止并清理本次隔离库。', flush=True)
            def stop_from_terminal():
                for line in sys.stdin:
                    if line.strip() == 'stop':
                        server.shutdown()
                        break
            threading.Thread(target=stop_from_terminal, daemon=True).start()
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            drop_isolated_database(*config)
            print('本轮隔离库已删除；原业务库未修改。', flush=True)


if __name__ == '__main__':
    main()
