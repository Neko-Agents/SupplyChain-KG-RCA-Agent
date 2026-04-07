import argparse
import random
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd


CSV_COLUMNS = [
    "订单ID",
    "订单日期",
    "发货日期",
    "预计送达日期",
    "实际送达日期",
    "支付类型",
    "订单状态",
    "销售总额_应付",
    "订单单笔利润",
    "客户ID",
    "客户姓名",
    "客户邮箱",
    "客户群体",
    "客户国家",
    "客户省份",
    "客户城市",
    "客户街道",
    "客户纬度",
    "客户经度",
    "部门名称",
    "类别名称",
    "产品ID",
    "产品SKU",
    "产品名称",
    "产品描述",
    "产品基础价格",
    "购买数量",
    "折扣率",
    "折扣金额",
    "实付总金额",
    "利润率",
    "供应商名称",
    "供应商城市",
    "核心组件名称",
    "单件制造成本",
    "次品率",
    "承运商名称",
    "运输方式",
    "发货模式",
    "计划物流天数",
    "实际物流天数",
    "发货延误风险_标签",
    "物流运送状态",
]


def read_csv_fallback(path: Path) -> pd.DataFrame:
    try:
        return pd.read_csv(path, encoding="utf-8-sig")
    except UnicodeDecodeError:
        return pd.read_csv(path, encoding="gbk")


