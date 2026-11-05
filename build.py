"""山と雪と波の天気まとめ。Open-Meteo の予報を取得して public/ に静的なHTMLを書き出す。
使い方: python build.py  (GitHub Actions で3時間ごとに実行。Python標準ライブラリのみ)"""
import hashlib
import html
import json
import os
import shutil
import math
import re
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).parent
OUT = ROOT / "public"
JST = ZoneInfo("Asia/Tokyo")
DAYS = 16          # 取得する日数(APIの上限)。値のない日は表示しない
TOP_DAYS = 7       # トップページに出す日数
WEEK = "月火水木金土日"
e = html.escape


# ---------------------------------------------------------------- 取得
def fetch(url, params, retries=3):
    q = urllib.parse.urlencode(params)
    for i in range(retries):
        try:
            with urllib.request.urlopen(f"{url}?{q}", timeout=60) as r:
                d = json.load(r)
            return d if isinstance(d, list) else [d]
        except Exception:
            if i == retries - 1:
                raise
            time.sleep(10 * (i + 1))


def forecast(points, **params):
    """points: [(緯度, 経度, 標高)]。50地点ずつまとめて取得する"""
    out = []
    for i in range(0, len(points), 50):
        chunk = points[i:i + 50]
        try:
            out += fetch("https://api.open-meteo.com/v1/forecast", {
                "latitude": ",".join(str(p[0]) for p in chunk),
                "longitude": ",".join(str(p[1]) for p in chunk),
                "elevation": ",".join(str(p[2]) for p in chunk),
                "timezone": "Asia/Tokyo", "wind_speed_unit": "ms", "forecast_days": DAYS, **params})
        except Exception as ex:        # 取得できなかった地点は表示しない
            print(f"天気の取得に失敗({len(chunk)}地点): {type(ex).__name__}")
            out += [None] * len(chunk)
    return out


def marine(points, **params):
    out = []
    for lat, lon in points:
        try:
            out += fetch("https://marine-api.open-meteo.com/v1/marine", {
                "latitude": lat, "longitude": lon, "timezone": "Asia/Tokyo",
                "forecast_days": DAYS, **params})
        except Exception as ex:        # 取得できなかった地点は表示しない
            print(f"波の取得に失敗({lat}, {lon}): {type(ex).__name__}")
            out.append(None)
    return out


def align(items, what):
    """取得できなかった地点を除き、全地点で値のそろった日だけを残す(表に空欄を作らない)。
    1地点も取得できなければ例外にして、前回公開したサイトをそのまま残す"""
    items = [x for x in items if x.get("days")]
    if not items:
        raise RuntimeError(f"{what}の予報を1地点も取得できませんでした")
    common = set.intersection(*({d["date"] for d in x["days"]} for x in items))
    for x in items:
        x["days"] = [d for d in x["days"] if d["date"] in common]
    return items


def hours_of(h, key, day, hours):
    """hourly データから、指定日・指定時間帯の値だけを取り出す"""
    return [v for t, v in zip(h["time"], h[key])
            if t[:10] == day and int(t[11:13]) in hours and v is not None]


# ---------------------------------------------------------------- 警報・注意報、火山(気象庁)
# 警報・注意報の種類コード: (名前, レベル)。レベル20=注意報、30以上=警報(40=危険警報、50=特別警報)
WARN_KINDS = {
    "02": ("暴風雪警報", 30), "03": ("レベル3大雨警報", 30), "05": ("暴風警報", 30), "06": ("大雪警報", 30),
    "07": ("波浪警報", 30), "08": ("レベル3高潮警報", 30), "09": ("レベル3土砂災害警報", 30),
    "10": ("レベル2大雨注意報", 20), "12": ("大雪注意報", 20), "13": ("風雪注意報", 20), "14": ("雷注意報", 20),
    "15": ("強風注意報", 20), "16": ("波浪注意報", 20), "17": ("融雪注意報", 20), "19": ("レベル2高潮注意報", 20),
    "20": ("濃霧注意報", 20), "21": ("乾燥注意報", 20), "22": ("なだれ注意報", 20), "23": ("低温注意報", 20),
    "24": ("霜注意報", 20), "25": ("着氷注意報", 20), "26": ("着雪注意報", 20), "29": ("レベル2土砂災害注意報", 20),
    "30": ("レベル3氾濫警報", 30), "31": ("レベル3氾濫警報", 30), "32": ("暴風雪特別警報", 50),
    "33": ("レベル5大雨特別警報", 50), "35": ("暴風特別警報", 50), "36": ("大雪特別警報", 50), "37": ("波浪特別警報", 50),
    "38": ("レベル5高潮特別警報", 50), "39": ("レベル5土砂災害特別警報", 50), "40": ("レベル4氾濫危険警報", 40),
    "41": ("レベル4氾濫危険警報", 40), "43": ("レベル4大雨危険警報", 40), "48": ("レベル4高潮危険警報", 40),
    "49": ("レベル4土砂災害危険警報", 40), "51": ("レベル5氾濫特別警報", 50), "53": ("レベル5氾濫特別警報", 50),
}


def fetch_alerts():
    """いま出ている警報・注意報。{市町村(class20)のコード: [(名前, レベル)]}。取れなければ空"""
    try:
        reports = fetch("https://www.jma.go.jp/bosai/warning/data/r8/map.json", {})
    except Exception as ex:
        print(f"警報・注意報の取得に失敗: {type(ex).__name__}")
        return {}
    out = {}
    for r in sorted(reports, key=lambda r: r["reportDatetime"]):     # 同じ地域は新しい発表で上書き
        for a in r.get("warning", {}).get("class20Items", []):
            out[a["areaCode"]] = sorted({WARN_KINDS[k["code"]] for k in a["kinds"]
                                         if k.get("status") in ("発表", "継続") and k.get("code") in WARN_KINDS},
                                        key=lambda x: -x[1])
    return out


def fetch_volcanoes():
    """噴火警報・予報が出ている火山。[(緯度, 経度, 火山名, 警報の名前, 発表日)]。取れなければ空。
    一覧に載っていない火山は噴火警戒レベル1(または警報なし)"""
    try:
        places = {v["code"]: (float(v["latlon"][0]), float(v["latlon"][1]), v["name_jp"])
                  for v in fetch("https://www.jma.go.jp/bosai/volcano/const/volcano_list.json", {})
                  if v.get("latlon") and v.get("code")}     # 位置のない項目(海域など)は除く
        reports = fetch("https://www.jma.go.jp/bosai/volcano/data/warning.json", {})
    except Exception as ex:
        print(f"火山の情報の取得に失敗: {type(ex).__name__}")
        return []
    now = {}
    for r in sorted(reports, key=lambda r: r["reportDatetime"]):
        for vi in r["volcanoInfos"]:
            if vi["type"] != "噴火警報・予報（対象火山）":
                continue
            for it in vi["items"]:
                for a in it["areas"]:
                    now[a["code"]] = (it["name"], r["reportDatetime"][:10])
    now = {c: v for c, v in now.items() if "留意" not in v[0]}    # 「活火山であることに留意」(レベル1相当)は除く
    return [(*places[c][:2], places[c][2], name, day) for c, (name, day) in now.items() if c in places]


def km_between(lat1, lon1, lat2, lon2):
    return 6371 * math.hypot(math.radians(lon2 - lon1) * math.cos(math.radians(lat1)), math.radians(lat2 - lat1))


# ページごとに関係のない警報・注意報(名前に含まれる言葉)。山で波浪注意報などを出さないため
ALERT_SKIP = {
    "mountain": ("波浪", "高潮", "氾濫", "乾燥", "霜"),
    "ski": ("波浪", "高潮", "氾濫", "乾燥", "霜"),
    "surf": ("大雪", "なだれ", "低温", "霜", "乾燥", "着氷", "着雪", "融雪", "土砂災害", "氾濫"),
}


def attach_alerts(items, alerts, volcanoes, near_volcano, page):
    """各場所に、その市町村の警報・注意報(そのページに関係のあるもの)と、10km以内の火山の噴火警報を付ける"""
    skip = ALERT_SKIP[page]
    for x in items:
        muni = x.get("muni")
        found = {k for code, ks in alerts.items() if muni and code.startswith(muni) for k in ks
                 if not any(w in k[0] for w in skip)}
        x["alerts"] = sorted(found, key=lambda k: (-k[1], k[0]))
        x["volcano"] = None
        if near_volcano:
            close = [v for v in volcanoes if km_between(x["lat"], x["lon"], v[0], v[1]) <= 10]
            x["volcano"] = close[0] if close else None


def fid(kind, x):
    """お気に入りの目印。場所の追加で並び順が変わっても崩れないよう名前で作る(同名の雲取山は県で区別)"""
    return {"m": f"m:{x['name']}:{x['prefs'][0]}" if "prefs" in x else "", "s": f"s:{x['name']}", "w": f"w:{x['name']}"}[kind]


def star(kind, x):
    """詳細の一番上に出す「☆ お気に入り登録」ボタン(押すと登録済みに。登録は見ている人のブラウザに保存する)"""
    return (f"<button type='button' class='star' data-fid='{e(fid(kind, x))}' aria-pressed='false' "
            f"aria-label='{e(x['name'])}をお気に入りに登録'>♡ お気に入りに追加</button>")


def hutlink(m):
    """山小屋の予約先の一覧ボタン(data の booking)。予約サイトの小屋を山頂に近い順に、その後に公式HPの小屋を並べる。
    選ぶとその予約ページ・公式HPが開く"""
    if not m.get("booking") and not m.get("tent_booking"):
        return ""
    def item(b, icon=""):
        return (f"<a href='{e(b['url'])}' class='mapbtn' target='_blank' rel='noopener'>"
                f"{icon}{e(b['name'])}<small class='muted'>({e(b['site'])})</small></a>")
    # テント場は、予約先(Yamatan・小屋の予約先)が分かるものだけ ⛺ を付けて後ろに並べる
    items = "".join(item(b) for b in m.get("booking", [])) + "".join(item(b, "⛺ ") for b in m.get("tent_booking", []))
    return (f"<details class='menu'><summary class='mapbtn'>🏠 山小屋</summary>"
            f"<div class='menu-list'>{items}</div></details> ")


def intro(x):
    """詳細の一番上に出す一言紹介(data の intro)。山は最後に山小屋・テント場の数を印と数字だけで付ける
    (範囲と出典はページ下部の説明に)"""
    fac = "".join(f" <span class='fac' title='{label}'>{icon}{len(x[k])}</span>"
                  for k, icon, label in (("huts", "🏠", "山小屋"), ("tents", "⛺", "テント場")) if x.get(k))
    if x.get("shelters"):       # 避難小屋(無人・緊急時用)は営業小屋と見分けられるよう「避」のバッジで
        fac += (f" <span class='fac' title='避難小屋(緊急時用。多くは無人)' role='img' aria-label='避難小屋{len(x['shelters'])}か所'>"
                f"<span class='hinan'>避</span>{len(x['shelters'])}</span>")
    text = e(x.get("intro", ""))
    flower, photo = flower_text(x)
    return (f"<p class='intro'>{text}{flower}{fac}</p>{photo}" if text or fac else "")


def flower_text(x):
    """花の百名山の花「花の百名山:○○(○月ごろ)」と、花の名前を押すと開く写真(作者・ライセンス・元のページ付き)"""
    f = x.get("flower")
    if not f:
        return "", ""
    where = f"({e(f['where'])})" if f.get("where") else ""
    if not f.get("photo"):
        return f"。花の百名山{where}:{e(f['name'])}({e(f['season'])}ごろ)", ""
    c = f["credit"]
    lic = (f"<a href='{e(c['license_url'])}' target='_blank' rel='noopener'>{e(c['license'])}</a>"
           if c.get("license_url") else e(c["license"]))
    by = f"{e(c['author'])} · " if c.get("author") else ""
    return (f"。花の百名山{where}:<button type='button' class='flw' aria-expanded='false'>{e(f['name'])}</button>({e(f['season'])}ごろ)",
            f"<figure class='flwimg' hidden><img src='flowers/{e(f['photo'])}' alt='{e(f['name'])}の写真' loading='lazy'>"
            f"<figcaption>{e(f['name'])} 写真: {by}{lic} · "
            + (f"<a href='{e(c['src'])}' target='_blank' rel='noopener'>{e(c['site'])}</a>" if c.get("src") else e(c["site"]))
            + "</figcaption></figure>")


def alert_tags(x):
    """一覧の名前の横に付ける印(火山・警報・注意報)"""
    out = ""
    if x.get("volcano"):
        out += f" <span class='tag danger'>🌋 {e(x['volcano'][3].split('（')[0])}</span>"
    lv = max((k[1] for k in x.get("alerts", [])), default=0)
    if lv >= 30:
        out += " <span class='tag danger'>警報</span>"
    elif lv:
        out += " <span class='tag warn'>注意報</span>"
    return out


def alert_box(x):
    """詳細の上に出す、火山・警報・注意報の内容"""
    out = ""
    if x.get("volcano"):
        lat, lon, name, warn, day = x["volcano"]
        out += (f"<p class='alert'>🌋 {e(name)}: {e(warn)}"
                f"<small class='muted'>(気象庁 {int(day[5:7])}/{int(day[8:10])}発表)</small></p>")
    if x.get("alerts"):
        out += f"<p class='alert'>⚠️ 気象庁: {e('・'.join(k[0] for k in x['alerts']))}</p>"
    return out


# ---------------------------------------------------------------- 表示の部品
def wx(code):
    if code is None:
        return "", "—"
    if code == 0:
        return "☀️", "快晴"
    if code == 1:
        return "🌤️", "晴れ"
    if code == 2:
        return "⛅", "晴れ時々曇り"
    if code == 3:
        return "☁️", "曇り"
    if code in (45, 48):
        return "🌫️", "霧"
    if 51 <= code <= 57:
        return "🌦️", "霧雨"
    if 61 <= code <= 67 or 80 <= code <= 82:
        return "🌧️", "雨"
    if 71 <= code <= 77 or code in (85, 86):
        return "❄️", "雪"
    if code >= 95:
        return "⛈️", "雷雨"
    return "", "—"


DIRS = ["北", "北北東", "北東", "東北東", "東", "東南東", "南東", "南南東",
        "南", "南南西", "南西", "西南西", "西", "西北西", "北西", "北北西"]


def dir_name(deg):
    return DIRS[round(deg / 22.5) % 16] if deg is not None else "—"


def ang_diff(a, b):
    return abs((a - b + 180) % 360 - 180)


def mean_dir(degs, weights=None):
    import math
    weights = weights or [1] * len(degs)
    x = sum(w * math.cos(math.radians(d)) for d, w in zip(degs, weights))
    y = sum(w * math.sin(math.radians(d)) for d, w in zip(degs, weights))
    return (math.degrees(math.atan2(y, x)) + 360) % 360 if degs else None


RATE = {3: ("◎", "r3"), 2: ("○", "r2"), 1: ("△", "r1"), 0: ("×", "r0")}


BADGES = {"百名山": ("日本百名山", " hyaku"), "花の百名山": ("花の百名山", " hana")}    # それ以外(関西百名山など)は関西のバッジ
NO_BADGE = {"都道府県最高峰", "ヤマレコ人気", "追加"}       # バッジを付けない一覧


def tags(m):
    """入っている一覧の印(バッジ)。複数の一覧に入る山は全部付ける"""
    return "".join(f" <span class='mk{cls}' title='{t}' role='img' aria-label='{t}'></span>"
                   for t, cls in (BADGES.get(x, (x, "")) for x in m.get("lists", []) if x not in NO_BADGE))


def trailhead(m):
    """登山口の検索語。登録がなければ「山名 登山口 都道府県」で検索する(同名の山と区別するため)"""
    return m.get("trailhead") or f"{m['name']} 登山口 {m['prefs'][0]}"


def trailhead_label(m):
    """登山口ボタンの文字。検索語の頭の山名(「利尻山 鴛泊登山口」の「利尻山」)は外す。登録がなければ「登山口」"""
    th = m.get("trailhead")
    if not th:
        return "登山口"
    head, _, rest = th.partition(" ")
    if rest in ("登山口", "駐車場"):     # 「雪彦山 登山口」のように山名を外すと場所が分からなくなるもの
        return th
    return rest if rest and (head in (m["name"], m.get("yomi")) or head.endswith(("山", "岳", "峰", "嶽"))) else th


def sitelink(url, label, icon=""):
    """外部サイトへのボタン(新しいタブで開く)。icon は public/ に置いた画像のファイル名"""
    img = f"<img src='{icon}' alt='' width='14' height='14'>" if icon else ""
    return f"<a class='mapbtn' href='{e(url)}' target='_blank' rel='noopener'>{img}{e(label)}</a>"


def routelinks(m):
    """ヤマレコ・YAMAP の山のページへのボタン。番号が分からない山は出さない"""
    out = ""
    if m.get("yamareco"):
        out += sitelink(f"https://www.yamareco.com/modules/yamainfo/ptinfo.php?ptid={m['yamareco']}", "ヤマレコ", "yamareco.png") + " "
    if m.get("yamap"):
        out += sitelink(f"https://yamap.com/mountains/{m['yamap']}", "YAMAP", "yamap.ico") + " "
    return out


# ご当地の名物(Google マップで検索する言葉)。地域(中心・半径km)に入る場所はその名物、入らなければ県の名物。上から順に見る
FOOD_AREAS = [
    (45.18, 141.24, 25, "ウニ丼"),            # 利尻
    (44.05, 145.0, 45, "海鮮丼"),             # 知床(羅臼・斜里)
    (43.33, 142.38, 30, "オムカレー"),         # 富良野・美瑛
    (43.08, 142.85, 60, "豚丼"),              # 十勝(サホロ・トムラウシ・トマム)
    (43.70, 142.65, 35, "旭川ラーメン"),       # 旭川・大雪山・ぴっぷ
    (43.15, 140.95, 25, "寿司"),              # 小樽(キロロ・朝里)
    (43.00, 141.25, 25, "スープカレー"),       # 札幌(テイネ・国際・藻岩・ばんけい)
    (39.53, 141.49, 25, "ジンギスカン"),       # 遠野(早池峰)
    (38.17, 140.40, 8, "ジンギスカン"),        # 蔵王温泉
    (37.65, 140.02, 22, "喜多方ラーメン"),     # 裏磐梯・猪苗代
    (37.85, 140.10, 20, "米沢牛"),            # 米沢(吾妻・天元台)
    (36.78, 139.45, 15, "ゆば"),              # 日光
    (37.20, 139.45, 30, "ソースカツ丼"),       # 南会津(燧・会津駒・たかつえ)
    (37.10, 139.97, 20, "そば"),              # 那須
    (35.36, 138.73, 20, "吉田うどん"),         # 富士山
    (34.86, 139.05, 20, "わさび丼"),           # 天城山
    (36.29, 137.57, 6, "飛騨牛"),             # 新穂高(笠ヶ岳・焼岳)
    (36.25, 137.70, 25, "山賊焼"),            # 松本・上高地・乗鞍
    (36.15, 137.25, 25, "飛騨牛"),            # 飛騨高山・ほおのき平
    (35.73, 137.90, 18, "ソースかつ丼"),       # 駒ヶ根(木曽駒・空木)
    (35.95, 136.55, 15, "おろしそば"),         # 大野・勝山(荒島岳)
    (35.90, 136.88, 20, "鶏ちゃん"),           # 郡上(ダイナランド・高鷲・白鳥・めいほう)
    (36.57, 137.45, 18, "ます寿司"),           # 立山・剱・有峰
    (36.15, 136.75, 22, "治部煮"),             # 白山
    (35.50, 135.65, 30, "焼き鯖"),            # 若狭(小浜・高浜・難波江)
    (35.30, 135.90, 15, "鯖寿司"),            # 湖西・朽木(鯖街道)
    (35.55, 136.25, 22, "焼鯖そうめん"),       # 湖北(長浜・余呉・伊吹)
    (35.00, 136.45, 22, "とんてき"),           # 鈴鹿(御在所・鎌ヶ岳・藤原岳)
    (34.32, 136.85, 25, "海鮮丼"),             # 伊勢志摩(国府の浜)
    (34.70, 136.12, 10, "伊賀牛"),             # 伊賀
    (34.48, 136.35, 17, "松阪牛"),             # 松阪・奥伊勢(局ヶ岳)
    (35.46, 134.87, 12, "出石そば"),           # 出石(東床尾山)
    (35.65, 135.10, 25, "海鮮丼"),             # 丹後(八丁浜・依遅ヶ尾山・由良ヶ岳)
    (35.38, 134.55, 30, "但馬牛"),             # 但馬(ハチ北・ハチ高原・氷ノ山)
    (35.08, 135.22, 18, "ぼたん鍋"),           # 丹波篠山
    (34.72, 135.20, 15, "そばめし"),           # 神戸(六甲・摩耶)
    (35.00, 134.65, 35, "そうめん"),           # 播磨(揖保乃糸の産地。雪彦山・千ヶ峰・段ヶ峰)
    (34.25, 134.85, 20, "海鮮丼"),             # 淡路(諭鶴羽山)
    (34.53, 135.85, 8, "三輪そうめん"),        # 三輪山
    (34.22, 135.58, 15, "ごま豆腐"),           # 高野山(楊柳山・護摩壇山・伯母子岳)
    (33.75, 135.60, 45, "めはり寿司"),         # 熊野(法師山・大塔山・玉置山)
    (34.26, 135.09, 10, "しらす丼"),           # 加太・磯ノ浦
    (33.85, 134.10, 30, "祖谷そば"),           # 剣山
    (31.88, 130.88, 15, "黒豚"),              # 霧島
    (30.35, 130.52, 40, "トビウオ"),           # 屋久島
]
PREF_FOOD = {
    "北海道": "ジンギスカン", "青森県": "煮干しラーメン", "岩手県": "冷麺", "宮城県": "牛タン", "秋田県": "稲庭うどん",
    "山形県": "肉そば", "福島県": "ソースカツ丼", "茨城県": "そば", "栃木県": "ゆば", "群馬県": "うどん", "埼玉県": "わらじかつ丼",
    "神奈川県": "豆腐料理", "新潟県": "へぎそば", "富山県": "富山ブラック", "石川県": "治部煮", "福井県": "おろしそば",
    "山梨県": "ほうとう", "長野県": "そば", "岐阜県": "鶏ちゃん", "静岡県": "富士宮やきそば", "三重県": "伊勢うどん",
    "滋賀県": "近江ちゃんぽん", "京都府": "鯖寿司", "大阪府": "お好み焼き", "兵庫県": "そばめし", "奈良県": "柿の葉寿司",
    "和歌山県": "和歌山ラーメン", "鳥取県": "牛骨ラーメン", "広島県": "お好み焼き", "徳島県": "徳島ラーメン", "愛媛県": "鯛めし",
    "大分県": "とり天", "熊本県": "あか牛丼", "宮崎県": "チキン南蛮", "鹿児島県": "黒豚",
}


def local_food(lat, lon, pref):
    """その場所のご当地の名物。地域に入らず、県の名物も決めていなければ None"""
    for alat, alon, r, food in FOOD_AREAS:
        if 6371 * math.hypot(math.radians(lon - alon) * math.cos(math.radians(lat)), math.radians(lat - alat)) <= r:
            return food
    return PREF_FOOD.get(pref)


