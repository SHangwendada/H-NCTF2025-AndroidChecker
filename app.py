import os
import threading
import random
import time
import uuid
from queue import Queue
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory, render_template, session, redirect, url_for
import uiautomator2 as u2
import logging
from androguard.misc import AnalyzeAPK
from loguru import logger

logger.disable("androguard")

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
_logger = logging.getLogger(__name__)
is_save_apk = True  # 是否保存APK文件
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'fallback_secret_key')  # 从环境变量获取密钥
UPLOAD_FOLDER = 'uploads'
SCREENSHOTS_FOLDER = 'screenshots'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(SCREENSHOTS_FOLDER, exist_ok=True)

# 安装任务队列和状态管理
install_queue = Queue()
current_task = None
lock = threading.Lock()
task_status = {}
task_history = []
device_status = "disconnected"

# 管理员凭据 (从环境变量获取)
ADMIN_USERNAME = os.environ.get('ADMIN_USERNAME', '6666')
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', '6666')

# 设备连接 (修改为你的设备ID或地址)
DEVICE_ID = "hecm8txgrwrs8h75"
d = None

# 目标包名和测试脚本配置
TARGET_PACKAGE = "com.example.flagquizgame"
INSTALL_PACKAGE = None  # 安装的包名会实时显示
FLAG = "flag{Hav3F4nnyCr@ckM320250607146}"  # 测试成功时返回的flag

class Task:
    def __init__(self, task_id, original_filename, stored_filename, apk_path, ip_address):
        self.task_id = task_id
        self.original_filename = original_filename  # 原始文件名
        self.stored_filename = stored_filename  # 存储的文件名（UUID）
        self.apk_path = apk_path
        self.ip_address = ip_address
        self.status = "QUEUED"
        self.message = ""
        self.progress = 0
        self.start_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.end_time = None
        self.screenshot = None
        self.screenshot_ready = False
        self.screenshot_countdown = 15

    def to_dict(self):
        return {
            "task_id": self.task_id,
            "original_filename": self.original_filename,
            "stored_filename": self.stored_filename,
            "ip_address": self.ip_address,
            "status": self.status,
            "message": self.message,
            "progress": self.progress,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "screenshot": self.screenshot
        }

    @property
    def filename(self):
        """兼容旧属性，返回原始文件名"""
        return self.original_filename

# 如果是pwn题为了防止选手上传与环境app一样包名的apk导致覆盖环境apk需要在安装前校验包名
def get_apk_package_name(apk_path):
    try:
        a, d, dx = AnalyzeAPK(apk_path)
        return a.get_package()
    except Exception as e:
        _logger.error(f"解析APK包名失败: {str(e)}")
        return None

# 尝试连接设备
def connect_device():
    global d, device_status
    try:
        if d is None:
            d = u2.connect(DEVICE_ID)
            device_status = "connected"
            _logger.info(f"设备已连接: {DEVICE_ID}")
        return True
    except Exception as e:
        _logger.error(f"设备连接失败: {str(e)}")
        device_status = "disconnected"
        d = None  # 重置设备连接
        return False