def build_default_universe(seed: int) -> dict:
    surnames = [
        "赵", "钱", "孙", "李", "周", "吴", "郑", "王", "冯", "陈", "褚", "卫", "蒋", "沈",
        "韩", "杨", "朱", "秦", "尤", "许", "何", "吕", "施", "张", "孔", "曹", "严", "华",
        "金", "魏", "陶", "姜", "戚", "谢", "邹", "喻", "柏", "水", "窦", "章", "云", "苏",
        "潘", "葛", "奚", "范", "彭", "郎", "鲁", "韦", "昌", "马", "苗", "凤", "花", "方",
        "俞", "任", "袁", "柳", "酆", "鲍", "史", "唐", "费", "廉", "岑", "薛", "雷", "贺",
        "倪", "汤", "滕", "殷",
    ]
    given_names = [
        "伟", "芳", "娜", "敏", "静", "强", "磊", "军", "洋", "勇", "艳", "杰", "娟", "涛",
        "明", "超", "秀", "霞", "平", "刚", "桂", "英", "丹", "萍", "凯", "琳", "飞", "晶",
        "鹏", "博", "颖", "鑫", "恒", "晨", "雪", "怡", "欣", "瑞", "宇", "凡", "豪", "璇",
        "琪", "璐", "龙", "峰", "宁", "璟", "坤", "媛",
    ]
    corp_names = [
        "科技有限公司", "实业集团", "商贸有限公司", "信息技术股份", "电子科技公司", "智能装备公司",
    ]
    gov_names = ["教育局", "卫生局", "交通厅", "税务局", "市政工程处", "科研所"]
    cities = [
        ("北京", 39.90, 116.40), ("上海", 31.23, 121.47), ("深圳", 22.54, 114.05), ("广州", 23.12, 113.26),
        ("成都", 30.57, 104.06), ("杭州", 30.27, 120.15), ("南京", 32.06, 118.80), ("武汉", 30.59, 114.31),
        ("西安", 34.34, 108.94), ("苏州", 31.30, 120.62), ("重庆", 29.56, 106.55), ("天津", 39.08, 117.20),
        ("郑州", 34.75, 113.62), ("长沙", 28.23, 112.93), ("东莞", 23.02, 113.75), ("青岛", 36.07, 120.38),
        ("沈阳", 41.81, 123.43), ("宁波", 29.86, 121.56), ("昆明", 25.04, 102.71), ("大连", 38.91, 121.61),
    ]
    street_prefixes = ["高新", "科技", "朝阳", "浦东", "滨海", "长安", "人民", "中山", "建设", "光明", "幸福", "振兴"]
    street_suffixes = ["路", "街", "大道", "巷", "南路", "北路", "东路", "西路"]
    product_catalog = {
        "核心存储": [
            {"cat": "固态硬盘 (SSD)", "sku": "SKU-ST-S01", "name": "2TB PCIe 4.0 旗舰固态", "price": 1299.0, "desc": "采用最新3D NAND技术"},
            {"cat": "内存条 (DRAM)", "sku": "SKU-ST-M01", "name": "16GB DDR5 6000MHz", "price": 799.0, "desc": "电竞级RGB高频内存"},
        ],
        "消费电子": [
            {"cat": "智能手机", "sku": "SKU-CE-P01", "name": "旗舰AI手机 15 Pro", "price": 6999.0, "desc": "搭载最新AI大模型"},
            {"cat": "笔记本电脑", "sku": "SKU-CE-L01", "name": "14寸 轻薄全能本", "price": 5999.0, "desc": "标压处理器+高分屏"},
        ],
        "汽车电子": [
            {"cat": "车载微控制器 (MCU)", "sku": "SKU-AE-M01", "name": "32位 车规级控制芯片", "price": 125.0, "desc": "AEC-Q100标准"},
            {"cat": "智能座舱", "sku": "SKU-AE-C01", "name": "15.6寸 中控娱乐屏", "price": 1800.0, "desc": "OLED车规级显示"},
        ],
        "智能穿戴": [
            {"cat": "智能手表", "sku": "SKU-WE-W01", "name": "全天候健康监测手表", "price": 1299.0, "desc": "心电与血氧监测"},
            {"cat": "VR头显", "sku": "SKU-WE-V01", "name": "4K超清 无线VR一体机", "price": 2999.0, "desc": "沉浸式元宇宙"},
        ],
    }
    supplier_info = {
        "长江存储 (YMTC)": "武汉",
        "中芯国际 (SMIC)": "上海",
        "京东方 (BOE)": "合肥",
        "歌尔股份 (Goertek)": "潍坊",
        "华为海思 (HiSilicon)": "深圳",
        "紫光展锐 (UNISOC)": "上海",
        "比亚迪半导体 (BYD)": "深圳",
        "立讯精密 (Luxshare)": "东莞",
        "闻泰科技 (Wingtech)": "嘉兴",
        "韦尔股份 (Willsemi)": "上海",
        "北方华创 (NAURA)": "北京",
    }
    components = ["硅晶圆", "闪存颗粒", "主控芯片", "OLED柔性屏", "聚合物锂电池", "光学镜头", "CMOS图像传感器", "PCB印刷电路板", "散热模组"]
    carriers = ["顺丰速运", "京东物流", "跨越速运", "中通快递", "圆通速递", "极兔速递", "德邦快递", "邮政EMS"]

    customers = {}
    for idx in range(1, 1001):
      rng = random.Random(seed * 10000 + idx)
      city, lat, lon = rng.choice(cities)
      street = f"{rng.choice(street_prefixes)}{rng.randint(1, 999)}{rng.choice(street_suffixes)}"
      segment = rng.choices(["个人消费者", "企业采购", "政府机构"], weights=[0.65, 0.25, 0.10])[0]
      if segment == "个人消费者":
        name = rng.choice(surnames) + "".join(rng.choices(given_names, k=rng.choice([1, 2])))
      elif segment == "企业采购":
        name = city + rng.choice(surnames) + rng.choice(given_names) + rng.choice(corp_names)
      else:
        name = city + "市" + rng.choice(gov_names)
      customer_id = f"CUST-{80000 + idx}"
      customers[customer_id] = {
          "id": customer_id,
          "name": name,
          "email": f"user_{80000 + idx}@{'163.com' if segment == '个人消费者' else 'corp.com'}",
          "segment": segment,
          "country": "中国",
          "province": city + "市",
          "city": city,
          "street": street,
          "lat": round(lat + rng.uniform(-0.08, 0.08), 5),
          "lon": round(lon + rng.uniform(-0.08, 0.08), 5),
      }

    products = []
    for dept, items in product_catalog.items():
        for item in items:
            products.append(
                {
                    "department": dept,
                    "category": item["cat"],
                    "sku": item["sku"],
                    "product_id": item["sku"],
                    "name": item["name"],
                    "desc": item["desc"],
                    "price": item["price"],
                }
            )

    suppliers = [{"name": name, "city": city} for name, city in supplier_info.items()]

    return {
        "customers": customers,
        "products": products,
        "suppliers": suppliers,
        "components": components,
        "carriers": carriers,
    }


