#!/usr/bin/env python3
"""分析原神语音包6.3（中）目录，交叉比对可游玩角色，生成完整报告。"""

import json
import os
import urllib.request
from pathlib import Path
from collections import Counter

GENSHIN_DIR = Path("/Users/foxmacstudio/Desktop/音色下载/原神语音包6.3（中）")
API_BASE = "http://localhost:8000"

# ═══════════════════════════════════════════════════════════════
# Wikipedia 可游玩角色完整数据（2026年5月，含6.x版本）
# ═══════════════════════════════════════════════════════════════
PLAYABLE = {
    "旅行者": {"en": "Traveler", "element": "无", "weapon": "单手剑", "region": "异世界", "stars": 5, "gender": "未知", "va_cn": "空:鹿喆安/荧:宴宁", "desc": "来自异世界的旅行者，寻找失散的血亲"},
    "安柏": {"en": "Amber", "element": "火", "weapon": "弓", "region": "蒙德", "stars": 4, "gender": "女", "va_cn": "蔡书瑾", "desc": "西风骑士团侦察骑士，充满活力的少女"},
    "凯亚": {"en": "Kaeya", "element": "冰", "weapon": "单手剑", "region": "蒙德", "stars": 4, "gender": "男", "va_cn": "孙晔", "desc": "西风骑士团骑兵队长，言辞诙谐"},
    "丽莎": {"en": "Lisa", "element": "雷", "weapon": "法器", "region": "蒙德", "stars": 4, "gender": "女", "va_cn": "钟可", "desc": "西风骑士团图书馆管理员，博学的魔女"},
    "芭芭拉": {"en": "Barbara", "element": "水", "weapon": "法器", "region": "蒙德", "stars": 4, "gender": "女", "va_cn": "宋芳琦", "desc": "蒙德教会的偶像牧师"},
    "雷泽": {"en": "Razor", "element": "雷", "weapon": "双手剑", "region": "蒙德", "stars": 4, "gender": "男", "va_cn": "周帅", "desc": "被狼抚养长大的少年"},
    "香菱": {"en": "Xiangling", "element": "火", "weapon": "长柄武器", "region": "璃月", "stars": 4, "gender": "女", "va_cn": "小N", "desc": "万民堂的天才厨师少女"},
    "北斗": {"en": "Beidou", "element": "雷", "weapon": "双手剑", "region": "璃月", "stars": 4, "gender": "女", "va_cn": "唐雅菁", "desc": "南十字船队船长，豪爽的女强人"},
    "行秋": {"en": "Xingqiu", "element": "水", "weapon": "单手剑", "region": "璃月", "stars": 4, "gender": "男", "va_cn": "唐雅菁", "desc": "飞云商会二少爷，热爱侠义小说"},
    "凝光": {"en": "Ningguang", "element": "岩", "weapon": "法器", "region": "璃月", "stars": 4, "gender": "女", "va_cn": "杜冥鸦", "desc": "璃月七星之天权星，商业巨头"},
    "菲谢尔": {"en": "Fischl", "element": "雷", "weapon": "弓", "region": "蒙德", "stars": 4, "gender": "女", "va_cn": "Mace", "desc": "自称断罪皇女的神秘少女"},
    "班尼特": {"en": "Bennett", "element": "火", "weapon": "单手剑", "region": "蒙德", "stars": 4, "gender": "男", "va_cn": "穆雪婷", "desc": "冒险家协会的倒霉少年"},
    "诺艾尔": {"en": "Noelle", "element": "岩", "weapon": "双手剑", "region": "蒙德", "stars": 4, "gender": "女", "va_cn": "宴宁", "desc": "西风骑士团的女仆骑士"},
    "重云": {"en": "Chongyun", "element": "冰", "weapon": "双手剑", "region": "璃月", "stars": 4, "gender": "男", "va_cn": "kinsen", "desc": "方士世家传人，纯阳之体"},
    "砂糖": {"en": "Sucrose", "element": "风", "weapon": "法器", "region": "蒙德", "stars": 4, "gender": "女", "va_cn": "小敢", "desc": "炼金术研究员，害羞内向"},
    "琴": {"en": "Jean", "element": "风", "weapon": "单手剑", "region": "蒙德", "stars": 5, "gender": "女", "va_cn": "林簌", "desc": "西风骑士团代理团长"},
    "迪卢克": {"en": "Diluc", "element": "火", "weapon": "双手剑", "region": "蒙德", "stars": 5, "gender": "男", "va_cn": "马洋", "desc": "晨曦酒庄庄主，暗夜英雄"},
    "温迪": {"en": "Venti", "element": "风", "weapon": "弓", "region": "蒙德", "stars": 5, "gender": "男", "va_cn": "喵酱", "desc": "自由之风的神，蒙德的风神巴巴托斯"},
    "可莉": {"en": "Klee", "element": "火", "weapon": "法器", "region": "蒙德", "stars": 5, "gender": "女", "va_cn": "花玲", "desc": "骑士团最强的火力输出，炸鱼小能手"},
    "迪奥娜": {"en": "Diona", "element": "冰", "weapon": "弓", "region": "蒙德", "stars": 4, "gender": "女", "va_cn": "诺亚", "desc": "猫尾酒馆的调酒师猫耳少女"},
    "达达利亚": {"en": "Tartaglia", "element": "水", "weapon": "弓", "region": "至冬", "stars": 5, "gender": "男", "va_cn": "鱼冻", "desc": "愚人众第十一执行官「公子」"},
    "钟离": {"en": "Zhongli", "element": "岩", "weapon": "长柄武器", "region": "璃月", "stars": 5, "gender": "男", "va_cn": "彭博", "desc": "岩王帝君摩拉克斯，璃月岩神"},
    "辛焱": {"en": "Xinyan", "element": "火", "weapon": "双手剑", "region": "璃月", "stars": 4, "gender": "女", "va_cn": "王雅欣", "desc": "璃月港的摇滚少女"},
    "阿贝多": {"en": "Albedo", "element": "岩", "weapon": "单手剑", "region": "蒙德", "stars": 5, "gender": "男", "va_cn": "Mace", "desc": "天才炼金术士，西风骑士团首席"},
    "甘雨": {"en": "Ganyu", "element": "冰", "weapon": "弓", "region": "璃月", "stars": 5, "gender": "女", "va_cn": "林簌", "desc": "璃月七星的秘书，半人半麒麟"},
    "魈": {"en": "Xiao", "element": "风", "weapon": "长柄武器", "region": "璃月", "stars": 5, "gender": "男", "va_cn": "kinsen", "desc": "守护璃月的仙众夜叉"},
    "胡桃": {"en": "Hu Tao", "element": "火", "weapon": "长柄武器", "region": "璃月", "stars": 5, "gender": "女", "va_cn": "陶典", "desc": "往生堂第七十七代堂主"},
    "罗莎莉亚": {"en": "Rosaria", "element": "冰", "weapon": "长柄武器", "region": "蒙德", "stars": 4, "gender": "女", "va_cn": "张安琪", "desc": "蒙德教会的修女，暗中进行情报工作"},
    "烟绯": {"en": "Yanfei", "element": "火", "weapon": "法器", "region": "璃月", "stars": 4, "gender": "女", "va_cn": "苏子怡", "desc": "璃月的法律顾问，半仙之人"},
    "优菈": {"en": "Eula", "element": "冰", "weapon": "双手剑", "region": "蒙德", "stars": 5, "gender": "女", "va_cn": "子音", "desc": "西风骑士团游击小队队长，劳伦斯家族末裔"},
    "枫原万叶": {"en": "Kaedehara Kazuha", "element": "风", "weapon": "单手剑", "region": "稻妻", "stars": 5, "gender": "男", "va_cn": "马斑马", "desc": "流浪的稻妻浪人武士"},
    "神里绫华": {"en": "Kamisato Ayaka", "element": "冰", "weapon": "单手剑", "region": "稻妻", "stars": 5, "gender": "女", "va_cn": "小N", "desc": "神里家大小姐，白鹭之公主"},
    "宵宫": {"en": "Yoimiya", "element": "火", "weapon": "弓", "region": "稻妻", "stars": 5, "gender": "女", "va_cn": "金娜", "desc": "稻妻「长野原烟花店」的当家人"},
    "早柚": {"en": "Sayu", "element": "风", "weapon": "双手剑", "region": "稻妻", "stars": 4, "gender": "女", "va_cn": "sakula.小舞", "desc": "终末番的小忍者，爱睡觉"},
    "雷电将军": {"en": "Raiden Shogun", "element": "雷", "weapon": "长柄武器", "region": "稻妻", "stars": 5, "gender": "女", "va_cn": "菊花花", "desc": "稻妻雷神，永恒的化身"},
    "九条裟罗": {"en": "Kujou Sara", "element": "雷", "weapon": "弓", "region": "稻妻", "stars": 4, "gender": "女", "va_cn": "杨梦吟", "desc": "天狗族将领，幕府军大将"},
    "珊瑚宫心海": {"en": "Sangonomiya Kokomi", "element": "水", "weapon": "法器", "region": "稻妻", "stars": 5, "gender": "女", "va_cn": "龟娘", "desc": "海祇岛现人神巫女"},
    "托马": {"en": "Thoma", "element": "火", "weapon": "长柄武器", "region": "稻妻", "stars": 4, "gender": "男", "va_cn": "张沛", "desc": "神里家的家政官"},
    "荒泷一斗": {"en": "Arataki Itto", "element": "岩", "weapon": "双手剑", "region": "稻妻", "stars": 5, "gender": "男", "va_cn": "刘照坤", "desc": "荒泷派一斗，鬼族后裔"},
    "五郎": {"en": "Gorou", "element": "岩", "weapon": "弓", "region": "稻妻", "stars": 4, "gender": "男", "va_cn": "杨昕燃", "desc": "海祇岛大将，忠义的犬系少年"},
    "申鹤": {"en": "Shenhe", "element": "冰", "weapon": "长柄武器", "region": "璃月", "stars": 5, "gender": "女", "va_cn": "秦紫格", "desc": "仙家弟子，留云借风真君的传人"},
    "云堇": {"en": "Yun Jin", "element": "岩", "weapon": "长柄武器", "region": "璃月", "stars": 4, "gender": "女", "va_cn": "贺文潇", "desc": "璃月戏曲名角，云翰社当家"},
    "八重神子": {"en": "Yae Miko", "element": "雷", "weapon": "法器", "region": "稻妻", "stars": 5, "gender": "女", "va_cn": "杜冥鸦", "desc": "鸣神大社宫司，雷神挚友"},
    "神里绫人": {"en": "Kamisato Ayato", "element": "水", "weapon": "单手剑", "region": "稻妻", "stars": 5, "gender": "男", "va_cn": "赵路", "desc": "神里家当主，社奉行"},
    "夜兰": {"en": "Yelan", "element": "水", "weapon": "弓", "region": "璃月", "stars": 5, "gender": "女", "va_cn": "徐慧", "desc": "璃月总务司秘密情报人员"},
    "久岐忍": {"en": "Shinobu", "element": "雷", "weapon": "单手剑", "region": "稻妻", "stars": 4, "gender": "女", "va_cn": "陈阳", "desc": "荒泷派二把手，实用主义者"},
    "鹿野院平藏": {"en": "Shikanoin Heizou", "element": "风", "weapon": "法器", "region": "稻妻", "stars": 4, "gender": "男", "va_cn": "林景", "desc": "天领奉行的少年侦探"},
    "提纳里": {"en": "Tighnari", "element": "草", "weapon": "弓", "region": "须弥", "stars": 5, "gender": "男", "va_cn": "莫然", "desc": "道成林巡林官，阔耳狐族"},
    "柯莱": {"en": "Collei", "element": "草", "weapon": "弓", "region": "须弥", "stars": 4, "gender": "女", "va_cn": "杨希", "desc": "须弥教令院学生，巡林员"},
    "多莉": {"en": "Dori", "element": "雷", "weapon": "双手剑", "region": "须弥", "stars": 4, "gender": "女", "va_cn": "王晓彤", "desc": "须弥的万能商人"},
    "赛诺": {"en": "Cyno", "element": "雷", "weapon": "长柄武器", "region": "须弥", "stars": 5, "gender": "男", "va_cn": "李轻扬", "desc": "须弥教令院大风纪官"},
    "坎蒂丝": {"en": "Candace", "element": "水", "weapon": "长柄武器", "region": "须弥", "stars": 4, "gender": "女", "va_cn": "张雨曦", "desc": "阿如村守护者"},
    "妮露": {"en": "Nilou", "element": "水", "weapon": "单手剑", "region": "须弥", "stars": 5, "gender": "女", "va_cn": "紫苏九月", "desc": "须弥的舞者，祖拜尔剧场之星"},
    "纳西妲": {"en": "Nahida", "element": "草", "weapon": "法器", "region": "须弥", "stars": 5, "gender": "女", "va_cn": "花玲", "desc": "草神布耶尔，世界之树的化身"},
    "莱依拉": {"en": "Layla", "element": "冰", "weapon": "单手剑", "region": "须弥", "stars": 4, "gender": "女", "va_cn": "侯晨晨", "desc": "须弥教令院学生，梦游少女"},
    "流浪者": {"en": "Wanderer", "element": "风", "weapon": "法器", "region": "须弥", "stars": 5, "gender": "男", "va_cn": "鹿喆安", "desc": "倾奇者，曾经的愚人众执行官「散兵」"},
    "珐露珊": {"en": "Faruzan", "element": "风", "weapon": "弓", "region": "须弥", "stars": 4, "gender": "女", "va_cn": "秦文静", "desc": "教令院百年前的学者前辈"},
    "瑶瑶": {"en": "Yaoyao", "element": "草", "weapon": "长柄武器", "region": "璃月", "stars": 4, "gender": "女", "va_cn": "刘一蕾", "desc": "萍姥姥的弟子，可爱的少女"},
    "艾尔海森": {"en": "Alhaitham", "element": "草", "weapon": "单手剑", "region": "须弥", "stars": 5, "gender": "男", "va_cn": "杨超然", "desc": "须弥教令院书记官，理性的知识分子"},
    "迪希雅": {"en": "Dehya", "element": "火", "weapon": "双手剑", "region": "须弥", "stars": 5, "gender": "女", "va_cn": "陈雨", "desc": "镀金旅团的佣兵战士"},
    "米卡": {"en": "Mika", "element": "冰", "weapon": "长柄武器", "region": "蒙德", "stars": 4, "gender": "男", "va_cn": "邓宥希", "desc": "西风骑士团游击小队测绘员"},
    "白术": {"en": "Baizhu", "element": "草", "weapon": "法器", "region": "璃月", "stars": 5, "gender": "男", "va_cn": "秦且歌", "desc": "璃月不卜庐的药师"},
    "卡维": {"en": "Kaveh", "element": "草", "weapon": "双手剑", "region": "须弥", "stars": 4, "gender": "男", "va_cn": "刘三木", "desc": "须弥知名建筑师"},
    "绮良良": {"en": "Kirara", "element": "草", "weapon": "单手剑", "region": "稻妻", "stars": 4, "gender": "女", "va_cn": "孙艳琦", "desc": "万端快递的猫又快递员"},
    "林尼": {"en": "Lyney", "element": "火", "weapon": "弓", "region": "枫丹", "stars": 5, "gender": "男", "va_cn": "锦衣", "desc": "枫丹知名魔术师"},
    "琳妮特": {"en": "Lynette", "element": "风", "weapon": "单手剑", "region": "枫丹", "stars": 4, "gender": "女", "va_cn": "可可味", "desc": "林尼的搭档兼妹妹"},
    "菲米尼": {"en": "Freminet", "element": "冰", "weapon": "双手剑", "region": "枫丹", "stars": 4, "gender": "男", "va_cn": "锦衣", "desc": "枫丹潜水少年，林尼的弟弟"},
    "那维莱特": {"en": "Neuvillette", "element": "水", "weapon": "法器", "region": "枫丹", "stars": 5, "gender": "男", "va_cn": "桑毓泽", "desc": "枫丹最高审判官，水龙王"},
    "莱欧斯利": {"en": "Wriothesley", "element": "冰", "weapon": "法器", "region": "枫丹", "stars": 5, "gender": "男", "va_cn": "刘北辰", "desc": "梅洛彼得堡公爵"},
    "芙宁娜": {"en": "Furina", "element": "水", "weapon": "单手剑", "region": "枫丹", "stars": 5, "gender": "女", "va_cn": "钱琛", "desc": "枫丹水神，五百年孤独的表演者"},
    "夏洛蒂": {"en": "Charlotte", "element": "冰", "weapon": "法器", "region": "枫丹", "stars": 4, "gender": "女", "va_cn": "史晓晨", "desc": "蒸汽鸟报社的记者"},
    "娜维娅": {"en": "Navia", "element": "岩", "weapon": "双手剑", "region": "枫丹", "stars": 5, "gender": "女", "va_cn": "范哲琛", "desc": "玫瑰会会长之女，刺玫会会长"},
    "夏沃蕾": {"en": "Chevreuse", "element": "火", "weapon": "长柄武器", "region": "枫丹", "stars": 4, "gender": "女", "va_cn": "潘丹妮", "desc": "枫丹特巡队队长"},
    "闲云": {"en": "Xianyun", "element": "风", "weapon": "法器", "region": "璃月", "stars": 5, "gender": "女", "va_cn": "秦紫格", "desc": "留云借风真君，璃月仙人"},
    "嘉明": {"en": "Gaming", "element": "火", "weapon": "双手剑", "region": "璃月", "stars": 4, "gender": "男", "va_cn": "谢莹", "desc": "璃月舞兽戏少年"},
    "千织": {"en": "Chiori", "element": "岩", "weapon": "单手剑", "region": "稻妻", "stars": 5, "gender": "女", "va_cn": "宋政楠", "desc": "稻妻出身时装设计师"},
    "阿蕾奇诺": {"en": "Arlecchino", "element": "火", "weapon": "长柄武器", "region": "至冬", "stars": 5, "gender": "女", "va_cn": "黄莺", "desc": "愚人众第四执行官「仆人」"},
    "希格雯": {"en": "Sigewinne", "element": "水", "weapon": "弓", "region": "枫丹", "stars": 5, "gender": "女", "va_cn": "赵爽", "desc": "梅洛彼得堡护士长"},
    "克洛琳德": {"en": "Clorinde", "element": "雷", "weapon": "单手剑", "region": "枫丹", "stars": 5, "gender": "女", "va_cn": "赵娜", "desc": "枫丹决斗代理人"},
    "赛索斯": {"en": "Sethos", "element": "雷", "weapon": "弓", "region": "须弥", "stars": 4, "gender": "男", "va_cn": "马语非", "desc": "须弥沙漠的神秘少年"},
    "艾梅莉埃": {"en": "Emilie", "element": "草", "weapon": "长柄武器", "region": "枫丹", "stars": 5, "gender": "女", "va_cn": "紫苏九月", "desc": "枫丹知名调香师"},
    "基尼奇": {"en": "Kinich", "element": "草", "weapon": "双手剑", "region": "纳塔", "stars": 5, "gender": "男", "va_cn": "斑马", "desc": "纳塔的猎龙人"},
    "卡齐娜": {"en": "Kachina", "element": "岩", "weapon": "长柄武器", "region": "纳塔", "stars": 4, "gender": "女", "va_cn": "静宸", "desc": "纳塔回声之子部族少女"},
    "玛拉妮": {"en": "Mualani", "element": "水", "weapon": "法器", "region": "纳塔", "stars": 5, "gender": "女", "va_cn": "王晓彤", "desc": "纳塔流水之部族少女"},
    "希诺宁": {"en": "Xilonen", "element": "岩", "weapon": "单手剑", "region": "纳塔", "stars": 5, "gender": "女", "va_cn": "弭洋", "desc": "纳塔回声之子部族名匠"},
    "恰斯卡": {"en": "Chasca", "element": "风", "weapon": "弓", "region": "纳塔", "stars": 5, "gender": "女", "va_cn": "张若瑜", "desc": "纳塔花羽会的调解人"},
    "欧洛伦": {"en": "Ororon", "element": "雷", "weapon": "弓", "region": "纳塔", "stars": 4, "gender": "男", "va_cn": "梁达伟", "desc": "纳塔的神秘少年"},
    "玛薇卡": {"en": "Mavuika", "element": "火", "weapon": "双手剑", "region": "纳塔", "stars": 5, "gender": "女", "va_cn": "李晔", "desc": "纳塔火神"},
    "茜特菈莉": {"en": "Citlali", "element": "冰", "weapon": "法器", "region": "纳塔", "stars": 5, "gender": "女", "va_cn": "柳知萧", "desc": "纳塔迷雾部族的老祖母"},
    "蓝砚": {"en": "Lanyan", "element": "风", "weapon": "法器", "region": "璃月", "stars": 4, "gender": "女", "va_cn": "陈婷婷", "desc": "璃月沉玉谷的陈酿师"},
    "瓦莱丽": {"en": "Varesa", "element": "雷", "weapon": "法器", "region": "纳塔", "stars": 5, "gender": "女", "va_cn": "小敢", "desc": "纳塔部族少女"},
    "伊安珊": {"en": "Iansan", "element": "岩", "weapon": "双手剑", "region": "纳塔", "stars": 5, "gender": "女", "va_cn": "陈婷婷", "desc": "纳塔部族首领"},
    "梦见月": {"en": "Mizuki", "element": "风", "weapon": "法器", "region": "稻妻", "stars": 5, "gender": "女", "va_cn": "藤田茜", "desc": "稻妻的心理咨询师"},
    "刻晴": {"en": "Keqing", "element": "雷", "weapon": "单手剑", "region": "璃月", "stars": 5, "gender": "女", "va_cn": "谢莹", "desc": "璃月七星之玉衡星"},
    "派蒙": {"en": "Paimon", "element": "无", "weapon": "无", "region": "异世界", "stars": 0, "gender": "女", "va_cn": "多多poi", "desc": "旅行者的向导，最佳食材鉴赏家"},
}

