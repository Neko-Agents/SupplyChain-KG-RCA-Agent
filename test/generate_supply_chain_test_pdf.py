import math
from pathlib import Path

import pandas as pd
from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = PROJECT_ROOT / "数据" / "Supply_Chain_Data_Fake.csv"
OUTPUT_PDF = PROJECT_ROOT / "test" / "supply_chain_natural_test_case.pdf"


def _load_dataset() -> pd.DataFrame:
    for encoding in ("gbk", "utf-8-sig", "utf-8"):
        try:
            return pd.read_csv(CSV_PATH, encoding=encoding)
        except Exception:
            continue
    raise RuntimeError(f"Unable to read dataset: {CSV_PATH}")


def _fmt_money(value: object) -> str:
    try:
        return f"{float(value):,.2f}"
    except Exception:
        return str(value)


def _fmt_float(value: object, digits: int = 4) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except Exception:
        return str(value)


def _select_rows(df: pd.DataFrame) -> pd.DataFrame:
    delayed = (
        df[df["发货延误风险_标签"] == 1]
        .sort_values("实付总金额", ascending=False)
        .head(6)
    )
    on_time = (
        df[df["发货延误风险_标签"] == 0]
        .sort_values("订单单笔利润", ascending=False)
        .head(6)
    )
    processing = df[df["订单状态"] == "正在处理"].head(4)
    selected = (
        pd.concat([delayed, on_time, processing], ignore_index=False)
        .drop_duplicates(subset=["订单ID"])
        .head(14)
        .copy()
    )
    return selected


def _build_styles():
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    styles = getSampleStyleSheet()

    styles.add(
        ParagraphStyle(
            name="ZhTitle",
            parent=styles["Title"],
            fontName="STSong-Light",
            fontSize=19,
            leading=26,
            alignment=TA_CENTER,
            textColor=HexColor("#17324d"),
        )
    )
    styles.add(
        ParagraphStyle(
            name="ZhHeading",
            parent=styles["Heading2"],
            fontName="STSong-Light",
            fontSize=13,
            leading=19,
            textColor=HexColor("#234c7a"),
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
            textColor=HexColor("#1f1f1f"),
            spaceAfter=6,
        )
    )
    styles.add(
        ParagraphStyle(
            name="ZhNote",
            parent=styles["BodyText"],
            fontName="STSong-Light",
            fontSize=9.5,
            leading=16,
            textColor=HexColor("#4f5d6b"),
            spaceAfter=5,
        )
    )
    return styles


def _summary_paragraphs(df: pd.DataFrame) -> list[str]:
    total_orders = len(df)
    total_amount = _fmt_money(df["实付总金额"].sum())
    total_profit = _fmt_money(df["订单单笔利润"].sum())
    delayed_count = int((df["发货延误风险_标签"] == 1).sum())
    top_suppliers = (
        df["供应商名称"].value_counts().head(5).to_dict()
    )

    supplier_text = "；".join(
        f"{name}涉及{count}笔样本订单" for name, count in top_suppliers.items()
    )

    return [
        (
            f"本测试文档依据数据集《Supply_Chain_Data_Fake.csv》中的真实字段结构改写而成，"
            f"共整理 {total_orders} 笔代表性订单，覆盖核心存储、消费电子、汽车电子与智能穿戴等部门。"
            f"样本订单的累计实付总金额约为 {total_amount} 元，累计订单利润约为 {total_profit} 元，"
            f"其中存在明确延误风险标签的订单共有 {delayed_count} 笔。"
        ),
        (
            "文档采用“业务叙述 + 事实摘录”的混合写法。前半部分尽量贴近采购周报、交付复盘和客户沟通纪要的自然语言风格，"
            "后半部分保留了关键字段，便于测试模板抽取、混合抽取和关系抽取在较长文本中的表现。"
        ),
        (
            f"从当前样本观察，供应商分布相对分散，但仍能看到局部集中现象：{supplier_text}。"
            "如果后续需要测试更复杂的图谱更新场景，可以继续在同一份材料里补充召回、替代料、品质异常和承运商切换等内容。"
        ),
    ]


