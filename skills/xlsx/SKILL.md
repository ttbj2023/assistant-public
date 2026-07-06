---
name: xlsx
description: "生成专业Excel报表(.xlsx). 当用户需要创建数据表格、财务模型、统计报表时触发. 提供金融模型颜色规范、数字格式约定、公式构造规则(用公式而非硬编码值), 产物经LibreOffice重算并验证零公式错误. 纯生成场景(不处理已上传文件)."
---

# Excel 报表生成

> 适配自 [anthropics/skills](https://github.com/anthropics/skills) 的 xlsx skill, 聚焦**生成新文件**(网关无文档上传通道, 不处理已存在文件).

## 运行环境

- **执行器**: skill_executor 工具(由 load_skill 激活后注入)
- **工作目录**: `/workspace`, 产物必须写入 `/workspace/output/` 才会被回收为附件
- **预装**: openpyxl / numpy / pandas + LibreOffice(公式重算)
- **重算脚本**: `/skills/xlsx/scripts/recalc.py`(用 LibreOffice 重算公式 + 扫描零错误)

### 重要: 执行环境无状态

**每次 `skill_executor` 调用都在独立的容器环境中执行.** 前一次调用写入 `/workspace/output/` 的 Excel 文件, 在后续调用中**不可见、不可读取、不可修改**.

因此:
- ❌ 不要分多次调用: 第一次生成, 第二次打开修改
- ✅ 必须在**同一次** `code` 中完成: 生成 → 重算 → (如需)调整格式 → 保存

例如, 重算公式必须紧跟在 `wb.save(...)` 之后, 写在同一段 code 里:

```python
wb.save('/workspace/output/report.xlsx')

# 同一 code 内立即重算
import subprocess, json
result = subprocess.run(
    ['python', '/skills/xlsx/scripts/recalc.py', '/workspace/output/report.xlsx'],
    capture_output=True, text=True,
)
```

## 输出规范

### 专业字体
- 所有产物使用一致的专业字体(如 Arial), 除非用户另有要求

### 零公式错误(强制)
- 每个 Excel 模型交付时必须**零公式错误**(#REF!, #DIV/0!, #VALUE!, #N/A, #NAME?)
- 用 recalc.py 重算 + 验证

## 金融模型规范

### 颜色编码(行业惯例)
- **蓝色 (RGB 0,0,255)**: 硬编码输入 / 用户会改的场景数字
- **黑色 (RGB 0,0,0)**: 所有公式和计算
- **绿色 (RGB 0,128,0)**: 同工作簿跨工作表链接
- **红色 (RGB 255,0,0)**: 跨文件外部链接
- **黄色背景 (RGB 255,255,0)**: 需关注的关键假设

### 数字格式
- **年份**: 文本格式("2024" 而非 "2,024")
- **货币**: `$#,##0`, 表头注明单位("Revenue ($mm)")
- **零值**: 用格式把零显示为 "-", 含百分比(`$#,##0;($#,##0);-`)
- **百分比**: 默认 0.0%(一位小数)
- **倍数**: 估值倍数(EV/EBITDA, P/E)用 0.0x
- **负数**: 用括号 (123) 而非 -123

### 公式构造规则(关键)

**永远用 Excel 公式, 不要在 Python 里算好再硬编码值.** 这保证表格可动态更新.

#### 假设独立放置
- 所有假设(增长率/利润率/倍数)放独立假设单元格
- 公式用单元格引用, 不硬编码
- 正确: `=B5*(1+$B$6)`  错误: `=B5*1.05`

#### 公式错误预防
- 校验所有单元格引用正确
- 检查范围 off-by-one
- 投影期公式一致
- 测试边界(零值/负数)
- 确认无意外循环引用

## 创建新文件工作流

1. 用 openpyxl 构造工作簿(数据 + 公式 + 格式)
2. 保存到 `/workspace/output/<name>.xlsx`
3. 调 recalc.py 重算公式 + 验证零错误
4. 如有错误, 修复后重算, 直到零错误
5. 产物由系统自动回收为附件

### openpyxl 示例

```python
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment

wb = Workbook()
ws = wb.active

# 数据
ws['A1'] = '项目'
ws['B1'] = 'Q3'
ws['B2'] = 1000  # 假设值(蓝色字体标记输入)
ws['B2'].font = Font(color='0000FF')
# 公式(不要硬编码, 用单元格引用)
ws['B3'] = '=B2*1.13'  # 增长13%, 引用B2

# 格式
ws['B3'].number_format = '$#,##0'
ws.column_dimensions['A'].width = 20

wb.save('/workspace/output/report.xlsx')
```

### 重算 + 验证(强制)

openpyxl 写入的公式是字符串, 未计算值. **必须**用 recalc.py 重算:

```python
import subprocess, json

result = subprocess.run(
    ['python', '/skills/xlsx/scripts/recalc.py', '/workspace/output/report.xlsx'],
    capture_output=True, text=True,
)
data = json.loads(result.stdout)
# data["status"] == "success" 表示零错误
# data["status"] == "errors_found" 时查 data["error_summary"] 定位错误
```

recalc.py 返回:
- `status`: "success" 或 "errors_found"
- `total_errors`: 错误总数
- `total_formulas`: 公式数
- `error_summary`(仅错误时): 按错误类型分组的位置清单

## 公式检查清单

- [ ] 测试 2-3 个样本引用, 确认拉取正确值
- [ ] 列映射正确(列 64 = BL, 不是 BK)
- [ ] 行偏移(Excel 行 1-indexed, DataFrame 行 5 = Excel 行 6)
- [ ] 除零检查(分母用 `/` 前确认非零, 避免 #DIV/0!)
- [ ] 引用正确(避免 #REF!)
- [ ] 跨表引用用正确格式(Sheet1!A1)

## 代码风格

- 生成精简 Python 代码, 无冗余注释
- 复杂公式/关键假设处加单元格注释
- 硬编码值注明数据来源
