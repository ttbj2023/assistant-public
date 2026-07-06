# Vega-Lite 语法参考

渲染环境: SVG renderer, nominal/ordinal X 轴标签默认 -45 度.

> **配色已内置**: AntV 8 色分类色板自动生效, 无需手动指定颜色. 仅连续型图表(如热力图)需用 `"scale": {"scheme": "blues"}` 指定渐变色.

## Spec 基本结构

```json
{
  "mark": {"type": "bar", "tooltip": true},
  "encoding": {
    "x": {"field": "category", "type": "nominal"},
    "y": {"field": "value", "type": "quantitative"}
  },
  "data": {"values": [
    {"category": "A", "value": 28},
    {"category": "B", "value": 55}
  ]}
}
```

字段类型 (type):
- `quantitative` 数值 (Y轴常用)
- `nominal` 类别名 (X轴分类)
- `ordinal` 有序类别 (如 "低/中/高")
- `temporal` 时间 (折线图X轴)

聚合: 在 encoding 中加 `"aggregate": "sum"` / `"average"` / `"count"` 等.

## bar (柱状图)

```json
{
  "mark": {"type": "bar", "tooltip": true},
  "encoding": {
    "x": {"field": "产品", "type": "nominal", "sort": "-y"},
    "y": {"field": "销量", "type": "quantitative"},
    "color": {"field": "产品", "type": "nominal", "legend": null}
  },
  "data": {"values": [
    {"产品": "手机", "销量": 1200},
    {"产品": "电脑", "销量": 800},
    {"产品": "平板", "销量": 450}
  ]}
}
```

## line (折线图)

```json
{
  "mark": {"type": "line", "point": true, "strokeWidth": 2},
  "encoding": {
    "x": {"field": "月份", "type": "ordinal"},
    "y": {"field": "收入", "type": "quantitative"}
  },
  "data": {"values": [
    {"月份": "1月", "收入": 320},
    {"月份": "2月", "收入": 280},
    {"月份": "3月", "收入": 410}
  ]}
}
```

时间序列用 `"type": "temporal"` + `"timeUnit": "month"`:
```json
"x": {"field": "date", "type": "temporal", "timeUnit": "yearmonth"}
```

## arc (饼图/环形图)

> **关键**: theta encoding 必须设 `"stack": true` 且放在顶层共享 encoding 中. 渲染器自动检测相邻标签重叠并隐藏较小扇区的标签, legend 兜底.

```json
{
  "encoding": {
    "theta": {"field": "value", "type": "quantitative", "stack": true},
    "color": {"field": "category", "type": "nominal", "legend": {"orient": "right"}}
  },
  "layer": [
    {"mark": {"type": "arc", "outerRadius": 80}},
    {
      "mark": {"type": "text", "radius": 105, "fontSize": 13},
      "encoding": {
        "text": {"field": "label", "type": "nominal"},
        "color": {"value": "#333333"}
      }
    }
  ],
  "data": {"values": [
    {"category": "食品", "value": 35, "label": "食品 35%"},
    {"category": "住房", "value": 30, "label": "住房 30%"},
    {"category": "交通", "value": 15, "label": "交通 15%"},
    {"category": "娱乐", "value": 10, "label": "娱乐 10%"},
    {"category": "医疗", "value": 5, "label": "医疗 5%"},
    {"category": "其他", "value": 5, "label": "其他 5%"}
  ]}
}
```

要点:
- **theta 的 `"stack": true` 不可省略**: 让 text 层和 arc 层共享极坐标堆叠布局
- **text 层必须设 `"color": {"value": "#333333"}`**: 不设则文字继承扇区颜色, 靠近时融入色块
- `radius` 比 `outerRadius` 大 20-25 (如 80→105), 留足间距
- 标签内容预先组合到 `label` 字段 (如 "食品 35%")
- 保留 legend 提供颜色-类别对应, 被自动隐藏的小扇区标签通过 legend 标识

## scatter (散点图)

```json
{
  "mark": {"type": "circle", "tooltip": true, "size": 100},
  "encoding": {
    "x": {"field": "身高", "type": "quantitative"},
    "y": {"field": "体重", "type": "quantitative"},
    "color": {"field": "性别", "type": "nominal"}
  },
  "data": {"values": [
    {"身高": 170, "体重": 65, "性别": "男"},
    {"身高": 160, "体重": 50, "性别": "女"}
  ]}
}
```

## stacked bar (堆叠柱状图)