# 英文ID → 中文名映射
EN_TO_CN = {}
for cn, info in PLAYABLE.items():
    en_lower = info["en"].lower().replace(" ", "").replace("'", "")
    EN_TO_CN[en_lower] = cn

# 额外别名
EN_TO_CN.update({
    "aether": "旅行者", "lumine": "旅行者",
    "childe": "达达利亚",
    "hutao": "胡桃", "hu_tao": "胡桃",
})

# Voice Studio 已有原神角色
EXISTING_GENSHIN = {
    "雷电将军", "胡桃", "钟离", "温迪", "甘雨", "神里绫华",
    "派蒙", "可莉", "枫原万叶", "刻晴", "八重神子", "芙宁娜",
}


def get_existing_voices():
    try:
        resp = urllib.request.urlopen(f"{API_BASE}/api/voices", timeout=5)
        voices = json.loads(resp.read())
        names = set()
        for v in voices:
            n = v.get("name", "")
            names.add(n)
            if "（" in n:
                names.add(n.split("（")[0])
            if "(" in n:
                names.add(n.split("(")[0])
        return names
    except Exception:
        return EXISTING_GENSHIN


def match_directory(dirname):
    if dirname in PLAYABLE:
        return dirname, PLAYABLE[dirname], "exact_cn"
    dn_lower = dirname.lower().strip()
    if dn_lower in EN_TO_CN:
        cn_name = EN_TO_CN[dn_lower]
        if cn_name in PLAYABLE:
            return cn_name, PLAYABLE[cn_name], "en_id"
    if "#{M#" in dirname or "#{F#" in dirname:
        return "旅行者", PLAYABLE["旅行者"], "template"
    if "NICKNAME" in dirname.upper():
        return "派蒙", PLAYABLE["派蒙"], "template"
    return None, None, None


