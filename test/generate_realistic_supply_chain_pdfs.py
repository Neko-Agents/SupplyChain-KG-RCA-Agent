from __future__ import annotations

from pathlib import Path

from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = PROJECT_ROOT / "test" / "realistic_pdf_cases"


def _styles():
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    styles = getSampleStyleSheet()
    styles.add(
        ParagraphStyle(
            name="ZhTitle",
            parent=styles["Title"],
            fontName="STSong-Light",
            fontSize=20,
            leading=28,
            alignment=TA_CENTER,
            textColor=HexColor("#17324d"),
        )
    )
    styles.add(
        ParagraphStyle(
            name="ZhSubTitle",
            parent=styles["BodyText"],
            fontName="STSong-Light",
            fontSize=10,
            leading=14,
            alignment=TA_CENTER,
            textColor=HexColor("#5f6b7a"),
        )
    )
    styles.add(
        ParagraphStyle(
            name="ZhHeading",
            parent=styles["Heading2"],
            fontName="STSong-Light",
            fontSize=13,
            leading=18,
            textColor=HexColor("#224b76"),
            spaceBefore=8,
            spaceAfter=6,
        )
    )
    styles.add(
        ParagraphStyle(
            name="ZhBody",
            parent=styles["BodyText"],
            fontName="STSong-Light",
            fontSize=10.5,
            leading=18,
            textColor=HexColor("#202020"),
            spaceAfter=6,
        )
    )
    styles.add(
        ParagraphStyle(
            name="ZhNote",
            parent=styles["BodyText"],
            fontName="STSong-Light",
            fontSize=9.2,
            leading=15,
            textColor=HexColor("#4e5b68"),
            spaceAfter=5,
        )
    )
    return styles


def _doc_header(story, styles, title: str, subtitle: str):
    story.append(Paragraph(title, styles["ZhTitle"]))
    story.append(Spacer(1, 3 * mm))
    story.append(Paragraph(subtitle, styles["ZhSubTitle"]))
    story.append(Spacer(1, 7 * mm))


def _section(story, styles, heading: str, paragraphs: list[str]):
    story.append(Paragraph(heading, styles["ZhHeading"]))
    for text in paragraphs:
        story.append(Paragraph(text, styles["ZhBody"]))


def _build_doc(output_path: Path, title: str, subtitle: str, sections: list[tuple[str, list[str]]]):
    styles = _styles()
    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=14 * mm,
    )
    story = []
    _doc_header(story, styles, title, subtitle)
    for idx, (heading, paragraphs) in enumerate(sections):
        _section(story, styles, heading, paragraphs)
        if idx < len(sections) - 1:
            story.append(Spacer(1, 3 * mm))
    doc.build(story)


