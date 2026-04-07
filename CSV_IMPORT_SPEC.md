# CSV 导入字段规范

本文档说明本项目在“CSV 批量更新”入口下，对导入文件的结构要求、字段建议和常见注意事项。

## 1. 支持的 CSV 类型

系统当前支持两类 CSV：

### 1. 业务主数据 CSV

适合导入订单、客户、产品、供应商、物流等业务数据。

典型示例字段：

```csv
order_id,customer_id,product_id,product_name,supplier_name,carrier_name,quantity,net_total
ORD-2024-100001,CUST-80401,SKU-WE-V01,4K超清 无线VR一体机,长江存储 (YMTC),跨越速运,3,8817.06
```

### 2. 关系型 CSV

适合直接补充图谱中的节点关系。

典型示例字段：

```csv
src_type,src_id,rel_type,dst_type,dst_name
Order,ORD-2024-100001,SHIPPED_BY,Carrier,跨越速运
```

## 2. 编码要求

- 推荐使用 `UTF-8 with BOM`（`utf-8-sig`）
- 系统也兼容部分 `GBK` 编码文件
- 如果导入后中文乱码，优先检查文件编码

## 3. 列名要求

系统支持：

- 中文列名
- 英文列名
- 与现有示例数据集一致的列名

推荐优先复用现有数据集 [Supply_Chain_Data_Fake.csv](e:\Projects\长江存储\SupplyChainGraph\数据\Supply_Chain_Data_Fake.csv) 的表头，兼容性最好。

## 4. 主数据 CSV 推荐字段

下面不是“全部必填”，但建议尽量完整。

### 4.1 核心标识字段

这些字段最重要，建议至少具备其中的一部分：

- `customer_id`
- `order_id`
- `product_id`
- `supplier_name`
- `component_name`
- `carrier_name`

如果缺少这些关键标识，系统虽然可能能读入文件，但很难在图谱中形成有效节点或关系。

### 4.2 常用业务字段

客户相关：

- `customer_name`
- `customer_email`
- `customer_segment`
- `customer_country`
- `customer_province`
- `customer_city`

订单相关：

- `order_date`
- `shipping_date`
- `scheduled_date`
- `actual_date`
- `payment_type`
- `order_status`

产品相关：

- `product_sku`
- `product_name`
- `product_desc`
- `product_base_price`
- `category_name`
- `department_name`

供应链与物流相关：

- `supplier_city`
- `mfg_cost`
- `defect_rate`
- `trans_mode`
- `ship_mode`
- `days_scheduled`
- `days_real`
- `late_risk`
- `delivery_status`

交易与金额相关：

- `quantity`
- `gross_total`
- `discount_rate`
- `discount_amount`
- `net_total`
- `profit`
- `profit_ratio`

## 5. 关系型 CSV 最低要求

如果是关系型 CSV，至少需要以下字段：

- `src_type`
- `rel_type`
- `dst_type`

并且还需要保证：

- 源节点有 `src_id` 或 `src_name`
- 目标节点有 `dst_id` 或 `dst_name`

示例：

```csv
src_type,src_id,rel_type,dst_type,dst_name
Customer,CUST-80401,PLACED_ORDER,Order,ORD-2024-100001
Order,ORD-2024-100001,SHIPPED_BY,Carrier,跨越速运
Supplier,,SUPPLIES_COMPONENT,Component,OLED柔性屏
```

## 6. 数据格式建议

### 6.1 数值字段

以下字段建议填写纯数字：

- `quantity`
- `gross_total`
- `discount_rate`
- `discount_amount`
- `net_total`
- `profit`
- `profit_ratio`
- `mfg_cost`
- `defect_rate`
- `days_scheduled`
- `days_real`
- `late_risk`

不建议写成：

- `3台`
- `12%`
- `5天`

建议写成：

- `3`
- `0.12`
- `5`

### 6.2 空值

- 空字符串会被系统视为 `null`
- 数值列格式不合法时，系统通常会转成空值而不是直接报错
- 但关键标识字段为空时，会影响图谱写入效果

### 6.3 日期

建议统一使用清晰的日期时间格式，例如：

- `2024/6/2 22:35`
- `2024-06-02 22:35:00`

只要同一批数据格式一致即可。

## 7. 更新策略说明

当前管理台中的 CSV 更新固定采用“安全补全”策略：

- 新数据会写入
- 已有节点或关系不会被随意覆盖
- 只有空字段才会被新值补全

这意味着：

- 适合企业场景下的稳妥增量更新
- 不适合直接拿来做强制纠错覆盖

如果后续确实需要“纠错覆盖”，建议单独设计管理员修正流程，而不是混在日常批量导入里。

## 8. 推荐做法

### 推荐做法一

直接参考现有示例数据集字段：

- [Supply_Chain_Data_Fake.csv](e:\Projects\长江存储\SupplyChainGraph\数据\Supply_Chain_Data_Fake.csv)

### 推荐做法二

每次导入前先自查这 3 件事：

- 是否包含关键标识字段
- 数值列是否为纯数字
- 编码是否为 `UTF-8 with BOM`

### 推荐做法三

如果是企业真实场景，建议把 CSV 作为“结构化主更新”入口，适合：

- ERP / WMS / MES 导出的批量数据
- 供应商台账
- 订单与物流报表

而 PDF、文本更适合作为补充知识来源，不建议替代结构化主更新。

## 9. 最小可用示例

### 9.1 最小主数据 CSV

```csv
order_id,customer_id,product_id,product_name,supplier_name,carrier_name
ORD-2024-200001,CUST-90001,SKU-ST-S01,2TB PCIe 4.0 旗舰固态,长江存储 (YMTC),顺丰速运
```

### 9.2 更推荐的主数据 CSV

```csv
order_id,order_status,customer_id,customer_name,product_id,product_name,supplier_name,supplier_city,carrier_name,quantity,net_total,profit,days_scheduled,days_real,late_risk
ORD-2024-200001,已发货,CUST-90001,张三,SKU-ST-S01,2TB PCIe 4.0 旗舰固态,长江存储 (YMTC),武汉,顺丰速运,10,12990,4200,1,2,1
```

### 9.3 最小关系型 CSV

```csv
src_type,src_id,rel_type,dst_type,dst_name
Order,ORD-2024-200001,SHIPPED_BY,Carrier,顺丰速运
```