```json
{
  "mark": {"type": "bar", "tooltip": true},
  "encoding": {
    "x": {"field": "季度", "type": "ordinal"},
    "y": {"field": "收入", "type": "quantitative"},
    "color": {"field": "业务线", "type": "nominal"}
  },
  "data": {"values": [
    {"季度": "Q1", "业务线": "云服务", "收入": 200},
    {"季度": "Q1", "业务线": "广告", "收入": 150},
    {"季度": "Q2", "业务线": "云服务", "收入": 250},
    {"季度": "Q2", "业务线": "广告", "收入": 180}
  ]}
}
```

## area (面积图)

```json
{
  "mark": {"type": "area", "tooltip": true, "opacity": 0.7},
  "encoding": {
    "x": {"field": "日期", "type": "ordinal"},
    "y": {"field": "用户数", "type": "quantitative", "aggregate": "sum"}
  },
  "data": {"values": [
    {"日期": "周一", "用户数": 1200},
    {"日期": "周二", "用户数": 1500},
    {"日期": "周三", "用户数": 1800}
  ]}
}
```

## heatmap (热力图)

用 `rect` mark + color encoding 展示二维矩阵数据:

```json
{
  "mark": {"type": "rect", "tooltip": true},
  "encoding": {
    "x": {"field": "day", "type": "ordinal"},
    "y": {"field": "time", "type": "ordinal"},
    "color": {"field": "val", "type": "quantitative", "scale": {"scheme": "blues"}}
  },
  "data": {"values": [
    {"day": "周一", "time": "上午", "val": 85},
    {"day": "周一", "time": "下午", "val": 120},
    {"day": "周一", "time": "晚间", "val": 180},
    {"day": "周二", "time": "上午", "val": 90},
    {"day": "周二", "time": "晚间", "val": 175}
  ]}
}
```

`"scale": {"scheme": "blues"}` 控制颜色渐变方案 (可选: "blues"/"reds"/"greens"/"viridis" 等).

## layered (双轴/多标记叠加)

用 `layer` 数组叠加多个 mark, 配合 `resolve` 实现双 Y 轴:

```json
{
  "layer": [
    {
      "mark": {"type": "bar", "tooltip": true},
      "encoding": {
        "x": {"field": "月份", "type": "ordinal"},
        "y": {"field": "营收", "type": "quantitative", "title": "营收(万元)"}
      }
    },
    {
      "mark": {"type": "line", "color": "#FF6B6B", "point": true},
      "encoding": {
        "x": {"field": "月份", "type": "ordinal"},
        "y": {"field": "增长率", "type": "quantitative", "title": "增长率(%)"}
      }
    }
  ],
  "resolve": {"scale": {"y": "independent"}},
  "data": {"values": [
    {"月份": "1月", "营收": 320, "增长率": 15},
    {"月份": "2月", "营收": 280, "增长率": -12},
    {"月份": "3月", "营收": 410, "增长率": 46}
  ]}
}
```

> `"resolve": {"scale": {"y": "independent"}}` 让两条 Y 轴各自独立缩放, 实现双 Y 轴效果.

## 常见陷阱

1. **字段类型必须正确**: quantitative(数值) vs nominal(类别) vs temporal(时间), 类型错误会导致图表异常
2. **聚合语法**: 多条数据聚合时加 `"aggregate": "sum"`, 否则叠加显示散点
3. **中文标签**: Vega-Lite 原生支持 Unicode, 中文字段名和值无需特殊处理
4. **X 轴标签旋转**: nominal/ordinal X 轴默认 -45 度斜向旋转, 水平角度自定义: `"axis": {"labelAngle": 0}`
5. **排序**: `"sort": "-y"` 按Y值降序排列, `"sort": "field名"` 按字段排序
6. **width/height**: 可在 spec 内设 `"width": 400`, 或通过工具参数注入覆盖
7. **tooltip**: 推荐在 mark 中加 `"tooltip": true` 启用交互提示 (渲染为PNG时虽不可交互但不报错)
8. **堆叠图数据格式**: 每个 (X类别, 分组类别) 组合需要一条独立数据记录, 不能合并. 如 Q1 的云服务和广告是两条记录
9. **双 Y 轴**: 必须 `"resolve": {"scale": {"y": "independent"}}`, 否则两层数据共用一个 Y 轴
10. **arc/饼图**: theta encoding 用 `"type": "quantitative"` (不是 y), color 用 `"type": "nominal"`
11. **layer 中的 mark**: 子层可不设 `data`, 继承外层 `data`; 但每个 layer 的 `encoding` 需独立声明
12. **temporal 日期格式**: 日期字符串用 ISO 格式 `"2024-03-01"`, 避免使用 `"3/1"` 等非标准格式
