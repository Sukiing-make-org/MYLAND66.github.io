#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Anitabi Web Updater - 增量巡礼点爬虫

针对 anitabi 网页版 (https://www.anitabi.cn/map?bangumiId={id}) 爬取巡礼点数据。
网页版的数据比 API 提供的更完整，因此本脚本用于将网页版的巡礼点增量更新到本地数据库。

运行逻辑:
  扫描本地目录 pic/data/*/
  ↓ 读取每个 info.json 的 "id" 字段 → 得到 bangumiId
  ↓ 对每一个 bangumiId:
  ├─ Selenium 打开 https://www.anitabi.cn/map?bangumiId={id}
  ├─ 等待 Vue.js 渲染并读取 window.mapApp.bangumi (含全部巡礼点+经纬度)
  ├─ 与本地 points.json 按 "id" 去重，找出新点
  ├─ 通过浏览器 canvas 下载新点的图片 (绕过 Cloudflare)
  └─ 写入本地 points.json / info.json
  ↓ 重新生成 index.json

用法:
  # 增量更新单个番剧 (本地文件夹 1)
  python anitabi_web_updater.py --local-id 1

  # 增量更新全部番剧
  python anitabi_web_updater.py --batch

  # 仅扫描对比不下载 (dry-run)
  python anitabi_web_updater.py --batch --dry-run

  # 指定 Chrome / chromedriver 路径
  python anitabi_web_updater.py --batch --chrome-path /path/to/chrome --driver-path /path/to/chromedriver
"""

import os
import re
import sys
import json
import time
import base64
import logging
import argparse
import traceback
from pathlib import Path

import requests
from typing import List, Dict, Optional, Tuple, Set
from urllib.parse import urlparse

try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.common.by import By
    from selenium.common.exceptions import (
        WebDriverException,
        NoSuchWindowException,
        TimeoutException,
    )
except ImportError:
    print("Error: selenium not installed. Run: pip install selenium", file=sys.stderr)
    raise

BASE_DIR = Path("pic/data")
WEB_BASE = "https://www.anitabi.cn"
IMG_CDN = "https://img-tc.anitabi.cn"
# 本地图片 URL 前缀 (与现有数据库约定一致)
LOCAL_IMG_PREFIX = "https://image.xinu.ink/pic/data"

# 锁文件 (与现有系统共享，避免和日/月更新器冲突)
LOCK_FILE = "anime_pilgrimage_scraper.lock"

LOG_FILE = "anitabi_web_updater.log"
logger = logging.getLogger("AnitabiWebUpdater")


def setup_logging(verbose: bool = False):
    logger.setLevel(logging.DEBUG)
    if logger.handlers:
        return
    fmt = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    sh = logging.StreamHandler(sys.stdout)
    sh.setLevel(logging.DEBUG if verbose else logging.INFO)
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    logger.addHandler(fh)


# ----------------------------------------------------------------------------
# Driver management
# ----------------------------------------------------------------------------


class AnitabiWebScraper:
    """通过 Selenium 爬取 anitabi 网页版巡礼点数据"""

    def __init__(
        self,
        base_dir: Path = BASE_DIR,
        headless: bool = True,
        page_wait: int = 5,  # 仅作轮询前最小等待; 真正等待由 _extract_via_mapapp 轮询处理
        chrome_path: Optional[str] = None,
        driver_path: Optional[str] = None,
        extract_timeout: int = 40,
    ):
        self.base_dir = Path(base_dir)
        self.headless = headless
        self.page_wait = page_wait
        self.extract_timeout = extract_timeout  # 轮询 mapApp.bangumi 的最大秒数
        self.chrome_path = chrome_path
        self.driver_path = driver_path
        self._driver: Optional[webdriver.Chrome] = None

    # ---- driver ----
    def _resolve_driver_path(self) -> Optional[str]:
        """解析 chromedriver 路径: 优先命令行参数，其次尝试 webdriver-manager 缓存"""
        if self.driver_path and os.path.isfile(self.driver_path):
            return self.driver_path
        # 尝试 webdriver-manager 缓存目录 (兼容 Selenium Manager 失败的情况)
        wdm_dir = Path.home() / ".wdm" / "drivers" / "chromedriver"
        if wdm_dir.exists():
            candidates = sorted(
                wdm_dir.rglob("chromedriver"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            for cand in candidates:
                # 跳过非可执行文件 (THIRD_PARTY_NOTICES.chromedriver 等)
                if os.access(cand, os.X_OK) and cand.is_file():
                    return str(cand)
        return None

    def get_driver(self) -> webdriver.Chrome:
        if self._driver is not None:
            try:
                # 检查 driver 是否还活着
                _ = self._driver.current_url
                return self._driver
            except (NoSuchWindowException, WebDriverException):
                self.quit()

        opts = Options()
        if self.headless:
            opts.add_argument("--headless=new")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument("--disable-gpu")
        opts.add_argument("--window-size=1920,1080")
        # anitabi 地图使用 WebGL, 需要 swiftshader 软件渲染
        opts.add_argument("--use-gl=angle")
        opts.add_argument("--use-angle=swiftshader")
        opts.add_argument("--enable-webgl")
        opts.add_argument("--ignore-gpu-blocklist")
        opts.add_argument(
            "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )

        # Chrome 二进制位置
        if self.chrome_path:
            opts.binary_location = self.chrome_path
        else:
            for guess in [
                "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                "/usr/bin/google-chrome",
                "/usr/bin/chromium-browser",
                "/usr/bin/chromium",
            ]:
                if os.path.exists(guess):
                    opts.binary_location = guess
                    break

        service = None
        dp = self._resolve_driver_path()
        if dp:
            try:
                os.chmod(dp, 0o755)
            except OSError:
                pass
            service = Service(executable_path=dp)

        try:
            self._driver = webdriver.Chrome(service=service, options=opts) if service else webdriver.Chrome(options=opts)
        except WebDriverException:
            # 回退: 让 Selenium Manager 自动处理
            self._driver = webdriver.Chrome(options=opts)

        # 设置页面加载超时
        self._driver.set_page_load_timeout(60)
        self._driver.set_script_timeout(45)
        logger.info("Chrome driver initialized (version=%s)", self._driver.capabilities.get("browserVersion", "?"))
        return self._driver

    def quit(self):
        if self._driver:
            try:
                self._driver.quit()
            except Exception:
                pass
            self._driver = None

    # ----------------------------------------------------------------------------
    # 页面加载 + 数据提取 (3层策略)
    # ----------------------------------------------------------------------------

    def _load_page(self, bangumi_id: int) -> bool:
        """打开番剧页面并等待 Vue 渲染"""
        driver = self.get_driver()
        url = f"{WEB_BASE}/map?bangumiId={bangumi_id}"
        logger.info("加载页面: %s", url)
        try:
            driver.get(url)
        except TimeoutException:
            logger.warning("页面加载超时，继续尝试提取数据")

        # 等待渲染
        time.sleep(self.page_wait)

        # 处理可能弹出的 WebGL 初始化失败 alert
        self._dismiss_alerts()
        # 关闭 changelog 更新公告遮罩 (避免遮挡 DOM 回退策略的点击)
        self._dismiss_changelog_overlay()
        return True

    def _dismiss_changelog_overlay(self):
        """关闭 anitabi 的更新公告遮罩 (点击"知道了"按钮)"""
        driver = self.get_driver()
        try:
            # 遮罩中的"知道了"按钮 / 关闭按钮
            for text in ("知道了", "Got it", "OK", "关闭"):
                btns = driver.find_elements(By.XPATH, f"//*[contains(text(),'{text}')]")
                for b in btns:
                    if b.is_displayed():
                        try:
                            b.click()
                            time.sleep(0.5)
                            logger.debug("已关闭公告遮罩 ('%s')", text)
                            return
                        except Exception:
                            continue
        except Exception:
            pass

    def _dismiss_alerts(self):
        driver = self.get_driver()
        try:
            # 用 JS 拦截 alert (页面初始化失败时 anitabi 会弹 alert)
            driver.execute_script(
                "window.alert = function(){}; window.confirm = function(){return true;};"
            )
        except Exception:
            pass

    def _extract_via_mapapp(self) -> Optional[Dict]:
        """策略1: 从 window.mapApp.bangumi 提取 (最可靠, 含全部点位+经纬度)

        轮询等待 mapApp.bangumi.points 就绪 (页面为 Vue SPA, 数据异步加载,
        固定 sleep 不可靠)。
        """
        driver = self.get_driver()
        deadline = time.time() + self.extract_timeout
        last_len = None
        # 等待 points 数量稳定 (连续 2 次相同视为加载完成)
        stable_count = 0
        while time.time() < deadline:
            try:
                ready = driver.execute_script(
                    """
                    var a = window.mapApp;
                    if (!a || !a.bangumi || !a.bangumi.points) return {ready:false};
                    var arr = a.bangumi.points;
                    if (!Array.isArray(arr)) {
                        arr = arr.__v_raw || arr;
                        try { arr = Array.prototype.slice.call(arr); } catch(e){}
                    }
                    return {ready: Array.isArray(arr) && arr.length > 0, len: arr ? arr.length : 0};
                    """
                )
            except WebDriverException as e:
                logger.warning("mapApp 轮询异常: %s", e)
                ready = {"ready": False}

            if ready and ready.get("ready"):
                cur = ready.get("len")
                if cur == last_len:
                    stable_count += 1
                    # 数量稳定即认为全部加载 (避免在增量加载过程中读取)
                    if stable_count >= 2:
                        break
                else:
                    stable_count = 0
                    last_len = cur
            time.sleep(1)
        else:
            logger.warning("mapApp.bangumi 在 %ds 内未就绪", self.extract_timeout)

        try:
            raw = driver.execute_script(
                """
                var a = window.mapApp;
                if (!a || !a.bangumi) return null;
                var b = a.bangumi;
                var arr = null;
                if (Array.isArray(b.points)) arr = b.points;
                else if (b.points) {
                    arr = b.points.__v_raw || b.points;
                    if (!Array.isArray(arr)) {
                        try { arr = Array.prototype.slice.call(arr); } catch(e){}
                    }
                }
                var pts = [];
                if (arr && arr.length) {
                    for (var i = 0; i < arr.length; i++) {
                        pts.push(JSON.parse(JSON.stringify(arr[i])));
                    }
                }
                var info = {};
                ['id','cn','en','title','city','color','cover','cat','geo','zoom','modified','abbr','tags','pointsLength'].forEach(function(k){
                    if (b[k] !== undefined) info[k] = b[k];
                });
                return JSON.stringify({info: info, points: pts});
                """
            )
        except WebDriverException as e:
            logger.warning("mapApp 提取失败: %s", e)
            return None

        if not raw:
            return None
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None
        if not data or not isinstance(data, dict):
            return None
        if not data.get("points"):
            return None
        return data

    def _extract_via_dom(self) -> Optional[Dict]:
        """策略2: 点击"展开"后解析 DOM (回退方案, 但 DOM 中无经纬度)"""
        driver = self.get_driver()
        try:
            # 点击展开
            for a in driver.find_elements(By.CSS_SELECTOR, ".fold-actions a"):
                if a.text.strip() in ("展开", "Expand"):
                    a.click()
                    time.sleep(2)
                    break
            items = driver.find_elements(By.CSS_SELECTOR, ".feature-item.point-item")
            if not items:
                return None
            points = []
            for it in items:
                html = it.get_attribute("outerHTML") or ""
                # 解析图片
                m = re.search(r'src="([^"]+)"', html)
                image = m.group(1) if m else ""
                # 标题
                nm = it.find_elements(By.CSS_SELECTOR, "h4")
                name = nm[0].text if nm else ""
                # ep / s
                ep = None
                sm = it.find_elements(By.CSS_SELECTOR, ".ep")
                if sm:
                    ep = _parse_ep(sm[0].text)
                s = None
                ss = it.find_elements(By.CSS_SELECTOR, ".s")
                if ss:
                    s = _parse_s(ss[0].text)
                # 无 id / geo - DOM 提取无法获得经纬度, 仅用于极端回退
                points.append(
                    {
                        "id": None,
                        "name": name,
                        "image": image,
                        "ep": ep,
                        "s": s,
                        "geo": None,
                        "_dom_only": True,
                    }
                )
            return {"info": {}, "points": points}
        except Exception as e:
            logger.warning("DOM 提取失败: %s", e)
            return None

    def _extract_via_api_fallback(self, bangumi_id: int) -> Optional[Dict]:
        """策略3: 浏览器内 fetch API (同源, 可绕过 Cloudflare)。API 数据可能不完整, 仅作兜底"""
        driver = self.get_driver()
        try:
            raw = driver.execute_async_script(
                """
                var done = arguments[arguments.length-1];
                var id = arguments[0];
                Promise.all([
                    fetch('https://www.anitabi.cn/api/bangumi/'+id+'/lite').then(function(r){return r.ok?r.json():null;}).catch(function(){return null;}),
                    fetch('https://www.anitabi.cn/api/bangumi/'+id+'/points/detail?haveImage=true').then(function(r){return r.ok?r.json():null;}).catch(function(){return null;})
                ]).then(function(res){
                    done({info: res[0], points: res[1]});
                });
                """,
                bangumi_id,
            )
        except WebDriverException as e:
            logger.warning("API 回退提取失败: %s", e)
            return None
        if not raw or not isinstance(raw, dict):
            return None
        pts = raw.get("points")
        if not pts:
            return None
        if isinstance(pts, dict) and "points" in pts:
            pts = pts["points"]
        if not isinstance(pts, list):
            return None
        return {"info": raw.get("info") or {}, "points": pts}

    def fetch_bangumi(self, bangumi_id: int) -> Optional[Dict]:
        """打开页面并提取番剧数据 (3层策略)"""
        self._load_page(bangumi_id)

        # 策略1: mapApp (首选, 含经纬度)
        data = self._extract_via_mapapp()
        if data and data.get("points"):
            n_valid = sum(1 for p in data["points"] if p.get("geo"))
            logger.info(
                "策略1(mapApp)成功: 共 %d 个点, 其中 %d 个含经纬度",
                len(data["points"]),
                n_valid,
            )
            if n_valid > 0:
                return data
            logger.warning("mapApp 提取的点全部缺少经纬度, 尝试其它策略")

        # 策略2: DOM (无经纬度, 通常不使用)
        data = self._extract_via_dom()
        if data and data.get("points"):
            logger.info("策略2(DOM)成功: %d 个点 (无经纬度)", len(data["points"]))
            return data

        # 策略3: API 回退
        data = self._extract_via_api_fallback(bangumi_id)
        if data and data.get("points"):
            logger.info("策略3(API回退)成功: %d 个点", len(data["points"]))
            return data

        logger.error("所有提取策略均失败 (bangumiId=%s)", bangumi_id)
        return None

    # ----------------------------------------------------------------------------
    # 图片下载 (通过浏览器 canvas 绕过 Cloudflare)
    # ----------------------------------------------------------------------------

    def _image_url_from_path(self, image_path: str) -> Optional[str]:
        """把 point.image 相对路径映射到 CDN 完整 URL

        /images/points/51/xxx.jpg        -> https://img-tc.anitabi.cn/points/51/xxx.jpg
        /images/user/0/bangumi/51/...jpg -> https://img-tc.anitabi.cn/user/0/bangumi/51/...jpg
        已经是完整 URL 则原样返回
        """
        if not image_path:
            return None
        if image_path.startswith("http://") or image_path.startswith("https://"):
            return image_path
        # 去掉查询参数
        path = image_path.split("?")[0]
        if path.startswith("/images/"):
            path = path[len("/images"):]  # -> /points/51/xxx.jpg
        if not path.startswith("/"):
            path = "/" + path
        return f"{IMG_CDN}{path}"

    def _download_image_requests(self, image_url: str, save_path: Path) -> bool:
        """通过 requests 直接下载图片 (适用于非 Cloudflare 的 CDN, 如封面图)"""
        if save_path.exists() and save_path.stat().st_size > 0:
            return True
        save_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            r = requests.get(
                image_url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Referer": f"{WEB_BASE}/",
                },
                timeout=30,
            )
            if r.status_code == 200 and len(r.content) > 100:
                ct = r.headers.get("content-type", "")
                if "image" in ct or "octet-stream" in ct:
                    with open(save_path, "wb") as f:
                        f.write(r.content)
                    logger.debug("图片已保存(requests) %s (%d bytes)", save_path.name, len(r.content))
                    return True
        except Exception as e:
            logger.debug("requests 下载失败 %s: %s", image_url, e)
        return False

    def download_image(self, image_url: str, save_path: Path) -> bool:
        """下载图片: 优先浏览器 canvas (绕过 Cloudflare), 失败则回退 requests"""
        if save_path.exists() and save_path.stat().st_size > 0:
            return True
        save_path.parent.mkdir(parents=True, exist_ok=True)

        # 策略1: 浏览器 canvas (适用于 img-tc.anitabi.cn 等 Cloudflare 保护的 CDN)
        if self._driver is not None:
            driver = self.get_driver()
            try:
                result = driver.execute_async_script(
                    """
                    var done = arguments[arguments.length-1];
                    var url = arguments[0];
                    var img = new Image();
                    img.crossOrigin = 'anonymous';
                    img.onload = function(){
                        try {
                            var c = document.createElement('canvas');
                            c.width = img.naturalWidth;
                            c.height = img.naturalHeight;
                            var ctx = c.getContext('2d');
                            ctx.drawImage(img, 0, 0);
                            var data = c.toDataURL('image/jpeg', 0.95);
                            done({ok:true, w:img.naturalWidth, h:img.naturalHeight, data:data});
                        } catch(e) {
                            done({ok:false, err:'canvas:' + e.toString()});
                        }
                    };
                    img.onerror = function(){ done({ok:false, err:'img_error'}); };
                    img.src = url;
                    """,
                    image_url,
                )
            except WebDriverException as e:
                logger.debug("canvas 下载异常 %s: %s", image_url, e)
                result = None

            if result and result.get("ok") and result.get("data"):
                data_url = result["data"]
                try:
                    _, b64 = data_url.split(",", 1)
                    raw = base64.b64decode(b64)
                    if raw:
                        with open(save_path, "wb") as f:
                            f.write(raw)
                        logger.debug("图片已保存(canvas) %s (%dx%d, %d bytes)",
                                     save_path.name, result.get("w"), result.get("h"), len(raw))
                        return True
                except Exception as e:
                    logger.debug("canvas 解码失败 %s: %s", image_url, e)

        # 策略2: requests 回退 (适用于非 Cloudflare 的 CDN, 如封面图 bgm-api.anitabi.cn)
        if self._download_image_requests(image_url, save_path):
            return True

        logger.warning("图片下载失败 %s (canvas + requests 均失败)", image_url)
        return False

    # ----------------------------------------------------------------------------
    # 本地数据读写
    # ----------------------------------------------------------------------------

    def _normalize_point(self, point: Dict) -> Dict:
        """把网页点数据归一化为本地 points.json 格式

        本地格式: {id, name, image, ep, s, geo, origin, originURL}
        """
        geo = point.get("geo")
        if geo is not None and isinstance(geo, list) and len(geo) == 2:
            geo = [round(float(geo[0]), 6), round(float(geo[1]), 6)]
        else:
            geo = None

        out = {
            "id": point.get("id"),
            "name": point.get("name", ""),
            "image": point.get("image", ""),  # 后续会被替换为本地 URL
            "geo": geo,
        }
        # ep: 网页为 int 或 "OP"/"ED" 等字符串
        if "ep" in point and point["ep"] not in (None, ""):
            out["ep"] = point["ep"]
        # s: 秒数 (网页为 int 或 "")
        if "s" in point and point["s"] not in (None, ""):
            out["s"] = point["s"]
        # mark: 备注
        if point.get("mark"):
            out["mark"] = point["mark"]
        # folder: 分组
        if point.get("folder"):
            out["folder"] = point["folder"]
        # origin / originLink
        if point.get("origin"):
            out["origin"] = point["origin"]
        if point.get("originLink"):
            out["originURL"] = point["originLink"]
        # 中文名
        if point.get("cn"):
            out["cn"] = point["cn"]
        return out

    def _local_image_url(self, local_id: str, filename: str) -> str:
        return f"{LOCAL_IMG_PREFIX}/{local_id}/images/{filename}"

    def _image_filename_from_url(self, cdn_url: str) -> str:
        """从 CDN URL 提取文件名 (去查询参数)"""
        path = urlparse(cdn_url).path
        return os.path.basename(path) or "image.jpg"

    def load_points(self, local_id: str) -> List[Dict]:
        path = self.base_dir / str(local_id) / "points.json"
        if not path.exists():
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("读取 points.json 失败 (local_id=%s): %s", local_id, e)
            return []
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and "points" in data:
            return data["points"]
        return []

    def save_points(self, local_id: str, points: List[Dict]):
        folder = self.base_dir / str(local_id)
        (folder / "images").mkdir(parents=True, exist_ok=True)
        path = folder / "points.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(points, f, ensure_ascii=False, indent=2)

    def load_info(self, local_id: str) -> Dict:
        path = self.base_dir / str(local_id) / "info.json"
        if not path.exists():
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}

    def save_info(self, local_id: str, info: Dict):
        folder = self.base_dir / str(local_id)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "info.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(info, f, ensure_ascii=False, indent=2)

    # ----------------------------------------------------------------------------
    # 去重
    # ----------------------------------------------------------------------------

    @staticmethod
    def _existing_point_keys(existing: List[Dict]) -> Set[str]:
        """已有点位 id 集合 (主键)。同时建立 (geo+name) 二级键防止 id 缺失时重复"""
        id_keys = set()
        geo_keys = set()
        for p in existing:
            if not isinstance(p, dict):
                continue
            pid = p.get("id")
            if pid:
                id_keys.add(str(pid))
            geo = p.get("geo")
            name = str(p.get("name", "")).strip()
            if isinstance(geo, list) and len(geo) == 2 and name:
                geo_keys.add((round(float(geo[0]), 5), round(float(geo[1]), 5), name))
        return id_keys, geo_keys

    def find_new_points(self, existing: List[Dict], incoming: List[Dict]) -> List[Dict]:
        """按 id 去重, 找出 incoming 中不存在于 existing 的新点"""
        id_keys, geo_keys = self._existing_point_keys(existing)
        new_points = []
        for pt in incoming:
            if not isinstance(pt, dict):
                continue
            pid = pt.get("id")
            if pid and str(pid) in id_keys:
                continue
            # id 缺失时用 geo+name 兜底去重
            geo = pt.get("geo")
            name = str(pt.get("name", "")).strip()
            if not pid and isinstance(geo, list) and len(geo) == 2 and name:
                k = (round(float(geo[0]), 5), round(float(geo[1]), 5), name)
                if k in geo_keys:
                    continue
            # 缺少经纬度的点 (DOM 回退产生) 不作为新点入库
            if not pt.get("geo"):
                continue
            new_points.append(pt)
        return new_points

    def _repair_missing_images(
        self, local_id: str, existing: List[Dict], web_points: List[Dict]
    ) -> int:
        """补下之前失败的图片: 检测已有点位中本地图片文件缺失的, 重新下载。

        通过 point id 把已有点位和网页点位的 image 路径关联, 用网页的相对路径
        构造 CDN URL 重新下载。返回成功补下的图片数。
        """
        images_dir = self.base_dir / str(local_id) / "images"
        # 建网页点位 id -> cdn_url 映射
        web_img_map = {}
        for wp in web_points:
            if not isinstance(wp, dict):
                continue
            pid = wp.get("id")
            if pid:
                cdn_url = self._image_url_from_path(wp.get("image", ""))
                if cdn_url:
                    web_img_map[str(pid)] = cdn_url

        repaired = 0
        for pt in existing:
            if not isinstance(pt, dict):
                continue
            img = pt.get("image", "")
            # 只处理本地占位 URL (xinu.ink) 但文件缺失的情况
            if not img or "xinu.ink" not in img:
                continue
            filename = os.path.basename(urlparse(img).path)
            if not filename:
                continue
            save_path = images_dir / filename
            if save_path.exists() and save_path.stat().st_size > 0:
                continue  # 文件已存在, 无需补下
            # 从网页数据找 CDN URL
            pid = str(pt.get("id", ""))
            cdn_url = web_img_map.get(pid)
            if not cdn_url:
                continue
            ok = self.download_image(cdn_url, save_path)
            if ok:
                pt["image"] = self._local_image_url(local_id, filename)
                repaired += 1
                logger.info("[%s] 补下图片: %s", local_id, filename)
        return repaired

    # ----------------------------------------------------------------------------
    # 单番剧增量更新
    # ----------------------------------------------------------------------------

    def update_bangumi(
        self,
        bangumi_id: int,
        local_id: str,
        dry_run: bool = False,
    ) -> Dict:
        """打开页面提取数据后增量更新 (单独页面模式)"""
        data = self.fetch_bangumi(bangumi_id)
        if not data or not data.get("points"):
            return {
                "bangumi_id": bangumi_id,
                "local_id": local_id,
                "status": "failed",
                "added": 0,
                "total": 0,
                "message": "提取数据失败或无点位",
            }
        return self.update_bangumi_from_data(bangumi_id, local_id, data, dry_run=dry_run)

    def update_bangumi_from_data(
        self,
        bangumi_id: int,
        local_id: str,
        data: Dict,
        dry_run: bool = False,
    ) -> Dict:
        """用已提取的数据增量更新本地番剧 (批量模式复用, 无需重复加载页面)"""
        result = {
            "bangumi_id": bangumi_id,
            "local_id": local_id,
            "status": "failed",
            "added": 0,
            "total": 0,
            "skipped_no_coords": 0,
        }

        web_points = data.get("points") or []
        web_info = data.get("info") or {}
        if not web_points:
            result["message"] = "无点位数据"
            return result

        existing = self.load_points(local_id)
        result["total_before"] = len(existing)

        new_points = self.find_new_points(existing, web_points)

        # 即使没有新点, 也补下之前失败的图片 (本地占位 URL 但文件缺失)
        repaired = 0
        if not dry_run and existing:
            repaired = self._repair_missing_images(local_id, existing, web_points)

        if not new_points:
            result["status"] = "no_change"
            result["total"] = len(existing)
            result["images_repaired"] = repaired
            result["message"] = "无新增巡礼点" + (f", 补下 {repaired} 张图片" if repaired else "")
            logger.info("[%s] bangumiId=%s 无新增巡礼点 (现有 %d)%s", local_id, bangumi_id, len(existing), f", 补下 {repaired} 张图片" if repaired else "")
            if repaired:
                self.save_points(local_id, existing)
            return result

        logger.info(
            "[%s] bangumiId=%s 发现 %d 个新点 (现有 %d, 网页 %d)",
            local_id,
            bangumi_id,
            len(new_points),
            len(existing),
            len(web_points),
        )

        if dry_run:
            result["status"] = "dry_run"
            result["added"] = len(new_points)
            result["total"] = len(existing) + len(new_points)
            result["new_points"] = [
                {"id": p.get("id"), "name": p.get("name"), "geo": p.get("geo")} for p in new_points
            ]
            return result

        images_dir = self.base_dir / str(local_id) / "images"
        images_dir.mkdir(parents=True, exist_ok=True)

        # 先归一化新点 (图片 URL 暂用本地占位), 并立即落盘 → 即使后续图片下载
        # 被中断, 巡礼点元数据 (id/名称/经纬度/ep/s/mark/origin) 也不会丢失。
        # 用并行列表 cdn_urls 记录原图 URL, 避免把临时字段写进 points.json。
        normalized_new = []
        cdn_urls = []
        for pt in new_points:
            np = self._normalize_point(pt)
            cdn_url = self._image_url_from_path(pt.get("image", ""))
            cdn_urls.append(cdn_url or "")
            if cdn_url:
                filename = self._image_filename_from_url(cdn_url)
                np["image"] = self._local_image_url(local_id, filename)
            else:
                np["image"] = ""
            normalized_new.append(np)

        # 落盘点位 (含本地图片 URL 占位; 图片尚未下载时 URL 指向不存在的文件,
        # 但点位元数据已保存, 可后续重跑补图)
        combined = existing + normalized_new
        self.save_points(local_id, combined)
        logger.info("[%s] 点位元数据已先落盘 (%d 个新点), 开始下载图片", local_id, len(normalized_new))

        # 下载图片, 每完成 checkpoint_size 张就重新保存一次 (防中断丢失进度)
        # 注意: 下载失败时保留本地占位 URL (非 CDN 回退), 这样下次重跑时
        # _repair_missing_images 能检测到文件缺失并重新下载。
        checkpoint_size = 25
        failed_images = 0
        downloaded = 0
        for idx, np in enumerate(normalized_new):
            cdn_url = cdn_urls[idx]
            if cdn_url:
                filename = self._image_filename_from_url(cdn_url)
                save_path = images_dir / filename
                ok = self.download_image(cdn_url, save_path)
                if ok:
                    np["image"] = self._local_image_url(local_id, filename)
                    downloaded += 1
                else:
                    failed_images += 1
                    # 保留本地占位 URL (文件不存在); 下次重跑会通过 _repair_missing_images 补下
                    np["image"] = self._local_image_url(local_id, filename)
            # 定期 checkpoint: 把已下载的进度落盘
            if downloaded and downloaded % checkpoint_size == 0:
                self.save_points(local_id, combined)
                logger.info("[%s] 图片下载进度: %d/%d (已落盘)", local_id, downloaded, len(normalized_new))

        # 最终落盘 (图片 URL 全部更新)
        self.save_points(local_id, combined)
        if failed_images:
            logger.warning("[%s] %d 张图片下载失败 (下次重跑可补下)", local_id, failed_images)
        else:
            logger.info("[%s] 全部 %d 张图片下载完成", local_id, downloaded)

        # 更新 info.json (合并网页元数据, 但保留本地字段)
        info = self.load_info(local_id)
        info_updated = False
        # 网页的 id 应等于 info.id; 不覆盖 local_id
        if web_info:
            for k in ("cn", "en", "title", "city", "color", "cover", "cat", "geo", "zoom", "modified"):
                if web_info.get(k) is not None and not info.get(k):
                    info[k] = web_info[k]
                    info_updated = True
        # 更新点数统计
        info["pointsLength"] = len(combined)
        info_updated = True
        # 确保保留 id 字段
        if not info.get("id"):
            info["id"] = bangumi_id
            info_updated = True
        if info_updated:
            self.save_info(local_id, info)

        result["status"] = "updated"
        result["added"] = len(normalized_new)
        result["total"] = len(combined)
        result["image_failures"] = failed_images
        result["images_repaired"] = repaired
        result["message"] = f"新增 {len(normalized_new)} 个巡礼点 (共 {len(combined)} 个)"
        logger.info("[%s] 完成: %s", local_id, result["message"])
        return result

    # ----------------------------------------------------------------------------
    # 批量更新
    # ----------------------------------------------------------------------------

    def scan_local_bangumi(self) -> List[Tuple[str, int]]:
        """扫描本地目录, 返回 [(local_id, bangumi_id), ...]"""
        items = []
        if not self.base_dir.exists():
            logger.error("数据目录不存在: %s", self.base_dir)
            return items
        for folder in sorted(self.base_dir.iterdir(), key=lambda p: _natural_key(p.name)):
            if not folder.is_dir() or not folder.name.isdigit():
                continue
            info_path = folder / "info.json"
            if not info_path.exists():
                continue
            try:
                with open(info_path, "r", encoding="utf-8") as f:
                    info = json.load(f)
            except (json.JSONDecodeError, OSError):
                continue
            bid = info.get("id")
            if not bid:
                continue
            try:
                bid = int(bid)
            except (ValueError, TypeError):
                continue
            items.append((folder.name, bid))
        return items

    def batch_update(
        self,
        only_local_id: Optional[str] = None,
        dry_run: bool = False,
        limit: Optional[int] = None,
        delay: float = 1.0,
    ) -> List[Dict]:
        results = []
        bangumi_list = self.scan_local_bangumi()
        if only_local_id:
            bangumi_list = [(lid, bid) for (lid, bid) in bangumi_list if lid == str(only_local_id)]

        if not bangumi_list:
            logger.warning("未找到需要更新的番剧 (only_local_id=%s)", only_local_id)
            return results

        logger.info("共 %d 个番剧待处理", len(bangumi_list))
        if limit:
            bangumi_list = bangumi_list[:limit]

        total_new = 0
        total_no_change = 0
        total_failed = 0
        for i, (local_id, bangumi_id) in enumerate(bangumi_list, 1):
            logger.info("=" * 60)
            logger.info("[%d/%d] local_id=%s bangumiId=%s", i, len(bangumi_list), local_id, bangumi_id)
            try:
                res = self.update_bangumi(bangumi_id, local_id, dry_run=dry_run)
            except Exception as e:
                logger.error("处理 local_id=%s bangumiId=%s 时异常: %s", local_id, bangumi_id, e)
                logger.debug(traceback.format_exc())
                res = {
                    "bangumi_id": bangumi_id,
                    "local_id": local_id,
                    "status": "failed",
                    "added": 0,
                    "message": str(e),
                }
                # 出错时重启 driver 防止状态污染
                self.quit()

            results.append(res)
            if res["status"] == "updated":
                total_new += res.get("added", 0)
            elif res["status"] == "no_change":
                total_no_change += 1
            elif res["status"] in ("failed",):
                total_failed += 1

            if delay and i < len(bangumi_list):
                time.sleep(delay)

        logger.info("=" * 60)
        logger.info(
            "批量完成: %d 个番剧, 新增 %d 点, 无变化 %d, 失败 %d",
            len(results),
            total_new,
            total_no_change,
            total_failed,
        )
        return results

    # ----------------------------------------------------------------------------
    # 番剧发现 (从主页 mapApp.bangumis 提取全量列表)
    # ----------------------------------------------------------------------------

    def load_main_page(self) -> bool:
        """加载 anitabi 主地图页并等待 mapApp.bangumis 就绪"""
        driver = self.get_driver()
        url = f"{WEB_BASE}/map"
        logger.info("加载主页: %s", url)
        try:
            driver.get(url)
        except TimeoutException:
            logger.warning("主页加载超时, 继续尝试")

        time.sleep(self.page_wait)
        self._dismiss_alerts()
        self._dismiss_changelog_overlay()

        # 轮询等待 bangumis 数组就绪
        deadline = time.time() + self.extract_timeout
        last_len = None
        stable = 0
        while time.time() < deadline:
            try:
                r = driver.execute_script(
                    """
                    var a = window.mapApp;
                    if (!a || !a.bangumis) return {ready:false};
                    var arr = a.bangumis;
                    if (!Array.isArray(arr)) { arr = arr.__v_raw || arr; }
                    return {ready: Array.isArray(arr) && arr.length > 0, len: arr ? arr.length : 0};
                    """
                )
            except WebDriverException:
                r = {"ready": False}
            if r and r.get("ready"):
                cur = r.get("len")
                if cur == last_len:
                    stable += 1
                    if stable >= 2:
                        logger.info("主页 bangumis 就绪: %d 部番剧", cur)
                        return True
                else:
                    stable = 0
                    last_len = cur
            time.sleep(1)
        logger.warning("主页 bangumis 在 %ds 内未就绪", self.extract_timeout)
        return False

    def extract_all_bangumi_ids(self) -> List[int]:
        """从已加载的主页提取全部番剧 ID 列表"""
        driver = self.get_driver()
        try:
            raw = driver.execute_script(
                """
                var a = window.mapApp;
                if (!a || !a.bangumis) return [];
                var arr = Array.isArray(a.bangumis) ? a.bangumis : (a.bangumis.__v_raw || a.bangumis);
                var ids = [];
                for (var i = 0; i < arr.length; i++) { if (arr[i] && arr[i].id) ids.push(arr[i].id); }
                return ids;
                """
            )
        except WebDriverException as e:
            logger.error("提取番剧 ID 列表失败: %s", e)
            return []
        return [int(x) for x in raw if x is not None] if raw else []

    def extract_bangumi_data(self, bangumi_id: int) -> Optional[Dict]:
        """从已加载主页的 mapApp.bangumis 中提取指定番剧的完整数据 (无需导航)

        主页的 bangumis 数组已包含每部番剧的全部巡礼点 + 经纬度, 因此无需
        逐个打开 ?bangumiId=XX 页面, 大幅减少页面加载次数。
        """
        driver = self.get_driver()
        try:
            raw = driver.execute_script(
                """
                var a = window.mapApp;
                if (!a || !a.bangumis) return null;
                var arr = Array.isArray(a.bangumis) ? a.bangumis : (a.bangumis.__v_raw || a.bangumis);
                for (var i = 0; i < arr.length; i++) {
                    if (arr[i] && arr[i].id == arguments[0]) {
                        var b = arr[i];
                        var pts = [];
                        if (b.points) {
                            var parr = Array.isArray(b.points) ? b.points : (b.points.__v_raw || b.points);
                            if (parr) {
                                for (var j = 0; j < parr.length; j++) {
                                    pts.push(JSON.parse(JSON.stringify(parr[j])));
                                }
                            }
                        }
                        var info = {};
                        ['id','cn','en','title','city','color','cover','cat','geo','zoom','modified','abbr','tags'].forEach(function(k){
                            if (b[k] !== undefined) info[k] = b[k];
                        });
                        return JSON.stringify({info: info, points: pts});
                    }
                }
                return null;
                """,
                bangumi_id,
            )
        except WebDriverException as e:
            logger.warning("从 bangumis 提取 bangumiId=%s 失败: %s", bangumi_id, e)
            return None
        if not raw:
            return None
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None
        if not data or not data.get("points"):
            return None
        return data

    def get_next_local_id(self) -> int:
        """获取下一个可用的本地文件夹 ID (max + 1)"""
        try:
            folders = [
                int(f.name)
                for f in self.base_dir.iterdir()
                if f.is_dir() and f.name.isdigit()
            ]
            if folders:
                return max(folders) + 1
        except Exception:
            pass
        return 1

    def save_new_bangumi(
        self, bangumi_id: int, local_id: str, data: Dict, dry_run: bool = False
    ) -> Dict:
        """保存一部新发现的番剧 (创建本地文件夹 + info.json + points.json + 图片)"""
        web_points = data.get("points") or []
        web_info = data.get("info") or {}
        result = {
            "bangumi_id": bangumi_id,
            "local_id": local_id,
            "status": "failed",
            "added": 0,
            "total": 0,
        }

        # 过滤掉无经纬度的点
        valid_points = [p for p in web_points if p.get("geo")]
        if not valid_points:
            result["message"] = "无有效点位 (缺经纬度)"
            return result

        title = web_info.get("title") or web_info.get("cn") or f"bangumi_{bangumi_id}"
        logger.info(
            "[新] local_id=%s bangumiId=%s %s: %d 个点",
            local_id, bangumi_id, title, len(valid_points),
        )

        if dry_run:
            result["status"] = "dry_run"
            result["added"] = len(valid_points)
            result["total"] = len(valid_points)
            return result

        images_dir = self.base_dir / str(local_id) / "images"
        images_dir.mkdir(parents=True, exist_ok=True)

        # 归一化全部点位 + 下载图片 (带 checkpoint)
        normalized = []
        cdn_urls = []
        for pt in valid_points:
            np = self._normalize_point(pt)
            cdn_url = self._image_url_from_path(pt.get("image", ""))
            cdn_urls.append(cdn_url or "")
            if cdn_url:
                filename = self._image_filename_from_url(cdn_url)
                np["image"] = self._local_image_url(local_id, filename)
            else:
                np["image"] = ""
            normalized.append(np)

        # 先落盘元数据
        self.save_points(local_id, normalized)
        logger.info("[%s] 点位元数据已落盘 (%d 个点), 开始下载图片", local_id, len(normalized))

        checkpoint_size = 25
        failed_images = 0
        downloaded = 0
        for idx, np in enumerate(normalized):
            cdn_url = cdn_urls[idx]
            if cdn_url:
                filename = self._image_filename_from_url(cdn_url)
                save_path = images_dir / filename
                if self.download_image(cdn_url, save_path):
                    np["image"] = self._local_image_url(local_id, filename)
                    downloaded += 1
                else:
                    failed_images += 1
                    np["image"] = self._local_image_url(local_id, filename)
            if downloaded and downloaded % checkpoint_size == 0:
                self.save_points(local_id, normalized)
                logger.info("[%s] 图片下载进度: %d/%d", local_id, downloaded, len(normalized))

        self.save_points(local_id, normalized)

        # 保存 info.json
        info = dict(web_info)
        info["id"] = bangumi_id
        info["local_id"] = int(local_id)
        info["pointsLength"] = len(normalized)
        # 封面图: 下载并替换为本地 URL
        cover = web_info.get("cover", "")
        if cover:
            cover_filename = os.path.basename(urlparse(cover).path) or "cover.jpg"
            cover_path = images_dir / cover_filename
            if self.download_image(cover, cover_path):
                info["cover"] = self._local_image_url(local_id, cover_filename)
        self.save_info(local_id, info)

        if failed_images:
            logger.warning("[%s] %d 张图片下载失败 (下次重跑可补下)", local_id, failed_images)
        else:
            logger.info("[%s] 全部 %d 张图片下载完成", local_id, downloaded)

        result["status"] = "new"
        result["added"] = len(normalized)
        result["total"] = len(normalized)
        result["image_failures"] = failed_images
        result["message"] = f"新番剧 {title}: {len(normalized)} 个巡礼点"
        return result

    def discover_and_update(
        self,
        max_new: int = 50,
        dry_run: bool = False,
        delay: float = 1.0,
    ) -> List[Dict]:
        """发现新番剧 + 增量更新已有番剧 (单次主页加载)

        主页的 mapApp.bangumis 包含全部番剧及其巡礼点 (含经纬度),
        因此只需加载一次主页即可完成发现 + 更新, 无需逐个打开番剧页面。
        """
        results = []

        # 1. 加载主页
        if not self.load_main_page():
            logger.error("主页加载失败, 无法发现/更新")
            return results

        # 2. 提取全部番剧 ID
        web_ids = self.extract_all_bangumi_ids()
        if not web_ids:
            logger.error("未能提取到任何番剧 ID")
            return results
        web_id_set = set(web_ids)
        logger.info("网站共 %d 部番剧", len(web_ids))

        # 3. 扫描本地已有番剧
        local_items = self.scan_local_bangumi()
        local_id_set = {bid for _, bid in local_items}
        local_bid_to_lid = {bid: lid for lid, bid in local_items}

        # 4. 发现新番剧
        new_ids = [bid for bid in web_ids if bid not in local_id_set]
        logger.info("本地已有 %d 部, 网站新增 %d 部 (本次最多处理 %d 部)",
                     len(local_items), len(new_ids), max_new)

        new_to_process = new_ids[:max_new]
        next_local_id = self.get_next_local_id()

        # 5. 保存新番剧
        for i, new_bid in enumerate(new_to_process, 1):
            logger.info("=" * 60)
            logger.info("[新 %d/%d] bangumiId=%s → local_id=%s", i, len(new_to_process), new_bid, next_local_id)
            try:
                data = self.extract_bangumi_data(new_bid)
                if not data:
                    logger.warning("无法提取 bangumiId=%s 的数据, 跳过", new_bid)
                    results.append({"bangumi_id": new_bid, "local_id": str(next_local_id),
                                    "status": "failed", "added": 0, "message": "提取数据失败"})
                    next_local_id += 1
                    continue
                res = self.save_new_bangumi(new_bid, str(next_local_id), data, dry_run=dry_run)
                results.append(res)
                if res["status"] not in ("failed",):
                    next_local_id += 1
            except Exception as e:
                logger.error("保存新番剧 bangumiId=%s 时异常: %s", new_bid, e)
                logger.debug(traceback.format_exc())
                results.append({"bangumi_id": new_bid, "local_id": str(next_local_id),
                                "status": "failed", "added": 0, "message": str(e)})
                next_local_id += 1
                self.quit()
            if delay and i < len(new_to_process):
                time.sleep(delay)

        # 6. 增量更新已有番剧 (从主页内存提取, 无需重新加载页面)
        existing_to_update = [(lid, bid) for lid, bid in local_items if bid in web_id_set]
        logger.info("=" * 60)
        logger.info("开始增量更新 %d 部已有番剧", len(existing_to_update))

        for i, (local_id, bid) in enumerate(existing_to_update, 1):
            logger.info("-" * 50)
            logger.info("[更新 %d/%d] local_id=%s bangumiId=%s", i, len(existing_to_update), local_id, bid)
            try:
                data = self.extract_bangumi_data(bid)
                if not data:
                    logger.warning("无法从主页提取 bangumiId=%s, 跳过", bid)
                    results.append({"bangumi_id": bid, "local_id": local_id,
                                    "status": "failed", "added": 0, "message": "提取数据失败"})
                    continue
                res = self.update_bangumi_from_data(bid, local_id, data, dry_run=dry_run)
                results.append(res)
            except Exception as e:
                logger.error("更新 local_id=%s bangumiId=%s 时异常: %s", local_id, bid, e)
                logger.debug(traceback.format_exc())
                results.append({"bangumi_id": bid, "local_id": local_id,
                                "status": "failed", "added": 0, "message": str(e)})
                self.quit()
            if delay and i < len(existing_to_update):
                time.sleep(delay)

        # 汇总
        new_ok = [r for r in results if r["status"] == "new"]
        updated = [r for r in results if r["status"] == "updated"]
        no_change = [r for r in results if r["status"] == "no_change"]
        failed = [r for r in results if r["status"] == "failed"]
        total_added = sum(r.get("added", 0) for r in new_ok + updated)
        logger.info("=" * 60)
        logger.info("完成: 新番剧 %d 部, 更新 %d 部, 无变化 %d 部, 失败 %d 部, 共新增 %d 点",
                     len(new_ok), len(updated), len(no_change), len(failed), total_added)
        return results


def regenerate_index(base_dir: Path = BASE_DIR) -> int:
    """根据 pic/data/*/info.json + points.json 重新生成 index.json"""
    base_dir = Path(base_dir)
    index_path = base_dir / "index.json"

    # 读取已存在 index 以保留未被遍历到的条目
    existing_index = {}
    if index_path.exists():
        try:
            with open(index_path, "r", encoding="utf-8") as f:
                existing_index = json.load(f)
        except (json.JSONDecodeError, OSError):
            existing_index = {}

    index = {}
    count = 0
    for folder in sorted(base_dir.iterdir(), key=lambda p: _natural_key(p.name)):
        if not folder.is_dir() or not folder.name.isdigit():
            continue
        local_id = folder.name
        info_path = folder / "info.json"
        points_path = folder / "points.json"
        if not info_path.exists():
            continue
        try:
            with open(info_path, "r", encoding="utf-8") as f:
                info = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue

        # 读取 points
        points = []
        if points_path.exists():
            try:
                with open(points_path, "r", encoding="utf-8") as f:
                    pdata = json.load(f)
                if isinstance(pdata, list):
                    points = pdata
                elif isinstance(pdata, dict) and "points" in pdata:
                    points = pdata["points"]
            except (json.JSONDecodeError, OSError):
                pass

        anime_name = info.get("name", "") or info.get("title", "")
        anime_name_cn = info.get("name_cn", "") or info.get("cn", "")

        # 保留已存在的字段 (防止被空值覆盖)
        prev = existing_index.get(local_id, {})
        if not anime_name and prev.get("name"):
            anime_name = prev["name"]
        if not anime_name_cn and prev.get("name_cn"):
            anime_name_cn = prev["name_cn"]

        cover = info.get("cover", "") or prev.get("cover", "")
        theme_color = info.get("theme_color", "") or info.get("color", "") or prev.get("theme_color", "#7f6a95")

        index[local_id] = {
            "name": anime_name,
            "name_cn": anime_name_cn,
            "cover": cover,
            "theme_color": theme_color,
            "points": points,
            "inform": f"{LOCAL_IMG_PREFIX}/{local_id}/points.json",
        }
        count += 1

    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)

    # 同时写一份到根目录
    root_index = Path("index.json")
    try:
        with open(root_index, "w", encoding="utf-8") as f:
            json.dump(index, f, ensure_ascii=False, indent=2)
        logger.info("index.json 已重新生成 (data 目录 + 根目录), 共 %d 条", count)
    except OSError as e:
        logger.warning("写入根目录 index.json 失败: %s", e)
    return count


# ----------------------------------------------------------------------------
# 锁文件 (复用现有系统的锁, 防止与日/月更新器冲突)
# ----------------------------------------------------------------------------


def create_lock():
    try:
        with open(LOCK_FILE, "w") as f:
            f.write(str(time.strftime("%Y-%m-%d %H:%M:%S")))
        return True
    except OSError as e:
        logger.error("创建锁文件失败: %s", e)
        return False


def remove_lock():
    try:
        if os.path.exists(LOCK_FILE):
            os.remove(LOCK_FILE)
        return True
    except OSError as e:
        logger.error("移除锁文件失败: %s", e)
        return False


def is_other_updater_running() -> bool:
    return os.path.exists("anitabi_updater.lock")


# ----------------------------------------------------------------------------
# 工具函数
# ----------------------------------------------------------------------------


def _parse_ep(text: str):
    if not text:
        return None
    text = text.strip()
    m = re.match(r"^EP?(\d+)$", text, re.I)
    if m:
        return int(m.group(1))
    if text.upper() in ("OP", "ED"):
        return text.upper()
    try:
        return int(text)
    except ValueError:
        return text


def _parse_s(text: str):
    if not text:
        return None
    text = text.strip()
    # 归一化全角冒号 (DOM 中可能出现 2：09 这种全角冒号)
    text = text.replace("：", ":")
    # 形如 18:04 -> 秒数
    m = re.match(r"^(\d+):(\d+)$", text)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))
    try:
        return int(text)
    except ValueError:
        return None


def _natural_key(s: str):
    """自然排序键: '10' 排在 '2' 之后"""
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", s)]


# ----------------------------------------------------------------------------
# 主入口
# ----------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Anitabi 网页版巡礼点增量爬虫",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--local-id", type=str, help="只更新指定 local_id 的番剧")
    parser.add_argument("--batch", action="store_true", help="批量更新所有本地番剧 (逐个打开页面)")
    parser.add_argument("--discover", action="store_true", help="发现新番剧 + 增量更新已有番剧 (单次主页加载, 推荐)")
    parser.add_argument("--max-new", type=int, default=50, help="发现模式下每次最多新增多少部新番剧 (默认 50)")
    parser.add_argument("--dry-run", action="store_true", help="只扫描对比不下载图片不写入")
    parser.add_argument("--limit", type=int, help="批量模式下最多处理多少个")
    parser.add_argument("--delay", type=float, default=1.0, help="每个番剧之间的间隔秒数")
    parser.add_argument("--no-headless", action="store_true", help="显示浏览器窗口")
    parser.add_argument("--page-wait", type=int, default=5, help="页面渲染最小等待秒数 (之后轮询 mapApp)")
    parser.add_argument("--base-dir", default=str(BASE_DIR), help="数据目录")
    parser.add_argument("--chrome-path", help="Chrome 二进制路径")
    parser.add_argument("--driver-path", help="chromedriver 路径")
    parser.add_argument("--no-regenerate-index", action="store_true", help="跳过 index.json 重新生成")
    parser.add_argument("--no-lock", action="store_true", help="跳过锁文件检查 (调试用)")
    parser.add_argument("-v", "--verbose", action="store_true", help="详细日志")
    args = parser.parse_args()

    setup_logging(verbose=args.verbose)

    if not args.batch and not args.local_id and not args.discover:
        parser.error("请指定 --discover / --batch / --local-id 之一")

    base_dir = Path(args.base_dir)

    # 锁文件
    if not args.no_lock:
        if os.path.exists(LOCK_FILE):
            logger.error("锁文件已存在 (%s), 可能另一个更新器正在运行, 退出", LOCK_FILE)
            sys.exit(2)
        if is_other_updater_running():
            logger.warning("月度更新器正在运行 (anitabi_updater.lock), 仍然继续 (增量网页爬虫不写 apiid.json)")
        create_lock()

    scraper = AnitabiWebScraper(
        base_dir=base_dir,
        headless=not args.no_headless,
        page_wait=args.page_wait,
        chrome_path=args.chrome_path,
        driver_path=args.driver_path,
    )

    try:
        if args.discover:
            # 发现模式: 单次主页加载, 发现新番剧 + 增量更新已有
            results = scraper.discover_and_update(
                max_new=args.max_new,
                dry_run=args.dry_run,
                delay=args.delay,
            )
            new_ok = [r for r in results if r["status"] in ("new", "dry_run")]
            updated = [r for r in results if r["status"] == "updated"]
            no_change = [r for r in results if r["status"] == "no_change"]
            failed = [r for r in results if r["status"] == "failed"]
            total_added = sum(r.get("added", 0) for r in new_ok + updated)
            print("-" * 60)
            print(f"总计: {len(results)} 个番剧")
            print(f"  新增: {len(new_ok)} 部" + (" (dry-run)" if args.dry_run else ""))
            print(f"  更新: {len(updated)} 部 (新增 {total_added} 点)")
            print(f"  无变化: {len(no_change)} 部")
            print(f"  失败: {len(failed)} 部")
            if new_ok:
                print("  新增详情:")
                for r in new_ok:
                    print(f"    local_id={r['local_id']} bangumiId={r['bangumi_id']}: {r.get('message', '')}")
            if updated:
                print("  更新详情:")
                for r in updated:
                    print(f"    local_id={r['local_id']} bangumiId={r['bangumi_id']}: +{r['added']} (共 {r['total']})")
            if failed:
                print("  失败详情:")
                for r in failed:
                    print(f"    local_id={r.get('local_id')} bangumiId={r.get('bangumi_id')}: {r.get('message', '')}")
        elif args.local_id and not args.batch:
            # 单个番剧: 需要先拿到 bangumiId
            info = scraper.load_info(args.local_id)
            bid = info.get("id")
            if not bid:
                logger.error("local_id=%s 的 info.json 没有 id 字段", args.local_id)
                sys.exit(1)
            res = scraper.update_bangumi(int(bid), args.local_id, dry_run=args.dry_run)
            print(json.dumps(res, ensure_ascii=False, indent=2))
        else:
            results = scraper.batch_update(
                only_local_id=args.local_id if args.local_id else None,
                dry_run=args.dry_run,
                limit=args.limit,
                delay=args.delay,
            )
            # 汇总
            updated = [r for r in results if r["status"] == "updated"]
            no_change = [r for r in results if r["status"] == "no_change"]
            failed = [r for r in results if r["status"] in ("failed",)]
            total_added = sum(r.get("added", 0) for r in updated)
            print("-" * 60)
            print(f"总计: {len(results)} 个番剧")
            print(f"  更新: {len(updated)} 个 (新增 {total_added} 点)")
            print(f"  无变化: {len(no_change)} 个")
            print(f"  失败: {len(failed)} 个")
            if updated:
                print("  更新详情:")
                for r in updated:
                    print(f"    local_id={r['local_id']} bangumiId={r['bangumi_id']}: +{r['added']} (共 {r['total']})")
            if failed:
                print("  失败详情:")
                for r in failed:
                    print(f"    local_id={r['local_id']} bangumiId={r['bangumi_id']}: {r.get('message', '')}")

        # 重新生成 index.json
        if not args.no_regenerate_index and not args.dry_run:
            regenerate_index(base_dir)
    finally:
        scraper.quit()
        if not args.no_lock:
            remove_lock()


if __name__ == "__main__":
    main()
