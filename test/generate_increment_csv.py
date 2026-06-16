import argparse
import csv
import random
from datetime import datetime, timedelta
from pathlib import Path


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


PRODUCTS = [
    {
        "department": "消费电子",
        "category": "智能手机",
        "product_id": "SKU-CE-P01",
        "sku": "SKU-CE-P01",
        "name": "旗舰AI手机 15 Pro",
        "desc": "搭载最新AI大模型",
        "base_price": 6999.0,
        "suppliers": [
            ("京东方 (BOE)", "合肥", "OLED柔性屏", 3125.80, 0.00410),
            ("北方华创 (NAURA)", "北京", "光学镜头", 1896.40, 0.00390),
            ("长江存储 (YMTC)", "武汉", "主控芯片", 1420.60, 0.00480),
        ],
    },
    {
        "department": "消费电子",
        "category": "笔记本电脑",
        "product_id": "SKU-CE-L01",
        "sku": "SKU-CE-L01",
        "name": "14寸 轻薄全能本",
        "desc": "标压处理器+高分屏",
        "base_price": 5999.0,
        "suppliers": [
            ("中芯国际 (SMIC)", "上海", "CMOS图像传感器", 2284.60, 0.00620),
            ("立讯精密 (Luxshare)", "东莞", "散热模组", 2510.15, 0.00560),
        ],
    },
    {
        "department": "智能穿戴",
        "category": "智能手表",
        "product_id": "SKU-WE-W01",
        "sku": "SKU-WE-W01",
        "name": "全天候健康监测手表",
        "desc": "心电与血氧监测",
        "base_price": 1299.0,
        "suppliers": [
            ("歌尔股份 (Goertek)", "潍坊", "PCB印刷电路板", 389.70, 0.00650),
            ("华为海思 (HiSilicon)", "深圳", "CMOS图像传感器", 521.48, 0.00220),
            ("紫光展锐 (UNISOC)", "上海", "PCB印刷电路板", 381.55, 0.00861),
        ],
    },
    {
        "department": "智能穿戴",
        "category": "VR头显",
        "product_id": "SKU-WE-V01",
        "sku": "SKU-WE-V01",
        "name": "4K超清 无线VR一体机",
        "desc": "沉浸式元宇宙",
        "base_price": 3999.0,
        "suppliers": [
            ("长江存储 (YMTC)", "武汉", "OLED柔性屏", 1188.20, 0.00210),
            ("京东方 (BOE)", "合肥", "主控芯片", 1295.20, 0.00194),
        ],
    },
    {
        "department": "核心存储",
        "category": "固态硬盘 (SSD)",
        "product_id": "SKU-ST-S01",
        "sku": "SKU-ST-S01",
        "name": "2TB PCIe 4.0 旗舰固态",
        "desc": "采用最新3D NAND技术",
        "base_price": 1299.0,
        "suppliers": [
            ("长江存储 (YMTC)", "武汉", "主控芯片", 462.30, 0.00480),
            ("歌尔股份 (Goertek)", "潍坊", "主控芯片", 638.24, 0.00402),
        ],
    },
    {
        "department": "核心存储",
        "category": "内存条 (DRAM)",
        "product_id": "SKU-ST-M01",
        "sku": "SKU-ST-M01",
        "name": "16GB DDR5 6000MHz",
        "desc": "电竞级RGB高频内存",
        "base_price": 799.0,
        "suppliers": [
            ("紫光展锐 (UNISOC)", "上海", "主控芯片", 352.10, 0.00380),
            ("韦尔股份 (Willsemi)", "上海", "主控芯片", 362.80, 0.00310),
        ],
    },
    {
        "department": "汽车电子",
        "category": "智能座舱",
        "product_id": "SKU-AE-C01",
        "sku": "SKU-AE-C01",
        "name": "15.6寸 中控娱乐屏",
        "desc": "OLED车规级显示",
        "base_price": 1800.0,
        "suppliers": [
            ("闻泰科技 (Wingtech)", "嘉兴", "CMOS图像传感器", 812.50, 0.00440),
            ("比亚迪半导体 (BYD)", "深圳", "光学镜头", 832.33, 0.00411),
            ("立讯精密 (Luxshare)", "东莞", "光学镜头", 785.30, 0.00490),
        ],
    },
    {
        "department": "汽车电子",
        "category": "车载微控制器 (MCU)",
        "product_id": "SKU-AE-M01",
        "sku": "SKU-AE-M01",
        "name": "32位 车规级控制芯片",
        "desc": "AEC-Q100标准",
        "base_price": 125.0,
        "suppliers": [
            ("华为海思 (HiSilicon)", "深圳", "主控芯片", 37.60, 0.00530),
            ("比亚迪半导体 (BYD)", "深圳", "PCB印刷电路板", 41.85, 0.00470),
            ("北方华创 (NAURA)", "北京", "散热模组", 36.70, 0.00540),
        ],
    },
]