def build_universe_from_existing(base_df: pd.DataFrame, seed: int) -> dict:
    universe = build_default_universe(seed)

    if not base_df.empty:
        customers = {}
        for _, row in base_df.iterrows():
            customer_id = str(row.get("客户ID", "")).strip()
            if not customer_id:
                continue
            customers[customer_id] = {
                "id": customer_id,
                "name": str(row.get("客户姓名", "")).strip(),
                "email": str(row.get("客户邮箱", "")).strip(),
                "segment": str(row.get("客户群体", "")).strip() or "个人消费者",
                "country": str(row.get("客户国家", "")).strip() or "中国",
                "province": str(row.get("客户省份", "")).strip(),
                "city": str(row.get("客户城市", "")).strip(),
                "street": str(row.get("客户街道", "")).strip(),
                "lat": float(row.get("客户纬度", 0) or 0),
                "lon": float(row.get("客户经度", 0) or 0),
            }
        if customers:
            universe["customers"] = customers

        products = []
        seen_skus = set()
        for _, row in base_df.iterrows():
            sku = str(row.get("产品SKU", "")).strip()
            if not sku or sku in seen_skus:
                continue
            seen_skus.add(sku)
            products.append(
                {
                    "department": str(row.get("部门名称", "")).strip(),
                    "category": str(row.get("类别名称", "")).strip(),
                    "sku": sku,
                    "product_id": str(row.get("产品ID", "")).strip() or sku,
                    "name": str(row.get("产品名称", "")).strip(),
                    "desc": str(row.get("产品描述", "")).strip(),
                    "price": float(row.get("产品基础价格", 0) or 0),
                }
            )
        if products:
            universe["products"] = products

        suppliers = []
        seen_suppliers = set()
        for _, row in base_df.iterrows():
            name = str(row.get("供应商名称", "")).strip()
            if not name or name in seen_suppliers:
                continue
            seen_suppliers.add(name)
            suppliers.append(
                {
                    "name": name,
                    "city": str(row.get("供应商城市", "")).strip(),
                }
            )
        if suppliers:
            universe["suppliers"] = suppliers

        components = sorted(
            {
                str(value).strip()
                for value in base_df.get("核心组件名称", pd.Series(dtype=str)).tolist()
                if str(value).strip()
            }
        )
        carriers = sorted(
            {
                str(value).strip()
                for value in base_df.get("承运商名称", pd.Series(dtype=str)).tolist()
                if str(value).strip()
            }
        )
        if components:
            universe["components"] = components
        if carriers:
            universe["carriers"] = carriers

    return universe


def parse_max_order_seq(df: pd.DataFrame) -> int:
    max_seq = 99999
    if "订单ID" not in df.columns:
        return max_seq
    for value in df["订单ID"].dropna().astype(str):
        if value.startswith("ORD-2024-"):
            try:
                max_seq = max(max_seq, int(value.split("-")[-1]))
            except ValueError:
                continue
    return max_seq


def build_record(universe: dict, row_index: int, order_seq: int, seed: int) -> dict:
    rng = random.Random(seed * 100000 + row_index)
    customers = list(universe["customers"].values())
    products = universe["products"]
    suppliers = universe["suppliers"]
    components = universe["components"]
    carriers = universe["carriers"]

    customer = rng.choice(customers)
    product = rng.choice(products)
    supplier = rng.choice(suppliers)
    component = rng.choice(components)
    carrier = rng.choice(carriers)

    qty = rng.randint(1, 3) if customer["segment"] == "个人消费者" else rng.randint(20, 300)
    discount_rate = round(rng.uniform(0, 0.15), 2)
    sales_total = round(product["price"] * qty, 2)
    discount_amount = round(sales_total * discount_rate, 2)
    net_total = round(sales_total - discount_amount, 2)
    mfg_cost = round(product["price"] * rng.uniform(0.20, 0.55), 2)
    profit = round(net_total - (mfg_cost * qty), 2)
    profit_ratio = round(profit / net_total, 4) if net_total > 0 else 0
    defect_rate = round(rng.uniform(0.0005, 0.012), 5)

    base_date = datetime(2023, 6, 1)
    order_date = base_date + timedelta(days=rng.randint(0, 500), hours=rng.randint(0, 23), minutes=rng.randint(0, 59))
    shipping_date = order_date + timedelta(hours=rng.randint(2, 48))

    if carrier in ["顺丰速运", "跨越速运", "京东物流"]:
        trans_mode = rng.choices(["航空货运", "公路干线"], weights=[0.6, 0.4])[0]
    elif carrier in ["极兔速递", "圆通速递"]:
        trans_mode = "公路干线"
    else:
        trans_mode = rng.choice(["公路干线", "铁路干线"])

    if trans_mode == "航空货运":
        ship_mode = "次晨达"
        days_scheduled = 1
    elif trans_mode == "公路干线":
        ship_mode = "标准汽运"
        days_scheduled = 3
    else:
        ship_mode = "普列运输"
        days_scheduled = 5

    if carrier == "跨越速运" and trans_mode == "公路干线":
        days_real = days_scheduled + rng.randint(2, 6)
    else:
        days_real = max(1, days_scheduled + rng.randint(-1, 2))

    late_risk = 1 if days_real > days_scheduled else 0
    if late_risk == 1:
        delivery_status = "延误发货"
    elif days_real < days_scheduled:
        delivery_status = "提前发货"
    else:
        delivery_status = "按时发货"

    scheduled_delivery_date = shipping_date + timedelta(days=days_scheduled)
    actual_delivery_date = shipping_date + timedelta(days=days_real)

    return {
        "订单ID": f"ORD-2024-{order_seq}",
        "订单日期": order_date.strftime("%Y-%m-%d %H:%M:%S"),
        "发货日期": shipping_date.strftime("%Y-%m-%d %H:%M:%S"),
        "预计送达日期": scheduled_delivery_date.strftime("%Y-%m-%d %H:%M:%S"),
        "实际送达日期": actual_delivery_date.strftime("%Y-%m-%d %H:%M:%S"),
        "支付类型": rng.choice(["微信支付", "支付宝", "企业对公转账", "信用卡"]),
        "订单状态": rng.choices(["交易完成", "正在处理", "已发货", "已退款"], weights=[0.7, 0.1, 0.15, 0.05])[0],
        "销售总额_应付": sales_total,
        "订单单笔利润": profit,
        "客户ID": customer["id"],
        "客户姓名": customer["name"],
        "客户邮箱": customer["email"],
        "客户群体": customer["segment"],
        "客户国家": customer["country"],
        "客户省份": customer["province"],
        "客户城市": customer["city"],
        "客户街道": customer["street"],
        "客户纬度": customer["lat"],
        "客户经度": customer["lon"],
        "部门名称": product["department"],
        "类别名称": product["category"],
        "产品ID": product["product_id"],
        "产品SKU": product["sku"],
        "产品名称": product["name"],
        "产品描述": product["desc"],
        "产品基础价格": product["price"],
        "购买数量": qty,
        "折扣率": discount_rate,
        "折扣金额": discount_amount,
        "实付总金额": net_total,
        "利润率": profit_ratio,
        "供应商名称": supplier["name"],
        "供应商城市": supplier["city"],
        "核心组件名称": component,
        "单件制造成本": mfg_cost,
        "次品率": defect_rate,
        "承运商名称": carrier,
        "运输方式": trans_mode,
        "发货模式": ship_mode,
        "计划物流天数": days_scheduled,
        "实际物流天数": days_real,
        "发货延误风险_标签": late_risk,
        "物流运送状态": delivery_status,
    }


