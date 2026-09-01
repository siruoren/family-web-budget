"""启动入口: python3 run.py"""
from app import create_app

app = create_app("dev")

if __name__ == "__main__":
    # use_reloader=False 避免子进程导致 Jinja 过滤器丢失
    app.run(host="127.0.0.1", port=5050, debug=True, use_reloader=False)