LOCATIONS = [
    ("北京市", "北京", 39.9042, 116.4074),
    ("上海市", "上海", 31.2304, 121.4737),
    ("广东省", "深圳", 22.5431, 114.0579),
    ("广东省", "广州", 23.1291, 113.2644),
    ("四川省", "成都", 30.5728, 104.0668),
    ("湖北省", "武汉", 30.5928, 114.3055),
    ("江苏省", "苏州", 31.2989, 120.5853),
    ("江苏省", "南京", 32.0603, 118.7969),
    ("湖南省", "长沙", 28.2282, 112.9388),
    ("浙江省", "杭州", 30.2741, 120.1551),
    ("山东省", "青岛", 36.0671, 120.3826),
    ("天津市", "天津", 39.0842, 117.2009),
    ("陕西省", "西安", 34.3416, 108.9398),
    ("辽宁省", "沈阳", 41.8057, 123.4315),
    ("福建省", "福州", 26.0745, 119.2965),
    ("河南省", "郑州", 34.7466, 113.6254),
    ("重庆市", "重庆", 29.5630, 106.5516),
]


STREETS = [
    "高新路",
    "人民路",
    "建设路",
    "振兴大道",
    "光明北路",
    "长安路",
    "朝阳街",
    "科技大道",
    "和平路",
    "滨海南路",
    "星湖街",
    "网商路",
]


CARRIERS = {
    "航空": [
        ("顺丰速运", "航空货运", "次晨达", 1),
        ("跨越速运", "航空货运", "次晨达", 1),
    ],
    "公路": [
        ("京东物流", "公路干线", "标准汽运", 3),
        ("中通快递", "公路干线", "标准汽运", 3),
        ("德邦快递", "公路干线", "标准汽运", 3),
        ("圆通速递", "公路干线", "标准汽运", 3),
    ],
    "铁路": [
        ("邮政EMS", "铁路干线", "普列运输", 5),
        ("中通快递", "铁路干线", "普列运输", 5),
        ("德邦快递", "铁路干线", "普列运输", 5),
    ],
}