def _news_case() -> tuple[str, str, list[tuple[str, list[str]]]]:
    return (
        "华东消费电子供应链快讯（样本稿）",
        "文档类型：新闻风格测试样本｜用途：验证 llm / llm_rel 对真实叙述文本的抽取能力",
        [
            (
                "一、市场概况",
                [
                    "华东消费电子与智能终端产业链本周再度出现局部波动。多家渠道商和代工企业表示，受上游关键器件交付节奏变化、部分承运线路时效不稳以及海外客户备货前置等因素影响，智能手机、轻薄笔记本和智能穿戴三条产品线的出货安排都有不同程度调整。相比此前直接以字段罗列的测试材料，本样本更接近行业媒体报道与企业内部纪要的混合写法。",
                    "位于苏州的终端品牌厂商星域智能在最近一轮采购沟通会上提到，其面向西南区域客户的“旗舰AI手机 15 Pro”项目仍保持生产，但核心图像模组和高性能存储器件的到货窗口明显收窄。参与该项目的上游企业包括长江存储（YMTC）、京东方（BOE）和北方华创（NAURA），其中北方华创所涉及的材料与设备环节被认为对制造节拍影响较大。",
                ],
            ),
            (
                "二、订单与交付进展",
                [
                    "渠道侧披露的一笔重点订单显示，订单编号 ORD-2026-300187 对应客户为成都星屿科技有限公司，客户ID 为 CUST-91027，采购产品为“14寸轻薄全能本”，产品ID 为 SKU-CE-L01，归属消费电子部门与笔记本电脑品类。该订单数量为 286 台，实付总金额约 1,876,540.00 元，单笔利润约 1,042,300.00 元，付款方式为企业对公转账，当前订单状态显示为“已发货”。",
                    "该订单核心部件来自中芯国际（SMIC）参与的图像传感器供应链，组件名称为 CMOS 图像传感器，单件制造成本约 2,180.60 元，次品率为 0.0058。物流由顺丰速运承接，运输方式为航空货运，发货模式为次晨达，计划物流天数为 1 天，实际物流天数为 2 天，系统在内部看板上已将其标记为“延误发货”，对应 late_risk 为 1。",
                    "另一笔面向重庆教育系统的手机采购订单编号为 ORD-2026-300241，客户名称为重庆市教育信息中心，产品为“旗舰AI手机 15 Pro”，产品ID 为 SKU-CE-P01，供应商链条中重复出现长江存储（YMTC）和京东方（BOE）。该订单数量为 318 台，实付总金额约 2,064,880.40 元，利润约 1,126,900.20 元，承运商为京东物流，运输方式为铁路干线，发货模式为普列运输，计划 5 天到货，实际用了 7 天，交付状态为“延误发货”。",
                ],
            ),
            (
                "三、质量与风险观察",
                [
                    "质量侧最受关注的是 CMOS 图像传感器和聚合物锂电池两类部件。接近整机厂采购部门的人士称，部分供应批次的缺陷率虽然仍处于可控区间，但已明显高于上一季度均值。以重庆项目为例，图像传感器环节的缺陷率接近 0.0049；而另一批由京东方相关链条支撑的智能手机订单中，聚合物锂电池的一致性波动使整机检测与返工成本有所上升。",
                    "从财务暴露来看，当前更值得关注的不是单笔订单金额，而是多笔高价值订单在同一供应商和同一运输模式上的集中度。过去两周在样本中反复出现的企业主要包括北方华创、长江存储、闻泰科技、京东方和中芯国际。一旦其中任意一方在关键器件供货、制造成本或良率方面出现异常，下游手机、笔记本甚至 VR 头显产品都可能受到联动影响。",
                ],
            ),
            (
                "四、样本附注",
                [
                    "除上述订单外，本稿还关注一笔面向华中区域零售渠道的智能穿戴订单：订单编号 ORD-2026-300305，客户名称为武汉嘉盛数码商贸，产品为“4K超清 无线VR一体机”，产品ID 为 SKU-WE-V01，供应商为长江存储（YMTC），核心组件为 OLED 柔性屏，承运商为跨越速运，运输方式为航空货运，发货模式为次晨达，订单数量为 22 台，净销售额约 71,206.80 元，利润约 28,450.00 元，计划 1 天到货但实际 3 天完成签收，交付状态同样被标记为“延误发货”。",
                    "本样本故意保留了新闻式叙述、业务事实、金额、状态、物流和器件信息的混合表达，以便测试系统对更真实供应链新闻文本的抽取效果。"
                ],
            ),
        ],
    )


