/* 规范豁免说明（coding-standards 4.3，通用组件上限 200 行）：全局布局组件承载菜单/侧栏/版本展示等框架级职责，天然聚合，故豁免。 */
import { useEffect, useState } from 'react';
import { Drawer, Layout, Menu, Button, Space, Typography, type MenuProps } from 'antd';
import {
  ApiOutlined,
  BookOutlined,
  CodeOutlined,
  DashboardOutlined,
  ClusterOutlined,
  FileTextOutlined,
  MenuOutlined,
  MoonOutlined,
  OrderedListOutlined,
  PlusOutlined,
  SettingOutlined,
  SunOutlined,
} from '@ant-design/icons';
import { COLORS } from '@/theme/tokens';
import { useTheme } from '@/theme/useTheme';
import { terminalStore } from '@/stores/terminalStore';
import { TerminalDrawer } from '@/components/TerminalDrawer';
import { Link, Outlet, useLocation } from 'react-router-dom';
import { fetchSystemVersions } from '@/api/systemVersion';
import { useMediaQuery } from '@/hooks/useMediaQuery';
import {
  beginMoveDesktopWindow,
  isDesktopShell,
  toggleMaximizeDesktopWindow,
} from '@/utils/desktopShell';
import { WindowControls } from '@/components/WindowControls';
import './AppLayout.css';

const { Sider, Header, Content } = Layout;
const { Title } = Typography;

/** 移动端断点：小于 768px 视为移动端/窄屏 */
const MOBILE_BREAKPOINT = '(max-width: 767px)';

/**
 * 侧边栏菜单配置。
 *
 * 使用 React Router 的 Link 作为菜单项入口，确保路由切换无刷新。
 */
const menuItems: MenuProps['items'] = [
  {
    key: 'dashboard',
    icon: <DashboardOutlined />,
    label: <Link to="/">集群仪表盘</Link>,
  },
  {
    key: 'nodes',
    icon: <ClusterOutlined />,
    label: <Link to="/nodes">节点管理</Link>,
  },
  {
    key: 'task-new',
    icon: <PlusOutlined />,
    label: <Link to="/tasks/new">新建对话</Link>,
  },
  {
    key: 'skills',
    icon: <ApiOutlined />,
    label: <Link to="/skills">技能</Link>,
  },
  {
    key: 'docs',
    icon: <BookOutlined />,
    label: <Link to="/docs">文档</Link>,
  },
  {
    key: 'logs',
    icon: <FileTextOutlined />,
    label: <Link to="/logs">日志</Link>,
  },
  {
    key: 'memory',
    icon: <FileTextOutlined />,
    label: <Link to="/memory">记忆</Link>,
  },
  {
    key: 'tasks',
    icon: <OrderedListOutlined />,
    label: <Link to="/tasks">任务队列</Link>,
  },
  {
    key: 'settings',
    icon: <SettingOutlined />,
    label: '系统设置',
    children: [
      {
        key: 'settings-llm',
        label: <Link to="/settings">系统设置</Link>,
      },
      {
        key: 'settings-changelog',
        label: <Link to="/settings/changelog">更新日志</Link>,
      },
      {
        key: 'settings-resources',
        label: <Link to="/settings/resources">资源管理</Link>,
      },
    ],
  },
];

/**
 * 根据当前路径匹配高亮的菜单 key。
 */
function getSelectedKey(pathname: string): string {
  if (pathname === '/' || pathname === '') {
    return 'dashboard';
  }
  if (pathname.startsWith('/nodes')) {
    return 'nodes';
  }
  if (pathname.startsWith('/tasks/new')) {
    return 'task-new';
  }
  if (pathname.startsWith('/skills')) {
    return 'skills';
  }
  if (pathname.startsWith('/docs')) {
    return 'docs';
  }
  if (pathname.startsWith('/logs')) {
    return 'logs';
  }
  if (pathname.startsWith('/memory')) {
    return 'memory';
  }
  if (pathname.startsWith('/tasks')) {
    return 'tasks';
  }
  if (pathname.startsWith('/settings/resources')) {
    return 'settings-resources';
  }
  if (pathname.startsWith('/settings/changelog')) {
    return 'settings-changelog';
  }
  if (pathname.startsWith('/settings')) {
    return 'settings-llm';
  }
  return 'dashboard';
}

/**
 * 根据当前路径获取顶部标题文案。
 */
function getPageTitle(pathname: string): string {
  if (pathname.startsWith('/nodes')) {
    return '节点管理';
  }
  if (pathname.startsWith('/tasks/new')) {
    return '新建任务';
  }
  if (pathname.startsWith('/skills')) {
    return '技能';
  }
  if (pathname.startsWith('/logs')) {
    return '日志';
  }
  if (pathname.startsWith('/memory')) {
    return '记忆';
  }
  if (pathname.startsWith('/tasks')) {
    return '任务队列';
  }
  if (pathname.startsWith('/settings/resources')) {
    return '资源管理';
  }
  if (pathname.startsWith('/settings')) {
    return '系统设置';
  }
  return '集群仪表盘';
}

