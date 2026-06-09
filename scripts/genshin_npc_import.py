#!/usr/bin/env python3
"""批量导入原神 NPC / 漏匹配可操控角色到 Voice Studio。

基于 genshin_analysis.json 中的 unknown_dirs，手动标注角色信息后批量导入。
"""

import json
import os
import time
import urllib.request
import urllib.error
from pathlib import Path

API_BASE = "http://localhost:8000"
GENSHIN_DIR = Path("/Users/foxmacstudio/Desktop/音色下载/原神语音包6.3（中）")
ANALYSIS_PATH = Path("/Users/foxmacstudio/Projects/mlx-indextts/scripts/genshin_analysis.json")
REPORT_PATH = Path(__file__).parent / "genshin_npc_import_report.json"

REF_COUNT = 3
MIN_WAV_SIZE = 100_000
MAX_WAV_SIZE = 1_500_000

# ── 角色数据库 ──────────────────────────────────────────────

NPC_DB = {
    # ===== 可操控角色（漏匹配 / 5.x~6.x 新角色）=====

    "荧": {
        "name": "荧", "en": "Lumine", "element": "风", "weapon": "单手剑",
        "region": "蒙德", "stars": 5, "gender": "female", "va_cn": "宴宁",
        "desc": "来自异世界的旅行者（女），与哥哥空一同降临提瓦特大陆，在旅途中被未知神明分离后踏上寻兄之旅。可根据不同国家的元素共鸣切换属性。",
        "is_playable": True,
    },
    "空": {
        "name": "空", "en": "Aether", "element": "风", "weapon": "单手剑",
        "region": "蒙德", "stars": 5, "gender": "male", "va_cn": "鹿喑",
        "desc": "来自异世界的旅行者（男），与妹妹荧一同降临提瓦特大陆，在旅途中被未知神明分离。可作为玩家操控角色，属性随国家切换。",
        "is_playable": True,
    },
    "莫娜": {
        "name": "莫娜", "en": "Mona", "element": "水", "weapon": "法器",
        "region": "蒙德", "stars": 5, "gender": "female", "va_cn": "陈婷婷",
        "desc": "伟大的占星术士莫娜·梅姬斯图斯，以占卜为生却穷困潦倒的神秘少女。拥有能洞察命运星象的水元素神之眼，性格高傲自尊心强。",
        "is_playable": True,
    },
    "七七": {
        "name": "七七", "en": "Qiqi", "element": "冰", "weapon": "单手剑",
        "region": "璃月", "stars": 5, "gender": "female", "va_cn": "",
        "desc": "在璃月不卜庐工作的僵尸少女，数百年前因意外身亡后被仙力复生，记忆支离破碎。性格乖巧安静，随身携带一本记录日常的笔记。",
        "is_playable": True,
    },
    "埃洛伊": {
        "name": "埃洛伊", "en": "Aloy", "element": "冰", "weapon": "弓",
        "region": "跨界联动", "stars": 5, "gender": "female", "va_cn": "",
        "desc": "来自《地平线：零之曙光》的诺拉族的猎手，因时空异变降临提瓦特大陆。身手矫健、意志坚定，擅长使用弓箭狩猎机械兽。",
        "is_playable": True,
    },
    "瓦雷莎": {
        "name": "瓦雷莎", "en": "Varesa", "element": "雷", "weapon": "法器",
        "region": "纳塔", "stars": 5, "gender": "female", "va_cn": "乔苏",
        "desc": "来自纳塔「沃陆之邦」的战士兼果园主，性格悠悠哉哉无比松弛，喜欢充满力量的英雄和巨量美食。五星雷属性法器主C，主打下落攻击输出。",
        "is_playable": True,
    },
    "丝柯克": {
        "name": "丝柯克", "en": "Skirk", "element": "冰", "weapon": "单手剑",
        "region": "挪德卡莱", "stars": 5, "gender": "female", "va_cn": "",
        "desc": "称号「虚渊暗星」，曾为公子达达利亚的师父，孤身一人的女武者。拥有专属能量「蛇之狡谋」，可进入「七相一闪」状态获得冰附魔并强化攻击。",
        "is_playable": True,
    },
    "爱可菲": {
        "name": "爱可菲", "en": "Effie", "element": "冰", "weapon": "长柄武器",
        "region": "枫丹", "stars": 5, "gender": "female", "va_cn": "",
        "desc": "称号「明绚千韵」，闻名枫丹的前德波大饭店主厨。五星冰元素长柄武器角色，定位后台辅助，提供减抗、治疗和暴击伤害提升。",
        "is_playable": True,
    },
    "伊法": {
        "name": "伊法", "en": "Iva", "element": "风", "weapon": "法器",
        "region": "纳塔", "stars": 4, "gender": "male", "va_cn": "",
        "desc": "称号「蔚风引灵」，纳塔「花羽会」的兽医，随身携带圆球形助手。四星风元素法器角色，提供治疗、扩散伤害与减抗辅助。",
        "is_playable": True,
    },
    "菈乌玛": {
        "name": "菈乌玛", "en": "Lauma", "element": "草", "weapon": "法器",
        "region": "挪德卡莱", "stars": 5, "gender": "female", "va_cn": "",
        "desc": "称号「圣银的辉冕」，来自挪德卡莱的草元素法器角色。月之篇章的重要角色，被誉为「咏月使」，守护着弱小的生灵。",
        "is_playable": True,
    },
    "奈芙尔": {
        "name": "奈芙尔", "en": "Nefertari", "element": "草", "weapon": "法器",
        "region": "挪德卡莱", "stars": 5, "gender": "female", "va_cn": "",
        "desc": "挪德卡莱地区的五星草元素法器角色，月绽放体系核心输出。核心攻击依赖强化重击，重击可进入「蛇行状态」高速移动并造成草元素伤害。",
        "is_playable": True,
    },
    "菲林斯": {
        "name": "菲林斯", "en": "Felins", "element": "雷", "weapon": "长柄武器",
        "region": "挪德卡莱", "stars": 5, "gender": "male", "va_cn": "",
        "desc": "全名克里洛·楚德米洛维奇·菲林斯，挪德卡莱的「执灯人」，看守北方小岛上的灯塔与墓地。五星雷元素长柄武器角色，月感电体系核心。",
        "is_playable": True,
    },
    "伊涅芙": {
        "name": "伊涅芙", "en": "Inef", "element": "雷", "weapon": "长柄武器",
        "region": "挪德卡莱", "stars": 5, "gender": "female", "va_cn": "",
        "desc": "称号「轰隆雷鸣波」，来自挪德卡莱的机器人族少女。原被拆散后由爱诺重新拼装唤醒，与雅珂达是挚友。五星雷元素长柄武器角色。",
        "is_playable": True,
    },
    "雅珂达": {
        "name": "雅珂达", "en": "Yakoda", "element": "风", "weapon": "弓",
        "region": "挪德卡莱", "stars": 4, "gender": "female", "va_cn": "",
        "desc": "挪德卡莱的四星风元素弓角色，月兆满辉奶辅助。颠沛流离的过去令她内心缺乏安全感，伊涅芙曾承诺一直陪伴在她身边。",
        "is_playable": True,
    },
    "梦见月瑞希": {
        "name": "梦见月瑞希", "en": "Yumemizuki Mizuki", "element": "风", "weapon": "法器",
        "region": "稻妻", "stars": 5, "gender": "female", "va_cn": "",
        "desc": "称号「绮梦缱绻」，稻妻的食梦貘一族心理诊疗师，经营秋沙钱汤。五星风元素法器角色，以扩散反应为核心，兼具输出与治疗。",
        "is_playable": True,
    },
    "杜林": {
        "name": "杜林", "en": "Durin", "element": "火", "weapon": "单手剑",
        "region": "挪德卡莱", "stars": 5, "gender": "male", "va_cn": "",
        "desc": "五星火元素单手剑角色，拥有白龙偏辅助、黑龙偏输出双形态。通过元素战技切换形态，元素爆发提供长时间后台挂火和元素减抗。",
        "is_playable": True,
    },
    "兹白": {
        "name": "兹白", "en": "Zibai", "element": "岩", "weapon": "单手剑",
        "region": "璃月", "stars": 5, "gender": "male", "va_cn": "",
        "desc": "称号「驹隙隐泉」，璃月出身的仙人（天使）。五星岩元素单手剑角色，月结晶站场输出主C，可将水结晶异化为月结晶反应。",
        "is_playable": True,
    },
    "尼可": {
        "name": "尼可", "en": "Nicole", "element": "火", "weapon": "法器",
        "region": "挪德卡莱", "stars": 5, "gender": "female", "va_cn": "云鹤追",
        "desc": "全名尼可·莱恩，魔女会成员代号N。五星火元素法器角色，T0级全能后台辅助，集护盾、攻击力加成和协同副C于一体。",
        "is_playable": True,
    },
    "叶洛亚": {
        "name": "叶洛亚", "en": "Yelouya", "element": "岩", "weapon": "长柄武器",
        "region": "挪德卡莱", "stars": 4, "gender": "male", "va_cn": "",
        "desc": "四星岩元素长柄武器角色，功能性辅助。元素战技召唤信鸟造成岩伤并产出微粒，元素爆发为队友提供岩伤和月结晶反应伤害增益。",
        "is_playable": True,
    },
    "哥伦比娅": {
        "name": "哥伦比娅", "en": "Columbina", "element": "水", "weapon": "法器",
        "region": "挪德卡莱", "stars": 5, "gender": "female", "va_cn": "杨梦露",
        "desc": "全名哥伦比娅·希珀塞莱尼娅，愚人众执行官第三席，代号「少女」。司掌霜月的女神，月神族裔。五星水元素法器角色。",
        "is_playable": True,
    },
    "塔利雅": {
        "name": "塔利雅", "en": "Talia", "element": "岩", "weapon": "法器",
        "region": "蒙德", "stars": 4, "gender": "female", "va_cn": "",
        "desc": "蒙德西风教会的助祭与唱诗班领队，受神明眷顾时常聆听神旨。四星岩元素法器角色，元素爆发提供护盾和辅助能力。",
        "is_playable": True,
    },
    "爱诺": {
        "name": "爱诺", "en": "Aino", "element": "水", "weapon": "双手剑",
        "region": "挪德卡莱", "stars": 4, "gender": "female", "va_cn": "",
        "desc": "挪德卡莱「叮铃哐当蛋卷工坊」的主人，擅长制作各种机械。将伊涅芙重新拼装为家政机器人并唤醒了她。四星水元素双手剑角色。",
        "is_playable": True,
    },

    # ===== 重要剧情 NPC =====

    "「少女」": {
        "name": "「少女」哥伦比娅", "en": "Damselette", "element": "水", "weapon": "法器",
        "region": "至冬", "stars": 5, "gender": "female", "va_cn": "杨梦露",
        "desc": "愚人众执行官第三席，代号「少女」。真实身份为月神族裔哥伦比娅·希珀塞莱尼娅，司掌霜月。拥有极强的实力，被公认为执行官中的顶级存在。",
        "is_playable": False,
    },
    "「木偶」": {
        "name": "「木偶」", "en": "Marionette", "element": "", "weapon": "",
        "region": "至冬", "stars": 0, "gender": "female", "va_cn": "",
        "desc": "愚人众执行官之一，代号「木偶」。极少露面，真实身份与实力仍笼罩在迷雾中。",
        "is_playable": False,
    },
    "「博士」": {
        "name": "「博士」", "en": "Dottore", "element": "", "weapon": "",
        "region": "至冬", "stars": 0, "gender": "male", "va_cn": "",
        "desc": "愚人众执行官第二席，代号「博士」。天才学者与疯狂科学家，通过切割自身不同年龄段的「切片」实现多段存在，追求超越死亡的永生。",
        "is_playable": False,
    },
    "「队长」": {
        "name": "「队长」卡皮塔诺", "en": "Capitano", "element": "", "weapon": "",
        "region": "至冬", "stars": 0, "gender": "male", "va_cn": "",
        "desc": "愚人众执行官第一席，代号「队长」。被公认为提瓦特最强战力之一，为人正直有原则，在纳塔剧情中展现出令人敬佩的牺牲精神。",
        "is_playable": False,
    },
    "「女士」": {
        "name": "「女士」", "en": "Signora", "element": "冰", "weapon": "法器",
        "region": "至冬", "stars": 0, "gender": "female", "va_cn": "",
        "desc": "愚人众执行官第八席，代号「女士」。原名罗莎琳·克鲁兹希卡·洛厄法特，曾为蒙德贵族少女，因失去挚爱后堕入炎之魔女形态，后加入愚人众。",
        "is_playable": False,
    },
    "戴因斯雷布": {
        "name": "戴因斯雷布", "en": "Dainsleif", "element": "", "weapon": "",
        "region": "坎瑞亚", "stars": 0, "gender": "male", "va_cn": "",
        "desc": "坎瑞亚王国的末代「拾枝者」，身披黑衣的神秘旅人。拥有不朽的诅咒之躯，见证了坎瑞亚的覆灭。常以冷静而沉重的口吻讲述提瓦特的历史真相。",
        "is_playable": False,
    },
    "法尔伽": {
        "name": "法尔伽", "en": "Varka", "element": "风", "weapon": "",
        "region": "蒙德", "stars": 0, "gender": "male", "va_cn": "郝祥海",
        "desc": "蒙德西风骑士团大团长，称号「北风骑士」，被誉为西风骑士团的顶点。长期率远征军在外，是蒙德最强战力的象征。",
        "is_playable": False,
    },
    "艾莉丝": {
        "name": "艾莉丝", "en": "Alice", "element": "", "weapon": "",
        "region": "蒙德", "stars": 0, "gender": "female", "va_cn": "张琦",
        "desc": "「魔女会」成员，可莉的母亲。被称为「诸世界的大冒险家」的传奇旅行家，行踪如风难以捉摸，拥有渊博的知识与强大的实力。",
        "is_playable": False,
    },
    "凯瑟琳": {
        "name": "凯瑟琳", "en": "Kathryne", "element": "", "weapon": "",
        "region": "蒙德", "stars": 0, "gender": "female", "va_cn": "",
        "desc": "冒险家协会的标志性接待员，各国外均有分部的统一面孔。口头禅是「向着星辰与深渊」，真实身份为仿生人偶。",
        "is_playable": False,
    },
    "迪娜泽黛": {
        "name": "迪娜泽黛", "en": "Dunyarzad", "element": "", "weapon": "",
        "region": "须弥", "stars": 0, "gender": "female", "va_cn": "张安琪",
        "desc": "须弥知名富商之女，身患厄病却性格开朗乐观。是旅行者在须弥的重要伙伴，其命运与草神纳西妲紧密相连。",
        "is_playable": False,
    },
    "萍姥姥": {
        "name": "萍姥姥", "en": "Madame Ping", "element": "风", "weapon": "法器",
        "region": "璃月", "stars": 0, "gender": "female", "va_cn": "",
        "desc": "璃月港的慈祥老妇人，真实身份为留云借风真君的化身。拥有强大的仙力，以凡人形态隐居于璃月，偶尔指点后辈修行。",
        "is_playable": False,
    },
    "留云借风真君": {
        "name": "留云借风真君", "en": "Cloud Retainer", "element": "风", "weapon": "法器",
        "region": "璃月", "stars": 0, "gender": "female", "va_cn": "",
        "desc": "璃月三仙人之一，号「留云借风真君」，仙兽为鹤。性格高傲却心系璃月众生，擅长机关术。人间化名为闲云。",
        "is_playable": False,
    },
    "芙卡洛斯": {
        "name": "芙卡洛斯", "en": "Focalors", "element": "水", "weapon": "",
        "region": "枫丹", "stars": 0, "gender": "female", "va_cn": "",
        "desc": "枫丹水神，水之神。以自身为代价摧毁了枫丹的原始胎海诅咒，拯救了枫丹人民。与芙宁娜实为一体两面。",
        "is_playable": False,
    },
    "大慈树王": {
        "name": "大慈树王", "en": "Greater Lord Rukkhadevata", "element": "草", "weapon": "",
        "region": "须弥", "stars": 0, "gender": "female", "va_cn": "沐霏",
        "desc": "须弥最初的草神，世界树的守护者。为清除禁忌知识污染而牺牲自我，其存在被从世界树中抹除，导致世人遗忘。",
        "is_playable": False,
    },
    "旁白": {
        "name": "旁白", "en": "Narrator", "element": "", "weapon": "",
        "region": "通用", "stars": 0, "gender": "unknown", "va_cn": "",
        "desc": "游戏中的旁白解说角色，负责故事叙述、场景描述和剧情推进的画外音。",
        "is_playable": False,
    },
    "奥兹": {
        "name": "奥兹", "en": "Oz", "element": "雷", "weapon": "",
        "region": "蒙德", "stars": 0, "gender": "male", "va_cn": "赵悦程",
        "desc": "菲谢尔的召唤物——夜鸦眷属「奥兹瓦尔多」，与菲谢尔形影不离的忠实伙伴。常以无奈而温柔的语气纠正菲谢尔的中二发言。",
        "is_playable": False,
    },
    "德沃沙克": {
        "name": "德沃沙克", "en": "Dvorak", "element": "", "weapon": "",
        "region": "枫丹", "stars": 0, "gender": "male", "va_cn": "",
        "desc": "枫丹音乐家，枫丹音乐节的推动者，热爱音乐与文化交流。",
        "is_playable": False,
    },
    "「式大将」": {
        "name": "「式大将」", "en": "Shiki Daishou", "element": "雷", "weapon": "",
        "region": "稻妻", "stars": 0, "gender": "male", "va_cn": "",
        "desc": "八重神子的式神「式大将」，负责守护神樱树和鸣神大社的式神大将。",
        "is_playable": False,
    },
    "「大肉丸」": {
        "name": "「大肉丸」", "en": "Taroumaru", "element": "", "weapon": "",
        "region": "稻妻", "stars": 0, "gender": "male", "va_cn": "",
        "desc": "稻妻社奉行经营的「木南料亭」附近的柴犬，曾为武士的忠实伙伴。",
        "is_playable": False,
    },
    "蒂玛乌斯": {
        "name": "蒂玛乌斯", "en": "Timaeus", "element": "", "weapon": "",
        "region": "蒙德", "stars": 0, "gender": "male", "va_cn": "",
        "desc": "蒙德炼金术士，阿贝多的助手和弟子。常在蒙德城内摆摊进行炼金合成研究。",
        "is_playable": False,
    },
    "哲平": {
        "name": "哲平", "en": "Teppei", "element": "", "weapon": "",
        "region": "稻妻", "stars": 0, "gender": "male", "va_cn": "",
        "desc": "稻妻海祇岛反抗军成员，旅行者在稻妻剧情中的重要伙伴。因使用邪眼导致生命力被过度消耗而英年早逝。",
        "is_playable": False,
    },
    "托克": {
        "name": "托克", "en": "Teucer", "element": "", "weapon": "",
        "region": "至冬", "stars": 0, "gender": "male", "va_cn": "",
        "desc": "公子达达利亚的弟弟，天真活泼的小男孩。相信哥哥是玩具销售员，梦想成为一名勇士。",
        "is_playable": False,
    },
    "海芭夏": {
        "name": "海芭夏", "en": "Hipparis", "element": "", "weapon": "",
        "region": "须弥", "stars": 0, "gender": "female", "va_cn": "严丽祯",
        "desc": "须弥教令院学者，曾参与世界树相关研究。",
        "is_playable": False,
    },
    "伊迪娅": {
        "name": "伊迪娅", "en": "Idyia", "element": "水", "weapon": "",
        "region": "枫丹", "stars": 0, "gender": "female", "va_cn": "",
        "desc": "枫丹的纯水精灵变身的人形少女，负责管理镜子世界中的领域。",
        "is_playable": False,
    },
    "索琳蒂丝": {
        "name": "索琳蒂丝", "en": "Sorintis", "element": "", "weapon": "",
        "region": "坎瑞亚", "stars": 0, "gender": "female", "va_cn": "",
        "desc": "坎瑞亚黑日王朝深秘院研究员，赤月王室旁系血脉，「猎月人」雷利尔的未婚妻。研究出打开月之门的方法，因非正规开启通道而惨遭撕裂。",
        "is_playable": False,
    },
    "居勒什": {
        "name": "居勒什", "en": "Jules", "element": "", "weapon": "",
        "region": "须弥", "stars": 0, "gender": "male", "va_cn": "",
        "desc": "须弥教令院素论派前代贤者，赛诺的养父兼老师，丽莎的恩师。退休后成为农夫，性格幽默风趣，热爱讲冷笑话。",
        "is_playable": False,
    },
    "塞琉斯": {
        "name": "塞琉斯", "en": "Cyrus", "element": "", "weapon": "",
        "region": "蒙德", "stars": 0, "gender": "male", "va_cn": "",
        "desc": "蒙德冒险家协会的分会长，负责派遣委托任务。",
        "is_playable": False,
    },
    "昆钧": {
        "name": "昆钧", "en": "Kun Jun", "element": "", "weapon": "",
        "region": "璃月", "stars": 0, "gender": "male", "va_cn": "",
        "desc": "璃月层岩巨渊剧情中的重要NPC，与千岩军和矿工密切相关。",
        "is_playable": False,
    },
    "雷利尔": {
        "name": "雷利尔", "en": "Raziel", "element": "", "weapon": "",
        "region": "坎瑞亚", "stars": 0, "gender": "male", "va_cn": "",
        "desc": "坎瑞亚赤月王朝的「猎月人」，索琳蒂丝的未婚夫。挪德卡莱主线「月之二」的重要剧情角色。",
        "is_playable": False,
    },
    "理水叠山真君": {
        "name": "理水叠山真君", "en": "Mountain Shaper", "element": "", "weapon": "",
        "region": "璃月", "stars": 0, "gender": "male", "va_cn": "",
        "desc": "璃月三仙人之一，仙兽为鹿。性格沉稳，守护璃月的仙人之一。",
        "is_playable": False,
    },
    "削月筑阳真君": {
        "name": "削月筑阳真君", "en": "Moon Carver", "element": "", "weapon": "",
        "region": "璃月", "stars": 0, "gender": "male", "va_cn": "",
        "desc": "璃月三仙人之一，仙兽为鹿。性格温和，与其他仙人共同守护璃月。",
        "is_playable": False,
    },
    "掇星攫辰天君": {
        "name": "掇星攫辰天君", "en": "Skybristle", "element": "", "weapon": "",
        "region": "璃月", "stars": 0, "gender": "male", "va_cn": "",
        "desc": "璃月仙人之一，与其他仙人共同守护璃月大地。",
        "is_playable": False,
    },
    "纯水精灵": {
        "name": "纯水精灵", "en": "Oceanid", "element": "水", "weapon": "",
        "region": "璃月", "stars": 0, "gender": "female", "va_cn": "",
        "desc": "水元素的精灵生物，曾为水神厄歌莉娅的眷属。可在水中自由变化形态，部分纯水精灵选择化身为人形。",
        "is_playable": False,
    },
    "深渊法师": {
        "name": "深渊法师", "en": "Abyss Mage", "element": "", "weapon": "",
        "region": "深渊", "stars": 0, "gender": "unknown", "va_cn": "",
        "desc": "来自深渊的施法者，拥有多种元素护盾和攻击手段。与坎瑞亚有密切关联。",
        "is_playable": False,
    },
    "恶龙": {
        "name": "恶龙", "en": "Evil Dragon", "element": "", "weapon": "",
        "region": "通用", "stars": 0, "gender": "unknown", "va_cn": "",
        "desc": "原神世界观中的龙族反派角色，常见于各类剧情任务中。",
        "is_playable": False,
    },
    "浮游水蕈兽·元素生命": {
        "name": "浮游水蕈兽", "en": "Floating Water Fungus", "element": "水", "weapon": "",
        "region": "须弥", "stars": 0, "gender": "unknown", "va_cn": "",
        "desc": "须弥地区的水元素蕈兽生物，蕈兽拟态活动中的重要角色之一。",
        "is_playable": False,
    },
    "主持人": {
        "name": "主持人", "en": "Host", "element": "", "weapon": "",
        "region": "通用", "stars": 0, "gender": "unknown", "va_cn": "",
        "desc": "游戏活动中的主持人角色，负责赛事解说和活动引导。",
        "is_playable": False,
    },
    "故事女巫": {
        "name": "故事女巫", "en": "Story Witch", "element": "", "weapon": "",
        "region": "通用", "stars": 0, "gender": "female", "va_cn": "",
        "desc": "原神中的故事讲述者角色，常以童话般的口吻为角色讲述传说故事。",
        "is_playable": False,
    },
    "回声海螺": {
        "name": "回声海螺", "en": "Echo Conch", "element": "", "weapon": "",
        "region": "金苹果群岛", "stars": 0, "gender": "unknown", "va_cn": "",
        "desc": "金苹果群岛活动中的特殊物品，内含过往的语音回声与回忆。",
        "is_playable": False,
    },
    "魔女M": {
        "name": "魔女M", "en": "Hexenzirkel M", "element": "", "weapon": "",
        "region": "通用", "stars": 0, "gender": "female", "va_cn": "",
        "desc": "魔女会成员之一，代号M。与艾莉丝、尼可同为魔女会的重要成员。",
        "is_playable": False,
    },
}