# 「飲食店」のジャンル(表示名, 検索する言葉)。ページごとに、よく使いそうなものを上に置く
# 飲食店のジャンル(どのページも同じ並び。ご当地はこの前に入る)
SHOP_GENRES = [("🍱 定食・食堂", "定食"), ("🍜 ラーメン", "ラーメン"), ("🍲 うどん・そば", "うどん そば"), ("🍣 海鮮", "海鮮"),
               ("🥩 焼肉", "焼肉"), ("🍽️ ファミレス", "ファミレス"), ("☕ カフェ", "カフェ"), ("🍰 スイーツ", "スイーツ")]


def foodlink(lat, lon, pref, page):
    """「飲食店」ボタン(押すとジャンルの一覧が開き、選ぶと Google マップでその地点の周りを探す)。
    その場所の名物(ご当地)があれば一覧の一番上に入れる"""
    food = local_food(lat, lon, pref)
    genres = ([(f"🏷️ {food}", food)] if food else []) + SHOP_GENRES
    items = "".join(
        f"<a href='{e(f'https://www.google.com/maps/search/{urllib.parse.quote(q)}/@{lat},{lon},13z')}' "
        f"class='mapbtn' target='_blank' rel='noopener'>{e(label)}</a>" for label, q in genres)
    return (f" <details class='menu'><summary class='mapbtn'>🍴 飲食店</summary>"
            f"<div class='menu-list'>{items}</div></details>")


def michilink(lat, lon):
    """その地点の周りの道の駅を Google マップで検索するボタン(道の駅は数が少ないので広めに探す)"""
    url = f"https://www.google.com/maps/search/{urllib.parse.quote('道の駅')}/@{lat},{lon},11z"
    return f" <a class='mapbtn' href='{e(url)}' target='_blank' rel='noopener'>🚗 道の駅</a>"


def conbilink(lat, lon):
    """その地点の周りのコンビニを Google マップで検索するボタン"""
    url = f"https://www.google.com/maps/search/{urllib.parse.quote('コンビニ')}/@{lat},{lon},13z"
    return f" <a class='mapbtn' href='{e(url)}' target='_blank' rel='noopener'>🏪 コンビニ</a>"


def onsenlink(lat, lon):
    """その地点の周りの日帰り温泉を Google マップで検索するボタン"""
    url = f"https://www.google.com/maps/search/{urllib.parse.quote('日帰り温泉')}/@{lat},{lon},12z"
    return f"<a class='mapbtn' href='{e(url)}' target='_blank' rel='noopener'>♨️ 温泉</a>"


def maplink(query, label):
    """Google マップの検索リンク(場所名で検索する。新しいタブで開く)"""
    url = "https://www.google.com/maps/search/?api=1&query=" + urllib.parse.quote(query)
    return (f"<a class='mapbtn' href='{e(url)}' target='_blank' rel='noopener' "
            f"title='{e(query)}'>📍 {e(label)}</a>")


_holidays = None


def holidays():
    """祝日(振替休日を含む)の日付の集合。内閣府の祝日一覧を1回だけ読む。取れなければ空(祝日の色を付けない)"""
    global _holidays
    if _holidays is None:
        _holidays = set()
        try:
            with urllib.request.urlopen("https://www8.cao.go.jp/chosei/shukujitsu/syukujitsu.csv", timeout=30) as r:
                for line in r.read().decode("cp932").splitlines()[1:]:
                    y, m, d = line.split(",")[0].split("/")
                    _holidays.add(date(int(y), int(m), int(d)))
        except Exception as ex:
            print(f"祝日一覧の取得に失敗: {type(ex).__name__}")
    return _holidays


def day_label(d):
    dt = date.fromisoformat(d)
    cls = "sat" if dt.weekday() == 5 else ("sun" if dt.weekday() == 6 or dt in holidays() else "")
    return f"<span class='{cls}'>{dt.month}/{dt.day}<small>({WEEK[dt.weekday()]})</small></span>"


def day_label2(d, first=False):
    """一覧の表の見出し用の短い日付(日と曜日の2段)。月は最初の列と月が変わる日だけ書く"""
    dt = date.fromisoformat(d)
    cls = "sat" if dt.weekday() == 5 else ("sun" if dt.weekday() == 6 or dt in holidays() else "")
    day = f"{dt.month}/{dt.day}" if first or dt.day == 1 else f"{dt.day}"
    return f"<span class='{cls}'>{day}<br>{WEEK[dt.weekday()]}</span>"


def weekend(days):
    """次の土日(今日が土日なら今日から)の日付"""
    return [d for d in days if date.fromisoformat(d).weekday() >= 5][:2]


# ---------------------------------------------------------------- 登山
# 風を見る気圧面とそのおよその高度(m)。山頂に最も近い面を使う
LEVELS = [(925, 760), (900, 1000), (850, 1500), (800, 1950), (700, 3000)]


def grid(m):
    """アンサンブル予報(GFS 0.5°)の格子。同じ格子の山は1回の取得にまとめる"""
    return round(m["lat"] * 2) / 2, round(m["lon"] * 2) / 2


def thunder_prob(mts):
    """雷の確率(%) = GFSアンサンブル31通りのうち、6〜15時に「大気が不安定(CAPE 1,000J/kg以上)で
    1時間0.5mm以上の雨」の時間がある割合。アンサンブルの天気コードは雷雨(95)を出さないため、この組み合わせで見る。
    {格子: {日付: %}}。取得できなかった格子は含めない"""
    cells = sorted({grid(m) for m in mts})
    out = {}
    for i in range(0, len(cells), 50):
        chunk = cells[i:i + 50]
        try:
            res = fetch("https://ensemble-api.open-meteo.com/v1/ensemble", {
                "latitude": ",".join(str(c[0]) for c in chunk),
                "longitude": ",".join(str(c[1]) for c in chunk),
                "models": "gfs05", "hourly": "cape,precipitation",   # 0.25°は10日分しかないため16日分ある0.5°
                "timezone": "Asia/Tokyo", "forecast_days": DAYS})
        except Exception as ex:
            print(f"雷の確率の取得に失敗({len(chunk)}地点): {type(ex).__name__}")
            continue
        for c, r in zip(chunk, res):
            h = r["hourly"]
            members = [k[4:] for k in h if k.startswith("cape")]   # "", "_member01", ...
            out[c] = {}
            for d in sorted({t[:10] for t in h["time"]}):
                idx = [i for i, t in enumerate(h["time"]) if t[:10] == d and 6 <= int(t[11:13]) <= 15]
                hits = n = 0
                for s in members:
                    pairs = [(h["cape" + s][i], h["precipitation" + s][i]) for i in idx]
                    if len(pairs) < 10 or any(a is None or b is None for a, b in pairs):
                        continue            # 値の欠けたメンバーは数えない
                    n += 1
                    hits += any(a >= 1000 and b >= 0.5 for a, b in pairs)
                if n:
                    out[c][d] = round(100 * hits / n)
    return out


# 日ごとに使う予報モデル: (モデル, 取得日数, 使う日(今日=0))。上から順に、6〜15時の値がそろったものを使う。
# てんきとくらすとの比較(2026-10-29)で近かった組み合わせ。どれも使えない日は Open-Meteo のおまかせ(best_match)
MT_MODELS = [("jma_msm", 3, range(0, 3)),
             ("ukmo_global_deterministic_10km", 6, range(3, 6)),
             ("jma_gsm", 11, range(6, 11))]


def wind_chill(t, v):
    """体感温度(℃)。気温 t(℃)と風速 v(m/s)から、カナダ気象局などが使う式で求める。
    気温が10℃を超えるときや風が弱いときは、気温のまま"""
    kmh = v * 3.6
    if t > 10 or kmh < 4.8:
        return t
    return 13.12 + 0.6215 * t - 11.37 * kmh ** 0.16 + 0.3965 * t * kmh ** 0.16


def by_hour(h, key, d, hours):
    """hourly データから、指定日・指定時間帯の {時: 値}(値のない時刻は除く)"""
    return {int(t[11:13]): v for t, v in zip(h["time"], h[key])
            if t[:10] == d and int(t[11:13]) in hours and v is not None}


VIEW = {3: "◎", 2: "○", 1: "△", 0: "×"}


def view_rate(h, d):
    """展望(見晴らし)の目安。6〜12時の雲の量の平均で ◎20%未満・○50%未満・△80%未満・×それ以上。
    霧の時間があれば ×。雲の量がそろわなければ None(列を出さない)"""
    if "cloud_cover" not in h:
        return None
    clouds = hours_of(h, "cloud_cover", d, range(6, 13))
    if len(clouds) < 7:
        return None
    if any(c in (45, 48) for c in hours_of(h, "weather_code", d, range(6, 13))):
        return 0
    avg = sum(clouds) / len(clouds)
    return 3 if avg < 20 else 2 if avg < 50 else 1 if avg < 80 else 0


def mt_day(h, d, elev, strict):
    """1日分(6〜15時)の山頂の天気。値が足りなければ None。strict なら10時間すべてそろった日だけ"""
    hours = range(6, 16)
    need = 10 if strict else 1
    rains = hours_of(h, "precipitation", d, hours)
    temps = hours_of(h, "temperature_2m", d, hours)
    codes = hours_of(h, "weather_code", d, hours)
    # 風は山頂に近い気圧面から順に、値のある面を使う(モデルによっては一部の面がない)
    winds = next((w for lv, _ in sorted(LEVELS, key=lambda x: abs(x[1] - elev))
                  if len(w := hours_of(h, f"wind_speed_{lv}hPa", d, hours)) >= need), None)
    if not winds or min(len(rains), len(temps), len(codes)) < need:
        return None
    # 体感温度: 同じ時刻の気温と風速の組から求めた、6〜15時で最も低い値
    lv = next(lv for lv, _ in sorted(LEVELS, key=lambda x: abs(x[1] - elev))
              if len(hours_of(h, f"wind_speed_{lv}hPa", d, hours)) >= need)
    th, wh = by_hour(h, "temperature_2m", d, hours), by_hour(h, f"wind_speed_{lv}hPa", d, hours)
    feels = [wind_chill(th[k], wh[k]) for k in th if k in wh]
    return {"rain": sum(rains), "wind": max(winds), "wind_avg": sum(winds) / len(winds),
            "tmin": min(temps), "tmax": max(temps), "tavg": sum(temps) / len(temps), "code": max(codes), "view": view_rate(h, d),
            "feel": min(feels) if feels else min(temps)}


def mt_hours(h, d, elev, step):
    """その日の時間ごとの山頂の天気(0〜23時)。step 時間ごとにまとめ、雨はその間の合計、風は最大、天気は最も悪いもの。
    値の欠けた時間は出さない"""
    near = [lv for lv, _ in sorted(LEVELS, key=lambda x: abs(x[1] - elev))]
    rows = []
    for start in range(0, 24, step):
        hrs = range(start, start + step)
        rains = hours_of(h, "precipitation", d, hrs)
        temps = hours_of(h, "temperature_2m", d, [start])
        codes = hours_of(h, "weather_code", d, hrs)
        winds = next((w for lv in near if len(w := hours_of(h, f"wind_speed_{lv}hPa", d, hrs)) == step), None)
        if len(rains) == step and len(codes) == step and temps and winds:
            cloud = hours_of(h, "cloud_cover", d, [start]) if "cloud_cover" in h else []
            rows.append({"hour": start, "code": max(codes), "temp": temps[0], "wind": max(winds), "rain": sum(rains),
                         "cloud": cloud[0] if cloud else None,
                         "feel": wind_chill(temps[0], max(winds))})
    return rows


# ---------------------------------------------------------------- 桜・紅葉の予想
# 山頂付近・中腹・麓の3か所で、気温から色づき・開花の日を予想する。式と係数は、気象庁の生物季節観測
# (全国約100地点、1991〜2025年のかえでの紅葉日・落葉日、さくらの開花日・満開日)とその地点の気温で決めた。
# 気温は Open-Meteo(過去92日と予報)。その前後は平年の気温(気象台の1991〜2020年の平均を標高で補正)に、
# 最近の平年との差を少しずつ弱めながら足して延ばす
CLIMATE = json.loads((ROOT / "data/climate.json").read_text(encoding="utf-8"))
LAPSE = 0.6                     # 標高100mあたりの気温の下がり方(℃)
OM_BIAS = (1.1, -0.1)           # Open-Meteo の最高・最低気温に足す値(気象台の実測との平均の差)
KOYO_CAP, SAKURA_CAP = 2400, 1500   # 紅葉は森林限界付近、桜はおおむね咲く高さまでを見る
KOYO = (20, 310)                # 紅葉日(見ごろの始め): 寒さの積算 Σmax(0, 基準 − 平均気温) の基準(℃)と、達する値(℃・日)
KOYO_SHIFT = -12                # 山の紅葉(ナナカマド・ブナなど)は街のかえでより早い。山の名所35か所の例年の見ごろとの平均の差
KOYO_LEN = 15                   # 見ごろの長さ(山の名所の例年の見ごろの中央値)
KOYO_EARLY = 10                 # 見ごろの前この日数を「色づき始め」とする
SAKURA = {"somei": {"bloom": (46, 520), "full": (60, 500)},       # 1月1日からの日数・最高気温の積算(℃)
          "ezo": {"bloom": (75, 460), "full": (75, 500)}}         # 北海道の山はエゾヤマザクラ
def sakura_rule(c):
    """説明用「2月15日からの最高気温の合計が520℃で開花、3月1日からの合計が500℃で満開」"""
    md = lambda n: (lambda d: f"{d.month}月{d.day}日")(date(2001, 1, 1) + timedelta(days=n - 1))
    return f"{md(c['bloom'][0])}からの最高気温の合計が{c['bloom'][1]}℃で開花、{md(c['full'][0])}からの合計が{c['full'][1]}℃で満開"
ZONES = (("top", "山頂付近"), ("mid", "中腹"), ("base", "麓"))


def zones(m, cap):
    """[(キー, 名前, 標高)]。山頂と麓の差が300m未満なら1か所。cap より高いところは cap で見る(山頂付近の名前も変える)"""
    top, base = min(m["elev"], cap), m.get("base", m["elev"])
    if base >= cap:
        return []
    name = "山頂付近" if m["elev"] <= cap else f"標高{cap:,}m付近"
    if top - base < 300:
        return [("top", name, top)]
    return [("top", name, top), ("mid", "中腹", round((top + base) / 2, -1)), ("base", "麓", base)]


def normals(m, elev):
    """その山・標高の平年の {MM-DD: (最高, 最低)}。近い気象台3地点の平年値を距離で重み付けし、標高で補正する"""
    near = sorted(CLIMATE, key=lambda c: (c["lat"] - m["lat"]) ** 2 + ((c["lon"] - m["lon"]) * math.cos(math.radians(m["lat"]))) ** 2)[:3]
    w = [1 / max(0.05, math.hypot(c["lat"] - m["lat"], (c["lon"] - m["lon"]) * math.cos(math.radians(m["lat"])))) for c in near]
    out = {}
    for k in near[0]["tmax"]:
        if all(k in c["tmax"] for c in near):
            out[k] = tuple(sum(wi * (c[v][k] - LAPSE / 100 * (elev - c["alt"])) for wi, c in zip(w, near)) / sum(w)
                           for v in ("tmax", "tmin"))
    return out


def temp_series(r, m, ref, elev, first, last):
    """first〜last の日ごとの (日付, 最高, 最低)。elev の標高での値。r は ref の標高で取った Open-Meteo の日別値"""
    nm = normals(m, elev)
    got = {}
    if r:
        dl = r["daily"]
        for x, hi, lo in zip(dl["time"], dl["temperature_2m_max"], dl["temperature_2m_min"]):
            if hi is not None and lo is not None:
                got[date.fromisoformat(x)] = (hi + OM_BIAS[0] - LAPSE / 100 * (elev - ref), lo + OM_BIAS[1] - LAPSE / 100 * (elev - ref))
    # 平年との差: 取得できた最後の14日の平均。その先は15日ほどで弱まるとする
    tail = sorted(got)[-14:]
    anom = [sum(got[d][i] - nm[d.strftime("%m-%d")][i] for d in tail if d.strftime("%m-%d") in nm) / max(1, len(tail))
            for i in (0, 1)] if tail else [0, 0]
    out, d = [], first
    while d <= last:
        k = d.strftime("%m-%d") if d.strftime("%m-%d") != "02-29" else "02-28"
        if d in got:
            out.append((d, *got[d]))
        elif k in nm:
            fade = math.exp(-(d - tail[-1]).days / 15) if tail and d > tail[-1] else 0
            out.append((d, nm[k][0] + anom[0] * fade, nm[k][1] + anom[1] * fade))
        d += timedelta(days=1)
    return out


def cold_day(s, tb, f):
    """寒さの積算 Σmax(0, tb − 平均気温) が f に達した日"""
    tot = 0
    for d, hi, lo in s:
        tot += max(0, tb - (hi + lo) / 2)
        if tot >= f:
            return d
    return None


def warm_day(s, d0, f):
    """1月1日から d0 日目以降の最高気温(0℃未満は0)の積算が f に達した日"""
    tot = 0
    for d, hi, lo in s:
        if d.month < 7 and d.timetuple().tm_yday >= d0:
            tot += max(0, hi)
            if tot >= f:
                return d
    return None


def seasons(mts):
    """山ごとの桜・紅葉の予想 {"koyo"/"sakura": [{key, name, elev, early, peak, end}]}(early〜peak が色づき始め・咲き始め、
    peak〜end が見ごろ)。紅葉は9〜12月、桜は2〜6月だけ計算する"""
    today = datetime.now(JST).date()
    kind = "koyo" if today.month >= 9 else "sakura" if 2 <= today.month <= 6 else None
    if not kind:
        return [{} for _ in mts]
    cap = KOYO_CAP if kind == "koyo" else SAKURA_CAP
    res = forecast([(m["lat"], m["lon"], min(m["elev"], cap)) for m in mts],
                   daily="temperature_2m_max,temperature_2m_min", past_days=92)
    out = []
    for m, r in zip(mts, res):
        ref, zs = min(m["elev"], cap), []
        for key, name, elev in zones(m, cap):
            if kind == "koyo":
                s = temp_series(r, m, ref, elev, date(today.year, 9, 1), date(today.year, 12, 31))
                peak = cold_day(s, *KOYO)
                peak = peak and peak + timedelta(days=KOYO_SHIFT)
                early, end = peak and peak - timedelta(days=KOYO_EARLY), peak and peak + timedelta(days=KOYO_LEN)
            else:
                s = temp_series(r, m, ref, elev, date(today.year, 1, 1), date(today.year, 6, 30))
                c = SAKURA["ezo" if m["region"] == "北海道" else "somei"]
                early, full = warm_day(s, *c["bloom"]), warm_day(s, *c["full"])
                peak = full and full - timedelta(days=2)
                end = full and full + timedelta(days=5)
                if early and peak and peak < early:
                    peak = early
            if peak and early:
                zs.append({"key": key, "name": name, "elev": elev, "early": early, "peak": peak, "end": end})
        out.append({kind: zs} if zs else {})
    return out


def season_state(z, d):
    """その日の状態: 1(色づき始め・咲き始め) / 2(見ごろ) / 3(見ごろ過ぎ) / None(まだ)"""
    d = date.fromisoformat(d) if isinstance(d, str) else d
    return 3 if d >= z["end"] else 2 if d >= z["peak"] else 1 if d >= z["early"] else None


# 季節の見ごろ: キー → (印, 名前, 見ごろ前の言い方)
SEASONS = {"sakura": ("🌸", "桜", "咲き始め"), "koyo": ("🍁", "紅葉", "色づき始め")}


def in_bloom(m, dates):
    """花の百名山の花の開花時期に、指定した日のどれかが入るか。時期は旬(from/to: [月, 0上旬/1中旬/2下旬])、
    なければ月(months)で見る"""
    f = m.get("flower", {})
    if "from" not in f:
        return any(int(d[5:7]) in f.get("months", []) for d in dates)
    a, b = (f["from"][0], 1 + 10 * f["from"][1]), (f["to"][0], 10 + 10 * f["to"][1] if f["to"][1] < 2 else 31)
    def inside(d):
        x = (int(d[5:7]), int(d[8:10]))
        return a <= x <= b if a <= b else x >= a or x <= b
    return any(inside(d) for d in dates)


def best_spot(zs, d):
    """その日にいちばん見ごろの場所の名前(見ごろの真ん中に近い所)。山頂付近・中腹・麓のどこも見ごろでなくても、
    2か所の見ごろの間の日なら、見ごろは高さとともに連続して移るので、その日が見ごろの真ん中になる間の標高を返す。なければ None"""
    d = date.fromisoformat(d) if isinstance(d, str) else d
    mid = lambda z: z["peak"].toordinal() + (z["end"] - z["peak"]).days / 2
    now = [z for z in zs if season_state(z, d) == 2]
    if now:
        return min(now, key=lambda z: abs(d.toordinal() - mid(z)))["name"]
    by_peak = sorted(zs, key=lambda z: z["peak"])
    for a, b in zip(by_peak, by_peak[1:]):
        if a["end"] <= d < b["peak"]:
            f = min(1, max(0, (d.toordinal() - mid(a)) / max(1, mid(b) - mid(a))))
            return f"標高{round(a['elev'] + (b['elev'] - a['elev']) * f, -2):,.0f}m付近"
    return None


def season_mark(m, dates, flower=True):
    """指定した日のどれかで桜・紅葉が見ごろ(予想)なら 🌸・🍁(どこが見ごろかを title に)、
    花の百名山の花の時期なら 🌼(flower=False で出さない)"""
    out = ""
    for key, (icon, name, _) in SEASONS.items():
        zs = m.get("season", {}).get(key, [])
        where = [z["name"] for z in zs if any(season_state(z, d) == 2 for d in dates)]
        if not where:           # 2か所の見ごろの間の日は、間の標高が見ごろ
            where = list(dict.fromkeys(x for d in dates if (x := best_spot(zs, d))))
        if where:
            t = f"{name}の見ごろ(予想): {'・'.join(where)}"
            out += f" <span class='fac' title='{t}' role='img' aria-label='{t}'>{icon}</span>"
    if flower and in_bloom(m, dates):
        name = e(m["flower"]["name"])
        out += f" <span class='fac' title='{name}の花の時期' role='img' aria-label='{name}の花の時期'>🌼</span>"
    return out