def _build_order_section(row: pd.Series, index: int) -> list[str]:
    order_id = row["订单ID"]
    customer = row["客户姓名"]
    customer_id = row["客户ID"]
    product = row["产品名称"]
    product_id = row["产品ID"]
    supplier = row["供应商名称"]
    component = row["核心组件名称"]
    carrier = row["承运商名称"]
    transport = row["运输方式"]
    ship_mode = row["发货模式"]
    order_status = row["订单状态"]
    delivery_status = row["物流运送状态"]
    delay_flag = int(row["发货延误风险_标签"])
    quantity = int(row["购买数量"])
    amount = _fmt_money(row["实付总金额"])
    profit = _fmt_money(row["订单单笔利润"])
    defect_rate = _fmt_float(row["次品率"], 5)
    mfg_cost = _fmt_money(row["单件制造成本"])
    scheduled_days = int(row["计划物流天数"])
    actual_days = int(row["实际物流天数"])
    department = row["部门名称"]
    category = row["类别名称"]
    city = row["客户城市"]
    province = row["客户省份"]
    payment_type = row["支付类型"]

    risk_sentence = (
        f"由于该订单被标记为延误风险订单，计划物流天数为 {scheduled_days} 天，但实际物流天数达到 {actual_days} 天，"
        f"因此在交付承诺和客户沟通节奏上需要重点关注。"
        if delay_flag
        else f"该订单当前未触发延误风险标签，计划物流天数与实际物流天数分别为 {scheduled_days} 天和 {actual_days} 天，"
        "整体交付节奏相对稳定。"
    )

    return [
        (
            f"样本案例 {index} 围绕订单 {order_id} 展开。客户 {customer}（客户ID：{customer_id}）来自 {province}{city}，"
            f"本次采购的是 {department} 线下的 {category} 产品“{product}”（产品ID：{product_id}）。"
            f"从业务角度看，这类订单通常会同时受到上游核心器件供给、承运商时效以及订单促销策略的共同影响。"
        ),
        (
            f"该订单由供应商 {supplier} 提供关键部件“{component}”，单件制造成本约为 {mfg_cost} 元，"
            f"当前记录到的次品率约为 {defect_rate}。订单购买数量为 {quantity} 件，实付总金额为 {amount} 元，"
            f"对应订单利润为 {profit} 元。付款方式为 {payment_type}，订单状态显示为“{order_status}”。"
        ),
        (
            f"物流侧由 {carrier} 承运，运输方式为 {transport}，发货模式为 {ship_mode}，当前物流状态为“{delivery_status}”。"
            f"{risk_sentence}"
        ),
        (
            f"事实摘录：订单ID：{order_id}；客户ID：{customer_id}；客户姓名：{customer}；部门名称：{department}；"
            f"类别名称：{category}；产品ID：{product_id}；产品名称：{product}；供应商名称：{supplier}；"
            f"核心组件名称：{component}；承运商名称：{carrier}；运输方式：{transport}；发货模式：{ship_mode}；"
            f"订单状态：{order_status}；发货延误风险_标签：{delay_flag}；物流运送状态：{delivery_status}；"
            f"购买数量：{quantity}；实付总金额：{amount}；订单单笔利润：{profit}。"
        ),
    ]


def _build_supplier_observations(df: pd.DataFrame) -> list[str]:
    supplier_stats = (
        df.groupby("供应商名称")
        .agg(
            订单数=("订单ID", "count"),
            累计实付=("实付总金额", "sum"),
            累计利润=("订单单笔利润", "sum"),
            平均次品率=("次品率", "mean"),
        )
        .sort_values("累计实付", ascending=False)
        .head(6)
        .reset_index()
    )

    paragraphs: list[str] = []
    for _, row in supplier_stats.iterrows():
        paragraphs.append(
            (
                f"供应商观察：{row['供应商名称']} 在当前样本中关联 {int(row['订单数'])} 笔订单，"
                f"累计实付金额约 {_fmt_money(row['累计实付'])} 元，累计利润约 {_fmt_money(row['累计利润'])} 元，"
                f"平均次品率约 {_fmt_float(row['平均次品率'], 5)}。这类描述适合用来测试实体聚合、供应商画像和风险排序相关的抽取链路。"
            )
        )
    return paragraphs