def build_tags(char_name, meta):
    tags = ["原神", "游戏角色", "二次元"]
    element = meta.get("element", "")
    if element and element != "无":
        tags.append(element + "元素")
    weapon = meta.get("weapon", "")
    if weapon and weapon != "无":
        tags.append(weapon)
    region = meta.get("region", "")
    if region:
        tags.append(region)
    gender = meta.get("gender", "")
    if gender == "女":
        tags.append("女声")
    elif gender == "男":
        tags.append("男声")
    stars = meta.get("stars", 0)
    if stars >= 4:
        tags.append(f"{'★' * stars}")
    return tags


def build_description(char_name, meta):
    parts = [f"{char_name}（{meta.get('en', '')}）"]
    element = meta.get("element", "")
    if element and element != "无":
        parts.append(f"{element}元素")
    weapon = meta.get("weapon", "")
    if weapon and weapon != "无":
        parts.append(f"{weapon}用户")
    region = meta.get("region", "")
    if region:
        parts.append(f"来自{region}")
    stars = meta.get("stars", 0)
    if stars:
        parts.append(f"{'★' * stars}")
    va = meta.get("va_cn", "")
    if va:
        parts.append(f"CV: {va}")
    desc = meta.get("desc", "")
    if desc:
        parts.append(desc)
    parts.append("《原神》可游玩角色参考音色，仅用于本地声音研究与测试。")
    return "。".join(parts)


