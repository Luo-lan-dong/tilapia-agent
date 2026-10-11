import { lazy, Suspense } from 'react';
import { App as AntApp, ConfigProvider, Spin, theme as antdTheme } from 'antd';
import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { COLOR_SETS, CONTROL, RADIUS } from '@/theme/tokens';
import { ThemeProvider } from '@/theme/ThemeProvider';
import { useTheme } from '@/theme/useTheme';
import { AppLayout } from '@/components/AppLayout';
import { ErrorBoundary } from '@/components/ErrorBoundary';
import './App.css';

// 页面组件按路由懒加载（代码分割）：首屏仅加载布局 chunk，各页面在路由命中时
// 按需加载，避免主 chunk 超过 500kB（Vite 体积警告，见 2026-09-21 代码审查 3.3）
const Changelog = lazy(() => import('@/pages/Changelog').then((m) => ({ default: m.Changelog })));
const Dashboard = lazy(() => import('@/pages/Dashboard').then((m) => ({ default: m.Dashboard })));
const Docs = lazy(() => import('@/pages/Docs').then((m) => ({ default: m.Docs })));
const NodeMetrics = lazy(() =>
  import('@/pages/NodeMetrics').then((m) => ({ default: m.NodeMetrics })),
);
const LazyLogs = lazy(() => import('@/pages/Logs').then((m) => ({ default: m.Logs })));
const Memory = lazy(() => import('@/pages/Memory').then((m) => ({ default: m.Memory })));
const Nodes = lazy(() => import('@/pages/Nodes').then((m) => ({ default: m.Nodes })));
const ResourceManage = lazy(() =>
  import('@/pages/ResourceManage').then((m) => ({ default: m.ResourceManage })),
);
const Settings = lazy(() => import('@/pages/Settings').then((m) => ({ default: m.Settings })));
const Skills = lazy(() => import('@/pages/Skills').then((m) => ({ default: m.Skills })));
const TaskNew = lazy(() => import('@/pages/TaskNew').then((m) => ({ default: m.TaskNew })));
const TaskQueue = lazy(() => import('@/pages/TaskQueue').then((m) => ({ default: m.TaskQueue })));

/**
 * 主题化应用根：按当前主题态切换 antd 算法（dark/default）与 token
 * （COLOR_SETS 双态，ADR-052）；CSS 变量由 html[data-theme] 驱动双态。
 */
function ThemedApp() {
  const { theme } = useTheme();
  const colors = COLOR_SETS[theme];

  return (
    <BrowserRouter>
      <ConfigProvider
        theme={{
          algorithm: theme === 'dark' ? antdTheme.darkAlgorithm : antdTheme.defaultAlgorithm,
          token: {
            colorBgBase: colors.bgBase,
            colorBgContainer: colors.bgContainer,
            colorBgElevated: colors.bgElevated,
            colorPrimary: colors.primary,
            colorTextBase: colors.textPrimary,
            colorTextSecondary: colors.textSecondary,
            colorBorder: colors.border,
            colorBorderSecondary: colors.borderStrong,
            // 尺寸档位（frontend §3.2，唯一数据源 tokens.ts）：
            // md=8 控件默认；sm=4 小控件（Tag/小按钮）；lg=12 容器（卡片/抽屉）
            borderRadius: RADIUS.md,
            borderRadiusSM: RADIUS.sm,
            borderRadiusLG: RADIUS.lg,
            // 密度默认 32；紧凑 24（size="small"）；重要操作 40
            controlHeight: CONTROL.heightMd,
            controlHeightSM: CONTROL.heightSm,
            controlHeightLG: CONTROL.heightLg,
          },
          components: {
            Menu: {
              // 侧边栏菜单配色（双态通用 token + darkItem* 兜底：theme="dark" 时
              // antd 用 darkItem* 默认深藏青覆盖通用 item*，必须同时提供）
              itemBg: 'transparent',
              itemColor: colors.textSecondary,
              itemHoverColor: colors.textPrimary,
              itemHoverBg: colors.surface,
              itemSelectedColor: colors.primary,
              itemSelectedBg: colors.surface,
              darkItemBg: 'transparent',
              darkItemColor: colors.textSecondary,
              darkItemHoverColor: colors.textPrimary,
              darkItemHoverBg: colors.surface,
              darkItemSelectedColor: colors.primary,
              darkItemSelectedBg: colors.surface,
              itemBorderRadius: 6,
              itemMarginInline: 10,
              itemMarginBlock: 2,
            },
            // Drawer 背景由全局 colorBgElevated 控制，无需组件级 token
            Table: {
              colorBgContainer: colors.bgContainer,
              headerBg: colors.surface,
              headerColor: colors.textPrimary,
              rowHoverBg: colors.surface,
              borderColor: colors.border,
            },
            Modal: {
              contentBg: colors.bgContainer,
              headerBg: colors.bgContainer,
              titleColor: colors.textPrimary,
            },
            Input: {
              colorBgContainer: colors.bgBase,
              colorBorder: colors.borderStrong,
              activeBorderColor: colors.primary,
              hoverBorderColor: colors.primary,
              colorText: colors.textPrimary,
            },
          },
        }}
      >
        <AntApp>
          <ErrorBoundary>
            <Suspense
              fallback={
                <div
                  style={{
                    display: 'flex',
                    justifyContent: 'center',
                    alignItems: 'center',
                    minHeight: '60vh',
                  }}
                >
                  {/* 裸 Spin 未加 tip，符合 frontend-component-standards 4.1 约束 */}
                  <Spin size="large" />
                </div>
              }
            >
              <Routes>
                <Route path="/" element={<AppLayout />}>
                  <Route index element={<Dashboard />} />
                  <Route path="nodes" element={<Nodes />} />
                  <Route path="nodes/:id/metrics" element={<NodeMetrics />} />
                  <Route path="tasks" element={<TaskQueue />} />
                  <Route path="tasks/new" element={<TaskNew />} />
                  <Route path="skills" element={<Skills />} />
                  <Route path="docs" element={<Docs />} />
                  <Route path="logs" element={<LazyLogs />} />
                  <Route path="memory" element={<Memory />} />
                  <Route path="settings" element={<Settings />} />
                  <Route path="settings/changelog" element={<Changelog />} />
                  <Route path="settings/resources" element={<ResourceManage />} />
                </Route>
              </Routes>
            </Suspense>
          </ErrorBoundary>
        </AntApp>
      </ConfigProvider>
    </BrowserRouter>
  );
}

/**
 * 应用根组件：ThemeProvider 提供主题态（localStorage 持久化 +
 * html[data-theme] 同步），ThemedApp 据此配置 antd 主题并渲染路由。
 */
function App() {
  return (
    <ThemeProvider>
      <ThemedApp />
    </ThemeProvider>
  );
}

export default App;