def season_line(m):
    """詳細に出す桜・紅葉の予想(山頂付近・中腹・麓ごと)。見ごろが近いか、見ごろの所があるときだけ"""
    md = lambda x: f"{x.month}/{x.day}"
    today = date.fromisoformat(m["days"][0]["date"]) if m["days"] else datetime.now(JST).date()
    lines = ""
    for key, (icon, name, early) in SEASONS.items():
        zs = m.get("season", {}).get(key, [])
        if not zs or not any(season_state(z, today) in (1, 2) or 0 < (z["peak"] - today).days <= 30 for z in zs):
            continue
        parts = []
        for z in zs:
            st = season_state(z, today)
            text = (f"{icon}見ごろ({md(z['end'] - timedelta(days=1))}頃まで)" if st == 2 else
                    f"{early}({md(z['peak'])}頃から見ごろ)" if st == 1 else
                    "見ごろ過ぎ" if st == 3 else f"{md(z['peak'])}頃から見ごろ")
            parts.append(f"{z['name']} {text}")
        lines += f"<p class='intro'>{icon} {name}(予想): {' / '.join(parts)}</p>"
    days = [d["date"] for d in m["days"]]
    if in_bloom(m, days[:1]):
        lines += f"<p class='intro'>🌼 {e(m['flower']['name'])}の花の時期です({e(m['flower']['season'])}ごろ)</p>"
    elif in_bloom(m, days):
        lines += f"<p class='intro'>🌼 {e(m['flower']['name'])}の花の時期が近づいています({e(m['flower']['season'])}ごろ)</p>"
    return lines


def build_mountains():
    mts = json.loads((ROOT / "data/mountains.json").read_text(encoding="utf-8"))
    points = [(m["lat"], m["lon"], m["elev"]) for m in mts]
    hourly = ",".join(["temperature_2m", "precipitation", "weather_code", "cloud_cover"]
                      + [f"wind_speed_{p}hPa" for p, _ in LEVELS])
    res = forecast(points, hourly=hourly + ",snow_depth", daily="sunrise,sunset")   # 積雪はおまかせのモデルだけで見る
    extra = [forecast(points, hourly=hourly, models=model, forecast_days=n) for model, n, _ in MT_MODELS]
    probs = thunder_prob(mts)
    sea = seasons(mts)
    today = datetime.now(JST).date()
    for i, (m, r) in enumerate(zip(mts, res)):
        m["days"], m["season"] = [], sea[i]
        if r is None:
            continue
        prob = probs.get(grid(m), {})
        dl = r.get("daily", {})
        if str(today) in dl.get("time", []):    # 本日の日の出・日の入り(山頂ではなく平地の地平線での時刻)
            k = dl["time"].index(str(today))
            if dl["sunrise"][k] and dl["sunset"][k]:
                m["sun"] = (dl["sunrise"][k][11:16], dl["sunset"][k][11:16])
        for d in sorted({t[:10] for t in r["hourly"]["time"]}):
            lead = (date.fromisoformat(d) - today).days
            day = None
            for (_, _, use), ex in zip(MT_MODELS, extra):
                if lead in use and ex[i] is not None and (day := mt_day(ex[i]["hourly"], d, m["elev"], True)):
                    src = ex[i]["hourly"]
                    break
            if not day:
                day = mt_day(r["hourly"], d, m["elev"], False)
                if not day:
                    continue            # 値のない日は表示しない
                src = r["hourly"]
            # 日付を押すと開く時間ごとの予報: 今日は1時間ごと、明日〜5日後は3時間ごと
            day["step"] = 1 if lead <= 0 else 3
            day["hours"] = mt_hours(src, d, m["elev"], day["step"]) if lead <= 5 else []
            rain, wind = day["rain"], day["wind"]
            thunder = prob.get(d)       # 雷の確率(%)。取得できなかった日は None
            if rain >= 10 or wind >= 17 or (thunder or 0) >= 50:
                score = 0
            elif rain >= 3 or wind >= 12:
                score = 1
            elif rain >= 0.5 or wind >= 10:
                score = 2
            else:
                score = 3
            if (thunder or 0) >= 20:
                score = min(score, 1)
            # 登山道の凍結注意: 最低気温0℃以下で、前日昼〜当日15時に雨・雪が0.5mm以上(濡れた道や雪が凍る)、
            # または最低気温-3℃以下(雨がなくても霜や前の雪が凍りやすい)
            prev = (date.fromisoformat(d) - timedelta(days=1)).isoformat()
            wet = (sum(hours_of(r["hourly"], "precipitation", prev, range(12, 24)))
                   + sum(hours_of(r["hourly"], "precipitation", d, range(0, 16))))
            day["ice"] = day["tmin"] <= 0 and (wet >= 0.5 or day["tmin"] <= -3)
            # 山頂付近の積雪: 6〜15時の積雪深の予報が5cm以上なら ❄️(格子が粗く山では実際とずれやすいので目安)
            depth = hours_of(r["hourly"], "snow_depth", d, range(6, 16)) if "snow_depth" in r["hourly"] else []
            day["snowy"] = bool(depth) and max(depth) >= 0.05
            if day["snowy"] or day["ice"]:      # 雪山登山には使わないので、積雪・凍結の日は一律 ×
                score = 0
            # 体感温度(6〜15時の最低): 濡れた体は風で冷えるので、0℃より上でも厳しくする
            f = day["feel"]
            score = 0 if f < -5 else min(score, 1) if f < 0 else min(score, 2) if f < 5 else score
            m["days"].append({"date": d, "score": score, "thunder": thunder, **day})
    return align(mts, "登山")


def mt_detail(m, with_map=True, compact=False):
    """山の日ごとの詳細(登山口の地図ボタン付き)。判定は日付のすぐ右。
    compact=True はトップページ用で、最大風速と雷の確率を出さない"""
    has_t = not compact and all(d["thunder"] is not None for d in m["days"])   # 雷の確率がない日があれば列ごと出さない
    has_v = all(d.get("view") is not None for d in m["days"])                  # 展望も同じ
    rows = ""
    for d in m["days"]:
        hours = d.get("hours")
        label = (f"<button type='button' class='hbtn' aria-expanded='false'>{day_label(d['date'])}</button>"
                 if hours else day_label(d["date"]))
        label += season_mark(m, [d["date"]], flower=False)    # 花の時期は月単位なので日ごとには付けない
        rows += (f"<tr><td class='dt'>{label}</td>"
                 f"<td class='{RATE[d['score']][1]}'><b>{RATE[d['score']][0]}</b></td>"
                 f"<td>{wx(d['code'])[0]} {wx(d['code'])[1]}</td>"
                 + (f"<td class='{RATE[d['view']][1]}'>{VIEW[d['view']]}</td>" if has_v else "")
                 + f"<td>{round(d['tmin'])}〜{round(d['tmax'])}℃"
                 + (" <span class='fac' title='凍結注意' role='img' aria-label='凍結注意'>🧊</span>" if d.get("ice") and not compact else "")
                 + (" <span class='fac' title='積雪あり(山頂付近の予報で5cm以上)' role='img' aria-label='積雪あり'>❄️</span>" if d.get("snowy") and not compact else "") + "</td>"
                 + ("" if compact else f"<td>{round(d['feel'])}℃</td>")
                 + f"<td>{d['wind_avg']:.0f}m/s</td>"
                 + ("" if compact else f"<td>{d['wind']:.0f}m/s</td>")
                 + f"<td>{d['rain']:.1f}mm</td>" + (f"<td>{d['thunder']}%</td>" if has_t else "") + "</tr>")
        for x in hours or []:
            when = f"{x['hour']}時" if d["step"] == 1 else f"{x['hour']}〜{x['hour'] + 3}時"
            # 時間ごとの行: 判定・雷は空ける。風速は平均・最大の2列にまたがって出す
            rows += (f"<tr class='hr' hidden><td class='dt'>{when}</td><td></td>"
                     f"<td>{wx(x['code'])[0]} {wx(x['code'])[1]}</td>"
                     + (f"<td>雲{x['cloud']:.0f}%</td>" if has_v and x.get("cloud") is not None else "<td></td>" if has_v else "")
                     + f"<td>{round(x['temp'])}℃</td>"
                     + ("" if compact else f"<td>{round(x['feel'])}℃</td>")
                     + f"<td colspan='{1 if compact else 2}'>{x['wind']:.0f}m/s</td>"
                     f"<td>{x['rain']:.1f}mm</td>" + ("<td></td>" if has_t else "") + "</tr>")
    if not m.get("access"):
        place = maplink(trailhead(m), trailhead_label(m))
    else:                       # YAMAP の駐車場(なければ登山口・駅)の位置を示す。文字は登録した登山口の名前を優先
        a = m["access"]
        label = (trailhead_label(m) if m.get("trailhead") else
                 a["name"] if a["name"] not in ("駐車場", "有料駐車場", "登山者駐車場") else f"{m['name']} {a['name']}")
        place = maplink(f"{a['lat']},{a['lon']}", label)
    if m.get("accesses"):       # YAMAP・ヤマレコに登山口が複数あれば、飲食店と同じく押すと一覧が開く(各登山口の近くの駐車場の位置)
        items = "".join(maplink(x.get("q") or f"{x['lat']},{x['lon']}", x["name"]) for x in m["accesses"])
        place = (f"<details class='menu'><summary class='mapbtn'>📍 登山口</summary>"
                 f"<div class='menu-list'>{items}</div></details>")
    near = m.get("access") or m            # 温泉は駐車場・登山口の位置(分からなければ山頂)の周りで探す
    link = (routelinks(m) + place + " " + onsenlink(near["lat"], near["lon"])
            + foodlink(near["lat"], near["lon"], m["prefs"][0], "mountain") + michilink(near["lat"], near["lon"])
            + conbilink(near["lat"], near["lon"]) + " " + hutlink(m)) if with_map else ""    # 山小屋は右端
    sun = (f"<p class='intro'>本日の日の出 {m['sun'][0].lstrip('0')} ／ 日の入り {m['sun'][1]}</p>"
           if m.get("sun") else "")
    # スキー場と同じく、文字をまとめて上に、ボタン(お気に入り登録が先頭)をその下に
    buttons = f"<div class='btnrow'>{star('m', m)} {link}</div>" if with_map else ""
    return (f"{alert_box(m)}{intro(m)}{sun}{season_line(m)}{buttons}<div class='scroll'><table class='sub'>"
            "<tr><th class='dt'>日付</th><th>判定</th><th>天気(6〜15時)</th>"
            + ("<th>展望</th>" if has_v else "") + "<th>山頂付近の気温</th>"
            + ("" if compact else "<th>体感温度</th>") + "<th>平均風速</th>"
            + ("" if compact else "<th>最大風速</th>")
            + f"<th>降水量</th>{'<th>雷の確率</th>' if has_t else ''}</tr>{rows}</table></div>")





SPOT_PAGES = {}                 # スポットごとの詳細ページ: ファイル名 → (タイトル, 本文, 一覧のページ)


def spot_url(kind, x):
    """スポットの詳細ページのファイル名。お気に入りの目印(名前で作る)から作るので、データの並びが変わっても変わらない"""
    return f"{kind}-{hashlib.sha1(fid(kind, x).encode()).hexdigest()[:8]}.html"


def day_info(days):
    """一覧の日付のボタン用: [{d: 日付, l: 11/8, w: 土, c: sat/sun/''}]"""
    out = []
    for d in days:
        dt = date.fromisoformat(d)
        out.append({"d": d, "l": f"{dt.month}/{dt.day}", "w": WEEK[dt.weekday()],
                    "c": "sat" if dt.weekday() == 5 else ("sun" if dt.weekday() == 6 or dt in holidays() else "")})
    return out


def season_flags(m, d):
    """その日の季節の印(一覧用): k=紅葉の見ごろ s=桜の見ごろ f=花の百名山の花の時期。判定は season_mark と同じ"""
    out = ""
    for key, ch in (("koyo", "k"), ("sakura", "s")):
        zs = m.get("season", {}).get(key, [])
        if any(season_state(z, d) == 2 for z in zs) or best_spot(zs, d):
            out += ch
    if m.get("flower") and in_bloom(m, [d]):
        out += "f"
    return out


def spots_json(data):
    """一覧のデータを、ページに埋め込む JSON にする(</script> で切れないように)"""
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")


def list_controls(placeholder, regions, subregions, filters, sorts):
    """3ジャンル共通の一覧の操作部分(仕様書 4): 切替・検索・日付・地域・絞り込み・件数と並び順・一覧"""
    chip = lambda group, v, label, on=False: (f"<button type='button' class='chip{' on' if on else ''}' data-g='{group}' "
                                              f"data-v='{e(v)}'>{label}</button>")
    tabs = "".join(f"<button type='button' class='tab' role='tab' data-tab='{v}' aria-selected='false'>{label}</button>"
                   for v, label in (("rec", "おすすめ"), ("fav", "お気に入り"), ("all", "全スポット")))
    reg = "".join(chip("region", v, label, v == "") for v, label in regions)
    sub = "".join(chip("region", v, label) for v, label in subregions)
    flt = "".join(f"<div class='fgroup'><span class='flabel'>{label}</span><div class='chips wrap'>"
                  + "".join(chip("f:" + key, v, l, v == "") for v, l in opts) + "</div></div>"
                  for key, label, opts in filters)
    srt = "".join(f"<option value='{v}'>{label}</option>" for v, label in sorts)
    return f"""<div class='tabs' role='tablist'>{tabs}</div>
<input id='q' type='search' placeholder='{placeholder}' aria-label='{placeholder}' autocomplete='off'>
<div class='dates' id='dates' aria-label='日付'></div>
<div class='chips wrap regions' id='regions' aria-label='地域'>{reg}</div>
<div class='chips wrap regions sub' id='subregions' hidden>{sub}</div>
<div class='frow'><button type='button' id='ftoggle' class='fbtn' aria-expanded='false'>絞り込み</button><div id='active' class='active'></div></div>
<div id='fpanel' class='fpanel' hidden>{flt}</div>
<div class='listbar'><span id='count'></span><label class='sortl'><span>並び順</span><select id='sort'>{srt}</select></label></div>
<div id='spots' class='spots'></div>
<p id='empty' class='empty' hidden>条件に合うスポットがありません。絞り込み条件を変更してください。</p>"""


def genre_head(icon, name, desc):
    """ジャンルの見出し(更新時刻は layout が見出しのすぐ後ろに入れる)"""
    return f"<div class='ghead'><h1><span class='gicon' aria-hidden='true'>{icon}</span>{name}</h1></div><p class='gdesc2'>{desc}</p>"


def mountains_page(mts):
    days = [d["date"] for d in mts[0]["days"]]
    items = []
    for m in mts:
        url = spot_url("m", m)
        lists = [c for c, key in (("h", "百名山"), ("k", "関西百名山"), ("a", "花の百名山")) if key in m.get("lists", [])]
        items.append({"k": m["key"], "f": fid("m", m), "n": m["name"], "y": m["yomi"], "r": m["region"], "p": m["prefs"],
                      "e": m["elev"], "l": lists, "u": url, "b": tags(m) + alert_tags(m),
                      "d": [[x["score"], wx(x["code"])[0], round(x["tavg"]), round(x["wind"]), season_flags(m, x["date"])]
                            for x in m["days"]]})
        back = "<p class='back'><a href='mountains.html'><svg class='chev' viewBox='0 0 24 24' aria-hidden='true'><path d='M15 5l-7 7 7 7' fill='none' stroke='currentColor' stroke-width='2.5' stroke-linecap='round' stroke-linejoin='round'/></svg>登山の一覧</a></p>"
        SPOT_PAGES[url] = (f"{m['name']}の登山天気",
                           f"{back}<div class='ghead'><h1 class='spoth'>{e(m['name'])}{tags(m)}{alert_tags(m)}</h1>"
                           f"<p class='spotsub'>{m['elev']:,}m · {e('・'.join(m['prefs']))}</p></div>{mt_detail(m)}",
                           "mountains.html")
    regions = ([("", "全国")] + [(r, r) for r in ("北海道", "東北", "関東", "上信越", "北アルプス", "南アルプス", "中部")]
               + [("近畿", "関西"), ("中国・四国|九州", "中四国・九州")])
    kinki = ["滋賀県", "京都府", "大阪府", "兵庫県", "奈良県", "和歌山県", "三重県", "福井県"]   # 関西を押すと出す
    filters = [("cls", "分類", [("", "すべて"), ("h", "日本100名山"), ("k", "関西100名山"), ("a", "花の100名山")]),
               ("rate", "評価", [("", "指定なし"), ("2", "◎○のみ"), ("3", "◎のみ")])]
    sorts = [("score", "総合評価順"), ("elev", "標高順"), ("name", "山名順")]
    data = {"kind": "m", "days": day_info(days), "we": weekend(days), "items": items}
    return (genre_head("⛰️", "登山", "山頂の高さの予報(6〜15時)から、登山に向いた日かを ◎○△× で判定します")
            + list_controls("山名で探す(例: 槍、ふじ)", regions, [(f"pref:{x}", x.rstrip("県府")) for x in kinki],
                            filters, sorts)
            + f"<script type='application/json' id='spots-data'>{spots_json(data)}</script>\n"
            + f"""
<details class='note'><summary>判定の基準・データについて</summary><h2>判定の基準</h2><ul>
<li><b class='r3'>◎</b> 降水0.5mm未満、風速10m/s未満</li>
<li><b class='r2'>○</b> 降水3mm未満、風速12m/s未満</li>
<li><b class='r1'>△</b> 降水10mm未満、風速17m/s未満</li>
<li><b class='r0'>×</b> それ以上、または雷の確率50%以上。山頂付近の積雪(5cm以上)や登山道の凍結が予想される日も一律 ×(雪山登山は対象外)</li>
<li>体感温度(山頂付近の6〜15時の最低): 5℃未満は最高でも○、0℃未満は最高でも△、-5℃未満は ×(汗や雨で濡れた体は風で冷え、0℃より上でも低体温症になることがあるため)</li></ul>
<p>雷の確率: 米国の予報モデル(GFS)を条件を少しずつ変えて31通り計算したうち、6〜15時に「大気が不安定で雨が降る」時間がある割合です。20%以上で判定は最高でも△、50%以上で×。山では早めの下山を。</p>
<p>🏠 山小屋: 山頂から4km以内の、山中の山小屋と登山口にある小屋(ヤマレコの登録をもとに、温泉旅館・休憩所・廃業した小屋・避難小屋は除く)。⛺ テント場: 山小屋併設のテント場と、管理棟のあるテント場(剱沢・雷鳥沢など)、避難小屋の横のテント場(登山口のキャンプ場や、小屋も管理棟もない野営指定地は含めません)。<span class='hinan'>避</span> 避難小屋: 山頂から4km以内の避難小屋の数(ヤマレコの登録)。緊急時に使うためのもので、多くは無人です(夏だけ管理人がいる小屋もあります)。冬は雪に埋まって使えないこともあります。宿泊の計画には入れないでください。「山小屋」のボタンは、山頂か登山口の近くの小屋・宿の予約先です。予約サイト(Yamatan・モンベル・RESERVA・南アルプス市の予約)で予約できる小屋を先に、それ以外は公式ホームページ(長野県山小屋情報ポータル・OpenStreetMap などの登録)を並べています。その後ろの ⛺ は、山頂から4km以内のテント場のうち、予約先(Yamatan、またはテント場を管理する小屋の予約先・公式HP)が分かるものです。</p>
<p>展望: 6〜12時の雲の量の平均で、◎20%未満・○50%未満・△80%未満・×それ以上(霧の時間があれば×)。眺めの目安です。</p>
<p>🌼 花の百名山の花: 一言紹介の後ろに、その山の花と咲く時期を載せています。時期は、iNaturalist の観察記録(花が咲いていると記録されたもの)と GBIF の標本の採集日から、山に近い記録を優先して(50km → 100km → 200km)、記録の60%が入る最も短い期間を旬単位で求めました。記録した場所との高さの差(100mで2.5日)と緯度の差(1度で3日)は補正しています。記録が少ない花や、遠くの記録しかない花、実や葉の記録が混ざって時期がずれた花は YAMAP MAGAZINE「花の百名山」の開花時期のままです。今日〜表の最後の日が開花時期に入ると 🌼 を付けています。その年の雪どけや天気で前後します。花の名前を押すと写真が開きます(写真の下に撮影者・ライセンス・元のページ。Wikimedia Commons・iNaturalist の再利用が許可された写真。ツクシフウロは撮影者の許可を得た提供写真)。</p>
<p>🌸 桜(予想): 気象庁の生物季節観測(1991〜2025年、全国のソメイヨシノ、北海道はエゾヤマザクラ)の開花日・満開日と、その気象台の毎日の気温から式を作りました。{sakura_rule(SAKURA['somei'])}です(北海道は{sakura_rule(SAKURA['ezo'])}。年ごとに分けて確かめた誤差は約3〜4日、山の桜の名所12か所の例年の見ごろとの差も約3日)。山頂付近(1,500mより高い山は1,500m付近)・中腹・麓のそれぞれの高さの気温(高さ100mで0.6℃低い)で計算し、開花〜満開の2日前を咲き始め、満開の2日前〜5日後を見ごろとしています。予報の先は平年の気温でつないでいます。</p>
<p>🍁 紅葉(予想): 気象庁の生物季節観測(1991〜2025年。山に近い涼しい気候の55地点のかえで)の紅葉日と、その気象台の毎日の気温から式を作りました。9月1日から「{KOYO[0]}℃と平均気温の差」を毎日足し、{KOYO[1]}℃に達した日が紅葉日です(年ごとに分けて確かめた誤差は約8日)。山の木(ナナカマド・ブナ・ダケカンバなど)は街のかえでより早く色づくため、山の紅葉の名所35か所の例年の見ごろと比べて{-KOYO_SHIFT}日早め、そこから{KOYO_LEN}日間を見ごろ、その前{KOYO_EARLY}日間を色づき始めとしています(名所との差は約5日)。山頂付近(2,400mより高い山は2,400m付近)・中腹・麓のそれぞれで計算しています。山頂付近・中腹・麓の見ごろの間の日も、間の標高が見ごろとして印を付けています。</p>
<p>❄️ 積雪あり: 山頂付近の積雪深の予報(6〜15時)が5cm以上の日。予報の格子が粗いため、稜線の吹き飛ばしや吹きだまりは表せません。雪がありそうかの目安です。</p>
<p>🧊 凍結注意: 山頂付近の最低気温(6〜15時)が0℃以下で前日昼〜当日に雨・雪が0.5mm以上の日、または最低気温が-3℃以下の日。登山道が凍りやすいので、チェーンスパイクやアイゼンの目安に。</p>
<p>体感温度: 山頂付近の気温と風速から求めた、6〜15時で最も低い値です(風が強いほど低くなります)。</p>
<p>⚠️ 警報・注意報は、駐車場・登山口(分からない山は山頂)のある市町村に気象庁がいま出しているもののうち、登山に関係するものです(波浪・高潮・氾濫・乾燥・霜は除く)。🌋 は、山頂から10km以内の火山に噴火警戒レベル2以上などの噴火警報・予報が出ているときに付けています。</p>
<p>風速は山頂の高さに最も近い上空(約760m・1,000m・1,500m・1,950m・3,000mのいずれか)の予報です。稜線の実際の風とは異なることがあり、突風はさらに強くなります。</p>
<p>予報モデル: 今日〜2日後は気象庁MSM、3〜5日後は英国気象局UKMO、6〜10日後は気象庁GSM、それ以降は Open-Meteo のおまかせ(各国のモデルの組み合わせ)を使っています。</p>
<h2>このページについて</h2>
<p>日本百名山(<span class='mk hyaku'></span>)・関西百名山(<span class='mk'></span>、山と溪谷社『関西百名山』)・花の百名山(<span class='mk hana'></span>、田中澄江『花の百名山』)と、これらに入っていない都道府県の最高峰・ヤマレコの人気の山(全期間の上位100)の予報です。複数に入る山は1つにまとめ、入っている一覧のバッジを全部付けています。花の百名山の尾瀬沼は燧ヶ岳(燧岳)に、久住山は九重山に、黒檜山は赤城山にまとめています。沼ノ平・雨竜沼・葦毛湿原・五色ヶ原などの湿原・高原は、その場所の標高で予報しています。
山頂の標高での予報(6〜15時)から、登山に向いた天気かを判定しています。表は7日間で、山名を押すと最大16日間の詳細が開きます。
詳細で今日から5日後までの日付を押すと、時間ごとの予報(今日は1時間ごと、明日〜5日後は3時間ごと。3時間ごとの行の風速・降水量はその3時間の最大・合計)が開きます。
日の出・日の入りは本日の時刻で、平地の地平線を基準にしています(山頂ではもう少し早く昇り、遅く沈みます)。</p></details>
""")