def _weekly_report_case() -> tuple[str, str, list[tuple[str, list[str]]]]:
    return (
        "终端制造与交付周报（第15周样本）",
        "文档类型：内部运营周报风格｜用途：验证半结构化长文本中的订单、物流与质量字段抽取",
        [
            (
                "一、周度总体情况",
                [
                    "本周公司终端交付节奏整体稳定，但消费电子和智能穿戴两条产品线仍受到上游器件供应波动影响。手机线新增有效订单 5 笔，笔记本电脑线新增订单 3 笔，VR 头显新增订单 2 笔。与上一周相比，交付状态被标记为“延误发货”的订单数量小幅增加，主要集中在铁路干线与普列运输路径。",
                    "本周重点监控的订单合计净销售额约 8,942,700 元，对应利润规模接近 5,010,000 元。高价值订单仍集中在旗舰AI手机与轻薄笔记本两类产品，其中利润暴露最大的三笔订单分别来自成都、长沙和重庆市场。"
                ],
            ),
            (
                "二、手机产品线",
                [
                    "订单 ORD-2026-310102 对应客户为长沙市智慧教育中心，客户ID 为 CUST-92011，采购产品为“旗舰AI手机 15 Pro”，产品ID 为 SKU-CE-P01，归属消费电子部门与智能手机品类。订单数量为 342 台，净销售额约 2,206,314.00 元，利润约 1,287,442.00 元，付款方式为微信支付，订单状态为“交易完成”。",
                    "该订单上游涉及北方华创（NAURA）提供的光学镜头链条以及京东方（BOE）显示模组链条。承运商为中通快递，运输方式为铁路干线，发货模式为普列运输，计划物流天数 5 天，实际物流天数 6 天，系统将其记为 late_risk=1，delivery_status 为“延误发货”。",
                    "订单 ORD-2026-310118 面向宁波常雪科技有限公司，采购同款旗舰AI手机 15 Pro 共 276 台，净销售额约 1,782,460.00 元，利润约 812,370.00 元。该订单核心组件来自京东方（BOE）提供的聚合物锂电池方案，次品率约 0.0087，承运商为京东物流，运输方式为公路干线，发货模式为标准汽运，计划 3 天，实际 5 天到货。"
                ],
            ),
            (
                "三、笔记本与穿戴产品线",
                [
                    "订单 ORD-2026-310205 由成都马涵科技有限公司下达，客户ID 为 CUST-92459，采购“14寸 轻薄全能本”299 台，产品ID 为 SKU-CE-L01。供应商链条中，中芯国际（SMIC）所关联的 CMOS 图像传感器环节仍是关键节点，单件制造成本约 2,405.70 元，次品率 0.0078。该订单由顺丰速运承运，运输方式为航空货运，发货模式为次晨达，计划 1 天、实际 1 天到货，因此被记为按时发货。",
                    "订单 ORD-2026-310244 则对应智能穿戴业务，客户为武汉视联数字科技，产品为“4K超清 无线VR一体机”，产品ID 为 SKU-WE-V01，供应商为长江存储（YMTC），核心组件为 OLED 柔性屏。订单数量 36 台，净销售额约 119,880.00 元，利润约 45,600.00 元，承运商为跨越速运，计划物流 1 天，实际 2 天，虽然整体金额不大，但在样本中属于高毛利且交付敏感的典型场景。"
                ],
            ),
            (
                "四、风险提示",
                [
                    "本周观察到的主要风险包括：一是单一组件在多笔订单中的重复出现导致集中暴露提升，尤其是 CMOS 图像传感器和 OLED 柔性屏；二是同一承运商在铁路干线与普列运输场景下反复出现时效偏差；三是企业对公转账订单通常金额更高，一旦交付延误，对利润兑现和客户满意度影响更明显。",
                    "建议后续继续对单一来源组件、供应商集中度、延迟根因组合以及客户侧的延迟暴露进行专项分析。"
                ],
            ),
        ],
    )