def main():
    print("=" * 70)
    print("原神语音包6.3（中）完整分析报告")
    print("=" * 70)

    existing = get_existing_voices()

    all_dirs = sorted([d.name for d in GENSHIN_DIR.iterdir() if d.is_dir()])
    print(f"\n📁 目录总数: {len(all_dirs)}")

    matched = {}
    test_dirs = []
    template_dirs = []
    unknown_dirs = []

    for dirname in all_dirs:
        dirpath = GENSHIN_DIR / dirname
        wav_count = sum(1 for _ in dirpath.glob("*.wav"))

        if dirname.startswith("(test)"):
            test_dirs.append((dirname, wav_count))
            continue

        cn_name, meta, match_type = match_directory(dirname)

        if meta:
            if match_type == "template":
                template_dirs.append((dirname, cn_name, wav_count))
            else:
                if cn_name not in matched or wav_count > matched[cn_name]["wav_count"]:
                    matched[cn_name] = {"meta": meta, "match_type": match_type, "dir_name": dirname, "wav_count": wav_count}
        else:
            unknown_dirs.append((dirname, wav_count))

    # 报告：匹配的角色
    print(f"\n{'='*70}")
    print(f"✅ 匹配到可游玩角色: {len(matched)} 个")
    print(f"{'='*70}")

    duplicate_count = 0
    new_count = 0
    char_details = []

    for cn_name in sorted(matched.keys()):
        info = matched[cn_name]
        meta = info["meta"]
        is_dup = cn_name in existing
        if is_dup:
            duplicate_count += 1
        else:
            new_count += 1

        tags = build_tags(cn_name, meta)
        desc = build_description(cn_name, meta)

        detail = {
            "name": cn_name,
            "en": meta["en"],
            "element": meta["element"],
            "weapon": meta["weapon"],
            "region": meta["region"],
            "stars": meta["stars"],
            "gender": meta["gender"],
            "va_cn": meta.get("va_cn", ""),
            "voice_type": "game_character",
            "tags": tags,
            "description": desc,
            "wav_count": info["wav_count"],
            "source_dir": info["dir_name"],
            "match_type": info["match_type"],
            "duplicate": is_dup,
            "importable": not is_dup,
        }
        char_details.append(detail)

        status = "🔄 重复" if is_dup else "🆕 可导入"
        print(f"\n  {status} │ {cn_name}（{meta['en']}）")
        print(f"         │ {meta['element']}元素 │ {meta['weapon']} │ {meta['region']} │ {'★'*meta['stars']}")
        print(f"         │ 性别: {meta['gender']} │ CV: {meta.get('va_cn','?')}")
        print(f"         │ WAV: {info['wav_count']} │ 目录: {info['dir_name']}")
        print(f"         │ 标签: {', '.join(tags)}")
        desc_short = desc[:100] + "..." if len(desc) > 100 else desc
        print(f"         │ 描述: {desc_short}")

    # 模板目录
    if template_dirs:
        print(f"\n{'='*70}")
        print(f"📝 模板/变量目录: {len(template_dirs)} 个")
        print(f"{'='*70}")
        for dirname, cn_name, wc in template_dirs:
            print(f"  {dirname} → {cn_name} ({wc} WAVs)")

    # 测试目录
    if test_dirs:
        print(f"\n{'='*70}")
        print(f"🧪 测试目录: {len(test_dirs)} 个")
        print(f"{'='*70}")
        for dirname, wc in sorted(test_dirs, key=lambda x: -x[1])[:20]:
            print(f"  {dirname} ({wc} WAVs)")
        if len(test_dirs) > 20:
            print(f"  ... 还有 {len(test_dirs)-20} 个")

    # 未匹配目录
    if unknown_dirs:
        print(f"\n{'='*70}")
        print(f"❓ 未匹配目录: {len(unknown_dirs)} 个（NPC/怪物/剧情角色）")
        print(f"{'='*70}")
        unknown_sorted = sorted(unknown_dirs, key=lambda x: -x[1])
        for dirname, wc in unknown_sorted[:50]:
            print(f"  {dirname} ({wc} WAVs)")
        if len(unknown_dirs) > 50:
            print(f"  ... 还有 {len(unknown_dirs)-50} 个")

    # 汇总
    total_wavs_matched = sum(d["wav_count"] for d in char_details)
    total_wavs_all = sum(wc for _, wc in unknown_dirs) + sum(wc for _, _, wc in template_dirs) + sum(wc for _, wc in test_dirs) + total_wavs_matched

    print(f"\n{'='*70}")
    print(f"📋 汇总统计")
    print(f"{'='*70}")
    print(f"  目录总数:          {len(all_dirs)}")
    print(f"  匹配可游玩角色:    {len(matched)} 个")
    print(f"  └─ 重复(已导入):   {duplicate_count} 个")
    print(f"  └─ 可新增导入:     {new_count} 个")
    print(f"  模板/变量目录:     {len(template_dirs)} 个")
    print(f"  测试目录:          {len(test_dirs)} 个")
    print(f"  未匹配目录(NPC等): {len(unknown_dirs)} 个")
    print(f"  WAV 总数:          {total_wavs_all:,}")
    print(f"  └─ 可游玩角色WAV:  {total_wavs_matched:,}")

    # 缺失分析
    print(f"\n{'='*70}")
    print(f"⚠️ 缺失信息分析")
    print(f"{'='*70}")

    missing_in_pack = set(PLAYABLE.keys()) - set(matched.keys())
    if missing_in_pack:
        print(f"\n  语音包中缺少的可游玩角色 ({len(missing_in_pack)} 个):")
        for name in sorted(missing_in_pack):
            m = PLAYABLE[name]
            print(f"    ❌ {name}（{m['en']}）— {m['element']}元素 {m['weapon']} {m['region']}")

    big_unknowns = [(d, wc) for d, wc in unknown_dirs if wc >= 50]
    if big_unknowns:
        print(f"\n  未匹配但WAV≥50的目录 ({len(big_unknowns)} 个，可能是重要NPC):")
        for dirname, wc in sorted(big_unknowns, key=lambda x: -x[1])[:30]:
            print(f"    🔸 {dirname} ({wc} WAVs)")

    print(f"\n  未匹配目录元信息缺失:")
    print(f"    - 无角色描述（NPC/怪物无官方数据）")
    print(f"    - 无元素/武器/地区标签")
    print(f"    - 需人工确认性别（部分目录名无法判断）")

    # 保存报告
    report = {
        "matched_characters": char_details,
        "template_dirs": [{"dir": d, "character": c, "wav_count": w} for d, c, w in template_dirs],
        "test_dirs": [{"dir": d, "wav_count": w} for d, w in test_dirs],
        "unknown_dirs": [{"dir": d, "wav_count": w} for d, w in unknown_dirs],
        "summary": {
            "total_dirs": len(all_dirs),
            "matched_playable": len(matched),
            "duplicates": duplicate_count,
            "new_importable": new_count,
            "template_count": len(template_dirs),
            "test_count": len(test_dirs),
            "unknown_count": len(unknown_dirs),
            "missing_playable_in_pack": sorted(missing_in_pack),
            "total_wavs": total_wavs_all,
            "matched_wavs": total_wavs_matched,
        }
    }

    report_path = Path("/Users/foxmacstudio/Projects/mlx-indextts/scripts/genshin_analysis.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n📄 完整报告已保存: {report_path}")


if __name__ == "__main__":
    main()