# ---------------------------------------------------------------- スノーボード
SITE_URL = "https://gelny7-cell.github.io/weather-site/"
REPORT_HOURS = (9, 12)      # SURF&SNOW の積雪情報を取り直す更新(日本時間の時)。スキー場の報告は1日1〜2回のため


def parse_report(h, today):
    """SURF&SNOW の積雪情報のページから、いちばん新しい「積雪深」の報告の日の値を取る。なければ None"""
    t = re.sub(r"\s+", " ", re.sub(r"<(script|style)[^>]*>.*?</\1>|<[^>]+>", " ", h, flags=re.S)).replace("&nbsp;", " ")
    m = re.search(r"積雪情報 日付 (.*?) 積雪深 平均\(cm\) (.*?) 積雪差\(cm\) (.*?) 雪質 (.*?) ゲレンデ状況 (.*?) 週間天気予報", t)
    if not m:
        return None
    dates = re.findall(r"(\d{1,2})/(\d{1,2})\(", m.group(1))
    cols = [x.split(" ") for x in m.group(2, 3, 4, 5)]
    if not dates or any(len(c) != len(dates) for c in cols):
        return None
    for (mo, dy), depth, diff, quality, state in zip(dates, *cols):     # 左が新しい日
        if re.fullmatch(r"\d+", depth) and int(depth) > 0:     # 0cm(オープン前など)は出さない
            year = today.year - (1 if int(mo) > today.month + 1 else 0)
            return {"date": f"{year}-{int(mo):02d}-{int(dy):02d}", "depth": int(depth),
                    "diff": int(diff) if re.fullmatch(r"-?\d+", diff) else None,
                    "quality": None if quality == "-" else quality, "state": None if state == "-" else state}
    return None


def attach_report(ski):
    """スキー場が報告したゲレンデの積雪深・雪質・ゲレンデ状況(SURF&SNOW の積雪情報)を s["report"] に付ける。
    取り直すのは朝・昼の更新のときだけで、ほかの回は公開中のサイトの snow_report.json(前回の値)を使う"""
    rids = {s["name"]: s.get("spec", {}).get("src", "")[9:] for s in ski}
    now = datetime.now(JST)
    data = None
    if now.hour not in REPORT_HOURS:
        try:
            data = fetch(SITE_URL + "snow_report.json", {})[0]
        except Exception as ex:
            print(f"前回の積雪情報の取得に失敗: {type(ex).__name__}")
    if data is None:
        data = {}
        for rid in sorted({r for r in rids.values() if r}):
            try:
                req = urllib.request.Request(f"https://surfsnow.jp/guide/htm/{rid}we.htm", headers={"User-Agent": "Mozilla/5.0"})
                data[rid] = parse_report(urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "ignore"), now.date())
            except Exception as ex:
                print(f"積雪情報の取得に失敗({rid}): {type(ex).__name__}")
            time.sleep(1)
    for s in ski:
        s["report"] = data.get(rids[s["name"]])
    OUT.mkdir(exist_ok=True)
    (OUT / "snow_report.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


SEASON = "2026-2027"
# 営業予定(オープン・クローズ)を SURF&SNOW で見直すのは週1回(金曜20時の定期実行。ワークフローが PLAN_REFRESH=1 を渡す)。
# ほかの回は、公開中のサイトの plan.json(前回見直した結果)を使う
PLAN_REFRESH = os.environ.get("PLAN_REFRESH") == "1"


def parse_plan(h):
    """SURF&SNOW のスキー場ページの「オープン予定日」「クローズ予定日」(2026/11/28 や 2026/12/上旬)。読めなければ None"""
    t = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", h))
    def one(label):
        m = re.search(label + r" ?(\d{4})[/年](\d{1,2})[/月](\d{1,2}|上旬|中旬|下旬)", t)
        return [m.group(1), m.group(2).zfill(2), m.group(3).zfill(2) if m.group(3).isdigit() else m.group(3)] if m else None
    op, cl = one("オープン予定日"), one("クローズ予定日")
    if not op:
        return None
    y, mo = int(op[0]), int(op[1])
    return {"open": op, "close": cl, "season": f"{y}-{y + 1}" if mo >= 7 else f"{y - 1}-{y}", "src": "SURF&SNOW"}


def attach_plan(ski):
    """営業予定を SURF&SNOW の今季の予定で更新する(オープン・クローズの変更も反映)。
    公式サイトで確かめた予定(data の src が「公式」)は残し、クローズ日が未発表のときだけ SURF&SNOW の値で埋める"""
    targets = [s for s in ski if not s.get("bc") and s.get("spec", {}).get("src", "").startswith("surfsnow:")]
    found = None
    if not PLAN_REFRESH:
        try:
            found = fetch(SITE_URL + "plan.json", {})[0]
            if not isinstance(found, dict) or any(not re.fullmatch(r"r\d{4}", k) for k in found):
                found = None        # 古い形(スキー場の名前で記録)なら取り直す
        except Exception as ex:
            print(f"前回の営業予定の取得に失敗: {type(ex).__name__}")
    if found is None:               # 週1回の見直し、または前回の分が読めないとき
        found = {}
        for rid in sorted({s["spec"]["src"][9:] for s in targets}):
            try:
                req = urllib.request.Request(f"https://surfsnow.jp/guide/htm/{rid}s.htm", headers={"User-Agent": "Mozilla/5.0"})
                pl = parse_plan(urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "ignore"))
                if pl and pl["season"] == SEASON:
                    found[rid] = pl
            except Exception as ex:
                print(f"営業予定の取得に失敗({rid}): {type(ex).__name__}")
            time.sleep(1)
        print(f"営業予定を見直し: 今季の予定 {len(found)}か所")
    for s in targets:
        pl, now = found.get(s["spec"]["src"][9:]), s.get("plan") or {}
        if not pl:
            continue
        if now.get("season") == SEASON and now.get("src", "").startswith("公式"):
            if not now.get("close") and pl.get("close"):
                s["plan"] = {**now, "close": pl["close"]}
        else:
            s["plan"] = pl
    OUT.mkdir(exist_ok=True)
    (OUT / "plan.json").write_text(json.dumps(found, ensure_ascii=False), encoding="utf-8")


def overnight_snow(h, day):
    """前日17時〜当日8時(ゲレンデが閉まってから開くまで)の降雪量(cm)。
    1時間ごとの値はその前の1時間の量なので、前日18時〜当日8時の値を合計する。データが欠けていれば None"""
    prev = (date.fromisoformat(day) - timedelta(days=1)).isoformat()
    vals = hours_of(h, "snowfall", prev, range(18, 24)) + hours_of(h, "snowfall", day, range(0, 9))
    return sum(vals) if len(vals) == 15 else None


def ski_hours(th, bh, d, today):
    """日付を押すと開く時間ごとの予報。今日は8〜21時(ナイターまで)の1時間ごと、ほかの日は9・12・15時。
    気温・風・積雪・0℃の高さはその時刻の値、雪と雨はその前の1時間(3時間ごとの日は3時間)の合計
    (1時間ごとの値はその前の1時間の量)。値の欠けた時刻は出さない"""
    step = 1 if d == today else 3
    rows = []
    for hr in (range(8, 22) if step == 1 else (9, 12, 15)):
        win = range(hr - step + 1, hr + 1)
        at = {k: hours_of(th, k, d, [hr]) for k in
              ("weather_code", "temperature_2m", "wind_speed_10m", "wind_gusts_10m", "snow_depth", "freezing_level_height",
               "wind_direction_10m")}
        code_b = hours_of(bh, "weather_code", d, [hr])
        sums = {(src, k): hours_of(h, k, d, win) for src, h in (("t", th), ("b", bh)) for k in ("snowfall", "rain")}
        if not all(at.values()) or not code_b or any(len(v) != step for v in sums.values()):
            continue
        rows.append({"hour": hr, "code": at["weather_code"][0], "code_base": code_b[0],
                     "temp": at["temperature_2m"][0], "wind": at["wind_speed_10m"][0], "gust": at["wind_gusts_10m"][0],
                     "feel": wind_chill(at["temperature_2m"][0], at["wind_speed_10m"][0]), "wind_dir": at["wind_direction_10m"][0],
                     "depth": at["snow_depth"][0] * 100, "fl": at["freezing_level_height"][0],
                     "snow": sum(sums[("t", "snowfall")]), "snow_base": sum(sums[("b", "snowfall")]),
                     "rain": sum(sums[("t", "rain")]), "rain_base": sum(sums[("b", "rain")])})
    return rows


def build_ski():
    """ゲレンデ上部(top)と下部(base)の標高で、それぞれ予報を取得する"""
    ski = json.loads((ROOT / "data/ski.json").read_text(encoding="utf-8"))
    # 前日の夜の降雪も数えるため、前日分(past_days=1)も取得する。前日そのものは表示しない
    top = forecast([(s["lat"], s["lon"], s["top"]) for s in ski], past_days=1,
                   hourly="snow_depth,freezing_level_height,wind_speed_10m,snowfall,"
                          "weather_code,temperature_2m,wind_gusts_10m,rain,wind_direction_10m")
    base = forecast([(s["lat"], s["lon"], s["base"]) for s in ski], past_days=1,
                    hourly="snowfall,weather_code,rain,temperature_2m")
    today = datetime.now(JST).date().isoformat()
    open_hrs = range(8, 17)             # 営業時間(8〜16時)の値で1日分をまとめる
    rain_hrs = range(9, 17)             # 雨は1時間ごとの値がその前の1時間の量なので、9〜16時の値で8〜16時の合計
    for s, t, b in zip(ski, top, base):
        s["days"] = []
        if t is None or b is None:
            continue
        h, bh = t["hourly"], b["hourly"]
        s["snowfall"] = [(x, v) for x, v in zip(h["time"], h["snowfall"]) if v is not None]   # 上部の1時間ごとの降雪(週末までの合計用)
        for d in sorted({x[:10] for x in h["time"]}):
            if d < today:
                continue
            night = overnight_snow(h, d)                # 上部の新雪(前日17時〜当日8時)
            # アイスバーン注意: 前日に上部で雨が降ったか0℃を超え(雪が湿る・解ける)、夜〜朝の上部の最低気温が-2℃以下
            # (湿った雪が凍る)で、新雪が3cm未満(新しい雪で覆われない)の日
            prev = (date.fromisoformat(d) - timedelta(days=1)).isoformat()
            thawed = (sum(hours_of(h, "rain", prev, range(0, 24))) > 0
                      or any(t > 0 for t in hours_of(h, "temperature_2m", prev, range(6, 19))))
            frozen = hours_of(h, "temperature_2m", prev, range(18, 24)) + hours_of(h, "temperature_2m", d, range(0, 9))
            night_base = overnight_snow(bh, d)
            depth = hours_of(h, "snow_depth", d, [9])
            v = {k: hours_of(h, k, d, open_hrs) for k in
                 ("weather_code", "temperature_2m", "wind_speed_10m", "wind_gusts_10m", "freezing_level_height")}
            code_b = hours_of(bh, "weather_code", d, open_hrs)
            morning = hours_of(bh, "temperature_2m", d, range(3, 9))    # 朝の路面: ふもと(下部)の3〜8時の最低気温
            rain, rain_b = hours_of(h, "rain", d, rain_hrs), hours_of(bh, "rain", d, rain_hrs)
            if (night is None or night_base is None or not depth or any(len(x) != 9 for x in v.values())
                    or len(code_b) != 9 or len(rain) != 8 or len(rain_b) != 8):
                continue                # 値のない日は表示しない
            ws = v["wind_speed_10m"]
            th, wh = by_hour(h, "temperature_2m", d, open_hrs), by_hour(h, "wind_speed_10m", d, open_hrs)
            wd = by_hour(h, "wind_direction_10m", d, open_hrs)
            s["days"].append({
                "date": d,
                "code": max(v["weather_code"]), "code_base": max(code_b),
                "snow": night, "snow_base": night_base,
                "depth": depth[0] * 100,
                "tmin": min(v["temperature_2m"]), "tmax": max(v["temperature_2m"]),
                "rain": sum(rain), "rain_base": sum(rain_b),
                "fl": min(v["freezing_level_height"]), "gust": max(v["wind_gusts_10m"]), "wind_avg": sum(ws) / len(ws),
                "feel": min(wind_chill(th[k], wh[k]) for k in th if k in wh),
                "wind_dir": mean_dir([wd[k] for k in wd if k in wh], [wh[k] for k in wd if k in wh]) if wd else None,
                "hours": ski_hours(h, bh, d, today),
                "road": min(morning) if len(morning) == 6 and not s.get("bc") else None,   # バックカントリー(標高2,450mの室堂)は路面の列を出さない
                "ice": bool(thawed and frozen and min(frozen) <= -2 and night < 3),
            })
    ski = align(ski, "スノーボード")
    attach_report(ski)
    attach_plan(ski)
    return ski


SKI_REGIONS = {
    "北海道": ["北海道"],
    "東北": ["青森県", "岩手県", "宮城県", "秋田県", "山形県", "福島県"],
    "関東": ["茨城県", "栃木県", "群馬県", "埼玉県", "千葉県", "東京都", "神奈川県", "山梨県"],
    "新潟": ["新潟県"],
    "長野": ["長野県"],
    "北陸": ["富山県", "石川県", "福井県"],
    "東海": ["岐阜県", "静岡県", "愛知県"],
    "近畿": ["三重県", "滋賀県", "京都府", "大阪府", "兵庫県", "奈良県", "和歌山県"],
    "中国・四国": ["鳥取県", "島根県", "岡山県", "広島県", "山口県", "徳島県", "香川県", "愛媛県", "高知県"],
    "九州": ["福岡県", "佐賀県", "長崎県", "熊本県", "大分県", "宮崎県", "鹿児島県"],
}
PREF_SKI_REGION = {p: r for r, ps in SKI_REGIONS.items() for p in ps}
# パウダー期待の基準(上部の新雪cm, 上部の最高気温℃)。地域ごとに変え、雪の少ない地域でも「その地域にしては良い雪」の日が分かるようにする。
# 2023-24・2024-25年の冬の気象データで、どの地域でも1冬に数日ほどになる見当で決めた(過去データは山の雪を少なめに出すので目安)
POWDER = {"北海道": (15, -5), "東北": (15, -3), "関東": (15, -3), "新潟": (15, -3), "長野": (15, -3),
          "北陸": (10, -2), "東海": (10, -2), "近畿": (5, -1), "中国・四国": (5, -1), "九州": (5, -1)}


def is_powder(s, d):
    snow, tmax = POWDER[PREF_SKI_REGION[s["pref"]]]
    return d["snow"] >= snow and d["tmax"] <= tmax


def hour_marks(s):
    """一覧のスキー場名の横の印: 🌅 早朝営業(平日か休日の営業開始が7時台以前)、🌙 ナイター"""
    h = s.get("hours") or {}
    starts = [int(m.group(1)) for x in (h.get("day"), h.get("holiday")) if x for m in [re.search(r"(\d{1,2}):\d{2}", x)] if m]
    out = ""
    if starts and min(starts) <= 7:
        out += " <span class='fac' title='早朝営業あり' role='img' aria-label='早朝営業あり'>🌅</span>"
    if h.get("night"):
        out += " <span class='fac' title='ナイターあり' role='img' aria-label='ナイターあり'>🌙</span>"
    return out


def ski_hours_line(s):
    """営業時間の文字(ウェザーニュースの掲載情報。営業時間は年ごとにあまり変わらないので一度取って保存している)"""
    h = s.get("hours")
    if not h:
        return ""
    clean = lambda x: re.sub(r"\s*[((][^))]*時期により変動[))]", "", x)     # 「(時期により変動)」は出さない
    day, hol = clean(h["day"]), clean(h["holiday"])
    time_ = day if day == hol else f"平日 {day} ／ 休日 {hol}"
    night = f" · ナイター {re.sub(r'[((][^))]*[))]', '', h['night'])}" if h.get("night") else ""     # (期間限定)などの注釈は出さない
    return f"{time_}{night}"


def plan_text(s):
    """営業予定(SURF&SNOW のオープン予定日・クローズ予定日)。今季が未発表なら昨季の期間に注釈を付ける"""
    pl = s.get("plan")
    if not pl:
        return ""
    md = lambda x: f"{int(x[1])}/{int(x[2])}" if x[2].isdigit() else f"{int(x[1])}月{x[2]}"
    text = f"{md(pl['open'])}〜{md(pl['close']) if pl.get('close') else ''}"
    if pl["season"] == SEASON:
        return f"{text} <small class='muted'>26-27シーズン</small>"
    return f"{text} <small class='muted'>昨シーズンの期間</small>"