def _notice_case() -> tuple[str, str, list[tuple[str, list[str]]]]:
    return (
        "采购与物流联合通告（保障重点订单）",
        "文档类型：正式通知 / 公告风格｜用途：验证规则文本中的实体关系与约束性信息抽取",
        [
            (
                "一、通告背景",
                [
                    "鉴于近期消费电子与智能穿戴业务出现阶段性交付波动，采购中心与物流管理部联合发布本通告，要求各区域仓配、采购、质量与客户交付团队针对重点订单开展保障行动。重点订单主要涉及旗舰AI手机、14寸轻薄全能本以及 4K 超清无线 VR 一体机三类产品。",
                    "经复盘，导致当前风险上升的主要因素包括：上游关键组件供货时间不稳定、承运线路时效离散度扩大，以及部分高价值订单过度依赖单一供应来源。"
                ],
            ),
            (
                "二、重点订单说明",
                [
                    "订单 ORD-2026-320041 对应客户为青岛市教育局信息装备中心，产品为“旗舰AI手机 15 Pro”，数量 305 台，产品ID 为 SKU-CE-P01，净销售额约 1,954,880.00 元，利润约 1,006,210.00 元。当前上游供应商涉及北方华创（NAURA）与长江存储（YMTC），核心组件包括光学镜头和高性能存储器件，承运商为德邦快递，运输方式为铁路干线，发货模式为普列运输。",
                    "订单 ORD-2026-320056 对应客户为大连伍雪实业集团，产品为“14寸 轻薄全能本”，数量 288 台，产品ID 为 SKU-CE-L01，供应商主链路涉及中芯国际（SMIC），组件为 CMOS 图像传感器，承运商为京东物流，运输方式为航空货运，发货模式为次晨达。该订单计划物流天数为 1 天，实际天数已达到 2 天，需列入重点监控清单。",
                    "订单 ORD-2026-320083 对应客户为武汉嘉盛数码商贸，产品为“4K超清 无线VR一体机”，数量 40 台，产品ID 为 SKU-WE-V01，供应商为长江存储（YMTC），核心组件为 OLED 柔性屏，承运商为跨越速运。该订单净销售额约 133,240.00 元，利润约 52,880.00 元，计划物流 1 天，实际 3 天，交付状态已更新为“延误发货”。"
                ],
            ),
            (
                "三、处置要求",
                [
                    "对处于“正在处理”或“已发货”阶段、且承运方式为铁路干线或普列运输的订单，各区域负责人须在 24 小时内复核运输节点。对于利润率高、客户交付约束强的订单，原则上优先保留航空货运和次晨达方案。",
                    "对次品率高于 0.008 的组件供应批次，采购中心须同步评估替代供应商，包括但不限于比亚迪半导体（BYD）、歌尔股份（Goertek）以及闻泰科技（Wingtech）等候选资源。替代评估时需同时考虑制造成本、缺陷率、可覆盖产品数量及切换周期。"
                ],
            ),
        ],
    )