def _build_appendix(df: pd.DataFrame) -> list[str]:
    top_product = df["产品名称"].value_counts().idxmax()
    top_carrier = df["承运商名称"].value_counts().idxmax()
    delayed_rows = df[df["发货延误风险_标签"] == 1]["订单ID"].tolist()
    delayed_text = "、".join(delayed_rows[:8])
    avg_margin = (
        df["订单单笔利润"].sum() / df["实付总金额"].sum()
        if df["实付总金额"].sum()
        else 0
    )

    return [
        (
            f"附录说明一：如果把整份文档视作一次供应链复盘材料，那么“{top_product}”是出现频率最高的产品，"
            f"“{top_carrier}”是出现频率最高的承运商。模型在抽取时既要能识别结构化字段，也要能理解这些实体在自然语言中的多次复现。"
        ),
        (
            f"附录说明二：当前样本中，带有延误风险标签的订单包括 {delayed_text} 等。"
            "这些订单通常会在交付节点、客户满意度和利润兑现节奏上形成连锁影响，因此很适合做关系抽取与多跳问答测试。"
        ),
        (
            f"附录说明三：如果按样本整体粗略计算，订单利润与实付金额的比值约为 {_fmt_float(avg_margin, 4)}。"
            "在后续测试中，可以让模型围绕“高利润但高延误风险订单”“供应商集中度较高的关键产品”“承运商与交付时效之间的关联”这几类问题做进一步抽取。"
        ),
    ]


def build_pdf(output_path: Path = OUTPUT_PDF) -> Path:
    df = _load_dataset()
    selected = _select_rows(df)
    styles = _build_styles()

    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
    )

    story = []
    story.append(Paragraph("供应链自然语言测试样本（PDF）", styles["ZhTitle"]))
    story.append(Spacer(1, 5 * mm))
    story.append(
        Paragraph(
            "用途：用于测试 PDF 文本抽取、结构化字段识别、关系抽取与知识图谱预览，不直接写入图数据库。",
            styles["ZhNote"],
        )
    )
    story.append(Spacer(1, 3 * mm))

    story.append(Paragraph("一、文档概览", styles["ZhHeading"]))
    for text in _summary_paragraphs(selected):
        story.append(Paragraph(text, styles["ZhBody"]))

    story.append(Spacer(1, 2 * mm))
    story.append(Paragraph("二、订单案例叙述", styles["ZhHeading"]))
    for idx, (_, row) in enumerate(selected.iterrows(), start=1):
        story.append(Paragraph(f"2.{idx} 订单案例 {idx}", styles["ZhHeading"]))
        for text in _build_order_section(row, idx):
            story.append(Paragraph(text, styles["ZhBody"]))
        story.append(Spacer(1, 2 * mm))
        if idx in {4, 8, 12}:
            story.append(PageBreak())

    story.append(Paragraph("三、供应商聚合观察", styles["ZhHeading"]))
    for text in _build_supplier_observations(selected):
        story.append(Paragraph(text, styles["ZhBody"]))

    story.append(Spacer(1, 2 * mm))
    story.append(Paragraph("四、补充说明与抽取提示", styles["ZhHeading"]))
    for text in _build_appendix(selected):
        story.append(Paragraph(text, styles["ZhBody"]))

    story.append(Spacer(1, 3 * mm))
    story.append(
        Paragraph(
            f"文档尾注：本文件由脚本自动生成，源数据文件为 {CSV_PATH.name}，输出页数会随样本内容略有变化。",
            styles["ZhNote"],
        )
    )

    doc.build(story)
    return output_path


if __name__ == "__main__":
    path = build_pdf()
    print(path)