def run_test_script():
    """运行uiautomator2测试脚本"""
    global INSTALL_PACKAGE
    try:
        _logger.info("开始运行测试脚本...")

        # 启动目标应用
        # d.app_start(TARGET_PACKAGE)
        time.sleep(4)
        current_pkg = d.app_current().get('package')
        print(current_pkg)
        INSTALL_PACKAGE = current_pkg
        if current_pkg != TARGET_PACKAGE:
            return False, f"请不要随意上传！"

        # 开局广告检测
        if d(text="欢迎参加 H&NCTF2025").exists:
            time.sleep(1)
            _logger.info("测试未通过：未绕过开局广告")
            return False, "测试未通过：未绕过开局广告"

        for i in range(10):
            _logger.info(f"第 {i+1} 次测试题检查...")

            # 检查是否有5个按钮
            buttons = d(className="android.widget.Button")
            if len(buttons) != 5:
                return False, f"测试未通过：第{i+1}题按钮数量不为5，破解失败"

            # 测试点2
            # 检查所有按钮内容是否为“中国”
            all_china = all(btn.info.get('text') == "中国" for btn in buttons)
            if not all_china:
                return False, f"测试未通过：第{i+1}题按钮内容不全是“中国”，破解失败"

            # 随机点击一个按钮
            selected_button = random.choice(buttons)
            selected_button.click()
            time.sleep(1)

            # 检查是否出现“错啦”提示
            if d(textContains="错啦!...孩子!").exists:
                return False, f"测试未通过：第{i+1}题点击后出现“错啦”，破解失败"

            # 点击“下一题”按钮
            if d(text="下一题").exists and i < 9 :
                d(text="下一题").click()
                time.sleep(1)
            elif i == 9 and d(text="查看结果").exists:
                d(text="查看结果").click()
                time.sleep(1)
            else:
                return False, f"测试未通过：第{i + 1}题后未找到“下一题”按钮"
        # 第11次：检查是否为游戏结果页
        time.sleep(2)
        _logger.info("第11次检查是否进入通关界面...")
        buttons = d(className="android.widget.Button")
        if len(buttons) == 2 and d(textContains="正确题数: 10/10").exists  and d(textContains="正确率: 100.0%").exists:
            return True, "测试成功"

        return False, "测试未通过：未检测到通关页面"

    except Exception as e:
        if d:
            _logger.info("正在关闭应用")
            d.app_stop(TARGET_PACKAGE)
            d.app_uninstall(INSTALL_PACKAGE)
        return False, f"测试异常: {str(e)}"


def take_screenshot(filename):
    """拍摄设备截图并保存"""
    try:
        screenshot = d.screenshot()
        filepath = os.path.join(SCREENSHOTS_FOLDER, filename)
        screenshot.save(filepath)
        return True, filename
    except Exception as e:
        return False, f"截图失败: {str(e)}"


def worker():
    global current_task, device_status
    while True:
        task = install_queue.get()
        with lock:
            ip_count = 0
            for item in list(install_queue.queue):
                if item.ip_address == task.ip_address:
                    ip_count += 1
            if ip_count >= 2:  # 当前任务还在队列中，所以>=3
                task.status = "ERROR"
                task.message = f"该IP同时任务数已达上限(3个)"
                task.end_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                task_history.append(task)
                task_status[task.task_id] = task
                if os.path.exists(task.apk_path):
                    os.remove(task.apk_path)
                install_queue.task_done()
                continue
            current_task = task.task_id
            task.status = "INSTALLING"
            task.progress = 30
            task_status[task.task_id] = task

        try:
            _logger.info(f"开始安装: {task.filename}")

            if not connect_device():
                raise Exception("设备未连接")

            #测试点1
            #这里会消耗非常多的时间
            # package_name = get_apk_package_name(task.apk_path)
            # _logger.info(f"APK包名: {package_name}")

            # # 检查包名是否符合要求
            # if package_name != TARGET_PACKAGE:
            #     raise Exception(f"拒绝安装：包名 {package_name} 不符合要求 (应为 {TARGET_PACKAGE})")

            # 安装APK
            d.app_install(task.apk_path)
            task.progress = 50
            task.message = "安装成功，准备运行测试脚本"
            _logger.info("安装成功，开始运行测试脚本")

            # 运行测试脚本
            test_success, test_message = run_test_script()
            task.progress = 70

            if test_success:
                task.message = "测试成功！"
                # 返回flag
                task.status = "SUCCESS"
                _logger.info(f"测试成功！Flag: {FLAG}")
                task.message = f"测试成功！Flag: {FLAG}"
            else:
                task.status = "TEST_FAILED"
                task.message = f"测试失败: {test_message}"
                # 拍摄截图
                screenshot_name = f"screenshot_{task.task_id}.png"
                _, screenshot_result = take_screenshot(screenshot_name)
                task.screenshot = screenshot_name
                task.screenshot_ready = True

            task.progress = 100

        except Exception as e:
            task.status = "ERROR"
            task.message = f"任务失败: {str(e)}"
            task.progress = 100
            _logger.error(f"任务失败: {str(e)}")

        finally:
            try:
                if d:
                    _logger.info("正在关闭所有应用")
                    d.app_stop_all()
                    _logger.info(f"正在卸载应用: {INSTALL_PACKAGE}")
                    d.app_uninstall(INSTALL_PACKAGE)
            except Exception as e:
                _logger.warning(f"关闭应用时出错: {str(e)}")

            task.end_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with lock:
                task_history.append(task)
                if len(task_history) > 50:
                    task_history.pop(0)
                task_status[task.task_id] = task
                current_task = None
                if os.path.exists(task.apk_path):
                    if not is_save_apk:
                        os.remove(task.apk_path)

        install_queue.task_done()
        time.sleep(1)