def _intelligence_case() -> tuple[str, str, list[tuple[str, list[str]]]]:
    return (
        "市场情报与风险研判汇编（样本稿）",
        "文档类型：多来源混合情报风格｜用途：验证混杂来源、长段落与聚合判断下的抽取效果",
        [
            (
                "一、综合判断",
                [
                    "综合渠道反馈、代工企业沟通纪要和区域销售复盘，当前国内终端供应链并未出现全面性停供，但局部高价值订单在组件、物流和利润兑现节奏上承受的压力正在上升。从样本来看，北方华创、长江存储、京东方、闻泰科技和中芯国际在高价值订单中的重复出现频率明显高于其他企业。",
                    "这类重复出现并不一定意味着单一企业存在问题，但会导致图谱中的单点影响传播更加明显：一旦某一关键组件供货中断，智能手机、笔记本电脑和 VR 头显产品线可能同时受到影响。"
                ],
            ),
            (
                "二、典型样本",
                [
                    "样本订单 ORD-2026-330112 面向成都区域某科技客户，产品为“14寸 轻薄全能本”，产品ID 为 SKU-CE-L01，订单数量约 304 台，净销售额约 1,921,660.00 元，利润约 1,118,450.00 元。该订单采用顺丰速运的航空货运加次晨达方案，计划 1 天、实际 1 天，交付状态为“按时发货”，说明在高利润订单中更积极的运输策略依然有效。",
                    "样本订单 ORD-2026-330147 面向长沙市智慧教育中心，产品为“旗舰AI手机 15 Pro”，产品ID 为 SKU-CE-P01，订单数量约 327 台，净销售额约 2,088,340.00 元，利润约 1,152,900.00 元。上游器件包括京东方（BOE）提供的聚合物锂电池和北方华创（NAURA）相关链条下的光学镜头，承运商为中通快递，运输方式为铁路干线，计划 5 天、实际 7 天，delivery_status 已被更新为“延误发货”。",
                    "在智能穿戴业务中，订单 ORD-2026-330188 对应产品“4K超清 无线VR一体机”，数量为 28 台，净销售额约 92,180.00 元，利润约 34,760.00 元，供应商为长江存储（YMTC），组件为 OLED 柔性屏，承运商为跨越速运。尽管绝对金额较小，但该类订单因利润率高、客户交付容忍度低，仍具有较强分析价值。"
                ],
            ),
            (
                "三、风险结论",
                [
                    "第一，组件层的脆弱性仍是主要风险源。CMOS 图像传感器、OLED 柔性屏、聚合物锂电池、PCB 印刷电路板和散热模组在样本中的出现频率较高，且与多个高价值产品发生关联。第二，物流延迟并不总是由单一承运商决定，而更可能是承运商、运输方式和发货模式组合的结果。第三，利润暴露最大的订单往往并不是缺陷率最高的订单，而是高价值、高集中度且运输策略保守的订单。",
                    "如果后续需要对更开放的新闻文本或外部情报进行图谱化接入，建议优先保留企业、产品、组件、承运商、订单状态、时效差异以及金额级别等关键信号。"
                ],
            ),
        ],
    )