def build_tags(char: dict) -> list[str]:
    tags = ["原神"]
    if char.get("is_playable"):
        tags.append("可操控角色")
    else:
        tags.append("NPC")
    if char.get("element"):
        tags.append(char["element"])
    if char.get("weapon"):
        tags.append(char["weapon"])
    if char.get("region"):
        tags.append(char["region"])
    if char.get("stars"):
        tags.append(f"{char['stars']}星")
    g = char.get("gender", "")
    if g == "female":
        tags.append("女声")
    elif g == "male":
        tags.append("男声")
    if char.get("va_cn"):
        tags.append(f"CV:{char['va_cn']}")
    return tags


def build_description(char: dict) -> str:
    parts = []
    name = char["name"]
    if char.get("en"):
        parts.append(f"《原神》角色{name}（{char['en']}）。")
    else:
        parts.append(f"《原神》角色{name}。")
    if char.get("desc"):
        parts.append(char["desc"])
    if char.get("is_playable"):
        parts.append("可操控角色。")
    else:
        parts.append("剧情NPC角色。")
    parts.append("语音包来源：原神语音包6.3（中），仅用于本地声音研究与测试。")
    return " ".join(parts)


def api_upload_wav(wav_path: str) -> str | None:
    filename = os.path.basename(wav_path)
    boundary = "----WebKitFormBoundary7MA4YWxkTrZu0gW"
    with open(wav_path, "rb") as f:
        file_data = f.read()
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: audio/wav\r\n\r\n"
    ).encode("utf-8") + file_data + f"\r\n--{boundary}--\r\n".encode("utf-8")
    req = urllib.request.Request(
        f"{API_BASE}/api/voices/upload",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    resp = urllib.request.urlopen(req, timeout=30)
    result = json.loads(resp.read())
    return result.get("file_id", "") or None


def api_post_json(path: str, data: dict) -> dict:
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"{API_BASE}{path}",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    resp = urllib.request.urlopen(req, timeout=15)
    return json.loads(resp.read())


def api_get(path: str) -> list | dict:
    resp = urllib.request.urlopen(f"{API_BASE}{path}", timeout=10)
    return json.loads(resp.read())


def get_existing_names() -> set[str]:
    voices = api_get("/api/voices")
    names = set()
    for v in voices:
        n = v.get("name", "")
        names.add(n)
        if "（" in n:
            names.add(n.split("（")[0])
        if "(" in n:
            names.add(n.split("(")[0].strip())
    return names


def select_diverse_wavs(wav_files: list[Path], count: int = REF_COUNT) -> list[Path]:
    valid = []
    for w in wav_files:
        try:
            size = w.stat().st_size
            if MIN_WAV_SIZE <= size <= MAX_WAV_SIZE:
                valid.append((w, size))
        except OSError:
            continue
    if not valid:
        valid = [(w, w.stat().st_size) for w in wav_files if w.stat().st_size > 10_000]
    if not valid:
        return []
    valid.sort(key=lambda x: x[1])
    if len(valid) <= count:
        return [w for w, _ in valid]
    indices = sorted(set([0, len(valid) // 2, len(valid) - 1]))
    return [valid[i][0] for i in indices[:count]]


def main():
    print("=" * 70)
    print("原神 NPC / 漏匹配角色 → Voice Studio 批量导入")
    print(f"已标注角色: {len(NPC_DB)} 个")
    print(f"策略: 每角色 {REF_COUNT} 条参考音频")
    print("=" * 70)

    with open(ANALYSIS_PATH, "r", encoding="utf-8") as f:
        analysis = json.load(f)

    unknown_map = {d["dir"]: d["wav_count"] for d in analysis["unknown_dirs"]}

    importable = []
    not_found = []
    for dir_name, char in NPC_DB.items():
        wav_count = unknown_map.get(dir_name, 0)
        if wav_count == 0:
            not_found.append(dir_name)
            continue
        source_dir = GENSHIN_DIR / dir_name
        if not source_dir.exists():
            not_found.append(dir_name)
            continue
        importable.append({**char, "dir_name": dir_name, "wav_count": wav_count})

    print(f"\n📊 可导入: {len(importable)} 个")
    if not_found:
        print(f"📊 目录不存在/无匹配: {len(not_found)} 个 → {', '.join(not_found[:10])}{'...' if len(not_found) > 10 else ''}")

    existing = get_existing_names()
    print(f"📊 Voice Studio 已有音色: {len(existing)} 个名称\n")

    results = {"success": [], "skipped": [], "failed": []}
    playable_count = 0
    npc_count = 0

    for i, char in enumerate(importable, 1):
        cn_name = char["name"]
        voice_name = f"{cn_name}（原神）"

        if voice_name in existing or cn_name in existing:
            print(f"  [{i}/{len(importable)}] ⏭️ {voice_name} — 已存在，跳过")
            results["skipped"].append(voice_name)
            continue

        source_dir = GENSHIN_DIR / char["dir_name"]
        wav_files = sorted(source_dir.glob("*.wav"))
        if not wav_files:
            print(f"  [{i}/{len(importable)}] ❌ {voice_name} — 无 WAV 文件")
            results["failed"].append({"name": voice_name, "reason": "无WAV文件"})
            continue

        selected = select_diverse_wavs(wav_files, REF_COUNT)
        if not selected:
            print(f"  [{i}/{len(importable)}] ❌ {voice_name} — 无合适 WAV")
            results["failed"].append({"name": voice_name, "reason": "无合适WAV"})
            continue

        is_playable = char.get("is_playable", False)
        tag_label = "🎮" if is_playable else "📋"
        if is_playable:
            playable_count += 1
        else:
            npc_count += 1

        print(f"  [{i}/{len(importable)}] {tag_label} {voice_name} ({char['wav_count']} WAVs, 选 {len(selected)} 条) [{char.get('element', '?')}/{char.get('region', '?')}]")

        file_ids = []
        for wav in selected:
            try:
                fid = api_upload_wav(str(wav))
                if fid:
                    file_ids.append(fid)
                    print(f"           ✅ {wav.name[:50]} → {fid[:12]}...")
                else:
                    print(f"           ⚠️ 上传返回空: {wav.name[:50]}")
                time.sleep(0.2)
            except Exception as e:
                print(f"           ❌ 上传失败 {wav.name[:50]}: {e}")

        if not file_ids:
            print(f"           ❌ 全部上传失败，跳过注册")
            results["failed"].append({"name": voice_name, "reason": "全部上传失败"})
            continue

        voice_data = {
            "name": voice_name,
            "voice_type": "virtual_character",
            "description": build_description(char),
            "default_language": "zh",
            "tags": build_tags(char),
            "reference_audio_ids": file_ids,
            "reference_text": "",
            "recommended_engine_id": "indextts-v2",
            "license_status": "test_only",
        }

        try:
            result = api_post_json("/api/voices", voice_data)
            vid = result.get("voice_id", "")
            print(f"           🎤 注册成功 → {vid[:12]}... ({len(file_ids)} refs)")
            results["success"].append({
                "name": voice_name,
                "voice_id": vid,
                "reference_count": len(file_ids),
                "wav_count": char["wav_count"],
                "source_dir": char["dir_name"],
                "element": char.get("element", ""),
                "gender": char.get("gender", ""),
                "is_playable": is_playable,
                "va_cn": char.get("va_cn", ""),
            })
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            print(f"           ❌ 注册失败 ({e.code}): {err_body[:200]}")
            results["failed"].append({"name": voice_name, "reason": f"注册失败 ({e.code}): {err_body[:100]}"})
        except Exception as e:
            print(f"           ❌ 注册失败: {e}")
            results["failed"].append({"name": voice_name, "reason": f"注册失败: {e}"})

        time.sleep(0.3)

    print(f"\n{'='*70}")
    print(f"导入完成！成功: {len(results['success'])} | 跳过: {len(results['skipped'])} | 失败: {len(results['failed'])}")
    print(f"  🎮 可操控: {playable_count} | 📋 NPC: {npc_count}")
    print(f"{'='*70}")

    if results["success"]:
        print(f"\n✅ 成功导入 ({len(results['success'])} 个):")
        for r in results["success"]:
            tag = "🎮" if r["is_playable"] else "📋"
            print(f"   {tag} {r['name']} | {r['element']} | {r['gender']} | {r['reference_count']}refs / {r['wav_count']}WAVs | CV:{r['va_cn']}")

    if results["failed"]:
        print(f"\n❌ 失败 ({len(results['failed'])} 个):")
        for r in results["failed"]:
            print(f"   {r['name']}: {r['reason']}")

    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n📄 报告已保存: {REPORT_PATH}")


if __name__ == "__main__":
    main()