PAYMENTS = ["企业对公转账", "微信支付", "支付宝", "信用卡"]
SEGMENTS = ["企业采购", "个人消费者", "政府机构"]
SURNAMES = list(
    "赵钱孙李周吴郑王冯陈蒋沈韩杨许何施张严金魏陶姜谢邹华秦吕褚卫朱于余伍顾孟黄林唐方任袁柳罗毕郝安常乐时傅齐康俞平祝董梁杜阮蓝闵季麻强贾路江童颜郭梅盛钟徐邱高夏蔡田樊胡凌霍虞万卢莫房解应宗丁宣邓洪包左石崔程陆荣羊惠曲封储焦牧山尹姚邵汪祁毛禹熊纪舒屈项"
)
GIVEN_NAMES = [
    "子墨",
    "晨轩",
    "雨宁",
    "思远",
    "清扬",
    "若彤",
    "浩然",
    "语桐",
    "亦凡",
    "嘉禾",
    "明轩",
    "锦程",
    "安然",
    "彦霖",
    "可欣",
    "嘉懿",
    "浩宇",
    "诗涵",
    "书瑶",
    "霄",
    "晨",
    "言",
    "瑞",
    "涵",
    "昊",
    "萱",
    "柠",
    "宁",
    "悦",
    "帆",
]
COMPANY_TOKENS = [
    "智景",
    "科锐",
    "澜芯",
    "海越",
    "数创",
    "星河",
    "启睿",
    "智航",
    "辰远",
    "睿达",
    "智联",
    "海川",
    "嘉盛",
    "景曜",
    "云启",
    "锐思",
    "浩维",
    "远图",
    "星屿",
    "云途",
]
COMPANY_SUFFIXES = [
    "信息技术有限公司",
    "教育装备有限公司",
    "数字科技有限公司",
    "汽车电子有限公司",
    "商贸有限公司",
    "工业控制有限公司",
    "智能装备公司",
    "医疗设备有限公司",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate realistic incremental CSV data.")
    parser.add_argument("--rows", type=int, default=200, help="How many rows to generate.")
    parser.add_argument(
        "--output",
        default="test/test_increment.csv",
        help="Output CSV path. Default: test/test_increment.csv",
    )
    parser.add_argument(
        "--start-order-seq",
        type=int,
        default=103001,
        help="Starting order numeric suffix, e.g. 103001 -> ORD-2024-103001.",
    )
    parser.add_argument(
        "--start-customer-seq",
        type=int,
        default=82001,
        help="Starting customer numeric suffix, e.g. 82001 -> CUST-82001.",
    )
    parser.add_argument("--seed", type=int, default=20260416, help="Random seed.")
    return parser.parse_args()


def make_customer_name(segment: str, city: str, index: int, rng: random.Random) -> tuple[str, str]:
    if segment == "政府机构":
        name = f"{city}市教育信息中心" if index % 2 else f"{city}市科研所"
        email = f"user_{index}@gov.cn"
    elif segment == "企业采购":
        token = COMPANY_TOKENS[index % len(COMPANY_TOKENS)]
        suffix = COMPANY_SUFFIXES[index % len(COMPANY_SUFFIXES)]
        name = f"{city}{token}{suffix}"
        email = f"user_{index}@corp.com"
    else:
        name = random.choice(SURNAMES) + random.choice(GIVEN_NAMES)
        email = f"user_{index}@163.com"
    return name, email


def quantity_for(segment: str, department: str, row_index: int) -> int:
    if segment == "个人消费者":
        return [1, 2, 3, 4, 5, 6][row_index % 6]
    if department in {"汽车电子", "消费电子"}:
        return 40 + (row_index * 7) % 280
    return 20 + (row_index * 5) % 180


def choose_carrier(row_index: int, rng: random.Random) -> tuple[str, str, str, int]:
    if row_index % 5 == 0:
        return rng.choice(CARRIERS["航空"])
    if row_index % 3 == 0:
        return rng.choice(CARRIERS["铁路"])
    return rng.choice(CARRIERS["公路"])


def delivery_for(row_index: int, days_scheduled: int) -> tuple[int, int, str]:
    pattern = row_index % 6
    if pattern == 0:
        days_real = max(1, days_scheduled - 1)
    elif pattern in {1, 2, 3}:
        days_real = days_scheduled
    else:
        days_real = days_scheduled + (1 if pattern == 4 else 2)

    if days_real > days_scheduled:
        return days_real, 1, "延误发货"
    if days_real < days_scheduled:
        return days_real, 0, "提前发货"
    return days_real, 0, "按时发货"


def order_status_for(row_index: int) -> str:
    if row_index % 17 == 0:
        return "已退款"
    if row_index % 11 == 0:
        return "正在处理"
    if row_index % 7 == 0:
        return "已发货"
    return "交易完成"


def generate_rows(row_count: int, start_order_seq: int, start_customer_seq: int, seed: int) -> list[list]:
    rng = random.Random(seed)
    base_date = datetime(2024, 9, 1, 8, 0, 0)
    rows: list[list] = []

    for i in range(row_count):
        row_index = i + 1
        product = PRODUCTS[i % len(PRODUCTS)]
        province, city, lat, lon = LOCATIONS[i % len(LOCATIONS)]
        segment = SEGMENTS[row_index % len(SEGMENTS)]
        customer_seq = start_customer_seq + i
        customer_name, email = make_customer_name(segment, city, customer_seq, rng)
        street = f"{rng.choice(STREETS)}{rng.randint(1, 999)}号"
        customer_id = f"CUST-{customer_seq:05d}"
        order_id = f"ORD-2024-{start_order_seq + i}"

        order_date = base_date + timedelta(days=row_index * 2, hours=row_index % 9, minutes=(row_index * 7) % 60)
        ship_date = order_date + timedelta(hours=8 + (row_index % 30))

        quantity = quantity_for(segment, product["department"], row_index)
        discount_rate = round(((row_index * 3) % 13) / 100, 3)
        if segment == "政府机构":
            discount_rate = round(0.06 + (row_index % 5) * 0.01, 3)
        elif segment == "企业采购":
            discount_rate = round(0.04 + (row_index % 7) * 0.01, 3)

        gross_total = round(product["base_price"] * quantity, 2)
        discount_amount = round(gross_total * discount_rate, 2)
        net_total = round(gross_total - discount_amount, 2)

        supplier_name, supplier_city, component_name, unit_cost, defect_rate = product["suppliers"][row_index % len(product["suppliers"])]
        mfg_cost = round(unit_cost * (0.94 + (row_index % 9) * 0.015), 2)
        defect_rate = round(defect_rate * (0.92 + (row_index % 6) * 0.03), 5)

        carrier_name, trans_mode, ship_mode, days_scheduled = choose_carrier(row_index, rng)
        days_real, late_risk, delivery_status = delivery_for(row_index, days_scheduled)
        scheduled_date = ship_date + timedelta(days=days_scheduled)
        actual_date = ship_date + timedelta(days=days_real)

        logistics_cost = round(
            quantity
            * (
                35.0
                if trans_mode == "航空货运"
                else 18.0
                if trans_mode == "铁路干线"
                else 12.0
            ),
            2,
        )
        packaging_cost = round(quantity * (8.0 if product["department"] in {"消费电子", "智能穿戴"} else 5.0), 2)
        total_cost = round(quantity * mfg_cost + logistics_cost + packaging_cost, 2)
        profit = round(max(net_total - total_cost, net_total * 0.12), 2)
        profit_ratio = round(profit / net_total, 4) if net_total else 0.0

        rows.append(
            [
                order_id,
                order_date.strftime("%Y-%m-%d %H:%M:%S"),
                ship_date.strftime("%Y-%m-%d %H:%M:%S"),
                scheduled_date.strftime("%Y-%m-%d %H:%M:%S"),
                actual_date.strftime("%Y-%m-%d %H:%M:%S"),
                PAYMENTS[row_index % len(PAYMENTS)],
                order_status_for(row_index),
                gross_total,
                profit,
                customer_id,
                customer_name,
                email,
                segment,
                "中国",
                province,
                city,
                street,
                round(lat + ((row_index % 9) - 4) * 0.0123, 5),
                round(lon + ((row_index % 7) - 3) * 0.0142, 5),
                product["department"],
                product["category"],
                product["product_id"],
                product["sku"],
                product["name"],
                product["desc"],
                product["base_price"],
                quantity,
                discount_rate,
                discount_amount,
                net_total,
                profit_ratio,
                supplier_name,
                supplier_city,
                component_name,
                mfg_cost,
                defect_rate,
                carrier_name,
                trans_mode,
                ship_mode,
                days_scheduled,
                days_real,
                late_risk,
                delivery_status,
            ]
        )

    return rows


def write_csv(path: Path, rows: list[list]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_COLUMNS)
        writer.writerows(rows)


def main() -> None:
    args = parse_args()
    rows = generate_rows(
        row_count=args.rows,
        start_order_seq=args.start_order_seq,
        start_customer_seq=args.start_customer_seq,
        seed=args.seed,
    )
    output = Path(args.output)
    write_csv(output, rows)
    print(f"Generated {len(rows)} rows -> {output}")
    print(f"Order range: ORD-2024-{args.start_order_seq} .. ORD-2024-{args.start_order_seq + len(rows) - 1}")


if __name__ == "__main__":
    main()