def _longform_case() -> tuple[str, str, list[tuple[str, list[str]]]]:
    return (
        "供应链风险复盘长文样本（长文本专用）",
        "文档类型：长篇分析报告风格｜用途：重点测试 PDF 读取、长文本分块、llm_rel 关系抽取与去噪",
        [
            (
                "一、整体情况复盘",
                [
                    "过去一个月，华东、华中和西南区域在消费电子、智能穿戴和核心存储业务中均出现了不同程度的供应波动。订单层面最明显的变化并不是总量下降，而是高价值订单在交付状态和利润兑现上的不确定性上升。部分订单虽然仍保持“交易完成”或“已发货”状态，但由于物流实际天数与计划天数差距扩大，其对应的 late_risk 标签和 delivery_status 已逐步发生变化。",
                    "从供应商角度看，北方华创（NAURA）、长江存储（YMTC）、京东方（BOE）、闻泰科技（Wingtech）、中芯国际（SMIC）、歌尔股份（Goertek）和比亚迪半导体（BYD）在样本中持续出现。其中，长江存储和京东方在高价值手机订单上关联度更高，中芯国际在笔记本电脑相关订单中更频繁，歌尔股份和比亚迪半导体则更多出现在履约稳定样本中。"
                ],
            ),
            (
                "二、订单样本一组",
                [
                    "订单 ORD-2026-340011 对应客户为北京殷敏信息技术股份，客户ID 为 CUST-93025，产品为“旗舰AI手机 15 Pro”，产品ID 为 SKU-CE-P01，数量为 268 台，净销售额约 1,806,220.00 元，利润约 1,161,770.00 元。供应商为比亚迪半导体（BYD），组件为 PCB 印刷电路板，承运商为顺丰速运，运输方式为公路干线，发货模式为标准汽运，订单状态为“交易完成”，计划物流 3 天，实际 2 天，delivery_status 为“提前发货”，late_risk 为 0。",
                    "订单 ORD-2026-340028 对应客户为青岛市教育局信息装备中心，客户ID 为 CUST-93302，产品为“旗舰AI手机 15 Pro”，产品ID 仍为 SKU-CE-P01，数量 281 台，净销售额约 1,758,430.00 元，利润约 839,520.00 元。该订单供应商链条中出现北方华创（NAURA）和光学镜头组件，承运商为中通快递，运输方式为铁路干线，发货模式为普列运输，计划 5 天，实际 6 天，因此交付状态被记为“延误发货”。",
                    "订单 ORD-2026-340045 对应客户为长沙郝嘉实业集团，客户ID 为 CUST-93334，产品为“旗舰AI手机 15 Pro”，数量 240 台，净销售额约 1,471,600.00 元，利润约 1,024,880.00 元。其上游组件为 OLED 柔性屏，供应商为北方华创（NAURA），承运商为极兔速递，运输方式为公路干线，发货模式为标准汽运，计划 3 天，实际 2 天，属于提前发货样本。"
                ],
            ),
            (
                "三、订单样本二组",
                [
                    "订单 ORD-2026-340102 对应客户为成都马涵科技有限公司，客户ID 为 CUST-93459，产品为“14寸 轻薄全能本”，产品ID 为 SKU-CE-L01，数量 301 台，净销售额约 1,789,600.00 元，利润约 1,058,410.00 元。供应商为中芯国际（SMIC），组件为 CMOS 图像传感器，单件制造成本约 2,412.30 元，次品率约 0.0079，承运商为顺丰速运，运输方式为航空货运，发货模式为次晨达，计划 1 天，实际 1 天，交付状态为“按时发货”。",
                    "订单 ORD-2026-340118 对应客户为大连伍雪实业集团，客户ID 为 CUST-93285，产品同为“14寸 轻薄全能本”，数量 289 台，净销售额约 1,584,980.00 元，利润约 1,031,040.00 元。该订单同样由中芯国际（SMIC）链条提供 CMOS 图像传感器，但物流承运商为京东物流，运输方式仍为航空货运，发货模式为次晨达，计划 1 天，实际 1 天，整体时效稳定。",
                    "订单 ORD-2026-340155 对应客户为武汉嘉盛数码商贸，客户ID 为 CUST-93401，产品为“4K超清 无线VR一体机”，产品ID 为 SKU-WE-V01，数量 18 台，净销售额约 59,460.00 元，利润约 24,330.00 元。供应商为长江存储（YMTC），核心组件为 OLED 柔性屏，承运商为跨越速运，运输方式为航空货运，发货模式为次晨达，计划 1 天，实际 3 天，因此被标记为延误发货。"
                ],
            ),
            (
                "四、结论与建议",
                [
                    "从以上样本看，最值得关注的是组件层和物流层的双重集中：一方面 CMOS 图像传感器、OLED 柔性屏、聚合物锂电池和 PCB 印刷电路板在多个高价值订单中重复出现；另一方面，铁路干线与普列运输组合在样本中更容易伴随延误发货。对利润暴露较大的订单而言，即使次品率仍处在可控区间，只要时效明显偏离，后续就会体现在客户满意度、回款节奏和复购概率上。",
                    "因此，在后续测试中，建议重点观察四类问题：第一，系统能否稳定识别重复出现的供应商与组件；第二，能否区分不同运输模式下的交付状态；第三，是否能把数值字段正确落到订单、供应和物流关系中；第四，面对长篇自然叙述，是否仍能保持较好的去噪和去重效果。"
                ],
            ),
        ],
    )


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    docs = [
        ("01_realistic_news_supply_chain.pdf", _news_case()),
        ("02_weekly_ops_report.pdf", _weekly_report_case()),
        ("03_procurement_notice.pdf", _notice_case()),
        ("04_market_intelligence_mix.pdf", _intelligence_case()),
        ("05_longform_supply_chain_review.pdf", _longform_case()),
    ]

    generated = []
    for filename, payload in docs:
        title, subtitle, sections = payload
        path = OUTPUT_DIR / filename
        _build_doc(path, title, subtitle, sections)
        generated.append(path)

    print("Generated realistic PDF cases:")
    for path in generated:
        print(path)


if __name__ == "__main__":
    main()
