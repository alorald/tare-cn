"""
app.py — FinFlow AaaS FastAPI 主应用

跨境财税智能体服务，真实调用 GMI Cloud API。

启动：
    uvicorn app:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional, Set

from fastapi import (
    FastAPI,
    WebSocket,
    WebSocketDisconnect,
    HTTPException,
    Request,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from gmi_client import gmi_client, PRICING
from agents import orchestrator, consultation_agent, risk_agent

# ---------------------------------------------------------------------------
# 日志
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("finflow")

# ---------------------------------------------------------------------------
# 内存存储
# ---------------------------------------------------------------------------

# task_id -> 任务详情
tasks_db: Dict[str, Dict[str, Any]] = {}

# task_id -> 财税报表
reports_db: Dict[str, Dict[str, Any]] = {}

# task_id -> 进度事件列表
progress_db: Dict[str, List[Dict[str, Any]]] = {}

# task_id -> 订阅该任务进度的 WebSocket 集合
ws_subscribers: Dict[str, Set[WebSocket]] = {}

# 合规咨询对话历史
compliance_chats_db: List[Dict[str, Any]] = []

# 风险评估结果
risk_assessments_db: List[Dict[str, Any]] = []

# ---------------------------------------------------------------------------
# 预置法规知识库文档（覆盖 EU / US / JP / KR 等主要跨境贸易法规）
# ---------------------------------------------------------------------------

KNOWLEDGE_DOCUMENTS: List[Dict[str, Any]] = [
    {
        "id": "kb-eu-vat-directive-2006-112",
        "title": "欧盟增值税指令 Council Directive 2006/112/EC",
        "country": "EU",
        "category": "间接税 / VAT",
        "summary": "欧盟增值税核心法律框架，规定成员国标准 VAT 税率不低于 15%、零税率与豁免适用范围、跨境 B2C 服务的纳税地点规则，以及 OSS/IOSS 一站式申报机制。跨境电商卖家向欧盟消费者销售商品须关注 IOSS（≤150 欧元小包）与远程销售阈值。",
        "url": "https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:02006L0112-20240101",
    },
    {
        "id": "kb-eu-gpsr-2023-988",
        "title": "欧盟通用产品安全法规 GPSR (EU) 2023/988",
        "country": "EU",
        "category": "产品安全",
        "summary": "2024 年 12 月 13 日起生效，取代原 GPSD。要求所有在欧盟市场投放的消费产品（含线上电商）必须安全，制造商/进口商/远程卖家须指定欧盟授权代表、进行风险评估、事故上报并建立可追溯性。对 Temu、速卖通等中国卖家影响显著。",
        "url": "https://eur-lex.europa.eu/eli/reg/2023/988/oj",
    },
    {
        "id": "kb-eu-ce-marking-768-2008",
        "title": "欧盟 CE 标识框架 Decision 768/2008/EC",
        "country": "EU",
        "category": "产品认证",
        "summary": "CE 标识是产品进入欧盟市场的合规通行证，覆盖电子电器（LVD/EMC/RED）、玩具、机械、医疗器械、PPE 等 20 余项指令/法规。制造商须起草符合性声明（DoC）、编制技术文档，必要时由公告机构（Notified Body）参与合格评定。",
        "url": "https://eur-lex.europa.eu/eli/dec/2008/768/oj",
    },
    {
        "id": "kb-eu-epr-packaging-waste",
        "title": "欧盟生产者责任延伸 EPR（包装与电子废弃物）",
        "country": "EU",
        "category": "环保合规",
        "summary": "欧盟包装与包装废弃物指令 94/62/EC 及 WEEE 指令 2012/19/EU 要求生产者（含远程卖家）承担产品生命周期末端回收责任。亚马逊等平台已强制要求卖家提供 EPR 注册号，未注册将下架商品，主要涉及德/法/西/奥等国。",
        "url": "https://environment.ec.europa.eu/topics/waste-and-recycling/packaging-waste_en",
    },
    {
        "id": "kb-us-sales-tax-wayfair",
        "title": "美国销售税经济联系 South Dakota v. Wayfair (2018)",
        "country": "US",
        "category": "间接税 / Sales Tax",
        "summary": "美国最高法院 Wayfair 案确立经济联系（Economic Nexus）原则，远程卖家在某州销售额或交易数超过阈值（如南达科他州 10 万美元或 200 笔交易）即需注册代征销售税。亚马逊默认代收，但第三方卖家在部分州仍需自行申报。无全国统一销售税，各州税率 0%-9.5% 不等。",
        "url": "https://www.supremecourt.gov/opinions/17pdf/17-494_j4el.pdf",
    },
    {
        "id": "kb-us-cpsia-2008",
        "title": "美国消费品安全改进法 CPSIA (H.R. 4040, 2008)",
        "country": "US",
        "category": "产品安全",
        "summary": "针对 12 岁以下儿童用品的强制联邦法规，要求铅含量限值、邻苯二甲酸盐（6P）禁令、玩具标准 ASTM F963 符合性测试，并强制通过 CPSC 认可的第三方实验室出具 Children's Product Certificate (CPC)。跨境电商玩具、母婴品类高频违规，亚马逊会要求提供 CPC 否则下架。",
        "url": "https://www.cpsc.gov/Business--Manufacturing/Business-Education/business-guidance/cpsia",
    },
    {
        "id": "kb-us-fcc-part-15",
        "title": "美国 FCC Part 15 射频设备合规",
        "country": "US",
        "category": "产品认证",
        "summary": "FCC 规则第 15 部分管控所有有意/无意辐射射频能量的电子设备（含蓝牙、Wi-Fi、IoT 设备）。出口美国的无线电子产品须通过 FCC SDoC 或 Certification 认证，加贴 FCC ID。亚马逊会强制要求 FCC 合规文件，违规将面临 NAL 罚款。",
        "url": "https://www.fcc.gov/engineering-technology/laboratory-division/general/radio-laboratory-division/topics/1486",
    },
    {
        "id": "kb-jp-consumption-tax-jct",
        "title": "日本消费税（JCT）与 invoice 制度",
        "country": "JP",
        "category": "间接税 / 消费税",
        "summary": "日本消费税标准税率 10%（食品饮料 8%）。2023 年 10 月起全面实施 invoice 登记制度，注册卖家须取得合格发票发行人资格，否则买家无法抵扣进项税。跨境卖家通过亚马逊日本站销售达到基期销售额 1000 万日元阈值须注册 JCT。",
        "url": "https://www.nta.go.jp/taxes/shiraberu/zeimokubetsu/shohi/keigenzeiritsu/invoice/index.htm",
    },
    {
        "id": "kb-jp-pse-pse-denan",
        "title": "日本 PSE 认证（电气用品安全法 DENAN）",
        "country": "JP",
        "category": "产品认证",
        "summary": "日本电气用品安全法要求 116 类（特定电气用品，如电源适配器、锂电池）须通过 PSE 强制认证并由第三方认证机构（RCAB）出具证书，343 类（非特定）须自我符合性声明。所有电气用品须标注 PSE 标志、进口商名称、菱形/圆形认证标识，否则日本海关不予放行。",
        "url": "https://www.meti.go.jp/policy/consumer/seiner/denan/",
    },
    {
        "id": "kb-kr-k-reach",
        "title": "韩国 K-REACH 化学品注册评估法 (2015)",
        "country": "KR",
        "category": "化学品合规",
        "summary": "《化学品物质的注册与评估等法案》（K-REACH）要求年生产/进口量 ≥1 吨的现有化学品须向环境部注册，≥0.1 吨的现有化学品须申报。化妆品、洗剂、清洁剂等含化学成分的跨境电商产品需关注下游用途通报义务，违规可处 5 年以下徒刑或 5000 万韩元罚款。",
        "url": "https://chem.eccer.me/chemportal/kreach.do",
    },
    {
        "id": "kb-kr-kc-certification",
        "title": "韩国 KC 认证（国家标准统一标志）",
        "country": "KR",
        "category": "产品认证",
        "summary": "KC（Korea Certification）是韩国强制性国家统一认证标志，覆盖电子电器（依据《电气用品安全管理法》）、儿童用品、生活用品等。儿童用品（13 类）须由指定机构测试并签发 KC 证书。跨境电商出口韩国的母婴、玩具、电器品类高频被海关抽查 KC 证书。",
        "url": "https://www.kats.go.kr/en/main.do",
    },
    {
        "id": "kb-uk-vat-post-brexit",
        "title": "英国脱欧后 VAT 制度（UK VAT post-Brexit）",
        "country": "GB",
        "category": "间接税 / VAT",
        "summary": "2021 年 1 月 1 日脱欧后，英国 VAT 标准税率 20%。境外卖家通过在线平台向英国消费者销售 ≤135 英镑商品，由平台代收代缴 VAT；超过 135 英镑由进口商在清关时缴纳。卖家须取得英国 EORI 号与 GB VAT 注册号，否则无法清关。",
        "url": "https://www.gov.uk/guidance/vat-and-overseas-goods-sold-directly-to-customers-in-the-uk",
    },
    # ===================== 全球关税专题（2025-2026 更新） =====================
    {
        "id": "kb-tariff-rcep",
        "title": "RCEP 区域全面经济伙伴关系协定（2022 年生效）",
        "country": "ASEAN+5",
        "category": "关税 / 自由贸易协定",
        "summary": "RCEP 涵盖东盟 10 国 + 中日韩澳新 15 国，是全球最大自贸区。协定生效后 90% 以上货物关税将逐步降至零，成员国采用统一原产地累积规则（区域价值成分 ≥40% 可享优惠税率）。跨境电商卖家可凭 RCEP 原产地证享受关税减免，中日之间首次建立自贸关系，机电、纺织品类降幅显著。",
        "url": "https://asean.org/our-communities/economic-community/integration-and-market-access/regional-comprehensive-economic-partnership-rcep/",
    },
    {
        "id": "kb-tariff-acfta",
        "title": "中国-东盟自贸区 ACFTA（Form E 原产地证）",
        "country": "ASEAN",
        "category": "关税 / 自由贸易协定",
        "summary": "中国-东盟自贸区（CAFTA/ACFTA）是中国与东盟十国组建的自由贸易区，自 2005 年起逐步降税，目前已实现 90% 以上商品零关税。出口商可申请 Form E 原产地证书享受协定优惠税率，涵盖文莱、柬埔寨、印尼、老挝、马来西亚、缅甸、菲律宾、新加坡、泰国、越南。跨境电商经东盟转口或直发需关注 Form E 申报与原产地规则。",
        "url": "https://asean.org/our-communities/economic-community/",
    },
    {
        "id": "kb-tariff-eu-taric",
        "title": "欧盟综合关税税则 TARIC（Combined Nomenclature）",
        "country": "EU",
        "category": "关税 / 进口关税",
        "summary": "TARIC 是欧盟统一关税税则数据库，包含全部 HS 编码对应的协定税率、最惠国税率（MFN）、反倾销/反补贴税、关税配额等。进口商品关税取决于 HS 编码与原产国，可通过 TARIC 在线查询系统（ec.europa.eu/taxation_customs/dds2/taric/）获取精确税率。欧盟 MFN 税率平均约 5.1%，但农产品、纺织品类税率较高（10%-12%）。",
        "url": "https://ec.europa.eu/taxation_customs/dds2/taric/taric_consultation.jsp",
    },
    {
        "id": "kb-tariff-eu-low-value-parcel-2026",
        "title": "欧盟 2026 年小额包裹关税改革（≤150 欧元）",
        "country": "EU",
        "category": "关税 / 跨境电商",
        "summary": "自 2026 年 7 月 1 日起，欧盟对申报价值 ≤150 欧元的进口小包裹征收固定关税：邮政渠道每件 2 欧元、其他渠道（快递/海运/空运）每件 3 欧元，按同一包裹内不同税则品目（tariff heading）分别计征。此举旨在应对 Temu、Shein 等中国电商平台海量小包冲击，取代原 ≤150 欧元免关税政策。同时 IOSS 机制继续适用 VAT 征收。欧洲邮政联盟已请愿推迟实施半年。",
        "url": "https://taxation-customs.ec.europa.eu/customs-4/eu-customs-reform_en",
    },
    {
        "id": "kb-tariff-eu-cbam",
        "title": "欧盟碳边境调节机制 CBAM（2026 年正式实施）",
        "country": "EU",
        "category": "关税 / 碳关税",
        "summary": "CBAM 被称为「碳关税」，2023 年 10 月起过渡期（仅申报），2026 年 1 月 1 日起进入正式实施阶段（需购买 CBAM 证书缴税）。覆盖水泥、钢铁、铝、化肥、电力、氢气六大行业，进口商须申报产品隐含碳排放并购买对应 CBAM 证书（价格与 EU ETS 碳价挂钩）。欧盟委员会已提议将范围扩大至下游产品。中国出口企业需建立碳足迹核算体系应对。",
        "url": "https://taxation-customs.ec.europa.eu/green-taxation-0/carbon-border-adjustment-mechanism_en",
    },
    {
        "id": "kb-tariff-us-htsus",
        "title": "美国协调关税表 HTSUS（Harmonized Tariff Schedule）",
        "country": "US",
        "category": "关税 / 进口关税",
        "summary": "HTSUS 是美国进口商品关税分类体系，基于国际 HS 编码（前 6 位全球统一）扩展至 10 位。关税税率分为普通（NTR/MFN）、特殊（协定优惠如 GSP/FTA）和法定（Column 2，非 NTR 国家）三列。美国平均 MFN 税率约 3.4%，但农产品、纺织服装、鞋类税率较高（最高达 37.5%）。可通过 USITC 在线查询系统（hts.usitc.gov）获取最新税率。",
        "url": "https://hts.usitc.gov/",
    },
    {
        "id": "kb-tariff-us-section-301",
        "title": "美国 301 条款对华加征关税（2025-2026 最新）",
        "country": "US",
        "category": "关税 / 加征关税",
        "summary": "依据 Section 301 调查，美国对中国进口商品分批加征 25%/7.5% 附加关税。2025 年起进一步加征：电动汽车 100%、太阳能电池 50%、锂电池 25%、钢铝 25%、半导体 50%。部分关税豁免延长至 2026 年 11 月 10 日。2025 年 7 月起 USTR 推出分级关税方案，跨境电商涉及含芯片的电子产品受影响最大。卖家需在 HTSUS 基础上叠加 301 关税计算总税负。",
        "url": "https://ustr.gov/issue-areas/enforcement/section-301-investigations/tariff-actions",
    },
    {
        "id": "kb-tariff-us-de-minimis",
        "title": "美国小额免税 De Minimis / Section 321 / T86（2025 改革）",
        "country": "US",
        "category": "关税 / 小额免税",
        "summary": "美国 Section 321 规定每人每天进口 ≤800 美元商品免关税（De Minimis）。Type 86（T86）入境形式允许跨境电商小包快速清关。2025 年特朗普政府签署行政命令，取消加拿大和墨西哥经小包路径的 $800 免税待遇，并对中国小包加征 10% 临时关税（7 月 24 日到期后拟加征 12.5%）。预计 De Minimis 阈值将进一步收紧，Temu/Shein 等平台受重大影响。",
        "url": "https://www.cbp.gov/trade/programs-administration/entry-summary/entry-type-86",
    },
    {
        "id": "kb-tariff-jp-reverse-calculation",
        "title": "日本逆算法（海关估价 / 关税计算方式）",
        "country": "JP",
        "category": "关税 / 海关估价",
        "summary": "日本海关对跨境电商进口商品采用「逆算法」（逆算征收）确定完税价格：以日本国内销售价倒推 CIF 价格（CIF = 零售价 - 日本国内费用 - 合理利润），而非按采购价或申报价征税。该方式大幅提高了跨境电商卖家的关税和消费税税基，亚马逊日本站直邮卖家受影响最大。建议卖家合理设定日本零售价、保留完整供应链成本凭证，并在定价时预留逆算关税成本。",
        "url": "https://www.customs.go.jp/english/summary/import_export_201116.pdf",
    },
    {
        "id": "kb-tariff-jp-epa",
        "title": "日本经济伙伴协定 EPA 网络（含中日 RCEP）",
        "country": "JP",
        "category": "关税 / 自由贸易协定",
        "summary": "日本已签署 21 个 EPA/FTA，包括 RCEP（2022 年生效，中日首次建立自贸关系）、CPTPP、日欧 EPA、日美贸易协定等。通过 RCEP，日本对中国约 92% 税目逐步降税，机电产品、纺织原料等受益。出口商需申请 RCEP 原产地证（由海关或贸促会签发）方可享受优惠税率。日本关税可通过海关网站 AEO / TARIF 系统查询具体 HS 编码税率。",
        "url": "https://www.mofa.go.jp/policy/economy/fta/",
    },
    {
        "id": "kb-tariff-kr-ckfta",
        "title": "中韩自由贸易协定 CKFTA（2015 年生效）",
        "country": "KR",
        "category": "关税 / 自由贸易协定",
        "summary": "中韩 FTA（CKFTA）2015 年 12 月生效，20 年过渡期内实现 90% 税目零关税。韩国对中国降税产品包括纺织服装、家电、农产品等，中国对韩国降税产品包括电子元器件、化工品等。出口商凭中韩 FTA 原产地证书（Form CK）享受优惠税率。韩国 MFN 平均关税约 13.3%，但制造业产品关税较低。跨境电商经韩国中转或直发需关注 CKFTA 与 K-REACH 双重合规。",
        "url": "https://www.customs.go.kr/english/main.dut",
    },
    {
        "id": "kb-tariff-in-bcd-igst-sws",
        "title": "印度三层关税结构 BCD + IGST + SWS",
        "country": "IN",
        "category": "关税 / 进口关税",
        "summary": "印度进口关税由三层构成：① 基本关税 BCD（Basic Customs Duty，税率 0%-150% 不等，电子产品常见 10%-20%）；② 社会福利附加税 SWS（Social Welfare Surcharge，为 BCD 的 10%）；③ 综合商品与服务税 IGST（Integrated GST，5%-28%，视商品类别而定）。总税负 = BCD + SWS + IGST，跨境电商电子产品综合税负可达 30%-45%。可通过印度海关 ICEGATE 系统（icegate.gov.in）按 HS 编码查询精确税率。",
        "url": "https://www.icegate.gov.in/",
    },
    {
        "id": "kb-tariff-mercosur-cet",
        "title": "南方共同市场 MERCOSUR 共同对外关税 CET",
        "country": "BR/AR/PY/UY",
        "category": "关税 / 共同关税",
        "summary": "MERCOSUR（南共市）由巴西、阿根廷、巴拉圭、乌拉圭组成，实行共同对外关税（CET），大部分商品关税税率 0%-20%，平均约 11%。成员国对部分敏感商品可保留例外清单。2025 年欧盟与 MERCOSUR 达成政治协议推进自贸协定谈判。跨境电商出口南美需注意巴西「Remessa Conforme」进口合规计划（≤50 美元小包免关税但征 17% ICMS，>50 美元征 60% 进口税 + 17% ICMS）。",
        "url": "https://www.mercosur.int/",
    },
    {
        "id": "kb-tariff-br-remessa-conforme",
        "title": "巴西 Remessa Conforme 进口合规计划（小额包裹税改）",
        "country": "BR",
        "category": "关税 / 跨境电商",
        "summary": "巴西 2023 年推出 Remessa Conforme（进口合规计划），加入平台的跨境小包享优惠税率：≤50 美元免进口关税但需缴纳 17% ICMS（州商品流通税）；>50 美元征收 60% 进口统一税 + 17% ICMS。2024 年 8 月起，Shein/Temu/AliExpress 等平台因税率优惠引发争议，巴西政府拟对 ≤50 美元包裹恢复 20% 进口税。目前约 94.5% 入境包裹通过 Remessa Conforme 渠道申报。",
        "url": "https://www.gov.br/receitafederal/pt-br/assuntos/aduana-e-comercio-exterior/remessa-conforme",
    },
    {
        "id": "kb-tariff-gcc-unified",
        "title": "海湾合作委员会 GCC 统一关税（5% 共同对外关税）",
        "country": "SA/AE/KW/QA/BH/OM",
        "category": "关税 / 共同关税",
        "summary": "GCC（海湾合作委员会）六国（沙特、阿联酋、科威特、卡塔尔、巴林、阿曼）实行统一海关法与共同对外关税，大部分商品关税 5%。部分商品免税（如基本食品、药品），烟草/酒精饮料税率 50%-100%。沙特对部分商品（如钢铁、建材）征收额外保护性关税。跨境电商出口中东需同时关注 GCC 认证（G-Mark）与各国本地认证（如沙特 SASO、阿联酋 ECAS）。",
        "url": "https://www.gcc-sg.org/en-us/AboutGCC/Overview",
    },
    {
        "id": "kb-tariff-chafta",
        "title": "中澳自由贸易协定 CHAFTA（2015 年生效）",
        "country": "AU",
        "category": "关税 / 自由贸易协定",
        "summary": "中澳 FTA（CHAFTA）2015 年 12 月生效，中国对澳大利亚出口商品降税覆盖面达 97%，包括机电产品、纺织品、服装等降为零关税。出口商可申请 CHAFTA 原产地证书（Certificate of Origin Form for China-Australia Free Trade Agreement）享受优惠税率。澳大利亚 MFN 平均关税约 3.5%，制造业产品多为 5%。2024 年中澳签署 CHAFTA 实施与审查谅解备忘录，进一步深化协定执行。",
        "url": "https://www.dfat.gov.au/trade/agreements/in-force/chafta",
    },
    {
        "id": "kb-tariff-global-de-minimis",
        "title": "全球小额免税（De Minimis）阈值对比",
        "country": "Global",
        "category": "关税 / 小额免税",
        "summary": "各国 De Minimis 阈值差异巨大：美国 $800（Section 321，改革收紧中）、澳大利亚 AUD$1,000、加拿大 CAD$20（低值免税）、日本 ¥10,000（约 $67）、欧盟 €150（2026 年 7 月起征固定关税）、英国 £135、新加坡 SGD$400、巴西 $50（Remessa Conforme）。阈值越高，跨境电商小包越容易免税入境。美国改革和欧盟新规标志着全球 De Minimis 收紧趋势，将深刻影响 Temu/Shein/AliExpress 等平台商业模式。",
        "url": "https://www.wcoomd.org/en/topics/facilitation/activities-and-programmes/national-customs-environments/~/media/4BFF52B47BFA481CB1A2C5C4F4C547F5.ashx",
    },
]

# ---------------------------------------------------------------------------
# Pydantic 模型
# ---------------------------------------------------------------------------

class TaskCreateRequest(BaseModel):
    task_type: str = Field(..., description="任务类型，如 receipt_parse / compliance_check / full_workflow")
    platform: str = Field(default="amazon", description="交易平台，如 amazon/ebay/shopify")
    target_country: str = Field(default="US", description="目标国家/地区")
    receipt_text: Optional[str] = Field(default=None, description="票据文本（无图片时使用）")
    receipt_image_base64: Optional[str] = Field(default=None, description="票据图片 base64")


class ReceiptParseRequest(BaseModel):
    image_base64: str = Field(..., description="票据图片 base64 字符串")
    platform: str = Field(default="amazon", description="交易平台")
    target_country: str = Field(default="US", description="目标国家")


class ComplianceChatRequest(BaseModel):
    query: str = Field(..., description="用户合规咨询问题")
    target_country: str = Field(default="US")
    product_info: Optional[str] = Field(default=None, description="产品信息描述")


class RiskAssessmentRequest(BaseModel):
    product_info: str = Field(..., description="产品信息")
    target_country: str = Field(default="US")
    platform: str = Field(default="amazon")


# ---------------------------------------------------------------------------
# 生命周期
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("FinFlow AaaS 服务启动")
    yield
    logger.info("FinFlow AaaS 服务关闭，释放 GMI 客户端")
    await gmi_client.close()


# ---------------------------------------------------------------------------
# FastAPI 应用
# ---------------------------------------------------------------------------

app = FastAPI(
    title="FinFlow AaaS API",
    description="跨境财税智能体服务 — 基于 GMI Cloud 多模型编排",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS 允许所有源
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 静态文件服务（前端放在 static/ 目录）
app.mount("/static", StaticFiles(directory="static", html=True), name="static")


# ---------------------------------------------------------------------------
# WebSocket 进度推送
# ---------------------------------------------------------------------------

async def broadcast_progress(task_id: str, event: Dict[str, Any]) -> None:
    """向订阅了某任务的 WebSocket 推送进度事件"""
    subs = ws_subscribers.get(task_id, set())
    if not subs:
        return
    message = json.dumps(event, ensure_ascii=False, default=str)
    dead: List[WebSocket] = []
    for ws in list(subs):
        try:
            await ws.send_text(message)
        except Exception:
            dead.append(ws)
    for ws in dead:
        subs.discard(ws)


async def make_progress_callback(task_id: str):
    """创建绑定到 task_id 的进度回调"""

    async def _cb(step: str, status: str, message: str, data: Optional[Dict[str, Any]] = None):
        event = {
            "task_id": task_id,
            "step": step,
            "status": status,
            "message": message,
            "data": data,
            "timestamp": time.time(),
        }
        progress_db.setdefault(task_id, []).append(event)
        logger.info("[task=%s] %s/%s — %s", task_id, step, status, message)
        await broadcast_progress(task_id, event)

    return _cb


# ---------------------------------------------------------------------------
# 异步任务执行
# ---------------------------------------------------------------------------

async def execute_task(task_id: str, request: TaskCreateRequest) -> None:
    """异步执行业财自动化任务

    流程：
      1. 调度 Agent 分解任务
      2. 票据解析 Agent 解析票据
      3. 合规决策 Agent 进行税法匹配和风险检测
      4. 聚合结果生成报表
      5. 每个步骤通过 WebSocket 推送进度
    """
    task = tasks_db[task_id]
    task["status"] = "running"
    task["started_at"] = time.time()
    progress_cb = await make_progress_callback(task_id)

    try:
        await progress_cb("task_start", "running", f"任务开始：{request.task_type} / {request.platform} / {request.target_country}", None)

        # 委托给 OrchestratorAgent 完整编排（内部已含规划/解析/合规/报表四步）
        result = await orchestrator.execute_task(
            task_type=request.task_type,
            platform=request.platform,
            target_country=request.target_country,
            receipt_text=request.receipt_text,
            receipt_image_base64=request.receipt_image_base64,
            progress=progress_cb,
        )

        task["result"] = result
        task["status"] = "completed" if not result.get("errors") else "completed_with_errors"
        task["completed_at"] = time.time()

        # 保存报表
        if result.get("report"):
            reports_db[task_id] = {
                "task_id": task_id,
                "report": result["report"],
                "generated_at": time.time(),
            }

        await progress_cb(
            "task_complete",
            "done",
            "任务执行完成",
            {
                "status": task["status"],
                "has_report": bool(result.get("report")),
                "errors": result.get("errors", []),
            },
        )

    except Exception as exc:
        logger.exception("任务执行异常 task=%s", task_id)
        task["status"] = "failed"
        task["error"] = str(exc)
        task["completed_at"] = time.time()
        await progress_cb("task_error", "error", f"任务执行失败: {exc}", {"error": str(exc)})


# ---------------------------------------------------------------------------
# REST API
# ---------------------------------------------------------------------------

@app.get("/api")
@app.get("/api/docs")
async def api_docs():
    """API 文档信息（接口列表）"""
    return {
        "name": "FinFlow AaaS API",
        "version": "1.0.0",
        "description": "跨境财税智能体服务 — 基于 GMI Cloud 多模型编排",
        "endpoints": [
            {"method": "GET", "path": "/api/dashboard", "desc": "仪表盘数据（任务统计、Agent 状态、Token 预算消耗）"},
            {"method": "GET", "path": "/api/gmi/models", "desc": "获取 GMI 可用模型列表"},
            {"method": "GET", "path": "/api/gmi/status", "desc": "GMI 推理实例状态与 Token 消耗"},
            {"method": "POST", "path": "/api/tasks", "desc": "创建财税处理任务"},
            {"method": "GET", "path": "/api/tasks", "desc": "任务列表"},
            {"method": "GET", "path": "/api/tasks/{task_id}", "desc": "查询任务状态"},
            {"method": "POST", "path": "/api/receipts/parse", "desc": "直接上传票据图片进行 OCR 解析"},
            {"method": "GET", "path": "/api/reports/{task_id}", "desc": "获取生成的财税报表"},
            {"method": "POST", "path": "/api/compliance/chat", "desc": "跨境合规咨询对话（ComplianceConsultationAgent）"},
            {"method": "GET", "path": "/api/compliance/chat/history", "desc": "获取合规咨询对话历史"},
            {"method": "POST", "path": "/api/compliance/risk-assess", "desc": "执行跨境合规风险评估（RiskAssessmentAgent）"},
            {"method": "GET", "path": "/api/compliance/risk-score", "desc": "获取最新合规健康分"},
            {"method": "GET", "path": "/api/compliance/alerts", "desc": "获取分级告警列表（red > yellow > blue）"},
            {"method": "GET", "path": "/api/knowledge/documents", "desc": "知识库法规文档列表（EU/US/JP/KR 等）"},
            {"method": "GET", "path": "/api/knowledge/search", "desc": "知识库搜索（按标题/摘要匹配）"},
            {"method": "GET", "path": "/api/docs", "desc": "API 文档信息（本接口）"},
            {"method": "WS", "path": "/ws", "desc": "WebSocket 实时推送任务进度（订阅 task_id）"},
        ],
        "models": list(PRICING.keys()),
    }


@app.get("/api/dashboard")
async def dashboard():
    """仪表盘数据：任务统计、Agent 状态、Token 预算消耗"""
    total = len(tasks_db)
    status_counts: Dict[str, int] = {}
    for t in tasks_db.values():
        s = t.get("status", "pending")
        status_counts[s] = status_counts.get(s, 0) + 1

    gmi_status = gmi_client.get_status()

    return {
        "task_stats": {
            "total": total,
            "by_status": status_counts,
        },
        "gmi": {
            "instances": gmi_status["instances"],
            "token_budget": gmi_status["token_budget"],
            "usage": gmi_status["usage"],
        },
        "agents": [
            {"name": "ReceiptParserAgent", "model": "openai/gpt-4o + deepseek-ai/DeepSeek-V3.2", "status": "ready"},
            {"name": "ComplianceAgent", "model": "openai/gpt-5 + zai-org/GLM-5-FP8", "status": "ready"},
            {"name": "OrchestratorAgent", "model": "Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8", "status": "ready"},
            {"name": "ComplianceConsultationAgent", "model": "zai-org/GLM-5-FP8", "status": "ready"},
            {"name": "RiskAssessmentAgent", "model": "openai/gpt-5", "status": "ready"},
        ],
        "recent_tasks": [
            {
                "task_id": tid,
                "task_type": t.get("request", {}).get("task_type"),
                "status": t.get("status"),
                "created_at": t.get("created_at"),
            }
            for tid, t in list(tasks_db.items())[-10:]
        ],
        "timestamp": time.time(),
    }


@app.get("/api/gmi/models")
async def gmi_models():
    """获取 GMI 可用模型列表"""
    models_data = await gmi_client.list_models()
    # 附加价格信息
    enriched: List[Dict[str, Any]] = []
    data = models_data.get("data", []) if isinstance(models_data, dict) else []
    for m in data:
        mid = m.get("id", "") if isinstance(m, dict) else str(m)
        price = PRICING.get(mid, {})
        enriched.append({
            "id": mid,
            "object": m.get("object", "model") if isinstance(m, dict) else "model",
            "owned_by": m.get("owned_by", "gmi") if isinstance(m, dict) else "gmi",
            "pricing": price,
        })
    return {
        "object": "list",
        "data": enriched,
        "source": models_data.get("source", "gmi_api") if isinstance(models_data, dict) else "gmi_api",
    }


@app.get("/api/gmi/status")
async def gmi_status():
    """GMI 推理实例状态（5个实例的运行状态、Token 消耗）"""
    return gmi_client.get_status()


@app.post("/api/tasks")
async def create_task(request: TaskCreateRequest):
    """创建财税处理任务

    接受 task_type, platform, target_country, receipt_text 或 receipt_image_base64。
    创建后异步执行，通过 WebSocket 推送进度。
    """
    # 至少要有一种输入
    if not request.receipt_text and not request.receipt_image_base64:
        # 允许无票据的任务（仅做合规咨询），但记录提示
        pass

    task_id = f"task-{uuid.uuid4().hex[:12]}"
    now = time.time()
    tasks_db[task_id] = {
        "task_id": task_id,
        "request": request.model_dump(),
        "status": "pending",
        "created_at": now,
        "started_at": None,
        "completed_at": None,
        "result": None,
        "error": None,
    }
    progress_db[task_id] = []

    # 异步执行任务（不阻塞响应）
    asyncio.create_task(execute_task(task_id, request))

    return {
        "task_id": task_id,
        "status": "pending",
        "message": "任务已创建，正在异步执行。请通过 WebSocket /ws 订阅进度，或轮询 GET /api/tasks/{task_id}。",
        "ws_url": f"/ws?task_id={task_id}",
        "created_at": now,
    }


@app.get("/api/tasks")
async def list_tasks(status: Optional[str] = None, limit: int = 50):
    """任务列表"""
    items = list(tasks_db.values())
    if status:
        items = [t for t in items if t.get("status") == status]
    items = sorted(items, key=lambda t: t.get("created_at", 0), reverse=True)[:limit]
    return {
        "total": len(items),
        "tasks": [
            {
                "task_id": t["task_id"],
                "task_type": t["request"].get("task_type"),
                "platform": t["request"].get("platform"),
                "target_country": t["request"].get("target_country"),
                "status": t.get("status"),
                "created_at": t.get("created_at"),
                "completed_at": t.get("completed_at"),
            }
            for t in items
        ],
    }


@app.get("/api/tasks/{task_id}")
async def get_task(task_id: str):
    """查询任务状态"""
    task = tasks_db.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")
    return {
        "task_id": task_id,
        "request": task.get("request"),
        "status": task.get("status"),
        "created_at": task.get("created_at"),
        "started_at": task.get("started_at"),
        "completed_at": task.get("completed_at"),
        "result": task.get("result"),
        "error": task.get("error"),
        "progress": progress_db.get(task_id, []),
    }


@app.post("/api/receipts/parse")
async def parse_receipt(request: ReceiptParseRequest):
    """直接上传票据图片进行 OCR 解析

    同步调用 ReceiptParserAgent，返回解析结果。
    """
    from agents import ReceiptParserAgent

    agent = ReceiptParserAgent()
    try:
        result = await agent.parse_image(image_base64=request.image_base64)
        return {
            "status": "success",
            "parsed_data": result.get("parsed_data"),
            "verification": result.get("verification"),
            "final_data": result.get("final_data"),
            "steps": result.get("steps"),
            "errors": result.get("errors"),
        }
    except Exception as exc:
        logger.exception("票据解析失败")
        raise HTTPException(status_code=500, detail=f"票据解析失败: {exc}")


@app.get("/api/reports/{task_id}")
async def get_report(task_id: str):
    """获取生成的财税报表"""
    if task_id not in tasks_db:
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")
    report = reports_db.get(task_id)
    if report is None:
        # 任务可能尚未完成，返回任务状态
        task = tasks_db[task_id]
        raise HTTPException(
            status_code=404,
            detail=f"报表尚未生成，当前任务状态: {task.get('status')}",
        )
    return report


# ---------------------------------------------------------------------------
# 跨境合规咨询与风险评估 API
# ---------------------------------------------------------------------------

@app.post("/api/compliance/chat")
async def compliance_chat(request: ComplianceChatRequest):
    """跨境合规咨询对话

    调用 ComplianceConsultationAgent，返回 HS 编码、税率、
    认证要求、合规建议与风险标记，并保存对话历史。
    """
    try:
        result = await consultation_agent.consult(
            query=request.query,
            target_country=request.target_country,
            product_info=request.product_info or "",
        )
    except Exception as exc:
        logger.exception("合规咨询失败")
        raise HTTPException(status_code=500, detail=f"合规咨询失败: {exc}")

    chat_id = f"chat-{uuid.uuid4().hex[:12]}"
    now = time.time()
    record = {
        "chat_id": chat_id,
        "query": request.query,
        "target_country": request.target_country,
        "product_info": request.product_info,
        "result": result,
        "created_at": now,
    }
    compliance_chats_db.append(record)

    return {
        "chat_id": chat_id,
        "status": "success" if not result.get("errors") else "completed_with_errors",
        "query": request.query,
        "target_country": request.target_country,
        "consultation": result.get("consultation"),
        "usage": result.get("usage"),
        "model": result.get("model"),
        "errors": result.get("errors", []),
        "created_at": now,
    }


@app.get("/api/compliance/chat/history")
async def compliance_chat_history(limit: int = 50):
    """获取合规咨询对话历史（最近对话）"""
    items = sorted(compliance_chats_db, key=lambda x: x.get("created_at", 0), reverse=True)[:limit]
    return {
        "total": len(compliance_chats_db),
        "returned": len(items),
        "history": [
            {
                "chat_id": x["chat_id"],
                "query": x.get("query"),
                "target_country": x.get("target_country"),
                "product_info": x.get("product_info"),
                "created_at": x.get("created_at"),
                "errors": (x.get("result") or {}).get("errors", []),
            }
            for x in items
        ],
    }


@app.post("/api/compliance/risk-assess")
async def compliance_risk_assess(request: RiskAssessmentRequest):
    """执行跨境合规风险评估

    调用 RiskAssessmentAgent，返回健康分（0-100）、
    风险等级、分级告警（red/yellow/blue）、产品扫描结果与整改建议，
    并保存评估结果。
    """
    try:
        result = await risk_agent.assess(
            product_info=request.product_info,
            target_country=request.target_country,
            platform=request.platform,
        )
    except Exception as exc:
        logger.exception("风险评估失败")
        raise HTTPException(status_code=500, detail=f"风险评估失败: {exc}")

    assessment_id = f"risk-{uuid.uuid4().hex[:12]}"
    now = time.time()
    record = {
        "assessment_id": assessment_id,
        "product_info": request.product_info,
        "target_country": request.target_country,
        "platform": request.platform,
        "result": result,
        "created_at": now,
    }
    risk_assessments_db.append(record)

    return {
        "assessment_id": assessment_id,
        "status": "success" if not result.get("errors") else "completed_with_errors",
        "product_info": request.product_info,
        "target_country": request.target_country,
        "platform": request.platform,
        "assessment": result.get("assessment"),
        "usage": result.get("usage"),
        "model": result.get("model"),
        "errors": result.get("errors", []),
        "created_at": now,
    }


@app.get("/api/compliance/risk-score")
async def compliance_risk_score():
    """获取最新合规健康分

    从 risk_assessments_db 取最新评估；若无评估，
    返回默认值（health_score=67, risk_level=medium, alerts=空列表）。
    """
    if not risk_assessments_db:
        return {
            "has_assessment": False,
            "health_score": 67,
            "risk_level": "medium",
            "alerts": [],
            "message": "尚未执行风险评估，返回默认健康分",
            "timestamp": time.time(),
        }

    latest = max(risk_assessments_db, key=lambda x: x.get("created_at", 0))
    assessment = (latest.get("result") or {}).get("assessment") or {}
    return {
        "has_assessment": True,
        "assessment_id": latest.get("assessment_id"),
        "health_score": assessment.get("health_score", 0),
        "risk_level": assessment.get("risk_level", "unknown"),
        "alerts": assessment.get("alerts", []),
        "target_country": latest.get("target_country"),
        "platform": latest.get("platform"),
        "created_at": latest.get("created_at"),
        "timestamp": time.time(),
    }


@app.get("/api/compliance/alerts")
async def compliance_alerts():
    """获取分级告警列表

    汇总所有评估中的告警，按级别排序（red > yellow > blue）。
    """
    level_order = {"red": 0, "yellow": 1, "blue": 2}

    all_alerts: List[Dict[str, Any]] = []
    for record in risk_assessments_db:
        assessment = (record.get("result") or {}).get("assessment") or {}
        for alert in assessment.get("alerts", []) or []:
            if isinstance(alert, dict):
                all_alerts.append({
                    "level": alert.get("level", "blue"),
                    "title": alert.get("title", ""),
                    "description": alert.get("description", ""),
                    "recommendation": alert.get("recommendation", ""),
                    "assessment_id": record.get("assessment_id"),
                    "target_country": record.get("target_country"),
                    "platform": record.get("platform"),
                    "created_at": record.get("created_at"),
                })

    all_alerts.sort(key=lambda a: (level_order.get(a["level"], 99), -(a.get("created_at") or 0)))

    level_counts = {"red": 0, "yellow": 0, "blue": 0}
    for a in all_alerts:
        lv = a["level"]
        if lv in level_counts:
            level_counts[lv] += 1

    return {
        "total": len(all_alerts),
        "by_level": level_counts,
        "alerts": all_alerts,
        "timestamp": time.time(),
    }


# ---------------------------------------------------------------------------
# 知识库 API
# ---------------------------------------------------------------------------

@app.get("/api/knowledge/documents")
async def knowledge_documents(country: Optional[str] = None):
    """知识库法规文档列表

    返回预置的法规知识库文档（EU/US/JP/KR 等），每个文档含
    id / title / country / category / summary / url。
    可通过 ?country= 过滤。
    """
    docs = KNOWLEDGE_DOCUMENTS
    if country:
        country_upper = country.upper()
        docs = [d for d in docs if d.get("country", "").upper() == country_upper]
    return {
        "total": len(docs),
        "documents": docs,
        "timestamp": time.time(),
    }


@app.get("/api/knowledge/search")
async def knowledge_search(q: str, limit: int = 20):
    """知识库搜索

    在预置知识库中搜索标题/摘要匹配的文档，返回匹配结果。
    """
    if not q or not q.strip():
        return {
            "query": q,
            "total": 0,
            "results": [],
            "message": "查询参数 q 不能为空",
        }

    keyword = q.strip().lower()
    results: List[Dict[str, Any]] = []
    for doc in KNOWLEDGE_DOCUMENTS:
        title = (doc.get("title") or "").lower()
        summary = (doc.get("summary") or "").lower()
        category = (doc.get("category") or "").lower()
        if keyword in title or keyword in summary or keyword in category:
            # 计算简单匹配评分：标题命中权重更高
            score = 0
            if keyword in title:
                score += 3
            if keyword in category:
                score += 2
            if keyword in summary:
                score += 1
            enriched = dict(doc)
            enriched["score"] = score
            results.append(enriched)

    results.sort(key=lambda x: x.get("score", 0), reverse=True)
    results = results[:limit]
    return {
        "query": q,
        "total": len(results),
        "results": results,
        "timestamp": time.time(),
    }


# ---------------------------------------------------------------------------
# 根路由 -> 静态前端
# ---------------------------------------------------------------------------

from fastapi.responses import FileResponse

@app.get("/")
async def root():
    """根路由，返回前端控制台"""
    return FileResponse("static/index.html", media_type="text/html")


# ---------------------------------------------------------------------------
# WebSocket /ws
# ---------------------------------------------------------------------------

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket 实时推送任务进度

    连接时可带 ?task_id=xxx 订阅指定任务；
    也可连接后发送 {"action": "subscribe", "task_id": "xxx"} 动态订阅。
    """
    await websocket.accept()

    # 从查询参数订阅
    query_params = websocket.query_params
    task_id = query_params.get("task_id")

    subscribed: Set[str] = set()
    if task_id:
        ws_subscribers.setdefault(task_id, set()).add(websocket)
        subscribed.add(task_id)
        # 发送已有进度
        for evt in progress_db.get(task_id, []):
            await websocket.send_text(json.dumps(evt, ensure_ascii=False, default=str))

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_text(json.dumps({"error": "invalid json"}))
                continue

            action = msg.get("action")
            tid = msg.get("task_id")

            if action == "subscribe" and tid:
                ws_subscribers.setdefault(tid, set()).add(websocket)
                subscribed.add(tid)
                # 推送历史进度
                for evt in progress_db.get(tid, []):
                    await websocket.send_text(json.dumps(evt, ensure_ascii=False, default=str))
                await websocket.send_text(json.dumps({
                    "type": "subscribed",
                    "task_id": tid,
                    "history_count": len(progress_db.get(tid, [])),
                }))

            elif action == "unsubscribe" and tid:
                ws_subscribers.get(tid, set()).discard(websocket)
                subscribed.discard(tid)
                await websocket.send_text(json.dumps({"type": "unsubscribed", "task_id": tid}))

            elif action == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))

            else:
                await websocket.send_text(json.dumps({"error": "unknown action"}))

    except WebSocketDisconnect:
        logger.info("WebSocket 断开")
    finally:
        for tid in subscribed:
            ws_subscribers.get(tid, set()).discard(websocket)


# ---------------------------------------------------------------------------
# 启动入口
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=8080,
        reload=True,
        log_level="info",
    )