/**
 * 应用整体布局组件。
 *
 * 提供左侧固定侧边栏、顶部标题栏以及右侧子路由渲染区域。
 * 子页面通过 <Outlet /> 注入，菜单高亮与页面标题跟随当前路由变化。
 */
export function AppLayout() {
  const { pathname } = useLocation();
  const { theme, setTheme } = useTheme();
  const selectedKey = getSelectedKey(pathname);
  const pageTitle = getPageTitle(pathname);
  const isMobile = useMediaQuery(MOBILE_BREAKPOINT);
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const [currentVersion, setCurrentVersion] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchSystemVersions()
      .then((versions) => {
        if (cancelled) {
          return;
        }
        const current = versions.find((item) => item.is_current);
        setCurrentVersion(current ? current.version : null);
      })
      .catch(() => {
        // 静默降级：接口失败时保持 null，侧边栏显示 --，不阻塞布局
        setCurrentVersion(null);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const menuNode = (
    <Menu
      mode="inline"
      theme={theme}
      selectedKeys={[selectedKey]}
      items={menuItems}
      className="app-layout__menu"
      onClick={() => setMobileMenuOpen(false)}
    />
  );

  /**
   * 桌面壳无系统标题栏：顶部 Header 左键按下进入系统拖动移动，
   * 双击切换最大化/还原；浏览器环境与交互元素（按钮/链接）不触发。
   */
  const onHeaderMouseDown = (e: React.MouseEvent<HTMLElement>) => {
    if (e.button !== 0 || !isDesktopShell()) {
      return;
    }
    const target = e.target as HTMLElement;
    if (target.closest('button, a, input, .ant-avatar')) {
      return;
    }
    beginMoveDesktopWindow();
  };
  const onHeaderDoubleClick = () => {
    if (isDesktopShell()) {
      toggleMaximizeDesktopWindow();
    }
  };

  return (
    <Layout className={`app-layout${isDesktopShell() ? ' app-layout--desktop' : ''}`}>
      {!isMobile && (
        <Sider className="app-layout__sider" width={200}>
          <div className="app-layout__brand">
            <img
              className="app-layout__logo"
              src={theme === 'dark' ? '/app-logo.svg' : '/app-logo-light.svg'}
              alt="Tilapia 中枢"
            />
            <div className="app-layout__title">Tilapia 中枢</div>
          </div>
          {menuNode}
          <div className="app-layout__sider-footer">
            <span className="app-layout__status-dot app-layout__status-dot--online" />
            <span>中枢服务运行正常</span>
            <Link to="/settings/changelog" className="app-layout__version">
              {currentVersion ?? '--'}
            </Link>
          </div>
        </Sider>
      )}

      <Drawer
        placement="left"
        open={isMobile && mobileMenuOpen}
        onClose={() => setMobileMenuOpen(false)}
        width={200}
        className="app-layout__mobile-drawer"
        closable={false}
        styles={{
          // 原 .ant-drawer-body 全局覆盖迁移为组件级 styles（规范要求）
          body: {
            display: 'flex',
            flexDirection: 'column',
            padding: '16px 0 12px',
            background: COLORS.bgContainer,
          },
        }}
      >
        <div className="app-layout__brand">
          <img
            className="app-layout__logo"
            src={theme === 'dark' ? '/app-logo.svg' : '/app-logo-light.svg'}
            alt="Tilapia 中枢"
          />
          <div className="app-layout__title">Tilapia 中枢</div>
        </div>
        {menuNode}
        <div className="app-layout__sider-footer">
          <span className="app-layout__status-dot app-layout__status-dot--online" />
          <span>中枢服务运行正常</span>
          <Link to="/settings/changelog" className="app-layout__version" onClick={() => setMobileMenuOpen(false)}>
            {currentVersion ?? '--'}
          </Link>
        </div>
      </Drawer>

      <Layout className={`app-layout__main${isMobile ? ' app-layout__main--mobile' : ''}`}>
        <Header
          className="app-layout__header"
          onMouseDown={onHeaderMouseDown}
          onDoubleClick={onHeaderDoubleClick}
        >
          {isMobile && (
            <Button
              type="text"
              icon={<MenuOutlined />}
              onClick={() => setMobileMenuOpen(true)}
              className="app-layout__mobile-menu-button"
              aria-label="打开菜单"
            />
          )}
          <Title level={4} className="app-layout__page-title">
            {pageTitle}
          </Title>
          <Space className="app-layout__header-actions">
            <Button
              type="text"
              className="app-layout__theme-toggle"
              icon={<CodeOutlined />}
              title="打开终端"
              aria-label="打开终端"
              onClick={() => terminalStore.setOpen(true)}
            />
            <Button
              type="text"
              className="app-layout__theme-toggle"
              icon={theme === 'dark' ? <SunOutlined /> : <MoonOutlined />}
              title={theme === 'dark' ? '切换到亮色主题' : '切换到暗色主题'}
              aria-label="切换主题"
              onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
            />
            <WindowControls />
          </Space>
        </Header>
        <Content className="app-layout__content">
          <Outlet />
        </Content>
      </Layout>
      {/* 全局终端抽屉（ADR-053）：顶栏图标 / AI 执行命令时自动展开 */}
      <TerminalDrawer />
    </Layout>
  );
}
