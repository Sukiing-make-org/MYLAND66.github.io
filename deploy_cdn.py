import subprocess
import os
import sys
import time

# 分组配置，和原矩阵保持一致
WORKER_GROUPS = [
    {"min": 1, "max": 399, "worker_name": "cdn-worker-1"},
    {"min": 400, "max": 749, "worker_name": "cdn-worker-2"},
    {"min": 750, "max": 999, "worker_name": "cdn-worker-3"},
    {"min": 1000, "max": 1999, "worker_name": "cdn-worker-4"},
    {"min": 2000, "max": 5999, "worker_name": "cdn-worker-5"},
    {"min": 6000, "max": 6399, "worker_name": "cdn-worker-6"},
    {"min": 6400, "max": 99999, "worker_name": "cdn-worker-7"},
]

# 最大重试次数
MAX_RETRY = 2
# 基础目录
BASE_BUILD_DIR = "./tmp_build"
SOURCE_DIR = "./pic/data"

def run_cmd(cmd: list) -> tuple[int, str]:
    """执行shell命令，返回退出码+输出内容"""
    print(f"执行命令: {' '.join(cmd)}")
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    output, _ = proc.communicate()
    print(f"命令输出:\n{output}")
    return proc.returncode, output

def build_worker(group: dict):
    worker_name = group["worker_name"]
    min_id = group["min"]
    max_id = group["max"]
    build_dir = os.path.join(BASE_BUILD_DIR, worker_name)
    public_dir = os.path.join(build_dir, "public")
    src_dir = os.path.join(build_dir, "src")
    pic_target = os.path.join(public_dir, "pic", "data")

    # 清理旧打包目录
    if os.path.exists(build_dir):
        run_cmd(["rm", "-rf", build_dir])
    os.makedirs(pic_target, exist_ok=True)
    os.makedirs(src_dir, exist_ok=True)

    # 生成Worker入口JS
    js_content = """export default {
  async fetch(request, env) {
    try {
      return await env.ASSETS.fetch(request);
    } catch {
      return new Response("File Not Found", { status: 404 });
    }
  }
}
"""
    with open(os.path.join(src_dir, "index.js"), "w", encoding="utf-8") as f:
        f.write(js_content)

    # 筛选区间内的数字ID文件夹并复制
    has_asset = False
    if os.path.exists(SOURCE_DIR):
        for entry in os.listdir(SOURCE_DIR):
            entry_path = os.path.join(SOURCE_DIR, entry)
            if os.path.isdir(entry_path) and entry.isdigit():
                dir_id = int(entry)
                if min_id <= dir_id <= max_id:
                    run_cmd(["cp", "-r", entry_path, pic_target])
                    has_asset = True

    if not has_asset:
        print(f"【{worker_name}】当前分组无资源，跳过部署")
        return True

    # 组装wrangler部署命令（剔除无效timeout参数，严格遵循官方参数）
    deploy_cmd = [
        "wrangler", "deploy",
        os.path.join(src_dir, "index.js"),
        "--name", worker_name,
        "--assets", public_dir,
        "--compatibility-date", "2026-07-01"
    ]

    # 重试部署逻辑
    ret_code = 1
    for retry in range(MAX_RETRY + 1):
        ret_code, _ = run_cmd(deploy_cmd)
        if ret_code == 0:
            print(f"【{worker_name}】部署成功")
            break
        if retry < MAX_RETRY:
            wait_sec = 5 * (retry + 1)
            print(f"【{worker_name}】部署失败，{wait_sec}秒后重试，剩余重试次数：{MAX_RETRY - retry}")
            time.sleep(wait_sec)

    return ret_code == 0

def main():
    all_success = True
    for group in WORKER_GROUPS:
        if not build_worker(group):
            all_success = False
    sys.exit(0 if all_success else 1)

if __name__ == "__main__":
    main()