def generate_perfect_supply_chain(
    num_records: int = 3000,
    seed: int = 42,
    output: str = "Perfect_Supply_Chain_Data.csv",
    base_csv: str | None = None,
    append: bool = False,
) -> pd.DataFrame:
    output_path = Path(output)
    base_path = Path(base_csv) if base_csv else None

    existing_df = pd.DataFrame(columns=CSV_COLUMNS)
    if append and output_path.exists():
        existing_df = read_csv_fallback(output_path)
        if base_path is None:
            base_path = output_path
    elif base_path and base_path.exists():
        existing_df = read_csv_fallback(base_path)

    if not existing_df.empty:
        universe = build_universe_from_existing(existing_df, seed)
        start_seq = parse_max_order_seq(existing_df) + 1
        print(f"Using existing universe from {base_path or output_path}, next order sequence starts at {start_seq}.")
    else:
        universe = build_default_universe(seed)
        start_seq = 100000
        print(f"Using deterministic default universe with seed={seed}.")

    rows = []
    for offset in range(num_records):
        rows.append(build_record(universe, offset, start_seq + offset, seed))

    new_df = pd.DataFrame(rows, columns=CSV_COLUMNS)

    if append and not existing_df.empty:
        final_df = pd.concat([existing_df, new_df], ignore_index=True)
        final_df = final_df.drop_duplicates(subset=["订单ID"], keep="first")
    else:
        final_df = new_df

    final_df.to_csv(output_path, index=False, encoding="utf-8-sig")
    print(f"Saved {len(new_df)} new rows to {output_path}. Total rows in file: {len(final_df)}")
    return final_df


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate deterministic or append-only supply chain demo data.")
    parser.add_argument("--num-records", type=int, default=3000, help="Number of new rows to generate.")
    parser.add_argument("--seed", type=int, default=42, help="Seed used for deterministic generation.")
    parser.add_argument("--output", default="Perfect_Supply_Chain_Data.csv", help="Output CSV path.")
    parser.add_argument(
        "--base-csv",
        default="",
        help="Existing CSV used as the base universe. Useful when you want new rows to follow an older dataset style.",
    )
    parser.add_argument(
        "--append",
        action="store_true",
        help="Append new rows to the output file and continue order ids.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    generate_perfect_supply_chain(
        num_records=max(1, args.num_records),
        seed=args.seed,
        output=args.output,
        base_csv=args.base_csv or None,
        append=args.append,
    )
