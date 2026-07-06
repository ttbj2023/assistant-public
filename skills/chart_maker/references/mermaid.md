# Mermaid 语法参考

渲染环境: securityLevel="loose" (HTML标签可用), theme="base" (柔和专业配色自动生效), 中文字体.

> mermaid 仅用于流程图和时序图. 甘特图/饼图/状态图/类图等渲染质量不稳定, 请改用 Vega-Lite 或用 flowchart 替代表达.

> **关键**: 每种图的第一行必须是图类型关键字 (`flowchart` 或 `sequenceDiagram`), 不能省略.

## flowchart (流程图)

方向: TD/TB(上→下), LR(左→右), BT(下→上), RL(右→左).

节点形状 (常用):
- `A[文本]` 矩形 | `A(文本)` 圆角 | `A{文本}` 菱形(决策)
- `A[(数据库)]` 圆柱 | `A((圆))` 圆形 | `A>文本]` 旗帜形

连线类型:
- `A --> B` 实线箭头 | `A --- B` 实线无箭头
- `A -.-> B` 虚线箭头 | `A ==> B` 粗线箭头
- `A -->|标签| B` 帶标签

子图: `subgraph 标题 ... end`

Unicode 语义符号增强可读性 (推荐在节点文本中使用):
- 👤 用户/角色 | 🌐 网络/网关 | 🔐 认证/安全 | ⚙️ 服务/处理
- 💾 数据库 | ⚡ 缓存 | 📬 消息队列 | 📦 存储 | ✅ 成功 | ❌ 失败

完整示例:
```
flowchart TD
    A[👤 用户请求] --> B{🔐 已登录?}
    B -->|是| C[⚙️ 处理请求]
    B -->|否| D[跳转登录]
    D --> E[输入凭证]
    E --> F{验证成功?}
    F -->|✅ 是| C
    F -->|❌ 否| D
    C --> G[返回结果]
```

高对比样式 (仅在需区分多角色时使用, 默认配色已足够):
```
flowchart TD
    A[👤 用户] --> B[🌐 API网关]
    B --> C[⚙️ 订单服务]
    C --> D[(💾 数据库)]

    classDef user fill:#90EE90,stroke:#333,stroke-width:2px,color:#006400
    classDef infra fill:#87CEEB,stroke:#333,stroke-width:2px,color:#00008B
    classDef data fill:#E6E6FA,stroke:#333,stroke-width:2px,color:#00008B
    class A user
    class B,C infra
    class D data
```

> **样式规则**: 每个 classDef 必须包含 `color:` 属性 (浅底深字/深底浅字), 否则 PNG 中文字可能不可读. 默认主题已使用柔和专业配色, 常规图无需 classDef.

## sequenceDiagram (时序图)

参与者声明: `participant A` / `actor A`
消息箭头:
- `A->>B: 消息` 实线箭头 | `A-->>B: 消息` 虚线箭头
- `A-)B: 消息` 异步 | `A-xB: 消息` 失败

控制结构:
- `loop 描述 ... end`
- `alt 条件 ... else 其他 ... end`
- `opt 可选 ... end`
- `par 并行 and 并行 ... end`

激活: `activate A` / `deactivate A` 或简写 `+`/`-`: `A->>+B: 消息` `B-->>-A: 回复`

完整示例:
```
sequenceDiagram
    participant U as 用户
    participant S as 服务端
    participant D as 数据库

    U->>S: 登录请求
    S->>D: 查询用户
    D-->>S: 用户数据
    S-->>U: 认证成功

    loop 心跳
        U->>S: 心跳包
        S-->>U: ACK
    end
```

## 常见陷阱

1. **图类型关键字不可省略**: `flowchart`、`sequenceDiagram` 必须是第一行
2. **classDef 名不能用关键字**: `end`/`loop`/`alt`/`else`/`subgraph` 等是保留字, 用作 classDef 名会导致解析错误. 改用 `done`/`finish`/`terminal` 等
3. **subgraph 中不能用 `direction`**: mermaid v10+ 的 subgraph 不支持 `direction` 关键字, 方向由外层 flowchart 声明决定
4. **"end" 关键字**: 节点文本含 "end" 会破坏解析, 需大写 "End" 或用引号包裹 `"end"`
5. **节点 ID 首字母 "o"/"x"**: 如 `o-->B` 会被解析为圆形边, 需大写或加空格
6. **特殊字符**: 括号 `()` `[]` `{}` 在节点文本中需用引号包裹: `A["文本(含括号)"]`
7. **subgraph 标题含特殊字符**: subgraph 名含空格/括号可能导致解析错误, 建议用简洁标识
8. **中文**: securityLevel="loose" 已配置, 中文标签和 HTML 标签可直接使用
9. **分号**: 语句末尾不需要分号
