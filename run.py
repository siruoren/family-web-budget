"""启动入口: python3 run.py"""
import sys
from app import create_app

config_name = sys.argv[1] if len(sys.argv) > 1 else "dev"
app = create_app(config_name)

if __name__ == "__main__":
    host = app.config.get("SERVER_HOST", "127.0.0.1")
    port = app.config.get("SERVER_PORT", 5050)
    debug = app.config.get("DEBUG", True)
    # use_reloader=False 避免子进程导致 Jinja 过滤器丢失
    app.run(host=host, port=port, debug=debug, use_reloader=False)