@app.route('/')
def index():
    """返回前端页面"""
    return render_template('index.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    """管理员登录"""
    error = None

    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')

        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            session['logged_in'] = True
            session['username'] = username
            return redirect(url_for('admin'))
        else:
            error = '无效的用户名或密码'

    return render_template('login.html', error=error)


@app.route('/logout')
def logout():
    """管理员登出"""
    session.pop('logged_in', None)
    session.pop('username', None)
    return redirect(url_for('login'))


@app.route('/download-apk/<stored_filename>')
def download_apk(stored_filename):
    """返回上传的APK文件"""
    return send_from_directory(UPLOAD_FOLDER, stored_filename, as_attachment=True)

@app.route('/admin')
def admin():
    """管理员面板"""
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    # 获取当前任务
    current = None
    with lock:
        if current_task and current_task in task_status:
            current = task_status[current_task]

        # 获取队列中的任务
        queue_tasks = []
        for item in list(install_queue.queue):
            queue_tasks.append({
                'filename': item.filename,
                'task_id': item.task_id,
                'ip_address': item.ip_address  # 新增IP地址
            })

    # 获取设备状态
    connect_device()  # 刷新设备状态

    return render_template('admin.html',
                           current=current,
                           current_task=current_task,
                           queue=queue_tasks,
                           DEVICE_ID=DEVICE_ID,
                           device_status="已连接" if device_status == "connected" else "未连接",
                           task_history=task_history)


@app.route('/screenshots/<filename>')
def get_screenshot(filename):
    """返回截图文件"""
    return send_from_directory(SCREENSHOTS_FOLDER, filename)


@app.route('/status/device')
def device_status_check():
    """检查设备状态"""
    connect_device()  # 刷新设备状态
    return jsonify({
        "status": "connected" if device_status == "connected" else "disconnected"
    })


@app.route('/task-error/<task_id>', methods=['GET'])
def get_task_error(task_id):
    with lock:
        task = task_status.get(task_id)
        if not task:
            return jsonify({"error": "Task not found"}), 404

        if task.status != "ERROR":
            return jsonify({"error": "No error details for this task"}), 400

        return jsonify({
            "error": task.message
        })


@app.route('/api/dashboard')
def api_dashboard():
    if not session.get('logged_in'):
        return jsonify({"error": "Unauthorized"}), 401

    # 获取当前任务
    current = None
    with lock:
        if current_task and current_task in task_status:
            task_obj = task_status[current_task]
            current = task_obj.to_dict()
            # 添加当前任务的apk_url和screenshot_url
            current['apk_url'] = url_for('download_apk', stored_filename=task_obj.stored_filename, _external=False) if task_obj.stored_filename else None
            current['screenshot_url'] = url_for('get_screenshot', filename=task_obj.screenshot, _external=False) if task_obj.screenshot else None

        # 获取队列中的任务
        queue_tasks = []
        for item in list(install_queue.queue):
            queue_tasks.append({
                'original_filename': item.original_filename,
                'task_id': item.task_id,
                'ip_address': item.ip_address
            })

        # 获取设备状态
        connect_device()
        device_connected = (device_status == "connected")

        # 准备历史记录数据
        history_data = []
        for task in task_history[-50:][::-1]:  # 只取最近的50条，逆序排列
            apk_url = url_for('download_apk', stored_filename=task.stored_filename, _external=False) if task.stored_filename else None
            screenshot_url = url_for('get_screenshot', filename=task.screenshot, _external=False) if task.screenshot else None
            history_data.append({
                'task_id': task.task_id,
                'original_filename': task.original_filename,
                'ip_address': task.ip_address,
                'start_time': task.start_time,
                'end_time': task.end_time if task.end_time else '-',
                'status': task.status,
                'stored_filename': task.stored_filename,
                'screenshot': task.screenshot,
                'progress': task.progress,
                'message': task.message,
                'apk_url': apk_url,
                'screenshot_url': screenshot_url
            })

    return jsonify({
        "current": current,
        "queue": queue_tasks,
        "device_status": "已连接" if device_connected else "未连接",
        "history": history_data,
        "device_id": DEVICE_ID
    })

@app.route('/upload', methods=['POST'])
def upload_apk():
    """处理APK上传请求"""

    if 'file' not in request.files:
        return jsonify({"error": "No file part"}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No selected file"}), 400

    # 检查文件扩展名
    if not file.filename.lower().endswith('.apk'):
        return jsonify({"error": "Invalid file type"}), 400

    # 保存文件到临时位置
    original_filename = file.filename
    stored_filename = f"{str(uuid.uuid4())}.apk"  # 使用UUID作为存储文件名
    apk_path = os.path.join(UPLOAD_FOLDER, stored_filename)
    file.save(apk_path)

    # 检查文件大小 (15MB限制)
    file_size = os.path.getsize(apk_path)
    if file_size > 20 * 1024 * 1024:  # 15MB
        os.remove(apk_path)
        return jsonify({"error": "File too large (max 20MB)"}), 400

    # 创建安装任务
    client_ip = request.headers.get('X-Forwarded-For', request.remote_addr)

    # 在加入队列前检查同IP任务数（包括队列中的任务和当前运行的任务）
    with lock:
        ip_count = 0

        # 1. 检查当前正在运行的任务（如果有）
        if current_task and current_task in task_status:
            running_task = task_status[current_task]
            if running_task.ip_address == client_ip:
                ip_count += 1

        # 2. 检查队列中的任务
        for item in list(install_queue.queue):
            if item.ip_address == client_ip:
                ip_count += 1

        # 3. 如果当前任务加上队列任务已经达到3个
        if ip_count >= 3:
            if os.path.exists(apk_path):
                os.remove(apk_path)
            return jsonify({
                "error": f"同一个IP同时只能有3个任务（当前已有{ip_count}个）"
            }), 400

    # 创建安装任务（添加ip_address参数）
    task_id = str(uuid.uuid4())
    # 同时保存原始文件名和存储文件名
    task = Task(task_id, original_filename, stored_filename, apk_path, client_ip)

    with lock:
        install_queue.put(task)
        task_status[task_id] = task

    return jsonify({
        "task_id": task_id,
        "queue_position": install_queue.qsize(),
        "status": "QUEUED"
    })


@app.route('/status/<task_id>', methods=['GET'])
def get_status(task_id):
    """获取任务状态"""
    with lock:
        task = task_status.get(task_id)
        if not task:
            return jsonify({"error": "Task not found"}), 404

        response = {
            "task_id": task.task_id,
            "status": task.status,
            "progress": task.progress,
            "message": task.message,
            "queue_position": 0,
            "screenshot": task.screenshot,
            "screenshot_ready": task.screenshot_ready,
            "screenshot_countdown": task.screenshot_countdown
        }

        # 计算队列位置
        if task.status == "QUEUED":
            queue_list = list(install_queue.queue)
            try:
                position = queue_list.index(task) + 1
                response["queue_position"] = position
            except ValueError:
                pass

    return jsonify(response)

if __name__ == '__main__':
    #连接设备
    connect_device()
    # 启动后台工作线程
    threading.Thread(target=worker, daemon=True).start()
    app.run(host='0.0.0.0', port=5000, threaded=True)