def season_status(s, day):
    """その日の営業状況(SURF&SNOW のオープン予定日・クローズ予定日による目安)。(状態, 表示の文字) を返す。
    状態は open(営業中)/before(営業前)/after(営業終了)/unknown(今季未発表)/none(予定なし・バックカントリー)。
    旬の日付は、オープンを旬の初日、クローズを旬の最終日として比べる"""
    pl = s.get("plan")
    if not pl or s.get("bc"):
        return "none", ""
    if pl["season"] != SEASON:
        return "unknown", "今季未発表"
    def when(x, first):     # 年はシーズンから決める(7〜12月は1年目、1〜6月は2年目。データの年がずれているものがあるため)
        m = int(x[1])
        y = int(pl["season"][:4]) + (m <= 6)
        if x[2].isdigit():
            return date(y, m, int(x[2]))
        last = (date(y + m // 12, m % 12 + 1, 1) - timedelta(days=1)).day     # その月の最終日
        a, b = {"上旬": (1, 10), "中旬": (11, 20), "下旬": (21, last)}.get(x[2], (1, last))
        return date(y, m, a if first else b)
    md = lambda x: f"{int(x[1])}/{int(x[2])}" if x[2].isdigit() else f"{int(x[1])}月{x[2]}"
    day = date.fromisoformat(day) if isinstance(day, str) else day
    if day < when(pl["open"], True):
        return "before", f"営業前 {md(pl['open'])}〜"
    if pl.get("close") and day > when(pl["close"], False):     # クローズ日が未発表なら営業終了は出さない
        return "after", "営業終了"
    return "open", "営業中"


def status_tag(s, day):
    """一覧・詳細に添える営業状況(営業中は緑、ほかは灰色)"""
    st, text = season_status(s, day)
    return f" · <span class='st{' r3' if st == 'open' else ''}'>{text}</span>" if text else ""


def ski_info(s):
    """「スキー場詳細」ボタン。押すと営業時間・リフト券・コースのデータが開く(分かる項目だけ)。
    データは公式サイトに書いてあればその値、なければ SURF&SNOW の値(data の spec)"""
    sp, rows = s.get("spec", {}), []
    def row(label, value):
        rows.append(f"<div><span class='muted'>{label}</span> {value}</div>")
    if s.get("about"):         # 詳しい紹介(公式サイト・Wikipedia・SURF&SNOW の情報をもとに書いたもの)
        rows.append(f"<p class='about'>{e(s['about'])}</p>")
    if s.get("plan"):
        row("📅 営業期間", plan_text(s) + status_tag(s, datetime.now(JST).date()))
    if s.get("hours"):
        row("🕘 営業時間", ski_hours_line(s))
    t = s.get("ticket")
    if t:
        row("🎫 大人1日リフト券", f"{t['adult']:,}円 <small class='muted'>{t['season'][2:4]}-{t['season'][7:9]}シーズン</small>")
    # キッカー・ハーフパイプ・ジブのパークがあれば 🏂(ウェザーニュースの「スノーパーク ○」。キッズパークは含まない)
    park = " <span class='fac' title='パーク(キッカー・ジブなど)あり' role='img' aria-label='パークあり'>🏂</span>" if s.get("park") else ""
    # ゲレンデで使うゴンドラ・ロープウェイがあれば 🚠(スキー場へ上がるためだけのものは除く)
    gondola = " <span class='fac' title='ゴンドラ・ロープウェイあり' role='img' aria-label='ゴンドラ・ロープウェイあり'>🚠</span>" if s.get("gondola") else ""
    if sp.get("courses") or sp.get("lifts") or gondola or park:
        crs = (f"{sp['courses']}コース" if sp.get("courses") else "") + park        # 「32コース🏂 ／ 16基🚠」
        lft = (f"{sp['lifts']}基" if sp.get("lifts") else "") + gondola
        row("コース・リフト", " ／ ".join(x.strip() for x in (crs, lft) if x.strip()))
    if sp.get("longest") or sp.get("max_slope"):
        row("📏 最長滑走・最大斜度", " ／ ".join(x for x in (f"{sp['longest']:,}m" if sp.get("longest") else "",
                                                          f"{sp['max_slope']}度" if sp.get("max_slope") else "") if x))
    if sp.get("vertical"):
        row("⛰️ 標高差", f"{sp['vertical']:,}m" + (f" <small class='muted'>({sp['base']:,}〜{sp['peak']:,}m)</small>" if sp.get("peak") and sp.get("base") else ""))
    else:                       # SURF&SNOW にないときは一覧の上部・下部の標高で
        row("⛰️ 標高差", f"{s['top'] - s['base']:,}m <small class='muted'>({s['base']:,}〜{s['top']:,}m)</small>")
    if s.get("treerun") or s.get("sidecountry"):     # 公式サイト・SURF&SNOW・Wikipedia に記載があったものだけ
        row("🌲 非圧雪", " ／ ".join(x for x, k in (("ツリー", "treerun"), ("サイド", "sidecountry")) if s.get(k)))
    if sp.get("level"):
        row("📊 難易度", " ".join(f"{k}{v}%" for k, v in sp["level"].items()))
    if sp.get("groom"):
        row("❄️ 圧雪", " ".join(f"{k}{v}%" for k, v in sp["groom"].items()))
    if not rows:
        return ""
    return (f"<details class='menu info'><summary class='mapbtn'>📋 スキー場詳細</summary>"
            f"<div class='info-body'>{''.join(rows)}</div></details> ")


def road(t):
    """朝の路面: ふもとの最低気温と凍結の印(1℃以下=凍結注意)"""
    t = round(t)                        # 表示する値で判定する(1.2℃が「1℃」なのに印がない、を防ぐ)
    tag = " <span class='frz' title='路面凍結注意' role='img' aria-label='路面凍結注意'>凍</span>" if t <= 1 else ""
    return f"{t}℃{tag}"


def ski_detail(s, with_map=True, simple=False):
    """スキー場の日ごとの詳細。上部と下部の天気・気温を並べる(駐車場の地図ボタン付き)。
    simple=True はトップページ用で、これまでどおり1列ずつ(値はゲレンデ上部)"""
    has_road = all(d.get("road") is not None for d in s["days"])     # 値のない日があれば列ごと出さない
    if simple:
        rows = ""
        for d in s["days"]:
            icon, label = wx(d["code"])
            powder = " <span class='tag'>パウダー期待</span>" if is_powder(s, d) else ""
            rain = f" <span class='tag warn'>雨{d['rain']:.0f}mm</span>" if d["rain"] >= 1 else ""
            rows += (f"<tr><td class='dt'>{day_label(d['date'])}</td><td>{icon} {label}{rain}</td>"
                     f"<td>{d['snow']:.0f}cm{powder}</td><td>{d['depth']:.0f}cm</td>"
                     f"<td>{round(d['tmin'])}〜{round(d['tmax'])}℃</td>"
                     f"<td>{d['gust']:.0f}m/s" + (" <span class='tag warn'>運休注意</span>" if d["gust"] >= 20 else "")
                     + "</td>" + (f"<td>{road(d['road'])}</td>" if has_road else "") + "</tr>")
        return (alert_box(s) + "<div class='scroll'><table class='sub'><tr><th class='dt'>日付</th><th>天気</th><th>新雪</th>"
                "<th>積雪</th><th>気温</th><th>最大瞬間風速</th>" + ("<th>朝の路面</th>" if has_road else "") + "</tr>"
                f"{rows}</table></div>")
    def gust(g):                # 突風が強い日はゴンドラ・リフトが止まりやすい
        return f"{g:.0f}m/s" + (" <span class='tag warn'>運休注意</span>" if g >= 20 else "")

    def wind(direction, speed):
        return (f"{dir_name(direction)} " if direction is not None else "") + f"{speed:.0f}m/s"

    def weather(code, rain):
        icon, label = wx(code)
        tag = f" <span class='tag warn'>雨{rain:.0f}mm</span>" if rain >= 1 else ""
        return f"{icon} {label}{tag}"
    rows = ""
    for d in s["days"]:
        powder = " <span class='tag'>パウダー期待</span>" if is_powder(s, d) else ""
        label = (f"<button type='button' class='hbtn' aria-expanded='false'>{day_label(d['date'])}</button>"
                 if d.get("hours") else day_label(d["date"]))
        rows += (f"<tr><td class='dt'>{label}</td>"
                 f"<td>{weather(d['code'], d['rain'])}</td><td>{weather(d['code_base'], d['rain_base'])}</td>"
                 f"<td>{d['snow']:.0f}cm{powder}" + (" <span class='fac' title='アイスバーン注意' role='img' aria-label='アイスバーン注意'>🧊</span>" if d.get("ice") else "")
                 + f"</td><td>{d['snow_base']:.0f}cm</td>"
                 f"<td>{d['depth']:.0f}cm</td>"
                 f"<td>{round(d['tmin'])}〜{round(d['tmax'])}℃</td><td>{round(d['feel'])}℃</td>"
                 f"<td>{d['fl']:,.0f}m</td><td>{wind(d.get('wind_dir'), d['wind_avg'])}</td><td>{gust(d['gust'])}</td>"
                 + (f"<td>{road(d['road'])}</td>" if has_road else "") + "</tr>")
        for x in d.get("hours", []):    # 時間ごと(雪・雨は前の3時間の合計、ほかはその時刻の値)
            rows += (f"<tr class='hr' hidden><td class='dt'>{x['hour']}時</td>"
                     f"<td>{weather(x['code'], x['rain'])}</td><td>{weather(x['code_base'], x['rain_base'])}</td>"
                     f"<td>{x['snow']:.0f}cm</td><td>{x['snow_base']:.0f}cm</td><td>{x['depth']:.0f}cm</td>"
                     f"<td>{round(x['temp'])}℃</td><td>{round(x['feel'])}℃</td><td>{x['fl']:,.0f}m</td>"
                     f"<td>{wind(x.get('wind_dir'), x['wind'])}</td><td>{gust(x['gust'])}</td>"
                     + ("<td></td>" if has_road else "") + "</tr>")
    link = ""
    if with_map:                # 公式ページ・ゲレンデマップ(分かるスキー場だけ)・駐車場の地図
        link = ((sitelink(s["site"], "🏠 公式ページ") + " " if s.get("site") else "")
                + (sitelink(s["map"], "🗺️ ゲレンデマップ") + " " if s.get("map") else "")
                + camlink(s) + maplink(s.get('parking') or s['name'] + ' 第1駐車場', '駐車場') + " " + onsenlink(s["lat"], s["lon"]) + foodlink(s["lat"], s["lon"], s["pref"], "ski") + michilink(s["lat"], s["lon"]) + conbilink(s["lat"], s["lon"]) + " ")
    rp = s.get("report")
    obs = ""
    if rp:                      # スキー場が報告したゲレンデの積雪(SURF&SNOW)。報告のない日は出さない
        when = date.fromisoformat(rp["date"])
        obs = (f"<p class='obs'>ゲレンデ積雪:<b>{rp['depth']}cm</b>"
               + (f"(前日比{rp['diff']:+d}cm)" if rp["diff"] is not None else "")
               + "".join(f"・{e(x)}" for x in (rp["quality"], rp["state"]) if x)
               + f" <small class='muted'>{when.month}/{when.day}時点</small></p>")
    # 文字(注意報・紹介・営業時間・標高・積雪の観測)をまとめて上に、ボタン(お気に入り登録が先頭)をその下の1か所に
    buttons = f"<div class='btnrow'>{star('s', s)} {ski_info(s)}{link}</div>" if with_map else ""
    return (f"{alert_box(s)}{obs}"
            f"{buttons}<div class='scroll'><table class='sub'>"
            "<tr><th rowspan='2' class='dt'>日付</th><th colspan='2'>天気</th><th colspan='2'>新雪</th><th rowspan='2'>積雪<br>(上部)</th>"
            "<th rowspan='2'>気温<br>(上部)</th><th rowspan='2'>体感温度<br>(上部)</th><th rowspan='2'>0℃の高さ<br>(8〜16時)</th><th rowspan='2'>平均風速<br>(上部)</th>"
            "<th rowspan='2'>最大瞬間風速<br>(上部)</th>"
            + ("<th rowspan='2'>朝の路面<br>(下部3〜8時)</th>" if has_road else "") + "</tr>"
            "<tr><th>上部</th><th>下部</th><th>上部</th><th>下部</th></tr>"
            f"{rows}</table></div>")


def ski_page(ski):
    days = [d["date"] for d in ski[0]["days"]]
    order = list(SKI_REGIONS)                  # 地域順に並べるとき用
    items = []
    stmap = {"open": "o", "before": "b", "after": "a", "unknown": "u", "none": "u"}
    for s in ski:
        url = spot_url("s", s)
        r = PREF_SKI_REGION[s["pref"]]
        st0 = season_status(s, days[0])[1] if s.get("plan") else ""
        items.append({"k": s["key"], "f": fid("s", s), "n": s["name"], "y": s.get("yomi", ""), "r": r, "ro": order.index(r),
                      "p": s["pref"], "u": url, "b": hour_marks(s) + alert_tags(s), "bc": 1 if s.get("bc") else 0,
                      "ol": (st0 if st0.startswith("営業前") else ""),
                      "d": [[round(x["snow"]), wx(x["code"])[0], round(x["tmin"]), round(x["tmax"]), round(x["wind_avg"]),
                             1 if is_powder(s, x) else 0, "" if s.get("bc") else stmap[season_status(s, x["date"])[0]]]
                            for x in s["days"]]})
        back = f"<p class='back'><a href='snow.html'><svg class='chev' viewBox='0 0 24 24' aria-hidden='true'><path d='M15 5l-7 7 7 7' fill='none' stroke='currentColor' stroke-width='2.5' stroke-linecap='round' stroke-linejoin='round'/></svg>スノーボードの一覧</a></p>"
        SPOT_PAGES[url] = (f"{s['name']}の雪と天気",
                           f"{back}<div class='ghead'><h1 class='spoth'>{e(s['name'])}{hour_marks(s)}{alert_tags(s)}</h1>"
                           f"<p class='spotsub'>{e(s['pref'])}{status_tag(s, datetime.now(JST).date())}</p></div>{ski_detail(s)}",
                           "snow.html")
    regions = ([("", "全国")] + [(r, "関西" if r == "近畿" else r) for r in SKI_REGIONS if r not in ("中国・四国", "九州")]
               + [("中国・四国|九州", "中四国・九州")])
    filters = [("snow", "予想新雪(選んだ日の合計)", [("", "指定なし"), ("5", "5cm以上"), ("10", "10cm以上")]),
               ("open", "営業状況", [("", "すべて"), ("o", "営業中")])]
    sorts = [("snow", "新雪量順"), ("powder", "パウダー期待順"), ("region", "地域順"), ("name", "スキー場名順")]
    data = {"kind": "s", "days": day_info(days), "we": weekend(days), "items": items}
    return (genre_head("🏂", "スノーボード", "ゲレンデ上部の予報から、前夜〜朝に降る新雪の量とパウダーを予想します(予報値で、ゲレンデの積雪の実測ではありません)")
            + list_controls("スキー場名で探す", regions, [], filters, sorts)
            + f"<script type='application/json' id='spots-data'>{spots_json(data)}</script>\n"
            + f"""
<details class='note'><summary>判定の基準・データについて</summary><ul>
<li>新雪: 前日17時〜当日8時(ゲレンデが閉まってから開くまで)に降る雪の量の予報</li>
<li>積雪: 朝9時に積もっている雪の深さの予報(気象モデルの推定。実際の積雪と大きく違うことがあります)</li>
<li>🌅 早朝営業: 平日か休日の営業開始が7時台以前のスキー場。🌙 ナイター: ナイター営業のあるスキー場(どちらもウェザーニュースの営業時間から。特定日だけのものも含みます)</li>
<li>📷 ライブカメラ: スキー場の公式サイトが載せているカメラです(公式にないスキー場はウェザーニュースのカメラ)</li>
<li>ゲレンデ積雪: スキー場が報告した積雪深(平均)・前日比・雪質・ゲレンデ状況で、<a href='https://surfsnow.jp/'>SURF&amp;SNOW</a> の積雪情報から朝・昼に取得しています。報告がないスキー場・日は表示しません</li>
<li><span class='tag'>パウダー期待</span> 上部の新雪と最高気温が、地域ごとの基準を満たす日です(北海道 15cm以上・-5℃以下、東北・関東・新潟・長野 15cm以上・-3℃以下、北陸・東海 10cm以上・-2℃以下、近畿・中国・四国・九州 5cm以上・-1℃以下)。雪の少ない地域でも「その地域にしては良い雪」の日が分かるようにしています。「❄️パウダー期待」のボタンで、7日間のうちにパウダー期待の日があるスキー場だけに絞り込めます</li>
<li>🧊 アイスバーン注意: 前日に上部で雨が降ったか0℃を超え、夜〜朝の上部の最低気温が-2℃以下で、新雪が3cm未満の日(湿った雪が凍って硬くなりやすい)</li>
<li>体感温度: 上部の気温と風速から求めた、8〜16時で最も低い値。平均風速には風向(風が吹いてくる方角)も表示しています</li>
<li>朝の路面: ふもと(ゲレンデ下部)の3〜8時の最低気温。<span class='frz' title='路面凍結注意' role='img' aria-label='路面凍結注意'>凍</span> 路面凍結注意(1℃以下)。冬用タイヤ・チェーンの準備の目安です(実際の路面は除雪や日当たりで変わります)</li>
<li><span class='tag warn'>運休注意</span> 上部の最大瞬間風速が20m/s以上(ゴンドラ・リフトが止まりやすい強さ)</li>
<li>⚠️ 警報・注意報は、スキー場のある市町村に気象庁がいま出しているもの。🌋 は10km以内の火山に噴火警報・予報が出ているとき</li>
<li>天気・雨・気温・0℃の高さ・平均風速・最大瞬間風速は、営業時間(8〜16時)の予報からまとめています(天気は最も悪いもの、0℃の高さは最も低いもの)</li>
<li>日付を押すと時間ごとの予報が開きます(今日は8〜21時の1時間ごと、ほかの日は9時・12時・15時)。雪・雨はその時刻までの1時間(3時間ごとの日は3時間)の合計、気温・積雪・0℃の高さ・風はその時刻の値です(風速の列は平均ではなくその時刻の風速、最大瞬間風速の列はその時刻の突風)</li>
<li><span class='tag warn'>雨</span> 雨の予報(雪質が重くなりやすい)</li>
<li>上部・下部の標高: OpenStreetMap のリフトの位置と地形の標高から求めた値(一部は手作業で補正)。概算です</li>
<li>スキー場の一覧・標高差: Wikipedia、Wikidata。リフトの位置: © OpenStreetMap contributors</li>
<li>公式ページ・ゲレンデマップのボタンは、リンク先が確認できたスキー場だけに出しています</li>
<li>スキー場詳細: コース数・リフト数・最長滑走距離・最大斜度・標高差は、公式サイトに書いてあればその値、なければ <a href='https://surfsnow.jp/'>SURF&amp;SNOW</a>、それもなければウェザーニュースの掲載値です。難易度・圧雪(コースにあるもの)は SURF&amp;SNOW、大人1日リフト券は公式サイトで確かめられたものはその料金(休日・通常料金)、ほかは SURF&amp;SNOW、ウェザーニュースの掲載料金です(2026-27シーズン。未発表のスキー場は2025-26シーズン)。営業期間は、各スキー場の公式サイトで今季の発表を確かめられたものはその日付、ほかは SURF&amp;SNOW のオープン予定日・クローズ予定日です(SURF&amp;SNOW は毎週金曜20時に見直し、26-27シーズンが未発表のスキー場は昨シーズンの期間)。一覧の「営業中」「営業前」「営業終了」「今季未発表」は、この予定日による目安です。雪不足などで変わることがあるので、公式ページで確認してください。週末おすすめは、週末に営業中のスキー場を先に並べています。コース・リフトは「コース数 ／ リフト数(基)」です。🏂 は、キッカー・ハーフパイプ・ジブなどのパークがあるスキー場です(ウェザーニュースの掲載情報。キッズパークは含みません)。🚠 は、ゲレンデで使うゴンドラ・ロープウェイがあるスキー場です(びわ湖バレイのように、スキー場へ上がるためだけのものは除く)。🌲 非圧雪のツリー(ツリーラン)・サイド(サイドカントリー。ゲートなどから管理区域外へ出られる仕組み)は、公式サイト・SURF&amp;SNOW・Wikipedia・<a href='https://steep.jp/'>STEEP</a> に記載があったスキー場だけ表示しています。サイドカントリーは自己責任の山岳エリアなので、装備と各スキー場のルールを必ず確認してください。料金や施設は変わることがあるので、公式ページで確認してください</li>
<li>立山室堂(バックカントリー): スキー場ではなく、バックカントリーのエリアとして載せています。積雪・新雪は予報モデルの値で、観測値ではありません。雪崩の危険がある山岳地なので、装備と情報収集は各自で行ってください。立山黒部アルペンルートが閉まる冬(12〜4月)は入れないため、週末おすすめには出しません</li>
<li>営業時間: ウェザーニュースの掲載情報(2025-26シーズン)。今シーズンの営業日・時間は公式ページで確認してください</li></ul>
<h2>このページについて</h2>
<p>標高差の大きいスキー場100か所(営業を終えた芸北国際スキー場を除く)に、志賀高原 焼額山・キューピットバレイ・だいせんホワイトリゾート・スノーウェーブパーク白鳥高原と、SURF&amp;SNOW の中京・北陸・関西のスキー場を加えた一覧です。表の数字はゲレンデ上部の新雪(前日17時〜当日8時、閉場から開場までに積もる雪)の予報(cm)です。表は7日間で、スキー場名を押すと最大16日間の積雪・気温・0℃の高さ・風の詳細が開きます。</p></details>
""")


# ---------------------------------------------------------------- サーフィン
def wind_kind(wdeg, facing):
    diff = ang_diff(wdeg, facing)     # 0に近いほど海から吹く
    return "オンショア" if diff <= 45 else ("オフショア" if diff >= 135 else "クロス")


def surf_hours(s, wh, fh, d, today):
    """日付を押すと開く時間ごとの予報(6〜16時): 今日・明日は1時間ごと、明後日は3時間ごと。値の欠けた時刻は出さない"""
    lead = (date.fromisoformat(d) - today).days
    if not 0 <= lead <= 2:
        return []
    rows = []
    for hr in range(6, 17, 1 if lead <= 1 else 3):
        v = {k: hours_of(src, k, d, [hr]) for src, keys in (
            (wh, ("wave_height", "swell_wave_height", "swell_wave_period", "swell_wave_direction")),
            (fh, ("wind_speed_10m", "wind_direction_10m", "weather_code"))) for k in keys}
        if all(v.values()):
            v = {k: x[0] for k, x in v.items()}
            air, sea = hours_of(fh, "temperature_2m", d, [hr]), hours_of(wh, "sea_surface_temperature", d, [hr])
            rows.append({"hour": hr, "height": v["wave_height"], "swell": v["swell_wave_height"],
                         "air": air[0] if air else None, "sea": sea[0] if sea else None,
                         "period": v["swell_wave_period"], "wave_dir": v["swell_wave_direction"],
                         "wind": v["wind_speed_10m"], "wind_dir": v["wind_direction_10m"],
                         "kind": wind_kind(v["wind_direction_10m"], s["facing"]), "code": v["weather_code"]})
    return rows


def tide_events(m15):
    """15分ごとの潮位から満潮・干潮を探す。[(時刻 "YYYY-MM-DDTHH:MM", "満潮" or "干潮")]。
    潮位は1cm単位で同じ値が続くので、同じ値の区間を1つ(真ん中の時刻)にまとめてから山と谷を探す。
    潮の差が小さい日本海側で細かい揺れを拾わないよう、隣の山・谷との差が5cm未満のものは除く"""
    runs = []                           # [(時刻, 潮位)]
    seg = []
    for t, v in zip(m15["time"], m15["sea_level_height_msl"]):
        if v is None:
            continue
        if seg and v != seg[0][1]:
            runs.append(seg[len(seg) // 2])
            seg = []
        seg.append((t, v))
    if seg:
        runs.append(seg[len(seg) // 2])
    ev = []
    for i in range(1, len(runs) - 1):
        a, b, c = runs[i - 1][1], runs[i][1], runs[i + 1][1]
        if b > a and b > c:
            ev.append([runs[i][0], "満潮", b])
        elif b < a and b < c:
            ev.append([runs[i][0], "干潮", b])
    changed = True
    while changed:                      # 差の小さい山と谷の組を取り除く
        changed = False
        for i in range(len(ev) - 1):
            if abs(ev[i][2] - ev[i + 1][2]) < 0.05:
                del ev[i:i + 2]
                changed = True
                break
    return [(x[0], x[1]) for x in ev]


def tide_text(m15, events, d, hrs):
    """時間帯の潮: その間の満潮・干潮の時刻。なければ上げ潮・下げ潮。値がなければ None"""
    inside = [f"{k} {int(x[11:13])}:{x[14:16]}" for x, k in events if x[:10] == d and int(x[11:13]) in hrs]
    if inside:
        return "・".join(inside)
    vals = [v for x, v in zip(m15["time"], m15["sea_level_height_msl"])
            if x[:10] == d and int(x[11:13]) in hrs and v is not None]
    if len(vals) < 2:
        return None
    return "上げ潮" if vals[-1] > vals[0] else "下げ潮"


def build_surf():
    spots = json.loads((ROOT / "data/surf.json").read_text(encoding="utf-8"))
    today = datetime.now(JST).date()
    waves = marine([(s["sea_lat"], s["sea_lon"]) for s in spots], hourly=",".join([
        "wave_height", "swell_wave_height", "swell_wave_period", "swell_wave_direction",
        "sea_surface_temperature"]))
    tides = marine([(s["sea_lat"], s["sea_lon"]) for s in spots], minutely_15="sea_level_height_msl")
    winds = forecast([(s["lat"], s["lon"], 0) for s in spots],
                     hourly="wind_speed_10m,wind_direction_10m,weather_code,temperature_2m", daily="sunrise,sunset")
    for s, w, f, tide in zip(spots, waves, winds, tides):
        s["days"] = []
        if w is None or f is None:
            continue
        dl = f.get("daily", {})               # 本日の日の出・日の入り(登山と同じく平地の地平線での時刻)
        if str(today) in dl.get("time", []):
            k = dl["time"].index(str(today))
            if dl["sunrise"][k] and dl["sunset"][k]:
                s["sun"] = (dl["sunrise"][k][11:16], dl["sunset"][k][11:16])
        m15 = tide["minutely_15"] if tide else None
        events = tide_events(m15) if m15 else []
        s["grid"] = (w["latitude"], w["longitude"])
        for d in sorted({t[:10] for t in w["hourly"]["time"]}):
            slots = []
            for name, hrs in (("朝", range(6, 12)), ("昼", range(12, 18))):
                hgt = hours_of(w["hourly"], "wave_height", d, hrs)
                per = hours_of(w["hourly"], "swell_wave_period", d, hrs)     # 周期はうねりの周期(風波を含めない)
                swh = hours_of(w["hourly"], "swell_wave_height", d, hrs)
                wdir = hours_of(w["hourly"], "swell_wave_direction", d, hrs)     # うねりの向き
                ws = hours_of(f["hourly"], "wind_speed_10m", d, hrs)
                wd = hours_of(f["hourly"], "wind_direction_10m", d, hrs)
                codes = hours_of(f["hourly"], "weather_code", d, hrs)
                # 時間帯の半分以上の時刻に値がなければ、その時間帯は表示しない
                if min(len(hgt), len(per), len(swh), len(wdir), len(ws), len(wd), len(codes)) < len(hrs) / 2:
                    continue
                h_, p_, w_ = sum(hgt) / len(hgt), sum(per) / len(per), sum(ws) / len(ws)
                sw_ = sum(swh) / len(swh)
                wdeg = mean_dir(wd, ws)
                kind = wind_kind(wdeg, s["facing"])
                # 判定はうねりの波力(kW/m、深い海の式 ρg²H²T/64π ≈ 0.49×うねりの高さ²×周期)で見る。
                # 同じ高さでも周期が長いほど力がある。風波だけの日は波力が小さく出る。荒れた海は風波も含めた波の高さで
                pw = 0.49 * sw_ ** 2 * p_
                if pw < 0.5 or h_ > 2.5:
                    score = 0
                else:
                    score = 1 if pw < 2 else 2 if pw < 3 else 3 if pw <= 12 else 2 if pw < 20 else 1
                    if kind == "オンショア" and w_ >= 5:
                        score -= 1
                    if kind == "オフショア" and w_ <= 6 and score < 3:
                        score += 1
                    if pw < 2 or pw >= 20:          # 小さい・大きすぎる(上級者向け)ときは風が良くても最高△
                        score = min(score, 1)
                    score = max(score, 0)
                # 気温・水温は判定に使わないので、値がなくても時間帯は残す(表で列ごと出さない)
                air = hours_of(f["hourly"], "temperature_2m", d, hrs)
                sea = hours_of(w["hourly"], "sea_surface_temperature", d, hrs)
                slots.append({"slot": name, "height": h_, "period": p_, "swell": sum(swh) / len(swh),
                              "air": sum(air) / len(air) if air else None, "sea": sum(sea) / len(sea) if sea else None,
                              "tide": tide_text(m15, events, d, hrs) if m15 else None,
                              "wave_dir": mean_dir(wdir), "wind": w_, "wind_dir": wdeg,
                              "kind": kind, "score": score,
                              "code": max(codes)})
            if len(slots) == 2:         # 朝・昼の両方がそろった日だけ表示する
                hours = surf_hours(s, w["hourly"], f["hourly"], d, today)
                for x in hours:             # 時間ごとの潮: その時刻の前後1時間に満潮・干潮があればその時刻、なければ上げ・下げ
                    x["tide"] = tide_text(m15, events, d, range(x["hour"], x["hour"] + 1)) if m15 else None
                s["days"].append({"date": d, "slots": slots, "hours": hours})
    return align(spots, "サーフィン")


def surf_table(s):
    """サーフポイントの朝・昼の表"""
    rows = ""
    every = [x for d in s["days"] for x in d["slots"] + d.get("hours", [])]
    has_air = all(x["air"] is not None for x in every)    # 値の欠けたスポットは列ごと出さない
    has_sea = all(x["sea"] is not None for x in every)
    has_tide = all(x.get("tide") for x in every)

    def front(deg):             # うねりが浜の正面(向きの差30°以内)から入るなら 🎯
        return ("<span class='fac' title='うねりが浜の正面から入る' role='img' aria-label='うねりが浜の正面から'>🎯</span>"
                if deg is not None and ang_diff(deg, s["facing"]) <= 30 else "")

    def power(x):               # うねりの波力(kW/m)。判定と同じ式(0.49×うねりの高さ²×周期)
        return f"<td>{0.49 * x['swell'] ** 2 * x['period']:.1f}</td>"

    def temps(x):
        return ((f"<td>{x['air']:.0f}℃</td>" if has_air else "")
                + (f"<td>{x['sea']:.0f}℃</td>" if has_sea else ""))
    for d in s["days"]:
        for i, sl in enumerate(d["slots"]):
            mark, cls = RATE[sl["score"]]
            label = (f"<button type='button' class='hbtn' aria-expanded='false'>{day_label(d['date'])}</button>"
                     if d.get("hours") else day_label(d["date"]))
            date_cell = (f"<td rowspan='{len(d['slots'])}' class='dt'>{label}</td>" if i == 0 else "")
            rows += (f"<tr>{date_cell}<td>{sl['slot']}</td><td class='{cls}'><b>{mark}</b></td>"
                     f"<td>{sl['swell']:.1f}m({dir_name(sl['wave_dir'])}{front(sl['wave_dir'])})</td>"
                     f"<td>{sl['height']:.1f}m</td><td>{sl['period']:.1f}秒</td>{power(sl)}"
                     + (f"<td>{sl['tide']}</td>" if has_tide else "")
                     + f"<td>{dir_name(sl['wind_dir'])} {sl['wind']:.1f}m/s <small>{sl['kind']}</small></td>"
                     f"<td>{wx(sl['code'])[0]}</td>{temps(sl)}</tr>")
        for x in d.get("hours", []):
            rows += (f"<tr class='hr' hidden><td class='dt'>{x['hour']}時</td><td colspan='2'></td>"
                     f"<td>{x['swell']:.1f}m({dir_name(x['wave_dir'])}{front(x['wave_dir'])})</td><td>{x['height']:.1f}m</td>"
                     f"<td>{x['period']:.1f}秒</td>{power(x)}" + (f"<td>{x['tide']}</td>" if has_tide else "")
                     + f"<td>{dir_name(x['wind_dir'])} {x['wind']:.1f}m/s <small>{x['kind']}</small></td>"
                     f"<td>{wx(x['code'])[0]}</td>{temps(x)}</tr>")
    return ("<div class='scroll'><table class='sub'>"
            "<tr><th class='dt'>日付</th><th></th><th>判定</th><th>うねり</th><th>波の高さ</th><th>周期</th><th>波力<br><small>kW/m</small></th>" + ("<th>潮</th>" if has_tide else "")
            + f"<th>風</th><th>天気</th>{'<th>気温</th>' if has_air else ''}{'<th>水温</th>' if has_sea else ''}</tr>{rows}</table></div>")


def facmarks(s):
    """トイレ・シャワーがあれば印だけ(surfride.net の設備欄)。一覧のポイント名の横に出す"""
    return "".join(f" <span class='fac' title='{label}' role='img' aria-label='{label}'>{icon}</span>"
                   for k, icon, label in (("toilet", "🚻", "トイレあり"), ("shower", "🚿", "シャワーあり")) if s.get(k) is True)


def camlink(s):
    """ライブカメラのボタン(data の cams。サーフィンはサーファーズオーシャン・surfride.net、スキー場は公式サイト
    (なければウェザーニュース)に載っているカメラ)。
    1つならそのまま開き、複数あれば押すと一覧が開く"""
    cams = s.get("cams") or []
    if len(cams) == 1:
        return sitelink(cams[0]["url"], "📷 ライブカメラ") + " "
    if not cams:
        return ""
    items = "".join(sitelink(c["url"], c["name"]) for c in cams)
    return (f"<details class='menu'><summary class='mapbtn'>📷 ライブカメラ</summary>"
            f"<div class='menu-list'>{items}</div></details> ")


SUITS = ("ボードショーツ・ラッシュガード", "スプリング(半袖・半ズボン)", "シーガル(半袖・長ズボン)", "3/2mm フルスーツ",
         "5/3mm フルスーツ", "5/3mm セミドライ+ブーツ", "5mm セミドライ+ブーツ・グローブ・キャップ")
SEA_NEAR = 1.5      # 水温の予報は沖合の値。岸近くは秋冬に1〜2℃低いことが多いので、この分を引いて見る


def wetsuit(sea, air=None):
    """水温(沖合の予報)と朝の気温それぞれからウェットスーツの目安を出し、厚い方を返す(一般的な目安)"""
    t = sea - SEA_NEAR
    by_sea = next((i for i, lo in enumerate((25, 23, 20, 17, 14, 11)) if t >= lo), 6)
    by_air = 0 if air is None else next((i for i, lo in enumerate((27, 24, 20, 14, 8)) if air >= lo), 5)
    return SUITS[max(by_sea, by_air)]


def surf_detail(s):
    """サーフポイントの詳細(名前を押すと開く): 注意報・紹介・ボタン(お気に入り登録が先頭)・朝昼の表"""
    pref = s["pref"][:s["pref"].index("県") + 1] if "県" in s["pref"] else s["pref"][:3]
    buttons = (f"<div class='btnrow'>{star('w', s)} {camlink(s)}{maplink(s['parking'], '駐車場')} {onsenlink(s['lat'], s['lon'])}"
               f"{foodlink(s['lat'], s['lon'], pref, 'surf')}{michilink(s['lat'], s['lon'])}{conbilink(s['lat'], s['lon'])}</div>")
    seas = [sl["sea"] for d in s["days"][:1] for sl in d["slots"] if sl.get("sea") is not None]
    t = sum(seas) / len(seas) if seas else None
    air = next((sl["air"] for d in s["days"][:1] for sl in d["slots"] if sl["slot"] == "朝" and sl.get("air") is not None), None)
    # 浜の向き・日の出・ウェットは紹介と同じ文字の形式で
    suit = (f"<p class='intro' title='今日の水温 {t:.0f}℃(沖合)"
            + (f"・朝の気温 {air:.0f}℃" if air is not None else "") + "から'>"
            f"ウェット予想:{wetsuit(t, air).split('(')[0]}</p>") if seas else ""
    sun = (f"<p class='intro'>本日の日の出 {s['sun'][0].lstrip('0')} ／ 日の入り {s['sun'][1]}</p>" if s.get("sun") else "")
    facing = (f"<p class='intro'>浜の向き:{dir_name(s['facing'])}</p>" if s.get("facing") is not None else "")
    return (f"{alert_box(s)}{intro(s)}{facing}{sun}{suit}{buttons}{surf_table(s)}"
            + (f"<p class='muted'>波は沖合({s['grid'][0]:.2f}, {s['grid'][1]:.2f})の予報</p>" if s.get("grid") else ""))


def surf_page(spots):
    days = [d["date"] for d in spots[0]["days"]]
    items = []
    for s in spots:
        url = spot_url("w", s)
        pref = re.match(r"北海道|東京都|京都府|大阪府|.{2,3}?県", s["pref"]).group(0)
        items.append({"k": s["key"], "f": fid("w", s), "n": s["name"], "y": s.get("yomi", ""), "r": pref, "p": s["pref"],
                      "lon": s["lon"], "u": url, "b": facmarks(s) + alert_tags(s), "fc": dir_name(s["facing"]),
                      "d": [[[sl["slot"], sl["score"], round(sl["height"], 1), round(sl["swell"], 1), round(sl["period"], 1),
                              round(sl["wind"], 1), round(sl["wind_dir"]) if sl["wind_dir"] is not None else None, sl["kind"]]
                             for sl in x["slots"]] for x in s["days"]]})
        back = f"<p class='back'><a href='surf.html'><svg class='chev' viewBox='0 0 24 24' aria-hidden='true'><path d='M15 5l-7 7 7 7' fill='none' stroke='currentColor' stroke-width='2.5' stroke-linecap='round' stroke-linejoin='round'/></svg>サーフィンの一覧</a></p>"
        SPOT_PAGES[url] = (f"{s['name']}の波と風",
                           f"{back}<div class='ghead'><h1 class='spoth'>{e(s['name'])}{facmarks(s)}{alert_tags(s)}</h1>"
                           f"<p class='spotsub'>{e(s['pref'])}</p></div>{surf_detail(s)}", "surf.html")
    prefs = list(dict.fromkeys(x["r"] for x in sorted(items, key=lambda x: -x["lon"])))     # 東から西へ
    regions = [("", "全国")] + [(x, x if x == "北海道" else x[:-1]) for x in prefs]
    filters = [("rate", "評価(朝・昼のよいほう)", [("", "指定なし"), ("2", "◎○のみ"), ("3", "◎のみ")]),
               ("wind", "風", [("", "指定なし"), ("オフショア", "オフショア"), ("オンショア", "オンショア")])]
    sorts = [("score", "総合評価順"), ("height", "波高順"), ("period", "周期順"), ("region", "地域順")]
    data = {"kind": "w", "days": day_info(days), "we": weekend(days), "items": items}
    return (genre_head("🏄", "サーフィン", "うねりの波力と風向き(オフショア・オンショア)から、朝(6〜11時)・昼(12〜17時)の波を判定します")
            + list_controls("ポイント名で探す", regions, [], filters, sorts)
            + f"<script type='application/json' id='spots-data'>{spots_json(data)}</script>\n"
            + f"""
<details class='note'><summary>判定の基準・データについて</summary><h2>判定の基準</h2><ul>
<li>判定は、うねり(風波を除いた遠くから届く波)の<b>波力</b>で見ています。波力 ≈ 0.49 × うねりの高さ² × 周期(kW/m)で、同じ高さでも周期が長いほど力のある波です(例: うねり1m・周期11秒で約5.4)。一覧の数字はうねりの高さです</li>
<li><b class='r3'>◎</b> 波力3〜12(うねり1m前後で周期8秒以上〜1.5m・10秒くらい)</li>
<li><b class='r2'>○</b> 波力2〜3(少し小さめ)、12〜20(大きめ)</li>
<li><b class='r1'>△</b> 波力0.5〜2(小さい・力がない)、20以上(上級者向け)。この2つは風が良くても最高△</li>
<li><b class='r0'>×</b> 波力0.5未満(フラット)、または風波を含めた波が2.5mを超える荒れた海</li>
<li>風: 5m/s以上のオンショアで一段下げ、6m/s以下のオフショアで一段上げます</li>
<li>オフショア=陸から海へ吹く風(波の面が整いやすい)、オンショア=海から陸へ吹く風</li>
<li>波の高さ=風波とうねりを合わせた高さ。うねり・周期=遠くから届く波(うねり)だけの高さと周期(周期が長いほど力のある波)</li>
<li>気温は浜付近、水温は沖合の海面水温の予報</li>
<li>潮: その時間帯にある満潮・干潮の時刻。なければ上げ潮・下げ潮(沖合の潮位の予報から求めた目安で、港の潮汐表とは数十分ずれることがあります)</li>
<li>⚠️ 警報・注意報は、浜のある市町村に気象庁がいま出しているもの</li>
<li>🚻 トイレ・🚿 シャワー: <a href='https://japan.surfride.net/'>surfride.net</a> の設備の情報で、あるポイントの名前の横に付けています(分からないポイントは付けていません)</li>
<li>うねり: 高さ(向き)。🎯 はうねりの向きが浜の向き(正面)から30°以内の時間帯。うねりがまっすぐ入り、波が立ちやすい目安です</li>
<li>ウェット予想: 水温と朝(6〜11時)の気温それぞれから目安を出し、厚い方にしています。水温は沖合の予報なので、岸近くに合わせて1.5℃低く見ています。水温: 25℃以上 ボードショーツ・ラッシュガード、23℃以上 スプリング、20℃以上 シーガル、17℃以上 3/2mm フル、14℃以上 5/3mm フル、11℃以上 5/3mm セミドライ+ブーツ、それ未満 5mm セミドライ+ブーツ・グローブ・キャップ。朝の気温: 27℃以上 ボードショーツ、24℃以上 スプリング、20℃以上 シーガル、14℃以上 3/2mm フル、8℃以上 5/3mm フル、それ未満 5/3mm セミドライ(一般的な目安です)</li>
<li>📷 ライブカメラ: <a href='https://www.surfers-ocean.com/'>サーファーズオーシャン</a>・<a href='https://japan.surfride.net/'>surfride.net</a> に載っているカメラのうち、ポイントかそのすぐ近くを映すものです(停止中のものは除く)。提供元のサイトが開きます</li>
<li>一覧は選んだ日の朝・昼のよいほうの判定と、その時間帯のうねり・周期・風です。ポイント名を押すと、朝・昼の詳しい予報(最大16日間)のページに移ります</li>
<li>今日〜明後日の日付を押すと、6〜16時の時間ごとの予報が開きます(今日・明日は1時間ごと、明後日は3時間ごと。その時刻の値)</li></ul>
<h2>このページについて</h2>
<p>朝(6〜11時)と昼(12〜17時)の平均です。波の高さは沖合の予報で、浜では地形や向きにより小さくなることが多いです。</p></details>
""")


def _size(h):
    return ("フラット" if h < 0.3 else "ひざ〜もも" if h < 0.6 else "こし〜むね" if h < 1.2
            else "かた〜あたま" if h < 2.0 else "頭オーバー")


def _swell(p):
    return ("長い周期の良いうねり" if p >= 10 else "まとまりのあるうねり" if p >= 7
            else "短めの周期の風波まじり" if p >= 5 else "まとまりのない風波")




# ---------------------------------------------------------------- トップ

# ---------------------------------------------------------------- トップ(ジャンルを選ぶだけ)
# ジャンルのカード: (ページ, クラス, アイコン, 名前, ひとこと, 写真のファイル名)
GENRES = [("mountains.html", "g-mt", "⛰️", "登山", "山頂の天気・風・体感温度から登山日和を判定", "mountain"),
          ("snow.html", "g-snow", "🏂", "スノーボード", "予想新雪・パウダー期待・営業状況", "snow"),
          ("surf.html", "g-surf", "🏄", "サーフィン", "うねり・周期・風向きから波を判定", "surf")]
# 写真が届くまでの仮の絵(ジャンルの色で描く山・雪山・波)
GENRE_ART = {
    "g-mt": "<svg viewBox='0 0 200 100' preserveAspectRatio='xMidYMax slice' aria-hidden='true'><path d='M0 100 L55 38 L80 62 L120 18 L200 100Z' fill='currentColor' opacity='.35'/><path d='M0 100 L70 55 L105 78 L150 45 L200 100Z' fill='currentColor' opacity='.6'/></svg>",
    "g-snow": "<svg viewBox='0 0 200 100' preserveAspectRatio='xMidYMax slice' aria-hidden='true'><path d='M20 100 L100 22 L180 100Z' fill='currentColor' opacity='.45'/><path d='M100 22 L80 42 L92 40 L100 50 L108 40 L120 42Z' fill='#fff' opacity='.9'/><g fill='#fff' opacity='.8'><circle cx='40' cy='25' r='2.5'/><circle cx='160' cy='18' r='2'/><circle cx='150' cy='50' r='2.5'/><circle cx='30' cy='60' r='2'/><circle cx='70' cy='15' r='1.8'/></g></svg>",
    "g-surf": "<svg viewBox='0 0 200 100' preserveAspectRatio='xMidYMax slice' aria-hidden='true'><circle cx='150' cy='38' r='16' fill='currentColor' opacity='.35'/><path d='M0 70 Q25 55 50 70 T100 70 T150 70 T200 70 V100 H0Z' fill='currentColor' opacity='.45'/><path d='M0 82 Q25 70 50 82 T100 82 T150 82 T200 82 V100 H0Z' fill='currentColor' opacity='.7'/></svg>",
}


def top_page(updated):
    """トップページ: サイト名・ひとこと・更新時刻と、3ジャンルの大きなカード。
    写真(data/top/<名前>.jpg)があれば背景に使い、なければジャンルの色の絵で表示する"""
    cards = ""
    for page, cls, icon, name, desc, photo in GENRES:
        has_photo = (ROOT / "data/top" / f"{photo}.jpg").exists()
        pos = {"mountain": "center 35%", "snow": "center 40%"}.get(photo, "center")   # 写真の見せたい所(富士山と稜線、日の出とシュプール)
        bg = (f"<span class='gphoto' style=\"background-image:url('top/{photo}.jpg');background-position:{pos}\"></span>" if has_photo
              else f"<span class='gart'>{GENRE_ART[cls]}</span>")
        body = ("" if has_photo else f"<span class='gbody'><span class='gname'><span class='gicon' aria-hidden='true'>{icon}</span>{name}</span>"
                                       f"<span class='gdesc'>{desc}</span></span>")
        cards += (f"<a class='gcard {cls}{' has-photo' if has_photo else ''}' href='{page}' aria-label='{name}'>{bg}{body}</a>")
    return (f"<section class='hero'><h1>山と雪と波</h1><p class='catch'>週末のコンディションをチェック</p>"
            f"<p class='muted'>更新 {updated.month}/{updated.day} {updated:%H:%M} · 次の更新は{next_update(updated)}時すぎの予定</p></section>"
            f"<nav class='genres' aria-label='ジャンル'>{cards}</nav>")


# ---------------------------------------------------------------- 共通レイアウト
CSS = """
:root{color-scheme:light;--bg:#f6f7f5;--card:#fff;--line:#e2e4df;--text:#14171a;--sub:#55595e;--muted:#868a8f;
--accent:#1f6fb2;--r3:#2F855A;--r2:#327EB5;--r1:#B7791F;--r0:#C2413B;--sat:#2a6fd6;--sun:#d03b3b;--hl:#e8f1fb;--hyaku:#c4312f;--hyaku-bg:#fdecea;--fav:#e0245e}
@media (prefers-color-scheme:dark){:root{color-scheme:dark;--bg:#151617;--card:#1e2022;--line:#34373a;--text:#eef0f2;
--sub:#b9bdc2;--muted:#8c9196;--accent:#6aaee8;--r3:#47c47c;--r2:#5fa3ef;--r1:#e0a43a;--r0:#ef6b69;--sat:#6aa6f0;
--sun:#f07a7a;--hl:#1f2a36;--hyaku:#ff8a87;--hyaku-bg:#3a2222;--fav:#ff6b8f}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);
font:15px/1.65 system-ui,-apple-system,"Hiragino Sans","Noto Sans JP","Yu Gothic UI",sans-serif}
header{background:var(--card);border-bottom:1px solid var(--line);position:sticky;top:0;z-index:5}
nav{max-width:1000px;margin:0 auto;padding:0 16px;display:flex;gap:4px;overflow-x:auto}
nav a{padding:12px 12px;color:var(--sub);text-decoration:none;white-space:nowrap;border-bottom:3px solid transparent}
nav a.on{color:var(--text);border-color:var(--accent);font-weight:600}
main{max-width:1000px;margin:0 auto;padding:20px 16px 48px}
h1{font-size:22px;margin:4px 0 6px}h2{font-size:17px;margin:24px 0 8px}h3{font-size:16px;margin:0 0 8px}
h3 small,li small{color:var(--muted);font-weight:400}
.muted{color:var(--muted);font-size:13px}.st{color:var(--muted)}.st.r3{color:var(--r3)}.updated{margin:0 0 8px}
a{color:var(--accent)}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin:12px 0}
.card h2{margin-top:0}.card h2 a{text-decoration:none;color:var(--text)}
.card ul{padding-left:18px;margin:6px 0}.card.big{margin:0 0 12px}
.sub td.l{white-space:normal;min-width:120px}
.mapbtn{display:inline-block;font-size:12px;padding:3px 10px;border-radius:99px;border:1px solid var(--line);
background:var(--card);color:var(--text);text-decoration:none;white-space:nowrap;box-shadow:0 1px 2px rgba(0,0,0,.12)}
.mapbtn:hover{border-color:var(--accent)}
/* 「飲食店」: 押すとジャンルのボタンがその場に並ぶ(浮かせると表の固定列に隠れるため) */
.menu{display:inline-block;vertical-align:top}
.menu>summary{list-style:none;cursor:pointer}.menu>summary::-webkit-details-marker{display:none}
.menu>summary::after{content:' ▼';font-size:10px}.menu[open]>summary::after{content:' ▲'}
.menu[open]>summary{background:var(--accent);border-color:var(--accent);color:#fff}
.menu.info[open]{display:block}.info-body{margin:2px 0 10px;padding:8px 12px;border:1px solid var(--line);border-radius:8px;font-size:13px;line-height:1.9;white-space:normal}.info-body .muted{display:inline-block;min-width:9.5em}.info-body .about{margin:0 0 6px;line-height:1.7}
.menu-list{display:inline;margin-left:4px}.menu-list .mapbtn{margin:0 4px 4px 0}.mapbtn img{vertical-align:-2px;margin-right:4px;border-radius:3px}
tr.detail .mapbtn{margin:0 0 8px}
.xlink{display:block;color:inherit;text-decoration:none}.xlink b{color:var(--accent);text-decoration:underline;
text-decoration-thickness:1px;text-underline-offset:3px}.xlink b::after{content:' ›';text-decoration:none;display:inline-block;margin-left:2px}
.sub .dt,.sub td.l,.grid .nm{position:sticky;left:0;z-index:1;background:var(--card)}
tr.detail .sub .dt{background:var(--hl)}
tr.detail td>*{position:sticky;left:8px;max-width:calc(100vw - 72px)}.sub .dt{box-shadow:1px 0 0 var(--line)}
.hbtn{all:unset;cursor:pointer;color:inherit;text-decoration:underline;text-decoration-thickness:1px;text-underline-offset:3px}
.hbtn[aria-expanded='false']::after{content:' ▼';font-size:10px}.hbtn[aria-expanded='true']::after{content:' ▲';font-size:10px}
.hbtn:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
tr.hr td{font-size:12px;color:var(--sub);padding-top:3px;padding-bottom:3px}
tbody[id],section[id]{scroll-margin-top:56px}   /* 上に固定したメニューに隠れないように */
.alert{margin:6px 0;color:var(--r0);font-size:13px;font-weight:600}.alert small{font-weight:400;margin-left:4px}
.tag.danger{background:var(--hyaku-bg);color:var(--r0);font-weight:600}
.star{all:unset;cursor:pointer;display:inline-block;font-size:13px;padding:3px 12px;margin:0 0 8px;border-radius:99px;
border:1px solid var(--line);background:var(--card);color:var(--text);box-shadow:0 1px 2px rgba(0,0,0,.12)}
.star[aria-pressed='true']{border-color:var(--fav);font-weight:600}
.favmark{color:var(--fav)}
.star:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
tr.detail .star{display:table}tr.detail .btnrow .star{display:inline-block;vertical-align:top}h3+.star{margin-top:-2px}
.favbox h3{font-size:14px;margin:4px 0}
.intro{margin:0 0 8px;font-size:14px;color:var(--text)}
.flw{font:inherit;color:var(--accent);background:none;border:0;padding:0;text-decoration:underline dotted;text-underline-offset:3px;cursor:pointer}
.flwimg{margin:0 0 10px}.flwimg img{display:block;width:100%;max-width:360px;height:auto;border-radius:8px}.flwimg figcaption{font-size:11px;color:var(--muted);margin-top:4px;max-width:360px}
.fac{font-size:12px}
.hinan{display:inline-block;width:17px;height:17px;border-radius:50%;background:var(--r3);color:#fff;font-size:10.5px;line-height:17px;text-align:center;font-weight:700;vertical-align:1px;margin-right:1px}
.frz{display:inline-block;width:17px;height:17px;border-radius:50%;background:var(--r2);color:#fff;font-size:10.5px;line-height:17px;text-align:center;font-weight:700;vertical-align:1px}
.obs{margin:6px 0}.obs b{display:inline!important}  /* 表の中の b{display:block} に負けないように */
.comment{background:var(--hl);border-radius:8px;padding:8px 12px;margin:0 0 8px;font-size:14px}
.sub.mini td,.sub.mini th{text-align:center;padding:4px 6px}.sub.mini td b{display:block}
.sub.mini td.l{text-align:left}
@media (min-width:600px){.sub.top{table-layout:fixed}.sub.top col.nmcol{width:40%}}
/* 週末ページの表は、スマホで横にずらさなくても見えるよう、画面の幅に収めて文字を折り返す */
@media (max-width:599px){.card{padding:12px 10px}.sub.top,.sub.mini{table-layout:fixed}.sub.top col.nmcol{width:36%}.sub.mini.surf th:first-child{width:30%}.sub.top th,.sub.top td,.sub.mini th,.sub.mini td{white-space:normal;padding:4px 3px;overflow-wrap:anywhere}.sub.top td.l,.sub.mini td.l{min-width:0}}.sub.mini.surf td:not(.l),.sub.mini.surf th{padding:4px 2px}
.scroll{overflow-x:auto}
table{border-collapse:collapse;font-variant-numeric:tabular-nums}
.sub{width:100%}.sub th,.sub td{padding:6px 8px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}
.sub th{color:var(--sub);font-weight:500;font-size:13px}
.grid{width:100%;background:var(--card);border:1px solid var(--line);border-radius:12px;overflow:clip}
/* 一覧の日付の行は、上のメニューの下に固定して残す(--hd はメニューの高さ。JS で測る)。外枠を横スクロールの入れ物にすると固定が効かないので、表は画面の幅に収める(とても狭い画面だけ横スクロール) */
.gridwrap{overflow-x:clip}.grid thead th{position:sticky;top:var(--hd,50px);z-index:2;background:var(--card)}
@media (max-width:359px){.gridwrap{overflow-x:auto}}
.grid th,.grid td{padding:4px 2px;text-align:center;border-bottom:1px solid var(--line)}
.grid thead th{font-size:12px;color:var(--sub);font-weight:500;padding:8px 2px}
.grid td b{display:block;font-size:16px;line-height:1.2}.grid td small{font-size:12px}
.grid .nm{text-align:left;min-width:120px;padding-left:10px}
@media (max-width:480px){.grid.surf .nm{min-width:92px;padding-left:8px}.grid.surf td small{font-size:11px}}
.nm button{all:unset;cursor:pointer;display:block}.nm button span{font-weight:600}
.nm button small{display:block;color:var(--muted);font-size:11px;font-weight:400}
.nm button:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
tr.detail td{background:var(--hl);text-align:left;padding:8px}
small{font-size:12px}
.sat{color:var(--sat)}.sun{color:var(--sun)}
.r3{color:var(--r3)}.r2{color:var(--r2)}.r1{color:var(--r1)}.r0{color:var(--r0)}
.big{font-weight:700;color:var(--r2)}.mid{font-weight:600}
.tag{display:inline-block;font-size:11px;padding:0 6px;border-radius:99px;background:var(--hl);color:var(--accent)}
.mk{display:inline-block;width:24px;height:24px;vertical-align:-5px;font-size:0;background:url(kansai.svg) center/contain no-repeat}.mk.hyaku{background-image:url(hyaku.svg)}.mk.hana{background-image:url(hana.svg)}
.tag.warn{color:var(--r1)}
.filters{display:flex;flex-direction:column;gap:8px;margin:8px 0 12px}
#q{font:inherit;font-size:16px;padding:8px 12px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--text);max-width:320px}
.chips{display:flex;flex-wrap:wrap;gap:6px}.chips[hidden]{display:none}
.chip{font:inherit;font-size:13px;padding:4px 12px;border-radius:99px;border:1px solid var(--line);background:var(--card);color:var(--sub);cursor:pointer}
.chip.on{background:var(--accent);border-color:var(--accent);color:var(--on-g,#fff)}
.note{color:var(--sub);font-size:13px;margin-top:24px}.note ul{padding-left:18px}
details.note>summary{cursor:pointer;font-size:15px;font-weight:600;color:var(--text);padding:10px 0;list-style:none}
details.note>summary::-webkit-details-marker{display:none}details.note>summary::after{content:' ▼';font-size:11px}
details.note[open]>summary::after{content:' ▲'}
/* スマホ: 指で押しやすい大きさに(目安 40px 以上)。絞り込みのボタンは1列にして横にスワイプ */
@media (max-width:599px){
.mapbtn,.star{font-size:13px;padding:9px 14px}.chip{padding:8px 14px}
.hbtn{display:inline-block;padding:8px 0}.flw{padding:6px 0}
.chips{flex-wrap:nowrap;overflow-x:auto;scrollbar-width:none;margin:0 -16px;padding:0 16px 2px}.chips::-webkit-scrollbar{display:none}
.chip{flex:none}}
/* ジャンルの色(仕様書): メイン・背景・薄いカード・文字 */
.g-mt{--g:#315D43;--g-bg:#EAF1E9;--g-card:#F1F4F0;--g-ink:#244832;--on-g:#fff}
.g-snow{--g:#367FB6;--g-bg:#EAF3FA;--g-card:#EDF4FA;--g-ink:#244866;--on-g:#fff}
.g-surf{--g:#C65A3B;--g-bg:#FBEDE7;--g-card:#FFF3ED;--g-ink:#713D30;--on-g:#fff}
@media (prefers-color-scheme:dark){
.g-mt{--g:#7FB894;--g-bg:#1C2620;--g-card:#202B24;--g-ink:#CFE3D5;--on-g:#10170f}
.g-snow{--g:#7DB6E3;--g-bg:#1A2430;--g-card:#1E2935;--g-ink:#CFE2F2;--on-g:#0d1620}
.g-surf{--g:#E8957C;--g-bg:#2C201C;--g-card:#33241F;--g-ink:#F3D5CB;--on-g:#1f120e}}
/* トップ: サイト名と3ジャンルの大きなカード(スマホで1画面に収める) */
.hero{text-align:center;padding:8px 0 14px}.hero h1{font-size:28px;margin:0;letter-spacing:.04em}
.hero .catch{margin:2px 0 4px;color:var(--sub);font-size:15px}.hero .muted{margin:0}
.genres{display:grid;gap:12px}
.gcard{position:relative;display:flex;align-items:flex-end;min-height:max(130px,calc((100svh - 260px) / 3));border-radius:16px;overflow:hidden;
text-decoration:none;color:var(--g-ink);background:var(--g-bg);border:1px solid color-mix(in srgb,var(--g) 25%,transparent);
box-shadow:0 2px 8px rgba(0,0,0,.08);transition:transform .15s}
.gcard:active{transform:scale(.98)}.gcard:focus-visible{outline:3px solid var(--g);outline-offset:2px}
.gart{position:absolute;inset:0;color:var(--g);-webkit-mask-image:linear-gradient(to right,transparent 0,#000 55%);mask-image:linear-gradient(to right,transparent 0,#000 55%)}.gart svg{position:absolute;inset:0;width:100%;height:100%}
.gphoto{position:absolute;inset:0;background-size:cover;background-repeat:no-repeat}

.gbody{position:relative;z-index:1;padding:14px 16px;flex:1;min-width:0}
.gname{display:flex;align-items:center;gap:8px;font-size:24px;font-weight:700;line-height:1.2}
.gicon{display:inline-grid;place-items:center;width:40px;height:40px;border-radius:12px;background:var(--g);font-size:22px}
.gcard.has-photo .gicon{background:color-mix(in srgb,var(--g) 85%,transparent)}
.gdesc{display:block;margin-top:4px;font-size:13px;opacity:.85}
.garrow{position:relative;z-index:1;align-self:center;flex:none;width:34px;height:34px;padding:6px;margin-right:12px;border-radius:50%;background:var(--card);color:var(--g);box-shadow:0 1px 3px rgba(0,0,0,.15)}

@media (min-width:720px){.genres{grid-template-columns:repeat(3,1fr)}.gcard{min-height:240px}}
/* ジャンルのページ: 背景とボタンの色をジャンルの色に */
body.g-mt,body.g-snow,body.g-surf{background:var(--g-bg);--accent:var(--g)}
.ghead h1{display:flex;align-items:center;gap:10px;margin:0;color:var(--g-ink)}
.ghead .gicon{width:36px;height:36px;font-size:20px}
.gdesc2{margin:2px 0 10px;font-size:13px;color:var(--sub)}
/* 切替(おすすめ/お気に入り/全スポット) */
.tabs{display:grid;grid-template-columns:repeat(3,1fr);gap:4px;padding:4px;margin:0 0 10px;background:var(--card);border:1px solid var(--line);border-radius:12px}
.tab{font:inherit;font-size:14px;font-weight:600;padding:9px 4px;border:0;border-radius:9px;background:none;color:var(--sub);cursor:pointer}
.tab[aria-selected='true']{background:var(--g);color:var(--on-g)}
body:is(.g-mt,.g-snow,.g-surf) #q{width:100%;max-width:none;margin:0 0 8px;font-size:16px}
.chips.scrollx{flex-wrap:nowrap;overflow-x:auto;scrollbar-width:none;margin:0 -16px 8px;padding:0 16px 2px}
.chips.scrollx::-webkit-scrollbar{display:none}.chips.scrollx .chip{flex:none}
.chips.wrap{flex-wrap:wrap}
/* 地域: 折り返して全部見せる(横にずらさなくても選べる) */
.chips.regions{gap:5px;margin:0 0 8px;padding:0;overflow:visible;flex-wrap:wrap}.chips.regions .chip{padding:5px 7px;font-size:12px;line-height:1.4}
.chips.regions.sub{padding:6px 8px;background:var(--g-card);border-radius:10px}.fpanel .chips{flex-wrap:wrap;overflow:visible;margin:0;padding:0}
/* 日付: 「土日」と、カレンダーのようなマス目(8列) */
.dates{display:flex;gap:6px;margin:0 0 8px;align-items:stretch}
.dates .we{flex:none;display:flex;flex-direction:column;justify-content:center;align-items:center;padding:4px 10px;line-height:1.2;font-weight:600}
.dates .we small{font-size:10px;font-weight:400}
.dates{flex-direction:column}.dhint{margin:0;font-size:11px;color:var(--muted)}
.cal{flex:1;display:grid;grid-template-columns:repeat(8,1fr);gap:4px}
.cal .chip{padding:3px 0;border-radius:8px;line-height:1.15;text-align:center;font-size:13px;min-width:0}
.cal .chip span{display:block}.cal .chip small{display:block;font-size:10px}
.chip .sat{color:var(--sat)}.chip .sun{color:var(--sun)}.chip.on .sat,.chip.on .sun{color:var(--on-g,#fff)}
.frow{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin:0 0 8px}
.fbtn{font:inherit;font-size:13px;padding:7px 14px;border-radius:99px;border:1px solid var(--g);background:var(--card);color:var(--g);cursor:pointer}
.fbtn::after{content:' ▼';font-size:10px}.fbtn[aria-expanded='true']::after{content:' ▲'}
.active{display:flex;gap:6px;flex-wrap:wrap}
.achip,.aclear{font:inherit;font-size:12px;padding:4px 10px;border-radius:99px;border:0;background:var(--g-card);color:var(--g-ink);cursor:pointer}
.aclear{background:none;text-decoration:underline;color:var(--sub)}
.fpanel{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:10px 12px;margin:0 0 10px}
.fgroup+.fgroup{margin-top:8px}.flabel{display:block;font-size:12px;color:var(--sub);margin-bottom:4px}
.listbar{display:flex;justify-content:space-between;align-items:center;margin:4px 0 8px;font-size:13px;color:var(--sub)}
.sortl{display:flex;align-items:center;gap:6px}.sortl[hidden]{display:none}
.sortl select{font:inherit;font-size:13px;padding:5px 8px;border-radius:8px;border:1px solid var(--line);background:var(--card);color:var(--text)}
/* スポットのカード: 上段に名前・印・ハート、中段に標高・県、下段に選んだ日の判定 */
.spots{display:grid;gap:8px}
.spot{position:relative;background:var(--card);border:1px solid var(--line);border-radius:12px;padding:9px 12px 10px}
.spot-top{display:flex;align-items:center;gap:4px;padding-right:40px;min-height:26px}
.spot-link{font-weight:700;font-size:16px;color:var(--text);text-decoration:none}
.spot-link::after{content:'';position:absolute;inset:0}       /* カードのどこを押しても詳細へ */
.spot .mk,.spot .tag{position:relative}
.heart{position:absolute;top:4px;right:4px;z-index:2;width:44px;height:44px;border:0;background:none;font-size:22px;line-height:1;color:var(--fav);cursor:pointer}
.spot-sub{font-size:12px;color:var(--muted);margin:0 0 6px}
.spot-days{display:grid;grid-template-columns:repeat(var(--n,1),1fr);gap:6px}.spot-days.n2{--n:2}
.sd{display:flex;flex-wrap:wrap;align-items:center;column-gap:6px;row-gap:0;background:var(--g-card);border-radius:8px;padding:4px 8px 5px;min-width:0}
.sd-l{flex-basis:100%;font-size:11px;line-height:1.5;color:var(--sub);white-space:nowrap}.spot-days.n1 .sd-l{flex-basis:auto;margin-right:4px}.sd-l.sat{color:var(--sat)}.sd-l.sun{color:var(--sun)}
.sd .mark{font-size:19px;line-height:1.2}.sd-wx{font-size:15px}.sd-v{font-size:12.5px;white-space:nowrap}
.snow-v{font-size:13px;white-space:nowrap}.snow-v{font-weight:700;font-size:19px;color:var(--g-ink)}.snow-v small{font-size:11px;font-weight:400;margin:0 1px}
.snow-v.big{color:var(--g)}.tag.pw{margin-left:auto}
.warr{display:inline-block;margin-right:2px;font-weight:700;color:var(--g)}.sd-v.wind small{font-size:11px}
.slots{flex-basis:100%;font-size:11px;color:var(--sub)}.slots b{font-size:12px;margin:0 2px 0 1px}
.empty{background:var(--card);border-radius:12px;padding:16px;text-align:center;color:var(--sub)}
.back{margin:0 0 6px;font-size:14px}.back a{display:inline-flex;align-items:center;gap:2px;text-decoration:none;padding:6px 0}.chev{width:18px;height:18px}.spoth{display:flex;align-items:center;gap:4px;flex-wrap:wrap}
.spotsub{margin:-2px 0 6px;color:var(--muted);font-size:13px}
@media (min-width:720px){.spots{grid-template-columns:1fr 1fr}}
footer{max-width:1000px;margin:0 auto;padding:16px;color:var(--muted);font-size:12px;border-top:1px solid var(--line)}
"""

JS = """
// お気に入り: 見ている人のブラウザに保存する(保存できない環境でも表示は崩れない)
const FKEY='weather-site-favs';
let favs=null;
try{favs=JSON.parse(localStorage.getItem(FKEY));}catch(e){}
if(!Array.isArray(favs))favs=DEFAULT_FAVS.slice();
try{if(!localStorage.getItem(FKEY+'-surf')){if(!favs.some(f=>f.startsWith('w:')))favs=favs.concat(DEFAULT_FAVS.filter(f=>f.startsWith('w:')));
  localStorage.setItem(FKEY+'-surf','1');localStorage.setItem(FKEY,JSON.stringify(favs));}}catch(e){}
const isFav=f=>favs.includes(f);
let apply=()=>{},region='';
// 詳細は開くまで <template> に入れておき(ページを軽くするため)、初めて開いたときに組み立てる
const paintStars=root=>root.querySelectorAll('.star').forEach(b=>{const on=isFav(b.dataset.fid);
  b.innerHTML=on?"<span class='favmark'>♥</span> お気に入り":'♡ お気に入りに追加';b.setAttribute('aria-pressed',String(on));});
const paintFavs=()=>{
  paintStars(document);
  document.querySelectorAll('tbody.mt[data-fid]').forEach(b=>{b.dataset.fav=isFav(b.dataset.fid)?'1':'';});
  document.querySelectorAll('.favonly').forEach(b=>{b.hidden=!isFav(b.dataset.fid);});
  document.querySelectorAll('.favhead').forEach(h=>{h.hidden=!h.closest('table').querySelector('.favonly:not([hidden])');});
  document.querySelectorAll('.favbox').forEach(box=>{box.hidden=!box.querySelector('.favonly:not([hidden])');
    const hint=box.nextElementSibling;if(hint&&hint.classList.contains('favhint'))hint.hidden=!box.hidden;});
};
const openDetail=(tb,open)=>{const d=tb.querySelector('tr.detail'),b=tb.querySelector('.nm button');if(!d)return;
  const t=d.querySelector('template');if(t&&open){t.replaceWith(t.content.cloneNode(true));paintStars(d);}
  const lz=d.querySelector('.lazy');
  if(lz&&open&&!lz.dataset.state){lz.dataset.state='1';lz.innerHTML="<p class='muted'>読み込み中…</p>";
    fetch(lz.dataset.src).then(r=>{if(!r.ok)throw 0;return r.text();})
      .then(h=>{lz.outerHTML=h;paintStars(d);if(location.hash==='#'+tb.id)tb.scrollIntoView({block:'start'});})
      .catch(()=>{lz.dataset.state='';lz.innerHTML="<p class='muted'>詳細を読み込めませんでした。もう一度開くか、ページを再読み込みしてください</p>";});}
  d.hidden=!open;if(b)b.setAttribute('aria-expanded',String(open));};
paintFavs();
const setHd=()=>{const h=document.querySelector('header');if(h)document.documentElement.style.setProperty('--hd',h.offsetHeight+'px');};
setHd();addEventListener('resize',setHd);
const q=document.getElementById('q');
if(q){
  apply=()=>{const t=q.value.trim();
    document.querySelectorAll('tbody.mt').forEach(b=>{
      const inRegion=!region||b.dataset.region===region||(region==='fav'&&b.dataset.fav)||(region==='powder'&&b.dataset.powder)||(region==='hana'&&b.dataset.hana)||(region==='hyaku'&&b.dataset.hyaku)||(region==='kansai'&&b.dataset.kansai)
        ||(region.startsWith('pref:')&&(b.dataset.prefs||'').split(',').includes(region.slice(5)));
      b.hidden=!(inRegion&&(!t||b.dataset.name.includes(t)));});};
  q.addEventListener('input',apply);
  document.querySelectorAll('.chip').forEach(c=>c.addEventListener('click',()=>{
    document.querySelectorAll('.chip').forEach(x=>x.classList.toggle('on',x===c));
    region=c.dataset.r;apply();
    // 関西の県ごとのボタン(登山)は、「関西」か県を選んでいる間だけ出し、県を選んでいる間は「関西」も選んだ表示に
    const pr=document.getElementById('prefs');
    if(pr){const k=region==='近畿'||region.startsWith('pref:');pr.hidden=!k;
      const kc=document.querySelector(".chip[data-r='近畿']");if(kc&&k)kc.classList.add('on');}}));
}
// ボタンの反応はページ全体で1か所で受ける(あとから組み立てた詳細の中のボタンにも効く)
document.addEventListener('click',ev=>{
  const star=ev.target.closest('.star');
  if(star){const f=star.dataset.fid;
    favs=isFav(f)?favs.filter(x=>x!==f):favs.concat([f]);
    try{localStorage.setItem(FKEY,JSON.stringify(favs));}catch(e){}
    paintStars(document);
    document.querySelectorAll(`[data-fid="${CSS.escape(f)}"]`).forEach(b=>{if(b.matches('tbody.mt'))b.dataset.fav=isFav(f)?'1':'';});
    if(region==='fav')apply();
    if(window.onFav)window.onFav();
    return;}
  const hb=ev.target.closest('.hbtn');
  if(hb){const open=hb.getAttribute('aria-expanded')!=='true';
    for(let r=hb.closest('tr').nextElementSibling;r&&!r.querySelector('.hbtn');r=r.nextElementSibling)if(r.classList.contains('hr'))r.hidden=!open;
    hb.setAttribute('aria-expanded',String(open));return;}
  const fw=ev.target.closest('.flw');
  if(fw){const fig=fw.closest('.intro').nextElementSibling;const open=fig.hidden;fig.hidden=!open;
    fw.setAttribute('aria-expanded',String(open));return;}
  const nb=ev.target.closest('.nm button');
  if(nb){const tb=nb.closest('tbody');openDetail(tb,tb.querySelector('tr.detail').hidden);return;}
  // 「飲食店」などの一覧は、外を押したら閉じる
  document.querySelectorAll('details.menu[open]').forEach(m=>{if(!m.contains(ev.target))m.open=false;});
});
// 開いたときは「★お気に入り」の絞り込みを選んだ状態にする(このページにお気に入りがあるとき、または #fav で来たとき)。
// トップページから特定の場所(#その場所)に来て、そこがお気に入りでなければ「すべて」のまま(隠れないように)
const target=location.hash&&location.hash!=='#fav'&&document.getElementById(location.hash.slice(1));
const favChip=document.querySelector(".chip[data-r='fav']");
if(favChip&&(location.hash==='#fav'||(document.querySelector('tbody.mt[data-fav="1"]')&&(!target||target.dataset.fav))))favChip.click();
if(target){openDetail(target,true);target.scrollIntoView({block:'start'});}
"""

LIST_JS = r"""
// ---- 一覧(3ジャンル共通): 切替・日付・検索・地域・絞り込み・並び順を組み合わせて、カードを描き直す
(()=>{
const el=document.getElementById('spots-data');if(!el)return;
const G=JSON.parse(el.textContent);
const $=id=>document.getElementById(id);
const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const MARK=['×','△','○','◎'];
const FLAG={k:['🍁','紅葉の見ごろ(予想)'],s:['🌸','桜の見ごろ(予想)'],f:['🌼','花の百名山の花の時期']};
const sum=(a,f)=>a.reduce((s,x)=>s+f(x),0);
// ジャンルごとの違い(表示・絞り込み・並び順・おすすめ)。判定の値はデータのまま使う
const KINDS={
 m:{
  sub:(it,days)=>{const fl=[...new Set(days.flatMap(i=>[...((it.d[i]||[])[4]||'')]))];
    return `${it.e.toLocaleString()}m · ${esc(it.p.join('・'))}`+fl.map(c=>` <span class='fac' title='${FLAG[c][1]}' role='img' aria-label='${FLAG[c][1]}'>${FLAG[c][0]}</span>`).join('');},
  day:(it,i)=>{const d=it.d[i];if(!d)return "<span class='muted'>—</span>";
    return `<b class='r${d[0]} mark'>${MARK[d[0]]}</b><span class='sd-wx'>${d[1]}</span><span class='sd-v'>${d[2]}℃ · ${d[3]}m/s</span>`;},
  region:(it,r)=>r.startsWith('pref:')?it.p.includes(r.slice(5)):r.split('|').includes(it.r),
  filter:{cls:(it,v)=>it.l.includes(v),rate:(it,v,days)=>days.every(i=>it.d[i]&&it.d[i][0]>=+v)},
  key:(it,days)=>[-sum(days,i=>(it.d[i]||[0])[0]),Math.max(...days.map(i=>(it.d[i]||[0,0,0,99])[3]))],
  sort:{score:null,elev:(a,b)=>b.e-a.e,name:(a,b)=>a.y.localeCompare(b.y,'ja')},
  // おすすめ: 選んだ日がすべて○以上の山を、判定の合計が高く風の弱い順に。同じエリアは5座まで、15座
  rec:(xs,days,K)=>{const per={},out=[];
    for(const it of xs.filter(it=>days.every(i=>it.d[i]&&it.d[i][0]>=2)).sort((a,b)=>cmp(K.key(a,days),K.key(b,days)))){
      if((per[it.r]||0)<5){out.push(it);per[it.r]=(per[it.r]||0)+1;}if(out.length===15)break;}
    return out;},
 },
 s:{
  // 営業状況は選んだ最初の日で。予定が分からないものは「未確認」(営業中と推測して出さない)
  st:(it,days)=>{const c=(it.d[days[0]]||[])[6];return c==='o'?"<span class='st r3'>営業中</span>":c==='b'?`<span class='st'>${it.ol||'営業前'}</span>`:c==='a'?"<span class='st'>営業終了</span>":c==='u'?"<span class='st'>未確認</span>":'';},
  sub:(it,days)=>`${esc(it.p)}`+(KINDS.s.st(it,days)?' · '+KINDS.s.st(it,days):''),
  day:(it,i)=>{const d=it.d[i];if(!d)return "<span class='muted'>—</span>";
    return `<span class='snow-v${d[0]>=10?' big':''}'><small>新雪</small>${d[0]}<small>cm</small></span><span class='sd-wx'>${d[1]}</span>`
      +`<span class='sd-v'>${d[2]}〜${d[3]}℃ · ${d[4]}m/s</span>`+(d[5]?"<span class='tag pw'>パウダー期待</span>":'');},
  region:(it,r)=>r.split('|').includes(it.r),
  filter:{snow:(it,v,days)=>sum(days,i=>(it.d[i]||[0])[0])>=+v,open:(it,v,days)=>(it.d[days[0]]||[])[6]==='o'},
  key:(it,days)=>[-sum(days,i=>(it.d[i]||[0])[0])],
  sort:{snow:null,powder:(a,b)=>cmp([-sum(DAYS(),i=>(a.d[i]||[])[5]||0),-sum(DAYS(),i=>(a.d[i]||[0])[0])],[-sum(DAYS(),i=>(b.d[i]||[])[5]||0),-sum(DAYS(),i=>(b.d[i]||[0])[0])]),
        region:(a,b)=>a.ro-b.ro||sum(DAYS(),i=>(b.d[i]||[0])[0])-sum(DAYS(),i=>(a.d[i]||[0])[0]),name:(a,b)=>(a.y||a.n).localeCompare(b.y||b.n,'ja')},
  // おすすめ: 営業中→未確認→営業前・終了の順に、新雪(パウダー期待の日は10cm分を加点)の多い順。同じ地方は5か所まで、15か所(登山と同じ)
  rec:(xs,days)=>{const rank={o:0,u:1,'':1,b:2,a:2},sc=it=>sum(days,i=>{const d=it.d[i];return d?d[0]+(d[5]?10:0):0;});
    const per={},out=[];
    for(const it of xs.filter(it=>!it.bc&&sc(it)>=1).sort((a,b)=>cmp([rank[(a.d[days[0]]||[])[6]||''],-sc(a)],[rank[(b.d[days[0]]||[])[6]||''],-sc(b)]))){
      if((per[it.r]||0)<5){out.push(it);per[it.r]=(per[it.r]||0)+1;}if(out.length===15)break;}
    return out;},
 },
 w:{
  best:(it,i)=>{const d=it.d[i];return d&&d.length?d.reduce((a,b)=>b[1]>a[1]?b:a):null;},     // 朝・昼のよいほう(同じなら朝)
  sub:it=>`${esc(it.p)} · 浜の向き ${esc(it.fc)}`,
  day:(it,i)=>{const d=it.d[i],b=KINDS.w.best(it,i);if(!b)return "<span class='muted'>—</span>";
    const both=d.map(s=>`${s[0]}<b class='r${s[1]}'>${MARK[s[1]]}</b>`).join(' ');
    const arrow=b[6]==null?'':`<span class='warr' style='transform:rotate(${(b[6]+180)%360}deg)' aria-hidden='true'>↑</span>`;
    return `<b class='r${b[1]} mark'>${MARK[b[1]]}</b><span class='sd-v'>うねり${b[3]}m · ${b[4]}秒</span>`
      +`<span class='sd-v wind'>${arrow}${b[5]}m/s <small class='${b[7]==='オフショア'?'r3':b[7]==='オンショア'?'r0':''}'>${b[7]}</small></span><span class='slots'>${both}</span>`;},
  region:(it,r)=>it.r===r,
  filter:{rate:(it,v,days)=>days.every(i=>{const b=KINDS.w.best(it,i);return b&&b[1]>=+v;}),
          wind:(it,v,days)=>days.every(i=>{const b=KINDS.w.best(it,i);return b&&b[7]===v;})},
  key:(it,days)=>[-sum(days,i=>(KINDS.w.best(it,i)||[0,0])[1]),-sum(days,i=>(KINDS.w.best(it,i)||[0,0,0,0])[3])],
  sort:{score:null,height:(a,b)=>Math.max(...DAYS().map(i=>(KINDS.w.best(b,i)||[0,0,0])[2]))-Math.max(...DAYS().map(i=>(KINDS.w.best(a,i)||[0,0,0])[2])),
        period:(a,b)=>Math.max(...DAYS().map(i=>(KINDS.w.best(b,i)||[0,0,0,0,0])[4]))-Math.max(...DAYS().map(i=>(KINDS.w.best(a,i)||[0,0,0,0,0])[4])),
        region:(a,b)=>b.lon-a.lon},
  // おすすめ: 選んだ日のすべてに○以上の時間帯があるポイントを、判定の良い順に(新しい判定は作らず、並べ方だけ)
  rec:(xs,days,K)=>xs.filter(it=>days.every(i=>{const b=KINDS.w.best(it,i);return b&&b[1]>=2;})).sort((a,b)=>cmp(K.key(a,days),K.key(b,days))),
 },
};
const DAYS=()=>st.days;
const cmp=(a,b)=>{for(let i=0;i<a.length;i++){if(a[i]!==b[i])return a[i]-b[i];}return 0;};
const K=KINDS[G.kind];
// 旧URL(mountains.html#m12 など)で来たら、その場所の詳細ページへ
const h=location.hash.slice(1);
if(h&&h!=='fav'){const it=G.items.find(x=>x.k===h);if(it){location.replace(it.u);return;}}
const weIdx=G.we.map(d=>G.days.findIndex(x=>x.d===d)).filter(i=>i>=0);
const st={tab:'',days:weIdx.length?weIdx.slice():[0],region:'',q:'',f:{},sort:$('sort').value};
// 日付のボタン: 土日 + 予報のある日すべて
$('dates').innerHTML=`<div class='cal'>`+G.days.map((d,i)=>`<button type='button' class='chip day' data-g='date' data-v='${i}' aria-label='${d.l}(${d.w})'><span class='${d.c}'>${(i===0||d.l.endsWith('/1'))?d.l:d.l.split('/')[1]}<small>${d.w}</small></span></button>`).join('')+`</div><p class='dhint'>日付は2日まで選べます(押すと選ぶ・外す)</p>`;
const mine=()=>G.items.filter(it=>isFav(it.f));
st.tab=(location.hash==='#fav'||mine().length)?'fav':'rec';      // お気に入りがあればお気に入りで開く
const label={rec:'おすすめ',fav:'お気に入り',all:'スポット一覧'};
const card=it=>{const on=isFav(it.f);
  return `<article class='spot'><div class='spot-top'><a class='spot-link' href='${it.u}'>${esc(it.n)}</a>${it.b||''}`
   +`<button type='button' class='heart' data-hfid='${esc(it.f)}' aria-pressed='${on}' aria-label='${esc(it.n)}をお気に入り${on?'から外す':'に追加'}'>${on?'♥':'♡'}</button></div>`
   +`<div class='spot-sub'>${K.sub(it,st.days)}</div>`
   +`<div class='spot-days n${st.days.length}'>${st.days.map(i=>`<div class='sd'><span class='sd-l ${G.days[i].c}'>${G.days[i].l}(${G.days[i].w})</span>${K.day(it,i)}</div>`).join('')}</div></article>`;};
const chipsOn=()=>document.querySelectorAll('.chip[data-g]').forEach(c=>{const g=c.dataset.g,v=c.dataset.v;
  c.classList.toggle('on',g==='date'?st.days.includes(+v):g==='region'?(v===st.region||(v==='近畿'&&st.region.startsWith('pref:'))):g.startsWith('f:')?(st.f[g.slice(2)]||'')===v:false);});
const render=()=>{
  let xs=st.tab==='fav'?mine():G.items;
  if(st.q)xs=xs.filter(it=>(it.n+it.y).includes(st.q));
  if(st.region)xs=xs.filter(it=>K.region(it,st.region));
  for(const [k,v] of Object.entries(st.f))if(v)xs=xs.filter(it=>K.filter[k](it,v,st.days));
  if(st.tab==='rec')xs=K.rec(xs,st.days,K);
  else{const s=K.sort[st.sort];xs=xs.slice().sort(s||((a,b)=>cmp(K.key(a,st.days),K.key(b,st.days))));}
  $('spots').innerHTML=xs.map(card).join('');
  $('empty').hidden=xs.length>0;
  $('count').textContent=`${label[st.tab]} ${xs.length}件`;
  document.querySelector('.sortl').hidden=st.tab==='rec';
  document.querySelectorAll('.tab').forEach(b=>b.setAttribute('aria-selected',String(b.dataset.tab===st.tab)));
  $('subregions').hidden=!(st.region==='近畿'||st.region.startsWith('pref:'));
  chipsOn();
  // 選んでいる条件(地域・絞り込み・検索)をチップで出す。押すと外れる
  const act=[];
  if(st.q)act.push(['q','「'+esc(st.q)+'」']);
  if(st.region)act.push(['region',esc(document.querySelector(`.chip[data-g='region'][data-v='${CSS.escape(st.region)}']`).textContent)]);
  for(const [k,v] of Object.entries(st.f))if(v)act.push(['f:'+k,esc(document.querySelector(`.chip[data-g='f:${k}'][data-v='${CSS.escape(v)}']`).textContent)]);
  $('active').innerHTML=act.map(([g,l])=>`<button type='button' class='achip' data-clear='${g}'>${l} ×</button>`).join('')
    +(act.length>1?"<button type='button' class='aclear' data-clear='all'>条件をクリア</button>":'');
};
document.addEventListener('click',ev=>{
  const tb=ev.target.closest('.tab');if(tb){st.tab=tb.dataset.tab;render();return;}
  const hb=ev.target.closest('.heart');
  if(hb){const f=hb.dataset.hfid;favs=isFav(f)?favs.filter(x=>x!==f):favs.concat([f]);
    try{localStorage.setItem(FKEY,JSON.stringify(favs));}catch(e){}render();return;}
  const c=ev.target.closest('.chip[data-g]');
  if(c){const g=c.dataset.g,v=c.dataset.v;
    if(g==='date'){const i=+v,k=st.days.indexOf(i);        // 押した日を選ぶ・外す(2日まで。3日目は古い方と入れ替え。0日にはしない)
      if(k>=0){if(st.days.length>1)st.days.splice(k,1);}else{st.days.push(i);if(st.days.length>2)st.days.shift();}
      st.days.sort((a,b)=>a-b);}
    else if(g==='region')st.region=v;else st.f[g.slice(2)]=v;
    render();return;}
  const x=ev.target.closest('[data-clear]');
  if(x){const g=x.dataset.clear;
    if(g==='q'||g==='all'){st.q='';$('q').value='';}
    if(g==='region'||g==='all')st.region='';
    if(g.startsWith('f:'))delete st.f[g.slice(2)];if(g==='all')st.f={};
    render();return;}
  const ft=ev.target.closest('#ftoggle');
  if(ft){const p=$('fpanel');p.hidden=!p.hidden;ft.setAttribute('aria-expanded',String(!p.hidden));}
});
$('q').addEventListener('input',()=>{st.q=$('q').value.trim();render();});
$('sort').addEventListener('change',()=>{st.sort=$('sort').value;render();});
window.onFav=render;
render();
})();
"""

PAGES = [("index.html", "トップ"), ("mountains.html", "登山"), ("snow.html", "スノーボード"),
         ("surf.html", "サーフィン")]


def default_favs():
    """☆をまだ押していない人に最初から入れておくお気に入り(data/favorites.json の山・スキー場)"""
    favs = json.loads((ROOT / "data/favorites.json").read_text(encoding="utf-8"))
    mts = json.loads((ROOT / "data/mountains.json").read_text(encoding="utf-8"))
    return ([fid("m", m) for n in favs.get("mountain", []) for m in mts if m["name"] == n]
            + [f"s:{n}" for n in favs.get("ski", [])] + [f"w:{n}" for n in favs.get("surf", [])])


def next_update(updated):
    """次の定期更新の時(日本時間の0・3・6…時の15分に始まる。GitHub の定期実行は遅れることがある)"""
    return (updated.hour // 3 + 1) * 3 % 24


GENRE_CLASS = {"mountains.html": "g-mt", "snow.html": "g-snow", "surf.html": "g-surf"}


def layout(file, title, body, updated, nav=None):
    """ページの共通の枠。nav は上のメニューで選んだ表示にするページ(詳細ページはそのジャンルの一覧)"""
    nav = nav or file
    bottom_line = f"<p class='muted'>更新: {updated:%Y年%-m月%-d日 %H:%M}(3時間ごとに自動更新)</p>"
    top_line = f"<p class='updated muted'>更新 {updated.month}/{updated.day} {updated:%H:%M} · 次の更新は{next_update(updated)}時すぎの予定</p>"
    menu = "".join(f"<a href='{f}' class='{'on' if f == nav else ''}'>{n}</a>" for f, n in PAGES)
    if file in GENRE_CLASS and "</h1>" in body:      # 更新時刻は一覧のジャンル名(見出し)のすぐ後ろに。詳細ページには出さない
        body = body.replace("</h1>", "</h1>" + top_line, 1) if "class='ghead'" not in body else body.replace("</div>", "</div>" + top_line, 1)
        top_line = ""
    return f"""<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} | 山と雪と波の天気</title>
<meta name="description" content="日本百名山・関西百名山・花の百名山の登山天気、スキー場の降雪、サーフポイントの波と風の予報をまとめたサイト">
<link rel="icon" href="favicon.png" sizes="32x32"><link rel="apple-touch-icon" href="apple-touch-icon.png">
<link rel="manifest" href="manifest.webmanifest"><meta name="theme-color" content="#1f6fb2">
<meta name="apple-mobile-web-app-title" content="山と雪と波"><style>{CSS}</style></head><body class='{GENRE_CLASS.get(nav, "")}'>
<header><nav>{menu}</nav></header>
<main>{body}{'' if file == 'index.html' else bottom_line}</main>
<footer>予報は目安です。登山・海に出る前に、気象庁の警報・注意報と現地の情報を必ず確認してください。<br>
気象データ: <a href="https://open-meteo.com/">Open-Meteo.com</a>(<a href="https://creativecommons.org/licenses/by/4.0/deed.ja">CC BY 4.0</a>) · ゲレンデ積雪: SURF&amp;SNOW · リフトの位置: © OpenStreetMap contributors ·
山の一覧・座標: Wikipedia「日本百名山」「花の百名山」、Wikidata、ヤマレコ「関西百名山」、YAMAP MAGAZINE「花の百名山」 · 花の写真: Wikimedia Commons、iNaturalist(撮影者・ライセンスは各写真の下)</footer>
<script>const DEFAULT_FAVS={json.dumps(default_favs(), ensure_ascii=False)};{JS}{LIST_JS}</script></body></html>"""


def check_output(mts, ski, spots):
    """公開する前の値のチェック。おかしければ例外にして、公開をやめ前回のサイトを残す(Actions の実行は失敗になる)"""
    bad = []
    for what, items, src in (("登山", mts, "mountains"), ("スノーボード", ski, "ski"), ("サーフィン", spots, "surf")):
        total = len(json.loads((ROOT / f"data/{src}.json").read_text(encoding="utf-8")))
        if len(items) < total * 0.8:
            bad.append(f"{what}: 予報が取れたのが{len(items)}/{total}地点")
        if min(len(x["days"]) for x in items) < 5:
            bad.append(f"{what}: 日数が5日未満")
    def rng(what, name, key, v, lo, hi):
        if v is not None and not lo <= v <= hi:
            bad.append(f"{what} {name} {key}={v}")
    for m in mts:
        for d in m["days"]:
            for k in ("tmin", "tmax"):
                rng("登山", m["name"], k, d[k], -50, 45)
            rng("登山", m["name"], "wind", d["wind"], 0, 80)
            rng("登山", m["name"], "rain", d["rain"], -1, 1000)   # 予報の値はわずかにマイナスになることがある
    for s in ski:
        for d in s["days"]:
            for k in ("tmin", "tmax"):
                rng("スノーボード", s["name"], k, d[k], -50, 45)
            rng("スノーボード", s["name"], "gust", d["gust"], 0, 80)
            for k in ("snow", "snow_base", "depth", "rain"):
                rng("スノーボード", s["name"], k, d[k], -1, 2000)
    for s in spots:
        for d in s["days"]:
            for x in d["slots"]:
                rng("サーフィン", s["name"], "height", x["height"], 0, 15)
                rng("サーフィン", s["name"], "wind", x["wind"], 0, 80)
                rng("サーフィン", s["name"], "air", x["air"], -50, 45)
    if bad:
        raise RuntimeError("値のチェックで異常があったので公開しません:\n" + "\n".join(bad[:30]))


def main():
    updated = datetime.now(JST)
    mts, ski, spots = build_mountains(), build_ski(), build_surf()
    alerts, volcanoes = fetch_alerts(), fetch_volcanoes()
    attach_alerts(mts, alerts, volcanoes, True, "mountain")
    attach_alerts(ski, alerts, volcanoes, True, "ski")
    attach_alerts(spots, alerts, volcanoes, False, "surf")
    for prefix, items in (("m", mts), ("s", ski), ("w", spots)):   # 行の id(トップページの「詳細」のリンク先)
        for i, x in enumerate(items):
            x["key"] = f"{prefix}{i}"
    OUT.mkdir(exist_ok=True)
    bodies = {"index.html": ("トップ", top_page(updated)),
              "mountains.html": ("登山天気", mountains_page(mts)),
              "snow.html": ("スノーボード", ski_page(ski)),
              "surf.html": ("サーフィン", surf_page(spots))}
    check_output(mts, ski, spots)
    for f, (title, body) in bodies.items():
        (OUT / f).write_text(layout(f, title, body, updated), encoding="utf-8")
    for old in OUT.glob("[msw]-*.html"):            # 前回の詳細ページを消してから書き出す
        old.unlink()
    for f, (title, body, nav) in SPOT_PAGES.items():
        (OUT / f).write_text(layout(f, title, body, updated, nav), encoding="utf-8")
    shutil.rmtree(OUT / "d", ignore_errors=True)    # 以前の詳細の置き場(今はスポットごとのページ)
    (OUT / "manifest.webmanifest").write_text(json.dumps({     # ホーム画面に追加したときの名前・アイコン
        "name": "山と雪と波の天気", "short_name": "山と雪と波", "start_url": "./index.html", "display": "standalone",
        "background_color": "#f6f7f5", "theme_color": "#1f6fb2",
        "icons": [{"src": f"icon-{n}.png", "sizes": f"{n}x{n}", "type": "image/png"} for n in (192, 512)]},
        ensure_ascii=False), encoding="utf-8")
    for icon in (ROOT / "data/icons").iterdir():     # ボタンのアイコン・サイトのアイコン
        (OUT / icon.name).write_bytes(icon.read_bytes())
    if (ROOT / "data/top").exists():                 # トップのジャンルのカードの写真
        (OUT / "top").mkdir(exist_ok=True)
        for photo in (ROOT / "data/top").glob("*.jpg"):
            (OUT / "top" / photo.name).write_bytes(photo.read_bytes())
    (OUT / "flowers").mkdir(exist_ok=True)
    for photo in (ROOT / "data/flowers").iterdir():  # 花の百名山の花の写真
        (OUT / "flowers" / photo.name).write_bytes(photo.read_bytes())
    print("書き出し:", ", ".join(bodies), "→", OUT)


if __name__ == "__main__":
    main()
