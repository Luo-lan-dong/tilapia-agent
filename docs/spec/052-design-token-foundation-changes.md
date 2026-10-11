# Changes：设计 token 底座落地（继承 ADR-052）

> 关联决策：[ADR-052](../adr/052-light-theme.md)｜规范：[frontend-component-standards.md §3.2](../standards/frontend-component-standards.md)
> 背景：批次①仅产出规范文档与门禁脚本（334 处尺寸字面量待迁移），三层 token 的"唯一数据源"尚未落地（tokens.ts 无尺寸档位、index.css 无语义变量、`src/theme/components.ts` 不存在）。2026-10-07 用户确认"现在补齐"，本清单登记落地改动。
> 原则：**零视觉风险为底**——档位值与 antd 现默认一致（md=8、control 24/32/40）；仅两处按用户已确认基调（2026-10-05）产生预期内变化：`borderRadiusSM 6→4`（小控件）、`borderRadiusLG 8→12`（容器）。
> 存量 334 处尺寸字面量**不在本批迁移**，按域分批（迁移纪律见 §3.2.3）。

## 变更清单

| 文件 | 类型 | 明细 | 作用 |
|---|---|---|---|
| `opc-web-console/src/theme/tokens.ts` | 增 | 新增 primitive 档位常量 `RADIUS{none,sm,md,lg,xl,full}`、`SPACE{xxs..xxl}`、`CONTROL{heightSm, heightMd, heightLg}`、`ICON{sm,md,lg}`；文件头注释更新为"设计 token 单一事实源（颜色 ADR-052 + 尺寸 §3.2）" | primitive 层唯一数据源，改基调只改此文件 |
| `opc-web-console/src/theme/components.ts` | 新增 | 组件层规格 `buttonSpec`/`inputSpec`/`cardSpec`/`tableSpec`/`tagSpec`/`iconSpec`（圆角/高度/图标，全部从 tokens.ts 档位取值；密度暂按 antd 默认，各域迁移时再收敛） | component 层集中声明，禁止页面覆盖 |
| `opc-web-console/src/index.css` | 增 | `:root` 新增尺寸语义变量：`--radius-{sm,md,lg}`、`--space-{xxs..xxl}`、语义间距 `--space-inline/--space-stack/--space-group/--card-padding/--section-gap`、`--control-height-{sm,md,lg}`、`--icon-{sm,md,lg}`；区块注释更新 | 两路输出之 CSS 侧；尺寸不分主题态，仅 `:root` 一份 |
| `opc-web-console/src/App.tsx` | 修 | `ThemedApp` ConfigProvider token：`borderRadius: 8` → `RADIUS.md`；新增 `borderRadiusSM: RADIUS.sm`（antd 默认 6→4）、`borderRadiusLG: RADIUS.lg`（antd 默认 8→12）、`controlHeight/SM/LG: CONTROL.*`（与 antd 默认 32/24/40 一致，零变化）；import 增 `CONTROL, RADIUS` | ConfigProvider 接入档位；`Menu.itemBorderRadius: 6` 等存量不动，留给 AppLayout 域迁移 |

## 验证记录

- `tsc -b` 零错误、`vitest run` 通过（见 changelog 2026-10-07 条目）
- 浏览器双主题走查：控件/卡片圆角与密度符合基调
- `python scripts/check_docs.py` 零错误（本文件已登记 INDEX 1.3）
